# Synthetic-speaker verification probe

**Real PSTN follow-up:** [REAL_PHONE_RESULTS.md](REAL_PHONE_RESULTS.md) reports
the genuine-only three-call comparison. Real WAVs, embeddings and detailed scores
remain private outside the repository; only aggregate findings are published here.

**Follow-up empirical comparison:** [COMPARISON.md](COMPARISON.md) reports actual
CPU runs of ECAPA, CAM++ PyTorch/ONNX, and released ReDimNet2 B3/B6 on these same
nine WAVs, full-length and first-three-second crops. `comparison.json` contains
all fixed trials, scores, hashes and timings. The released CAM++ ONNX artifact
fails parity with its PyTorch checkpoint here; the discrepancy is retained, not
attributed to the architecture. No model is selected for production from this test.

Offline research proof of concept, not an authentication mechanism. No production
code, caller recordings, credentials, real-person cloning, or authorization rules.
Breeze weights and locally generated outputs are research/noncommercial restricted.

## Design

Three **intended** synthetic identities contrast register, resonance, accent and
rhythm. These descriptions are generation requests, not audited acoustic facts.
Each has one fresh voice-designed anchor and two independent, reference-guided
utterances. Follow-ups use the original anchor WAV plus its intended transcript,
not the design prompt or a reused seed. The three texts differ within each
identity and are shared across identities to control text. All nine seeds differ.
No human has selected these identities by ear or verified the spoken transcripts.

`generation.json` records exact requests, immutable model revision, anchor/file
hashes, durations, token counts, peaks, versions and generation wall times.
It is metadata only. All nine file hashes must differ before scoring.
The original Breeze environment is used read-only, without installing anything.

SpeechBrain ECAPA-TDNN (`speechbrain/spkrec-ecapa-voxceleb`) embeds each complete
mono clip at 16 kHz on CPU, four Torch threads. We L2-normalize the raw 192-D
`encode_batch` embeddings and take cosine similarity, without the classifier's
training-set speaker labels or a verification threshold. These are **similarity
scores, not probabilities**. No threshold is selected or calibrated.

For each clean and telephone condition, all 36 distinct unordered pairs give nine
same-designed-identity pairs and 27 different-designed-identity pairs. These are
correlated observations, not 36 independent trials. Six of nine same-identity
pairs involve the anchor; three compare the two follow-ups. There are no self-pairs
or exact duplicated audio as genuine evidence. A third condition compares each
clean anchor against all telephone follow-ups: six genuine and 12 impostor trials.
`scores.json` contains every pair plus summaries and per-clip inference times.

## Telephone approximation

Apply a sixth-order 300–3400 Hz Butterworth bandpass with offline zero-phase
filtering, polyphase resample to 8 kHz, convert to signed 16-bit PCM, perform actual
8-bit G.711 mu-law encode/decode using Python `audioop`, and polyphase resample
to the encoder's 16 kHz rate. Clean audio is only resampled. No automatic loudness
normalization, denoising or silence trimming is applied.

This does not model a physical phone, microphone, handset frequency response,
network packetization, packet loss/concealment, jitter, AGC, echo, background noise,
transcoding chains, cellular codecs, clipping, or changing devices. Zero-phase
filtering is noncausal and doubles the filter's magnitude response. Upsampling
does not restore removed information. `test` checks output length, finite samples,
out-of-band rejection, nonidentity and silence, not telephony fidelity.

## Rerun

From the repository root:

```sh
# Existing local MLX installation, serialized generation in one process.
/Users/adam/Desktop/voice-doodle/.venv/bin/python experiments/speaker_verification/experiment.py generate

# ECAPA dependencies are isolated by uv, never installed into voice-doodle.
uv run --python 3.11 --script experiments/speaker_verification/experiment.py test
uv run --python 3.11 --script experiments/speaker_verification/experiment.py score
```

The adjacent `experiment.py.lock` pins ECAPA transitive dependencies. Use `--locked`
with `uv run` to require it. Models are pinned by the revisions recorded in each
JSON; pass `--revision COMMIT` to either `generate` or `score` to use that exact
revision. Generation refuses an existing manifest/audio directory, rather than
silently reusing stale audio. For fresh generation, copy the script and lock to a
new directory (without existing JSON/artifacts). Keep the original run intact.
Scoring can be rerun against the existing local audio; it overwrites `scores.json`.
Seeds plus model commits are provenance, not a promise of bit-identical GPU output.
The Breeze environment versions are in `generation.json`; its preexisting pinned
source installation is `/Users/adam/Desktop/voice-doodle/requirements-doodle.txt`.

Audio and model downloads live under ignored `artifacts/`; uv's environment is
outside this project in its own cache. Breeze reuses its existing Hugging Face
cache. ECAPA downloads only its hyperparameters, label map and checkpoint files,
not the training dataset. The script is Python 3.11–3.12 only because `audioop`
was removed in 3.13. SciPy 1.15.3 initially failed to load a macOS arm64 PROPACK
binary on this machine; the experiment pins 1.16.3 instead.

## Interpretation, security and privacy

Any separation here demonstrates this pipeline on nine synthetic clips only.
Reference-guided synthesis may retain generator artifacts and recording style,
not just speaker identity. This tiny, deliberately contrasting set does not
measure real callers, demographic fairness, near-neighbor voices, accent robustness,
illness, aging, emotional speech, unseen channels, or deployment accuracy. Training
domain is VoxCeleb, not this product's calls. Do not interpret a sample gap as a
validated operational threshold or claim false accept/reject rates from this run.

The synthetic “genuine” follow-ups are themselves cloned speech. Separation among
them is **not** spoof resistance, liveness or proof of a human identity. Replay,
voice cloning, synthetic impersonation and a stolen anchor remain explicit threats.
Nothing here justifies an authentication gate, especially one controlling powerful
coding agents. No production wiring or security decisions are included.

Input must be **caller-only audio**. No mixed caller/Voice Agent stream, echo,
overlapping speakers, segmentation, VAD or diarization is handled. Full clips may
contain silence, and their intended transcripts are not independently checked.
No human auditory or ASR quality assurance has been performed.

All synthesis and embedding inference runs locally. Hugging Face receives model
download/metadata requests, not the audio or embeddings. File paths in metadata
are relative. Synthetic WAVs and model caches are ignored; metadata/scores are
intended to be reviewable in Git. If extending to real people, recordings and
embeddings are sensitive biometric data: obtain informed consent, minimize caller
collection, restrict access and retention, and keep them out of Git and hosted
telemetry. Do not treat ignored files as encrypted or securely deleted.

## Measured run

Run on Adam's M3 Max, macOS arm64, Python 3.11.12. All nine WAV hashes differ;
clips last 6.16–9.52 seconds, totaling 73.60 seconds. No take reached the 350-token
cap; generated waveform peaks were 0.181–0.632, without saturation at 1.0.
These checks do not establish intelligibility or perceived identity.

Cosine results, with no threshold selection:

| Enrollment/query condition | Genuine range (mean) | Impostor range (mean) | Min genuine minus max impostor |
| --- | --- | --- | --- |
| Clean/clean | 0.692–0.843 (0.745) | −0.078–0.224 (0.085) | 0.469 |
| Telephone/telephone | 0.720–0.867 (0.784) | 0.168–0.339 (0.233) | 0.381 |
| Clean anchor/telephone follow-up | 0.477–0.583 (0.530) | 0.012–0.245 (0.104) | 0.232 |

This sample separates intended identities, but telephone processing raises both
matched-channel genuine and impostor scores and reduces their sample gap.
Channel mismatch reduces genuine similarity further. Higher genuine cosine by
itself does not mean a better verifier. No real-world accuracy follows from this.

Generation took **100.25 seconds**, including 3.19 seconds for cached model
resolution/loading. Initial ECAPA scoring/download took 9.08 seconds. A pinned,
checkpoint-hash-verified rerun took **1.25 seconds**, including 0.49 seconds of
model resolution/loading; the 18 embeddings totaled 0.65 seconds. These are
in-process wall times, exclude Python import/startup and uv setup, and are not
streaming or call-latency benchmarks. Scores were identical on the rerun.
ECAPA checkpoints total approximately 88.9 MB. Breeze reused cached weights.

Versions: Breeze MLX 0.32.2, mlx-audio 0.5.3 (the existing installation is based
on commit `17001a6950956302f15b53d86b601324efe716ba`), transformers 5.16.1;
ECAPA SpeechBrain 1.0.3, Torch/TorchAudio 2.8.0, NumPy 2.2.6, SciPy 1.16.3,
Hugging Face Hub 0.36.0, SoundFile 0.14.0. Immutable model commits:

- Breeze: `3a06d26b172ea4ae1da2f42d708383e9c79d5526`.
- ECAPA: `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286`.

Validation: telephone unit checks passed, file hashes checked before scoring,
and the three collected ECAPA checkpoint hashes matched the pinned snapshot.
TorchAudio backend/CUDA autocast deprecation warnings are emitted by SpeechBrain;
file decoding here uses SoundFile and inference runs on CPU. No blocker remains
for this bounded experiment; auditory/transcript and real-caller validation remain
explicitly outside the evidence collected.
