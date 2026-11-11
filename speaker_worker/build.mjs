// Bundle worker.js with a patched ONNX Runtime Web for workerd: ORT is told to
// instantiate the statically imported, precompiled WASM module (workerd forbids
// compiling WASM from bytes at runtime), and its variable dynamic imports are
// removed. Technique from CosteGieF (microsoft/onnxruntime#27400,
// cloudflare/workers-sdk#12616). ORT is pinned; the patch fails closed if its
// internals change. The model must match the hash named in EMBEDDING_SPACE.
import * as esbuild from "esbuild";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { resolve } from "node:path";

const ONNX_SHA = "c2653e3df4e2f5e3f2c495951dd7cf0cb2784226fed67b5f0a7f42d4bbfa7eb1";
const model = readFileSync("artifacts/b3-waveform.onnx");
const digest = createHash("sha256").update(model).digest("hex");
if (digest !== ONNX_SHA) throw Error(`artifacts/b3-waveform.onnx is ${digest}, expected ${ONNX_SHA}: run export.py`);
const worker = readFileSync("worker.js", "utf8");
if (!worker.includes(`onnx-waveform@${ONNX_SHA.slice(0, 16)}/`)) throw Error("EMBEDDING_SPACE does not name the bundled model");

mkdirSync(".worker", { recursive: true });
const dist = resolve("node_modules/onnxruntime-web/dist");
await esbuild.build({
  entryPoints: ["worker.js"], outfile: ".worker/index.js", bundle: true,
  format: "esm", target: "es2022", platform: "browser", minify: false,
  external: ["cloudflare:workers"],
  alias: { "onnxruntime-web": resolve(dist, "ort.wasm.bundle.min.mjs") },
});
let code = readFileSync(".worker/index.js", "utf8");
const configs = [...code.matchAll(/let\s+(\w+)\s*=\s*\{\s*numThreads:\s*(\w+)\s*\}/g)];
if (configs.length !== 1) throw Error(`ORT config matches ${configs.length}, expected 1`);
const config = configs[0][1];
const factories = [...code.matchAll(new RegExp(`(\\w+)\\(${config}\\)\\.then\\(`, "g"))];
if (factories.length !== 1) throw Error("ORT factory pattern changed");
code = code.replace(factories[0][0], `${config}.instantiateWasm=(imports,cb)=>{
  const instance=new WebAssembly.Instance(__ORT_WASM__,imports);
  cb(instance,__ORT_WASM__); return instance.exports;
}; ${factories[0][0]}`);
let removed = 0;
code = code.replace(/await import\([\s\S]*?\)/g, (match) => {
  if (/await import\(\s*["'`]/.test(match)) return match;
  removed++;
  return 'await Promise.reject(new Error("dynamic import disabled in workerd"))';
});
if (removed < 1) throw Error("No dynamic imports patched: inspect pinned ORT");
code = `import __ORT_WASM__ from "./ort-wasm-simd-threaded.wasm";
import __MODEL_BYTES__ from "./b3-waveform.onnx";
globalThis.__modelBytes=__MODEL_BYTES__;
` + code;
writeFileSync(".worker/index.js", code);
copyFileSync(resolve(dist, "ort-wasm-simd-threaded.wasm"), ".worker/ort-wasm-simd-threaded.wasm");
copyFileSync("artifacts/b3-waveform.onnx", ".worker/b3-waveform.onnx");
console.log(JSON.stringify({ patchedConfig: config, removedDynamicImports: removed, onnx: digest }));
