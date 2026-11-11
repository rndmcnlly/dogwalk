// Local split of a cold verifier request into model load, first inference, and warm
// inference, under the deployed ORT graph optimization level and alternatives. Node on
// a laptop is faster than a Workers isolate, so compare ratios, not milliseconds, with
// latency_probe.py. Needs artifacts/b3-waveform.onnx (export.py). ORT initializes its WASM
// backend on the first session in a process, so each level runs in a fresh process and
// every create_ms includes that setup.
//   node load_bench.mjs [all|extended|basic|disabled ...]
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import * as ort from "onnxruntime-web";

const requested = process.argv.slice(2).length ? process.argv.slice(2) : ["all", "disabled"];
if (requested.length > 1) {
  for (const level of requested) {
    process.stdout.write(execFileSync(process.execPath, [fileURLToPath(import.meta.url), level]));
  }
  process.exit(0);
}

ort.env.wasm.numThreads = 1;
ort.env.wasm.proxy = false;
ort.env.logLevel = "error";

const model = new Uint8Array(readFileSync(new URL("artifacts/b3-waveform.onnx", import.meta.url)));
const waveform = new Float32Array(48000).map((_, i) => 0.1 * Math.sin(i / 7) * Math.sin(i / 3000));

{
  const [level] = requested;
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
  console.log(JSON.stringify(row));
}
