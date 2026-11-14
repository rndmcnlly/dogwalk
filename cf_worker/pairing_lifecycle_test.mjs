// Exercise actual VoiceSession methods without opening a provider connection.
// Run: node --test cf_worker/pairing_lifecycle_test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { stripTypeScriptTypes } from "node:module";
import { ACCESS_COMPONENTS, boardElements, UPLOAD_LIMITS, uploadFolder } from "./src/board_images.ts";

const source = readFileSync(new URL("src/voice_session.ts", import.meta.url), "utf8");
const methods = ["pairRequest", "resolvePairingClaim", "cleanupVoice", "endPairingTimers", "grantAccess", "workspacePath"].map((name) => {
  const start = source.search(new RegExp("  private (?:async )?" + name + "\\("));
  const end = source.indexOf("\n  private ", start + 1);
  assert.ok(start >= 0 && end > start, name);
  return source.slice(start, end);
});
const code = stripTypeScriptTypes(`globalThis.Harness = class Harness { ${methods.join("\n")}\n};`);

function harness(hash = async () => "ticket-hash", failDelete = false) {
  const pending = [], warnings = [], cleared = [], events = [];
  const context = vm.createContext({
    Response, Date, WebSocket: { OPEN: 1 }, normalizeDisplayName: (input) => input.trim().toLowerCase(),
    ACCESS_COMPONENTS, UPLOAD_LIMITS, uploadFolder, GATEWAY_PORT: 8765, WORKSPACE_ROOT: "/home/daytona", errorMessage: (error) => error.message,
    newBrowserSecret: () => "ticket-secret", sha256Hex: hash,
    setTimeout: () => 7, clearTimeout: (id) => cleared.push(id),
    summarizeLevels: () => ({}), newLevels: () => ({}), lastWords: () => [],
    console: { warn: (message) => warnings.push(message) },
  });
  vm.runInContext(code, context);
  const session = new context.Harness();
  let closed = false, saved = false;
  const stored = new Map();
  Object.assign(session, {
    pairing: { codeHash: "code-hash", expiresAt: Date.now() + 90_000, display: "main", unclaimedTimer: 3, claim: null },
    liveAttached: true, liveSessionId: "live-1", identityHash: "identity", callSid: "call-1",
    live: { readyState: 1, close: () => { closed = true; } },
    inputLevels: { open: {} }, transcript: [],
    env: { DB: { prepare: () => ({ bind: () => ({ run: async () => {
      if (failDelete) throw new Error("simulated D1 outage");
    } }) }) } },
    state: {
      waitUntil: (promise) => pending.push(promise),
      storage: { put: async (key, value) => stored.set(key, value), delete: async (key) => stored.delete(key), get: async (key) => stored.get(key) },
    },
    endMenu() {}, record: async () => {}, flushTiming: async () => {}, sendLive() {},
    saveTranscript: async () => { saved = true; }, orchestratorEvent: async (line) => { events.push(line); }, writeNextGreeting: async () => {},
    closeOutContext: async () => {}, pairedBrowsers: async () => stored.get("paired_browsers") ?? [], scheduleAlarm: async () => {},
  });
  return { session, pending, warnings, cleared, stored, events, get closed() { return closed; }, get saved() { return saved; } };
}
const request = () => new Request("https://voice-session/pair/claim", { method: "POST", headers: { "x-dogwalk-code-hash": "code-hash" } });

test("disconnect during the secret hash cannot install a stale waiting claim", async () => {
  let completeHash;
  const h = harness(() => new Promise((resolve) => { completeHash = resolve; }));
  const claiming = h.session.pairRequest(request(), "/pair/claim");
  await h.session.cleanupVoice("disconnect");
  completeHash("ticket-hash");
  assert.equal((await claiming).status, 404);
  assert.equal(h.session.pairing.claim, null);
  assert.equal(h.closed, true);
  await Promise.all(h.pending);
});

test("disconnect expires an installed claim, even when D1 deletion fails", async () => {
  const h = harness(undefined, true);
  assert.equal((await h.session.pairRequest(request(), "/pair/claim")).status, 200);
  assert.equal(h.session.pairing.claim.state, "waiting");
  await h.session.cleanupVoice("disconnect");
  await Promise.all(h.pending);
  assert.equal(h.session.pairing.claim.state, "expired");
  assert.equal(h.session.pairing.codeHash, "");
  assert.equal(h.session.pairing.expiresAt, 0);
  assert.equal(h.closed, true);
  assert.equal(h.saved, true);
  assert.equal(h.session.cleaningUpVoice, false);
  assert.equal(h.stored.get("last_call").reason, "disconnect");
  assert.equal(h.warnings.length, 1);
});

test("disconnect preserves an accepted browser's uncollected credential", async () => {
  const h = harness();
  h.session.pairing.claim = { state: "accepted", token: "accepted-credential" };
  await h.session.cleanupVoice("disconnect");
  await Promise.all(h.pending);
  assert.equal(h.session.pairing.claim.state, "accepted");
  assert.equal(h.session.pairing.claim.token, "accepted-credential");
});

const claimRequest = () => new Request("https://voice-session/pair/claim", {
  method: "POST", headers: { "x-dogwalk-code-hash": "code-hash", "x-dogwalk-browser": "Chrome on a Mac", "x-dogwalk-place": "near Santa Cruz" },
});

test("a claim wakes the Orchestrator with pairing_claimed and waits without a timer", async () => {
  const h = harness();
  assert.equal((await h.session.pairRequest(claimRequest(), "/pair/claim")).status, 200);
  assert.deepEqual(h.events, ['[harness event] pairing_claimed: {"device":"Chrome on a Mac","place":"near Santa Cruz","display":"main"}']);
  assert.equal(h.session.pairing.claim.state, "waiting");
  assert.equal("timer" in h.session.pairing.claim, false);
});

test("accept_browser joins the claiming browser to a Display, honoring a spoken override", async () => {
  const h = harness();
  h.stored.set("paired_browsers", [{ secretHash: "old", display: "slides" }]);
  await h.session.pairRequest(claimRequest(), "/pair/claim");
  const result = plain(await h.session.resolvePairingClaim("accepted", " Slides "));
  assert.deepEqual(result, { ok: true, outcome: "accepted", display: "slides", mirrors_existing_display: true, device: "Chrome on a Mac" });
  assert.equal(h.session.pairing.claim.state, "accepted");
  assert.equal(h.session.pairing.claim.token, "identity.ticket-secret");
  assert.equal(h.session.pairing.codeHash, "");
  assert.deepEqual(h.stored.get("paired_browsers").map((item) => item.display), ["slides", "slides"]);
  await assert.rejects(h.session.resolvePairingClaim("accepted"), /no browser is waiting to pair/);
});

test("accept_browser without an override keeps pair_browser's Display", async () => {
  const h = harness();
  await h.session.pairRequest(claimRequest(), "/pair/claim");
  const result = plain(await h.session.resolvePairingClaim("accepted", "  "));
  assert.equal(result.display, "main");
  assert.equal(result.mirrors_existing_display, false);
});

test("refuse_browser spends the code and pairs nothing; with no claim both tools throw", async () => {
  const h = harness();
  await assert.rejects(h.session.resolvePairingClaim("refused"), /no browser is waiting to pair/);
  await assert.rejects(h.session.resolvePairingClaim("accepted"), /no browser is waiting to pair/);
  await h.session.pairRequest(claimRequest(), "/pair/claim");
  assert.deepEqual(plain(await h.session.resolvePairingClaim("refused")), { ok: true, outcome: "refused", device: "Chrome on a Mac" });
  assert.equal(h.session.pairing.claim.state, "refused");
  assert.equal(h.session.pairing.claim.token, null);
  assert.equal(h.session.pairing.codeHash, "");
  assert.equal(h.stored.get("paired_browsers"), undefined);
});

const statusRequest = () => new Request("https://voice-session/pair/claim/status", { headers: { "x-dogwalk-secret": "ticket-secret" } });
const status = async (h) => plain(await (await h.session.pairRequest(statusRequest(), "/pair/claim/status")).json());

test("an accept in flight reads as waiting until its credential exists", async () => {
  const h = harness();
  await h.session.pairRequest(claimRequest(), "/pair/claim");
  let release;
  h.session.pairedBrowsers = () => new Promise((resolve) => { release = () => resolve([]); });
  const accepting = h.session.resolvePairingClaim("accepted");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(await status(h), { state: "waiting" });
  await assert.rejects(h.session.resolvePairingClaim("accepted"), /no browser is waiting to pair/);
  release();
  assert.equal(plain(await accepting).outcome, "accepted");
  assert.deepEqual(await status(h), { state: "accepted", token: "identity.ticket-secret" });
});

test("a code replaced while accept_browser awaits stores no browser", async () => {
  const h = harness();
  await h.session.pairRequest(claimRequest(), "/pair/claim");
  const claim = h.session.pairing.claim;
  let release;
  h.session.pairedBrowsers = () => new Promise((resolve) => { release = () => resolve([]); });
  const accepting = h.session.resolvePairingClaim("accepted");
  await new Promise((resolve) => setImmediate(resolve));
  h.session.pairing = null;  // pair_browser replaced the code
  release();
  await assert.rejects(accepting, /ended before it was accepted/);
  assert.equal(h.stored.get("paired_browsers"), undefined);
  assert.equal(claim.state, "expired");
  assert.equal(claim.token, null);
});

// Access Grants (DOMAIN.md): drawing in a call is the authorization. Results cross back from the
// harness's realm, so they compare as plain JSON.
const plain = (value) => JSON.parse(JSON.stringify(value));
const ACCESS_BOARD = 'root = Stack([Preview(5173), Preview(8765), Preview(70000), Terminal(), Terminal("Build"), Desktop(), SshAccess(), Download("out/a.zip"), Download("../../etc/passwd"), Upload("talk-slides"), Upload("../etc"), Preview(5173)])';

test("a board drawn in a call grants its Access Elements for the minutes asked, on valid targets only", () => {
  const h = harness();
  const before = Date.now();
  const { access, refused } = plain(h.session.grantAccess(boardElements(ACCESS_BOARD), 90));
  assert.deepEqual(Object.keys(access), ["preview:5173", "terminal:", "desktop:", "ssh:", "download:out/a.zip", "upload:talk-slides"]);
  assert.deepEqual(access["upload:talk-slides"].upload, { limits: { file_bytes: 25_000_000, files: 20, bytes: 100_000_000 }, files: 0, bytes: 0, failures: 0 });
  for (const [key, grant] of Object.entries(access)) {
    assert.equal(`${grant.kind}:${grant.target}`, key);
    assert.ok(grant.expires_at >= before + 90 * 60_000 && grant.expires_at <= Date.now() + 90 * 60_000);
    assert.equal(grant.revoked, undefined);
  }
  // A Terminal has no name since provider-hosted terminals (#65): Terminal("Build") is the one Terminal.
  assert.deepEqual(refused.map((item) => item.key), ["preview:8765", "preview:70000", "download:../../etc/passwd", "upload:../etc"]);
  assert.match(refused[0].reason, /Agent Gateway/);
  assert.match(refused[1].reason, /port number/);
  assert.match(refused[2].reason, /under \/home\/daytona/);
  assert.match(refused[3].reason, /inside ~\/uploads/);
});

test("outside a call, nothing on a board is granted", () => {
  const h = harness();
  h.session.liveAttached = false;
  const { access, refused } = plain(h.session.grantAccess(boardElements(ACCESS_BOARD), 60));
  assert.deepEqual(access, {});
  assert.ok(refused.length >= 4 && refused.every((item) => /only while drawing in a call/.test(item.reason)));
});
