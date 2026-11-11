# On-device Voice Frontend (Android prototype)

A small Android app that runs the whole Voice Frontend on the phone: on-device speech
recognition, a local Gemma 4 model for conversation and phrasing, and an offline
text-to-speech voice. Only text crosses the network, to the Session Orchestrator. The
Orchestrator and ACP Agents stay remote and unchanged. Proposal:
[#9](https://github.com/rndmcnlly/dogwalk/issues/9); research archive (do not merge):
[#10](https://github.com/rndmcnlly/dogwalk/pull/10).

This is a parallel path for callers with a recent smartphone, mainly to cut per-minute
GPT-Live cost. It does not change the ordinary-telephone path or the minimum equipment a
caller needs. Normal use runs in a microphone foreground service and keeps working with the
screen locked; the screen is only for one-time setup and diagnostics.

**Status: development prototype, tested only against a scripted fixture Orchestrator.**
Main's Worker has no route that accepts this app, so it cannot reach the real Orchestrator
yet (see Gaps).

## Pieces and provenance

| Function | What runs | Network fallback |
| --- | --- | --- |
| Speech recognition | `SpeechRecognizer.createOnDeviceSpeechRecognizer`, en-US, checked with `checkRecognitionSupport` (installed on-device language required) | None. Missing language pack: readiness fails and says so |
| Conversation and phrasing | [LiteRT-LM](https://github.com/google-ai-edge/LiteRT-LM) `com.google.ai.edge.litertlm:litertlm-android:0.19.1` (Apache-2.0), CPU by default, GPU optional | None |
| Model | `litert-community/gemma-4-E2B-it-litert-lm` @ `5b472bf4…`, `gemma-4-E2B-it.litertlm`, SHA-256 `181938105e0e…`, 2,588,147,712 bytes (Apache-2.0, ungated). E4B: `litert-community/gemma-4-E4B-it-litert-lm` @ `e75b1386…`, SHA-256 `0b2a8980ce15…`, 3,659,530,240 bytes | None |
| Speech output | Android TTS with a voice whose `isNetworkConnectionRequired()` is false and whose data is installed | None. No offline voice: readiness fails |

The pinned revisions and full hashes are in `LocalModel.kt` (`ModelSpec`). Laptop oMLX/MLX
Gemma checkpoints are a different format and were not used. The ML Kit Prompt API (AICore)
was not chosen: on a Pixel 10 it serves Gemini Nano (nano-v3), and Gemma 4 through AICore is
a Developer Preview needing enrollment. LiteRT-LM lets this app pin and verify the exact model.

Readiness happens in order, and each step is spoken when it fails: offline voice selected,
on-device recognizer installed, model file SHA-256 verified (hashed once, then a marker),
engine loaded, **one real warmup generation**, Orchestrator link opened. Only then does the app
say it is ready and start listening.

## Behaviour (code-owned rules)

`Frontend.kt` is Android-free and unit-tested. It encodes the archive's lessons:

- **Handoffs.** Caller input about sessions, work, permissions, cancelling, ending the call
  and so on is a Handoff by a deterministic guard before any model sees it. Otherwise the
  local model decides `HANDOFF` or `REPLY: …`. Anything malformed, or a reply that claims an
  action ("started", "cancelled", "done"), becomes a Handoff. The model can add Handoffs, never
  remove one. The spoken acknowledgement is fixed text ("Let me think about that.").
- **Transcript out, brief and say in.** The app sends `session.input_transcript.delta`,
  `session.output_transcript.delta` (only words the caller heard) and
  `session.delegation.created` (`target: client`, `offset_ms`), and receives
  `session.thinking.append` (brief), `session.commentary.append` (say) and
  `session.instructions.append`. These are the shapes main's `VoiceSession` already uses with
  GPT-Live. The app never writes engineering briefs and has no session tools.
- **Brief and Following** are kept separately (`Following …` thoughts replace the followed
  session's line; other briefs keep the latest six).
- **One speech request, one phrasing job, one utterance, one playback receipt.** Request IDs
  are bound by code. Each phrasing job sees only its own request plus context, under a stable
  per-task system prompt; heard history is quoted and separate from the current input.
- **Coverage guard.** Phrasing that drops a capitalised name, a number, a permission choice
  word (allow, deny, once, always, reject, approve), or the question mark of a question, or that comes back in the wrong task format, is replaced by the
  Orchestrator's own words (`VERBATIM`). This guard was added after real on-device E2B turned
  a live permission question into "Hello." (see Evidence). Passing the guard is necessary, not
  sufficient: it does not prove meaning was conveyed.
- **Interruption.** A local stop or barge-in stops playback at once. The receipt records only
  the heard prefix (from `onRangeStart`, approximate), and the request stays pending and is
  re-phrased later. Playback completion means the words played, not semantic fulfilment.
- **Stale work is discarded.** New caller input or a local stop advances an epoch; in-flight
  generations are cancelled (`Conversation.cancelProcess`) and their results ignored. The
  latest caller input is handled before queued speech.
- **Local stop is not cancellation.** "Stop talking", "quiet", "hush" (and bare "stop" while
  speech is playing or queued) stop speech locally, hold queued speech until the caller speaks
  again, and send no Handoff. Bare "stop" with nothing to stop is a Handoff, since it may mean
  the work.
- **No supersession.** No pending request is ever retired except by its own completed
  playback. FIFO can therefore speak stale progress or a withdrawn question. That is known and
  **not production-safe**; the remote contract carries no authoritative replacement IDs, and
  small-model supersession dropped live permission questions in the archived experiments.

## Running it

Toolchain: JDK 17, Android SDK platform 35 and build-tools 35, Gradle 8.11.1, reused from the
archived audio probe's bootstrapped `.tools` (this prototype never accepts SDK licenses).

```sh
export DOGWALK_ANDROID_TOOLS=/path/to/android_voice_probe/.tools
uv run --script build.py               # unit tests, assembleDebug, lintDebug
uv run --script fixture_orchestrator.py test   # fixture self-test
uv run --script fixture_orchestrator.py        # FIXTURE Orchestrator on 127.0.0.1:8770
adb reverse tcp:8770 tcp:8770          # phone or emulator reaches the fixture over USB/loopback
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

The development package is `as.adamsmith.dogwalk.ondevice.dev` and coexists with the
archived audio probe. Provision the model either with the app's pinned download button, or
with `adb push gemma-4-E2B-it.litertlm /sdcard/Android/data/as.adamsmith.dogwalk.ondevice.dev/files/models/`
followed by "Verify model file". Then grant the microphone and press Start voice, or:

```sh
adb shell am start -n as.adamsmith.dogwalk.ondevice.dev/dogwalk.ondevice.MainActivity \
  --ez start true --es endpoint ws://127.0.0.1:8770/ws
adb shell am start -n …/dogwalk.ondevice.MainActivity --ez selftest true      # model only, no audio
adb shell am start -n …/dogwalk.ondevice.MainActivity --ez stop_speaking true # local stop
adb logcat -s DogwalkFrontend:I
```

On the emulator, `emulator_speech.py "phrase"` synthesizes caller speech with macOS `say` and
streams it into the emulated microphone through the emulator's authenticated gRPC
`injectAudio` (start the emulator with `-grpc 8554 -grpc-use-token`). Debug builds also accept
`--es inject TEXT`, logged as `INJECTED caller text (not speech)`; it was not used for the
evidence below.

## Evidence (2026-11-10 to 11)

Android Emulator, API 35 `google_apis` arm64 image on an M3 Max, 6 GB guest RAM, CPU backend.
This is emulated Android, **not Pixel hardware**: latency is not phone latency.

- 12 JVM unit tests pass; lint has 0 errors. Fixture self-test passes.
- On-device model self-test, E2B CPU: SHA-256 verified on device, engine load 0.4–2.2 s,
  decide about 1.0–1.4 s, phrasing about 1.0–1.6 s per request.
- The first on-device run, with both tasks in one system prompt, phrased "Fir wants
  permission to delete the build folder… allow it once, or deny it?" as **"Hello."** and the
  test-result request also as "Hello.". Separate per-task system prompts fixed both outputs,
  and the coverage guard now catches that class of output (unit-tested). A later run phrased
  the same question as "Should I let it go through once, or stop it?": names kept, but "deny"
  became "stop it", which a caller could repeat back and be heard as cancelling. The guard now
  also requires permission choice words, so that output falls back to the Orchestrator's words.
- Readiness on the emulator: Google TTS voice `en-us-x-tpf-local` (offline), on-device en-US
  recognizer installed, warmup generation, fixture link.
- Speech exchanges, with synthetic `say` audio in the emulated microphone, on-device
  recognition, the local model, the fixture Orchestrator, and offline TTS playback receipts:
  - "hi there, how are you today": the local model replied ("I am doing well today."), no Handoff.
  - "what are the sessions doing": guard Handoff; the fixture received the cut transcript;
    the say was phrased locally and played to completion.
  - "please start the rebuild in fir": Handoff, then two say requests (starting, then a
    permission question) became two separate jobs; the permission question kept the alias
    and both choices.
  - "how are both sessions doing": two queued requests. A local stop mid-playback gave a real
    `onStop` receipt with heard prefix "Oak finished the test suite. All". The request stayed
    pending and held; after the caller spoke, it was re-phrased and completed, then the
    distinct staging-or-production question played.

## Gaps and limits

- **No route to the real Orchestrator.** Main's Worker accepts a Voice Frontend only as a
  GPT-Live sideband; the archive's `/dev/voice-frontend` route was not ported. Adding a
  route, plus a production identity and authentication for a non-PSTN client, needs an
  operator decision. Operator-admin credentials must never go in the app.
- **No authoritative supersession.** FIFO speaks every request, including obsolete ones.
- **Not tested on a Pixel.** No physical device was attached. The GPU backend, real
  microphones, locked-screen use and battery and thermal behaviour are untested.
- **Recognition defects seen:** "in fir" was heard as "infer" (aliases are not biased into
  recognition), and endpointing split "yes, allow it once" and "okay, go on" after the first
  word, and a final run heard "please start the rebuild" as "Please start". Half-duplex listening then lost the remainder while the acknowledgement played. Only
  "Yes" reached the Orchestrator, which is not enough on its own to answer a permission
  question.
- **Barge-in by voice** only runs while a headset output is connected (to avoid hearing the
  loudspeaker). Otherwise interruption is by the Stop speaking control; there is no headset
  media-button handling yet.
- **Unsupported, and said so:** recording, audio clips and earcons (mute/unmute and clip
  messages are logged as unsupported), acoustic browser pairing, the keypad menu, Theme voice
  switches, and status readout by key. Theme is plain.
- **E4B is selectable but unexercised.** Its pinned file was downloaded and its SHA-256
  matched, but at 3.66 GB it does not fit the emulator's 6 GB data partition (3.4 GB free), and
  growing the partition would mean changing or wiping that AVD.
- **Heard prefixes are approximate** (TTS range callbacks, not acoustic measurement).
- The coverage guard is lexical. It can reject a good paraphrase (then the Orchestrator's own
  wording plays) and accept a misleading one that keeps the names and numbers.
