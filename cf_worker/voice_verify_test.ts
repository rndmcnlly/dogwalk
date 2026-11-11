// node --experimental-strip-types cf_worker/voice_verify_test.ts
// warmSpeaker never delays the caller and never fails a call: it returns before any D1 or
// SPEAKER work, hands that work to waitUntil, and swallows every failure.
import assert from "node:assert/strict";
import { warmSpeaker } from "./src/voice_verify.ts";

type Calls = { queries: number[]; warms: number };

function harness(options: { bound?: boolean; enrolled?: boolean; warm?: () => Promise<{ cold: boolean }>; dbFails?: boolean }) {
  const calls: Calls = { queries: [], warms: 0 };
  const pending: Promise<unknown>[] = [];
  const DB = {
    prepare: (sql: string) => ({
      bind: (userId: number) => ({
        first: async () => {
          assert.match(sql, /FROM voice_references WHERE user_id = \?/);
          calls.queries.push(userId);
          if (options.dbFails) throw new Error("D1 unavailable");
          return options.enrolled ? { ok: 1 } : null;
        },
      }),
    }),
  };
  const SPEAKER = options.bound === false ? undefined : {
    warm: () => { calls.warms++; return (options.warm ?? (async () => ({ cold: true })))(); },
  };
  const env = { DB, SPEAKER } as never;
  const ctx = { waitUntil: (promise: Promise<unknown>) => { pending.push(promise); } };
  return { env, ctx, calls, pending };
}

const quiet = { error: console.error, log: console.log };
console.error = console.log = () => {};
try {
  // Unbound (local dev, tests, or before the speaker Worker is deployed): nothing scheduled.
  {
    const { env, ctx, calls, pending } = harness({ bound: false, enrolled: true });
    assert.equal(warmSpeaker(env, ctx, 7), undefined);
    assert.equal(pending.length, 0);
    assert.deepEqual(calls, { queries: [], warms: 0 });
  }
  // Enrolled User: one background task, which warms; nothing ran before warmSpeaker returned.
  {
    const { env, ctx, calls, pending } = harness({ enrolled: true });
    warmSpeaker(env, ctx, 7);
    assert.equal(pending.length, 1);
    assert.deepEqual(calls, { queries: [7], warms: 0 }, "the D1 query starts synchronously, the RPC does not");
    await Promise.all(pending);
    assert.deepEqual(calls, { queries: [7], warms: 1 });
  }
  // No Voice Reference: no RPC.
  {
    const { env, ctx, calls, pending } = harness({ enrolled: false });
    warmSpeaker(env, ctx, 8);
    await Promise.all(pending);
    assert.deepEqual(calls, { queries: [8], warms: 0 });
  }
  // A verifier that never answers does not hold up the caller of warmSpeaker.
  {
    const { env, ctx, pending } = harness({ enrolled: true, warm: () => new Promise(() => {}) });
    const started = performance.now();
    warmSpeaker(env, ctx, 9);
    assert.ok(performance.now() - started < 50);
    assert.equal(pending.length, 1);
  }
  // Failures (an older speaker Worker without warm(), busy, D1 down) resolve quietly.
  for (const options of [
    { enrolled: true, warm: async () => { throw new Error("warm is not a function"); } },
    { enrolled: true, warm: async () => { throw new Error("busy: too many concurrent requests"); } },
    { enrolled: true, dbFails: true },
  ]) {
    const { env, ctx, pending } = harness(options);
    warmSpeaker(env, ctx, 10);
    await assert.doesNotReject(Promise.all(pending));
  }
} finally {
  Object.assign(console, quiet);
}
console.log("voice_verify_test: ok");
