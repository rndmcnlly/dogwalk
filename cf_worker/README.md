# Dogwalk Cloudflare Worker

This Worker is the production Dogwalk service at `https://dogwalk.tools`. It
provides Twilio PSTN voice, phone registration, Daytona sandbox lifecycle,
Managed Session multiplexing, publication links, SMS delivery, recovery, and
a read-only Diagnostics surface.

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
npx wrangler secret put TWILIO_ACCOUNT_SID
npx wrangler secret put ADMIN_PASSWORD
npm run deploy
```

`DOGWALK_IDENTITY_SECRET` should be a stable random value. Changing it prevents
recovery by provider label for assignments that have not yet stored a Daytona
ID.

Set `OPENAI_PROJECT_ID` in `wrangler.toml`. In the matching OpenAI project,
subscribe `https://dogwalk.tools/openai/live` to `live.transport.incoming` and
store its `whsec_...` signing secret as `OPENAI_WEBHOOK_SECRET`.

`DAYTONA_SNAPSHOT` is currently `dogwalk-acp-20261107-v2`. It contains the ACP
Gateway, OpenCode `2.2.5`, passwordless sandbox-local `sudo`, and network
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

It checks registration, retry limits, snapshot and credential-free configuration,
sandbox creation, deletion recovery, readiness, status execution, identity
isolation, webhook signatures, and fail-closed admin access.

`voice_session_test.py` runs mock OpenAI GPT-Live control, Daytona preview, and
ACP endpoints around a local Worker. It checks signed incoming-call correlation,
client delegation, the Session Orchestrator loop against a mock Responses
endpoint, ACP initialization, Managed
Session creation, asynchronous Prompt Turns, signed-preview SMS, and call
termination without consuming provider traffic.

## Voice Sessions

Warm calls retain Twilio's deterministic registration and Sandbox wake-up front
door, then use secure SIP (`TLS` signaling and `SRTP` media) to hand the active
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
neutral operations (`create_managed_session`, `begin_prompt_turn`,
`inspect_managed_session`, `list_managed_sessions`, `cancel_prompt_turn`,
`resolve_permission`, plus SMS and call control), and answers through `brief`
(`session.thinking.append`) and `say` (`session.commentary.append`). Events that
arrive mid-loop are injected before its next model call, so corrections steer
work in flight. Its context persists in Durable Object storage across calls;
between calls, events accumulate but the model does not run.
`ORCHESTRATOR_API_BASE` and `ORCHESTRATOR_API_KEY` can point it at any compatible
endpoint.

Twilio adds a five-minute HMAC capability containing the existing Call SID to a
custom SIP header. The signed OpenAI webhook validates it and resolves the User
and current Sandbox Assignment from D1; SIP caller headers alone never authorize
a call. On attach, the Durable Object gives the Orchestrator a bounded call-start
snapshot of aliases and recent state as a harness event; tools remain
authoritative for current reads and all actions.

The Voice Frontend normally speaks one short sentence, then listens. Dog-walking
presentation language is isolated in `src/voice_flavors.ts`; machine-facing
contracts remain neutral. `end_call` closes the call, while
`open_recovery_menu` hands control back to deterministic TwiML for restart or
confirmed destructive replacement.

The OpenCode 1.x plugin tools `publish_review_bundle` and
`register_ephemeral_service` are not in the OpenCode 2 snapshot. Until they
return as Client-provided MCP tools served by this Worker, Agents cannot create
new Review Bundles or Ephemeral Services; the Worker endpoints below remain.

ACP Agents publish Review Bundles through `publish_review_bundle`. The initial
bounded implementation stores at most 1 MiB per bundle in D1 for seven days and
exposes safe unguessable `/b/<token>/<filename>` links. R2 can replace this
storage adapter after it is enabled on the Cloudflare account.

ACP Agents register listening ports through `register_ephemeral_service`. The
Voice Agent sees only a speech-safe name and opaque service ID. On request,
Dogwalk mints a one-hour Daytona signed preview URL and sends it directly by SMS;
the URL never enters spoken or model context.

Cloudflare currently guarantees an outbound WebSocket prevents Durable Object
eviction for 15 minutes. Agent updates generally keep active turns alive beyond
that window, but a completely silent long Prompt Turn could still lose its Agent
Connection. A sandbox-initiated inbound ACP connection is the hardening path for
strictly durable unattended turns.

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

A capability-scoped caller lens (a Scoped Diagnostic View, per `DOMAIN.md`) is
served read-only at `/v/<token>`. The Voice Agent tool `text_diagnostic_view`
mints a per-call unguessable link and texts it to the registered caller so they
can see this call's own activity and share it when reporting a problem. The token
is an HMAC over the Call SID, so a leaked link exposes only that one call and can
never reveal another call; it carries no expiry by design. The link is never
verbalized. Its backing row lives in the `diagnostic_views` table.

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

Outbound SMS requests include a signed callback to
`https://dogwalk.tools/sms/status`. Dogwalk records provider acceptance as
`queued`, then updates delivery status and failure code from that callback. It
must never claim carrier delivery merely because Twilio accepted the request.

The Worker also supports Cloudflare Access JWT verification through
`ACCESS_TEAM_DOMAIN` and `ACCESS_AUD`. Those variables remain empty until a
path-scoped Access application is configured for `/admin` and `/admin/*`.
