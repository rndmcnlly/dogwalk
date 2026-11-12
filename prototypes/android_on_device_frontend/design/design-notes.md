# Dogwalk Android usability design

Design-only work by GPT-6.1 Sol. No Android edits, builds, installs, service actions, external services or audio. Mockup is a review artifact, not implementation approval.

## v1: layout and journeys

- Read the worktree's AGENTS.md, DOMAIN.md, prototype README, MainActivity and VoiceService. Worktree was clean; design directory did not exist.
- Adopted the existing pale blue User / neutral assistant palette, with a restrained blue action color. There is no existing app token system in the Activity.
- One fixed-height phone preview with one chronological timeline, compact independent Link and Local states, explicit mode, pinned composer and an optional simulated keyboard. Setup is a modal sheet, not a second panel consuming chat height.
- Return: connect with saved endpoint, local readiness remains visible. First run: a contextual model-install blocker occupies the otherwise empty timeline and leads to model setup. It disappears from daily chat once provisioned.
- Text: Send accepts a draft in the preview, separate sample replies append independently. Voice: an explicit enable confirmation precedes listening, thinking and speaking scenario states. Interruption: the header and timeline both show the failure, with Retry and no implicit resend. Inspect: sanitized diagnostic details expand in place. Setup: endpoint/model/backend/self-test live in the sheet.
- All outcomes are labeled sample or preview, with persistent development/fixture identity. Brief/Following are not assistant turns.

## Expected journeys for user review

1. Return with installed model: see Link disconnected and Local model ready, then Connect without opening Setup. Installed and runtime-ready must remain distinct.
2. First run: see one blocking model action and storage size, not a permanent provisioning console. Verify an existing file is a secondary route in Setup.
3. Text with keyboard: composer remains above IME, Send remains reachable, timeline alone shrinks. Draft survives not-ready states.
4. Asynchronous output: two requests create two separate assistant items. Displayed means appended, not spoken, read, understood or authorized.
5. Explicit voice: acknowledge microphone/speaker enable, show truthful activity, stop speech locally, switch to silent text without replaying displayed requests.
6. Drop: retain running-process history and draft, show concise diagnostic plus persistent disconnected state, Retry without retransmission claims.
7. Inspect: expand one sanitized event inside the same chronology. No independent log scroller.
8. Configure: open a sheet, inspect endpoint/backend/models and self-test, close and return to the same timeline position and draft.

## v2: keyboard and state stress review

- Reviewed fixed-height layout geometry: at the intended 360 px phone width, approximately 160 px of header/mode, 90 px composer and 204 px preview keyboard leave roughly 265 px of usable history. Only history shrinks; header, mode and composer are outside its scroll container. These are design estimates, not measured Android results.
- Found installed-file versus warmed-engine ambiguity. Added a cold-return scenario with Local installed/not loaded and Start text. Stop frontend now returns to installed/not loaded, not falsely ready.
- Added a deliberate connected-but-local-loading stress case. It keeps Send disabled despite Connected. This combination is a presentation stress test, not a claim that today's sequential startup emits it.
- Found a broken preview journey: Connect could only be completed by resetting the whole scenario. Added Complete preview step outside the phone, preserving timeline/draft through install, warmup, connect and voice phase steps. The native UI does not get this control.
- Setup remains an overlay, not an inline expansion stealing timeline height. Draft stays editable while unavailable; disabled Send has a plain-language reason next to it.
- Actual IME behavior, font scaling and physical-device dimensions remain native acceptance tests. No browser or Android measurement is claimed.

## v3: recovery, accessibility and provenance review

- Audited source-derived claims. Changed User provenance from Sent to frontend to Accepted locally: there is no delivery-acknowledgment signal here. Assistant items now distinguish a local reply from a locally phrased fixture request, with Displayed/not spoken labels.
- Prevented a provenance error in the preview: adding replies in voice mode produces a diagnostic about pending speech requests, not false text-display receipts. No sample action captures, plays or acknowledges actual audio.
- Found linkClosed does not explicitly stop recognition/playback. Voice error copy now admits uncertain audio state and offers the deterministic Switch to text path; it does not falsely promise a paused microphone.
- Added severity/category/sample time in collapsed diagnostic rows, not only expanded detail. Times are clearly sample data, not reconstructed from the app's untimestamped arrays.
- Added modal background inertness, focus trap, Escape and return focus; keyboard Send respects IME composition. New-activity announcements do not automatically speak whole turns. Incoming items preserve the viewport when reading history; Latest is explicit.
- Documented missing structured readiness, voice observations, unified ordering and provenance as implementation gaps. Added a native translator brief with source mappings, IME/system inset guidance, retention and receipt boundaries, settings resource concerns and ten acceptance-test groups.
- Final artifact remains one refined mockup, not three competing designs. User review is still required before translation. Static validation results are recorded below; no rendered browser/Android test is claimed.

## Unresolved product choices and implementation limits

- Manual silent startup versus automatic silent connect on return; voice must remain explicit.
- Receipt boundary while a settings modal covers chat: recommend withholding newly covered outputs until visible.
- Voice behavior on link loss: current code does not guarantee audio stops.
- History/draft durability and bounded retention: existing chat/queue are process-local; no durable messenger promise.
- Self-test memory policy while a model is already loaded, and precise settings stop/restart workflow.
- Authentication/real Orchestrator adapter and authoritative request supersession remain outside this UI task.
- Current service has private readiness flags but exposes one string. ChatTurn lacks times/source/delivery fields; logs lack time/shared ordering. Exact merged historical chronology and truthful voice activity need additional instrumentation. See handoff for details.

## Validation

- Inline JavaScript compiled with Node's `new Function` syntax check.
- Python HTMLParser parsed the document: 29 unique static element IDs, no remote script/style/image/link resources, no `100vh`, no authored em dashes. The self-contained HTML is about 26 KB.
- Changes are confined to the three new files in this design directory. No Android source was edited and no existing changes were overwritten.
- No browser rendering, interaction automation, accessibility audit or physical-device/IME test was performed. Parent will inspect and render this artifact for critique before user review. Layout dimensions above are estimates, not evidence from a screenshot.
