# Round two: LFM3 as separately scored organs

2026-11-12. Driver: Claude Opus 5.5, informed by round one (`FINDINGS.md`,
`RESULTS.json`). Same runtime, model revision, quantization and suite.

## Why change the decomposition

Round one asked one 2B model to be router, authority and voice at once, through
a native tool-call choice, and then tuned context. Routing plateaued at 30–31/40
with 21/72 false and 7/48 missed Handoffs, and generative speech rewrote
consequential facts (granted permission became "we can't proceed"). Further
prompt tuning of the same monolith would mostly measure prompt luck.

Round two keeps authority in the host and asks the model narrower questions,
each scored on its own, then composes them:

1. **Router organ.** A closed question over caller audio plus a compact host
   state snapshot, answered by one constrained token. Read token probabilities
   rather than sampling, so routing becomes a score with a precision–recall
   curve and a chosen operating point, not one tool-call coin flip. A
   multi-way variant also classifies silence and resume requests.
2. **Speech organ.** Orchestrator content is rendered (a) verbatim through male
   `/tts`, or (b) with generative glue around host-locked spans, checked by the
   host against predicted text and independent Parakeet recognition, falling
   back to (a) on any mismatch. Native generative speech is used only for
   social turns the router declines.
3. **Host.** Owns silence, the audible ledger, Handoff transcript cuts,
   instructions and authority state. Deterministic host behavior is reported as
   host behavior, never credited to the model.

Then a comparison floor: **Orchestrator-always**, where every caller turn is a
Handoff and LFM only listens and speaks. It measures what frontend routing buys
in Handoff volume and latency, and whether a strong Orchestrator absorbs false
Handoffs harmlessly.

## Hypotheses

- H1: A probability-scored router reaches 100% positive recall on the main suite
  with fewer false Handoffs than round one's best arm (21/72 across repeats,
  about 7/24 per pass).
- H2: The threshold chosen on the main suite transfers to fresh held-out
  positives and negatives, including caller audio from an independent
  synthesizer (macOS `say`), which breaks the self-TTS/self-STT loop.
- H3: Verbatim and locked-span speech eliminate meaning reversals in
  Orchestrator content (round one: 5/9 best generative cue).
- H4: The composed pipeline beats round one's 30–31/40 routing and passes the
  silence and authority gates that round one failed, with the host gate's
  share of that success stated separately.
- H5: Under Orchestrator-always, false Handoffs cost Orchestrator calls and
  latency but not correctness. If so, the frontend router is an optimization,
  not a safety boundary, and its operating point should favor recall.

## Controls

- Threshold and prompt selection use the main suite only. Held-out cases are
  authored by an agent that never sees router prompts, and are scored once.
- Same frozen caller WAVs as round one for matched comparisons; three input
  arms (native audio, oracle text, LFM STT with explicit 16 kHz normalization).
- Serialized GPU: LFM inference and Parakeet never overlap. oMLX untouched.
- Report each organ's numbers, then the composite, then the baseline. Round-one
  numbers are cited, not re-derived.
- A missed correction or cancellation is a critical failure regardless of
  aggregate score.

## Gates (screening, not certification)

- Router: 16/16 main positives at the chosen threshold; report false Handoffs
  on main negatives and held-out negatives with counts, plus the full curve.
- Speech: zero locked-span violations reaching the caller after host checking;
  report fallback rate and Parakeet word error on locked spans.
- Composite: zero silence violations, zero invented completion or status, every
  correction and cancellation handed off.
- Latency: caller end to routing decision and to first PCM, median and p95.
