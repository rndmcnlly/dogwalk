// node speaker_worker/worker_test.mjs
// The warm() state in worker.js against a stubbed ONNX Runtime and Workers runtime, so it
// runs without the model, the bundle, or workerd: concurrent warms share one load and one
// inference, a failed warm is retried, and warm state is shared with embed() and verify().
import assert from "node:assert/strict";
import { registerHooks } from "node:module";

// The stub's counters and failure switches, reset for each fresh copy of worker.js.
const ort = globalThis.__ortStub = { creates: 0, runs: 0, failCreate: 0, failRun: 0 };
const STUBS = {
  "cloudflare:workers": "export class WorkerEntrypoint {}",
  "onnxruntime-web": `
    const stub = globalThis.__ortStub;
    const tick = () => new Promise((resolve) => setTimeout(resolve, 5));
    export const env = { wasm: {} };
    export class Tensor { constructor(type, data, dims) { Object.assign(this, { type, data, dims }); } }
    export const InferenceSession = {
      async create() {
        stub.creates++;
        await tick();
        if (stub.failCreate > 0 && stub.failCreate--) throw new Error("create failed");
        return {
          async run() {
            stub.runs++;
            await tick();
            if (stub.failRun > 0 && stub.failRun--) throw new Error("run failed");
            return { embedding: { data: new Float32Array(192).fill(1), dispose() {} } };
          },
        };
      },
    };`,
};
registerHooks({
  resolve: (specifier, context, next) =>
    specifier in STUBS ? { url: `stub:${specifier}`, shortCircuit: true } : next(specifier, context),
  load: (url, context, next) =>
    url.startsWith("stub:") ? { format: "module", source: STUBS[url.slice(5)], shortCircuit: true } : next(url, context),
});
globalThis.__modelBytes = new ArrayBuffer(0);

let copies = 0;
async function freshVerifier() {
  Object.assign(ort, { creates: 0, runs: 0, failCreate: 0, failRun: 0 });
  const { SpeakerVerifier } = await import(`./worker.js?copy=${copies++}`);
  return new SpeakerVerifier();
}

// Four seconds of a voiced, gated 16 kHz tone, enough "speech" for preprocess.js.
function audio() {
  const rate = 16000, samples = new Int16Array(rate * 4);
  let phase = 0;
  for (let i = 0; i < samples.length; i++) {
    const t = i / rate;
    phase += (2 * Math.PI * (110 + 20 * Math.sin(2 * Math.PI * 0.7 * t))) / rate;
    let voice = 0;
    for (let k = 1; k < 12; k++) voice += Math.sin(k * phase) / k;
    const gate = t > 1.2 && Math.sin(2 * Math.PI * 1.3 * t) > -0.2 ? 1 : 0;
    samples[i] = Math.round(Math.max(-1, Math.min(1, 0.2 * voice * gate)) * 32767);
  }
  return { audio: samples.buffer, format: "pcm_s16le", sampleRate: rate };
}

// Concurrent warms share one model load and one inference; exactly one did the work.
{
  const verifier = await freshVerifier();
  const results = await Promise.all([verifier.warm(), verifier.warm(), verifier.warm()]);
  assert.deepEqual(results.map((r) => r.cold), [true, false, false]);
  assert.deepEqual([ort.creates, ort.runs], [1, 1]);
  assert.deepEqual(await verifier.warm(), { cold: false });
  assert.deepEqual([ort.creates, ort.runs], [1, 1], "a warm isolate runs nothing");
}

// A failed model load or primer inference rejects, and the next warm tries again.
for (const failure of ["failCreate", "failRun"]) {
  const verifier = await freshVerifier();
  ort[failure] = 1;
  await assert.rejects(verifier.warm(), /inference-failed/);
  assert.deepEqual(await verifier.warm(), { cold: true }, `retry after ${failure}`);
  assert.deepEqual(await verifier.warm(), { cold: false });
  assert.equal(ort.creates, failure === "failCreate" ? 2 : 1, "a loaded session is kept across a failed run");
}

// A real inference warms the isolate: warm() afterwards costs nothing.
for (const method of ["embed", "verify"]) {
  const verifier = await freshVerifier();
  const reference = { embedding: Array(192).fill(1), embeddingSpace: (await verifier.embed(audio())).embeddingSpace };
  if (method === "verify") {
    const result = await verifier.verify({ ...audio(), reference });
    assert.ok(Math.abs(result.cosineSimilarity - 1) < 1e-9);
  }
  const runs = ort.runs;
  assert.deepEqual(await verifier.warm(), { cold: false }, `warm after ${method}`);
  assert.deepEqual([ort.creates, ort.runs], [1, runs]);
}

// Warming first leaves real requests on the loaded session, and a failed embed does not mark
// the isolate warm.
{
  const verifier = await freshVerifier();
  await verifier.warm();
  await verifier.embed(audio());
  assert.deepEqual([ort.creates, ort.runs], [1, 2]);
  const cold = await freshVerifier();
  ort.failRun = 1;
  await assert.rejects(cold.embed(audio()), /inference-failed/);
  assert.deepEqual(await cold.warm(), { cold: true });
}

console.log("worker_test: ok");
