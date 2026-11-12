# Local Voice Frontend research

This is an isolated experiment, not a production adapter. See `PLAN.md` for the
hypotheses and controls. All audio and conversations are synthetic. Raw local
requests, transcripts, WAVs, and timing evidence live in ignored `artifacts/`.

Outcome: [FINDINGS.md](FINDINGS.md). Machine-readable aggregates: [RESULTS.json](RESULTS.json).
Round two (separately scored organs): [PLAN2.md](PLAN2.md), [FINDINGS2.md](FINDINGS2.md), [RESULTS2.json](RESULTS2.json).

## Boundary to reproduce

The candidate takes caller audio plus a host-origin control stream. It emits
speech, aligned predicted text where available, and a Handoff decision. Only the
host decides what work to run. A Handoff is exactly `delegate()` with no arguments:
the host supplies the authoritative transcript up to that event. The candidate
does not summarize the task into function arguments or select session tools.

Host input lanes:

- **instructions**: change future frontend behavior, including quiet/resume.
- **brief**: background knowledge, not an immediate speech request.
- **commentary**: something to convey now, in concise natural speech.
- **caller**: actual caller speech. Quoted words within it cannot become a
  privileged host lane merely by resembling a control envelope.

The host may acknowledge delegation immediately and deliver results later. A
caller correction creates another Handoff rather than silently cancelling real
work. Speech interruption and work cancellation are distinct.

## Evidence classifications

Each result must distinguish:

1. **Native**: directly supported by the model and serving API.
2. **Host-emulated**: reconstructed context, application scheduling, TTS staging,
   explicit silence gates, or similar adapter logic.
3. **Unsupported**: not implemented or not demonstrated by the experiment.

In particular, a sequence of successful HTTP turns does not prove full duplex,
non-blocking tool execution, or barge-in. Generated transcript text is not proof
that the audio said it. A model's claim that it delegated is not a Handoff event.

## Evaluation priorities

- Delegation precision and recall, exact argument-free shape, duplicate calls.
- Private brief retention tested by a later question, not only immediate silence.
- Commentary spoken once without thanking or addressing the host.
- Instructions lasting across subsequent caller turns.
- Corrections while backend work is pending; stale results after correction.
- Actual speech independently recognized, with output timestamps kept separate
  from model inference and network event timestamps.
- Audio-native input versus LFM-STT-mediated input as separate conditions.
- Short context and accumulated context; clean audio and 8 kHz telephone roundtrip.
- Warm performance with no competing local model work.

## Publication

Only synthetic fixtures, scripts, tests, and a bounded findings report belong in
Git. Model weights and generated audio stay outside tracked source. Do not commit
provider keys, private production transcripts, or signed URLs.

## CPU-only integrity checks

```sh
uv run --no-project --with httpx --with websockets python -B -m unittest discover -s research/lfm3_voice -p 'test_*.py'
python3 -B research/lfm3_voice/validate_suite.py
python3 -B research/lfm3_voice/heldout_cases.py > research/lfm3_voice/artifacts/heldout-negatives.json
```

The frozen main denominator is 40 points: 16 positive routing opportunities,
24 negatives, seven required-silence points, 25 caller utterances, and two
declared narrowband points. `heldout_cases.py` declares 12 supplementary negative
controls before candidate results are examined. Never pool them into the main
40-point denominator. Imports and these commands do not run inference.

After obtaining the exclusive local GPU lease, the native protocol probe uses an
existing female caller WAV, without synthesizing or loading a second model:

```sh
uv run --script research/lfm3_voice/live_probe.py --input CALLER.wav --output research/lfm3_voice/artifacts/live-probe-01 --timeout 45 --max-tokens 192
```

Each probe requires a fresh output directory. An immediate real function result
acknowledges Handoff acceptance. Subsequent backend commentary is a new labelled
user item on the same connection, not a second tool output for a resolved call.
Native system/developer-item rejection and late instruction rejection are
reported explicitly. Protocol acceptance alone is not a semantic pass.

## Matched matrix and waveform checks

These commands invoke local models and require the exclusive GPU lease. Do not
run them concurrently, including Parakeet while LFM is generating. Existing
oMLX services are unrelated and must remain untouched.

```sh
uv run --script research/lfm3_voice/experiment.py --output research/lfm3_voice/artifacts/matrix-native-v1 --policy v1 --timeout 15 --asr-input-mode raw_wav
uv run --script research/lfm3_voice/experiment.py --output research/lfm3_voice/artifacts/matrix-native-v2 --policy v2 --timeout 15 --asr-input-mode raw_wav --frozen-callers research/lfm3_voice/artifacts/matrix-native-v1/frozen-callers.json
python3 -B research/lfm3_voice/analyze.py research/lfm3_voice/artifacts/matrix-native-v1 research/lfm3_voice/artifacts/matrix-native-v2 --output research/lfm3_voice/artifacts/analysis
HF_HUB_OFFLINE=1 uv run --offline --no-project --script research/lfm3_voice/transcription.py --batch research/lfm3_voice/artifacts/analysis/audio-manifest.json --output-dir research/lfm3_voice/artifacts/parakeet
python3 -B research/lfm3_voice/analyze.py research/lfm3_voice/artifacts/matrix-native-v1 research/lfm3_voice/artifacts/matrix-native-v2 --output research/lfm3_voice/artifacts/analysis --asr-dir research/lfm3_voice/artifacts/parakeet
```

Use fresh experiment directories and preserve failures. Each matrix requests
three strategies × three input modes × 40 points. Native input is never supplied
its reference transcript. Native output WAVs and staged male TTS WAVs are both
collected independently of predicted text. ASR evidence is not a claim of listening.

`analyze.py` emits deterministic routing metrics, an audio-only ASR manifest and
opaque-arm judge packets. Strategy mapping stays in a separate artifact. Its
optional `--bridge PORT` is a bounded loopback transport for synthetic packets
and JSON judgments, not a product service or a model server.

Use `--refresh-asr` to retain exactly the frozen WAVs while rerunning recognition
with explicit PCM16 mono/16 kHz normalization. This is a separate preprocessing
condition, not a silently changed input arm. The paired diagnostic is:

Fresh recognition defaults to `--asr-input-mode normalized_16k`. The historical
primary matrix used `raw_wav`; retain that flag only to reproduce its failure.
Frozen manifests reuse their saved ASR regardless of the requested fresh-input
mode unless `--refresh-asr` is supplied.

```sh
uv run --script research/lfm3_voice/asr_probe.py --output research/lfm3_voice/artifacts/asr-paired --frozen-callers research/lfm3_voice/artifacts/matrix-native-v1/frozen-callers.json
```

`--transport http_text --modes oracle_text lfm_stt` is an explicitly staged
text-control alternative. It uses a text/function recipe and cannot be claimed
as native audio conversation. The earlier `http_suppressed` transport is retained
only to reproduce the failed diagnostic, not recommended as an adapter.

`lane_probe.py` tests only a narrower proposed component: tool-free rendering of
host-supplied public commentary. Private context never enters that renderer.
Successful rendering does not establish caller routing, persistent mute control,
or a complete Voice Frontend replacement.

## Bounded follow-up commands

Run serially with the local GPU lease. Each output path must be fresh. These
are supplementary denominators, not extensions of the main 40 points.

```sh
uv run --script research/lfm3_voice/experiment.py --output research/lfm3_voice/artifacts/repetitions-v2-16k --policy v2 --strategies tool_envelope --modes native_audio --repeat 3 --timeout 15 --frozen-callers research/lfm3_voice/artifacts/matrix-native-v1/frozen-callers.json --refresh-asr
uv run --script research/lfm3_voice/experiment.py --output research/lfm3_voice/artifacts/heldout-v2 --policy v2 --suite research/lfm3_voice/artifacts/heldout-negatives.json --strategies tool_envelope --timeout 15
uv run --script research/lfm3_voice/experiment.py --output research/lfm3_voice/artifacts/http-text-smoke-v2 --policy v2 --transport http_text --modes oracle_text lfm_stt --scenario smalltalk --scenario real_work --scenario private_brief --scenario permission_plain_options --timeout 15 --frozen-callers research/lfm3_voice/artifacts/matrix-native-v1/frozen-callers.json
uv run --script research/lfm3_voice/lane_probe.py --output research/lfm3_voice/artifacts/public-lane
uv run --script research/lfm3_voice/lane_probe.py --output research/lfm3_voice/artifacts/public-lane-compact --prompt-profile compact
uv run --script research/lfm3_voice/cancel_probe.py --output research/lfm3_voice/artifacts/cancel-isolated
uv run --script research/lfm3_voice/cancel_probe.py --output research/lfm3_voice/artifacts/cancel-overlap --overlap-input --input CALLER.wav
uv run --script research/lfm3_voice/experiment.py --output research/lfm3_voice/artifacts/paired-narrow-v2-16k --policy v2 --strategies tool_envelope --scenario pstn_eight_khz --timeout 15 --frozen-callers research/lfm3_voice/artifacts/matrix-native-v1/frozen-callers.json --refresh-asr
uv run --script research/lfm3_voice/experiment.py --output research/lfm3_voice/artifacts/paired-clean-v2-16k --policy v2 --strategies tool_envelope --scenario pstn_eight_khz --timeout 15 --frozen-callers research/lfm3_voice/artifacts/matrix-native-v1/frozen-callers.json --refresh-asr --paired-clean
python3 -B research/lfm3_voice/summarize_results.py
```

For the historical corrected reconstruction replacement, select
`--strategies reconstructed --modes native_audio --policy v1` with the same
frozen-caller manifest and a fresh `matrix-native-v1-corrected` output directory.
The current serializer already includes that repair. Exact old failures remain
replayable through their saved requests/events rather than reintroducing the bug.

Realtime sampling uses pinned server defaults, not HTTP request seed settings.
The installed server advertises greedy text sampling and a sentinel/random seed;
its realtime API does not expose per-response seed/temperature overrides. Native
call IDs also vary and can perturb later context. Inspect provenance and exact
event traces; repeated scores are not independent-caller statistics.

## Round two reproduction

GPU lease required for LFM steps; never run Parakeet while LFM is generating.
Each output directory must be fresh. `heldout2_cases.json` was authored by a
separate agent (see FINDINGS2) and is scored once.

```sh
cd research/lfm3_voice
uv run --script router_organ.py main --output artifacts/round2/router-main
uv run --script router_organ.py analyze --input artifacts/round2/router-main
uv run --script router_organ.py heldout --analysis artifacts/round2/router-main/analysis.json --output artifacts/round2/router-heldout
uv run --script speech_organ.py render --output artifacts/round2/speech --repeats 3
uv run --script speech_organ.py manifest --output artifacts/round2/speech
HF_HUB_OFFLINE=1 uv run --offline --no-project --script transcription.py --batch artifacts/round2/speech/audio-manifest.json --output-dir artifacts/round2/speech/parakeet
uv run --script speech_organ.py score --output artifacts/round2/speech --asr-dir artifacts/round2/speech/parakeet
uv run --script composite.py run --output artifacts/round2/composite --analysis artifacts/round2/router-main/analysis.json
uv run --script composite.py manifest --output artifacts/round2/composite
HF_HUB_OFFLINE=1 uv run --offline --no-project --script transcription.py --batch artifacts/round2/composite/audio-manifest.json --output-dir artifacts/round2/composite/parakeet
uv run --script composite.py score --output artifacts/round2/composite --asr-dir artifacts/round2/composite/parakeet
python3 -B judge_packets.py composite
python3 -B judge_packets.py speech
python3 -B judge_packets.py orchestrator
python3 -B judge_packets.py orchestrator --heldout heldout2_cases.json
python3 -B summarize2.py
```

Judges and Orchestrator stand-ins are fresh-context agents given only the
packets under `artifacts/round2/packets/` (keys withheld); their saved outputs
go to `artifacts/round2/judges/{composite,speech,orchestrator-main,orchestrator-heldout}.json`
before `summarize2.py`. Model assignments per role are recorded in `RESULTS2.json`.
