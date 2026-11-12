---
name: voice-research
description: Investigate Dogwalk speaker verification from recordings: Voice Donations, the Voice Reference, verifier scores, thresholds, challenge phrases, prompt leakage, mic or codec conditions, "why did that score low", "listen to the donations". Use before writing ad hoc audio analysis, and before changing the threshold, the verifier, or the Identity Check on a hunch.
---

# Voice research

Judge speaker verification by listening to the recordings and reading the verifier's own view of them, not by debating hypotheses. Read `DOMAIN.md` first for terms: Voice Reference, Re-enrollment, Identity Check, Identity Challenge, Challenge Practice, Voice Donation, Donation Session. Operations keep waveforms transient. Raw audio exists only in Voice Donations, which callers consent to, and in the earlier private collection corpus.

## Start with the soundboard

```sh
uv run --script voice_research.py list            # Donation Sessions in R2: rounds, audio, size, consent version
uv run --script voice_research.py pull            # fetch every session not yet local (or name session ids)
uv run --script research_soundboard.py            # regenerate and open the page; --no-open, --rescore
```

The page is `~/Library/Application Support/Dogwalk/voice_research/soundboard.html`. Every clip gets a player, a waveform and spectrogram, a Parakeet transcript with clickable word times, the verifier's chosen 3 s window, the production cosine to the Voice Reference against the threshold, the clip's role in the reference (used, or dropped as an outlier), and, for donation rounds, where the tone and spoken prompt fall and whether they leaked. Transcripts and scores are cached by audio hash, so a rerun only processes new clips. Ask Adam to listen to anything surprising: his ear identified both outliers the leave-one-out rule had dropped (an unusual neck posture, a poorly captured clip) as his own voice, but doing something odd.

Donations carry no link to their caller. Adam annotates speaker and condition from memory, using a session's time, in a private `annotation.json` beside the pulled session (`{"speaker": "...", "condition": "..."}`); the soundboard groups clips by it. Ask him for the annotation before analyzing a new session.

## Raw access, when the soundboard lacks something

- Score any WAV (8 or 16 kHz mono 16-bit) against a User's Voice Reference: `POST https://dogwalk.tools/admin/api/voice-verify?user=<id>`, HTTP Basic `adam` with `security find-generic-password -a adam -s dogwalk-admin -w`, header `user-agent: dogwalk-research/1` (the zone blocks Python's default). `GET /admin/api/voice-verify/health` first to warm the model. Returns cosine, embedding space, audio and speech seconds. It refuses audio over 30 s (score the first 30 s) and returns 422 for too little speech.
- Challenge Practice rounds keep no audio, but each scored round leaves a `practice.round` Call Activity event (`outcome`, `cosine`, `speech_seconds`; wrong phrases and short speech too; `wait_ms`, and `speculative` with `head_start_ms` when the score started at the end of the answer, before `/heard`). Read them through the operator timing API (`/admin/api/timing?call_sid=...`) or `playtest.py`; they show how a real call scores in a real condition without a donation.
- The verifier is `speaker_worker/` (Service Binding only, no public route). `preprocess.js` and `check_preprocess.py` define the window: skip 0.5 s, the most speech-active 3 s, resampled from 8 to 16 kHz. Any local analysis must reuse this window, or its scores are not comparable.
- The private Voice Reference builder and Adam's reference: `~/Library/Application Support/Dogwalk/voice_reference/` (`build_reference.py`, `adam.json`). The earlier corpus: `.../phone_voice_collection/`. Save working files under `/tmp/dogwalk_voice_<slug>/`.

## Privacy and safety

- Audio, embeddings, transcripts, scores tied to clips, and annotations never go to Git, a hosted service, or a chat log beyond summaries. Keep the private directories 0700 and files 0600.
- Never use Twilio `<Record>`: this account served recording media without authentication. Audio arrives by `<Start><Stream>` only.
- Deleting a donation is `voice_research.py delete <session> --yes`, done only at the donor's request, relayed through Adam.
- Synthetic impostors and clones are made locally and deleted after scoring unless Adam asks to keep a set.

## Findings so far (2026-11-11)

- Production and local scores agree within 1.5e-6 when the model hash matches the embedding space string.
- Adam's reference clips (handset, buds, speakerphone): 0.60 to 0.90. Car hands-free while driving, never enrolled: 0.50 to 0.73. Ten macOS `say` voices through G.711: at most 0.40. The starting threshold is 0.5.
- Car audio had no IVR prompt leakage: the phone's echo cancellation removed it. The lowest car round (0.504) had only 1.6 s of speech in its window. Too little speech to judge earns another phrase, never a failure.
- A fresh verifier instance takes about 4 s to load the model; a warm one about 0.7 s. Cloudflare often routes to fresh instances, so warm the verifier early in a call.
- Warmth is per machine (2026-11-11, `speaker_worker/latency_probe.py`): 13 of 30 sequential probes on fresh connections were slow (over 2 s, a model load), 0 of 30 on one kept-alive connection. An idle instance stayed warm at 120 s and was cold by 300 s (one sample each). Score where and soon after you warmed: a warm from one invocation helps a later request only if it lands on the same machine within a couple of minutes. Nothing yet shows where Twilio's later webhooks or streams land relative to `/voice`. The `speaker warm` log line reports only the warm RPC (did it load, how long) and cannot show whether a later challenge reaches the warmed isolate: that takes correlating it with an observation from the challenge's own verification, such as its latency or a cold flag on that request. Measure latency with the probe, not with ad hoc loops; its health probes stand in for challenges, and since health calls `warm()` first, its `cold` flag separates a model load from queueing.

## Challenge round timing and the speech floor (2026-11-11)

From Adam's speakerphone practice call (10 rounds), the car donation (8 phrase rounds), and the 18-clip phone corpus, with `context3` and the local ONNX model:

- **Where a round's time goes.** Scored rounds took 15.7 s on average from the previous outcome, wrong phrases (which end at `/heard`) 10.8 s: about 5 s of silence passed between the answer and its level, where a warm verifier needs 0.7 s, so the verifier was probably cold on most rounds. Inside a donation round: tone and prompt about 3.5 s, the caller starts 0.7 s after the prompt (up to 2.5), answers in 3.2 s (Parakeet first to last word), then `speechTimeout="auto"` closed the Gather 1 s after the last speech energy in 3 rounds and 3.5 to 4.1 s after it in 5. The stream ended 0.45 s after `/heard`. Donation `/heard` webhooks took 0.7 to 1.3 s to answer.
- **Pauses.** No repetition held a pause over 0.76 s (energy) or 0.24 s (between Parakeet words), so a 1 s `speechTimeout` (Twilio's smallest integer; any `speechModel` requires an integer) should not cut a phrase. Unverified on a live call until the next playtest; `practice.round` now logs `wait_ms`, the result step's wait for the score.
- **Speech measure.** `speech_seconds` counts energy-active frames over the whole clip, not the window, against an adaptive threshold, so it depends on the line as much as on the words: 0.25 s per phrase word on the enrolled phone conditions, 0.19 in the car, while articulation ran 0.38 s per word in both. An 8-word phrase measures about 2.0 s clean and 1.6 s in the car.
- **How little speech is enough.** Keeping only the first N measured seconds of each genuine answer (the rest replaced by the clip's own floor): on enrolled conditions the cosine fell 0.53 on average at 1.0 s, 0.21 at 1.3, 0.07 at 1.5 (worst 0.21), 0.04 at 1.6, about 0.01 from 1.8. Car clips barely moved, because the measure there undercounts speech. Hence `MIN_SPEECH_SECONDS` 1.5, down from 2.0, which rejected two strong rounds (1.9 and 1.64 s); 6 of 8 car rounds clear it. Truncation only lowered genuine scores; it says nothing about impostors with little speech, and enrolled clips are optimistic.
- **Not shown:** whether numeric `speechTimeout` endpoints faster than `auto` on a noisy line, whether per-take warming reaches the same verifier instance, and how phrase length moves the wrong-phrase rate (3 of 10 playtest rounds were wrong phrases; phrases are now 8 to 11 words, mean 8.7, up from 6 to 10, mean 7.8).

## Answer endpointing for speculative scoring (2026-11-12)

`AnswerEndpointer` (`cf_worker/src/caller_stream.ts`) was tuned by simulating it on 20 ms frame energies of the car donation's eight phrase rounds and the 18-clip phone corpus. Car line floor about 27 dB, speech frames 45 to 65 dB; the corpus line is often digitally silent (0 dB), hence the 40 dB absolute level beside floor + 15 dB. Every phrase answer gave exactly one end. The longest pause inside an answer was 0.78 s, in one corpus clip whose first part held under a second of speech, so the 1 s minimum kept it from ending early. Against the donation's `heard_at` (with `speechTimeout="auto"`), the end came 2.2 to 2.8 s earlier in five rounds, about 0 in two, and 1 s later in one noisy round (which falls back to scoring at the stop). **Not shown:** head start under today's 1 s `speechTimeout` on a live call (read `head_start_ms`), and speakerphones whose prompt echo could end a false "answer" early (harmless: talking again restarts the score).

## Extension policy

Like `call-analysis`: a new kind of check starts as prose here, moves into `research_soundboard.py` after the same extraction is written twice or after a stumble, and a feature that adds a voice concept updates this checklist with `DOMAIN.md`. Record a dated finding when an experiment settles a question, and say what it does not show.

## Checklist (what to look for, and why)

- **Window, not clip:** the verifier judges one 3 s window. Check where it landed and how much speech it held before blaming the voice.
- **Condition before identity:** a low score from a genuine speaker usually means an unenrolled condition (car, earbuds, a new phone, codec damage). That calls for Re-enrollment, which Adam starts, never for adaptive learning.
- **Leakage:** IVR tone or prompt in the caller-only stream (speakerphones without echo cancellation are the risk). Words Parakeet hears from the prompt before the caller answers are the strongest evidence.
- **Impostor realism:** synthetic voices are easier to separate than people. Match impostors by age, gender, and accent, play them through a real telephone path, and include one clone of the enrolled speaker to show the ceiling honestly.
- **Optimism:** clips used to build a reference score high against it. Report held-out and enrolled scores separately.
- **Practice is not a sample:** a User practicing climbs toward a high level by adjusting their delivery, so practice scores run optimistic, and an impostor practicing would too. Treat `practice.round` cosines as per-condition anecdotes for that User, never as an impostor distribution.
- **Threshold changes:** propose them from the soundboard's distribution with held-out genuine and impostor clips in the same condition, never from one call.
