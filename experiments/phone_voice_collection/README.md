# Temporary telephone voice collection

## Activated direct-stream implementation (supersedes hosted Record plan below)

The user subsequently authorized immediate activation. Live synthetic tests showed
that this Twilio account returned **HTTP 200 to unauthenticated recording-media
requests**. Both synthetic hosted recordings were deleted, and the original
number routing was restored while the collector was replaced. No personal voice
was collected into those hosted recordings. No account-wide setting was changed.

`stream.py` is now the implementation: six basic Say prompts, generated short beep,
and ten seconds of caller-only inbound Media Stream per round. This uses
`Start/Stream` and `Stop/Stream`, **never Record**. It writes mono 8 kHz PCM WAV
directly on the laptop after decoding Twilio's inbound G.711 mu-law frames.
There are no Twilio recording resources, public audio URLs, or manual downloads.
Only the nonpersonal generated beep and health check are publicly served.
Signed HTTP and WebSocket requests are required for voice/media endpoints;
WebSocket start metadata must match the account and a known call and round.
Incomplete streams are retained with `complete=false`, not falsely reported as
complete. Original telephone-bandwidth capture and inserted transport gaps are
documented in private per-call JSON, with checksums and phrase labels.

Private runtime directory:
`~/Library/Application Support/Dogwalk/phone_voice_collection/`
contains routing snapshot, tested restore payload, logs, per-call JSON and WAV.
Directory mode 0700 and file creation umask 0077. No phone numbers, secrets or
recording URLs are written into source control. Twilio and the HTTPS tunnel still
transport voice; this avoids public hosted recordings, not provider involvement.

From the repository root, immediate rollback is:

```sh
uv run --script experiments/phone_voice_collection/collect.py restore
```

This exact command was tested against the original live configuration before
activation and again after synthetic privacy testing. It restores all saved
per-number voice fields and verifies read-back, refusing unexpected config drift.
No shared Application, SIP Trunk, production Worker or production code is changed.

Operational status:

```sh
uv run --script experiments/phone_voice_collection/stream.py status
```

The attended service accepts up to three calls, each roughly two minutes, within a
four-hour window. The server watchdog then restores original Twilio routing.
Server, separate Quick Tunnel, and a bounded caffeinate process run in the
background. Keep this Mac online and lid open. If its process dies or networking
fails, automatic restoration cannot be guaranteed: use the restore command above.
Restoring routing affects new calls, not a call already collecting. Do not stop
the tunnel until an active sample call ends. No unsolicited user call is placed.
Caller identity is manually confirmed afterward; the three-call budget is not
authentication, and unexpected callers must not be labeled as Adam.

Local retention is seven days, with explicit expired-only cleanup:

```sh
uv run --script experiments/phone_voice_collection/stream.py cleanup
```

This command removes only expired stream WAV and matching metadata. It is not a
scheduled seven-day deletion job. No hosted voice recording charges apply to this
implementation. Normal inbound voice minutes and any applicable Media Streams
pricing apply; no OpenAI/Daytona/model charge or new-number purchase is needed.

The material below preserves the original hosted-recording investigation and
privacy gate, not the final active audio transport.

---

## Original proposal

Scope: Adam knowingly records his own voice using the existing Dogwalk number.
No identity decisions, speaker verification, transcription, or secret challenges.
No live Twilio requests, tunnel startup, deployment, or production edits were made.
`flow.py` is an **offline TwiML contract proof**, not a running HTTP endpoint.

## Recommended path

Temporarily route the existing number directly to a separate, small HTTPS TwiML
endpoint. Leave the existing Cloudflare Worker, D1, Durable Object, Daytona and
OpenAI setup entirely untouched. Dogwalk incoming calls are unavailable on that
number during this collection window. Existing active calls are not redirected by
changing a number's incoming URL; finish them before starting.

The separate endpoint says a short randomized sentence, beeps, records the caller,
then returns the next sentence from the Record action callback. Six rounds, then
goodbye. The phrase is spoken **before** recording begins, not mixed into the file
by a continuous two-party call recorder. Acoustic speakerphone echo is still possible.

Both `cloudflared` and `ngrok` are installed locally. No named-tunnel configuration
was found in `~/.cloudflared` (only a certificate filename was observed, not its
contents). An existing suitable secure tunnel has **not** been established.
Simplest one-session hosting option: a loopback-only local endpoint and a separate
temporary HTTPS tunnel, with Twilio signature checking on every POST. Do not alter
Dogwalk's tunnel or ingress configuration. A Quick Tunnel needs no Worker deployment
or DNS change, but is a public URL, has no uptime guarantee, and the laptop must
stay awake through delayed recording callbacks. Do not put an interactive Access
login in front of a Twilio webhook. Disable tunnel request inspection/body logging.
If local uptime is impractical, deploy the same tiny endpoint as a **separate**
ephemeral service, with explicit approval of its hosting cost and secret storage.
TwiML Bins alone are a poor fit for persistent randomized labels and callback state.

## Before changing anything: snapshot and verify routing

Use authenticated read-only Twilio API GETs, with credentials retrieved privately,
not printed or placed in command arguments. Save raw JSON outside Git in a new
0700 directory, files 0600. Treat URLs in this snapshot as potentially sensitive.
Do not dump it into chat or logs.

1. Read the exact IncomingPhoneNumber resource by PN SID in its owning account.
   Save the full response, plus an explicit restore projection of:
   `voice_url`, `voice_method`, `voice_fallback_url`, `voice_fallback_method`,
   `voice_application_sid`, `trunk_sid`, `status_callback`,
   `status_callback_method`, `voice_receive_mode`, `voice_caller_id_lookup`,
   `api_version`, account SID, number SID, and snapshot timestamp. Preserve null
   versus empty values in the snapshot. Leave SMS and number ownership unchanged.
2. If `voice_application_sid` is nonempty, fetch and save that Application's full
   configuration, including its voice/fallback/status callback URLs and methods.
   Do not edit the shared Application, detach only this number after approval.
3. If `trunk_sid` is nonempty, save the full Trunk configuration and origination
   routing plus its number association. Do not edit the shared Trunk. Detaching
   and reattaching this number requires explicit approval and a verified restore
   sequence. Inspect regional voice routing/Console routing selection as well if
   the account uses non-US1 Voice Regions; do not assume a US1 URL controls it.
4. Determine effective routing: **trunk overrides voice applications and URLs;
   application overrides number VoiceUrl**. Setting a trunk SID automatically
   removes the application SID, and vice versa. Just setting VoiceUrl can do
   nothing. The existing deployment docs establish a Worker webhook architecture,
   but are not evidence of the live number's current routing settings.
5. Precompute a private restore payload using API field names (VoiceUrl,
   VoiceMethod, VoiceFallbackUrl, VoiceFallbackMethod, VoiceApplicationSid,
   TrunkSid, StatusCallback, StatusCallbackMethod, VoiceReceiveMode,
   VoiceCallerIdLookup, ApiVersion). Do not blindly POST the fetched resource:
   it contains read-only properties. Validate clearing semantics for inactive SID
   fields against the actual SDK/API before switching. Never restore both active
   application and trunk SIDs in one request.

For a plain webhook number, the intended temporary diff is VoiceUrl -> collector
`/voice`, VoiceMethod -> POST, VoiceFallbackUrl -> collector `/failure`,
VoiceFallbackMethod -> POST, and clearing StatusCallback while collecting to avoid
production call-status traffic. Leave mode/lookup/version unchanged. `/failure`
must return a short failure notice and Hangup, never start Dogwalk or Record.
If the old fallback points at Dogwalk, retaining it could unexpectedly wake
production on a collector failure. A fallback on the same tunnel cannot help if
the tunnel is down, but fails closed rather than falling into production.
Any active Application/Trunk association must first be cleared and its result
read back. That adds routing risk and is a blocker until the exact live shape is
known. No mutation helper is provided, deliberately.

## Endpoint adapter contract for the next implementation

Run the offline proof now, safely, with no credentials:

```sh
uv run --script experiments/phone_voice_collection/flow.py
```

The adapter is intentionally not implemented pending approval of the plan:

- Bind only `127.0.0.1`, no debug mode or access/body logs. Return no media or data
  from any public route. Validate all Twilio POSTs with the official SDK and
  `X-Twilio-Signature` using the exact configured external HTTPS URL and all form
  fields, not a guessed proxy Host header. Also check AccountSid.
- Arm a short collection window with a strict call budget, ideally one call.
  The consent notice is not caller authentication. Do not silently label any
  unexpected caller as Adam: reject extra calls, and manually confirm the captured
  call afterward. From-number filtering is optional accident prevention only,
  not trustworthy identity verification. Never persist telephone fields.
- `POST /voice`: create six distinct phrases once, persist before responding.
  Key state by signed CallSid. Repeated webhooks reuse state and exact TwiML.
- `POST /next/<i>`: associate completed round i-1 with its recording if present,
  render round i. Do not wait for media availability or advance based on status
  callbacks. Preserve enough state for a hangup during a round to be labeled.
- `POST /recording/<i>`: validate signature, account, known call, round range,
  RecordingSid, source RecordVerb and mono channel count where supplied. Persist
  only allowlisted fields: opaque call/recording IDs, round, phrase, timestamp,
  status, duration, channels. Deduplicate by call/round/recording. Handle delayed,
  duplicate and out-of-order callbacks, and absent/failed recordings. Do not save
  raw payloads or RecordingUrl. Return 204 promptly, not TwiML to advance a call.
- Private metadata: SQLite or atomic JSON outside the repository, 0700 directory,
  0600 files, restrictive umask, encrypted laptop storage. Labels are experimental
  prompts, never credentials or future secrets. Speaker label starts unconfirmed;
  Adam confirms which call is his. Retain phone/codec/environment notes only when
  voluntarily supplied, without a phone number.
- A separate local-only export command builds canonical Twilio API recording paths
  from validated account/recording SIDs, not callback URLs. Download `.wav` with
  Basic Auth (prefer API key SID/secret), without logging URLs, headers or response
  bodies. Validate successful WAV headers, actual sample rate/channels/duration,
  and record a checksum. PSTN audio remains telephone-bandwidth, not studio audio;
  do not assume high-fidelity wideband capture from the WAV container.
- Export only completed media. Retry transient not-ready/rate-limit failures with
  bounded backoff. Do not send credentials to arbitrary redirect origins. If the
  API issues a signed media redirect, follow only HTTPS without Authorization and
  do not store/log its URL. No browser download links, public object bucket, or
  media-serving endpoint. The tunnel carries webhook data, not WAV exports.

## Recording choices and caveats

The proof uses `voice="man"` for basic Say, `timeout="3"`, `maxLength="12"`,
`playBeep="true"`, `trim="do-not-trim"`, `finishOnKey=""`, and
`transcribe="false"`. Six clips have a hard total recording bound of 72 seconds
per call, excluding prompts and call setup. Expect roughly 2–3 minutes on the phone.

- `<Record>` records caller audio and supports **one channel** only. Do not pass
  recordingChannels/recordingTrack attributes from other recording APIs to it.
- Read the sentence after the beep, then pause. No keypad ritual is required.
  Clearing finishOnKey avoids accidental DTMF stopping and the documented possible
  loss of roughly the last second when a finish key is used.
- Silence timeout can truncate hesitant speakers. No-speech clips are possible.
  Silence detection depends on received audio; missing RTP is not silence.
  MaxLength still bounds each attempted recording. Assess first clips manually.
- Record's defaults trim silence and allow any finish key: we explicitly override
  both. Preserve original WAV and do analysis trimming on a derivative later.
- The synchronous `action` RecordingUrl may not yet be downloadable. The async
  `recordingStatusCallback` completed event is the readiness signal. Subscribe to
  completed and absent; conservatively handle failed if received.
- Verbs after Record are unreachable. Each action response provides the next
  Say/Record document; the sixth action response provides goodbye/Hangup.

## Privacy gate and retention

**Before any voice collection, verify media authentication is enforced in this
account.** The Recordings reference says all media URLs enforce Basic Auth, while
the general Security guide still describes this as an opt-in Voice account setting.
Do not resolve this inconsistency by assumption. Read the Console setting and,
if an existing safe test recording is available, verify an unauthenticated request
fails without printing or saving a returned body. If no safe test exists, require
the setting to be confirmed first, then validate with an explicitly approved
nonpersonal/silent smoke recording before Adam's voice is captured. Never request
an existing private recording just to inspect it without authorization.
If disabled, changing the account-wide setting needs separate approval and a
snapshot: it can affect other integrations. Sending credentials on downloads does
not by itself make unauthenticated recording access impossible.

Proposed retention, requiring confirmation: delete **only this experiment's**
Twilio recordings immediately after verified local export, within 24 hours at
latest. Retain local WAV/phrase metadata for 7 days, then delete unless Adam
explicitly chooses a longer research retention period. Keep a private deletion
ledger without URLs. Deletion is not performed by the offline proof and must be
an explicit operation; no unattended cleanup job has been installed. Preserve a
deadline reminder and reconcile recordings by recorded CallSid if callbacks were
lost. Twilio documents that deleted recording metadata may remain for 40 days;
audio deletion is not a promise of immediate physical erasure from every backup.
Local snapshots, SQLite journals, temporary exports and backups need the same
retention treatment. Do not auto-delete unrelated account recordings.

## Rollback and verification

1. Disarm new collection calls. Finish or hang up the current call. Restore the
   snapshot's number URLs/methods/status callbacks and original association,
   including empty original values. If a trunk was active, reattach that original
   trunk; if an application was active, reattach that original application.
   Shared application/trunk resources should never have changed.
2. Read back the affected number fields and compare the restore projection,
   accounting only for documented null/empty normalization. Recheck region routing
   if applicable. Do not overwrite concurrent operator changes blindly: stop on
   unexpected config drift and ask which version to restore.
3. Make a normal Dogwalk test call after explicit permission. Confirm normal
   routing, not just successful API update. Restoring a number does **not** migrate
   an in-progress collector call or its future recording callbacks.
4. Keep collector/tunnel alive until completed media are reconciled/exported and
   delayed callbacks have been handled, then shut them down. If the tunnel died,
   restore Twilio first, recover recordings privately via REST using known call IDs.
   Reconcile all six attempted rounds, including absent or interrupted ones.
5. Delete provider media and local data according to the agreed schedule. Stop
   the separate ephemeral service/tunnel; no production Worker rollback is needed.

Main risks: wrong number/account/region, ignored VoiceUrl due to higher-precedence
association, stale snapshot, implicit SID clearing, unexpected callers, old
fallback/status traffic hitting production, laptop sleep, missing callbacks, and
forgetting to restore routing or delete recordings. The fix is a short attended
window, private before/after snapshots, a prepared restore payload, and read-back.

## Charges and blockers

Public US list rates observed on the Twilio pricing page: inbound local calls
$0.0085/min, recording $0.0025/min, storage $0.0005/recorded min/month.
For a 3-minute local inbound call with at most 1.2 recorded minutes, approximately
$0.029 before tax, rounding, account-specific rates, or any existing CNAM lookup
charge. Per-recording billing increments can increase that estimate; budget under
$0.10 for one short local test rather than promising an exact price. Toll-free or
non-US rates differ. No new number, OpenAI, Daytona, transcription or speaker-model
charge is required. The existing number's normal monthly rent continues. Use basic
Say, not paid premium/generative voices. A paid tunnel/hosting tier is not required
for the local attended proof, subject to provider availability/terms.

Blockers before activation: authorized credential path, actual live number and
effective routing snapshot, account media privacy verification, approved separate
HTTPS endpoint, implementation and offline signature/callback tests of its adapter
and exporter, agreed retention/call window, and explicit permission for Twilio
switch plus restore. Neither credentials nor live state have been inspected here.

Next action: approve the approach, then implement/test the isolated endpoint and
exporter and perform **read-only** routing/privacy preflight. Report the exact
private field diff before requesting approval to switch. Do not deploy or mutate
Twilio merely because the offline TwiML proof passes.

## Primary sources checked

- https://www.twilio.com/docs/voice/twiml/record : caller recording, mono,
  action vs status callback, beep, silence, trim, finish-key clipping.
- https://www.twilio.com/docs/phone-numbers/api/incomingphonenumber-resource :
  application/trunk precedence, mutual SID clearing, updateable voice settings.
- https://www.twilio.com/docs/voice/api/recording : authenticated WAV downloads,
  completed-media requirement, deletion and 40-day deleted metadata retention.
- https://www.twilio.com/docs/usage/security : HTTPS, official SDK signature
  validation and the conflicting opt-in media authentication guidance.
- https://www.twilio.com/en-us/voice/pricing/us : US list voice/recording/storage rates.
