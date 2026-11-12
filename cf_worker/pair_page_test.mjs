// Browser-only pairing tests: node --test cf_worker/pair_page_test.mjs
// Run the page's actual claim code with a small DOM/HTTP harness, without a Worker or microphone.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("src/pair_page.ts", import.meta.url), "utf8");
const html = source.split("String.raw`")[1].split("`;")[0];
const script = html.split("<script>")[1].split("</script>")[0];
const claims = script.slice(script.indexOf("// ---- Typed Pairing Code"), script.indexOf("// ---- Small helpers"));
const submit = script.slice(script.indexOf('$("pairForm").addEventListener'), script.indexOf('$("unpair").addEventListener'));
const reply = (status, body) => ({ ok: status >= 200 && status < 300, status, statusText: "Test response", json: async () => body });

function harness(responses) {
  const nodes = Object.fromEntries(["pairCode", "claimCode", "pairForm"].map((id) => [id, {
    value: "", disabled: false, attributes: {}, focused: false,
    focus() { this.focused = true; },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, handler) { this[name] = handler; },
  }]));
  const requests = [], statuses = [], tokens = new Map();
  let now = 0, connections = 0, screen;
  const context = vm.createContext({
    $: (id) => nodes[id],
    Date: { now: () => now },
    sleep: async (ms) => { now += ms; },
    setStatus: (text, tone) => statuses.push({ text, tone }),
    showScreen: (name) => { screen = name; },
    log: () => {},
    TOKEN_KEY: "token",
    sessionStorage: { setItem: (key, value) => tokens.set(key, value) },
    connect: () => { connections++; },
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      const next = responses.shift();
      if (next instanceof Error) throw next;
      assert.ok(next, "unexpected request to " + url);
      return typeof next === "function" ? next() : next;
    },
  });
  vm.runInContext(claims + submit, context);
  return { nodes, requests, statuses, tokens, context,
    claim: (code) => context.claim(code),
    get connections() { return connections; },
    get screen() { return screen; },
  };
}

test("page has valid JavaScript and an accessible typed form, with no acoustic path", () => {
  new vm.Script(script);
  assert.match(html, /<form id="pairForm">/);
  assert.match(html, /for="pairCode"/);
  assert.match(html, /id="pairCode"[^>]*type="text"[^>]*required/);
  assert.match(html, /aria-describedby="pairHelp status"/);
  assert.match(html, /Use spaces or hyphens/);
  assert.match(html, /90 seconds/);
  assert.match(html, /press 1 in the call to accept it, or star to refuse/);
  // AudioContext still powers the paired screen's notification chime, not pairing.
  const acoustic = /AudioWorklet|getUserMedia|ScriptProcessor|decode-failed|Reed-Solomon|Press Listen|id="listen"|id="frames"|class="meter"/;
  assert.equal(acoustic.exec(html), null);
});

test("submitting the form uses the typed code and prevents navigation", async () => {
  const h = harness([reply(404, {})]);
  h.nodes.pairCode.value = "123 aardvark adroitness";
  let prevented = false;
  h.nodes.pairForm.submit({ preventDefault() { prevented = true; } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(prevented, true);
  assert.equal(h.requests[0].method, "POST");
  assert.equal(h.requests[0].url, "/pair/claim");
  assert.deepEqual(JSON.parse(h.requests[0].body), { code: h.nodes.pairCode.value });
});

test("claim preserves spaces/case for server normalization and polls until accepted", async () => {
  const h = harness([reply(200, { ticket: "ticket" }), reply(200, { state: "waiting" }),
    reply(200, { state: "accepted", token: "paired-token" })]);
  await h.claim(" 123-Aardvark Adroitness ");
  assert.deepEqual(JSON.parse(h.requests[0].body), { code: " 123-Aardvark Adroitness " });
  assert.equal(h.requests[1].url, "/pair/claim/status");
  assert.equal(h.requests[1].headers.authorization, "Bearer ticket");
  assert.equal(h.tokens.get("token"), "paired-token");
  assert.equal(h.connections, 1);
  assert.equal(h.nodes.pairCode.value, "");
  assert.equal(h.nodes.pairForm.attributes["aria-busy"], "false");
});

test("in-flight claims disable the form and ignore duplicate submissions", async () => {
  let resolve;
  const h = harness([() => new Promise((done) => { resolve = done; })]);
  const pending = h.claim("1-aardvark-adroitness");
  assert.equal(h.nodes.pairCode.disabled, true);
  assert.equal(h.nodes.claimCode.disabled, true);
  assert.equal(h.nodes.pairForm.attributes["aria-busy"], "true");
  await h.claim("2-aardvark-adroitness");
  assert.equal(h.requests.length, 1);
  resolve(reply(404, {}));
  await pending;
  assert.equal(h.nodes.pairCode.disabled, false);
  assert.equal(h.nodes.claimCode.disabled, false);
});

test("empty code does not fetch; claim errors allow correction and retry", async () => {
  const h = harness([new Error("offline"), reply(400, { error: "Invalid code" }), reply(404, {})]);
  await h.claim("   ");
  assert.equal(h.requests.length, 0);
  for (const expected of [/Could not reach/, /Invalid code/, /unknown or expired/]) {
    await h.claim("123 aardvark adroitness");
    assert.match(h.statuses.at(-1).text, expected);
    assert.equal(h.nodes.pairCode.disabled, false);
    assert.equal(h.nodes.pairCode.focused, true);
  }
});

for (const state of ["refused", "expired"]) {
  test(state + " approval returns to code entry", async () => {
    const h = harness([reply(200, { ticket: "ticket" }), reply(200, { state })]);
    await h.claim("999-aardvark-adroitness");
    assert.equal(h.screen, "pair");
    assert.equal(h.connections, 0);
    assert.equal(h.nodes.claimCode.disabled, false);
    assert.match(h.statuses.at(-1).text, /Enter a new pairing code/);
  });
}

test("approval retains its existing 45-second polling deadline", async () => {
  const h = harness([reply(200, { ticket: "ticket" }), ...Array.from({ length: 45 }, () => reply(200, { state: "waiting" }))]);
  await h.claim("123-aardvark-adroitness");
  assert.equal(h.requests.length, 46);
  assert.match(h.statuses.at(-1).text, /within 45 seconds/);
  assert.equal(h.nodes.claimCode.disabled, false);
});
