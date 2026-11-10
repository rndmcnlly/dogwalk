# B3 Worker experiment: numerical success, account caveat

2026-11-10, CPU-only, float32, no quantization. All files and generated work stay
inside `experiments/speaker_worker/`. Existing speaker experiments were read only.
No commits, pushes, production configuration, routes, D1 or storage mutations.

## Implemented

- Exact cached official B3 LM checkpoint, strict loading and SHA256 verification.
- Static feature graph `[1,72,299] → [1,192]`, opset 17.
- Full native checkpoint waveform frontend plus embedding graph:
  `[1,48000] → embedding [1,192]`, diagnostic features `[1,72,299]`.
- Local native ONNX parity against the original PyTorch waveform model.
- Patched ORT 1.24.1 WASM running in deployed Cloudflare Workers, not Node emulation.
- Bounded authenticated binary POST, finite/length validation, one inference per
  isolate at a time, disabled observability, no request/embedding logging.
- No externally hosted inference, GPU, container purchase, model quantization,
  real caller audio, templates or private phone metadata.

This is decoded/resampled **16 kHz mono float32 waveform inference**, not an
encoded WAV endpoint, Twilio integration, VAD, segmentation, calibration,
authentication threshold or replay/cloning defense.

## Numerical evidence

18 inputs: nine existing synthetic Breeze clips, each clean and telephone-like,
cropped after original shared preprocessing to three seconds. All 153 unordered
pairs are checked, including cross-channel pairs, rather than only a selected
verification-trial subset. Metrics compare unrounded float32 outputs using
float64 analysis and normalization.

Declared embedding tolerances: raw max abs < 1e-3, normalized max abs < 1e-5,
embedding cosine > 0.999999, pairwise cosine-score difference < 1e-5.

Feature graph, local ONNX versus PyTorch:

- Raw max abs: **6.377696990966797e-6**.
- Normalized max abs: **2.702022769418555e-7**.
- Minimum embedding cosine: **0.9999999999994815**.
- Maximum verification-score difference: **2.921268905109464e-7**.
- PyTorch feature split versus full original waveform model: **bitwise equal**.

Feature graph, deployed WASM versus PyTorch:

- Raw max abs: **6.377696990966797e-6**.
- Normalized max abs: **2.6780030397410837e-7**.
- Minimum embedding cosine: **0.9999999999992402**.
- Maximum verification-score difference: **5.223985908159312e-7**.

Full waveform graph, local ONNX versus PyTorch:

- Raw max abs: **2.6702880859375e-5**.
- Normalized max abs: **1.1480002267364592e-6**.
- Maximum verification-score difference: **1.3655427254510855e-6**.
- Frontend feature max abs: **0.0031709671020507812**.

Full waveform graph, deployed WASM versus PyTorch:

- Raw max abs: **3.337860107421875e-5**.
- Normalized max abs: **1.4074666217708809e-6**.
- Minimum embedding cosine: **0.999999999978058**.
- Maximum verification-score difference: **1.3196589215974663e-6**.
- Frontend feature max abs: **0.0051727294921875**.

All embedding/score tolerances passed. The frontend graph preserves original
operators and constants, but different float32 convolution/reduction kernels
produce non-bitwise feature differences, most apparent after log near small
spectral values. Do not describe the full graph as exactly numerically equal.
No independent feature-error pass threshold was declared. This experiment
demonstrates embedding and verification-score equivalence on these bounded inputs,
not a global numerical guarantee or actual speaker-verification accuracy.

## Deployed measurements and limits

The completed numerical remote runs were inadvertently made in **BayLeaf**,
because the default Wrangler OAuth login belongs there. This account mismatch was
detected during the experiment, and that experimental Worker was **deleted**.
Only synthetic inputs went there. No existing BayLeaf resource was modified.
`remote_results.json` and `remote_waveform_results.json` retain that historical
workers.dev URL deliberately as provenance, not as a currently available service.

Feature version: `4d4d56be-594e-4cd8-9e99-faa440051b09` before secret setup;
full waveform version: `17b40e42-742d-4625-97d9-67d4ae87a701`.
Full upload: **29,770.70 KiB uncompressed**, gzip 19,034.26 KiB, startup 3 ms.
All 18 requests succeeded for each variant. Auth rejection was 401, wrong length
and excessive length 400, wrong content type 415. No platform resource error was
observed in these completed remote runs.

Full waveform WASM linear memory grew from **53.875 MiB** after session creation,
to **93.1875 MiB** after first inference, then **111.875 MiB** on a second inference,
and stayed at that observed level on later warm calls. These are monotonically
growing WASM buffer observations, **not total peak isolate memory**. JavaScript heap,
model module storage, runtime overhead and transient allocations remain unmeasured.
128 MB is shared JS/WASM **per isolate**. This leaves concerning headroom, not a
production capacity guarantee. Do not infer the cause of isolate churn from it.
The initial feature-only memory probe incorrectly searched exports and reported
zero, because ORT imports its memory. Those zeros are unavailable measurements;
the corrected full-waveform probe captures imported `a.a` memory.

Full waveform client round trips, small descriptive sample:

- First model initialization in an isolate (`cold=true`, 10): min **2.292 s**,
  median **2.792 s**, max **6.946 s**.
- Reused initialized model (`cold=false`, 8): min **0.782 s**, median **0.884 s**,
  max **1.245 s**.

Feature-only round trips: cold median **2.793 s** (10), warm median **0.819 s** (8).
The client creates individual HTTP requests; isolate assignment, TLS, geography,
network and serialization are included. Cold means first model session in an
isolate, not an independently forced empty edge process. Worker `Date.now()`
stayed frozen during compute and returned zero durations, so **model init time,
kernel execution time and billed CPU were not measured**. No tail logs containing
authorization headers or bodies were collected merely to obtain timings.

Cloudflare's current documentation, read on 2026-11-10, says 64 MiB
**uncompressed** bundle size, no compressed-size limit; this deployment confirms
the older discussions' 10 MB compressed limit is obsolete. It still says 128 MB
isolate RAM, free CPU 10 ms, paid CPU default 30 s and configurable max 300 s.

## Corrected personal deployment

`wrangler.toml` now explicitly pins personal account
`890753ccd6f0bbbfbe2c030cc44f6a87`, and `personal_wrangler.py` refreshes the named
personal OAuth credential privately. It refuses unrelated commands. The default
Wrangler login is no longer trusted for this project.

The personal account is **Workers Free**. Its first upload rejected the paid-plan
CPU limit setting with API error **100328**, `CPU limits are not supported for the
Free plan`. No subscription was changed. Removing that setting allowed upload:
version **1a8393cc-f6a0-4547-a08e-c1cf1093b1ad** before secret installation,
startup **2 ms**, same 29,770.70 KiB full-waveform bundle.

Current isolated service:
`https://dogwalk-redimnet-b3-experiment.adam-890.workers.dev`.
Bearer secret installed securely from a mode-0600 file outside the repository.
API resource inspection confirmed no domains, service bindings, Durable Objects,
queues or tail consumers, previews disabled, observability/logs/traces disabled.

The immediate personal-account HTTPS probe failed during TLS handshake, before
any inference or authentication test. See `personal_waveform_results.json` for
the final probe outcome. This is not evidence of a CPU-limit failure. The 10 ms
free-plan budget is a separate likely obstacle, not a measured runtime outcome.
Do not upgrade the plan or alter production domains to make the experiment pass
without user approval.

## Files and cleanup

`export.py` and lock: local float32 exports, synthetic fixtures, local parity.
`worker.js`, `build.mjs`, npm manifests and `wrangler.toml`: isolated runtime.
`check_remote.py` and lock: authenticated synthetic remote checks.
`personal_wrangler.py`: explicit account/credential guardrail.
Four numerical JSON reports plus personal deployment probe report: raw aggregate
evidence. `README.md` and `THIRD_PARTY_NOTICES.md`: reproduction and model license.

From this directory:

```sh
python3 personal_wrangler.py delete --name dogwalk-redimnet-b3-experiment --force
```

Then explicitly remove the outside-repository private token file specified in the
agent handoff. No production resources depend on this Worker. The unintended
BayLeaf experimental copy was already deleted.
