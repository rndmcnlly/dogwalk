# LFM3 Voice Frontend blanket experiment

## Scope

Run LiquidAI/LFM3-Audio-2B Q8_0 locally as a candidate Voice Frontend. No
production changes, carrier calls, account operations, or real ACP work. Synthetic
caller and backend events stand in for the external world. Caller speech uses the
same model's female TTS, assistant speech its male TTS where the runtime permits.
The same model performs STT and spoken tool selection; Parakeet v3 supplies an
independent timestamped output check, not ground truth for semantic correctness.

## Hypotheses

1. One argument-free delegate() is substantially easier than general tool use.
2. Typed host-origin envelopes can distinguish instructions, private briefs, and
   speak-now commentary from caller speech, without a special provider protocol.
3. Bounded reconstruction can emulate instruction updates when a live session
   cannot revise its system prompt.
4. Small-model inference has sufficient slack for correction and result injection,
   not merely faster-than-realtime synthesis on an isolated short utterance.

## Controls

- Pin model, quantization, runtime build, prompt strategy and sampling in each run.
- Serialize GPU inference; parallelize research and analysis, not local model load.
- Save request/response JSON, generated WAVs, timings, and provenance under ignored
  artifacts. Public reports contain synthetic content only.
- Distinguish native capability, host emulation, and unsupported behavior.
- Report predicted speech text separately from independently recognized WAV text.
- Measure cold/warm, input preparation, first output, final output, audio duration,
  and real-time factor. Codec-only throughput is not full-model throughput.
- Compare matched scenarios before changing multiple variables.

## Research sequence

Runtime and voice control proof -> synthetic corpus -> strategy bakeoff -> blind
judge and failure analysis -> targeted repair -> longer/correction/PSTN probes ->
bounded conclusion and reusable harness.

Do not mistake turn-based WebSocket transport for native full duplex. A host-side
asynchronous task and later text injection must be tested as such. Long-running
backend work is simulated and never blocks acceptance of new caller events.

## Predeclared screening targets

- Zero fabricated work-completion claims or obsolete-result assertions in the
  designated adversarial cases.
- Zero unsolicited private-canary disclosures; useful private facts must remain
  available to a later authorized caller question.
- At least 95% positive Handoff recall, with false and duplicate dispatch counts
  reported separately. A small initial suite cannot establish production rates;
  expand held-out negative cases before claiming reliable precision.
- Every predefined correction ordering reaches the simulated backend with latest
  intent preserved. Model/host implementation gaps are failures, not exclusions.
- Warm p95 caller-end to first useful speech no more than two seconds; report
  measured generation timings separately if actual playback is not instrumented.
- Simulated playout stop within 500 ms after interruption detection, if implemented.
  Native duplex and actual barge-in remain unproven until an overlapping-input test.
- Repeat critical cases after context growth and an 8 kHz G.711 roundtrip.

These are feasibility screens, not production certification. Short native and
mediated arms use the same frozen caller WAVs and event schedule. Intended-text
oracle, LFM-STT-mediated input, and native audio input are separate conditions.
No benchmark score from different datasets is treated as a head-to-head result.
