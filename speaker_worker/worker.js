// Dogwalk's speaker verification Worker: a stateless ReDimNet2 B3 waveform model
// in ONNX Runtime WASM, reachable only through a Service Binding RPC entrypoint.
// It holds no per-User state and never logs audio, embeddings, or scores; the
// calling Worker owns Voice References, thresholds, and every decision.
import { WorkerEntrypoint } from "cloudflare:workers";
import * as ort from "onnxruntime-web";
import { MODEL_RATE, SpeakerError, WINDOW_SECONDS, modelInput } from "./preprocess.js";

// Names the weights, exported graph, input contract, frontend, and normalization.
// The hashes are checked at build time against the bundled model (build.mjs). Not
// exported: workerd treats every module export as an entrypoint, and local dev refuses a
// string one.
const EMBEDDING_SPACE =
  "redimnet2-b3-vox2-lm@02d14efdc1845738/onnx-waveform@c2653e3df4e2f5e3/pcm16k-mono-f32-48000/tfmelbanks-native/l2-cosine";
const DIMENSION = 192;
const MAX_SECONDS = 30;
const MAX_QUEUED = 4;

ort.env.wasm.numThreads = 1;
ort.env.wasm.proxy = false;
ort.env.wasm.wasmPaths = { wasm: "dummy.wasm" };
ort.env.logLevel = "error";

let sessionPromise;
let queue = Promise.resolve();
let queued = 0;
// Settles once this isolate has run an inference, so a later warm() costs nothing.
let warmed;

function session() {
  sessionPromise ??= ort.InferenceSession.create(new Uint8Array(globalThis.__modelBytes), {
    executionProviders: ["wasm"], graphOptimizationLevel: "all", enableCpuMemArena: true, enableMemPattern: true,
  }).catch((error) => { sessionPromise = undefined; throw error; });
  return sessionPromise;
}

// One inference at a time per isolate (single-threaded WASM), with a short queue.
function serialized(task) {
  if (queued >= MAX_QUEUED) throw new SpeakerError("busy", "too many concurrent requests");
  queued++;
  const run = queue.then(task);
  queue = run.catch(() => {}).finally(() => { queued--; });
  return run;
}

async function embedWaveform(waveform) {
  if (waveform.length !== WINDOW_SECONDS * MODEL_RATE) throw new SpeakerError("inference-failed", "bad window length");
  try {
    const model = await session();
    const outputs = await model.run({ waveform: new ort.Tensor("float32", waveform, [1, waveform.length]) }, ["embedding"]);
    const raw = Array.from(outputs.embedding.data);
    outputs.embedding.dispose();
    warmed ??= Promise.resolve();
    const norm = Math.hypot(...raw);
    if (raw.length !== DIMENSION || !Number.isFinite(norm) || norm === 0) throw new Error("degenerate embedding");
    return raw.map((v) => v / norm);
  } catch (error) {
    throw new SpeakerError("inference-failed", String(error?.message ?? error).slice(0, 200));
  }
}

// A fixed voiced-like window for warm(): not speech, but a full-length model input.
function primer() {
  return Float32Array.from({ length: WINDOW_SECONDS * MODEL_RATE }, (_, i) => 0.1 * Math.sin(i / 7) * Math.sin(i / 3000));
}

function checkAudio(input) {
  if (!input || typeof input !== "object") throw new SpeakerError("invalid-input", "expected an object");
  if (input.format !== "pcm_s16le") throw new SpeakerError("invalid-input", 'format must be "pcm_s16le"');
  return modelInput(input.audio, input.sampleRate, MAX_SECONDS);
}

function checkReference(reference) {
  if (reference?.embeddingSpace !== EMBEDDING_SPACE)
    throw new SpeakerError("incompatible-reference", `reference embedding space is not ${EMBEDDING_SPACE}`);
  const e = reference.embedding;
  if (!Array.isArray(e) || e.length !== DIMENSION || !e.every(Number.isFinite))
    throw new SpeakerError("incompatible-reference", `reference embedding must be ${DIMENSION} finite numbers`);
  const norm = Math.hypot(...e);
  if (norm === 0) throw new SpeakerError("incompatible-reference", "reference embedding is zero");
  return e.map((v) => v / norm);
}

export class SpeakerVerifier extends WorkerEntrypoint {
  async fetch() {
    return new Response("Not found", { status: 404 });
  }

  /** Audio to a unit embedding in EMBEDDING_SPACE. */
  async embed(input) {
    const { waveform, audioSeconds, speechSeconds } = checkAudio(input);
    const embedding = await serialized(() => embedWaveform(waveform));
    return { embedding, embeddingSpace: EMBEDDING_SPACE, audioSeconds, speechSeconds };
  }

  /**
   * Loads the model and runs one inference on a fixed signal, once per isolate, so a later
   * request on this machine skips the model load. `cold` says whether this call did the work.
   */
  async warm() {
    const cold = !warmed;
    warmed ??= serialized(() => embedWaveform(primer())).catch((error) => { warmed = undefined; throw error; });
    await warmed;
    return { cold };
  }

  /** Cosine similarity between the audio and a reference in the same embedding space. */
  async verify(input) {
    const reference = checkReference(input?.reference);
    const { waveform, audioSeconds, speechSeconds } = checkAudio(input);
    const embedding = await serialized(() => embedWaveform(waveform));
    const cosineSimilarity = embedding.reduce((sum, v, i) => sum + v * reference[i], 0);
    return { cosineSimilarity, embeddingSpace: EMBEDDING_SPACE, audioSeconds, speechSeconds };
  }
}

// No public surface: workers.dev, previews, and routes are all disabled too.
export default {
  async fetch() {
    return new Response("Not found", { status: 404 });
  },
};
