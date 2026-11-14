# Dogwalk Cloudflare Worker

This Worker is the production Dogwalk service at `https://dogwalk.tools`. It
provides Twilio PSTN voice, phone registration, Daytona sandbox lifecycle,
Managed Session multiplexing, recovery, and a read-only Diagnostics surface.

This is a public repository for Adam's operator-managed deployment. Public
hostnames, provider resource IDs, Keychain service names, and local secret-lookup
commands may be documented. Secret values, `.env`, `.dev.vars`, private keys,
capability tokens, signed preview URLs, and sensitive production payloads must
never be committed.

## Setup

Wrangler stores deployed Worker secrets remotely. Adam's laptop also keeps the
operator environment in the ignored repository-root `.env`; Daytona's token is
available through the ignored `~/.tokens/daytona-api` file. These locations are
safe to document, but their contents are not.

```bash
npm install
npm run db:init:remote
npx wrangler secret put TWILIO_AUTH_TOKEN
npx wrangler secret put DAYTONA_API_KEY
npx wrangler secret put DOGWALK_IDENTITY_SECRET
npx wrangler secret put OPENAI_API_KEY
npx wrangler secret put OPENAI_WEBHOOK_SECRET
npx wrangler secret put ADMIN_PASSWORD
npm run deploy
```

`DOGWALK_IDENTITY_SECRET` should be a stable random value. Changing it prevents
recovery by provider label for assignments that have not yet stored a Daytona
ID.

Set `OPENAI_PROJECT_ID` in `wrangler.toml`. In the matching OpenAI project,
subscribe `https://dogwalk.tools/openai/live` to `live.transport.incoming` and
store its `whsec_...` signing secret as `OPENAI_WEBHOOK_SECRET`.

`DAYTONA_SNAPSHOT` is currently `dogwalk-acp-20261108-v2` (see `wrangler.toml`). It contains the ACP
Gateway, OpenCode `2.2.5`, tmux and Daytona's computer-use desktop (Xvfb, xfce4,
x11vnc, noVNC, Chromium with `--no-sandbox`) for Human Takeover, passwordless sandbox-local `sudo`, and network
diagnostic utilities. The OpenCode config selects `openai/gpt-6.1-sol`. `build_sandbox_snapshot.py` now
builds `dogwalk-agent-20261112-v1` instead: OpenCode `2.2.7` and the Agent Gateway, which
supervises the shared OpenCode service and serves its native API rather than ACP. The
Worker on this branch speaks only that API, so adopting it is an ordered cut-over (below).

From the repository root, build a new immutable version rather than replacing
this snapshot in place:

```bash
set -a; source ~/.tokens/daytona-api; set +a
uv run --script build_sandbox_snapshot.py --name dogwalk-agent-YYYYMMDD-vN
```

Verify the new snapshot before changing `wrangler.toml` and deploying the Worker.

The Worker submits this mapping at sandbox creation:

```json
{
  "snapshot": "configured-snapshot-name",
  "secrets": [{ "OPENAI_API_KEY": "dogwalk-openai" }]
}
```

`dogwalk-openai` is an org Daytona Secret allowlisted to `api.openai.com`,
named by `DAYTONA_AGENT_SECRET`. The Worker sends only its name. Daytona's
egress proxy substitutes the value into HTTPS headers, so the Sandbox holds a
`dtn_secret_` placeholder; see `../SANDBOX_AGENT_GATEWAY.md`. A Sandbox created
before the secret existed must be replaced (or restarted after
`update_secrets`) to receive it.

If a sandbox is deleted directly in Daytona, D1 temporarily retains a stale
provider ID. On the next registered call, a Daytona 404 atomically clears that
reference and starts idempotent replacement provisioning. The Registration is
retained; the deleted sandbox's files and ACP Session history are not.

Normal user sandboxes are not disposable. They auto-stop after 15 idle minutes,
auto-archive after 7 days, and never auto-delete in Daytona. A call starts or
restores that same Sandbox, preserving its filesystem and Agent-held session
history until Dogwalk reaps it (below). Replacement otherwise occurs only after
explicit provider-side deletion or the caller's confirmed recovery replacement.

### Sandbox Reaping

A daily cron pass (`17 10 * * *`, `SANDBOX_REAP_CRON` in `src/index.ts`) looks
for Sandbox Assignments whose User has made no Voice Call for
`SANDBOX_REAP_AGE_DAYS` (default 30, minimum 1; the assignment's creation counts
when there are no calls) and none in progress. It handles at most ten per pass,
oldest first. `SANDBOX_REAP_MODE` is `delete`. Any other value (such as
`dry-run`, the mode reaping first shipped in) makes the pass only audit
`sandbox.reap.candidate`. The pass audits nothing when it finds nothing; to see
that it ran, look for the `17 10 * * *` trigger in Workers Logs.

In `delete` mode, reaping is one D1 batch that rechecks idleness (counting an
in-progress call from the User's Home that `/voice` has not yet linked): it
inserts the Sandbox into `sandbox_deletions` and clears the assignment
(`provider_id` NULL, state `reaped`), auditing `sandbox.reap.started`. The old
Sandbox is out of service from that moment: the User's next call provisions a
fresh one through the ordinary path, and discovery by label skips every
provider ID in `sandbox_deletions` (and anything Daytona lists as destroying or
destroyed). A call never deletes anything.

Only the scheduled pass sends Daytona DELETE. In the same pass, and every later
one, it works through due `sandbox_deletions` rows (20 per pass, oldest due
first). On success or 404 it sets `deleted_at` and audits `sandbox.reaped` once
(`already_gone`, `attempts`). On failure it audits `sandbox.reap.failed`, counts
`attempts`, and backs off (`next_attempt_at` 1, 2, 4, 8, then 16 days on). At
the third failure it also audits `sandbox.reap.stuck` for operator attention,
and keeps retrying. A stuck Sandbox never returns to service. Rows stay after
deletion: as tombstones, because Daytona's label listing is eventually
consistent, and as the notice the User's next Opening consumes (`noticed_at`),
which says the sandbox was cleared exactly once, independent of when the
deletion finished. The VoiceSession drops the old projection when the fresh
Sandbox arrives, as on replacement; its Memory stays. Every assignment refresh
from Daytona (`updateAssignment`) writes only while the assignment still names
the Sandbox it read, so a slow diagnostics refresh cannot restore a reaped or
replaced Sandbox.

Apply `migrations/2026-11-12-sandbox-deletions.sql` remotely before deploying
reaping:

```sh
npx wrangler d1 execute dogwalk --remote --file migrations/2026-11-12-sandbox-deletions.sql
```

Deletions not yet confirmed:

```sh
npx wrangler d1 execute dogwalk --remote --command \
  "SELECT provider_id, user_id, attempts, datetime(next_attempt_at, 'unixepoch') AS next, last_error
     FROM sandbox_deletions WHERE deleted_at IS NULL"
```

Recent reaping, candidates included when in a dry run:

```sh
npx wrangler d1 execute dogwalk --remote --command \
  "SELECT datetime(ts, 'unixepoch') AS at, event, detail FROM audit_log
    WHERE event LIKE 'sandbox.reap%' ORDER BY id DESC LIMIT 50"
```

Each row's `detail` names `user_id`, `provider_id`, `last_call_at`, and
`idle_days`. Join `user_id` to `users.handle` to recognize whose Sandbox it is.
To pause deletion, set `SANDBOX_REAP_MODE = "dry-run"` under `[vars]` in
`wrangler.toml` and deploy (or change the variable in the Cloudflare dashboard,
which the next `wrangler deploy` overwrites with the file's value). A reap
already started stays started. A pause applies to passes that start after the
deploy: one already running keeps its `delete` mode until it finishes, and
Daytona completes deletions it has accepted. Later passes start no reaps and
send no deletions, and resume pending deletions once `delete` is back. In production a pass
runs only on that schedule, so the first candidates appear after the next 10:17
UTC following a deploy. Locally, `wrangler dev --test-scheduled` exposes
`/__scheduled?cron=17+10+*+*+*`, which the smoke test uses.

## Deploying Changes

From the repository root, `uv run --script deploy.py` deploys with the `cf`
`personal` profile and then refreshes the VoiceSession Durable Object. A Durable
Object keeps running its starting code while it holds the outbound ACP
WebSocket, so a plain `wrangler deploy` leaves calls on the old version. The
script waits until no call is in progress, then stops the Sandbox so the object
reloads; stopping it mid-call would leave the caller without dogs while the
object, still on old code, reconnects. `--no-deploy` performs
only the refresh.

A deploy is refused unless `DAYTONA_SNAPSHOT` names a `dogwalk-agent-*` snapshot and
every managed Sandbox either was created from one or carries the
`dogwalk.dev/agent-api=native` label that an in-place migration sets; an ACP-era
Sandbox would fail every call. `--allow-legacy-sandboxes` overrides the second check
(it still warns), for example when the operator's Sandbox is about to be deleted anyway.

### Cutting Over to the Native API

The Worker talks to OpenCode's native API through the Agent Gateway's `/events` and
`/api/*`. An ACP-era Sandbox (`dogwalk-acp-*`) boots `sandbox_acp_gateway.py` with
`opencode acp` from its image entrypoint and serves neither, so every Sandbox must move
before the Worker does. Run these in order, from the repository root unless noted, with
no call in progress. From step 2 to step 5 calls fail in both directions (the old Worker
cannot talk to a migrated Sandbox either), so do steps 2–5 in one sitting:

1. Build and verify the snapshot. The build runs the gateway self-test and starts
   `opencode serve --service` once to check `/api/info` reports `2.2.7`:

   ```bash
   set -a; source ~/.tokens/daytona-api; set +a
   uv run --script build_sandbox_snapshot.py --name dogwalk-agent-20261112-v1
   ```

2. Migrate each existing Sandbox in place. A dry run first, then apply:

   ```bash
   uv run --script deploy.py --no-deploy --patch-sandboxes          # lists what would change
   uv run --script deploy.py --no-deploy --patch-sandboxes --apply
   ```

   Since the image entrypoint cannot change, the patch uploads OpenCode `2.2.7` (fetched
   on the laptop, so the Sandbox needs no egress to GitHub) and renames it over
   `/usr/local/bin/opencode`, writes the Agent Gateway and the other snapshot files,
   and replaces `/opt/dogwalk/sandbox_acp_gateway.py` with a shim that execs the Agent
   Gateway, mapping `opencode acp` to `opencode serve --service`. It checks the gateway's
   self-test, the new OpenCode serving `/api/info` under a scratch HOME (the live
   database is left to the new version's first real start), and the shim; any failure
   restores every file and the old binary from the `.dogwalk-backup-<stamp>` copies.
   Success labels the Sandbox `dogwalk.dev/agent-api=native`, and the refresh stops it
   so the next start boots the shim. The Workspace, `~/.local/share/opencode` (session
   history), Terminal state, and the Sandbox's Daytona identity all survive.
   It keeps the old image's other packages, which matched this snapshot at the cut-over.

   Recreation is the fallback when a migration fails twice: delete the Sandbox in
   Daytona and let the next call provision one from the new snapshot. That loses its
   Workspace files and all Agent-held session history; the User's Memory, Registration,
   and Voice Donations live in Cloudflare and stay.

3. Apply the D1 migration to remote D1, immediately before the deploy. It narrows
   `call_activity.source` to exclude `acp`, which the old Worker still writes, so do not
   leave a gap where the old Worker serves calls:

   ```bash
   (cd cf_worker && npx wrangler d1 execute dogwalk --remote --file migrations/2026-11-17-agent-activity.sql)
   ```

4. Set `DAYTONA_SNAPSHOT = "dogwalk-agent-20261112-v1"` in `wrangler.toml`, so Sandboxes
   created from now on start from the new snapshot.

5. Deploy: `uv run --script deploy.py`. It refuses if step 2 or 4 was skipped, then
   refreshes the Durable Object.

6. Verify with a call: the Opening, `agent_connected` in the call timeline, a short
   Prompt Turn that reads a Workspace file, and a session from before the cut-over
   still listed. `uv run --script playtest.py` shows `agent.*` activity for that call.
   In the Sandbox, `curl -s localhost:8765/healthz` should report `"service": "resolved"`
   with a `child_pid`.

To roll back before step 3, restore the backups (`cp -p` each `.dogwalk-backup-<stamp>`
file over its original, `sudo` for the binary), remove the label, and stop the Sandbox.
After step 3, the old Worker also needs the migration reversed, which has no script.

## Invite Codes

Invite Codes are maintained directly in D1. Omit `expires_at` for no expiration
and `max_uses` for unlimited registrations.

```sql
INSERT INTO invite_codes (code_words, expires_at, max_uses)
VALUES ('three pgp words', unixepoch('now', '+7 days'), 5);
```

Registered Users can also create Invitations by voice (`src/invitations.ts`):
each is an `invite_codes` row with `max_uses = 1` and a 24-hour expiry, plus an
`invitations` row naming the inviter, an optional bound recipient, and
revocation, hiding, and redemption times. A User holds at most three active
Invitations. Operator codes are unaffected. Nothing is deleted; trace the
invitation graph in `/admin` under Access Control. Select a User to see
who invited them and the Invitations they created, or select an Invitation to
follow its inviter and registered invitee. Hidden and revoked history remains
visible. The default view masks all phone numbers and omits Invite Code words;
the operator's verbose toggle reveals them. This is read-only and unavailable
to Paired Browsers. The same graph can be queried with:

```sql
SELECT inviter.handle AS inviter, v.code_words, invitee.handle AS invitee,
       v.redeemed_from, invitee.registered_at, v.recipient_phone, v.revoked_at, v.hidden_at
  FROM invitations v JOIN users inviter ON inviter.id = v.inviter_user_id
  LEFT JOIN users invitee ON invitee.id = v.redeemed_by_user_id
 ORDER BY inviter.handle, invitee.registered_at;
```

## Users, Handles, and Homes

A User (`users`) is a two-word Handle (`src/handles.ts`), an opaque
`identity_hash` that keys their VoiceSession Durable Object
and their Daytona label `identity-v1:<hash>`, Registration provenance, and at
most one Home number. Registration assigns a random Handle and speaks it twice.
An unexpired Home (used within 90 days) selects the User, and the shadow Identity
Check (below) runs before the Voice Agent but never withholds entry;
`voice_calls.user_id` records which User a call reached. `voice_references` holds
enrolled speaker embeddings (never audio). The `SPEAKER` Service Binding reaches
the separately deployed `dogwalk-speaker` Worker (`../speaker_worker/`), which
scores audio against them; operator-only `/admin/api/voice-verify` routes expose
it for calibration and answer 503 when it is unbound or unreachable. Local tests
bind `speaker_mock.js` in its place (see Verification). A Home call, and each
challenge take, warm the verifier in the background (`warmSpeaker`, a `waitUntil`
that never delays TwiML and drops every failure), so a later challenge skips the
3–5 s model load. Warmth belongs to one machine, so a User's warms and scores all
go through their VoiceSession Durable Object (`userSpeaker` and `hostSpeaker` in
`src/voice_verify.ts`, routes `/speaker/warm|embed|verify`): a Durable Object
stays where it was created, and a Service Binding call runs on its caller's
server, so the instance warmed at call arrival is the one that scores. Each
hosted score calls `warm()` first and reports `cold`, which `identity.round` and
`practice.round` log.

`migrations/2026-11-11-users.sql` converted the phone-keyed `registrations`
table, keeping each existing `identity_hash` so Durable Objects, Sandboxes, and
pairing tokens survived. It was followed by a one-time operator back-patch, kept
out of Git because it names Handles: relabel each Sandbox from
`phone-hmac-v1:` to `identity-v1:` (Daytona's label PUT replaces all labels, so
read and write back the rest), assign Handles to migrated Users, and store the
operator's Voice Reference.

## Voice Donations

Any caller can donate engineering voice samples (DOMAIN.md, Voice Research)
without an account: an unregistered caller says "donate" at the registration
prompt, and a User asks the Voice Agent, whose `start_voice_donation` tool leaves
a `donation` handoff so `/voice/after-live` redirects to `/voice/donation`. That
route speaks the consent script (`CONSENT_VERSION` in `src/voice_donation.ts`;
change the version with the words) and records nothing without a spoken yes.
Each round then starts a caller-only `<Start><Stream>` to
`wss://dogwalk.tools/voice/donation/stream/<capability>`, plays
`/voice/donation/tone.wav`, and says the prompt inside a speech `<Gather>` whose
action (`/voice/donation/round`) keeps the transcript, stops that stream, and
starts the next round. Eight challenge phrases (`src/challenge_phrases.ts`, the
generator the Identity Check uses) and two free-speech prompts make about
three minutes. A short "stop" ends it, keeping what was recorded. Never `<Record>`.

The stream upgrade must carry a valid Twilio signature (over its wss URL) and an
unexpired HMAC capability (`DOGWALK_IDENTITY_SECRET`) naming one round of one
session and the Twilio account that requested it; the start event must match
that account, session, round, and 8 kHz mono mu-law. The Worker itself holds the
WebSocket for the round, buffers frames in memory (at most 90 seconds), and on
stop or close writes `round-NN.wav` (8 kHz mono 16-bit PCM decoded from what
Twilio sent, lost frames filled with silence and counted) to the private R2
bucket `dogwalk-voice-research` (binding `RESEARCH_AUDIO`), then records the
stream in `session.json` with a conditional put, as the round webhooks do.
Layout: `donations/YYYY-MM-DD/<session id>/{session.json,round-NN.wav}`. Nothing
about a donation goes to D1 except Call Activity events (`donation.*`) that never
name the session; the bucket stores no phone number, CallSid, User, or Handle.

From the repository root, with the personal Cloudflare profile:

```bash
uv run --script voice_research.py list                  # time, session id, outcome, rounds, size
uv run --script voice_research.py pull [SESSION ...]    # default: everything not yet pulled
uv run --script voice_research.py delete SESSION --yes  # a donor asked; removes the local copy too
```

Pulls land in `~/Library/Application Support/Dogwalk/voice_research/<date>/<session>/`
(mode 0700, files 0600, outside Git). A donor asking for deletion names roughly
when they called; match it against `list`. Annotate speaker and microphone
beside the pulled copy, not in the bucket.

## Challenge Practice

A User asks the Voice Agent to "challenge me" or "test my voice" (DOMAIN.md,
Challenge Practice); its `start_challenge_practice` tool leaves a `practice`
handoff, so `/voice/after-live` redirects to `/voice/practice`
(`src/challenge_practice.ts`). That route checks for the call's User's Voice
Reference (`voice_calls.user_id`) and the `SPEAKER` binding, returning to `/voice`
with a spoken reason when either is missing, and starts round one. Rounds come
from the engine Challenge Practice shares with the Identity Check
(`src/challenge_rounds.ts`; practice keeps only its words and limits). Each round
inserts a transient `challenge_rounds` row with a fresh phrase, starts a caller-only
`<Start><Stream>` to `/voice/practice/stream/<capability>` (the same signed,
per-round machinery as Voice Donations, in `src/caller_stream.ts`), plays the
tone, and says the phrase inside a speech `<Gather>`. Its action,
`/voice/practice/heard`, checks the transcript first (`phraseMatches` in
`src/challenge_phrases.ts`, shared with the Identity Check): every
content word in order, plurals forgiven, insertions allowed, or the round is a
wrong phrase and is not scored. Every phrase Gather (donation rounds too) passes
the phrase's content words as speech `hints`. A short "repeat", "say that
again", "again", "what", or "pardon" replays the phrase as a new take (its own
stream and capability, `challenge_rounds.take`), up to three times per round,
without counting it; a fourth request moves to a fresh phrase (`repeat_limit`). A captured answer, matched or a wrong phrase (never
silence, "stop", or a repeat request), first plays the received cue,
`/voice/practice/received.wav` (`answerReceivedCue` in `src/caller_stream.ts`:
C6 for 70 ms, 30 ms of silence, G5 for 110 ms, 8 kHz mono, public and cached a
day like the tone), so the caller hears their answer arrive instead of four to
seven seconds of silence before the level; on a match it plays while the
verifier scores. Scoring is speculative: the stream's `AnswerEndpointer`
(`src/caller_stream.ts`) watches 20 ms frames and, when at least a second of
speech (energy over both the line's noise floor plus 15 dB and 40 dB absolute)
is followed by 700 ms of silence, sends the in-memory 8 kHz PCM so far (its last
30 s, `MAX_VERIFY_SECONDS`, the verifier's limit) to `SPEAKER.verify` at once,
storing the result in the row's `spec_` columns. Talking again makes that score
stale, and the next end starts a fresh one; only the latest is kept (`spec_seq`
orders them), and a take starts at most four, after which the stop scores the
whole stream. A match
marks the row `scoring` (with `heard_ms`) and stops the stream; the stream's
stop event then uses the latest speculative score if the caller has not talked
since it started, or else scores the whole stream as before, and writes back
only the cosine and speech seconds, while `/voice/practice/result` waits up to
10 seconds for them. A wrong phrase, silence, or repeat request clears the
`spec_` columns, and a score landing later is dropped, so it is never used,
spoken, or logged. Under 1.5 seconds of speech, or the verifier's
`insufficient-audio`, earns another phrase; otherwise the caller hears "Strong
voice match." (cosine at least `STRONG_MATCH_COSINE`, 0.7), "Voice matched."
(`MATCH_COSINE`, 0.5), or "Voice not matched.", never the number. A short "stop",
ten scored rounds (unjudged rounds do not count; twenty attempts in all), two
silent rounds, or a verifier error ends practice and redirects
to `/voice`, reconnecting the Voice Agent. Audio is never written anywhere; each
round's row is deleted once spoken, and Call Activity keeps `practice.round`
events for every round (`outcome`: `strong_match`, `match`, `no_match`,
`not_enough_speech` with the score when the verifier gave one, `wrong_phrase`,
`repeat_limit`, `silent`, or `unscored`; `cosine` and `speech_seconds` when available; on every
round that waited for a score, `wait_ms`, `speculative`, and, when the
speculative score was used, `head_start_ms`, from its start to `/heard`) plus
`practice.started`, `practice.repeat`, `practice.ended`, and
`practice.unavailable`.

On every return to `/voice` (and on `practice.error`), practice writes
`voice_calls.returned_from` as `{"flow": "practice", "outcome": <the
practice.ended reason, "unavailable" with its reason, or "error">}`. The Voice
Session reads and clears it when the Voice Agent reconnects, and `call_started`
carries it as `returned_from`, so the Session Orchestrator knows how the flow it
handed the call to ended. Outcome names only: never a level, score, or phrase.
Re-enrollment does the same with `"flow": "reenrollment"` and its
`reenrollment.result` outcome. Voice Donation ends the call, so it never
returns. Apply `migrations/2026-11-15-returned-from.sql` remotely before
deploying this:

```bash
npx wrangler d1 execute dogwalk --remote --file migrations/2026-11-15-returned-from.sql
```

## Identity Check (shadow mode)

The Identity Check (DOMAIN.md) runs in `src/identity_check.ts` on the round engine
of `src/challenge_rounds.ts`, so it has Challenge Practice's shape and rules:
tone, phrase, answer, received cue, level; strict `phraseMatches` with hints;
replays on request; speculative scoring from the endpointer; a wrong phrase is
never voice evidence; too little speech earns another phrase without counting.
Shadow mode means every outcome connects a Home caller as before; the record says
what enforcement would have done.

- **Home** (`/voice`, a caller whose number is an unexpired Home): once per Voice
  Call (`voice_calls.identity` set means done; returning to `/voice` after
  practice, a Theme switch, or recovery skips it), `/voice` speaks "Dogwalk, for
  <Handle>." and one challenge, while the Sandbox wakes in `waitUntil`
  (`wakeSandbox`). Full instructions until `users.challenges_completed` reaches
  `TERSE_AFTER` (3), then "Your sentence." The result speaks the level and
  redirects to `/voice`, which dials the Voice Agent. Stop, two silences, a
  verifier error, or four rounds without a judgment also connect.
- **Name path** (`HOME_NAME_WINDOW_HOURS` in `wrangler.toml`, `"24"`; `"0"` or
  unset turns it off). When another `voice_calls` row with this `user_id` (a Home
  or Roaming-admitted call, from any phone) ended, or was last active, within the
  window, `/voice` says "Dogwalk, for
  <Handle>." and round 1 is a name round (`mode=name` in its webhook URLs): its
  phrase is the Handle, hinted, and the Gather says "Say: this is <Handle>."
  (a carrier phrase that lengthens the speech). The match needs only the Handle's
  words in order, so "this is", "it's", or the bare Handle all match. A matched
  name plays the received cue, scores like any challenge, and connects with no
  level spoken and no second try, whatever the score or speech seconds;
  `challenges_completed` does not move. A wrong name ("I didn't catch your
  name."), silence, or a replay limit falls back to one sentence (round 2), led
  by "Your sentence." or the full instructions per `TERSE_AFTER`. Escapes work
  as from a sentence. The real verifier refuses under 3.5 s of audio or 1 s of
  measured speech, so a bare Handle (production's first was 0.88 s) records
  `not_enough_speech` with `insufficient-audio`; the refusal's measured `speech_seconds` is kept, and a
  speculative refusal is never used over a score of the whole stream.
- **Classification** (`/voice/identity/heard`, after the phrase check): two
  adjacent Handle words that are a User's Handle start that User's challenges; an
  Invite Code (three PGP words) goes straight to its readback, and a mention of an
  invite to "Say your three invite words" (`/voice/claim`); "donate" redirects to
  `/voice/donation`; "someone else" asks who (`/voice/identity/who`, one retry,
  then a polite end). Naming the Handle being checked replays the sentence.
- **Roaming** (`path=roaming`): three challenges against the named User's Voice
  Reference, each level spoken, then "Calling as yourself from another phone is
  coming soon" and a hangup; the caller is never admitted as that User. A named
  User without a Voice Reference ends at once.
- **Roaming admission** (`ROAMING_ADMISSION = "on"` in `wrangler.toml`; anything
  else keeps the shadow behavior above, byte for byte). The unregistered greeting
  asks for "your two-word Dogwalk name, or your three invite words", and
  `/voice/claim` starts Roaming on a Handle (`startRoamingFromGreeting`) before
  matching an Invite Code; a retry offers both. `startRoaming` first checks
  `roaming_attempts` for a pause (3 failed attempts as the Handle, or 5 from the
  number, in 24 hours; the caller hears which and the call ends with `outcome:
  "paused"`), then claims the Handle in one statement: it records the attempt
  unless another call's attempt on that Handle is in progress (its `voice_calls`
  row has no `identity` and no `ended_at`, within 10 minutes), which ends the call
  with `outcome: "busy"`; an attempt past those 10 minutes ends at its next result with `outcome: "expired"`, no failure and no admission. Only a call with a recorded attempt is enforced (a call
  begun before the flag turned on stays shadow). The first `no_match` increments
  `failures` and hangs up ("I can't let you in as <Handle> this time"); three
  matches recheck the pause, then set `voice_calls.user_id`, mark `admitted_at`,
  store `identity` with `admitted: true, shadow: false`, say "Welcome, <Handle>.",
  and redirect to `/voice`, where `admittedRoamingUser` wins over the number's Home
  (only `last_seen_at` is touched, never the Home). The User's next call not
  admitted by Roaming, first calls included, speaks a counted notice after the
  caller-ID clause and sets `noticed_at` once that Opening is sent. Rows older than
  90 days are pruned when an attempt starts.
- **Enrollment** (`path=enroll` on a Home call from a User without a Voice
  Reference; `path=register` at the end of Registration, when the confirm webhook
  carries an `AccountSid` and `SPEAKER` is bound): the shape first (`enroll`:
  "One step first, about a minute: 5 short sentences ..."; `register`, before
  the Handle: "Two steps left, about two minutes: your Dogwalk name, then 5
  short sentences ..."), an explanation, then five
  matched answers embedded with `SPEAKER.embed` (no level spoken; under 1.5 s of
  speech earns another phrase), kept as transient `challenge_rounds.embedding`
  until the end. "Two more." and "Last one." (`enrollmentMark`, also used by
  Re-enrollment) follow the third and fourth collected answers, never a round.
  The Voice Reference is the mean of the L2-normalized embeddings,
  re-normalized, in the embedding space most answers share; fewer than three
  stores nothing, and `ON CONFLICT DO NOTHING` never replaces an existing one.
  Stop skips it until the next call.
- **Invitee onboarding** (`src/invitations.ts`): when a readback matches an
  Invitation, `invitations.onboarding_call_sid` records that call (and the
  redemption rebinds it to the call that registered); Registration's
  enrollment updates `enrollment_answers` per collected answer and
  `enrollment_outcome` when the check concludes (`unavailable` when it cannot
  start, `error` for a verifier or webhook error; a hangup leaves it NULL). Once that call has a terminal status, or the
  invitee has a Voice Reference, the inviter's next VoiceSession attach puts one
  sentence per invitee in `call_started.invitees` (`handle`,
  `invited_days_ago`, `how_it_went`; Invitations from the last 14 days, not
  revoked or hidden) and sets `onboarding_noticed` to that sentence once a model
  step that read that `call_started` succeeds (`runOrchestrator`), so each change
  is told once and a failed step leaves it for the next call. Apply
  `migrations/2026-11-16-invitee-onboarding.sql` remotely before deploying
  this (`npx wrangler d1 execute dogwalk --remote --file
  migrations/2026-11-16-invitee-onboarding.sql`): the readback writes the new
  columns, and a failed write is only logged, so an unmigrated database still
  registers callers but tells inviters nothing.

Without `SPEAKER` or an `AccountSid` (local dev, most tests), `/voice` records
`identity` as `unavailable` and connects at once. An exception in an identity
webhook, or in starting the check from `/voice`, records `error` and connects a
Home path (Roaming and Registration paths hang up; a Registration whose
enrollment cannot start still speaks the Handle). Webhook URLs carry `path`,
`user`, progress, and `start` (the round before the path began, so a path entered
mid-call gets its own round budget); Twilio signs them. A call's rounds, and with
them any enrollment embeddings, are deleted whenever a path ends, on an error,
and when the call's terminal status arrives.

Call Activity (source `access`): `identity.started` (`path`, `user_id`, `terse`),
with `mode` (`name` or `sentence`) on every Home record and round below (except
`unavailable`, recorded before a mode is chosen),
`identity.round` (`path`, `round`, `outcome`, `cosine` or for enrollment none,
`speech_seconds`, `cold`, `wait_ms`, `speculative`, `head_start_ms`),
`identity.repeat`, `identity.escape` (`to`), `identity.who`, `identity.result`
(the stored record: `path`, `user_id`, `outcome`, `level`, `enforcement` of
`admit`, `require_roaming`, `admit_roaming`, `deny`, or `undecided`, `shadow`),
and `identity.error`. Never a transcript, phrase, embedding, or audio. The
VoiceSession reads `voice_calls.identity` into `call_started` as `identity`
(`via`, `mode`, `outcome`, `level`, `would_have`, `shadow`, `enrolled_now`) for the
Session Orchestrator; the spoken Opening does not change.

Apply `migrations/2026-11-13-identity-check.sql` remotely before deploying this
(it recreates the transient `challenge_rounds` from `practice_rounds`, and adds
`users.challenges_completed` and `voice_calls.identity`); run it while no practice
or check is in progress:

```bash
npx wrangler d1 execute dogwalk --remote --file migrations/2026-11-13-identity-check.sql
```

## Re-enrollment

A User asks the Voice Agent to re-enroll their voice (DOMAIN.md, Re-enrollment);
its `start_reenrollment` tool leaves a `reenrollment` handoff, so
`/voice/after-live` redirects to `/voice/reenrollment` (`src/reenrollment.ts`).
Without a Voice Reference it says there is nothing to replace and returns to
`/voice`; without the `SPEAKER` binding it says it is unavailable. Otherwise it
runs on the round engine of `src/challenge_rounds.ts`, with the Identity
Check's streams and cues (`/voice/identity/stream`, `tone.wav`,
`received.wav`) and its own Gather actions, `/voice/reenrollment/heard` and
`/voice/reenrollment/result`, whose URLs carry `phase` (`check` or `enroll`),
`silent`, `judged`, `start`, and `ref`, a digest of the Voice Reference the check
began against: if another call replaces it meanwhile, the next webhook ends this
ritual unchanged (`lost_reference`).

- **check:** `identity` rounds against the current reference, as many as
  Roaming (`ROAMING_CHALLENGES`, 3), each at least `MATCH_COSINE`. A `no_match`
  ends it at once ("Voice not matched. Your voice profile is unchanged."). This
  is enforced regardless of shadow mode: it guards the credential itself.
  Wrong phrases, repeats, and thin speech behave as in the Identity Check.
  Eight rounds bound the phase.
- **enroll:** `enrollment` rounds, as in first-call enrollment
  (`ENROLL_ANSWERS`, 5; at least `MIN_ENROLL_ANSWERS`, 3, with 1.5 s of speech,
  via `collectedAnswers` in `src/identity_check.ts`), starting after the check's
  last round with their own budget of ten. The mean embedding must be in the old
  reference's embedding space and have a cosine of at least `MATCH_COSINE` with
  it, or nothing changes (`discontinuous`). The `UPDATE` of `voice_references`
  is conditioned on the old embedding, so a concurrent change is never
  overwritten.

Every end (stop, two silent rounds, a verifier error, running out of rounds,
too few answers) deletes the call's `challenge_rounds` and returns to `/voice`
with "Your voice profile is unchanged." unless it was replaced. Call Activity,
source `access`: `reenrollment.unavailable`, `reenrollment.started`,
`reenrollment.round` (`phase`, `round`, `outcome`, `cosine` in the check,
`speech_seconds`, `cold`, `wait_ms`, `speculative`, `head_start_ms`),
`reenrollment.repeat`, `reenrollment.checked`, `reenrollment.result`
(`outcome`: `reenrolled`, `failed`, `discontinuous` with `continuity`,
`enrollment_failed`, `stopped`, `silent`, `unscored`, `undecided`,
`lost_round`, or `lost_reference`), and `reenrollment.error`. Never a
transcript, phrase, embedding, or audio. `voice_calls.identity` is untouched:
it records how the call was admitted. The outcome also goes to
`voice_calls.returned_from` for the reconnected Voice Agent's `call_started`
(see Challenge Practice).

Apply `migrations/2026-11-14-reenrollment.sql` remotely before deploying this (it
rebuilds `call_handoffs` to allow `reenrollment`, copying any pending handoff):

```bash
npx wrangler d1 execute dogwalk --remote --file migrations/2026-11-14-reenrollment.sql
```

Roaming admission needs `roaming_attempts`; the table is written only while the
flag is on, so the migration is safe to apply ahead of turning it on:

```bash
npx wrangler d1 execute dogwalk --remote --file migrations/2026-11-14-roaming-admission.sql
```

To turn admission on, set `ROAMING_ADMISSION = "on"` in `wrangler.toml` and
deploy; set it back and deploy to return to shadow Roaming. Attempts already
recorded keep pausing Handles and numbers only while the flag is on.

## Verification

The local smoke test runs a fake Daytona lifecycle and toolbox server:

```bash
npm run typecheck
uv run --script smoke_test.py
uv run --script voice_session_test.py
uv run --script admin_test.py
uv run --script ../sandbox_agent_gateway.py test
node --experimental-strip-types challenge_phrases_test.ts
node --experimental-strip-types voice_verify_test.ts
node --experimental-strip-types endpointer_test.ts
node --experimental-strip-types operator_content_test.ts
node --experimental-strip-types invitations_match_test.ts
```

The local suites listen on 8787 (Worker), 8790-8792 (mocks), and 10229 (inspector).
To run them in several worktrees at once, give each its own base, for example
`DOGWALK_TEST_PORT_BASE=8800 uv run --script voice_session_test.py`; ports are
the base, base+3 to base+5, and base+1442. Each worktree keeps its own local D1.

The smoke test's Voice Donation checks use wrangler dev's local R2. A local D1
created before `migrations/2026-11-11-call-handoff-donation.sql`,
`migrations/2026-11-12-challenge-practice.sql`,
`migrations/2026-11-12-speculative-scoring.sql`,
`migrations/2026-11-12-spoken-pairing.sql`,
`migrations/2026-11-13-identity-check.sql`,
`migrations/2026-11-14-reenrollment.sql`,
`migrations/2026-11-14-roaming-admission.sql`, or
`migrations/2026-11-16-invitee-onboarding.sql` needs those files applied with
`--local` once, since `schema.sql` only creates missing tables. The suites apply
`migrations/2026-11-17-agent-activity.sql` themselves when `call_activity` still
allows the `acp` source.

The smoke test runs `wrangler dev -c wrangler.toml -c speaker_mock.toml`, so the
`SPEAKER` binding reaches `speaker_mock.js`, whose answers follow from the audio
streamed to it (Challenge Practice and Identity Check levels, short speech,
verifier errors; its `embed` returns one fixed unit vector, so enrollment's
reference is checkable). It
gives wrangler a private dev registry (`WRANGLER_REGISTRY_PATH`), so another
worktree's `wrangler dev` never binds to the mock.

It checks registration, retry limits, the unregistered greeting, snapshot and
credential-free configuration, sandbox creation, immediate dialing while a
Sandbox is provisioning or stopped (with a start request), recovery for a broken
Sandbox, deletion recovery, the prewarm reaching the Durable Object, status
execution, identity isolation, webhook signatures, and fail-closed admin access.

`voice_session_test.py` runs mock OpenAI GPT-Live control, Daytona preview, and
ACP endpoints around a local Worker, over two calls. It checks signed
incoming-call correlation, client delegation, the first-call Opening, ACP
connecting in the background (`agent_connecting`, fail-fast session tools,
`agent_connected`), the Session Orchestrator loop against a mock Responses endpoint,
Managed Session creation, asynchronous Prompt Turns, session inspection, the
end-of-call welcome, a returning caller's composed Opening with caller-ID and
since-last-call clauses, `session/resume` on reconnect with a cached preview
URL, the keypad conventions and System Menu, voice-set Earcon Style, browser
pairing end to end (spoken code allocation, typed claim, single
use, keypad acceptance, status and file reads over the browser socket, path
confinement, refusal, and unpairing), and call termination without
consuming provider traffic.

## Front Door

A registered caller talks first. `/voice` resolves the Sandbox once: a stopped,
archived, or paused Sandbox gets a start request (not awaited); one being
provisioned or replaced is left to provisioning; only `error`, `build_failed`,
or `destroyed` goes to the TwiML recovery menu. Every other case dials the
GPT-Live SIP URI immediately; there is no wake-up loop. Meanwhile `/voice`
fires a `/prewarm` at the sandbox-keyed Durable Object (`ctx.waitUntil`), which
starts connecting ACP before GPT-Live has even answered. Prewarm is idempotent
and harmless beside an attached call.

The Durable Object never makes the call wait for the Agent. On attach it plays
the call-start earcon, speaks the Opening, and keeps connecting ACP in the
background with backoff for about three minutes (the preview URL fetch or the
WebSocket upgrade fail until the Sandbox's gateway listens). The Daytona preview
URL and token for the ACP port are cached in memory and Durable Object storage
per Sandbox and refetched once whenever a connection through them fails. The
Orchestrator gets `agent_connecting` with `call_started`, then `agent_connected` (with the
session count) or `agent_unavailable`. Session tools that need the Agent fail
fast with "the sandbox is still starting; try again after agent_connected", while
`list_managed_sessions` and `inspect_managed_session` answer from the persisted
projection. A dropped Agent Connection mid-call reconnects in the background
instead of hanging up. When the OpenCode service the gateway started dies, the gateway reads
cgroup v2 `memory.events` (its own cgroup and ancestors) to tell an OOM kill from another
signal or exit, closes each `/events` WebSocket with 1011 and the reason as compact JSON
`{reason, signal, exit_status, limit_bytes}`, and restarts the service with backoff
(`../SANDBOX_AGENT_GATEWAY.md`); the Durable Object puts it in `failure`, `turn_failed`, and
`agent_unavailable` (`agent_termination`, `failed_turns`). The gateway is baked
into the snapshot at `/opt/dogwalk/sandbox_agent_gateway.py`, so a change reaches
a Sandbox through a new snapshot (new Sandboxes only) or a back-patch:
`uv run --script deploy.py --no-deploy --patch-sandboxes` lists which snapshot-owned
files (`SNAPSHOT_FILES` in `build_sandbox_snapshot.py`: gateway, OpenCode config,
rules, plugin) differ in each managed Sandbox, and `--apply` writes them with
backups, self-tests a patched gateway inside the Sandbox (restoring everything on
failure), and stops the Sandbox so the next call boots them. It also installs
OpenCode at the snapshot's version when a Sandbox has another (see Cutting Over to the
Native API); other packages still need a new snapshot. On reconnect, Managed Sessions are restored with
`session/resume` when the Agent advertises `sessionCapabilities.resume` in
`initialize`, otherwise `session/load`, always from the session's recorded
directory (OpenCode rejects a reload from any other). A record without a
directory, or one whose reload fails, is repaired from the session's `cwd` in
`session/list` and retried once; the audit records
`acp.session.directory_repaired`.

The Opening is deterministic and costs no model call at connection:
`<caller-ID clause> Dogwalk. <welcome line> Where to?` The caller-ID clause is
"Verified." (STIR/SHAKEN A), "Partly verified." (B or C), "Caller ID failed
verification.", or "Caller ID unverified." (missing or `No-TN-Validation`). The
welcome line is `next_greeting`, written by one short Orchestrator-model call
(`tool_choice: none`) when the previous call ended, plus "Since then, ..."
clauses for Prompt Turns that stopped or failed, Permission Requests, and saved
recordings while no call was attached, plus a few words if the last call ended
abnormally; "Welcome back." is the fallback. A first call after Registration
says "Dogwalk. Welcome! I'm your walker; your sandbox is being set up, which
takes a minute or two." and the Orchestrator loads the `first-call-tour` skill.

## Keypad

| Mode | Key | Effect |
|---|---|---|
| Conversation | `*` | System Menu (below) |
| Conversation | `#` | Stop talking: a 0.1 s silent fixture cuts speech off, then an instruction to listen |
| Conversation | `1` | Instant status from the persisted projection, as commentary; bypasses the Orchestrator |
| Conversation | `0` | Repeat the Voice Frontend's last utterance verbatim |
| Conversation | other | `keypad` harness event to the Orchestrator with the conversation since the last handoff |
| Recording | `#` / `*` | Save / discard |
| Pairing confirmation | `1` / `*` | Accept / refuse the browser that claimed the Pairing Code |

The System Menu is a deterministic state machine in the Durable Object, kept for
what must work when the Voice Agent cannot hear or think straight: it mutes the
Voice Frontend's input (`session.input_audio.mute`), holds Orchestrator speech
and earcons, and plays pre-rendered prompts from `../make_earcons.py` (always
spoken, whatever the earcon style). `1` restart (a second `1` confirms; the
Daytona Sandbox is stopped, then started, and the Orchestrator gets
`sandbox_restart_requested`), `2` the TwiML recovery menu (as
`open_recovery_menu`), `*` or 15 s after the prompt back. Leaving unmutes and
plays "Back." Actions are recorded as `menu.*` Call Activity. Status, hanging
up, sound style, pairing, and a voice-confirmed restart are voice operations
(below).

## Voice Sessions

Calls use secure SIP (`TLS` signaling and `SRTP` media) to hand the active
audio leg directly to OpenAI GPT-Live. A sandbox-keyed SQLite Durable Object owns
the Agent Connection and persisted Managed Session projection; it attaches one
GPT-Live sideband WebSocket. Ending the call detaches that sideband but does not
cancel active Prompt Turns or close ACP Sessions.

The Voice Agent is two components (see `../DOMAIN.md`). The Voice Frontend is
`gpt-live-1` under client delegation: it converses, has no tools, and raises a
delegation (a Handoff) when the caller asks about work. The Session Orchestrator
runs inside the Durable Object as a Responses API tool loop on
`ORCHESTRATOR_MODEL` (default `gpt-6.1-sol`, `low` reasoning effort). It reads the
call transcript up to each delegation's offset (each transcript delta once, with
no wait: GPT-Live sends them before the delegation), plus a Continuation
(`[continued <id>]`) when the caller keeps talking past the offset and no new
delegation comes; `delegation_probe.py` at the repository root measures both
against synthetic callers on the real GPT-Live API. It operates Managed Sessions through the
neutral operations (`create_managed_session` (`session/new` rooted in an
existing directory under `/home/daytona`, checked with `test -d` through the
Daytona toolbox), `begin_prompt_turn`, `inspect_managed_session`,
`list_managed_sessions`, `cancel_prompt_turn`, `resolve_permission`,
`list_workspace_projects` (git repositories and `AGENTS.md` directories up to
three levels below home, with credential-free origin URLs),
`discover_persisted_sessions` (ACP `session/list` across all directories,
following `nextCursor`, keeping those inside `/home/daytona`),
`load_persisted_session` (`session/resume` from the session's own directory,
with a fresh Alias), `set_alias`, `close_managed_session` (`session/close`;
history is kept), `restart_sandbox` (after the caller confirms aloud; shares
the menu's restart), `set_earcon_style` (`tonal`, `spoken`, or `none`; stored
as `earcon_preference` and previewed with the opening earcon), `pair_browser`
(see Paired Browser), plus call control), and answers through `brief`
(`session.thinking.append`) and `say` (`session.commentary.append`). Events that
arrive mid-loop are injected before its next model call, so corrections steer
work in flight. Its context persists in Durable Object storage across calls;
between calls, events accumulate but the model does not run.
`compact_context` (optional `focus`, at most 500 characters) returns at once: it
appends a neutral commentary telling the caller the Voice Agent is tidying its
notes, and once its step is answered it snapshots the context and starts the
Memory writer, a separate Responses request (same instructions, tools with
`tool_choice: "none"`, pinned Memory, and context, so the prompt cache serves
it, ending in a `[write memory]` developer message; an over-long draft gets one
`[condense memory]` request with only the draft). The writer runs off the
Orchestrator's queue, so handoffs, harness events, and tools (`end_call` too)
proceed meanwhile; call CAa3722805 waited 31.5 s to hang up behind a Memory
written inline. When it finishes, the Memory version is stored and the cut
removes context only before the user message chosen from the snapshot (found by
identity, so items added meanwhile stay), recording `orchestrator.compaction`
or `orchestrator.compaction.failed`; a call still attached gets
`context_compacted` or `compaction_failed`. A compaction outlives its call
(`waitUntil`, and the keepalive alarm re-arms while it runs), so the next call
starts compacted. The context backstop stays a synchronous mechanical cut at
150,000 input tokens: it writes no Memory and needs no model, and a cut past a
running compaction's anchor leaves that compaction nothing to cut.
`ORCHESTRATOR_API_BASE` and `ORCHESTRATOR_API_KEY` can point it at any compatible
endpoint.

Voice Agent Skills live in `voice_agent_skills/*.md`: frontmatter `name` and
`description` (the trigger), an Orchestrator body, and an optional
`## Voice Frontend guidance` section pushed through `session.instructions.append`
when the skill loads. The index is inlined in the Orchestrator prompt; bodies
load through `load_voice_agent_skill`. Add a skill by writing the file and
registering it in `src/voice_agent_skills.ts`. `platform-self-knowledge` embeds
`DOMAIN.md` and `PSTN_CLOUDFLARE.md` at build time; `runtime-dramaturgy` appends
the active Theme's authoring notes from `src/themes.ts` when it loads.

Audio goes to the caller as GPT-Live output fixtures. Earcons mark events and
queue for lulls, in the caller's Earcon Style (set by voice): `tonal`, the
default, is the semantic chime vocabulary designed in `../earcon_lab.py` (see
`../SOUND_DESIGN_NOTES.md`) and exported to `src/earcon_designs.json` with
`earcon_lab.py --export`; `spoken` is labels rendered by `../make_earcons.py`
with macOS `say`, committed under `earcons/`; `none` plays no event earcons (the
recording beep still plays). `play_audio` renders a window of any Workspace audio or video file with
`ffmpeg` in the Sandbox. `start_recording` mutes the Voice Frontend, buffers the
caller audio GPT-Live reflects to the sideband, and saves a WAV under
`recordings/` when the caller presses pound (star discards), after 20 s of
silence, or at its limit. Keypad presses arrive as `transport.dtmf.received`
(see Keypad).

Recording deliberately does not use `session.input_audio.mute`. The docs say
muting preserves sideband reflection, but over SIP (verified 2026-11-07) the
reflected caller audio becomes exact zeros while muted; unmuted it carries the
caller (peaks around 15–21k, about two thirds of 200 ms chunks non-silent).
Instead the Voice Frontend is instructed to stay silent and its handoffs are
ignored until the recording ends. `RECORDING_MUTE=true` restores muting for
re-testing provider behavior.

Twilio adds a five-minute HMAC capability containing the existing Call SID to a
custom SIP header. The signed OpenAI webhook validates it and resolves the User
and current Sandbox Assignment from D1; SIP caller headers alone never authorize
a call. On attach, the Durable Object gives the Orchestrator a bounded call-start
snapshot of aliases and recent state, the Opening's exact words, the caller-ID
attestation, and a since-last-call summary as a harness event; tools remain
authoritative for current reads and all actions.

The Voice Frontend normally speaks one short sentence, then listens. Dog-walking
presentation language is isolated in Themes (`src/themes.ts`); machine-facing
contracts remain neutral. `end_call` closes the call, while
`open_recovery_menu` hands control back to deterministic TwiML for restart or
confirmed destructive replacement.

Cloudflare currently guarantees an outbound WebSocket prevents Durable Object
eviction for 15 minutes. Agent updates generally keep active turns alive beyond
that window, but a completely silent long Prompt Turn could still lose its Agent
Connection. A sandbox-initiated inbound ACP connection is the hardening path for
strictly durable unattended turns.

## Paired Browser

Before deploying spoken pairing, apply `migrations/2026-11-12-spoken-pairing.sql`
to D1 while no call is in progress, then deploy with `deploy.py` at once: code
from before this change cannot write the new `nameplate` column, and this code
cannot use the old table, so pairing fails in whichever gap is left open. It discards pending acoustic codes and removes decode-failure telemetry,
but does not touch accepted browser credentials in Durable Object storage:

```bash
npx wrangler d1 execute dogwalk --remote --file migrations/2026-11-12-spoken-pairing.sql
```

For an existing local D1 database, apply the same migration before running the
test suites (`schema.sql` alone does not replace an existing table):

```bash
npx wrangler d1 execute dogwalk --local --file migrations/2026-11-12-spoken-pairing.sql
```

An optional, read-only second surface (`../DOMAIN.md`, Paired Browser). The
caller asks to pair; the `pair-browser` Voice Agent Skill walks them through
opening `https://dogwalk.tools/pair`, then the Orchestrator calls `pair_browser`
with no arguments. The Durable Object allocates a fresh Wormhole-style
`number-word-word` Pairing Code (`src/pairing.ts`), retrying collisions on its
1–999 Nameplate prefix. D1 `pairing_codes` stores the unique `nameplate`,
`code_hash` (SHA-256 of the full code), `identity_hash`, `expires_at`, and
`attempts` (initially zero), never the code text. It replaces the User's previous
outstanding code and expires after 90 s. The tool returns the code to the
Orchestrator, which has the Voice Frontend say it clearly, including the hyphens;
the caller types it into the page (`src/pair_page.ts`). No browser microphone,
speakerphone, second device, or generated keypad tones are involved. This is
spoken-code rendezvous, not the Magic Wormhole protocol. If the code expires
without a claim, an expiry timer emits `pairing_unclaimed` and the Orchestrator
offers a fresh code. Code text is excluded from diagnostic events, Memory, and
Workspace files, apart from unavoidable spoken/model conversation content.
`POST /pair/claim` consumes the code row and
forwards the browser's description (user agent) and place (Cloudflare `cf`
city and region) to the Durable Object, which has the Voice Frontend name them;
`1` accepts, `*` refuses, 30 s lapses. The page polls
`GET /pair/claim/status` with its ticket and receives its credential once.

Two complete PGP wordlists give 16 secret bits (odd list first, matching
Wormhole); the numeric Nameplate is public routing, not entropy. Entry accepts
spaces or hyphens and ignores case. A transactional D1 batch counts at most five
guesses per code across all source addresses, spends a successful code, and
retires an exhausted code. `pairing_claim_budget` enforces 120 requests per
fixed minute service-wide, including malformed requests. HTTPS and independent
caller approval are required; the budgets do not prevent denial of service or
overhearing. Run `node --experimental-strip-types pairing_test.ts` for code
generation, normalization, and vocabulary checks; `voice_session_test.py`
exercises claim and approval behavior through the Worker.

`node --test pairing_lifecycle_test.mjs` exercises disconnect/claim interleaving
and a D1 cleanup failure against the actual Voice Session methods.

Credentials are `<identity>.<secret>`: the identity half routes to the
sandbox-keyed Durable Object (and must have a Sandbox Assignment), and only the
secret's hash is stored there, in `paired_browsers` with the Display the browser
joined (an hour idle, twelve hours in all; cleared on Sandbox replacement). The
browser keeps it in `sessionStorage` and opens
`wss://dogwalk.tools/pair/ws?for=<identity>`, authenticating in its first
message. The socket uses the Hibernation API, so a paired tab never keeps the
object or its Agent Connection in memory.

Several browsers may be paired, each to a named Display (`../DOMAIN.md`,
Display). `pair_browser({display?})` names it (default `main`, then `second`,
...); a browser paired to a Display that has one mirrors it, and accepting a
browser never unpairs another. A Display ends with its last browser (expiry,
unpairing on the page, or `unpair_display`), deleting its boards and revoking its
Access Grants; `rename_display` renames one. The Orchestrator gets
`browser_connected` and `browser_disconnected` per Display (`displays_open` in
storage) and `displays` in its call-start snapshot and `inspect_call_state`.

The page is whiteboard-only: no file browser, session view, timeline, or live
feed. On connect it gets `hello` (with its `display`) and its Display's `boards`.
It sends `ping`, `board_answer`, `board_error`, `unpair`, `open` (an Access
Element) and `read` (a Document's text). Files reach it only through boards:
`GET /pair/file?path=<as written>&board=<id>` (bearer credential, served
sandboxed, `nosniff`, `private, no-store`) serves a path only if a board on the
requester's Display names it (Image with `&as=image`, Document, or Download with
`&download=1` and a live grant), and only while Daytona reports the Sandbox
`started` (checked without waking it, cached 15 s); otherwise `503 {asleep:
true}`. Reads resolve symlinks in the Sandbox (`realpath`) and refuse anything
that leads outside `/home/daytona`. `as=image` reads at most 1.5 MB, typed by
sniffing the bytes (a name that disagrees is refused), and serves SVG with
`default-src 'none'; style-src 'unsafe-inline'; img-src data:`. Dogwalk keeps
no copy of a board's files (the retired Board Assets did): a page left open
keeps what it loaded. When the page reads an `.html` Document, each `<img src>`
naming a Workspace image (absolute, or relative to the report) is rewritten with
`HTMLRewriter` into a `data:` URI (1.5 MB each, 6 MB and 24 images per
document); others keep their tag, with the reason in `alt`. HTML renders in an
`<iframe sandbox="allow-scripts">` from `srcdoc` with a prepended CSP of
`default-src 'none'` (inline scripts, styles, and data: images only).

### Whiteboard

#### Image follow-up decisions (#3)

These decisions predate named Displays, which retired Board Assets: the 1.5 MB
cap below now bounds each board image's read rather than storage, and a board's
images are read live rather than captured.

- **Large images:** retain the 1.5 MB stored-asset limit rather than add R2 or a
  Sandbox image-processing dependency to the call path. There is now a small,
  explicit rendition helper: `uv run --script board_rendition.py INPUT.png OUTPUT.webp`
  from the repository root. It preserves the original, refuses output overwrite,
  bounds input pixels, and produces a still WebP under the asset cap. A synthetic
  1280 × 1600 RGB PNG was reduced from 6,154,022 to 1,231,116 bytes without reducing
  dimensions; transparency is covered separately. This proves the conversion,
  not the correctness or visual legibility of any generated diagram. The Voice
  Agent skill supplies the same concrete limits to a producing session.
- **Decoded pixels:** raster headers are checked before serving or capture:
  at most 8192 pixels per side and 16 megapixels. PNG, JPEG, GIF, and WebP are
  covered; missing dimensions fail locally. These are canvas-size bounds, not a
  complete decoder, an animation-frame budget, or SVG complexity protection.
- **Path capture:** comments and quoted examples no longer produce false assets;
  whitespace before `(` and string-valued statement references are supported,
  including aliases and cycle termination. The browser parser confirms those
  references are legal. No general expression evaluator was added to the server.
- **Sleeping-Sandbox HTML reports:** source tracing confirms `workspaceRead`
  downloads the report and calls `inlineReportImages` on every read; the iframe
  then owns a self-contained `srcdoc`. Already displayed reports remain visible,
  but reopening needs the Sandbox. Keep that live-file contract: persistent
  report snapshots would require a new retention/replacement policy, not just
  fixing Board Assets. No cache or wake-up promise is implied.
- **Export/canvas:** inspection found no Whiteboard export or canvas-readback
  operation. Keep export unsupported, rather than certify a nonexistent path.
  Blob image display alone proves nothing about canvas origin cleanliness.
- **Connectors/freeform layout:** `Steps(..., "row")` already renders simple flows;
  a Workspace SVG supplies arbitrary diagrams through Image. Keep the component
  layout rather than add editable geometry or a new canvas model.
- **Symlink race:** a synthetic local filesystem test reproduced redirection by
  replacing a parent of the *resolved* pathname between validation and open.
  Replacing the original symlink alone did not redirect the resolved read.
  Keep the limitation for now: this requires a concurrently mutating Sandbox
  process, which already runs with the Sandbox user's filesystem authority.
  This is not proof of race-free confinement or a test against the Daytona
  toolbox. Closing it properly would require changing the Sandbox read adapter
  (descriptor-based traversal/read), not another Worker pathname check.

Focused checks: `node --experimental-strip-types cf_worker/board_images_test.ts`
and `uv run --script test_board_rendition.py` from the repository root. The image
endpoint regression cases also live in `voice_session_test.py`.

Validation for this follow-up: focused header/path tests (including five real
Pillow-encoded raster formats), the extracted browser parser, both rendition
tests, TypeScript checking, Worker smoke tests (31 core checks plus timing), and
the gateway's checks passed. The full voice integration suite, including the new
`wide.png`/`pixels.png` endpoint regressions and the referenced-path parser case,
passed end to end when this was committed (two earlier attempts had timed out
before reaching the image checks). No production deploy or live-call test was done.

`draw_on_whiteboard(program, display?, note?, access_minutes?)` puts an
openui-lang board on a Display: diagrams, tables, charts, forms, per-item
decisions such as before/after edit review, Workspace images and Documents, and
Access Elements. The page parses and renders it (`src/pair_page.ts`; the dialect
and examples are in `voice_agent_skills/whiteboard.md`) and answers with
`board_answer`, accepted once per board, which reaches the Orchestrator as a
`whiteboard_answer` harness event and plays the `whiteboard_answer` earcon. A
board the page cannot render returns `whiteboard_error`. The last five boards per
Display are kept in `whiteboard_boards`, each with its `display`, the files that
failed their draw-time check (`failed`), and its grants (`access`); answers are
caller input, never authorization. `boardElements` (`src/board_images.ts`) finds
the files and Access Elements a program names, keyed `<kind>:<target>` the same
way the page keys them.

### Access Grants and Human Takeover

Access Elements (`../DOMAIN.md`, Access Grant) are `Terminal(label?)`,
`Desktop()`, `Preview(port)`, `SshAccess()`, and `Download(path)`. Drawing one
while a call is live grants it on that board's Display for `access_minutes`
(60 by default, at most 720); the board stores only the grant. A click sends
`open {board_id, key}`; if the grant is live and the Sandbox is running (nothing
on a board wakes it), the Durable Object mints a credential for that browser and
records it under `<board id>:<key>`:

- Terminal: a Daytona signed preview URL for port 22222, opening Daytona's own
  web terminal without a Daytona login. Each open gets a fresh link and shell;
  the optional argument is just a label, not a named tmux session. For persistent
  work, the User runs tmux and reattaches after reopening. Dogwalk hosts no
  terminal emulator, assets, PTY relay, or terminal-specific credentials.
- Desktop: starts Daytona computer use and returns a signed noVNC URL for port
  6080 (`vnc.html?autoconnect=1&resize=scale&path=websockify`).
- Preview: a Daytona signed preview URL for the port (never 8765, the Agent
  Gateway). Terminal, Desktop and Preview links are bounded by the grant's
  remaining whole seconds and are kept in `signed_links`. The browser receives
  only that port's signed link, never the API key or Sandbox-wide preview token.
- SSH Access: `POST /sandbox/{id}/ssh-access?expiresInMinutes=` within the
  grant; the page shows the returned `sshCommand`, kept in `ssh_tokens`.
- Download: no credential; `/pair/file?...&download=1` checks the grant.

`revoke_access`, a Display ending, a board evicted from its Display's five, or
Sandbox replacement revoke the grants' credentials: signed links are expired,
SSH tokens are deleted, and computer use stops once no desktop link remains.
Provider sockets close on a grace period, not synchronously with revocation or
expiry. In the isolated-browser PoC on daemon 0.232.2 (2026-11-14), revocation
blocked new requests with HTTP 401 and closed the live terminal after 3.7 seconds;
a 60-second link's live connection closed 108 seconds after minting (48 seconds
after expiry), and a fresh request then returned 401. These observations are not
a provider SLA. An open Access Element is not yet made to keep the Sandbox awake
beyond the traffic it carries.

Mint completion rechecks the stored grant, browser membership, and Sandbox
Assignment in the same transaction that registers the credential. A grant that
ended while Daytona was minting gets no returned link; that credential is revoked
instead. Failed provider revocations remain in the durable `access_revocations`
outbox (including their Sandbox ID, so replacement cannot misdirect a retry).
The Durable Object alarm retries them every 30 seconds until success or natural
expiry; `revoke_access` reports incomplete provider cleanup instead of confirming
that all access has ended. The same alarm serves call keepalive and the earliest
paired-browser expiry, so an unattended Display ends and revokes its links even
when no page sends another message. Browser activity recomputes the deadline
without postponing an already-due revocation retry.

Deploying this change applies Wrangler migration `v3`, deleting the retired
`TerminalRelay` Durable Object class. A one-time VoiceSession back-patch removes
`terminal_tickets`, `terminal_tokens`, and old Terminal grants, leaving other
grants and pairings intact. Redraw any existing Terminal element on the call;
reload an old Paired Browser tab so it uses `Terminal(label?)`. Existing tmux
sessions in the Sandbox are not killed; attach manually if needed.

Focused checks from the repository root:
`node --experimental-strip-types cf_worker/access_grants_test.ts` covers
short remaining lifetimes, refused opens, mint/revoke races, failed revocation
retries, unattended Display expiry, and the one-time state back-patch.
`voice_session_test.py` covers fresh terminal links, Display isolation, board
eviction, explicit revocation, unpairing, and keeping credentials out of the
Orchestrator context.

The desktop needs the snapshot's desktop packages (Xvfb, xfce4, x11vnc, noVNC,
Chromium with `--no-sandbox`); a Sandbox from an older snapshot answers
"the desktop is not installed on this sandbox yet" until it is back-patched.
At 1 GiB the desktop with Chromium leaves little room for an Agent; snapshots from
`dogwalk-acp-20261108-v2` on default to 4 GiB.

## Diagnostics

The read-only Diagnostics surface is at `https://dogwalk.tools/admin`. It is a
passive mirror of runtime state for harness development and debugging, never a
required user interface and never a control surface: all authorization and
action happen over voice. The interim username is `adam`; its generated password
is stored in macOS Keychain:

```bash
security find-generic-password -a adam -s dogwalk-admin -w
```

The page renders one runtime tree whose nesting follows object containment, with
Voice Calls, Sandbox Assignments, Access Control records, and the audit tail as
sibling roots. Selecting a node drives the detail pane. It receives authenticated
server-sent events from `/admin/api/events` every ten seconds. Streams close
after five minutes so the browser reconnects and revalidates authentication
automatically.

The Session Management and ACP Integration subtrees (Managed Sessions, Prompt
Turns, Attention Requests) are marked "not wired" until the backend emits that
telemetry; the operator lens shows only calls, sandboxes, registrations, and the
audit log today.

Live calls are keyed by Twilio Call SID and display a bounded, source-aware
activity timeline. The reserved sources are `voice`, `access`, `hosting`,
`menu`, and `acp`. Diagnostics starts in demo-safe mode: phone numbers,
provider IDs, Call SIDs, identity hashes, and event details are redacted by the
server. `Reveal verbose telemetry` reconnects with `verbose=1` for the current
page session only. Large conversation or tool payloads should be stored by
reference rather than copied into every activity row.

### Latency traces

#### Provider event time and delayed evidence

The operator diagram also accepts Twilio observations, placed by provider
occurrence time when available, even before the Worker's first observation.
Receipt time remains separately visible. Timestamps expressed in milliseconds
are not necessarily millisecond-accurate: Twilio callback RFC 2822 timestamps
and standard Call resource dates have one-second precision. Cross-clock reporting
age includes clock skew and is not a measured one-way network delay.

The outgoing SIP leg subscribes to `initiated ringing answered completed` on
`/voice/provider-status`. This endpoint verifies the Twilio signature and uses
`ParentCallSid` to correlate with an existing incoming PSTN call. It is diagnostic
only: no `From` number from a SIP child is used as caller identity, and neither
late progress nor historical enrichment can reopen a completed call. The normal
incoming-number `/voice/status` callback also preserves provider timing evidence.
Nonterminal callbacks there no longer modify operational call state.

Signed call observations schedule standard Twilio Call resource metadata reads
off the voice request's critical path. Terminal observations schedule delayed
Call Events enrichment after sixteen minutes; a once-per-minute Worker cron
processes durable jobs even when the operator page is closed. Retrieval uses the
Account SID from the authenticated Twilio form and the existing Worker
`TWILIO_AUTH_TOKEN`. `TWILIO_API_BASE` is a local-test override, not a URL accepted
from a caller. No paid Voice Insights feature is enabled.

Provider observations retain only allowlisted metadata: identifiers, explicit
parent-leg relationships, timestamps/provenance/precision, status, request path
without its query string, response status, and request duration. They never
retain raw forms, phone-number parameters, TwiML/response bodies, or signed SIP
URLs. Retries deduplicate on a durable fingerprint. Provider logs enrich Call
Activity only, never the lifecycle controller or the Session Orchestrator.

Apply the idempotent `provider_timing.sql` schema addition remotely before deploying
this version; fresh local databases include it in `schema.sql`. Existing historical calls with
no authenticated Account SID observation are not guessed into an account. Newly
observed calls are enriched automatically, including their earlier provider times.

The operator-only live sequence diagram is at `https://dogwalk.tools/admin/timing`,
linked from the Diagnostics tree. It is separate from `/pair`: it can discover
recent calls for every caller without a pairing ritual or access to that caller's
browser. It uses the existing operator authentication on **every** page/API
request: HTTPS HTTP Basic (`adam`, password in Keychain as above), or a verified
Cloudflare Access JWT when configured. Pairing bearer credentials do not grant
operator access. No new bearer token, URL secret, or browser-local credential
store is introduced. Treat the diagram and event inspector as verbose private
operator telemetry, including when observing a friend's call.

The diagram polls an incremental D1 projection, not the live Sandbox, so merely
watching does not wake an Agent or change call state. Call discovery is
`GET /admin/api/timing/calls` (newest 50, masked caller labels); event retrieval is
`GET /admin/api/timing?call_sid=CA...&after=0`. Event responses contain at most 500
rows, a monotonically increasing insertion `cursor`, and `has_more`. Drain all
pages and continue from the returned cursor. Late timing batches still arrive
after that cursor even when their observation timestamps precede earlier rows;
sort the merged observations by `detail.ts_ms` for display. A call ending does
not imply all asynchronous trace writes have arrived. Agent tool observations
include tool IDs/status/kind and ACP Session IDs; Prompt Turn lifecycle records
include Alias, ACP Session ID, and duration. Nested Agent sessions are only
renderable when their identities/relationships are actually observed, never
inferred from concurrent activity.

The domain bands map the current deployment: Twilio PSTN then SIP at the left,
OpenAI Voice Frontend and reasoning API, Cloudflare Orchestrator/control transport,
and Daytona-hosted Managed Sessions/Agent tools. Reasoning API durations remain
Worker-observed round trips, not model-internal compute measurements.

`Show content` is independent of raw metadata: it reveals transcript text,
Handoffs, speech/context requests, ACP engineering briefs, and Agent messages
alongside the technical timeline. This is intentionally private operator access,
not the Paired Browser experience. Turning it off hides message content in the
diagram, inspector, and local exports, but does not revoke the operator's access.
No raw audio, arbitrary provider payload, or model-private reasoning dump is added.

Historical call content is read on demand from
`GET /admin/api/timing/content?call_sid=CA...`, using the same operator gate.
Saved media-relative transcripts are labeled separately from observations with
exact timing correlations. Retention/compaction may leave gaps, which the page
reports rather than inventing content or timestamps. New calls also retain
bounded content at the instrumented boundaries; truncation is explicitly marked.

### OpenTelemetry export (issue #4, Phase 1; issue #8)

`src/otlp.ts` projects one call's journal into standard OTLP/JSON, metadata only.
`GET /admin/api/otlp/v1/logs?call_sid=CA...&after=0` returns an
`ExportLogsServiceRequest` (every journal row is one LogRecord, 500 per page; paging
travels in `x-dogwalk-cursor` / `x-dogwalk-has-more` so the body stays pure OTLP).
`GET /admin/api/otlp/v1/traces?call_sid=CA...` returns an `ExportTraceServiceRequest`
of reconstructed spans: the ended Voice Call, Orchestrator model requests and tools,
Prompt Turns (their own trace, linked to the call), and ACP RPC roundtrips matched
within one Agent Connection. Both use the operator gate; spans are matched over at most
20,000 rows per call (`x-dogwalk-truncated`). Provider occurrence time maps to `timeUnixNano` only when
known; receipt is `observedTimeUnixNano`. Log records are row-local: each depends only
on its own row, so a page fetched during a live call never changes later. A start row
names its span's trace and span ID at once; if the end never arrives, that span is
simply never exported. Every journal row records its producer when written: the
Worker Version ID (`version_metadata` binding, exported as `service.version`) and
`DEPLOYMENT_ENVIRONMENT` (`deployment.environment.name`; `production` in
`wrangler.toml`, overridden by the integration tests and worth overriding for a manual
`wrangler dev`). The export groups records into one Resource per recorded producer;
older rows keep a Resource without either, rather than borrowing today's build.
Orchestrator tools record their own outcome (`ok`, `error_result` for `ok:false`,
`returned` with no flag, or `threw`, which emits `tool.failed`), and tool span status
follows it. `tool.completed` rows from before outcomes were recorded stay `Unset`.

Live operations carry W3C trace context recorded when they start (`src/trace_context.ts`).
The front door mints each Voice Call's context into `voice_calls.trace_id`/`span_id` and
hands it to the Durable Object as a `traceparent` header. Model requests and Orchestrator
tools are child spans of that call context, snapshotted at start. Each Prompt Turn is its
own trace, and it records the initiating call as a link, not a parent. Its end row is
journaled under whichever call observed it; the traces export for the starting call
looks recorded spans' ends up across calls by `span_id` (indexed; beyond 900 open
spans or 1,000 rows per lookup, `x-dogwalk-truncated` is set). Between calls, a tool or
ACP request that completes is journaled under the scope captured when it started. The turn's context is persisted with its Managed Session: if eviction loses the
turn, the reload records `acp.turn.interrupted`, which names the span but never
completes it, since the actual end was not observed.
Each ACP request is a span whose parent is its Prompt Turn (`session/prompt`) or the call;
the response row names the request's span. Rows record `trace_id`, `span_id`,
`parent_span_id`, and for turns `link_trace_id`/`link_span_id`; the export uses those
and matches recorded starts to ends by span ID, marking spans
`dogwalk.context=recorded_at_start`. Older rows and calls keep derived IDs. Apply
`migrations/2026-11-10-voice-call-trace.sql` remotely before deploying this, and to any
existing local D1 state (or delete `.wrangler/state/v3/d1`). Attributes come from a server-side allowlist: content,
Aliases, error strings, and thought tails are dropped and counted in
`droppedAttributesCount`, whatever the page's Show content setting.

Each Orchestrator model request sends its own span as `traceparent` (no `tracestate` or
`baggage`) and its `request_id` as `X-Client-Request-Id`. OpenAI documents the client ID
for support correlation; neither header makes a provider continue the trace or return spans.
The journal records metadata only: the requested model, endpoint host and port (never the
path or key), and reasoning effort on `model.started`; the provider's `x-request-id`
(dropped unless a plain identifier of at most 128 characters) on `model.headers`; the
response `id`, served `model`, and input/output token counts on `model.completed`; and
`error_type` on `model.failed`.

Each ACP request Dogwalk sends carries its own span (the one on its `acp.sent` row) in
`params._meta.traceparent`, merged into any existing `_meta`; the ACP extensibility spec
reserves that key, and upgrade headers cannot name individual requests. No `tracestate`,
`baggage`, credentials, or content go in `_meta`. Notifications (`session/cancel`) and
responses to Agent-initiated requests carry none: they are not Dogwalk spans. The Agent
Gateway now proxies OpenCode's native API instead: it forwards a request's `traceparent`
header upstream and logs a metadata-only `agent_trace_context` line
(`SANDBOX_AGENT_GATEWAY.md`). OpenCode 2.2.6 accepts the key, but its ACP layer does not read it,
and nothing here gives the Sandbox an OTLP endpoint, so ACP spans end at the request/response
boundary.

Model request and Orchestrator tool spans follow the Development GenAI semantic conventions
pinned at `open-telemetry/semantic-conventions-genai@e6658f14c9d9ea0760825f029cb82e7673f48fab`
(2026-11-06; `src/otlp.ts` lists every adopted attribute). Model spans are named
`chat {model}` with `gen_ai.request.model`, `gen_ai.response.*`, `gen_ai.usage.*`,
`server.address`/`server.port`, and `error.type`; `gen_ai.provider.name` is `openai` only
when the recorded host is `api.openai.com` or a subdomain of it, and is omitted for any other
`ORCHESTRATOR_API_BASE` rather than guessed. Tool spans are `execute_tool {name}` with
`gen_ai.tool.name`, `gen_ai.tool.call.id`, and `gen_ai.tool.type=function`. Content
attributes (`gen_ai.input.messages` and the like) are Opt-In and never exported. Model rows
written before this change keep the `orchestrator.model_request` name and no GenAI
attributes; usage everywhere is now `gen_ai.usage.*`. Because the conventions are
Development, moving the pin is a deliberate review, never tracking `main`.

`uv run --script otlp_send.py CA...` fetches both and POSTs them unchanged to an
OTLP/HTTP receiver (default `http://localhost:4318`), honouring partial success and
retrying 429/502/503/504. `--file DIR` sends synthetic fixtures written by
`node --experimental-strip-types cf_worker/otlp_test.ts DIR`, which also checks the
projection's provenance, matching, and redaction rules. A stock `otelcol-contrib`
with the `debug` exporter is enough to verify ingestion; no Collector is deployed.
For a browsable waterfall, run a stock Jaeger v2 binary (it listens on `localhost:4318`
and serves its UI on `localhost:16686`) and send with `--signals traces`, since Jaeger
accepts traces but not logs. Jaeger 2.23.0 rendered the fixture's call and Prompt Turn
traces, with every parent resolved and the turn linked to its call.

Browser regression checks use synthetic events and mocked API requests only:
`uv run --script cf_worker/operator_timing_test.py` (requires local Google Chrome).
They cover incremental pagination, timing spans, packet-noise reduction, safe
rendering of metadata, pause/resume, caller selection, and authentication failure.

`uv run --script playtest.py --only timing --full` prints the latest call's detailed
timeline. All newly written activity/audit details carry `ts_ms` (server Unix
milliseconds); the SQL `ts` column remains Unix seconds. Historical timestamps
cannot be made more precise. The stats report uses millisecond timestamps for
handoff-to-say acceptance, which is not time-to-audible-response.

The authenticated `/admin/api/calls?verbose=1&timing=1` includes `voice.timing`
rows, hidden from the ordinary diagnostic tree and the Voice Agent's activity
summary. They record sideband receipt and message-chain wait (`queue_ms`), transcript
delta character counts and provider media offsets, handoff preparation, model
request/headers/completion/failure, tool start/end, ACP send/receipt, and actual
sideband sends versus appends held behind menus or fixtures. Every sideband command
carries an `event_id` (`<kind>-<n>`, such as `commentary-12`); acks and errors that
echo it as `client_event_id` are recorded with the command's type and delegation ID,
and the provider's `start_ms`/`end_ms` on an append ack place that say or brief on the
media clock. An error naming a command is recorded as `voice.live.command.rejected`
(or `voice.fixture.rejected`), not as a call-level `voice.live.error`. Server events
replayed on attach are dropped by `event_id` (`live.replayed`), and a delegation
repeated under a new one is one handoff. `session.usage.updated` is recorded as
`voice.live.usage` (cumulative `seconds`, context `usage_ratio`) for observation only.
Request IDs, tool-call IDs, delegation IDs, and voice-session IDs support joins. `clock_id` and `seq`
identify local observation order across Durable Object resets; database insertion
order is not observation order because timing rows are batched.

Timing rows may retain bounded, allowlisted content at conversation and ACP
boundaries. Arbitrary tool argument/result dumps, private reasoning chunks, and
audio payloads are excluded. The compact view adaptively fuses text deltas into
observed bursts by stream identity and cadence, with explicit lifecycle ends;
these are presentation segments, not guaranteed speech turns or VAD silence.
Raw deltas and observation timestamps remain available independently.
They flush off the voice message chain every second or 50 observations, and at
normal voice cleanup. This is best-effort telemetry: an abrupt reset can lose
the pending batch, and failed writes are logged rather than delaying speech.
Millisecond timestamp units do not imply millisecond measurement accuracy.
Provider `start_ms`, `end_ms`, and `offset_ms` belong to the voice session's media
clock, not the server's Unix clock. Output transcript receipt is not audio playout;
`fixture.playing` means the provider accepted a fixture and `fixture.played` that its
mixer drained, neither arrival at the telephone. No full-call audio is retained by this instrumentation.

The Twilio incoming number must send status callbacks to:

```text
https://dogwalk.tools/voice/status
```

Use `POST`. Terminal callbacks close live-call cards; a five-minute activity
window is the fallback for missed callbacks.

The Worker also supports Cloudflare Access JWT verification through
`ACCESS_TEAM_DOMAIN` and `ACCESS_AUD`. Those variables remain empty until a
path-scoped Access application is configured for `/admin` and `/admin/*`.
