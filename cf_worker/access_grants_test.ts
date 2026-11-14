// node --experimental-strip-types cf_worker/access_grants_test.ts
// Like the paired-page parser checks, exercise the actual methods without starting a Worker.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

const source = readFileSync(new URL("./src/voice_session.ts", import.meta.url), "utf8");
const accessMethods = source.slice(source.indexOf("  private async openAccess("), source.indexOf("  // Paths resolve under the Workspace root;"));
const alarmMethods = source.slice(source.indexOf("  async alarm()"), source.indexOf("  async fetch("));
const otherMethods = ["boards", "retireDisplays", "unpairBrowsers", "resetSandboxProjection", "drawOnWhiteboard", "revokeAccess", "pairedBrowsers", "touchBrowser"]
  .map((name) => source.match(new RegExp(`  private async ${name}\\(.*?\\n  }`, "s"))![0]).join("\n");
const helpers = ["liveGrant", "browserExpiresAt", "displayNames", "endedDisplays", "grantIds"]
  .map((name) => source.match(new RegExp(`function ${name}\\(.*?\\n}`, "s"))![0]).join("\n");
const AccessHost = new Function("TERMINAL_PORT", "DESKTOP_PORT", "KEEPALIVE_MS", "PAIRED_IDLE_MS", "PAIRED_MAX_MS", "errorMessage", "boardElements", "toHexId", "MAX_BOARDS", "MAX_BOARD_PROGRAM", "GRANT_DEFAULT_MINUTES", "GRANT_MAX_MINUTES", stripTypeScriptTypes(
  `${helpers}\nclass AccessHost {${accessMethods}\n${alarmMethods}\n${otherMethods}}`,
) + "; return AccessHost;")(22222, 6080, 30_000, 3_600_000, 12 * 3_600_000, (error: Error) => error.message, () => [], () => "new", 5, 50_000, 60, 720);

function storage(initial: Record<string, unknown> = {}) {
  const values = new Map(Object.entries(structuredClone(initial)));
  let alarm: number | null = null;
  let chain = Promise.resolve();
  const api = {
    async get(key: string) { return structuredClone(values.get(key)); },
    async put(keyOrEntries: string | Record<string, unknown>, value?: unknown) {
      const entries = typeof keyOrEntries === "string" ? { [keyOrEntries]: value } : keyOrEntries;
      for (const [key, value] of Object.entries(entries)) values.set(key, structuredClone(value));
    },
    async delete(keys: string[]) { for (const key of keys) values.delete(key); },
    async getAlarm() { return alarm; },
    async setAlarm(at: number) { alarm = at; },
    async deleteAlarm() { alarm = null; },
    transaction<T>(run: (tx: typeof api) => Promise<T>): Promise<T> {
      const result = chain.then(() => run(api));
      chain = result.then(() => {}, () => {});
      return result;
    },
  };
  return api;
}

let now = 1_800_000_000_000;
const originalNow = Date.now;
Date.now = () => now;
try {
  const board = { id: "b1", display: "main", access: {
    "terminal:": { kind: "terminal", target: "", expires_at: now + 59_999 },
    "preview:5173": { kind: "preview", target: "5173", expires_at: now + 59_999 },
  } };
  const browser = { secretHash: "paired", display: "main", device: "test", createdAt: now, lastSeen: now };
  const state = { storage: storage({ sandbox_id: "sandbox", paired_browsers: [browser], whiteboard_boards: [board] }), getWebSockets: () => [] };
  const requests: string[] = [];
  const records: Array<{ event: string; detail: unknown }> = [];
  const host = Object.assign(new AccessHost(), {
    identityHash: "identity", attachedSandboxId: "sandbox", state,
    sessions: new Map(), liveAttached: false, compaction: null,
    requireRunningSandbox: async () => {},
    record: async (event: string, detail: unknown) => { records.push({ event, detail }); },
    broadcastDisplay: async () => {},
    targetDisplay: async () => "main",
    daytona: async (path: string) => {
      requests.push(path);
      return { url: "https://signed.preview.test/", token: `signed-${requests.length}` };
    },
  });
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
  await state.storage.put("whiteboard_boards", [board]);
  await assert.rejects(host.openAccess(browser, "b1", "terminal:"), /expired/);
  board.access["terminal:"].expires_at = now;
  await state.storage.put("whiteboard_boards", [board]);
  await assert.rejects(host.openAccess(browser, "b1", "terminal:"), /expired/);
  board.access["terminal:"].expires_at = now + 60_000;
  await state.storage.put("whiteboard_boards", [board]);
  host.requireRunningSandbox = async () => { throw new Error("Sandbox asleep"); };
  await assert.rejects(host.openAccess(browser, "b1", "terminal:"), /asleep/);
  assert.equal(requests.length, 2, "refused opens must mint nothing");

  // A mint returning after revoke must be expired, never retained or delivered.
  host.requireRunningSandbox = async () => {};
  await state.storage.put({ signed_links: [], ssh_tokens: [] });
  let release!: () => void;
  let entered!: () => void;
  const mintEntered = new Promise<void>((resolve) => { entered = resolve; });
  const mintRelease = new Promise<void>((resolve) => { release = resolve; });
  const expiryRequests: string[] = [];
  host.daytona = async (path: string) => {
    if (path.endsWith("/expire")) { expiryRequests.push(path); return {}; }
    entered();
    await mintRelease;
    return { url: "https://late.preview.test/", token: "late-token" };
  };
  const lateOpen = host.openAccess(browser, "b1", "terminal:");
  await mintEntered;
  assert.equal((await host.revokeAccess("main")).ok, true);
  release();
  await assert.rejects(lateOpen, /ended while opening/);
  assert.equal(expiryRequests.length, 1);
  assert.match(expiryRequests[0], /22222\/signed-preview-url\/late-token\/expire$/);
  assert.deepEqual(await state.storage.get("signed_links"), []);
  assert.deepEqual(await state.storage.get("access_revocations"), []);

  // Other authority changes while minting fail closed too: unpairing, eviction and replacement.
  for (const change of ["unpair", "evict", "replace", "expire"]) {
    await state.storage.put({ sandbox_id: "sandbox", paired_browsers: [browser], whiteboard_boards: [board] });
    host.daytona = async (path: string) => {
      if (path.endsWith("/expire")) { expiryRequests.push(path); return {}; }
      if (change === "unpair") await state.storage.put("paired_browsers", []);
      if (change === "evict") await state.storage.put("whiteboard_boards", []);
      if (change === "replace") await state.storage.put("sandbox_id", "replacement");
      if (change === "expire") await state.storage.put("whiteboard_boards", [{ ...board,
        access: { "terminal:": { ...board.access["terminal:"], expires_at: now } },
      }]);
      return { url: "https://late.preview.test/", token: `late-${change}` };
    };
    await assert.rejects(host.openAccess(browser, "b1", "terminal:"), /ended while opening/);
    assert.match(expiryRequests.at(-1)!, new RegExp(`late-${change}/expire$`));
    assert.deepEqual(await state.storage.get("signed_links"), []);
  }
  await state.storage.put({ sandbox_id: "sandbox", paired_browsers: [browser] });

  // Failed provider revocation reports incomplete cleanup and stays durable for the alarm.
  await state.storage.put({ whiteboard_boards: [board], signed_links: [{
    token: "retry-token", grant: "b1:terminal:", port: 22222, expiresAt: now + 60_000,
  }] });
  host.daytona = async () => { throw new Error("provider unavailable"); };
  const incomplete = await host.revokeAccess("main");
  assert.equal(incomplete.ok, false);
  assert.equal(incomplete.revocation_pending, 1);
  assert.match(incomplete.warning, /retry automatically/);
  assert.equal((await state.storage.get("access_revocations") as any[]).length, 1);
  assert.equal(await state.storage.getAlarm(), now + 30_000);
  // A browser ping must not postpone an already scheduled provider retry.
  now += 10_000;
  await host.touchBrowser(browser, true);
  assert.equal(await state.storage.getAlarm(), now + 20_000);
  host.daytona = async (path: string) => { expiryRequests.push(path); return {}; };
  // A replacement and a fresh object must not send an old token to the new Sandbox.
  const restarted = Object.assign(new AccessHost(), host, { attachedSandboxId: "replacement" });
  now += 20_000;
  await restarted.alarm();
  assert.deepEqual(await state.storage.get("access_revocations"), []);
  assert.match(expiryRequests.at(-1)!, /^\/sandbox\/sandbox\/ports\/22222\/signed-preview-url\/retry-token\/expire$/);

  // With no call or messages, the last browser's expiry still ends its Display and links.
  const expiry = now + 5000;
  browser.lastSeen = expiry - 3_600_000;
  browser.createdAt = browser.lastSeen;
  await state.storage.put({ paired_browsers: [browser], whiteboard_boards: [board], signed_links: [{
    token: "display-token", grant: "b1:terminal:", port: 22222, expiresAt: now + 12 * 3_600_000,
  }] });
  host.liveAttached = true;
  await host.scheduleAlarm();
  assert.equal(await state.storage.getAlarm(), expiry, "browser expiry wins over call keepalive");
  host.liveAttached = false;
  now = expiry;
  await host.alarm();
  assert.deepEqual(await state.storage.get("paired_browsers"), []);
  assert.deepEqual(await state.storage.get("whiteboard_boards"), []);
  assert.match(expiryRequests.at(-1)!, /display-token\/expire$/);
  assert.equal(await state.storage.getAlarm(), null);
  assert.ok(records.some((item) => item.event === "display.ended"), "Display retirement retains its diagnostic event");

  // Crash immediately after each authority-removal transaction, before any provider I/O.
  // A fresh object must find both signed/SSH credentials and an already-committed retry alarm.
  for (const operation of ["revoke", "unpair", "expire", "evict", "replace"]) {
    const paired = { ...browser, createdAt: now, lastSeen: now };
    const b = { id: "crash", display: "main", access: {
      "terminal:": { kind: "terminal", target: "", expires_at: now + 3_600_000 },
      "ssh:": { kind: "ssh", target: "", expires_at: now + 3_600_000 },
    } };
    const boards = operation === "evict" ? [b, ...Array.from({ length: 4 }, (_, i) => ({ id: `other${i}`, display: "main" }))] : [b];
    const saved = storage({ sandbox_id: "old-sandbox", paired_browsers: [paired], whiteboard_boards: boards,
      signed_links: [{ token: `signed-${operation}`, grant: "crash:terminal:", port: 22222, expiresAt: now + 3_600_000 }],
      ssh_tokens: [{ token: `ssh-${operation}`, grant: "crash:ssh:", expiresAt: now + 3_600_000 }],
    });
    const interrupted = Object.assign(new AccessHost(), host, {
      state: { storage: saved, getWebSockets: () => [] }, attachedSandboxId: "old-sandbox",
      finishAccessRevocation: async () => { throw new Error("crash after commit"); },
      browserPresenceChanged: async () => {}, closeAgent: () => {},
      endedExecutions: new Set(), undelivered: new Map(), permissions: new Map(), sessions: new Map(),
      boardDraws: Promise.resolve(), checkBoardFiles: async () => ({}), grantAccess: () => ({ access: {}, refused: [] }),
    });
    if (operation === "revoke") await assert.rejects(interrupted.revokeAccess("main"), /crash after commit/);
    if (operation === "unpair") await assert.rejects(interrupted.unpairBrowsers([paired.secretHash]), /crash after commit/);
    if (operation === "expire") {
      paired.createdAt = paired.lastSeen = now - 3_600_001;
      await saved.put("paired_browsers", [paired]);
      await assert.rejects(interrupted.pairedBrowsers(), /crash after commit/);
    }
    if (operation === "evict") await assert.rejects(interrupted.drawOnWhiteboard("root = Stack([])", "", "main", 60), /crash after commit/);
    if (operation === "replace") await assert.rejects(interrupted.resetSandboxProjection(), /crash after commit/);
    assert.deepEqual(await saved.get("signed_links"), [], operation);
    assert.deepEqual(await saved.get("ssh_tokens"), [], operation);
    assert.equal((await saved.get("access_revocations") as any[]).length, 2, operation);
    assert.equal(await saved.getAlarm(), now + 1, operation);
    const resumed = Object.assign(new AccessHost(), host, {
      state: { storage: saved, getWebSockets: () => [] }, attachedSandboxId: "new-sandbox",
      daytona: async (path: string) => { assert.ok(path.startsWith("/sandbox/old-sandbox/"), path); return {}; },
    });
    await resumed.alarm();
    assert.deepEqual(await saved.get("access_revocations"), [], operation);
    assert.equal(await resumed.touchBrowser(paired, true), !["unpair", "expire", "replace"].includes(operation), "an old snapshot cannot resurrect removed pairings");
  }

  // One-time back-patch removes old terminal authority, not pairings or other grants.
  const start = source.indexOf("      // One-time back-patch for provider-hosted terminals");
  const patch = source.slice(start, source.indexOf("\n      await this.scheduleAlarm();", start));
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
