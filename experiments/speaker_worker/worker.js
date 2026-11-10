import * as ort from "onnxruntime-web";

ort.env.wasm.numThreads = 1;
ort.env.wasm.proxy = false;
ort.env.wasm.wasmPaths = {wasm:"dummy.wasm"};
ort.env.logLevel = "error";
const shape = FEATURE_SHAPE;
const featureBytes = shape.reduce((a,b)=>a*b,1)*4;
let sessionPromise;
let busy = false;
let calls = 0;
let initMs;
const memoryBytes = () => globalThis.__ortMemories?.length ? globalThis.__ortMemories.reduce((n,m)=>n+m.buffer.byteLength,0) : null;
const json = (value,status=200)=>Response.json(value,{status,headers:{"Cache-Control":"no-store"}});

async function boundedBody(request, maxBytes) {
  const reader=request.body?.getReader();
  if(!reader) throw Error("Missing body");
  const output=new Uint8Array(maxBytes);
  let offset=0;
  while(true) {
    const {done,value}=await reader.read();
    if(done) break;
    if(offset+value.length>maxBytes) {await reader.cancel(); throw Error("Body too large");}
    output.set(value,offset); offset+=value.length;
  }
  if(offset!==maxBytes) throw Error("Wrong body size");
  return output.buffer;
}

export default {
  async fetch(request,env) {
    const url=new URL(request.url);
    if(url.pathname==="/health" && request.method==="GET") return json({ok:true});
    // Fail closed before parsing, model creation or inference, even before secret setup.
    if(!env.EXPERIMENT_TOKEN || request.headers.get("Authorization")!==`Bearer ${env.EXPERIMENT_TOKEN}`)
      return json({error:"Unauthorized"},401);
    if(url.pathname!==ENDPOINT || request.method!=="POST") return json({error:"Not found"},404);
    if(request.headers.get("Content-Type")!=="application/octet-stream") return json({error:"Expected float32 little-endian features"},415);
    if(busy) return json({error:"Busy"},429);
    busy=true;
    try {
      let buffer;
      try {buffer=await boundedBody(request,featureBytes);} catch {return json({error:"Invalid feature body"},400);}
      const features=new Float32Array(buffer);
      if(!features.every(Number.isFinite)) return json({error:"Nonfinite features"},400);
      const cold=!sessionPromise;
      if(!sessionPromise) {
        const start=Date.now();
        sessionPromise=ort.InferenceSession.create(new Uint8Array(globalThis.__modelBytes),{
          executionProviders:["wasm"], graphOptimizationLevel:"all",
          enableCpuMemArena:true, enableMemPattern:true,
        }).then(s=>{initMs=Date.now()-start; return s;});
      }
      const session=await sessionPromise;
      const before=memoryBytes();
      const start=Date.now();
      const outputs=await session.run({[INPUT_NAME]:new ort.Tensor("float32",features,shape)});
      const inferenceMs=Date.now()-start;
      const raw=Array.from(outputs.embedding.data);
      const frontendFeatures=outputs.features ? Array.from(outputs.features.data) : undefined;
      outputs.features?.dispose();
      outputs.embedding.dispose();
      const norm=Math.hypot(...raw);
      if(!Number.isFinite(norm) || norm===0) throw Error("Invalid embedding");
      return json({raw,normalized:raw.map(v=>v/norm),frontendFeatures,cold,call:++calls,initMs,inferenceMs,
                   wasmMemoryBefore:before,wasmMemoryAfter:memoryBytes()});
    } catch(error) {
      // No input, embedding or authorization value is ever logged.
      return json({error:"Inference failed",detail:String(error).slice(0,500),wasmMemoryBytes:memoryBytes()},500);
    } finally {busy=false;}
  }
};
