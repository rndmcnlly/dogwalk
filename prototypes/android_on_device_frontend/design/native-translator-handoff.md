# Android translator handoff: review proposal, not implementation authorization

Read `chat-redesign.html` with `design-notes.md`. User review must precede translation. This artifact is from the requested GPT-6.1 Sol design subagent, not an Android implementer. No translator was found or substituted in this task.

## Scope and non-negotiables

- Parallel Android access only. The ordinary-telephone accessibility promise remains unchanged.
- Current endpoint `ws://100.85.3.93:8770/ws` is a scripted fixture. Keep Development / scripted fixture prominent even when connected. There is no real coding route or non-PSTN authentication adapter. Do not add production credentials or imply completed coding work.
- Preserve native Activity/widgets. This does not require Compose, new dependencies or enterprise architecture. A vertical root, a weighted timeline and a modal settings dialog/sheet are sufficient.
- Preserve package ID, signing configuration, `setup` preferences (`endpoint`, `model`, `gpu`), pinned models, verification markers and external model storage. Update in place, never clear app data or uninstall to bypass a conflict.
- Opening/restoring the current Activity silences voice via ACTION_RESTORE_TEXT. Retain this safety contract unless Adam explicitly approves a change. A visible mode change event should explain it. Notifications that launch this Activity therefore also silence voice today.
- Text startup remains silent. No TTS/recognizer construction, microphone permission prompt or microphone foreground-service type until explicit voice enable. Opening Setup does not enable audio. Do not alter system volume or accessibility speech.

## Visual layout and IME

The HTML is an interaction specification, not pixel-perfect Android CSS to translate literally. Use dp for geometry and sp for text, not the current raw-pixel paddings. Colors start from current User `#e8eef4` and assistant neutral gray; blue `#245b89` is an action color, not readiness evidence.

1. Compact app bar: Dogwalk, persistent development/fixture subtitle, Menu (48 dp minimum target).
2. Two independently labeled status lines: Link and Local. Reserve enough width for Connecting and Installed/not loaded without truncation. Place the current primary action alongside when useful: Start text, Connect or Retry. Do not use a solitary green dot or one ambiguous Ready label.
3. Compact mode row: Text, microphone/speaker off; explicit Enable voice. Voice uses a labeled state panel and separate Stop speaking / Switch to text controls. Stop speaking is local audio interruption, never Cancel Prompt Turn.
4. One weighted vertical scrolling timeline. Conversation turns, important state transitions and collapsed diagnostics share this container. No nested independently scrolling logs.
5. Composer outside the timeline, above the IME. Text input grows from one to at most four lines, then internally scrolls. Send has a 48 dp minimum target and an IME Send action. Preserve draft during loading, disconnect, opening/closing Setup and Activity recreation. Shift+Enter is an optional hardware-keyboard newline; do not intercept composition Enter.

Use resize semantics with IME insets, not pan and not a hardcoded keyboard height. With edge-to-edge/API 35, consume system bars and IME exactly once. Typically bottom inset is `max(systemBars.bottom, ime.bottom)`, not their sum. Confirm this with the actual window/manifest configuration rather than stacking adjustResize and manual translation blindly. The current listener consumes only systemBars, not IME (MainActivity 70–74). Keep gesture navigation, display cutouts, three-button navigation and landscape in the test matrix. The HTML keyboard and gesture handle are preview-only, never native app views.

At 360 dp width with a roughly 200–300 dp IME, a meaningful history slice must remain above the composer. At large font sizes/short windows, let labels wrap and use a compact stacked header layout; do not shrink font or targets. Prioritize a reachable composer, truthful state and a usable timeline. The fixed-height HTML is not evidence that 200% fonts work on Android.

## Presentation state model

Keep this as an app projection, not new ACP state or Managed Session state:

- Transport: disconnected, connecting, connected, error, with sanitized reason. Connected means socket open only, not authenticated or capable of real work.
- Local model: unknown/not checked, missing, installed/not loaded, verifying, loading, warming, ready, error. Ready requires engine load plus successful warmup generation. File verified alone is not enough. Backend and model identity belong to settings/detail, not a giant daily panel.
- Mode: text or explicitly enabled voice. Audio capability checks are independent: offline TTS, on-device ASR pack, microphone permission, voice preparation. Text readiness must not depend on ASR/TTS.
- Voice activity: preparing, listening, thinking (local model busy), speaking, locally paused, unavailable or unknown. Show Listening only when the recognition callback confirms readiness, not merely when a start request was issued. Speaker and microphone can overlap on a headset, so a single label must not falsely imply mutual exclusion. A supporting line can say microphone also listening.
- Delivery: typed/recognized input, output pending, displayed, playback started/completed/interrupted. Displayed does not mean spoken/read/understood/authorized. Playback complete does not prove acoustic hearing or consent. An approximate played prefix is not exact acoustic observation.
- Derived availability: text Send requires text mode + warmed model + connected link + actual service acceptance. Voice additionally requires checked audio capabilities. On unavailable state, leave draft editable and explain the disabled action in words.

The mockup's connected-plus-loading scenario is a stress test. Current startup loads/warms first and opens the link afterward. The preview's Complete preview step button and scenario chooser are not native UI controls.

## Actual source mapping and limitations

References are to current source in `app/src/main/java/dogwalk/ondevice/`.

- `VoiceService.status` / `stateListener` (447–450, 504–506) expose only a mutable string. MainActivity uses `startsWith("Text ready")` to gate Send (187–195). Strings are not a reliable structured readiness API. Add a small immutable presentation snapshot emitted at transitions instead of parsing logs for product state.
- `connected`, `connecting` (58–59), `loading` (55), `model` (42), `ready` (44), `voicePrepared` (57) and `core.mode` already exist privately. `loadModel` verifies, loads and warms before setting `model` (180–205). That assignment is the available runtime readiness boundary. Verification and warmup substeps need explicit events if shown separately.
- `model != null` plus connected is used by textMode (122). Installed file state is not exposed in the Activity's service projection; obtain safe ModelStore checks separately. Do not infer file missing from every generic fatal error. Preserve unknown when checks have not run.
- `connect` marks connecting and socket-open transitions (209–230); `linkClosed` clears readiness (246–254). It does not establish production auth, request delivery acknowledgment or coding capability. It also does not explicitly stop an already running recognizer or TTS. On a drop, do not promise microphone off; Switch to text must stop audio deterministically. A stronger automatic-pause policy needs implementation and approval.
- `core.inFlight`/`core.playing`, `noticeSpeaking` and `listening` can inform activity. Today `startListening` sets a private flag without updating UI (391–396), `onReadyForSpeech` is empty (406), job start has no Thinking update (302–311), and notices are not reflected by the single state string. `finished` writes Listening before pump may start another job (379). Truthful voice state requires observed transition instrumentation, including failure and concurrent headset listening.
- `ChatTurn(id, role, text, requestId)` (501) is text-only and lacks time, source kind, delivery mode and speech completion data. `showChat` appends synchronously through the active Activity (507–511); `deliver` uses that success for textShown (334–343). Keep that receipt boundary. Do not acknowledge on receipt from socket, insertion into an invisible buffer or a passive settings screen alone. Clarify the modal visibility case before translation.
- `chat` and `lines` are separate process-local collections (502, 513); `lines` is bounded to 200 and has no occurrence timestamps or shared order. Chat is not bounded. Exact historical interleaving cannot be reconstructed from these two arrays. Introduce one small ordered presentation-event collection at emission time, with stable IDs and local observation time/sequence. Until then, migrated chronology must be explicitly unknown, not fabricated. Process death loses history and pending requests today, so do not promise durable retention.
- Inbound Say becomes its own code-owned request; Brief and Guidance update background context only (235–241). Following is a Brief convention, not an assistant reply. Useful diagnostic projection may say Background context updated, with sanitized detail. It must never render a background brief as spoken or displayed assistant output.
- `completed` logs source request IDs, utterance IDs, phrasing kind and note (317–327); `deliver` logs TEXT_DISPLAYED and `finished` logs playback result. These are useful instrumentation boundaries. Retain request identity internally; do not expose opaque IDs as the principal reading surface. Source can be Local reply or Orchestrator request, locally phrased, with fixture identity while testing.
- `log` writes freeform strings, including raw text and generated output (487–489). Never feed this verbatim into the unified timeline. Use an allowlisted structured projection. MainActivity self-test logs raw output (166, 176); the user-facing event should contain bounded timing/result summaries only.
- Current reconnect uses ACTION_TEXT to connect when model exists (70–76). A new Retry button can use that existing behavior, but there is no demonstrated resume/replay contract or idempotent resend. Do not resend drafts/previous turns automatically and do not claim remote delivery confirmed.

## Timeline taxonomy and ordering

Use a small sealed/enum-backed event type, not string-prefix parsing. Suggested presentation types:

- `UserInput`: typed or recognized, content, local acceptance, source and observation time. Debug injection remains explicitly not speech.
- `AssistantOutput`: code-bound request/utterance IDs, local reply versus remote-request phrasing, content and exact delivery observation. Two Say requests remain two items even when they arrive together. No merging by visual convenience or “latest wins.”
- `ConnectionTransition`: connected/lost/retry started, concise reason and actionable current header state. Header is authoritative now; old timeline events are history.
- `ReadinessTransition`: model warmup, blocking failure, audio dependency failure. Coalesce noisy download percentages into one progress item; a collapsed event can hold the final safe summary.
- `LocalControl`: switched mode, speech stopped, Activity restored to silent text. Explicitly distinguish stop speech from cancellation of a Prompt Turn.
- `Diagnostic`: severity, category, local observation time, concise summary and bounded sanitized details. Examples: local inference timing, unsupported capability, context update, self-test result. Background context is a diagnostic subtype, never AssistantOutput.

Order by the phone's observed sequence, retaining originating IDs/times where actually supplied. Display only real recorded times in native code. HTML times are samples. Do not reorder existing rows when late metadata arrives; update the correlated item or append an explanatory observation. This is a modest chat timeline, not the cross-provider diagnostic sequence diagram.

Coalesce duplicate low-value status events and repetitive ASR/no-match retries; record meaningful transitions, not every token, recognizer amplitude, raw prompt or socket envelope. Inline expansion stays within the timeline. A long sanitized detail may wrap, but must not become another scrollable console. No tokens, secret URLs, auth values, full production payloads or chain-of-thought. Do not infer permission authorization from viewing or expanding anything.

## Scroll and focus rules

- Follow the end on first open and after the User sends. Incoming activity follows only if the User was already near the end (suggested threshold: 64 dp), and not while selecting text or navigating an expanded diagnostic.
- While reading older material, preserve the top visible item and offset. Show a 48 dp Latest target with a count. A polite announcement says new items arrived, without reading entire turns automatically.
- Keyboard show/hide preserves the visible anchor when reading history; if following the end, keep the newest item and composer visible. New content never moves input focus.
- Expanding an event preserves its header position and accessibility focus. Opening settings preserves timeline anchor and draft. Closing restores focus to the invoking control if still visible, otherwise Menu.
- Modal blocks interaction and accessibility traversal behind it. Back closes the modal first, then dismisses IME, without stopping remote work. Use platform dialog focus/TalkBack semantics rather than hand-rolled DOM behavior in native code.
- Status uses words plus tone, never color alone. Minimum 48 dp targets, adequate contrast, scalable text, meaningful labels. Text startup remains silent, but TalkBack remains under the User's system settings.

## Settings behavior

Settings opens as a modal bottom sheet or ordinary native dialog with one scrolling content region. A platform dialog is acceptable if simpler. Endpoint, model, backend, verify/download, self-test and Stop frontend do not crowd daily chat.

Preserve current saved choices. Save does not silently restart an active engine or change an active socket. Show “Applies on next start” and require Stop frontend before model/backend changes, matching current prototype limits. Changing the endpoint should not silently connect. A future Apply/restart flow needs a clear confirmation, not hidden side effects.

First-run model action opens this same setup with the model section in view. Download needs pinned identity, approximate storage, progress, verification result and readable error, all silent in text. Keep verify-existing-file route. The HTML describes download steps but transfers no bytes. The native implementation must wire real progress, cancellation policy and insufficient-storage handling.

Self-test remains no audio. If the active service already holds an engine, do not accidentally allocate a second multi-GB model: define whether the UI disables self-test until stopped or reuses a safe serialized existing engine. This resource policy is unresolved. Results belong in the single timeline, with sanitized detail.

## Acceptance tests after approval

1. Existing package update retains signing, endpoint/model/backend settings, verified weights and storage. No uninstall/data clear. Opening app with microphone denied still supports text and requests no audio permission.
2. Cold start with installed model shows installed/not loaded, then verifying/loading/warming, then local ready. Connect uses saved endpoint without visiting settings. Socket connecting/connected is independently visible. Connected plus local failure never enables Send.
3. First run shows one clear model setup action, truthful size and missing-file state. Download/verify errors are visible and recoverable without permanently occupying chat height.
4. At 360 dp portrait with real IME visible, input and Send stay above IME/system bars and history has a useful viewport. Four-line input, hardware IME Send, long draft, landscape, cutouts, gesture/three-button nav, large fonts and TalkBack do not obscure or trap the composer.
5. Two distinct asynchronous Say requests append two assistant outputs and two correct request-bound display receipts. Brief/Following produces no assistant speech/bubble. Detached/hidden display does not create a text-display receipt. Reopening displays pending outputs once, with no replay when switching to voice.
6. Display provenance says displayed, not heard/read. Spoken output records playback state only from real callbacks; interrupted approximate prefix stays labeled approximate. No fake speech duration for typed/displayed text.
7. Explicit Enable voice checks microphone permission and installed offline ASR/TTS, then reflects actual listening/thinking/speaking. Stop speaking stops local audio, never cancels Agent work. Switch to text invalidates late voice callbacks and shuts audio down. Restore Activity silences voice and explains the transition. Screen-lock behavior remains a separate on-device test.
8. Drop during draft, local inference and speech: persistent disconnected status, concise warning, retained process-local history/draft, no automatic retransmission. Retry reuses the endpoint. Do not claim microphone off or remote work unaffected without observation. Process death is not described as retained history until persistence exists.
9. While scrolled up, a burst of replies does not pull the viewport to the end. Latest works. Diagnostic expansion, IME transitions and sheet close retain focus and scroll anchor. No duplicate items/receipts after Activity recreation.
10. Settings closed by Done/Back restores focus/draft. Save does not unexpectedly restart. Self-test has a safe memory policy and emits no raw model output. Tokens and credential-bearing URLs never enter collapsed or expanded diagnostics.

## Decisions for Adam, not for the translator to silently choose

- Approve the layout and journeys before code. Mockup controls simulate progress, not actual adapter capability.
- Retain manual Start text/Connect on return, as proposed, or explicitly authorize automatic silent connection? No automatic voice resume either way.
- Keep current process-local history limit honestly documented, or separately scope durable history/drafts? Define event retention bounds before adding a unified collection.
- Does opening settings hide the conversation enough that new output must wait for a display receipt? Recommended conservative choice: wait until conversation is visible. Current service only knows an active Activity callback, not modal coverage.
- On link loss, should voice automatically stop listening and playback, or remain enabled with truthful uncertain/unavailable state? The preview does not pretend today's code pauses audio.
- Should no-audio self-test require stopping the frontend? How should changing settings guide that stop/restart?
- Production identity/authentication, capability negotiation and authoritative request supersession remain separate architecture work. The UI must not invent answers, authorization controls or real coding integration.
