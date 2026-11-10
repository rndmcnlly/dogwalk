# ReDimNet2 B3 float32 Worker proof of concept

Read `RESULTS.md` first: full deployed numerical parity passed on the initial
BayLeaf deployment, which was then deleted after correcting the account target.
The corrected personal deployment has separate plan/TLS limitations documented
there. Numerical success must not be confused with personal-account availability.

Isolated experimental Worker, not production authentication. Uses only the existing
nine synthetic Breeze utterances, both clean and telephone approximation, each
cropped to 48,000 samples after shared preprocessing. No real-caller material is
read or transmitted. Model export, fixture arrays and runtime bundles are ignored.

## Reproduce

From the repository root:

```sh
uv run --locked --python 3.11 --script experiments/speaker_worker/export.py
```

Export requires the already cached pinned B3 source and checkpoint under
`../speaker_verification/artifacts/`. It reads them, but never modifies that
experiment. It verifies the checkpoint SHA256 and loads state strictly on CPU.
The export is a static `[1,72,299]` float32 feature tensor to raw 192-D embedding,
opset 17. The default feature export is **not waveform inference**. The original PyTorch frontend is kept
locally; the feature-boundary split is checked bitwise against the complete
PyTorch model. L2 normalization and raw cosine scoring follow the prior experiment.

```sh
cd experiments/speaker_worker
npm ci
npm run build
python3 personal_wrangler.py deploy --no-bundle
```

Always inspect `wrangler.toml`: only the separately named experimental Worker,
workers.dev address, no routes, D1, KV, R2 or production service bindings. Secret
absence rejects all inference requests, including immediately after first deploy.
The account is pinned to Adam's personal account. `personal_wrangler.py` refreshes
the named `cf` personal OAuth credential before privately handing it to Wrangler;
the default Wrangler login belongs to BayLeaf and must not be used here.
Generate a secure token in a mode-0600 file **outside the repository**, then:

```sh
python3 personal_wrangler.py secret put EXPERIMENT_TOKEN < /absolute/private/token-file
```

Do not print or commit the token. The current run's private token file is recorded
in the agent handoff, not in this repository. From the repository root:

```sh
uv run --locked --python 3.11 --script experiments/speaker_worker/check_remote.py \
  https://dogwalk-redimnet-b3-experiment.adam-890.workers.dev \
  --token-file /absolute/private/token-file
```

The client only sends generated synthetic feature fixtures. `/embed` requires
Bearer authentication, `application/octet-stream`, exactly 86,112 little-endian
float32 bytes, all finite. It rejects concurrent inference in one isolate with 429.
`/health` exposes only `{ok:true}`. There is no public model/fixture serving route,
console logging, request-body logging or enabled Worker observability.

## Correctness contract

Across all 18 inputs: raw embedding max absolute error < 1e-3, L2-normalized max
absolute error < 1e-5, embedding cosine > 0.999999. Across all 153 unordered pairs:
absolute cosine-score change < 1e-5. These tolerances are declared in advance of
remote results and are not a claim of bitwise equality or authentication accuracy.
Metrics use float64 analysis of float32 raw outputs, not rounded printed scores.

`local_results.json` records exact provenance, configuration, export hash, local
CPU parity and per-input timings. `remote_results.json` records remote success or
failure, auth/body validation, per-input equivalence and measured timing/resource
observations. Fixture embeddings are not printed or stored in the public report.

## Platform and runtime evidence

Cloudflare's [limits documentation](https://developers.cloudflare.com/workers/platform/limits/),
read 2026-11-10, last updated 2026-10-29: 128 MB **per isolate** shared between JS
and WASM, paid default 30 s CPU with configurable max 300 s, 64 MiB uncompressed
bundle, no compressed-size limit, 1 s top-level startup. Do not reuse the older
discussion's 10 MB compressed-bundle budget.

The pinned ORT 1.24.1 bundle receives one checked `instantiateWasm` config patch
to use a statically imported `CompiledWasm` module, plus removal of unsupported
variable dynamic import. `wasmPaths` bypasses runtime source URL resolution.
This follows the linked public discussions in `THIRD_PARTY_NOTICES.md`, but is
tested independently rather than trusting their performance/resource claims.
One WASM thread; no proxy, GPU, quantization, container or external inference.

Reported WASM memory is exported linear-memory buffer size at observation points,
**not total isolate peak RAM**. Actual deployed resource failure or success is the
platform check. JavaScript heap, module storage, transient allocations and
concurrency are not captured by that number. Client round-trip and Worker
`Date.now()` timing are wall time, not billed CPU. In actual deployed probes,
Cloudflare froze `Date.now()` during compute, producing zero elapsed values:
those values are **unavailable timing measurements, not zero-cost inference**.
A `cold` flag means first model
session initialization in that isolate, not a guaranteed fresh geographic process.

## Native waveform variant

After the feature-baseline deployment is measured, the **same isolated service**
can be replaced by a full waveform variant (only one model lives in each version):

```sh
# From repository root:
uv run --locked --python 3.11 --script experiments/speaker_worker/export.py --waveform
# From experiments/speaker_worker:
npm run deploy:waveform
# From repository root:
uv run --locked --python 3.11 --script experiments/speaker_worker/check_remote.py \
  https://dogwalk-redimnet-b3-experiment.adam-890.workers.dev \
  --token-file /absolute/private/token-file --waveform \
  --output experiments/speaker_worker/personal_waveform_results.json
```

Its authenticated endpoint is `/waveform`, exactly 192,000 little-endian float32
bytes: one mono 16 kHz 48,000-sample waveform. The graph includes the checkpoint's
unmodified `TFMelBanks` frontend: waveform mean/population-std normalization,
reflected pre-emphasis, pinned convolutional spectral kernels and mel weights,
clipping, epsilon, log, time-mean subtraction. It does **not** substitute a generic
mel frontend. It returns features too, solely to diagnose frontend parity in this
experiment. The client only publishes aggregate errors, never returned features or
embeddings. `local_waveform_results.json` compares the full exported path locally;
the historical `remote_waveform_results.json` compares deployed full inference to PyTorch and
local native ONNX, and deployed features directly to PyTorch's original frontend.

This accepts already decoded and resampled float32 audio, **not a PSTN media
stream or encoded WAV**. Decoding, 8→16 kHz resampling, segmentation, VAD, enough
voiced evidence, authentication policy and liveness remain outside the Worker.

## Cleanup

From `experiments/speaker_worker/`:

```sh
python3 personal_wrangler.py delete --name dogwalk-redimnet-b3-experiment --force
```

Then remove the private token file explicitly. No production resources depend on
this Worker. No commits or pushes are part of the experiment.
