// A stand-in for the dogwalk-speaker Worker (../speaker_worker/) in local tests: smoke_test.py
// runs it beside the Worker with `wrangler dev -c wrangler.toml -c speaker_mock.toml`, so the
// SPEAKER Service Binding resolves to it. Its answers are a function of the audio, so a test
// chooses an outcome by what it streams: the first sample over 10,000 is the cosine, a negative
// first sample fails inference, under half a second is insufficient audio, and the audio's
// length is its speech seconds. Like the real verifier, it refuses more than 30 seconds.
import { WorkerEntrypoint } from "cloudflare:workers";

const SPACE = "mock-speaker-space";

export class SpeakerVerifier extends WorkerEntrypoint {
  async embed(input) {
    const seconds = new Int16Array(input.audio).length / input.sampleRate;
    return { embedding: new Array(192).fill(1 / Math.sqrt(192)), embeddingSpace: SPACE, audioSeconds: seconds, speechSeconds: seconds };
  }

  async verify(input) {
    const samples = new Int16Array(input.audio);
    const seconds = samples.length / input.sampleRate;
    if (seconds > 30) throw new Error("invalid-input: audio exceeds 30 s");
    if (seconds < 0.5) throw new Error("insufficient-audio: too little speech");
    if (samples[0] < 0) throw new Error("inference-failed: mock failure");
    return { cosineSimilarity: samples[0] / 10000, embeddingSpace: input.reference.embeddingSpace, audioSeconds: seconds, speechSeconds: seconds };
  }
}

export default { fetch: () => new Response("speaker mock\n") };
