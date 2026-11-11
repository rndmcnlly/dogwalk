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
warm(): Promise<{ cold: boolean }>;
```

Mono signed 16-bit little-endian PCM, 3.5–30 s. Failures throw an `Error` whose
message starts with a code: `invalid-input`, `insufficient-audio` (under 3.5 s, or
under 1 s of detected speech), `incompatible-reference` (another embedding space,
or not 192 finite numbers), `busy` (more than four requests queued in one
isolate), or `inference-failed`. There is never a placeholder score.

`warm()` loads the model and runs one inference on a fixed signal, once per
isolate; later calls on a warm isolate return in about a millisecond. `cold` says
whether this call did the work. `dogwalk` calls it in the background (`warmSpeaker`)
when a User with a Voice Reference calls and when Challenge Practice starts (see
Latency), and its health endpoint calls it first so a probe can tell a model
load from queueing.

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
node speaker_worker/worker_test.mjs                 # warm() state against a stubbed ORT; no model needed
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

`dogwalk` serves operator-only `GET /admin/api/voice-verify/health` (calls
`warm()`, then embeds a synthetic signal; reports `cold`, `warm_ms`, and total `ms`) and `POST
/admin/api/voice-verify?user=<id>` with a mono 16-bit 8 or 16 kHz WAV body,
returning `{cosine_similarity, embedding_space, audio_seconds, speech_seconds, ms}`.
Without the binding (local dev, tests) both answer 503.

## Latency

Measured from production on 2026-11-11 with `latency_probe.py` (health probes
through `dogwalk`, timing the SPEAKER RPC alone). These runs predate health's
`cold` flag, so they count requests over 2 s as slow: a model load, or, with
concurrency, a wait behind other requests in one serialized isolate.

- Sequential requests over 2 s: 2.1–8.8 s (median 3.5 s); the rest 0.6–1.4 s
  (median about 0.65 s). Analytics put a cold request at about 3.6 s of CPU (P99
  up to 4.3 s), a warm one at 0.7 s, so sequential slowness is model loads.
- Warmth belongs to a machine. 13 of 30 back-to-back probes on fresh connections
  were slow (alternating SJC and LAX); 0 of 30 on one kept-alive connection.
  Scoring 8 synthetic clips one at a time after a quiet spell: 6 of 8 slow. Four
  concurrent probes: 6 of 16 slow, but four requests queued on one warm isolate
  already take about 2.6 s, so these are not all loads; rerun with the flag.
- On one pinned connection an instance stayed warm after 60 s and 120 s idle and
  was slow after 300 s and 600 s (one sample each, same socket). Pinning is
  imperfect: one probe 30 s after a warm-up was also slow.
- Locally (`load_bench.mjs`, Node, a fresh process per level) ORT session
  creation takes 198–286 ms across graph optimization levels and the first
  inference about 230 ms, a small part of production's roughly 2.9 s cold
  penalty. Most of a cold request is isolate and WASM instantiation, which no
  ORT setting changes.

So warm close to the scoring in both place and time: `dogwalk` calls `warm()`
when a User with a Voice Reference calls, seconds before an Identity Challenge,
from the invocation handling the call. A keep-warm cron would warm one arbitrary
machine, and Smart Placement has nothing to act on (no subrequests, RPC only).

## Limits

128 MB per isolate, shared by JS and WASM; the experiment observed 112 MiB of WASM
linear memory after warm inferences, so headroom is thin. Inference is serialized
per isolate. Peak isolate memory has never been measured in production: Workers
analytics has no memory metric and observability is off here, but 172 invocations
on 2026-11-11, including four concurrent, ended with no resource-limit status.
