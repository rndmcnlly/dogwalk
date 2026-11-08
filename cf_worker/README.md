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

`DAYTONA_SNAPSHOT` is currently `dogwalk-acp-20261108-v1`. It contains the ACP
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
auto-archive after 7 days, and never auto-delete. A call starts or restores that
same Sandbox, preserving its filesystem and Agent-held session history across
months. Replacement occurs only after explicit provider-side deletion.

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

## Verification

The local smoke test runs a fake Daytona lifecycle and toolbox server:

```bash
npm run typecheck
uv run --script smoke_test.py
uv run --script voice_session_test.py
uv run --script ../sandbox_acp_gateway.py test
```

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
instead of hanging up. On reconnect, Managed Sessions are restored with
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
`nosniff`, 50 MB cap) and shows a live feed of the call (`feed` messages:
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
At 1 GiB the desktop with Chromium leaves little room for an Agent.

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

The Twilio incoming number must send status callbacks to:

```text
https://dogwalk.tools/voice/status
```

Use `POST`. Terminal callbacks close live-call cards; a five-minute activity
window is the fallback for missed callbacks.

The Worker also supports Cloudflare Access JWT verification through
`ACCESS_TEAM_DOMAIN` and `ACCESS_AUD`. Those variables remain empty until a
path-scoped Access application is configured for `/admin` and `/admin/*`.
