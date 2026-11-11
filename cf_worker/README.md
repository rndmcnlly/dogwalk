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
diagnostic utilities. The OpenCode config selects `openai/gpt-6.1-sol`.

From the repository root, build a new immutable version rather than replacing
this snapshot in place:

```bash
set -a; source ~/.tokens/daytona-api; set +a
uv run --script build_sandbox_snapshot.py --name dogwalk-acp-YYYYMMDD-vN
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
`dtn_secret_` placeholder; see `../SANDBOX_ACP_GATEWAY.md`. A Sandbox created
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
oldest first. `SANDBOX_REAP_MODE` ships as `dry-run`: the pass only audits
`sandbox.reap.candidate`. Any value other than `delete` is a dry run.

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

See the candidates before turning deletion on:

```sh
npx wrangler d1 execute dogwalk --remote --command \
  "SELECT datetime(ts, 'unixepoch') AS at, event, detail FROM audit_log
    WHERE event LIKE 'sandbox.reap%' ORDER BY id DESC LIMIT 50"
```

Each row's `detail` names `user_id`, `provider_id`, `last_call_at`, and
`idle_days`. Join `user_id` to `users.handle` to recognize whose Sandbox it is.
To turn deletion on, set `SANDBOX_REAP_MODE = "delete"` under `[vars]` in
`wrangler.toml` and deploy (or change the variable in the Cloudflare dashboard,
which the next `wrangler deploy` overwrites with the file's value). In production a pass
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
`identity_hash` that keys their VoiceSession and TerminalRelay Durable Objects
and their Daytona label `identity-v1:<hash>`, Registration provenance, and at
most one Home number. Registration assigns a random Handle and speaks it twice.
Until the Identity Check exists, an unexpired Home (used within 90 days) admits
its caller as the phone number alone did before; `voice_calls.user_id` records
which User a call reached. `voice_references` holds enrolled speaker embeddings
(never audio) for the coming Identity Check. The `SPEAKER` Service Binding reaches
the separately deployed `dogwalk-speaker` Worker (`../speaker_worker/`), which
scores audio against them; operator-only `/admin/api/voice-verify` routes expose
it for calibration and answer 503 when it is unbound or unreachable. Local tests
bind `speaker_mock.js` in its place (see Verification). A Home call from a User
with a Voice Reference, and each Challenge Practice take, warm the verifier in
the background (`warmSpeaker`, a `waitUntil` that never delays TwiML and drops
every failure), so a later challenge on the same machine skips the 3–5 s model load.

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
generator the Identity Check will reuse) and two free-speech prompts make about
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
with a spoken reason when either is missing, warms the verifier with a synthetic
`embed` in `waitUntil`, and starts round one. Each round inserts a transient
`practice_rounds` row with a fresh phrase, starts a caller-only
`<Start><Stream>` to `/voice/practice/stream/<capability>` (the same signed,
per-round machinery as Voice Donations, in `src/caller_stream.ts`), plays the
tone, and says the phrase inside a speech `<Gather>`. Its action,
`/voice/practice/heard`, checks the transcript first (`phraseMatches` in
`src/challenge_phrases.ts`, shared with the coming Identity Check): every
content word in order, plurals forgiven, insertions allowed, or the round is a
wrong phrase and is not scored. Every phrase Gather (donation rounds too) passes
the phrase's content words as speech `hints`. A short "repeat", "say that
again", "again", "what", or "pardon" replays the phrase as a new take (its own
stream and capability, `practice_rounds.take`), up to three times per round,
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

## Verification

The local smoke test runs a fake Daytona lifecycle and toolbox server:

```bash
npm run typecheck
uv run --script smoke_test.py
uv run --script voice_session_test.py
uv run --script admin_test.py
uv run --script ../sandbox_acp_gateway.py test
node --experimental-strip-types challenge_phrases_test.ts
node --experimental-strip-types voice_verify_test.ts
node --experimental-strip-types endpointer_test.ts
```

The local suites listen on 8787 (Worker), 8790-8792 (mocks), and 10229 (inspector).
To run them in several worktrees at once, give each its own base, for example
`DOGWALK_TEST_PORT_BASE=8800 uv run --script voice_session_test.py`; ports are
the base, base+3 to base+5, and base+1442. Each worktree keeps its own local D1.

The smoke test's Voice Donation checks use wrangler dev's local R2. A local D1
created before `migrations/2026-11-11-call-handoff-donation.sql`,
`migrations/2026-11-12-challenge-practice.sql`, or
`migrations/2026-11-12-speculative-scoring.sql` needs those files applied with
`--local` once, since `schema.sql` only creates missing tables.

The smoke test runs `wrangler dev -c wrangler.toml -c speaker_mock.toml`, so the
`SPEAKER` binding reaches `speaker_mock.js`, whose answers follow from the audio
streamed to it (Challenge Practice levels, short speech, verifier errors). It
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
pairing end to end (Pairing Tones decoded from the played fixture, claim, single
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
instead of hanging up. When the Agent process dies, the gateway reads cgroup v2
`memory.events` (its own cgroup and ancestors) to tell an OOM kill from another
signal or exit, answers unanswered requests with JSON-RPC error data
`{reason, signal, exit_status, limit_bytes, detail}`, and closes with 1011 and
the same reason; the Durable Object puts it in `failure`, `turn_failed`, and
`agent_unavailable` (`agent_termination`, `failed_turns`). The gateway is baked
into the snapshot at `/opt/dogwalk/sandbox_acp_gateway.py`, so a change reaches
a Sandbox through a new snapshot (new Sandboxes only) or a back-patch:
`uv run --script deploy.py --no-deploy --patch-sandboxes` lists which snapshot-owned
files (`SNAPSHOT_FILES` in `build_sandbox_snapshot.py`: gateway, OpenCode config,
rules, plugin) differ in each managed Sandbox, and `--apply` writes them with
backups, self-tests a patched gateway inside the Sandbox (restoring everything on
failure), and stops the Sandbox so the next call boots them. Packages and binaries,
OpenCode included, still need a new snapshot. On reconnect, Managed Sessions are restored with
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
| Pairing Tones playing | any | Ignored (until 0.5 s after), so echoes read back as presses cannot act |

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
call transcript up to each delegation, operates Managed Sessions through the
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

An optional, read-only second surface (`../DOMAIN.md`, Paired Browser). The
caller asks to pair; the `pair-browser` Voice Agent Skill walks them through
opening `https://dogwalk.tools/pair` on another device and pressing Listen, then
the Orchestrator calls `pair_browser`. The Durable Object stores a fresh Pairing
Code's hash in D1 `pairing_codes` (90 s, single use), waits for a lull, and
plays a spoken "Pairing tones." cue (after the Voice Frontend has finished
speaking) and then the Pairing Tones (`src/pairing.ts`: 10 random bytes plus 6
Reed-Solomon parity bytes over GF(256), 32 DTMF symbols at a `baud` the
Orchestrator picks from 4, 6, 8, 12, 16, default 8, so four seconds; each tone
fills 60% of its slot, at most 70 ms, and the rest is silence so room echoes
decay). The page (`src/pair_page.ts`) decodes with a Goertzel AudioWorklet,
tries each allowed rate against the frame's length, runs a symbol clock that
marks unclear slots as erasures, and claims only Reed-Solomon decodes with
parity to spare. `POST /pair/claim` consumes the code row and
forwards the browser's description (user agent) and place (Cloudflare `cf`
city and region) to the Durable Object, which has the Voice Frontend name them;
`1` accepts, `*` refuses, 30 s lapses. The page polls
`GET /pair/claim/status` with its ticket and receives its credential once.

Credentials are `<identity>.<secret>`: the identity half routes to the
sandbox-keyed Durable Object (and must have a Sandbox Assignment), and only the
secret's hash is stored there, in `paired_browsers` (at most three; an hour idle,
twelve hours in all; cleared on Sandbox replacement). The browser keeps it in
`sessionStorage` and opens `wss://dogwalk.tools/pair/ws?for=<identity>`,
authenticating in its first message. The socket uses the Hibernation API, so a
paired tab never keeps the object or its Agent Connection in memory. It receives
the `publicSession` projection on connect and (throttled) on changes, and can
list and read files under `/home/daytona` through the Daytona toolbox (512 KiB
shown, binary refused).

The page also streams raw Workspace files for its viewers from
`GET /pair/file?path=` (the credential as a bearer header; served sandboxed and
`nosniff`, 50 MB cap). Reads resolve symlinks in the Sandbox (`realpath`) and
refuse anything that leads outside `/home/daytona`. `&as=image`, or any `.png`,
`.jpg`, `.gif`, `.webp`, or `.svg`, reads the file as an image: at most 8 MB
actually downloaded, typed by sniffing the bytes (a name that disagrees is
refused), and SVG served with
`default-src 'none'; style-src 'unsafe-inline'; img-src data:`.

Whiteboard images are Board Assets, captured when `draw_on_whiteboard` runs
through the same image checks (at most 1.5 MB each, 6 MB and 8 images per board,
25 MB stored per identity). Each is stored once in Durable Object storage under
`board_asset:<sha-256>` (`{type, bytes}`), indexed in `board_assets`, and the
board keeps a manifest `images: {path: {hash, type, bytes} | {error}}`. The page
fetches `GET /pair/asset/<hash>` (bearer credential, `private, no-store`,
`nosniff`, the image CSP), which serves only hashes a retained board refers to
and never touches the Sandbox. Assets no retained board refers to are deleted
when a draw evicts boards, and all of them on Sandbox replacement. The page
shows every image through an `<img>` from a blob URL, one per hash, revoked when
the last board using it leaves. When the page reads an `.html` file, each
`<img src>` naming a Workspace image (absolute, or relative to the report) is
rewritten with `HTMLRewriter` into a `data:` URI (1.5 MB each, 6 MB and 24
images per document); others keep their tag, with the reason in `alt`. It also shows a live feed of the call (`feed` messages:
caller and Voice Frontend speech, Orchestrator briefs, says, and Agent prompts,
Agent remarks, tool calls and Reports, harness events, keypad presses).

One Paired Browser at a time: accepting a new one unpairs the old (its takeovers
are revoked, its page closes with 4003). The Orchestrator gets
`browser_connected` and `browser_disconnected` harness events and
`paired_browser` in its call-start snapshot, and `show_in_browser` sends the
page `{type: "show", uri, note}` for `dogwalk:file/<path>`, `dogwalk:dir/<path>`,
`dogwalk:session/<Alias>`, `dogwalk:timeline`, `dogwalk:whiteboard`, `dogwalk:memory`, `dogwalk:terminal`,
`dogwalk:desktop`, or an http(s) address. Files and views open directly;
takeovers and web pages show a banner the User clicks. HTML renders in an
`<iframe sandbox="allow-scripts">` from `srcdoc` with a prepended CSP of
`default-src 'none'` (inline scripts, styles, and data: images only).

### Whiteboard

#### Image follow-up decisions (#3)

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

`draw_on_whiteboard(program, note)` puts an openui-lang board on the paired
page (the Hands tab, or a tab of its own): diagrams, tables, charts, forms, or
per-item decisions such as before/after edit review. The page parses and renders
it (`src/pair_page.ts`; the dialect and examples are in
`voice_agent_skills/whiteboard.md`) and answers with `board_answer`, accepted
once per board, which reaches the Orchestrator as a `whiteboard_answer` harness
event, plays the `whiteboard_answer` earcon, and appears in the timeline. A
board the page cannot render returns `whiteboard_error`. The last five boards
are kept in `whiteboard_boards`; answers are caller input, never authorization.

### Human Takeover

The page's Hands tab launches a terminal or a desktop in a new full-screen tab
(`../DOMAIN.md`, Human Takeover). `launch` with `kind: "terminal"` returns
`/pair/terminal#t=<identity>.<ticket>`: a single-use ticket (60 s) in the
fragment. The terminal page (`src/terminal_page.ts`, vendored xterm.js under
`vendor/xterm/`) strips it, opens `/pair/terminal/ws?for=<identity>`, and
authenticates in its first message; the `TerminalRelay` Durable Object (its own
class, so `VoiceSession` keeps hibernating) has `VoiceSession` redeem the ticket
for a reconnect token, creates or reuses the toolbox PTY `human`, hands a new
one to `tmux new-session -A -s human`, and relays bytes both ways (binary
frames; JSON `resize`, `ping`, `exit`, `notice`). `kind: "desktop"` starts
Daytona computer use and returns a signed noVNC URL for port 6080
(`vnc.html?autoconnect=1&resize=scale&path=websockify`), valid at most an hour
and never beyond the pairing. Unpairing, pairing expiry, or Sandbox
replacement revoke both: terminals close with 4003, desktop links are expired,
and computer use stops once no link remains.

The desktop needs the snapshot's desktop packages (Xvfb, xfce4, x11vnc, noVNC,
Chromium with `--no-sandbox`); a Sandbox from an older snapshot answers
"the desktop is not installed on this sandbox yet" until it is back-patched.
At 1 GiB the desktop with Chromium leaves little room for an Agent; snapshots from
`dogwalk-acp-20261108-v2` on default to 4 GiB.

The prototype that settled the signal design is
`../prototypes/acoustic_pairing.html`: a transmitter, a live raw versus ECC
decoder view, and a simulated phone line (mu-law, noise, burst loss).

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
responses to Agent-initiated requests carry none: they are not Dogwalk spans. The Sandbox
gateway forwards `_meta` unchanged and logs a metadata-only `acp_trace_context` line
(`SANDBOX_ACP_GATEWAY.md`). OpenCode 2.2.6 accepts the key, but its ACP layer does not read it,
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
sideband sends versus appends held behind menus or fixtures. Request IDs, tool-call
IDs, delegation IDs, and voice-session IDs support joins. `clock_id` and `seq`
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
fixture acknowledgements indicate the provider mixer drained, not arrival at the
telephone. No full-call audio is retained by this instrumentation.

The Twilio incoming number must send status callbacks to:

```text
https://dogwalk.tools/voice/status
```

Use `POST`. Terminal callbacks close live-call cards; a five-minute activity
window is the fallback for missed callbacks.

The Worker also supports Cloudflare Access JWT verification through
`ACCESS_TEAM_DOMAIN` and `ACCESS_AUD`. Those variables remain empty until a
path-scoped Access application is configured for `/admin` and `/admin/*`.
