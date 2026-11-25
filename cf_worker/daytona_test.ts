// node --experimental-strip-types cf_worker/daytona_test.ts
// The provider adapter against a scripted fetch: what the integration mocks do not reach.
import assert from "node:assert/strict";
import { execute, getSandbox, startSandbox } from "./src/daytona.ts";

const env = { DAYTONA_API_KEY: "key" } as any;
const box = { toolbox: "https://toolbox.test/sbx", headers: {} };
const realFetch = globalThis.fetch;
let replies: Array<[number, unknown]> = [];
let asked: string[] = [];
globalThis.fetch = (async (url: string, init: RequestInit = {}) => {
  asked.push(`${init.method ?? "GET"} ${new URL(url).pathname}`);
  const [status, body] = replies.length > 1 ? replies.shift()! : replies[0];
  return new Response(JSON.stringify(body), { status });
}) as typeof fetch;
const script = (...next: Array<[number, unknown]>) => { replies = next; asked = []; };
try {
  const page = (data: string, eof: boolean) => ({ frames: [{ data }], nextCursor: "c", eof });
  script([201, { id: "p", state: "terminal", exitCode: 3, logs: page("a", false) }], [200, page("b", false)], [200, page("c", true)]);
  assert.deepEqual(await execute(box, "x", 1), { result: "abc", exitCode: 3 });
  // Output past the bound is an error, never a short success.
  script([201, { id: "p", state: "terminal", exitCode: 0, logs: page("a", false) }], [200, page("b", false)]);
  await assert.rejects(execute(box, "x", 1), /too much output/);
  assert.equal(asked.length, 100);
  script([201, { id: "p", state: "running" }]);
  await assert.rejects(execute(box, "x", 1), /did not finish/);

  // A deleted Sandbox still answers 200.
  script([200, { id: "s", status: "deleted" }]);
  await assert.rejects(getSandbox(env, "s"), (error: any) => error.status === 404);

  // A refused start is retried while the Sandbox falls asleep, and left alone once another started it.
  script([409, {}], [200, { id: "s", status: "pausing" }], [409, {}], [200, { id: "s", status: "paused" }], [202, { id: "s", status: "assigned" }]);
  assert.equal((await startSandbox(env, "s")).status, "assigned");
  assert.equal(asked.filter((item) => item.startsWith("POST")).length, 3);
  script([409, {}], [200, { id: "s", status: "booting" }]);
  assert.equal((await startSandbox(env, "s")).status, "booting");
  script([403, {}]);
  await assert.rejects(startSandbox(env, "s"), /Daytona 403/);
} finally {
  globalThis.fetch = realFetch;
}
console.log("command output, deleted Sandboxes, and refused starts: passed");
