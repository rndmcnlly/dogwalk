# Android local audio probe

A deliberately small **audio-plumbing experiment**, now extended with a local
text-model Voice Frontend spike. It tests whether an ordinary Android app can
listen, transcribe, and speak while the screen is locked, then exchange only text
and control events with an operator-run gateway. The ordinary telephone path is
preserved; this is a parallel rich-client experiment, not a new minimum interface.

## Text Voice Frontend spike

Blank gateway settings retain the original deterministic audio probe. Configuring
a gateway selects the experimental network mode: on-device ASR and TTS, a text
Voice Frontend on the laptop, and a separately configured Orchestrator attachment.
The app never receives provider or operator-admin credentials.

The [protocol](FRONTEND_PROTOCOL.md) is the boundary contract. The first validation
was local and undeployed; a separately authorized live phase is recorded below.
Fixture downstream responses do not create or inspect real Managed Sessions.
The existing Orchestrator's reasoning loop, prompt, tools,
Session Management, and ACP integration remain the downstream implementation.

From this directory, test the gateway without paid inference or real sessions:

```sh
uv run --script test_frontend_server.py
uv run --script frontend_server.py --fixture-orchestrator --fixture-model
```

For real text inference with explicitly fake downstream behavior:

```sh
set -a
source "$HOME/.tokens/openrouter-api"
set +a
uv run --script frontend_model_smoke.py --live-model
uv run --script frontend_server.py --fixture-orchestrator
```

The default endpoint is loopback-only, `127.0.0.1:8768`. Add
`--bind 127.0.0.1 --bind 100.85.3.93` to make it reachable over this laptop's
tailnet address as well. Open the gateway's home page for its private configuration
link. In the emulator the host is `10.0.2.2`; on the Pixel it is `100.85.3.93`.
Opening a configuration link populates settings but never starts recording.
The user must explicitly tap Start. Keep capability links and token files private.

In network mode, the gateway receives recognized text and playback/control events,
not microphone audio. Silence does not trigger model inference. Frontend token
usage, duration, and available cost are logged separately from Orchestrator/ACP
costs. Short-run hourly projections are not representative-hour measurements.
The default model is configurable; initial tests use `google/gemini-3.1-flash-lite`
with zero-retention routing required.

Local Stop and the exact spoken escape commands remain local. Interrupting speech
does not cancel an ACP Prompt Turn. Headset-only duplex protection is unchanged.

### Local Gemma through oMLX: a device-side surrogate

The gateway can use an on-laptop model instead of cloud frontend inference,
while the Orchestrator remains remote and unchanged. This approximates the
intended device/cloud responsibility split, not Pixel performance or power use.
[Google identifies Gemma 4 E2B/E4B as the foundation for Gemini Nano 4](https://developer.android.com/blog/posts/announcing-gemma-4-in-the-ai-core-developer-preview).
The open MLX checkpoints are not asserted to be identical to Google's optimized
AICore weights, adapters, safety behavior, quantization, or runtime policies.

Both models were downloaded through the existing oMLX model-management API and
validated by actual inference:

- `gemma-4-e2b-it-4bit`, from `mlx-community/gemma-4-e2b-it-4bit` (about 3.58 GB).
- `gemma-4-e4b-it-4bit`, from `mlx-community/gemma-4-e4b-it-4bit` (about 5.18 GB).

Both are instruction-tuned 4-bit affine checkpoints with group size 64. Other
models, service settings, defaults, and the OpenCode model picker were not
changed. Actual server IDs come from `/v1/models`, not guessed HF repo names.

```sh
set -a
source "$HOME/.tokens/omlx-api"
set +a
uv run --script frontend_model_smoke.py --live-model --provider omlx \
  --model gemma-4-e4b-it-4bit --max-calls 6 --reply-preview \
  --instructions-file .tools/canonical-frontend-instructions.txt
uv run --script frontend_server.py --fixture-orchestrator --provider omlx \
  --model gemma-4-e4b-it-4bit
```

Replace the model ID with E2B to compare it. The six local cases include the
original three cloud comparison cases, greeting, repeat/repair, and Following
context that distinguishes a failed step from a failed Prompt Turn. Native
playback and Breeze uptime fixtures also accept `--provider omlx --model ID`.

The oMLX API defaults to loopback `http://localhost:12348/v1`, requires its own
environment credential, advertises the selected model, and receives strict JSON
decision requests with thinking explicitly disabled per request. Invalid outputs
still fail application-side validation; successful schema samples are not proof
of hard server-side grammar enforcement. There is no cloud fallback or weakening
of the OpenRouter privacy policy. OpenRouter remains the default provider unless
the explicit local option is supplied.

Local usage has an explicit `local_no_api_billing` policy label. This means no
per-request API charge, not zero compute, power, hardware, or hosting cost.
The app still sends text/control to the laptop: the laptop stands in for an
eventual on-phone Frontend, and does not emulate AICore background restrictions.

#### E4B context engineering, experimental and offline only

`frontend_context.py` and `frontend_model_smoke.py --context-profile` compare:
`canonical` (the original prompt and JSON state), `roles` (canonical prompt with
actual conversation roles), `compact_json` (short plain prompt, JSON state), and
`compact` (short plain prompt plus conversation roles). These switches affect
the scenario harness only: the running gateway and production Orchestrator have
not been switched. Compact profiles replace, rather than faithfully compile,
the canonical prompt. They omit Park presentation, intensity, opening-specific
rules, and other canonical detail; do not treat this as feature parity.

The first E4B comparison reproduced 4/6 expected actions with canonical input.
Roles alone still missed the unknown-work question and encountered an invalid
response. A first compact prompt got 6/6 actions, but speech review caught internal
language and agency ambiguity. After explicit speech guidance, compact plus roles
again got 6/6 original actions and 10/12 additional actions. Compact plus JSON got
5/6 and 8/12. The additional cases were authored before those results were read;
they are now regression/development cases, not a permanently held-out test set.

Routing alone overstates success. Offline re-scoring the latter batch against
empty-text requirements for non-replies and selected speech-fidelity checks yields
**11/18 compact** versus **9/18 compact_json**. Compact failures include malformed
JSON after interruption, silence after thanks, nonempty Handoff text, contradictory
stale/current build status, and wording that makes the Frontend sound like the
permission decision-maker. The adapter already replaces Handoff text, so that
particular failure is guarded rather than necessarily audible. Speech regex checks
are necessary conditions, not a full semantic or human quality assessment.

Compact inputs were 786–844 reported tokens versus roughly 2,900 for canonical
input. Compact median latency was 445.5 ms over these 18 laptop calls, with mixed
cache state, not a phone benchmark. There were no cloud fallbacks, production
attachments, Android tests, or Orchestrator changes in this context experiment.

Retained evidence under `.tools/`: `e4b-context-2a1ae5a4/` (first comparison) and
`e4b-context-checks-6ba5b3bd/` (revised prompt, replies, timings and compact prompt
snapshot). The earlier `gemma-frontend-eval-f2c7f1b1/` baseline is untouched.
The smoke harness now reports routing separately from output-contract and supplied
speech-pattern checks, records prompt/scenario hashes, and counts malformed outputs
as failed cases without retrying or aborting the remaining cases.

```sh
uv run --script frontend_model_smoke.py --live-model --provider omlx \
  --model gemma-4-e4b-it-4bit --context-profile compact --max-calls 12 \
  --scenarios-file frontend_context_checks.json --reply-preview \
  --instructions-file .tools/canonical-frontend-instructions.txt
```

Current finding: reducing interpretation burden helps substantially, but a single
E4B decision-and-phrasing call is not yet sufficient. A useful next experiment is
to separate routing from phrasing and test each against its own fidelity criteria,
without moving engineering judgment out of the remote Orchestrator.

The subsequent [design bake-off](FRONTEND_BAKEOFF.md) compares mutable-prefix,
stable-tail, split routing/phrasing, and append-only ledger designs on E4B, with
reported KV cache reuse and matched exact-warm/cold-prefix probes. Its standalone
runner is `frontend_bakeoff.py`; it does not switch the gateway or touch production.

The [broader two-model evaluation](FRONTEND_EVAL.md) adds fresh intent/authorization,
coverage, grounding, uncertainty, privacy/injection, spoken-usability, and simulated
closed-loop repair/race criteria. `frontend_eval.py` compares the previous split
checkpoint with event-specialized split and single-call refinements on both E4B
and E2B. The completed round retains 270 event records and separate automatic versus
inspected findings. E4B priority split and E2B event single remain research candidates,
not deployed defaults: missed speech-request content is still a central blocker.

The next [per-request delivery prototype](FRONTEND_REQUESTS.md) tests individual
phrasing jobs and playback acknowledgements, separately recorded supersession,
and controls for batching versus FIFO versus model-assisted selection. It remains
a local synthetic experiment; playback completion does not certify semantic coverage.

### Evidence and limits of the text spike

- Android assembles and passes lint and host tests. Independent reviews covered
  stale replies, Stop/cleanup, token separation, and playback acknowledgements.
- `frontend_app_smoke.py` exercised the actual Android WebSocket client against
  an authenticated local fake server: local TTS playback, exactly one correlated
  completion, recorded sustained speech, and normal connection close after Stop.
  Artifacts are in ignored `.tools/native-network-smoke/`.
- The stitched `frontend_app_smoke.py --gateway-core --live-model` response lane
  also passed: actual text inference, native local TTS, and the gateway sending
  the output transcript upstream only after playback completion. One call with
  fixture instructions cost $0.00016125 and captured 3.48 seconds of interior
  speech. Its upstream is explicitly fake; no real Orchestrator was connected.
  This verifies the response lane, not microphone transcription through it.
- `frontend_model_smoke.py --live-model` passed three structured behavior cases
  on real `google/gemini-3.1-flash-lite` inference, with fake downstream context.
  The short fixture-prompt run used three requests at roughly one second each,
  costing $0.000469 in total according to provider usage. This is not the full
  canonical-prompt cost and not a connected-hour cost measurement.
- Repeating those cases with the actual 11,065-byte canonical Park frontend
  instructions used 2,797–2,825 input tokens per request. Action selection passed
  all three cases; inference took 0.947–1.353 seconds, with provider cost
  $0.00076075–$0.00077525 per call (total $0.002311). Manual reply inspection found
  an overspecific Handoff acknowledgement, so the gateway constrains these to
  neutral thinking acknowledgements rather than promising unconfirmed actions.
- The stitched native response-lane test also passed with those canonical
  instructions and a real model call ($0.0007625), capturing 3.48 seconds of
  interior Android speech. The test still has fake upstream context and no ASR
  input; it is not a production Orchestrator or complete acoustic-loop test.
- Silence and failed recognition without a transcript do not cause idle model
  calls. A no-transcript control event releases the floor so pending commentary
  is not permanently suppressed after a no-match.
- After a cold emulator restart, the original deterministic ASR path stalled
  before recognition-ready despite the installed SODA model initializing. This
  was not changed or hidden to make the initial network test pass. The subsequent
  Breeze uptime test resolved the test-ordering problem: inject only after a
  confirmed active guest microphone, but do not wait for ASR-ready before sending
  the first PCM. Readiness then arrived and actual transcription succeeded.
- At the initial local-spike checkpoint no production adapter was deployed and
  no real Managed Sessions were attached. These separate component tests do not
  establish a completed GPT-Live replacement.
- Local Worker adapter tests exercise the actual existing Orchestrator path,
  including attach initialization ordering and direct progress delivery without
  an additional Orchestrator call. The legacy Voice Session suite, 31 smoke
  checks, and TypeScript checks pass. Earlier intermittent legacy `i-relist`
  timeouts were not explained; three subsequent modified-Worker runs passed.
  The canonical prompts, tool schemas, model method, Orchestrator loop, and
  inner tool dispatcher were verified byte-identical to the checkout baseline.

### Separately authorized live phase

After the Breeze fixture task passed with actual recognition and real frontend
inference, the operator authorized reconciliation onto current main, deployment
of the guarded adapter, and a normal start of the existing stopped Sandbox.
The original spike worktree was preserved. A clean three-way application onto
`66e32fb` retained the newer operator/provider timing modules and cron; local
Worker, legacy Voice Session, smoke, type, Android, and gateway checks passed.

Version `47c7a8d6-f50e-459f-b373-44530241ebb3` was deployed at 2026-11-10 23:28:05
UTC with the development flag enabled. The existing Sandbox was confirmed
started at 23:28:57 UTC without replacement, stop/restart, changed auto-stop
settings, or the disruptive `deploy.py` refresh. The ordinary PSTN bindings,
model configuration, custom domain, and cron were preserved. Actual playtest
payloads and runtime configuration are kept outside the repository in approved
private temporary storage, not committed or served publicly.

The subsequent live Breeze task passed the full voice pipeline: actual Android
recognition of the original question, unchanged text forwarded through a real
Frontend Handoff, the existing deployed Orchestrator, a normally completed ACP
Agent turn in the real Sandbox, and Android speaking the returned answer. Four
frontend-model calls cost $0.00245625; Orchestrator and ACP costs are excluded.
An independent script-free Parakeet pass recovered both the question and the
spoken answer from the private 65.67-second review recording.

The answer's measurement provenance is narrower than its phrasing: the Agent
reported 171.14 seconds from `/proc/uptime`, and the Orchestrator described that
as Sandbox lifetime without checking PID 1 elapsed time or establishing the
relationship to provider start time. The audio pipeline passed, but container
lifetime acceptance is withheld. This was documented, not repaired by changing
the downstream Orchestrator. The requested Agent turn ended normally and the
development voice connection closed; the app's endpoint/token were reset blank.

The Worker attachment is `GET /dev/voice-frontend?phone=<URL-encoded E.164>`,
operator-admin authenticated and disabled unless `ENABLE_TEXT_FRONTEND_SPIKE`
equals `true`. Registration and Sandbox Assignment are resolved server-side.
The first upstream event is `dev.frontend.ready`; its `opening` is metadata, and
the following commentary append requests speaking it once. The adapter explicitly
rejects transport-specific media, recording, acoustic pairing, telephone recovery,
and Theme-bounce operations rather than pretending to perform them. The open
production identity/capability question is recorded in `DOMAIN.md`.

### Breeze-driven uptime task

`uptime_local_test.py` exercises actual Cone C Breeze speech, Android recognition,
the text frontend, a Handoff, and a spoken answer from explicit fixture data.
The fixture result is **not real sandbox uptime**. Two deterministic runs and a
real-frontend-model run passed without relaxing the app's watchdogs. The model
run forwarded the actual recognized question unchanged, emitted its Handoff
before any speech, and completed both the neutral acknowledgement and Android's
fixture answer. Its two provider calls cost $0.00152075.

```sh
uv run --script uptime_local_test.py --output .tools/uptime-local/new-fixture-run
uv run --script uptime_local_test.py --live-model --output .tools/uptime-local/new-model-run
```

Use a new output directory for each retained take. The live model requires the
environment setup above. The separate uptime fixture is generated by the existing
Breeze helper's custom-phrase mode; its manifest is
`.tools/uptime-fixtures/manifest.json`. The original five fixture takes remain
unchanged. Output includes `review.wav`, role/timing labels, actual app and gateway
transcripts, model decisions, raw Android audio, and assertion results.

An independent script-free Parakeet v3 pass over
`.tools/uptime-local/live-model-001/review.wav` recovered the question, "Let me
think," and the explicit simulated uptime answer. Raw timestamp evidence is in
that take's `asr/` directory. This is now a real acoustic input/output test of the
new frontend, while downstream sandbox behavior remains a labeled fixture.

To repeat full-prompt inference measurements after generating the canonical
instructions artifact with the local Worker adapter test:

```sh
uv run --script frontend_model_smoke.py --live-model \
  --instructions-file .tools/canonical-frontend-instructions.txt --reply-preview
uv run --script frontend_app_smoke.py --gateway-core --live-model \
  --instructions-file .tools/canonical-frontend-instructions.txt
```

These remain bounded synthetic cases, not hourly cost evidence. As an arithmetic
illustration only, 100 calls at $0.00077 each cost about $0.077. Model calls are not
the same as user turns: a Handoff and later speech request can require two calls,
and progress narration adds more. Longer conversation context, transport/hosting,
and representative activity rates require separate measurement. Orchestrator and
ACP costs are deliberately outside this comparison.

## What runs where

In the original deterministic probe mode, everything below runs on the phone:

1. A microphone/media-playback foreground service holds audio focus and a partial
   wake lock for an explicitly started test (maximum two hours).
2. `SpeechRecognizer.createOnDeviceSpeechRecognizer()` recognizes US English.
   There is **no fallback** to the potentially cloud-backed default recognizer.
3. A deterministic text policy returns an echo or a canned test reply.
4. Android `TextToSpeech` speaks using an installed English voice whose
   `isNetworkConnectionRequired()` is false.

This bypasses AICore and ML Kit GenAI. It does not establish whether those newer
APIs work in the background. It establishes a baseline using Android's existing
public speech services. It also does not establish that system-service recognition
honors the requested communication route: the requested route and actual behavior
must be compared on the phone.

The app now declares `INTERNET` for optional text gateway access. Blank-endpoint
probe mode makes no gateway connection. Speech engines are separate system apps
with their own permissions, so **test deterministic mode with internet disabled**
rather than treating our manifest as evidence about those apps. Download
recognition and synthesis language data before that test.

Deterministic mode has no LLM, phone calls, ACP integration, or engineering
authority. The optional gateway adds conversational behavior, Handoffs, and speech
requests through text/control events. It must not be confused with a completed
production replacement of GPT-Live: transport attachment and actual downstream
integration are verified separately.

## Build

Requires Android 12/API 31 or newer on the phone. Pixel 10 Pro is a suitable
target. The app is Java with OkHttp for optional WebSocket transport; the UI, service,
logging, and test-response policy live in `MainActivity.java`.

### Android Studio

Open this directory as a Gradle project. Use JDK 17, Android SDK platform 35,
and build tools 35.0.0. Build/run the `app` debug variant. Studio can provide the
SDK and Gradle configuration.

### Isolated command-line build

From this directory:

```sh
uv run --script build.py --bootstrap --accept-sdk-licenses
```

First run downloads JDK 17, Gradle 8.11.1, Android command-line tools, platform
35, build tools, and adb into ignored `.tools/` (hundreds of MB). The explicit
`--accept-sdk-licenses` flag accepts Google's SDK licenses. Omit that flag to
download the toolchain without accepting licenses: missing SDK packages will
then block the build. The script never implicitly accepts them. Existing
`ANDROID_HOME`/`ANDROID_SDK_ROOT` is respected;
otherwise no system-wide installation is changed. Gradle caches also stay under
`.tools/`. Bootstrap downloads require internet; the app's runtime does not.

After setup:

```sh
uv run --script build.py
```

This assembles a debug APK, runs Android lint, ten host-side tests of the exact
deterministic response policy embedded in the app, and four log-summary tests.
It **does not** run instrumentation tests or validate real audio hardware.
The host tests alone:

```sh
uv run --script build.py --test
```

APK: `app/build/outputs/apk/debug/app-debug.apk`.

Connect an authorized USB-debugging phone, then install with:

```sh
uv run --script build.py --install
```

Installation does not launch the microphone. Open **Dogwalk Audio Probe** on the
phone and explicitly start it. Android may require allowing installation or USB
debugging first. Emulator tests do not establish physical headset behavior,
real-device power management, or credential-secured lock/unlock behavior.

### What an emulator can establish

The included helper uses an isolated API 35 Google APIs ARM64 virtual phone,
`dogwalk_api35_arm64`, on serial `emulator-5580`. From this directory:

```sh
uv run --script emulator.py setup --accept-sdk-licenses
uv run --script emulator.py start --headless
```

The second command stays running. In another terminal, run:

```sh
uv run --script emulator.py smoke
```

The smoke command installs the existing debug APK, grants runtime permissions,
taps Start through the UI, checks for ready/unsupported outcomes and clean service
shutdown, and captures UI XML, a screenshot, event logs, and crash diagnostics in
`.tools/smoke/`. It never starts the microphone service directly from an adb shell.
Stop the virtual phone with `uv run --script emulator.py stop`. Omit `--headless`
when starting if you want a visible emulator window. Do not run two copies of
this AVD at once.

**Initial observation:** installation/UI/startup/error cleanup passed, but
recognition returned `LANGUAGE_UNAVAILABLE`. The initial smoke report correctly
marked speech unsupported. After installing the correct model as described below,
the probe reaches `listening ready`. Readiness alone is not an acoustic round trip.

#### Downloading recognition data on this emulator image

The on-device engine is Android System Intelligence:
`com.google.android.as/com.google.android.apps.miphone.aiai.app.AiAiSpeechRecognitionService`.
It does **not** use the language pack downloaded by Google TTS's
`com.google.recognition.action.DOWNLOAD_LANGUAGE` intent or the engine identified
by the secure `voice_recognition_service` setting.

On this debuggable emulator only, open its non-exported language-pack settings:

```sh
.tools/sdk/platform-tools/adb -s emulator-5580 shell su 0 am start \
  -n com.google.android.as/com.google.android.apps.miphone.aiai.speech.languagepacks.settings.ui.SettingsActivity
```

In the settings UI choose **Add a language → English (US) → DOWNLOAD**. This
downloaded SODA `en-US_v3056` (63 MB) and resolved `LANGUAGE_UNAVAILABLE` in the
existing probe, without app-source changes or a reboot. The headless UI can be
operated with adb/UI XML, or start the emulator with a window for manual setup.
This root-only diagnostic route is **not** a proposal for provisioning real phones.

Android Emulator on Apple Silicon uses an ARM64 system image. It is useful for
installation, UI, permission prompts, lifecycle diagnostics, and checking that
missing speech capabilities fail clearly rather than falling back to a server.
An emulator image may not include an on-device recognition service or an installed
offline TTS voice, even if it includes Google APIs. An unsupported-engine result
is a limitation of that image, not evidence that the Pixel cannot run the probe.

#### Direct file-based audio, without Mac microphone/speaker routing

`emulator.py start` enables bearer-authenticated gRPC only on `127.0.0.1:8554`
and uses the timer-only audio backend (`-audio none`). Direct PCM injection and
capture work with that backend: macOS microphone permission, physical speakers,
and audio loopback devices are not prerequisites. Credentials and generated
protobuf bindings stay under ignored `.tools/`; credentials are not printed.
Do not expose the emulator control port to a network.

Generate a reproducible local speech fixture with macOS `say` and `ffmpeg`:

```sh
mkdir -p .tools
say -o .tools/probe-status.aiff 'probe status'
ffmpeg -hide_banner -loglevel error -y -i .tools/probe-status.aiff \
  -af 'adelay=500,apad=pad_dur=1' -ar 16000 -ac 1 -c:a pcm_s16le \
  .tools/probe-status.wav
```

With the emulator running, app installed, and both English speech models ready:

```sh
uv run --script audio_bridge.py status
uv run --script audio_bridge_smoke.py .tools/probe-status.wav
uv run --script test_audio_bridge.py
```

The bridge's **seven host-side unit tests** cover PCM/WAV validation, sample
statistics, truncation, and the active-microphone guard. They are separate from
`build.py`'s policy/summary tests and do not need an emulator.

The audio smoke starts the existing app through its UI for each fresh recognition
window, first awake and then screen-off. It temporarily enables swipe keyguard,
asserts keyguard showing, display off, and `dumpsys power` wakefulness `Asleep`
**before injecting**, then restores the original keyguard-disabled setting in
`finally`. It never sets or changes a PIN/password. This is a swipe-keyguard
lifecycle test, **not** PIN/unlock-security validation. Missing speech models
produce a startup failure rather than a claimed pass.

**Observed end-to-end evidence:** both awake and keyguard-showing runs recognized
the injected WAV as final transcript `Probe status`, completed the offline TTS
reply (“The local audio probe is running. There is no model or remote backend
connected.”), and captured substantial nonzero output signal. This is stronger
than recognizer readiness or merely producing a WAV header. Evidence is retained
in `.tools/audio-bridge-smoke/result.json`, `awake.wav`, `screen-off.wav`, per-run
event logs, `screen-off-keyguard.txt`, and `screen-off-power.txt`.

#### Reviewable interaction montage

With the validated Breeze fixture manifest available:

```sh
uv run --script interaction_test.py .tools/voice-fixtures/manifest.json --include-stop
uv run --with numpy python -m unittest interaction_helpers_test.py
```

Open [the generated local review](.tools/interaction-review/review.html). Breeze
uses Adam's previously approved **Cone C** identity as a simulated human, a role
assigned by the test agent, not Adam. Android replies use the actual installed
offline system voice. The intro is review-only and is never injected. The stereo
review places Breeze slightly left and Android slightly right, without pitch
shifts or reply dubbing; `android-only.wav` retains the captured Android track.

Status, echo, long reply, optional spoken stop, and locked status are independently
restarted turns, **not an uninterrupted conversation**. One-second montage gaps
are edited; packet timing within each turn retains idle gaps and contiguous PCM.
`timeline.json` records timing/provenance and `result.json` records assertions.
Swipe keyguard is temporarily enabled and its original disabled setting restored.
Fresh readiness plus an active guest microphone is required before each injection;
the emulator's microphone-close race cannot be eliminated. Without `--include-stop`,
spoken stop is explicitly skipped. Headphone-only duplex protection is unchanged;
emulator interruption is unsupported without a real headset. Existing run outputs
are never overwritten: preserve them or use a fresh `--output` directory.

Optional independent word timing:

```sh
uv run --script parakeet_analysis.py transcribe .tools/interaction-review/review.wav --out .tools/interaction-review/asr
uv run --script interaction_test.py --attach-analysis .tools/interaction-review/asr
```

Parakeet receives no reference text and does not identify speakers. Its calibration
missed isolated Android chimes: absent words establish neither silence nor absent
chimes. Word times are approximate, distinct from audio-event candidates and
Android TTS callback intervals. Repeated calibrated speech boundaries varied by
up to 180 ms; this is an observed variation, not a guaranteed error bound.

**Validated review:** all five turns passed, including spoken stop without a reply
and locked/asleep status. The 74.55-second review, raw captures, and hashes are in
`.tools/interaction-review/`. Mixed and Android-only Parakeet transcripts omit
different audible stretches, so neither is an exact output-word oracle. Four
exposed-earcon template candidates were independently measured on this run at the
frozen 0.85 threshold; their review-time onsets and source hashes are recorded
separately in `timeline.json`. This detector misses speech-overlapped chimes and
does not prove which Android component generated a sound.

For individual operations, tap **Start local audio** and wait for fresh
`listening ready` immediately before injecting. `exchange` starts output capture
concurrently with input; separate sequential `inject` then `capture` may miss
the beginning of a reply:

```sh
uv run --script audio_bridge.py exchange .tools/probe-status.wav .tools/reply.wav \
  --seconds 18 --require-signal
uv run --script audio_bridge.py inject .tools/probe-status.wav --timeout 10
uv run --script audio_bridge.py capture .tools/output.wav --seconds 10 --require-signal
```

Input is uncompressed PCM WAV, mono/stereo, unsigned 8-bit or signed little-endian
16-bit at 8–48 kHz, with no implicit conversion. Capture is 48 kHz 16-bit stereo.
Input/capture duration is bounded to 120 seconds, and RPC deadlines bound silent
streams. Capture contains guest output, not injected microphone audio unless the
guest plays it back. The stream can omit idle periods, so the captured WAV's
duration need not equal the requested wall-clock capture window.

**Emulator 37.2.13 limitation:** `injectAudio` can crash the emulator natively when
no guest input voice is open. The helper checks AudioFlinger for active recording
before calling it and refuses the idle case. This cannot eliminate a race if the
guest closes its microphone after the check. Prefer the smoke's fresh Start per
fixture rather than injecting near a recognition timeout. Disabling VirtioSndCard
is not a workaround: this ARM64 image lacks the alternate HDA driver.

These tests still do not reproduce real-phone power management, Bluetooth
microphone routing, acoustic echo/barge-in, thermal or battery behavior, or prove
network isolation of the system speech engines. Those remain physical-device
tests, including an internet-disabled run after model provisioning.

## First experiment: locked-screen round trip

1. Download English recognition and offline synthesis language data in Android's
   speech settings. Open the app and grant microphone permission. Bluetooth and
   notification permissions are useful but optional.
2. Connect earbuds. Leave **Listen during replies** off initially. Choose
   **Prefer headset**, then **Start local audio** while the app is visible.
3. Wait for `offline_voice` and `listening ready` in the log. Speak a sentence.
   The app should answer “I heard: …”. Silence timeouts are expected between turns.
4. Lock the screen. Repeat a sentence, wait quietly for a minute, then try again.
   Say **probe status** to get an unmistakable fixed reply.
5. Repeat with Wi-Fi and mobile data disabled. Keep Bluetooth enabled if using
   Bluetooth earbuds. Airplane mode may require re-enabling Bluetooth.
6. Say **stop listening** or **stop the probe** to end the test. The notification's
   **Stop** action and the app's Stop button are independent escape hatches.

In the default half-duplex mode the microphone recognizer is paused during TTS.
Voice commands therefore cannot interrupt a reply in that mode. This avoids
turning speaker echo into an endless conversation with itself.

## Second experiment: interruption

Stop, enable **Listen during replies**, and Start again with earbuds connected.
Say **long reply**, or tap **Speak long test reply**. Speak a different sentence
while it is talking. The recognizer's `onBeginningOfSpeech` should cancel TTS and
the eventual final transcript should receive a new reply.

Duplex refuses the speaker route and waits for a confirmed headset route. Once a
headset route has been established, losing it stops the probe rather than
silently sending your transcript to a speaker. Bluetooth transitions may trigger
conservative false stops; record those as part of the experiment and Start again.

This is **experimental barge-in**, not full-duplex conversational intelligence.
There is no independent voice activity detector or custom acoustic echo
canceller. Noise or leaked synthesized speech can cause false interruptions.
Do not enable this mode on speakerphone as a meaningful success test. The probe
does not add a second microphone recorder: that would contend with the system
recognizer and confound the experiment.

`tts_range` logs character ranges reported by the engine when available. They
are useful for exploring interrupted playback, not proof of exactly which words
reached the listener's ears.

## Walk-test checklist

Compare half-duplex and experimental interruption under the same conditions:

- Screen on, then locked in a pocket for 20–30 minutes.
- Earbuds, then built-in speaker (half-duplex).
- No internet after downloading the necessary speech data.
- A few seconds of silence, then several minutes of silence, then speech.
- Disconnect earbuds while running. The probe should stop, not fall back to the
  speaker. Reconnect and explicitly Start again.
- Play competing audio or take a real phone call. The probe should pause on
  transient focus loss and stop on permanent focus loss. It must not fight the call.
- Stop the probe, then check another app can use the microphone normally.

Record whether input really came from the headset: move the phone away and speak
quietly into the headset microphone. An `accepted=true` route request alone does
not prove the external recognition service used that microphone.

**Passing evidence:** useful recognition and audible replies while locked and
offline, tolerable turn gaps, interruption that responds to the human rather than
its own voice, sensible audio-focus handling, and acceptable battery/heat.

**A valid negative result:** the recognizer requires repeated reopening, has
unacceptable gaps, refuses the background microphone, uses the wrong route, or
rate-limits the loop. Android documents `SpeechRecognizer` as not intended for
continuous recognition. We intentionally test that limitation rather than hide
it. Such a result argues for app-owned streaming ASR/VAD (for example sherpa-onnx),
not against Android's ability to sustain microphone capture like Signal.

## Diagnostics and privacy

The UI is a diagnostic control panel; it is not a proposed Dogwalk interface.
After initial setup, the core test works without looking at it.

**Export diagnostic log** saves JSON Lines through Android's document picker.
Logs include transcripts, synthesized text, device/route names, screen events,
recognizer errors, TTS request-to-start time, first-partial latency, battery
percentage/current, and thermal status. These latency values measure callback
timing, not an external end-to-end acoustic benchmark. Battery current is noisy
and device-dependent; use long comparative runs, not a single sample.

Logs live in private app storage and are excluded from Android backup. No raw
audio is retained by this app. Logs append across runs and truncate after 8 MiB;
export after each experiment if you need complete results. Clearing app storage
or uninstalling deletes the private log. Exported copies persist where you save
them. **Do not commit exported logs or publish transcripts without review.**

To summarize an exported log without printing its transcripts:

```sh
uv run --script summarize.py /path/to/dogwalk-audio.jsonl
uv run python -m unittest test_summarize.py
```

The summary counts final transcripts observed while screen-off or sampled locked,
recognizer failures, and median callback latencies. It cannot establish which
microphone was actually used, whether audio was intelligible, or whether the
system speech service used the network. Those require the physical tests above.

Recovery intentionally favors legibility over production behavior: unsupported
recognition/voices fail closed; repeated unexpected recognition errors stop the
probe rather than retry forever. The service does not automatically restart after
process death or reboot. Notification dismissal behavior and device battery
policies are themselves things to test, not silently bypass.

Watchdogs stop failed startup after 30 seconds, recognizer readiness/final-result
waits after 15 seconds, long utterances after two minutes, and TTS startup or
completion after 15 seconds/two minutes respectively. Idle recognition without
an endpoint is restarted after 45 seconds, with bounded retries. Old recognizer
callbacks are isolated by generation, so cancellation cannot accidentally finish
a newer attempt. Route/mode change listeners log OS decisions rather than
continually forcing the requested communication mode back on.

## Google references

- [On-device recognizer API](https://developer.android.com/reference/android/speech/SpeechRecognizer)
- [Offline voice properties](https://developer.android.com/reference/android/speech/tts/Voice)
- [Microphone foreground services](https://developer.android.com/develop/background-work/services/fgs/service-types#microphone)
- [Android audio input sharing](https://developer.android.com/media/platform/sharing-audio-input)
- [ML Kit streaming speech recognition](https://developers.google.com/ml-kit/genai/speech-recognition/android)
- [ML Kit generative speech synthesis](https://developers.google.com/ml-kit/genai/text-to-speech/android)
- [ML Kit background-use policy](https://developers.google.com/ml-kit/genai#background_usage)
- [Gemini Nano custom prompts](https://developers.google.com/ml-kit/genai/prompt/android)
- [Google Android AI samples](https://github.com/android/ai-samples)
