// Exercise actual VoiceSession methods without opening a provider connection.
// Run: node --test cf_worker/pairing_lifecycle_test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { stripTypeScriptTypes } from "node:module";

const source = readFileSync(new URL("src/voice_session.ts", import.meta.url), "utf8");
const methods = ["pairRequest", "cleanupVoice", "endPairingTimers"].map((name) => {
  const start = source.search(new RegExp("  private (?:async )?" + name + "\\("));
  const end = source.indexOf("\n  private ", start + 1);
  assert.ok(start >= 0 && end > start, name);
  return source.slice(start, end);
});
const code = stripTypeScriptTypes(`globalThis.Harness = class Harness { ${methods.join("\n")} };`);

function harness(hash = async () => "ticket-hash", failDelete = false) {
  const pending = [], warnings = [], cleared = [];
  const context = vm.createContext({
    Response, Date, WebSocket: { OPEN: 1 }, PAIRING_CONFIRM_MS: 30_000,
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
    pairing: { codeHash: "code-hash", expiresAt: Date.now() + 90_000, unclaimedTimer: 3, claim: null },
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
    saveTranscript: async () => { saved = true; }, orchestratorEvent: async () => {}, writeNextGreeting: async () => {},
  });
  return { session, pending, warnings, cleared, stored, get closed() { return closed; }, get saved() { return saved; } };
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
  assert.equal(h.session.pairing.claim.timer, null);
  assert.equal(h.closed, true);
  assert.equal(h.saved, true);
  assert.equal(h.session.cleaningUpVoice, false);
  assert.equal(h.stored.get("last_call").reason, "disconnect");
  assert.equal(h.warnings.length, 1);
});

test("disconnect preserves an accepted browser's uncollected credential", async () => {
  const h = harness();
  h.session.pairing.claim = { state: "accepted", token: "accepted-credential", timer: null };
  await h.session.cleanupVoice("disconnect");
  await Promise.all(h.pending);
  assert.equal(h.session.pairing.claim.state, "accepted");
  assert.equal(h.session.pairing.claim.token, "accepted-credential");
});
