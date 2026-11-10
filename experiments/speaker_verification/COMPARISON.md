# Local ECAPA, CAM++ and ReDimNet2 comparison

2026-11-10, M3 Max, macOS arm64, CPU only. Research plumbing/sanity experiment,
not authentication or a measure of real-caller accuracy. All nine existing Breeze
WAVs were reused without regeneration. No production changes or deployment.

## Fixed protocol

`compare.py` reuses `experiment.py`'s resampling and telephone approximation.
Every model receives identical 16 kHz float32 waveform arrays. Each gets its
native feature frontend, not a shared frontend that would violate its recipe.
All embeddings are L2 normalized and scored with raw cosine, not WeSpeaker's
optional `(cosine+1)/2` presentation or classifier labels. No score normalization,
training, calibration, thresholds or threshold-derived error rates.

Each duration has 36 clean/clean pairs (9 genuine, 27 impostor), 36
telephone/telephone pairs (9/27), and 18 clean-anchor/telephone-follow-up pairs
(6/12). Every model has those same 90 trials, recorded once and fingerprinted in
`comparison.json`. There are 180 scores/model across two durations:

- **Full:** existing 6.16–9.52 s clips.
- **First3:** first 48,000 samples of each processed waveform. BOTH enrollment and
  query are cropped, including clean-anchor/telephone-query trials. This is not a
  full-enrollment/short-query protocol. Crop after full telephone processing avoids
  adding a different filter-edge treatment to the short condition.

No VAD, silence trimming, ASR/auditory check, loudness normalization, diarization
or echo handling. Three seconds of waveform is not necessarily three seconds of
voiced speech. Crops are correlated with their full clips, trials share utterances,
and all speech comes from one reference-guided generator. These are not independent
call trials. Text is shared across designed speakers. Nothing tests actual Adam,
similar human impostors, day/device variability, replay, or targeted cloning.

## Measured cosine ranges

G/I are genuine/impostor ranges. Gap is min(G) minus max(I) within THAT model and
condition. Cosine scales and gap magnitudes are not calibrated across models;
do not read this as a unified accuracy leaderboard. All means and every pair are
in `comparison.json`.

| Duration | Model | Channel enrollment/query | G range | I range | Sample gap |
| --- | --- | --- | --- | --- | --- |
| Full | ECAPA | clean/clean | .692–.843 | −.078–.224 | .469 |
| Full | ECAPA | telephone/telephone | .720–.867 | .168–.339 | .381 |
| Full | ECAPA | clean/telephone | .477–.583 | .012–.245 | .232 |
| Full | CAM++ PyTorch | clean/clean | .726–.879 | −.020–.246 | .480 |
| Full | CAM++ PyTorch | telephone/telephone | .698–.860 | −.020–.224 | .474 |
| Full | CAM++ PyTorch | clean/telephone | .522–.711 | −.086–.211 | .311 |
| Full | ReDimNet2 B3 LM | clean/clean | .709–.871 | −.016–.278 | .431 |
| Full | ReDimNet2 B3 LM | telephone/telephone | .733–.892 | .080–.328 | .406 |
| Full | ReDimNet2 B3 LM | clean/telephone | .587–.699 | −.075–.227 | .359 |
| Full | ReDimNet2 B6 LM | clean/clean | .723–.860 | −.029–.284 | .440 |
| Full | ReDimNet2 B6 LM | telephone/telephone | .726–.879 | .062–.295 | .430 |
| Full | ReDimNet2 B6 LM | clean/telephone | .607–.709 | −.040–.258 | .349 |
| First3 | ECAPA | clean/clean | .336–.629 | −.182–.208 | .128 |
| First3 | ECAPA | telephone/telephone | .464–.683 | .086–.308 | .156 |
| First3 | ECAPA | clean/telephone | .322–.415 | −.021–.228 | .094 |
| First3 | CAM++ PyTorch | clean/clean | .564–.735 | −.113–.269 | .295 |
| First3 | CAM++ PyTorch | telephone/telephone | .467–.712 | −.028–.210 | .258 |
| First3 | CAM++ PyTorch | clean/telephone | .385–.594 | −.094–.189 | .196 |
| First3 | ReDimNet2 B3 LM | clean/clean | .478–.758 | −.124–.278 | .200 |
| First3 | ReDimNet2 B3 LM | telephone/telephone | .514–.800 | .004–.346 | .168 |
| First3 | ReDimNet2 B3 LM | clean/telephone | .433–.583 | −.009–.259 | .174 |
| First3 | ReDimNet2 B6 LM | clean/clean | .435–.709 | −.136–.309 | .126 |
| First3 | ReDimNet2 B6 LM | telephone/telephone | .459–.770 | .038–.295 | .164 |
| First3 | ReDimNet2 B6 LM | clean/telephone | .406–.569 | .039–.229 | .177 |

All four PyTorch adapters separate these designed identities in every condition.
Short crops reduce their gaps. B6 does not dominate B3 in these observations.
Architecture age is not the sole criterion: data, training, channel, frontend,
runtime correctness and operational cost matter. This experiment cannot establish
that any model is better on real PSTN calls or validate an operational threshold.

## Actual CAM++ ONNX discrepancy, not hidden as a model failure

The released HF ONNX file runs, but does NOT reproduce its adjacent PyTorch
checkpoint with this adapter. The ONNX frontend follows the official pinned
`wespeaker/bin/infer_onnx.py`: PCM scale 32768, Kaldi 80-bin fbank, 25/10 ms,
Hamming window, no dither, utterance mean subtraction. The PyTorch audit receives
identical features and loads every embedding-network parameter strictly, removing
only the training-only `projection.weight` classifier head.

| Duration | Channel | ONNX G range | ONNX I range | ONNX gap |
| --- | --- | --- | --- | --- |
| Full | clean/clean | .264–.894 | −.107–.625 | −.361 |
| Full | telephone/telephone | .160–.860 | −.079–.731 | −.570 |
| Full | clean/telephone | .170–.634 | −.131–.453 | −.283 |
| First3 | clean/clean | .800–.904 | .713–.904 | −.104 |
| First3 | telephone/telephone | .903–.967 | .734–.968 | −.065 |
| First3 | clean/telephone | .785–.908 | .694–.912 | −.127 |

Additional fixed random feature probes (298 and 700 frames, seed 1701) isolate
model execution from waveform decoding. ONNX-vs-PyTorch embedding cosines were
**.0636 and .0570**, max absolute differences **12.506 and 8.110**. This is not
roundoff. Exact artifact hashes and probe results are recorded. Root cause is not
established: export, artifact identity, toolkit-version or runtime incompatibility
remain possibilities. Do NOT attribute this to CAM++ architecture quality or ship
this ONNX artifact without resolving parity. No re-export was attempted.

## CPU embedding times

Primary `comparison.json` run, milliseconds per utterance, clean and telephone
pooled. Four Torch intra-op threads, one inter-op thread; ONNX four/one likewise.
One unmeasured three-second warm-up per adapter, then three serial repeats per
input, 18 inputs/duration, 54 timings/cell. These small-sample p95s are descriptive.

| Adapter | Full p50 / p95 ms | First3 p50 / p95 ms | Cached resolution/load s |
| --- | --- | --- | --- |
| ECAPA PyTorch | 35.5 / 40.0 | 18.6 / 20.3 | .573 |
| CAM++ ONNX (parity unresolved) | 35.7 / 43.3 | 16.2 / 16.8 | .140 |
| CAM++ PyTorch | 34.9 / 38.8 | 17.9 / 22.9 | .389 |
| ReDimNet2 B3 PyTorch | 132.8 / 151.4 | 74.3 / 82.0 | .085 |
| ReDimNet2 B6 PyTorch | 306.4 / 362.8 | 152.5 / 167.1 | .128 |

Timed extraction includes native features, neural inference, finite/norm checks
and embedding normalization. Excludes file decode, channel simulation/resampling,
Python imports/startup, uv environment setup and model load. Preprocessing is
timed separately in JSON. Load includes resolution, checkpoint hashing and any
download, so this cached run is NOT a cold-download benchmark. CAM++ PyTorch load
also constructs ONNX and performs two parity probes, not a minimal deployment
loader. Warm-ups were 16–148 ms. No memory/RSS, concurrency, streaming,
energy or remote-sandbox latency benchmark was attempted. CPU frequency, thermal
state, background work, fixed adapter order, differing feature implementations
and runtime backends confound architecture-only speed comparisons. No MPS/GPU.
Three repeats verify embedding stability within tolerance, not independent trials.

ReDimNet2 B3/B6 have 4,181,576 / 12,394,784 parameters in these loaded models.
New model-weight downloads totaled about **164 MB**: CAM++ ONNX 29.3 MB, PyTorch
66.2 MB (includes training projection), B3 17.9 MB, B6 50.9 MB, plus small source
archives/files. ECAPA reused 88.9 MB already cached. No training datasets downloaded.
Weights size is neither peak RAM nor network call cost. Speech accumulation still
costs at least the three seconds of waveform, regardless of millisecond inference.

## Pinned artifacts and integration notes

- ECAPA: SpeechBrain `speechbrain/spkrec-ecapa-voxceleb`, revision
  `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286`. Card says Apache-2.0.
  Native `encode_batch`, raw 192-D embedding, CPU. Existing 88.9 MB checkpoints
  reused. Full scores reproduce original `scores.json` within 1e-7.
- CAM++: `Wespeaker/wespeaker-voxceleb-campplus`, non-LM VoxCeleb artifact,
  revision `acf623ad8ca746e50baa432255cf8fc57c669c45`, 512-D embedding. Both ONNX
  and `avg_model.pt` fetched for the parity audit, not substituted Mandarin weights.
  HF card says Apache-2.0, but [publisher pretrained policy](https://github.com/wenet-e2e/wespeaker/blob/9fecd6cb4f47475d01761d87c826298dff4ef18c/docs/pretrained.md)
  separately says weights follow dataset licensing and cites VoxCeleb CC BY 4.0.
  Preserve that distinction, not blanket commercial clearance.
  WeSpeaker source revision `9fecd6cb4f47475d01761d87c826298dff4ef18c`, Apache-2.0.
  Only model modules downloaded. Its package initializer otherwise imports CLI/VAD/
  diarization, an unnecessary dependency cascade for embedding inference; the adapter
  explicitly namespaces model modules without executing that initializer. Current
  pooling module also imports multi-view attention, downloaded but unused by CAM++.
  Checkpoint includes a training projection absent from the embedding module; only
  that named key is removed before strict loading. No full toolkit installation.
- ReDimNet2: official PalabraAI source revision
  `c5bbe0b76e37df698c403f8844e41304ceab6307`, [MIT LICENSE](https://github.com/PalabraAI/redimnet2/blob/c5bbe0b76e37df698c403f8844e41304ceab6307/LICENSE).
  Released `v1.0.0/b3-vox2-lm.pt` and `b6-vox2-lm.pt`, approximately 17.9/50.9 MB.
  GitHub release SHA256 digests hardcoded and checked before deserialization;
  `torch.load(weights_only=True)` works. Pinned source archive, official
  `ReDimNet2Wrap`, checkpoint-defined native frontend, strict state loading.
  No moving Torch Hub code or setup script executed. B6 added because it uses the
  same working adapter and only one additional 51 MB checkpoint. Both trained on
  Vox2, no extra-data B6 substitution. Repository MIT is not a separate audit of
  training-data rights or a blanket biometric-deployment clearance.

Pinned upstream source is necessarily executed locally, not sandboxed as untrusted
code. Source archive/license hashes, checkpoints, package versions, exact model
configs, audio/processed-input/trial hashes and per-extraction timings are in JSON.
No publisher real-speech benchmark reproduction was performed, so this remains a
local adapter/corpus experiment, not independent confirmation of model-card EERs.

## Reproduce and extend

```sh
uv run --locked --python 3.11 --script experiments/speaker_verification/experiment.py test
uv run --locked --python 3.11 --script experiments/speaker_verification/compare.py
uv run --locked --python 3.11 --script experiments/speaker_verification/compare.py --output experiments/speaker_verification/comparison_rerun.json
python3 experiments/speaker_verification/validate_comparison.py --rerun experiments/speaker_verification/comparison_rerun.json
```

`compare.py.lock` isolates pinned dependencies in uv's cache. Never installs in
`voice-doodle`. Downloads/caches stay in ignored `artifacts/`; only model downloads
and metadata requests leave the laptop, never audio or embeddings. ECAPA emits
upstream TorchAudio/CUDA-API deprecation warnings despite CPU inference.

Validation completed: telephone unit checks passed, all five adapters used identical
trial/input sets, original full ECAPA results matched within 1e-7, and an independent
complete rerun produced **maximum cosine difference 0.0** over all 900 scores.
`comparison_validation.json` records report fingerprints and checks. Timing varied
between runs, as expected; the table above uses the primary run only.

`--models ecapa cam-pt redim-b3` is the economical later rerun. `--manifest PATH`
accepts another manifest with `clips` entries `{speaker, take, path, sha256}` and
audio paths relative to that manifest; `take: 0` denotes enrollment, other takes
queries. Inputs must be finite mono WAVs at least three seconds long. Preserve
original files/hashes and channel provenance for real calls. This tiny adapter is
amenable to new WAVs, not a VAD/segmentation or calibration framework. Validation
currently intentionally checks this nine-clip synthetic experiment.

**Provisional choice:** retain ECAPA baseline, carry CAM++ PyTorch and ReDimNet2 B3
into the consented real-PSTN collection. CAM++ is attractive at ECAPA-like CPU cost;
B3 provides a contemporary independently implemented challenger. No synthetic winner
is promoted to production, and B6's greater cost is not justified by these samples
alone. Decide on held-out real callers/calls with per-model calibration and an
explicit threat model, including replay/cloning and ordinary-telephone recovery.
