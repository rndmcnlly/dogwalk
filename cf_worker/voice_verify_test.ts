// node --experimental-strip-types cf_worker/voice_verify_test.ts
// warmSpeaker never delays the caller and never fails a call: it returns before any SPEAKER or
// Durable Object work, hands that work to waitUntil, and swallows every failure. With an
// identity hash it warms through that User's VoiceSession (userSpeaker), whose side
// (hostSpeaker) warms first so every score reports whether it loaded the model.
import assert from "node:assert/strict";
import { hostSpeaker, userSpeaker, warmSpeaker } from "./src/voice_verify.ts";

type Calls = { warms: number; verifies: number; objects: string[]; paths: string[]; verified: Record<string, any>[] };

function harness(options: { bound?: boolean; warm?: () => Promise<{ cold: boolean }>; verify?: () => Promise<unknown> }) {
  const calls: Calls = { warms: 0, verifies: 0, objects: [], paths: [], verified: [] };
  const pending: Promise<unknown>[] = [];
  const SPEAKER = options.bound === false ? undefined : {
    warm: () => { calls.warms++; return (options.warm ?? (async () => ({ cold: true })))(); },
    verify: async (input: Record<string, any>) => { calls.verifies++; calls.verified.push(input); return (options.verify ?? (async () => ({ cosineSimilarity: 0.6, embeddingSpace: "s", audioSeconds: 3, speechSeconds: 2 })))(); },
    embed: async () => ({ embedding: [1, 0], embeddingSpace: "s", audioSeconds: 3, speechSeconds: 2 }),
  };
  const env: Record<string, unknown> = { SPEAKER };
  // The VoiceSession stub answers by running hostSpeaker against the same env, as the object does.
  env.VOICE_SESSIONS = {
    idFromName: (name: string) => name,
    get: (id: string) => ({
      fetch: async (url: string, init: RequestInit) => {
        calls.objects.push(id);
        const path = new URL(url).pathname;
        calls.paths.push(path);
        return hostSpeaker(env as never, new Request(url, init), path.slice("/speaker/".length));
      },
    }),
  };
  const ctx = { waitUntil: (promise: Promise<unknown>) => { pending.push(promise); } };
  return { env: env as never, ctx, calls, pending };
}

const quiet = { error: console.error, log: console.log };
console.error = console.log = () => {};
try {
  // Unbound (local dev, tests, or before the speaker Worker is deployed): nothing scheduled.
  {
    const { env, ctx, calls, pending } = harness({ bound: false });
    assert.equal(warmSpeaker(env, ctx, "user-hash"), undefined);
    assert.equal(userSpeaker(env, "user-hash"), null);
    assert.equal(pending.length, 0);
    assert.deepEqual(calls.objects, []);
  }
  // A User's warm goes through their VoiceSession, in the background.
  {
    const { env, ctx, calls, pending } = harness({});
    assert.equal(warmSpeaker(env, ctx, "user-hash"), undefined, "nothing to await");
    assert.equal(pending.length, 1);
    await Promise.all(pending);
    assert.deepEqual(calls.objects, ["user-hash"]);
    assert.deepEqual(calls.paths, ["/speaker/warm"]);
    assert.equal(calls.warms, 1);
  }
  // Without an identity hash it warms this machine directly.
  {
    const { env, ctx, calls, pending } = harness({});
    warmSpeaker(env, ctx);
    await Promise.all(pending);
    assert.deepEqual(calls.objects, []);
    assert.equal(calls.warms, 1);
  }
  // A verifier that never answers does not hold up the caller of warmSpeaker.
  {
    const { env, ctx, pending } = harness({ warm: () => new Promise(() => {}) });
    const started = performance.now();
    warmSpeaker(env, ctx, "user-hash");
    assert.ok(performance.now() - started < 50);
    assert.equal(pending.length, 1);
  }
  // Failures (an older speaker Worker without warm(), busy) resolve quietly.
  for (const warm of [async () => { throw new Error("warm is not a function"); }, async () => { throw new Error("busy: too many"); }]) {
    const { env, ctx, pending } = harness({ warm });
    warmSpeaker(env, ctx, "user-hash");
    await assert.doesNotReject(Promise.all(pending));
  }
  // A hosted score warms first and reports cold; the audio and reference reach the verifier.
  {
    const { env, calls } = harness({});
    const speaker = userSpeaker(env, "user-hash")!;
    const samples = Int16Array.from({ length: 8000 }, (_, i) => (i % 200) - 100);
    const reference = { embedding: [0.6, 0.8], embeddingSpace: "s" };
    const result = await speaker.verify({ audio: samples.slice().buffer, format: "pcm_s16le", sampleRate: 8000, reference });
    assert.deepEqual(result, { cosineSimilarity: 0.6, embeddingSpace: "s", audioSeconds: 3, speechSeconds: 2, cold: true });
    assert.deepEqual([calls.warms, calls.verifies], [1, 1]);
    // The audio, its format and rate, and the reference cross the hop unchanged.
    const sent = calls.verified[0];
    assert.deepEqual(new Int16Array(sent.audio), samples);
    assert.equal(sent.format, "pcm_s16le");
    assert.equal(sent.sampleRate, 8000);
    assert.deepEqual(sent.reference, reference);
    const embedded = await speaker.embed({ audio: new Int16Array(8000).buffer, format: "pcm_s16le", sampleRate: 8000 });
    assert.equal(embedded.embeddingSpace, "s");
  }
  // A verifier error keeps its code across the hop, so callers classify it as before.
  {
    const { env } = harness({ verify: async () => { throw new Error("insufficient-audio: too little speech"); } });
    await assert.rejects(userSpeaker(env, "user-hash")!.verify({ audio: new ArrayBuffer(2), format: "pcm_s16le", sampleRate: 8000,
      reference: { embedding: [1], embeddingSpace: "s" } }), /^Error: insufficient-audio: too little speech$/);
  }
} finally {
  Object.assign(console, quiet);
}
console.log("voice_verify_test: ok");
