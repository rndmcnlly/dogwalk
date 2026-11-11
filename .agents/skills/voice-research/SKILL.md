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
- Challenge Practice rounds keep no audio, but each scored round leaves a `practice.round` Call Activity event (`outcome`, `cosine`, `speech_seconds`; wrong phrases and short speech too). Read them through the operator timing API (`/admin/api/timing?call_sid=...`) or `playtest.py`; they show how a real call scores in a real condition without a donation.
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
