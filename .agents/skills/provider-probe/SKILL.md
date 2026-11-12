---
name: provider-probe
description: Measure how the Voice Frontend provider (GPT-Live) actually behaves instead of guessing: delegation timing, transcript ordering and timestamps, when it delegates or stays quiet, whether it speaks a brief, how it handles pauses, corrections, and barge-in. Use before tuning a timer, grace period, or window in voice_session.ts, before changing delegation config, and whenever a hypothesis about provider behavior would decide a code change.
---

# Provider probe

Production calls cannot tell you the ground truth: you never know exactly when the caller stopped speaking. A synthetic caller can. `delegation_probe.py` scripts caller lines, so every line's true start and end are known, streams them to the real GPT-Live API in real time, and logs every server event against the same clock. Then replay those logs through a copy of the production logic to see what the Orchestrator would have received.

This replaced a 300 ms grace period nobody had measured. The first run showed the wait was useless; the replay showed two real bugs (merged entries resending requests, and mid-request delegations losing corrections). Measure first.

## Run it

```sh
uv run --script delegation_probe.py --only plain                 # one session, ~25 s: smoke test
uv run --script delegation_probe.py --reps 3 --out /tmp/dwprobe/runs/<slug>   # every scenario, ~6 min
uv run --script delegation_probe.py --analyze /tmp/dwprobe/runs/<slug>        # re-analyze, no API calls
```

It reads `OPENAI_API_KEY` from `.env` and spends real GPT-Live minutes (about 20 s a session). Run it in the background for batches, one session at a time: parallel sessions skew each other's timing. Raw events, one JSON per session with `lines` (ground truth) and `events` (`recv_ms` on the audio clock, 0 = first frame), stay under `/tmp/dwprobe/runs/`, never in the repository.

What the harness keeps true to production: the Voice Frontend prompt and voice come from `frontendInstructions(DEFAULT_THEME)`, bundled with esbuild (`.md` as text, `.pcm` and `.wasm` as binary, `cloudflare:*` external), and the delegation config matches `index.ts` (`client`, 400 ms acknowledgement delay). Audio is G.711 mu-law at 8 kHz, band-limited to 300–3400 Hz, paced as 20 ms frames with silence included. A stand-in Orchestrator sends a `brief` at once and a `say` after `--reply-ms`.

## Add a scenario

`SCENARIOS` maps a name to a list: a string is a caller line, an integer is silence in milliseconds. Write the scenario around the question: a pause length that might trigger an early delegation, a correction, a caller who talks over the Voice. Always keep a control that should not delegate (`chitchat`). Leave about 12 s of silence at the end so the Voice can answer.

To probe something other than delegation (a brief being spoken, an instruction append, mute), change the stand-in `reply()` and add a column to `analyze()`. Keep the probe one file.

## Replay through production logic

The probe tells you what the provider sent; the question is usually what Dogwalk did with it. Port the few production functions involved (for transcripts: `appendTranscript`, `takeTranscript`, `forwardContinuation`) to Python line for line, feed them the logged events in `recv_ms` order, and print what each Handoff and Continuation would contain. Replay the current code first to confirm the bug, then the proposed change against the same logs before touching TypeScript. Timers replay as `recv_ms` deadlines.

## Baseline (2026-11-12, gpt-live-1, 15 sessions)

Compare new runs to these; a shift means the provider changed.

- Transcripts arrive word by word, `start_ms` and `end_ms` on a 200 ms grid, about 400 ms later than the true speech edges.
- Every delta up to `offset_ms` arrives before its `session.delegation.created` (12 of 12), and `offset_ms` is the `start_ms` of the last word handed over.
- A delegation arrives 520–715 ms after its offset, 640–950 ms after the caller's true end of speech.
- Pauses of 450–700 ms mid-request drew an early delegation in 4 of 6 runs, with no second delegation for the rest. A 250 ms pause never did.
- Chitchat drew no delegation (0 of 3).
- The Voice spoke the stand-in's `brief` text aloud in 3 of 12 runs (unresolved).

## Limits

- A primary WebSocket from the laptop is not SIP plus a sideband from Cloudflare. Event order within the provider's stream should carry over; absolute latencies will not.
- `say` is cleaner than a caller on a noisy line. Treat thresholds tuned here as starting points and check them against real calls with the `call-analysis` skill (`voice.continuation.forwarded`, `--only timing`).
- Three runs per scenario finds structure, not tails. Before setting a cap from a percentile, run more.
