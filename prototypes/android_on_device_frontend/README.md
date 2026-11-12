# On-device Voice Frontend (Android prototype)

**Resume here:** [2026-11-11 checkpoint](CHECKPOINT.md) records the successful
physical-phone permission/file-write trial, shutdown, preserved branches and
private local recovery assets.

A small Android app that runs the whole Voice Frontend on the phone: on-device speech
recognition, a local Gemma 4 model for conversation and phrasing, and an offline
text-to-speech voice. Only text crosses the network, to the Session Orchestrator. The
Orchestrator and ACP Agents stay remote and unchanged. Proposal:
[#9](https://github.com/rndmcnlly/dogwalk/issues/9); research archive (do not merge):
[#10](https://github.com/rndmcnlly/dogwalk/pull/10).

This is a parallel path for callers with a recent smartphone, mainly to cut per-minute
GPT-Live cost. It does not change the ordinary-telephone path or the minimum equipment a
caller needs. Explicit voice use runs in a microphone foreground service and keeps working
with the screen locked. Optional silent text chat uses the screen and a non-microphone service.

**Status: Adam-only authenticated real-Orchestrator adapter implemented locally, not deployed.**
Fixture mode remains available. No production credential, deployed WSS session, real Sandbox
trial or physical-phone verification is claimed. See the [operator runbook](../../cf_worker/README.md#adam-only-on-device-trial-not-deployed-by-this-change).

## Approved side-channel: local Worker and local ACP, not production

The current trial does **not** deploy this branch or touch production D1, Daytona,
the production Sandbox, the shared OpenCode service, or Tailscale configuration.
`local_trial.py` starts a private loopback Worker with a fresh local D1/DO store and
the existing ACP Gateway running real `opencode acp` in a disposable macOS directory.
The real Session Orchestrator and ACP Agent both use `openai/gpt-6.1-sol`.

```sh
uv run --script local_trial.py start
# Prints only process/port/path metadata. Keep its returned STATE directory.
uv run --script local_trial.py prove --state STATE
uv run --script local_trial.py stop --state STATE
```

`start` reads only `OPENAI_API_KEY` from the explicitly authorized
`/Users/adam/Desktop/dogwalk/.env` (override with `--provider-env`). It never sources that
file, prints a credential, or passes a secret as a command argument. It creates one
32-byte app token at `STATE/app-token`, provider configuration and evidence as 0600
files, under a 0700 private temporary directory. The workspace is `STATE/workspace`;
synthetic Registration `+15555550199` and a `provider='local'` assignment exist only in
the generated local schema. No production database or Managed Session is copied.

The generated Wrangler config has no production resource IDs, routes, cron, or remote
bindings. All D1 commands explicitly use `--local` and `STATE/persist`; secret discovery
is confined to the generated `STATE/cf/.dev.vars`. The dedicated local entrypoint accepts
only `/app/frontend` and `/app/heartbeat`; admin, PSTN, browser and other paths return
404. The provider adapter is enabled only when `DEPLOYMENT_ENVIRONMENT=local-app-trial`
and `LOCAL_APP_TRIAL_ENABLED=true`, with fixed `LOCAL_APP_TRIAL_ACP_URL` on IPv4 loopback
and `LOCAL_APP_TRIAL_WORKSPACE` under the private disposable trial directory. Invalid
configuration, including `production`, fails closed rather than falling through to
Daytona. The production Wrangler configuration is unchanged.

Default ports are Worker **18781** and Gateway **18782**, both bound to `127.0.0.1`.
Busy ports fail rather than taking over a service; 8770 is refused. No fake hosting API
pretends to be Daytona. Hosting mutation, audio, browser, discovery/load and invitation
tools are unsupported in this local mode. Create Managed Session accepts only directory
`.` (or the exact configured root), and the Orchestrator is explicitly told it is local,
disposable and not a security sandbox. Existing session tools and ACP permissions remain
real. OpenCode ACP starts its documented private server, using isolated HOME/XDG paths
and database, not the shared service. V2 permissions deny shell, external paths, network
tools, Code Mode and subagents; reads within the workspace are allowed, and only
`trial-marker.txt` and `permission-marker.txt` may be edited, with `ask` permission.
Configuration/instructions cannot be edited. These application permissions are **not an
OS sandbox**: the macOS process still runs as the operator. Use only harmless tasks.

`prove` sends actual typed Handoffs, never scripted model replies. A real model creates
Fir, starts a real ACP Prompt Turn, presents a real three-choice Permission Request,
and receives an explicit User Handoff selecting **Allow once**. The Agent writes and
reads back the harmless marker; the harness checks its exact bytes and a real
`acp.turn.stopped` event (`end_turn`). It disconnects and reconnects, asks for retained
status without replaying the task, and saves private wire/telemetry evidence. A failed
or completed proof is not automatically retried on the same state. The first development
attempt correctly reached a real permission but watched the wrong telemetry table and
timed out; the corrected fresh-state proof completed successfully. No permissions were
weakened to make it pass.

For a phone trial, the parent/operator separately exposes an unused tailnet TLS port,
for example `tailscale serve --bg --https=8443 http://127.0.0.1:18781`. This script never
runs that command. Use `wss://<Mac-tailnet-DNS>:8443/app/frontend` in Android's **Real
Orchestrator** mode and manually enter the token from the private file. The existing v5
APK needs no protocol or TLS changes. Tailscale terminates TLS and proxies only to the
loopback Worker; the local entrypoint marks that trusted internal hop as HTTPS for the
unchanged app authorization check. The app still requires WSS with normal certificate
validation. Do not expose the ACP port or use Funnel. Local token authority is solely
this synthetic trial, not production identity. Existing ready/ack/unknown, revocation
lease, explicit reconnect and no-replay semantics are unchanged.

Leave the supervisor running for the parent handoff. Its process IDs and exact paths are
in `STATE/runtime.json`; `stop` validates the supervisor's state argument, terminates
only its child process groups, waits for its own ports to close, and preserves private
evidence. Removing a tailnet Serve route is a separate operator action.

## Adam-only trial (0.5, versionCode 5)

This update preserves native UI v4, package/signing, endpoint/backend/model settings,
existing weights and verification markers, local Gemma prompts and inference, silent
text default and explicit voice enable. Install as an update, never uninstall to resolve
a signing conflict. **Nothing has been deployed or installed on a phone by this change.**

Setup now has an explicit **Real Orchestrator · authenticated WSS** switch (off means
unauthenticated fixture transport). Real mode requires `wss://<reviewed-host>/app/frontend`
and the separately provisioned app bearer token. Paste it into **Replace app credential**
and Save. Blank keeps the saved value; Clear removes it locally. It is never read back
into a widget, intent, timeline, diagnostic, log or backup. Built-in Android Keystore
AES-GCM encrypts a private nonbackup file, authenticated against the exact endpoint.
Changing endpoint requires re-entering the token. Fixture mode never loads/sends it,
and real mode refuses cleartext, URL credentials, query strings, fragments and redirects.
Do not use admin, provider, inference or hosting keys as the app credential.

Start text explicitly. Link waits for `frontend.ready`, not just the WebSocket upgrade.
It reports **frontend accepted** separately from **Agent connecting/connected/unavailable**.
You may converse while the Agent starts; session tools that need it fail fast until ready.
Permission answers remain Handoffs through the existing Orchestrator and ACP path, not
buttons or receipt metadata that authorize tools. Brief and Following stay background.

Pending and accepted network events appear in diagnostics. **Server acceptance is not
execution, completion, display, or authorization.** A disconnect reports unacknowledged
events as unknown and never resends them. Retry is explicit: inspect existing work before
repeating consequential input. Old remote requests are discarded on detach, so replacement
connections never replay stale speech; work and Permission Requests remain server-side.
Each Say, including unsolicited ones, binds to its own server request ID for display or
local-playback receipts. Recognized/typed/displayed/played provenance reaches the real
Orchestrator. There is no exactly-once promise across process/network crashes and no
durable speech inbox. FIFO within an attachment still has no supersession metadata.

The app renews its server lease with an authenticated HTTPS heartbeat every 20 seconds.
Rotation/disable prevents new attachments and heartbeats; existing sockets detach within
60 seconds of their last successful renewal, subject to deployment propagation/platform
scheduling. Clearing the local saved credential does not revoke a copy elsewhere or stop
an already-running frontend. Stop it separately. App/PSTN competition fails with HTTP 409;
neither transport steals the other's active attachment. Disconnect never cancels work,
closes Managed Sessions, deletes history, or stops the existing Sandbox.

Local verification for v5: 33 JVM tests and debug APK build pass; lint has 0 errors and
17 warnings (English-only UI copy and conservative free-space checking). The Worker has a
new Node fake-socket integration test exercising its actual Orchestrator loop, session
tools and ACP Permission Request/response, plus auth/owner binding, dedupe, provenance,
lease expiry, concurrent frontend exclusion, startup order, stale output rejection and
work after detach. The final stable-source run passes the normal smoke, PSTN session,
and Gateway suites. Several runs made while watched Worker sources were still being edited timed out at
different points (invitations, a completion Say, and pairing), with mock sockets closing
without a close frame. Verification must hold those sources fixed while Wrangler runs.
Both v4 and v5 APKs verify with the same v2 signing certificate (fingerprint below).

### Narrow runtime followup (2026-11-11)

Three instrumentation tests pass on the existing API 35 ARM64 AVD, explicitly selected
as `emulator-5562`, launched with `-no-audio -no-window` and without wiping or downloading
models. `TransportRuntimeTest.kt` runs only from the separate test APK:

- Actual Android Keystore encryption, save/load through a new store instance, endpoint
  binding (wrong port, cleartext and query URL fail), no plaintext in its private
  nonbackup ciphertext, and clear. Test storage is an isolated child directory, not the
  app's saved credential. No production token was generated or used.
- Actual `OrchestratorLink` connecting to an ephemeral IPv4-loopback HTTPS/WSS server
  inside the emulator. Shipping-default TLS rejects the self-signed test certificate.
  Only the injected test client trusts the test CA, compiled exclusively in the test APK:
  no CA installation, production trust override or network-security resource changes.
- Socket-open is not ready; ready with `agent_ready:false` reports Agent connecting.
  Real input envelopes and ID acknowledgments, authenticated HTTP heartbeat, unsolicited
  Say source binding, text-display receipt, and unknown unacknowledged delivery at
  disconnect all traverse real sockets. Fixture transport remains raw and sends no auth.

Reproduce with the toolchain/signing environment below and an explicitly selected,
already-running silent emulator: `uv run --script runtime_test.py --serial emulator-5562`.
The helper checks emulator identity, builds/updates without uninstalling, and treats
instrumentation's textual test result as authoritative (adb alone can exit zero on test
failure). No microphone, ASR or TTS is constructed by these tests. An initial harness
attempt bound IPv6 loopback while the certificate/debug cleartext allowlist expected
IPv4; explicit `127.0.0.1` binding corrected it, and all three tests then passed.

The existing silent UI smoke also passes against isolated fixture port **18776** with
the existing E2B model: actual typed greeting/inference, work Handoff, two independent
replies, permission question and typed answer, disconnect and explicit Retry. No voice
or injection logs appeared; microphone permission stayed denied. Existing model bytes,
verification marker and first-install time survived the signing-compatible update.

These are local emulator/client-boundary proofs, not a deployed Worker/Daytona trial.
Physical-phone behavior, locked-screen heartbeat/audio, real hosting and a production
WSS-to-ACP exchange remain untested. Earlier v4 emulator evidence below is historical.

## Native conversation UI (0.4, versionCode 4)

The approved `design/chat-redesign.html` is implemented with native widgets, not a WebView.
The pale surface, blue actions, compact Dogwalk/Development header, independent Link/Local
strip, mode strip, single chronological timeline, pinned composer and modal Setup map to
that design. Preview keyboards, scenario selectors and sample history are not app features.

1. Open the app, then **Start text**. Opening/restoring the Activity always silences voice.
   Installed/not loaded is not ready: verification, engine load and a real warmup precede
   model readiness. Link connecting/connected is independently reported.
2. Type in **Message Dogwalk**, then **Send** or the IME Send action. Draft stays editable
   while blocked. There is no automatic resend on reconnect.
3. **Menu** opens modal **Setup and tools**: endpoint, pinned model, backend,
   download/verify, no-audio self-test and Stop frontend. **Done** or Back returns to chat.
   Save applies on the next start, not to a running engine. Stop before changing backend/model.
4. **Enable voice** requires explicit confirmation and microphone permission. After granting
   permission, confirm again. Listening/speaking labels use recognizer/TTS callbacks; local
   inference shows Thinking. **Stop speaking** is local interruption, not cancellation of work.
   **Switch to text** closes audio. A dropped link alone does not promise audio has stopped.
5. Expand a diagnostic in place. New activity follows only when near the end; otherwise use
   **Latest**. Conversation and diagnostics share one scroller, never a second log.

Presentation is typed (`Presentation.kt`), emitted at source, ordered by phone observation
sequence and labeled with locally recorded time, not invented provider time. Background
Brief/Following context produces a sanitized diagnostic only. Each request produces its own
assistant item and receipt. A display receipt is withheld when the Activity is paused,
unfocused or covered by a modal. Closing Setup resumes pending presentation. Displayed
does not mean spoken, read, understood or authorized. Raw logs are not timeline data.

The timeline is bounded to 300 process-local events. No conversation database or disk history
is added. Draft survives Activity recreation and transient layout changes (saved instance
state plus process-local fallback); process death/history persistence is not guaranteed.
Download progress is visible in Setup, not appended as repeated timeline rows. Closing Setup
does not cancel downloading; an interrupted partial file can resume on the next attempt.
Free storage is checked before downloading. Self-test requires the frontend stopped and is
serialized against provisioning to avoid allocating a second multi-GB model.

**Keyboard/accessibility acceptance:** edge-to-edge root owns system/cutout/IME insets once,
using `max(navigation, IME)` with resize, not pan. Only the timeline shrinks. Composer grows
to four lines and then scrolls internally. Actions have 48 dp minimum height, native modal
focus semantics, semantic labels and scalable sp text. Link/Local and errors use words, not
color alone. Real emulator evidence and remaining test-matrix gaps are recorded below.

## Silent text chat

Install the updated APK **over the existing app**, without uninstalling or clearing data.
The application ID and debug signing configuration are unchanged; versionCode is now 5
(`0.5-operator-trial-dev`).
Existing model weights, verification markers and saved endpoint/backend choices remain.
Android's package installer should offer **Update**. If it instead reports a signing
conflict, stop: do not uninstall to get past it.

Version 3 fixes the physical-phone tailnet fixture connection: debug builds permit
cleartext WebSockets to the specific laptop host `100.85.3.93`, in addition to
loopback/emulator addresses. Use `ws://100.85.3.93:8770/ws` when the fixture is running
and Tailscale is connected. The main/release resource does not gain this exception;
arbitrary cleartext destinations remain blocked. Real connections need `wss://`
and the authenticated app adapter described above. Cleartext-policy failures now
show an actionable message without exposing endpoint URLs or tokens.

1. Open the app. Opening/restoring the Activity silences any existing voice service;
   legacy `--ez start true` intents now start **text**, never voice.
2. Tap **Start text**. Wait for **Link Connected / Local Model ready**. Text readiness
   loads and warms the same local model and opens the same OrchestratorLink, but never
   constructs TTS or a speech recognizer, requests microphone access, or checks ASR/TTS packs.
3. Type into **Message Dogwalk**, then tap **Send** (or the keyboard's Send action).
   User and assistant turns remain visible, including asynchronous Orchestrator replies.
   Work, permissions, corrections and cancellations follow the same Handoff policy as voice.
4. Setup, pinned model download/verification, endpoint and self-test are under **Menu**.
   Voice requires **Enable voice**, explicit confirmation and microphone permission.

In text mode there are no TTS speak calls, recognizer creation/listening, microphone
foreground-service type, or spoken startup/download/readiness/error notices. The app uses
a silent notification channel and disables its own control click sounds. It does not touch
system volume, output devices or phone settings. Your keyboard's own key sounds, system
accessibility speech, and unrelated apps remain outside this app's control.

Switching to text stops/shuts down TTS, cancels/destroys recognition, advances the generation
epoch and invalidates voice callbacks. Late jobs cannot reopen audio. Already displayed
requests do not replay on a later voice switch. A request not yet displayed remains pending.
Each queued say gets its own phrasing job and separate chat turn; brief/Following entries
are background only (visible in diagnostics, never assistant chat bubbles).

**Text delivery semantics:** `TEXT_DISPLAYED` and `Displayed` are distinct from
`PLAYBACK_COMPLETE` and `Heard`. Only appending the exact utterance to an active Activity
acknowledges its code-bound source request. With the screen absent, the utterance waits;
returning to the Activity presents it. Local model history labels typed/displayed turns
accurately. Existing input/output transcript delta envelopes and Handoff offsets are retained,
with an app-local `delivery` extension: `text_typed`, `text_displayed`, or
`speech_recognized`. Text uses a single session-relative timestamp (`start_ms == end_ms`),
not an invented audio duration; it sends no `spoken_chars` or synthetic playback callback.
Request/utterance IDs stay code-owned in local receipts/logs. A display receipt proves
neither reading nor understanding, just as playback proves neither understanding nor consent.
Existing adapters that ignore `delivery` cannot distinguish text from speech: a real adapter
must preserve that provenance before this is used beyond the fixture.

Chat and pending delivery state are process-local, not a durable messenger. Keep the app
open for active text interaction; it holds a special-use foreground service for inference
and the socket, but Android/process death loses this local history/queue. Errors are shown
in the readiness line; your unsent draft is kept if Send is pressed before readiness. To
change model/backend after starting, stop the frontend in Setup, then start again.
Fixture mode still does no real work; real mode requires separate operator setup/deployment.

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

In explicitly enabled voice mode, readiness happens in order, and each step is spoken when it fails: offline voice selected,
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
  `session.output_transcript.delta` (heard words in voice mode, displayed words in text mode) and
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
  playback or explicit text-display receipt. FIFO can therefore present stale progress or a withdrawn question. That is known and
  **not production-safe**; the remote contract carries no authoritative replacement IDs, and
  small-model supersession dropped live permission questions in the archived experiments.

## Running it

Toolchain: JDK 17, Android SDK platform 35 and build-tools 35, Gradle 8.11.1, reused from the
archived audio probe's bootstrapped `.tools` (this prototype never accepts SDK licenses).

```sh
export DOGWALK_ANDROID_TOOLS=/path/to/android_voice_probe/.tools
export DOGWALK_ANDROID_DEBUG_KEYSTORE=/path/to/existing/android_on_device_frontend/.tools/android-user/debug.keystore
uv run --script build.py               # unit tests, assembleDebug, lintDebug
uv run --script fixture_orchestrator.py test   # fixture self-test
uv run --script fixture_orchestrator.py        # FIXTURE Orchestrator on 127.0.0.1:8770
adb reverse tcp:8770 tcp:8770          # phone or emulator reaches the fixture over USB/loopback
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

The v5 build verifies the APK certificate against native v4 and refuses installation
on a mismatch. A fresh worktree otherwise creates a new debug key. Point
`DOGWALK_ANDROID_DEBUG_KEYSTORE` at the existing prototype's key, never commit/copy
it into tracked files. The expected public certificate SHA-256 is
`770c6f0b86fa8f1de880a8f60a3f4c937733335f17e3ba8667b97bc331959b92`.

The development package is `as.adamsmith.dogwalk.ondevice.dev` and coexists with the
archived audio probe. Provision the model either with the app's pinned download button, or
with `adb push gemma-4-E2B-it.litertlm /sdcard/Android/data/as.adamsmith.dogwalk.ondevice.dev/files/models/`
followed by "Verify model file". Text mode needs no microphone grant. Start text with:

```sh
adb shell am start -n as.adamsmith.dogwalk.ondevice.dev/dogwalk.ondevice.MainActivity \
  --ez start true --es endpoint ws://127.0.0.1:8770/ws
adb shell am start -n …/dogwalk.ondevice.MainActivity --ez selftest true      # model only, no audio
adb shell am start -n …/dogwalk.ondevice.MainActivity --ez stop_speaking true # local stop
adb logcat -s DogwalkFrontend:I
```

For an isolated test fixture, set `DOGWALK_FIXTURE_PORT=18771` before running it and
reverse that port on an **explicitly selected emulator** (`adb -s emulator-5558 reverse
tcp:18771 tcp:18771`). Never reuse a fixture connection carrying another user's playtest.

On the emulator, `emulator_speech.py "phrase"` synthesizes caller speech with macOS `say` and
streams it into the emulated microphone through the emulator's authenticated gRPC
`injectAudio` (start the emulator with `-grpc 8554 -grpc-use-token`). Debug builds also accept
`--es inject TEXT`, logged as `INJECTED caller text (not speech)`; it was not used for the
evidence below.

## Evidence (2026-11-10 to 11)

### Native conversation redesign (2026-11-11)

- 29 JVM tests pass (the existing 24 plus five presentation tests): independent readiness,
  source ordering/clock, distinct request provenance, retention/deduplication, conservative
  modal visibility, and no false audio-off claim on link loss. Existing mode-switch and
  failed-display tests still pass. `assembleDebug` and `lintDebug` pass: 0 errors,
  13 warnings (prototype English-only copy and conservative free-space checking).
- API 35 ARM64 emulator `emulator-5560`, launched here with `-no-audio -no-window`, CPU
  LiteRT-LM 0.19.1, existing E2B weights. Explicit `adb -s emulator-5560 install -r`
  succeeded. First install time remained 2026-11-10; package version is 4. Model SHA-256
  still equals `181938105e0eefd105961417e8da75903eacda102c4fce9ce90f50b97139a63c`,
  the verification marker remains, and `RECORD_AUDIO` remains denied.
- Final APK silent UI smoke against isolated port 18774 passed: actual UI text entry,
  real local greeting decision, work Handoff, two independent asynchronous requests,
  permission question/typed answer, fixture disconnect and Retry. No TTS, ASR, playback
  or debug-injection path appeared in the test process's logs. No real Agent work ran.
- Real Gboard visible: timeline bounds `[0,540][1080,1296]` (756 px high), composer
  `[31,1317][797,1441]`, above IME. The native Send target remained reachable and the
  multiline IME offered Send. Screenshots were inspected against the approved HTML.
  System-bar contrast and diagnostic density were corrected during this review.
- At exactly 360 dp width (945 px at 420 dpi), real Gboard left a 571 px timeline
  viewport `[0,523][945,1094]` and composer `[31,1115][662,1239]`, with Send above the IME.
  The actual selected-model-missing route was exercised by selecting the absent E4B file,
  stopping and reopening, then restoring E2B. No weights or app data were removed.
- A second controlled fixture on port 18775 proved that a new output receives no display
  receipt while Setup is open, then receives exactly one after Done. Five separate initial
  requests retained independent receipts. While reading older history, a new reply preserved
  the top visible item/offset and exposed Latest. Draft survived opening/closing Setup and
  landscape Activity recreation. This was real local inference with scripted socket inputs,
  not simulated model readiness or audio.
- `silent_text_smoke.py` now uses semantic accessibility labels and separate status words,
  and requires `--artifacts /absolute/local/directory` for screenshot/XML evidence. It still
  refuses physical-device serials and port 8770, never installs or enables audio.
- Connected-plus-loading is tested as a typed-state invariant, not a fabricated runtime
  screenshot: the real startup still warms locally before opening the link.
- No physical phone, real microphone/speaker, screen-lock voice, E4B/GPU, new download,
  low-storage fault, TalkBack, cutout/three-button navigation or 200% font-scale runtime
  coverage is claimed. Download/verification failure and genuinely empty-device installation
  are not runtime-tested. The platform dialog is intentionally used instead of a custom
  bottom sheet. Process-local pending requests are not a durable or fully bounded mailbox.
  Extremely short landscape windows or large-font combinations can leave little history:
  the upper surface is constrained so it cannot push the composer below the IME. Full
  landscape-with-IME and large-font acceptance remains a test-matrix gap.

### Silent text update (2026-11-11)

- 22 JVM tests pass, including 10 text-specific tests with fake audio adapters: default
  silence, per-request receipts, unavailable display, late playback callbacks, stale model
  jobs, mode switches without replay, latest typed intent, background context and transcript provenance.
- API 35 ARM64 emulator `emulator-5558`, launched with `-no-audio`, existing E2B weights,
  CPU LiteRT-LM 0.19.1, isolated loopback fixture on port 18771. `adb -s emulator-5558
  install -r` succeeded without uninstalling and the existing 2,588,147,712-byte model remained.
- Actual UI text entry and Send, not debug injection: greeting produced a real local
  reply, status produced a Handoff and visible answer, and a two-say burst produced separate
  `r3 -> u6` and `r4 -> u7` display receipts. Brief stayed background. Fixture disconnect
  appeared as a readable error. No TTS/ASR/playback/debug-injection path appeared in app logs.
- The final expanded run also passed with the app's `RECORD_AUDIO` permission **denied**:
  a rebuild request, its permission question, and typed "yes allow it once" all traveled
  through Handoffs and visible replies. The isolated fixture recorded only `text_typed`
  and `text_displayed` deltas, with equal start/end timestamps. Build/lint pass (0 errors,
  2 prototype warnings: Intent SAM false-positive and untranslated UI copy); fixture self-test passes.
- Measured local generation: warmup 2292 ms, greeting decision 2820 ms, the two queued
  phrasings 1419 and 1530 ms. These are emulator measurements, not Pixel latency.
- `silent_text_smoke.py` reproduces UI checks against an already installed app, model and
  isolated fixture; it accepts only an explicit emulator serial and refuses port 8770.
  It never installs, enables voice, injects audio, or touches a physical phone.
- Real voice-to-text audio interruption was **not** exercised: all audio tests were
  prohibited for this update. Mode/generation/receipt behavior is covered by JVM fakes
  and source-level gates. No physical-device install or Pixel runtime check was performed.

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

- **Real transport not deployed or live-tested.** The Adam-only `/app/frontend` route
  requires separate deployment approval and manual credential provisioning. The archived
  admin-plus-phone development route was not ported. No general enrollment service exists.
- **No authoritative supersession.** FIFO speaks every request, including obsolete ones.
- **Voice evidence above is not a Pixel test.** No physical device was attached for these checks. The GPU backend, real
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
