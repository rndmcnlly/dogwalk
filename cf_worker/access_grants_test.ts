// node --experimental-strip-types cf_worker/access_grants_test.ts
// Like the paired-page parser checks, exercise the actual methods without starting a Worker.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

const source = readFileSync(new URL("./src/voice_session.ts", import.meta.url), "utf8");
const accessMethods = source.slice(source.indexOf("  private async openAccess("), source.indexOf("  // Paths resolve under the Workspace root;"));
const alarmMethods = source.slice(source.indexOf("  async alarm()"), source.indexOf("  async fetch("));
const otherMethods = ["boards", "retireDisplays", "unpairBrowsers", "resetSandboxProjection", "drawOnWhiteboard", "revokeAccess", "sshWarning", "unpairDisplay", "pairedBrowsers", "touchBrowser"]
  .map((name) => source.match(new RegExp(`  private async ${name}\\(.*?\\n  }`, "s"))![0]).join("\n");
const helpers = ["liveGrant", "browserExpiresAt", "displayNames", "endedDisplays", "grantIds"]
  .map((name) => source.match(new RegExp(`function ${name}\\(.*?\\n}`, "s"))![0]).join("\n");
// The provider, as the test scripts it: every request is recorded as "<operation> <sandbox> <detail>".
class ApiError extends Error { status: number; constructor(status: number) { super("provider"); this.status = status; } }
const provider = {
  requests: [] as string[], status: "running", signFails: false, onStop: async () => {}, fail: null as Error | null,
  onMint: async () => {},
  async call(request: string) { provider.requests.push(request); if (provider.fail) throw provider.fail; },
  matching(prefix: string) { return provider.requests.filter((item) => item.startsWith(prefix)); },
};
const daytona = {
  signedPreviewUrl: async (_env: unknown, id: string, port: number, seconds: number) => {
    if (provider.signFails) throw new Error("signing failed");
    await provider.call(`sign ${id} ${port} ${seconds}`);
    await provider.onMint();
    return { url: "https://signed.preview.test/" };
  },
  sshCommand: async (_env: unknown, id: string, seconds: number) => {
    await provider.call(`ssh ${id} ${seconds}`);
    await provider.onMint();
    return { command: "ssh test", expiresAt: now + seconds * 1000 };
  },
  revokeSignedPreviewUrls: async (_env: unknown, id: string) => provider.call(`revoke ${id}`),
  getSandbox: async (_env: unknown, id: string) => { await provider.call(`get ${id}`); return { id, status: provider.status }; },
  startTakeoverServer: async (_box: unknown, port: number) => provider.call(`start ${port}`),
  stopTakeoverServer: async (box: { toolbox: string }, port: number) => { await provider.onStop(); await provider.call(`stop ${box.toolbox} ${port}`); },
  toolboxOf: (_env: unknown, sandbox: { id: string }) => ({ toolbox: sandbox.id, headers: {} }),
  isTakeoverPort: (port: number) => port === 7681 || port === 6080,
  DaytonaApiError: ApiError, BROKEN_STATUSES: ["failed", "lost"], TERMINAL_PORT: 7681, DESKTOP_PORT: 6080,
  fetch: async (url: string) => { await provider.call(`fetch ${url}`); return { ok: true }; },
};
const constants = { SSH_TOKEN_SECONDS: 300, ASLEEP_RETRY_MS: 21_600_000, KEEPALIVE_MS: 30_000, PAIRED_IDLE_MS: 3_600_000, PAIRED_MAX_MS: 12 * 3_600_000,
  errorMessage: (error: Error) => error.message, boardElements: () => [], toHexId: () => "new", MAX_BOARDS: 5, MAX_BOARD_PROGRAM: 50_000,
  GRANT_DEFAULT_MINUTES: 60, GRANT_MAX_MINUTES: 720 };
const names = { ...daytona, ...constants };
const AccessHost = new Function(...Object.keys(names), stripTypeScriptTypes(
  `${helpers}\nclass AccessHost {${accessMethods}\n${alarmMethods}\n${otherMethods}}`,
) + "; return AccessHost;")(...Object.values(names));

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
    async delete(keys: string | string[]) { for (const key of [keys].flat()) values.delete(key); },
    async list({ prefix }: { prefix: string }) { return new Map([...values].filter(([key]) => key.startsWith(prefix))); },
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
  const records: Array<{ event: string; detail: any }> = [];
  const host = Object.assign(new AccessHost(), {
    identityHash: "identity", attachedSandboxId: "sandbox", state, env: {},
    sessions: new Map(), liveAttached: false, compaction: null,
    requireRunningSandbox: async () => {},
    workspaceToolbox: async () => ({ toolbox: "sandbox", headers: {} }),
    record: async (event: string, detail: unknown) => { records.push({ event, detail }); },
    broadcastDisplay: async () => {},
    targetDisplay: async () => "main",
    browserPresenceChanged: async () => {},
  });
  assert.deepEqual(await host.openAccess(browser, "b1", "terminal:"), { url: "https://signed.preview.test" });
  assert.deepEqual(provider.requests, ["start 7681", "sign sandbox 7681 59"], "the server runs before its link exists");
  assert.deepEqual(await state.storage.get("signed_links"), [{ grant: "b1:terminal:", port: 7681, expiresAt: now + 59_999 }]);
  assert.equal(await state.storage.getAlarm(), now + 59_999, "a terminal ends with its last link");
  await host.openAccess(browser, "b1", "preview:5173");
  assert.deepEqual(provider.requests.slice(2), ["sign sandbox 5173 59"]);
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
  assert.equal(provider.requests.length, 3, "refused opens must mint and start nothing");
  host.requireRunningSandbox = async () => {};

  // Any expired link ends its server, as a page opened by it may still be connected, and revokes
  // nobody else's links.
  const other = { grant: "b2:terminal:", port: 7681, expiresAt: now + 120_000 };
  await state.storage.put({ signed_links: [{ grant: "b1:terminal:", port: 7681, expiresAt: now + 60_000 }, other] });
  provider.requests = [];
  now += 60_000;
  await host.alarm();
  assert.deepEqual(provider.requests, ["get sandbox", "stop sandbox 7681"], "another Display's link does not keep an expired page");
  assert.deepEqual(await state.storage.get("signed_links"), [other]);
  provider.requests = [];
  now += 60_000;
  await host.alarm();
  assert.deepEqual(provider.requests, ["get sandbox", "stop sandbox 7681"]);
  assert.deepEqual(await state.storage.get("signed_links"), []);
  assert.deepEqual(await state.storage.get("access_revocations"), []);
  browser.createdAt = browser.lastSeen = now;
  for (const grant of Object.values(board.access)) grant.expires_at = now + 60_000;

  // A desktop's display server ends too; one in a paused Sandbox waits for the next wake.
  await state.storage.put({ paired_browsers: [browser], whiteboard_boards: [board], signed_links: [{ grant: "b1:terminal:", port: 6080, expiresAt: now + 60_000 }] });
  provider.requests = [];
  provider.status = "paused";
  assert.equal((await host.revokeAccess("main")).ok, true, "a frozen server serves nobody");
  assert.deepEqual(provider.requests, ["revoke sandbox", "get sandbox"]);
  assert.equal((await state.storage.get("access_revocations") as any[]).length, 1);
  provider.status = "running";
  provider.requests = [];
  await host.flushAccessRevocations(true);
  assert.deepEqual(provider.requests, ["revoke sandbox", "get sandbox", "stop sandbox 6080", "fetch sandbox/computeruse/stop"]);
  assert.deepEqual(await state.storage.get("access_revocations"), []);

  // An ending still owed when a page opens comes first, so it cannot close the new server; one that
  // cannot be done refuses the open.
  const owed = { id: "owed", sandboxId: "sandbox", port: 7681, linksUntil: 0, retryAt: now + 21_600_000 };
  await state.storage.put({ whiteboard_boards: [board], signed_links: [], access_revocations: [owed] });
  for (const grant of Object.values(board.access) as any[]) delete grant.revoked;
  await state.storage.put("whiteboard_boards", [board]);
  provider.requests = [];
  await host.openAccess(browser, "b1", "terminal:");
  assert.deepEqual(provider.requests, ["get sandbox", "stop sandbox 7681", "start 7681", "sign sandbox 7681 60"]);
  await state.storage.put({ signed_links: [], access_revocations: [owed] });
  provider.status = "paused";
  await assert.rejects(host.openAccess(browser, "b1", "terminal:"), /still being ended/);
  provider.status = "running";
  await state.storage.put({ signed_links: [], access_revocations: [] });

  // A deleted Sandbox has nothing left to end.
  await state.storage.put({ access_revocations: [{ id: "gone", sandboxId: "sandbox", port: 7681, linksUntil: now + 60_000, retryAt: now }] });
  provider.fail = new ApiError(404);
  assert.equal(await host.flushAccessRevocations(true), 0);
  assert.deepEqual(await state.storage.get("access_revocations"), []);
  provider.fail = null;

  // SSH tokens last minutes, and retiring one says plainly that it cannot be revoked.
  const sshBoard = { id: "b3", display: "main", access: { "ssh:": { kind: "ssh", target: "", expires_at: now + 3_600_000 } } };
  await state.storage.put({ whiteboard_boards: [sshBoard], signed_links: [] });
  provider.requests = [];
  assert.deepEqual(await host.openAccess(browser, "b3", "ssh:"), { ssh: { command: "ssh test", expires_at: now + 300_000 } });
  assert.deepEqual(provider.requests, ["ssh sandbox 300"]);
  const sshRevoked = await host.revokeAccess("main");
  assert.equal(sshRevoked.ok, true);
  assert.match(sshRevoked.ssh_warning, /still connects until/);
  // The command lapses, but the session it opened may not have: later endings say so too.
  now += 301_000;
  assert.match((await host.revokeAccess("main")).ssh_warning, /no longer connects, but a session/);
  assert.match((await host.unpairDisplay("main")).ssh_warning, /lasts until it disconnects or the sandbox restarts/);
  await state.storage.put({ ssh_opened_until: 0, paired_browsers: [browser] });
  assert.equal((await host.revokeAccess("main")).ssh_warning, undefined);
  browser.createdAt = browser.lastSeen = now;
  for (const grant of Object.values(board.access)) grant.expires_at = now + 60_000;
  await state.storage.put({ paired_browsers: [browser], whiteboard_boards: [board] });

  // A failed mint returns nothing, but its server is already running: it still ends with the grant.
  provider.signFails = true;
  provider.requests = [];
  await assert.rejects(host.openAccess(browser, "b1", "terminal:"), /signing failed/);
  provider.signFails = false;
  assert.equal((await state.storage.get("signed_links") as any[]).length, 1);
  await host.revokeAccess("main");
  assert.deepEqual(provider.requests, ["start 7681", "revoke sandbox", "get sandbox", "stop sandbox 7681"]);

  // The alarm and a revocation share an ending, and a later open waits for it rather than losing to it.
  await state.storage.put({ whiteboard_boards: [board], access_revocations: [{ id: "one", sandboxId: "sandbox", port: 7681, linksUntil: 0, retryAt: now }] });
  let stopping!: () => void;
  const stopped = new Promise<void>((resolve) => { stopping = resolve; });
  provider.onStop = () => stopped;
  provider.requests = [];
  const endings = [host.alarm(), host.flushAccessRevocations(true)];
  const reopened = host.openAccess(browser, "b1", "terminal:");
  stopping();
  await Promise.all(endings);
  await reopened;
  provider.onStop = async () => {};
  assert.deepEqual(provider.requests, ["get sandbox", "stop sandbox 7681", "start 7681", "sign sandbox 7681 60"]);

  // A mint returning after revoke must be ended, never retained or delivered.
  await state.storage.put({ whiteboard_boards: [board], signed_links: [] });
  let release!: () => void;
  let entered!: () => void;
  const mintEntered = new Promise<void>((resolve) => { entered = resolve; });
  const mintRelease = new Promise<void>((resolve) => { release = resolve; });
  provider.onMint = async () => { entered(); await mintRelease; };
  const lateOpen = host.openAccess(browser, "b1", "terminal:");
  await mintEntered;
  assert.equal((await host.revokeAccess("main")).ok, true);
  provider.requests = [];
  release();
  await assert.rejects(lateOpen, /ended while opening/);
  assert.deepEqual(provider.requests, ["revoke sandbox", "get sandbox", "stop sandbox 7681"]);
  assert.deepEqual(await state.storage.get("signed_links"), []);
  assert.deepEqual(await state.storage.get("access_revocations"), []);

  // Other authority changes while minting fail closed too: unpairing, eviction and replacement.
  for (const change of ["unpair", "evict", "replace", "expire"]) {
    await state.storage.put({ sandbox_id: "sandbox", paired_browsers: [browser], whiteboard_boards: [board] });
    provider.onMint = async () => {
      if (change === "unpair") await state.storage.put("paired_browsers", []);
      if (change === "evict") await state.storage.put("whiteboard_boards", []);
      if (change === "replace") await state.storage.put("sandbox_id", "replacement");
      if (change === "expire") await state.storage.put("whiteboard_boards", [{ ...board,
        access: { "terminal:": { ...board.access["terminal:"], expires_at: now } },
      }]);
    };
    provider.requests = [];
    await assert.rejects(host.openAccess(browser, "b1", "terminal:"), /ended while opening/);
    assert.equal(provider.matching("revoke sandbox").length, 1, change);
    assert.equal(provider.matching("stop sandbox 7681").length, 1, change);
    assert.deepEqual(await state.storage.get("signed_links"), []);
  }
  provider.onMint = async () => {};
  await state.storage.put({ sandbox_id: "sandbox", paired_browsers: [browser] });

  // Failed provider revocation reports incomplete cleanup and stays durable for the alarm.
  await state.storage.put({ whiteboard_boards: [board], signed_links: [{ grant: "b1:terminal:", port: 7681, expiresAt: now + 60_000 }] });
  provider.fail = new Error("provider unavailable: secret-token");
  const incomplete = await host.revokeAccess("main");
  assert.equal(incomplete.ok, false);
  assert.equal(incomplete.revocation_pending, 1);
  assert.match(incomplete.warning, /retry automatically/);
  assert.doesNotMatch(JSON.stringify(records), /secret-token/, "provider error text is never recorded");
  assert.equal((await state.storage.get("access_revocations") as any[]).length, 1);
  assert.equal(await state.storage.getAlarm(), now + 30_000);
  // A browser ping must not postpone an already scheduled provider retry.
  now += 10_000;
  await host.touchBrowser(browser, true);
  assert.equal(await state.storage.getAlarm(), now + 20_000);
  provider.fail = null;
  provider.requests = [];
  // A replacement and a fresh object must not act on the new Sandbox.
  const restarted = Object.assign(new AccessHost(), host, { attachedSandboxId: "replacement" });
  now += 20_000;
  await restarted.alarm();
  assert.deepEqual(await state.storage.get("access_revocations"), []);
  assert.deepEqual(provider.requests, ["revoke sandbox", "get sandbox", "stop sandbox 7681"]);

  // With no call or messages, the last browser's expiry still ends its Display and links.
  const expiry = now + 5000;
  browser.lastSeen = expiry - 3_600_000;
  browser.createdAt = browser.lastSeen;
  await state.storage.put({ paired_browsers: [browser], whiteboard_boards: [board], signed_links: [{
    grant: "b1:terminal:", port: 7681, expiresAt: now + 12 * 3_600_000,
  }] });
  host.liveAttached = true;
  await host.scheduleAlarm();
  assert.equal(await state.storage.getAlarm(), expiry, "browser expiry wins over call keepalive");
  host.liveAttached = false;
  now = expiry;
  provider.requests = [];
  await host.alarm();
  assert.deepEqual(await state.storage.get("paired_browsers"), []);
  assert.deepEqual(await state.storage.get("whiteboard_boards"), []);
  assert.deepEqual(provider.requests, ["revoke sandbox", "get sandbox", "stop sandbox 7681"]);
  assert.equal(await state.storage.getAlarm(), null);
  assert.ok(records.some((item) => item.event === "display.ended"), "Display retirement retains its diagnostic event");

  // Crash immediately after each authority-removal transaction, before any provider I/O.
  // A fresh object must find the links to end and an already-committed retry alarm.
  for (const operation of ["revoke", "unpair", "expire", "evict", "replace"]) {
    const paired = { ...browser, createdAt: now, lastSeen: now };
    const b = { id: "crash", display: "main", access: {
      "terminal:": { kind: "terminal", target: "", expires_at: now + 3_600_000 },
      "ssh:": { kind: "ssh", target: "", expires_at: now + 3_600_000 },
    } };
    const boards = operation === "evict" ? [b, ...Array.from({ length: 4 }, (_, i) => ({ id: `other${i}`, display: "main" }))] : [b];
    const saved = storage({ sandbox_id: "old-sandbox", paired_browsers: [paired], whiteboard_boards: boards,
      signed_links: [{ grant: "crash:terminal:", port: 7681, expiresAt: now + 3_600_000 }],
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
    assert.equal((await saved.get("access_revocations") as any[]).length, 1, operation);
    assert.equal(await saved.getAlarm(), now + 1, operation);
    const resumed = Object.assign(new AccessHost(), host, {
      state: { storage: saved, getWebSockets: () => [] }, attachedSandboxId: "new-sandbox",
    });
    provider.requests = [];
    await resumed.alarm();
    assert.deepEqual(provider.requests, ["revoke old-sandbox", "get old-sandbox", "stop old-sandbox 7681"], operation);
    assert.deepEqual(await saved.get("access_revocations"), [], operation);
    assert.equal(await resumed.touchBrowser(paired, true), !["unpair", "expire", "replace"].includes(operation), "an old snapshot cannot resurrect removed pairings");
  }

  // The one-time back-patch ends everything minted on the old provider, but not pairings or boards.
  const start = source.indexOf("      // One-time back-patch for Daytona v2");
  const patch = source.slice(start, source.indexOf("\n      await this.scheduleAlarm();", start));
  const migrate = new Function(stripTypeScriptTypes(`async function migrate(state) {${patch}}`) + "; return migrate;")();
  const migrationState = { storage: storage({
    access_revocations: [{ token: "old" }], signed_links: [{ token: "old" }], ssh_tokens: [{ token: "old" }],
    "gateway_preview:old": { url: "https://old", token: "old" },
    paired_browsers: [{ display: "main" }], whiteboard_boards: [{ id: "legacy", access: board.access }],
  }) };
  await migrate(migrationState);
  for (const key of ["access_revocations", "signed_links", "ssh_tokens", "gateway_preview:old"]) {
    assert.equal(await migrationState.storage.get(key), undefined, key);
  }
  assert.deepEqual(await migrationState.storage.get("paired_browsers"), [{ display: "main" }]);
  assert.deepEqual(await migrationState.storage.get("whiteboard_boards"), [{ id: "legacy" }]);
  await migrationState.storage.put({ whiteboard_boards: [board] });
  await migrate(migrationState);
  assert.deepEqual(await migrationState.storage.get("whiteboard_boards"), [board], "new grants survive later starts");
} finally {
  Date.now = originalNow;
}
console.log("signed access bounds, refused opens, takeover endings, and provider back-patch: passed");
