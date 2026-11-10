# Real telephone probe: genuine-only, three audio modes

## Outcome and limits

Ran the existing pinned **ECAPA, CAM++ PyTorch and official ReDimNet2 B3 LM**
adapters locally on CPU against 18 unique, complete real PSTN clips: six clips
from each of three calls. Adam manually confirmed his identity for all three.
Collector metadata itself still labels the speaker unconfirmed; the experiment
records the user's confirmation separately and does not modify collector records.

**No impostor data exists in this experiment.** There is no false-accept estimate,
security ranking, threshold selection, calibration, authentication decision or
spoof/liveness test. Higher genuine cosine does not demonstrate better identity
discrimination, and model-specific cosine scales must not be ranked against one
another. These are consistency measurements, not accuracy measurements.

Calls were assigned modes by **recorded creation time**, never directory listing
order: **B** = Pixel Buds in-ear Bluetooth on Android; **S** = speakerphone;
**H** = normal handset to ear as requested. The Bluetooth-disconnected instruction
for H was not independently confirmed, so H is a reported mode, not verified
device telemetry. There is only one call per mode. Call, microphone/channel,
utterance content and order effects cannot be disentangled here.

All synthetic-flagged records were excluded: one synthetic streaming transport
call plus two earlier synthetic recording-test records were present in metadata.
No synthetic voice was relabeled as a human impostor.

## Actual audio, not nominal recording duration

The raw clips total **184.44 s**, approximately ten seconds apiece, but include
substantial silence. A shared endpoint energy gate retained **42.58 s** of
waveform, including internal pauses; estimated energy-active audio totaled
**35.66 s**. Energy-active duration per clip was **1.56–2.38 s**, not ten seconds
of usable speech. No samples reached the tested digital clipping level.
Collector metadata reports **104–112 ms inserted silence per clip**; this is a
transport observation, not proof of audible packet loss or an error-rate estimate.

The same windows were prepared once and fingerprinted privately for all models:

1. Decode existing mono 8 kHz WAVs and verify stored source hashes/completeness.
   No second simulated telephone codec/filter is applied to actual PSTN captures.
2. Exclude the first **500 ms**: the collector starts inbound capture before a
   **150 ms, 880 Hz** outbound beep, which could return through acoustic echo.
   All retained trimmed regions in this corpus begin at least **1.18 s** into the
   source. This is a conservative opening guard, not validated echo cancellation.
3. Use nonoverlapping **20 ms** RMS frames. Activity threshold in dBFS is the
   maximum of **−55**, **20th percentile + 12**, and **95th percentile − 30**.
   Discard active runs shorter than **100 ms**. Trim to the first/last retained
   active frame with **100 ms endpoint padding**; preserve internal pauses.
   No concatenation, peak normalization or model-dependent segmentation.
4. Resample each selected source window from 8 to 16 kHz with SciPy polyphase
   resampling. Upsampling cannot restore missing telephone bandwidth.
5. **Full trimmed:** all 18 retained, waveform lengths **1.82–3.60 s**.
   **Three seconds active speech:** all **18 excluded**, because no clip has
   three seconds of energy-active material. No padded waveform is called speech.
6. As an explicit alternative sensitivity check, select a **three-second source
   context** maximizing the number of energy-active frames (earliest tie wins),
   retaining surrounding silence. All 18 qualify. This is NOT a three-second
   voiced-speech evaluation and does not add invented or repeated audio.

This is a transparent energy heuristic, **not a trained speech VAD**. Noise,
breaths or echo may count as active; quiet consonants may be missed. No listening,
ASR/transcript, speaker-overlap or phonetic audit was performed. Opening-beep and
silence rejection were checked with offline test signals, not proof that every
retained frame is caller speech. Better segmentation remains an evaluation need.

## Genuine cosine matrices: full trimmed windows

Cells are **median [minimum, maximum]** raw cosine. Diagonal cells contain 15
distinct within-call clip pairs, off-diagonal cells contain 36 cross-call pairs.
No self-pairs or duplicate WAVs. Matrices are symmetric, not extra independent
observations. Per condition, there are 45 within-mode and 108 cross-mode pairs,
153 pairs/model. The two window conditions produce 306 pairs/model, highly
correlated through shared recordings. Raw pairs and embeddings remain private.

### ECAPA

| | B | S | H |
| --- | --- | --- | --- |
| B | .630 [.465, .733] | .622 [.421, .698] | .560 [.318, .711] |
| S | .622 [.421, .698] | .673 [.615, .762] | .642 [.490, .766] |
| H | .560 [.318, .711] | .642 [.490, .766] | .678 [.530, .749] |

### CAM++ PyTorch

| | B | S | H |
| --- | --- | --- | --- |
| B | .636 [.510, .751] | .627 [.384, .761] | .614 [.345, .772] |
| S | .627 [.384, .761] | .753 [.622, .830] | .680 [.482, .787] |
| H | .614 [.345, .772] | .680 [.482, .787] | .701 [.630, .777] |

### ReDimNet2 B3 LM

| | B | S | H |
| --- | --- | --- | --- |
| B | .649 [.346, .757] | .644 [.380, .766] | .612 [.298, .762] |
| S | .644 [.380, .766] | .738 [.586, .808] | .630 [.451, .783] |
| H | .612 [.298, .762] | .630 [.451, .783] | .689 [.567, .773] |

## Within-model channel/call mismatch and window sensitivity

Pooled medians, with cross-minus-within computed separately for each model.
These differences are descriptive within-model effects, not comparable calibrated
security margins. Neither condition contains a negative identity trial.

| Model | Trimmed within / cross | Difference | 3 s context within / cross | Difference |
| --- | --- | --- | --- | --- |
| ECAPA | .670 / .610 | −.061 | .691 / .630 | −.061 |
| CAM++ PyTorch | .701 / .643 | −.058 | .738 / .704 | −.034 |
| ReDimNet2 B3 LM | .688 / .624 | −.064 | .707 / .660 | −.047 |

Each adapter has lower pooled cross-mode than within-mode median in both window
conditions. The Buds call has the lowest within-call median for each adapter, but
this does not establish Bluetooth as the cause: the call/content confound remains.
The source-context window changes scores without supplying extra speech. This
reinforces that padding/context/segmentation can materially affect embeddings;
increased cosine is not evidence that silence improves verification.

Only one cross-call pair repeats an exact collection phrase; the other 107 use
different phrases. Excluding that one shared-text pair changes trimmed cross
medians to .610 / .650 / .625 respectively. There is no adequate repeated-text
stratum to isolate a text effect. Phrase contents are not published.

## CPU extraction cost

M3 Max, CPU, four intra-op threads and one inter-op thread. One unmeasured model
warm-up, then three serial extractions/input, 54 timings per model/window.

| Model | Trimmed median ms [min, max] | 3 s context median ms [min, max] |
| --- | --- | --- |
| ECAPA | 15.7 [13.5, 21.3] | 18.5 [17.0, 19.6] |
| CAM++ PyTorch | 15.4 [14.0, 20.8] | 17.7 [16.9, 19.6] |
| ReDimNet2 B3 LM | 66.9 [59.5, 80.4] | 73.1 [69.5, 80.0] |

Includes each native frontend, inference, finite/norm checks and L2 normalization.
Excludes source decoding, segmentation/resampling, imports, uv setup, model load
and speech collection. Small-sample extrema are not service-level tail guarantees.
Fixed model order, thermal state and background load confound architecture-only
speed claims. No GPU, concurrency, RAM, streaming or deployment CPU benchmark.
Cached model resolution/load includes provenance checks and CAM++ audit work, not
a minimal service loader. Speech accumulation, not these milliseconds, dominates
time-to-evidence. Package/revision/hash details remain in the private report and
the earlier synthetic comparison's public model-provenance documentation.

## Practical next candidate, not an authentication decision

**Use CAM++ PyTorch as a provisional efficiency candidate for the next evaluation,
keep ECAPA as the baseline and B3 as the contemporary challenger.** CAM++ ran at
ECAPA-like extraction cost with no execution blocker. Nothing here demonstrates
that its genuine-score level or variation translates into better impostor
discrimination. B3's roughly fourfold compute cost in this run has no demonstrated
security payoff from positive-only data. Architecture age alone is not a reason
to replace ECAPA. Keep the previously observed released CAM++ ONNX/PyTorch parity
problem unresolved; this experiment uses PyTorch, not that ONNX output.

Before deciding: collect longer caller-only speech across independent days/calls
and multiple handsets, plus consented non-Adam voices, including similar voices.
Separate call/day-held-out calibration from evaluation. Decide a threat model,
test replay/cloning separately, and preserve an insufficient-evidence outcome and
ordinary-telephone-compatible recovery. Do not choose a cosine threshold here.

## Privacy and reproduction

No source samples were deleted or changed. WAVs, identities/provenance linking to
calls, exact phrases, embeddings, per-trial scores and validation fingerprints
stay under the **private collector runtime directory**, outside Git. New output
directory permissions are 0700; result/embedding files are 0600. This is access
control, not encryption or a retention/deletion policy. No audio/embeddings were
sent to hosted inference. No collector, routing or production code was edited.

From repository root, with the private corpus available:

```sh
uv run --locked --python 3.11 --script experiments/speaker_verification/real_compare.py
uv run --locked --python 3.11 --script experiments/speaker_verification/real_compare.py \
  --output "$HOME/Library/Application Support/Dogwalk/phone_voice_collection/speaker_comparison/rerun.json"
uv run --locked --python 3.11 --script experiments/speaker_verification/validate_real_compare.py \
  --rerun "$HOME/Library/Application Support/Dogwalk/phone_voice_collection/speaker_comparison/rerun.json"
```

Default detailed output is private `speaker_comparison/run.json`, with adjacent
per-model embedding files. Existing outputs are deliberately not overwritten:
choose a new private `--output` for subsequent runs. Outputs outside the collector
runtime are rejected. `--inspect` performs only shared energy analysis, also to a
private output. Each uv script has its own dependency lock and never installs in
the `voice-doodle` environment. No commit or push was performed.

Validation passed: all 18 WAV hashes are unique, call chronology is ordered,
all three adapters used the same 36 processed inputs and 306 trial definitions,
stored embedding norms are unit length, all scores reconstruct from private
embeddings, and all 3×3 matrix summaries recompute. The independent complete rerun
had **maximum cosine difference 0.0 over 918 genuine scores**. Output/embedding
permissions and rejection of silence/opening-beep-only test signals passed.
New public-facing files were scanned for provider identifiers, exact private
phrases and source WAV hashes; none were present. Detailed validation stays private.
