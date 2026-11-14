// node --experimental-strip-types cf_worker/access_grants_test.ts
// Like the paired-page parser checks, exercise the actual methods without starting a Worker.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

const source = readFileSync(new URL("./src/voice_session.ts", import.meta.url), "utf8");
const opening = source.slice(source.indexOf("  private async openAccess("), source.indexOf("  // End every credential minted under these grants:"));
const liveGrant = source.match(/function liveGrant\(.*?\n}/s)![0];
const AccessHost = new Function("TERMINAL_PORT", "DESKTOP_PORT", stripTypeScriptTypes(
  `${liveGrant}\nclass AccessHost {${opening}}`,
) + "; return AccessHost;")(22222, 6080);

function storage(initial: Record<string, unknown> = {}) {
  const values = new Map(Object.entries(structuredClone(initial)));
  return {
    async get(key: string) { return structuredClone(values.get(key)); },
    async put(keyOrEntries: string | Record<string, unknown>, value?: unknown) {
      const entries = typeof keyOrEntries === "string" ? { [keyOrEntries]: value } : keyOrEntries;
      for (const [key, value] of Object.entries(entries)) values.set(key, structuredClone(value));
    },
    async delete(keys: string[]) { for (const key of keys) values.delete(key); },
  };
}

const now = 1_800_000_000_000;
const originalNow = Date.now;
Date.now = () => now;
try {
  const board = { id: "b1", display: "main", access: {
    "terminal:": { kind: "terminal", target: "", expires_at: now + 59_999 },
    "preview:5173": { kind: "preview", target: "5173", expires_at: now + 59_999 },
  } };
  const state = { storage: storage() };
  const requests: string[] = [];
  const host = Object.assign(new AccessHost(), {
    identityHash: "identity", attachedSandboxId: "sandbox", state,
    boards: async () => [board],
    requireRunningSandbox: async () => {},
    record: async () => {},
    daytona: async (path: string) => {
      requests.push(path);
      return { url: "https://signed.preview.test/", token: `signed-${requests.length}` };
    },
  });
  const browser = { display: "main", device: "test" };
  assert.deepEqual(await host.openAccess(browser, "b1", "terminal:"), { url: "https://signed.preview.test" });
  assert.equal(requests[0], "/sandbox/sandbox/ports/22222/signed-preview-url?expiresInSeconds=59");
  assert.deepEqual(await state.storage.get("signed_links"), [{
    token: "signed-1", grant: "b1:terminal:", port: 22222, expiresAt: now + 59_999,
  }]);
  await host.openAccess(browser, "b1", "preview:5173");
  assert.equal(requests[1], "/sandbox/sandbox/ports/5173/signed-preview-url?expiresInSeconds=59");
  await assert.rejects(host.openAccess({ display: "other" }, "b1", "terminal:"), /no longer on this display/);
  await assert.rejects(host.openAccess(browser, "b1", "terminal:build"), /not granted/);
  board.access["terminal:"].expires_at = now + 999;
  await assert.rejects(host.openAccess(browser, "b1", "terminal:"), /expired/);
  board.access["terminal:"].expires_at = now;
  await assert.rejects(host.openAccess(browser, "b1", "terminal:"), /expired/);
  board.access["terminal:"].expires_at = now + 60_000;
  host.requireRunningSandbox = async () => { throw new Error("Sandbox asleep"); };
  await assert.rejects(host.openAccess(browser, "b1", "terminal:"), /asleep/);
  assert.equal(requests.length, 2, "refused opens must mint nothing");

  // One-time back-patch removes old terminal authority, not pairings or other grants.
  const start = source.indexOf("      // One-time back-patch for provider-hosted terminals");
  const patch = source.slice(start, source.indexOf("\n    });", start));
  const migrate = new Function(stripTypeScriptTypes(`async function migrate(state) {${patch}}`) + "; return migrate;")();
  const migrationState = { storage: storage({
    terminal_tickets: [{ hash: "old-ticket" }], terminal_tokens: [{ hash: "old-token" }],
    paired_browsers: [{ display: "main" }],
    signed_links: [{ token: "existing-preview" }],
    whiteboard_boards: [{ id: "legacy", access: {
      "terminal:build": { kind: "terminal", target: "build" },
      "terminal:": { kind: "terminal", target: "" },
      "preview:5173": board.access["preview:5173"],
    } }],
  }) };
  await migrate(migrationState);
  assert.equal(await migrationState.storage.get("terminal_tickets"), undefined);
  assert.equal(await migrationState.storage.get("terminal_tokens"), undefined);
  assert.deepEqual(await migrationState.storage.get("paired_browsers"), [{ display: "main" }]);
  assert.deepEqual(await migrationState.storage.get("signed_links"), [{ token: "existing-preview" }]);
  assert.deepEqual(await migrationState.storage.get("whiteboard_boards"), [{ id: "legacy", access: {
    "preview:5173": board.access["preview:5173"],
  } }]);
  await migrationState.storage.put({ whiteboard_boards: [board] });
  await migrate(migrationState);
  assert.deepEqual(await migrationState.storage.get("whiteboard_boards"), [board], "new terminal grants survive later starts");
} finally {
  Date.now = originalNow;
}
console.log("signed access bounds, refused opens, and terminal state back-patch: passed");
