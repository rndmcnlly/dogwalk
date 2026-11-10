// The precompiled-WASM injection follows CosteGieF/ort-cloudflare-workers,
// discussions microsoft/onnxruntime#27400 and cloudflare/workers-sdk#12616.
// Pin ORT and fail closed when its internal config pattern changes.
import * as esbuild from "esbuild";
import {mkdirSync, readFileSync, writeFileSync, copyFileSync} from "node:fs";
import {resolve} from "node:path";

mkdirSync(".worker", {recursive:true});
const dist = resolve("node_modules/onnxruntime-web/dist");
const meta = JSON.parse(readFileSync("local_results.json", "utf8"));
const waveform = process.argv.includes("--waveform");
const inputShape = waveform ? [1,48000] : meta.feature_shape;
await esbuild.build({
  entryPoints:["worker.js"], outfile:".worker/index.js", bundle:true,
  format:"esm", target:"es2022", platform:"browser", minify:false,
  define:{FEATURE_SHAPE:JSON.stringify(inputShape), INPUT_NAME:JSON.stringify(waveform ? "waveform" : "features"),
          ENDPOINT:JSON.stringify(waveform ? "/waveform" : "/embed")},
  alias:{"onnxruntime-web":resolve(dist,"ort.wasm.bundle.min.mjs")},
});
let code = readFileSync(".worker/index.js", "utf8");
const configs = [...code.matchAll(/let\s+(\w+)\s*=\s*\{\s*numThreads:\s*(\w+)\s*\}/g)];
if(configs.length !== 1) throw Error(`ORT config matches ${configs.length}, expected 1`);
const config = configs[0][1];
const factoryRe = new RegExp(`(\\w+)\\(${config}\\)\\.then\\(`, "g");
const factories = [...code.matchAll(factoryRe)];
if(factories.length !== 1) throw Error("ORT factory pattern changed");
code = code.replace(factories[0][0], `${config}.instantiateWasm=(imports,cb)=>{
  const instance=new WebAssembly.Instance(__ORT_WASM__,imports);
  globalThis.__ortMemories=[...new Set([
    ...Object.values(instance.exports),
    ...Object.values(imports).flatMap(namespace=>Object.values(namespace))
  ].filter(x=>x instanceof WebAssembly.Memory))];
  cb(instance,__ORT_WASM__); return instance.exports;
}; ${factories[0][0]}`);
let removed=0;
code = code.replace(/await import\([\s\S]*?\)/g, match=>{
  if(/await import\(\s*["'`]/.test(match)) return match;
  removed++;
  return 'await Promise.reject(new Error("dynamic import disabled in workerd"))';
});
if(removed < 1) throw Error("No dynamic imports patched: inspect pinned ORT");
code = `import __ORT_WASM__ from "./ort-wasm-simd-threaded.wasm";
import __MODEL_BYTES__ from "./b3.onnx";
globalThis.__modelBytes=__MODEL_BYTES__;
` + code;
writeFileSync(".worker/index.js",code);
copyFileSync(resolve(dist,"ort-wasm-simd-threaded.wasm"),".worker/ort-wasm-simd-threaded.wasm");
copyFileSync(waveform ? "artifacts/b3-waveform.onnx" : "artifacts/b3.onnx",".worker/b3.onnx");
console.log(JSON.stringify({patchedConfig:config,removedDynamicImports:removed,inputShape,waveform}));
