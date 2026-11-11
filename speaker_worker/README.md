# dogwalk-speaker

The speaker verification Worker: ReDimNet2 B3 (VoxCeleb2, large margin) as a
16 kHz waveform-to-embedding ONNX graph in ONNX Runtime WASM, scoring audio
against Voice References. Stateless. It has no public surface (no routes,
workers.dev, or preview URLs; `fetch` answers 404) and is reached only through
the `dogwalk` Worker's `SPEAKER` Service Binding to the `SpeakerVerifier`
entrypoint. It never logs audio, embeddings, or scores, and observability is off.
The calling Worker owns Voice References, thresholds, and every decision.

Proven out on the `speaker-verification` branch (`experiments/speaker_worker/`),
which records the numerical parity evidence and memory observations.

## RPC

```ts
type Audio = { audio: ArrayBuffer; format: "pcm_s16le"; sampleRate: 8000 | 16000 };
type Reference = { embedding: number[]; embeddingSpace: string };
embed(input: Audio): Promise<Reference & { audioSeconds: number; speechSeconds: number }>;
verify(input: Audio & { reference: Reference }): Promise<{
  cosineSimilarity: number; embeddingSpace: string; audioSeconds: number; speechSeconds: number;
}>;
```

Mono signed 16-bit little-endian PCM, 3.5–30 s. Failures throw an `Error` whose
message starts with a code: `invalid-input`, `insufficient-audio` (under 3.5 s, or
under 1 s of detected speech), `incompatible-reference` (another embedding space,
or not 192 finite numbers), `busy` (more than four requests queued in one
isolate), or `inference-failed`. There is never a placeholder score.

## Embedding space

```text
redimnet2-b3-vox2-lm@02d14efdc1845738/onnx-waveform@c2653e3df4e2f5e3/pcm16k-mono-f32-48000/tfmelbanks-native/l2-cosine
```

Checkpoint hash, exported graph hash, model input, the checkpoint's native
TFMelBanks frontend, and L2-normalized cosine scoring. `preprocess.js` reproduces
the Voice Reference builder exactly: samples scaled by 1/32768, the 0.5 s skip,
20 ms frame energies against an adaptive threshold
(max(-55 dB, p20 + 12 dB, p95 - 30 dB)) with active runs under 100 ms dropped,
the 3 s span holding the most active frames, then 8 to 16 kHz with scipy's
`resample_poly(x, 2, 1)` filter (41-tap Kaiser-windowed sinc, beta 5, float32
taps, zero-padded window edges). Changing any of this, or re-exporting to a
different hash, is a new embedding space: every Voice Reference must be rebuilt.

## Build and deploy

Weights, the upstream source, the ONNX export, and the bundle are gitignored.

```sh
uv run --script speaker_worker/export.py            # from the repository root
uv run --script speaker_worker/check_preprocess.py  # preprocess.js against scipy; optional WAV paths
cd speaker_worker && npm ci && npm run build
```

`export.py` downloads the pinned source revision and release checkpoint, verifies
both, exports, checks ONNX against PyTorch, and fails unless the export hashes to
the pinned graph (it reproduces bitwise with the pinned packages). `build.mjs`
refuses a model whose hash `EMBEDDING_SPACE` does not name.

Deploy only to Adam's personal account, which is on Workers Paid (`cpu_ms` needs
it); a BayLeaf Wrangler login also exists on this machine and must not be used:

```sh
export CLOUDFLARE_ACCOUNT_ID=890753ccd6f0bbbfbe2c030cc44f6a87
cf workers list --profile personal >/dev/null   # refreshes the OAuth bearer
export CLOUDFLARE_API_TOKEN=$(python3 -c 'import json,pathlib;print(json.loads((pathlib.Path.home()/"Library/Preferences/cloudflare/config/personal.json").read_text())["oauth_token"])')
npx wrangler deploy
```

Deploy this Worker before a `dogwalk` version that binds it. The upload is about
29 MiB uncompressed (19 MiB gzip) against the 64 MiB limit.

## Calibration

`dogwalk` serves operator-only `GET /admin/api/voice-verify/health` (embeds a
synthetic signal: warms the model, reports `ms`) and `POST
/admin/api/voice-verify?user=<id>` with a mono 16-bit 8 or 16 kHz WAV body,
returning `{cosine_similarity, embedding_space, audio_seconds, speech_seconds, ms}`.
Without the binding (local dev, tests) both answer 503.

## Limits

128 MB per isolate, shared by JS and WASM; the experiment observed 112 MiB of WASM
linear memory after warm inferences, so headroom is thin. Inference is serialized
per isolate. A model load in a fresh isolate takes about 3–4 s; a warm 3 s window
about 0.6–0.8 s end to end through the binding.
