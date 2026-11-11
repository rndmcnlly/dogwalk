// Local split of a cold verifier request into model load, first inference, and warm
// inference, with the WASM linear memory after each, under the deployed ORT settings and
// alternatives. Node on a laptop is faster than a Workers isolate, so compare ratios, not
// milliseconds, with latency_probe.py. Needs artifacts/b3-waveform.onnx (export.py).
//   node load_bench.mjs [all|extended|basic|disabled ...]
import { readFileSync } from "node:fs";
import * as ort from "onnxruntime-web";

ort.env.wasm.numThreads = 1;
ort.env.wasm.proxy = false;
ort.env.logLevel = "error";

const model = new Uint8Array(readFileSync(new URL("artifacts/b3-waveform.onnx", import.meta.url)));
const levels = process.argv.slice(2).length ? process.argv.slice(2) : ["all", "disabled"];
const waveform = new Float32Array(48000).map((_, i) => 0.1 * Math.sin(i / 7) * Math.sin(i / 3000));
const mib = () => {
  const memory = ort.env.wasm.wasmMemory ?? globalThis.__ortMemory;
  return memory ? +(memory.buffer.byteLength / 2 ** 20).toFixed(1) : null;
};

for (const level of levels) {
  let started = performance.now();
  const session = await ort.InferenceSession.create(model, {
    executionProviders: ["wasm"], graphOptimizationLevel: level, enableCpuMemArena: true, enableMemPattern: true,
  });
  const row = { level, create_ms: Math.round(performance.now() - started) };
  for (const label of ["first_run_ms", "second_run_ms", "third_run_ms"]) {
    started = performance.now();
    const out = await session.run({ waveform: new ort.Tensor("float32", waveform, [1, waveform.length]) }, ["embedding"]);
    row[label] = Math.round(performance.now() - started);
    row[label.replace("_ms", "_head")] = Array.from(out.embedding.data.slice(0, 3), (v) => +v.toFixed(6));
    out.embedding.dispose();
  }
  row.rss_mib = +(process.memoryUsage().rss / 2 ** 20).toFixed(0);
  row.array_buffers_mib = +(process.memoryUsage().arrayBuffers / 2 ** 20).toFixed(0);
  console.log(JSON.stringify(row));
  await session.release();
}
