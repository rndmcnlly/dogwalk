# Dogwalk Cloudflare Worker

This Worker is the production Dogwalk service at `https://dogwalk.tools`. It
provides Twilio PSTN voice, phone registration, Daytona sandbox lifecycle,
Managed Session multiplexing, publication links, SMS delivery, recovery, and
diagnostic Mission Control.

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
npx wrangler secret put TWILIO_ACCOUNT_SID
npx wrangler secret put ADMIN_PASSWORD
npm run deploy
```

`DOGWALK_IDENTITY_SECRET` should be a stable random value. Changing it prevents
recovery by provider label for assignments that have not yet stored a Daytona
ID.

`DAYTONA_SNAPSHOT` is currently `dogwalk-acp-20260721-v3`. It contains the ACP
Gateway, OpenCode `1.18.4`, passwordless sandbox-local `sudo`, network diagnostic
utilities, and global OpenCode tools for Review Bundles and Ephemeral Services.
The OpenCode config selects `opencode/deepseek-v4-flash-free`.

From the repository root, build a new immutable version rather than replacing
this snapshot in place:

```bash
source ~/.tokens/daytona-api
uv run --script build_sandbox_snapshot.py --name dogwalk-acp-YYYYMMDD-vN
```

Verify the new snapshot before changing `wrangler.toml` and deploying the Worker.

The Worker submits this mapping at sandbox creation:

```json
{
  "snapshot": "configured-snapshot-name"
}
```

No inference key is injected for the initial free-model deployment. A clean
Daytona sandbox with OpenCode `1.18.4` and zero stored credentials successfully
ran this model on 2026-07-20. Free-model availability is temporary, and prompts
may be used for provider improvement, so this tier must not be presented as
suitable for confidential code. If a credentialed provider is added later, use
an allowlisted Daytona Secret rather than a snapshot layer or workspace file.

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

`voice_session_test.py` runs mock Twilio, OpenAI Realtime, Daytona preview, and
ACP WebSockets around a local Worker. It checks μ-law passthrough, barge-in
clearing, ACP initialization, Managed Session creation, asynchronous Prompt
Turns, signed-preview SMS, and call termination without consuming provider
traffic.

## Voice Sessions

Warm calls use Twilio bidirectional Media Streams. A sandbox-keyed SQLite Durable
Object owns the live Agent Connection and persisted Managed Session projection;
the Voice Call temporarily attaches Twilio and OpenAI Realtime sockets to it.
Ending the call closes the audio sockets but does not cancel active Prompt Turns
or close ACP Sessions.

The production voice model defaults to `gpt-realtime-2.1`. The bridge forwards
Twilio and Realtime PCMU payloads without transcoding. Voice Agent tools use the
neutral operations `create_managed_session`, `begin_prompt_turn`,
`inspect_managed_session`, `list_managed_sessions`, `cancel_prompt_turn`, and
`resolve_permission`.

The Voice Agent normally speaks one to three words, then listens. Dog-walking
presentation language is isolated in `src/voice_flavors.ts`; machine-facing
contracts remain neutral. `end_call` closes the call, while
`open_recovery_menu` hands control back to deterministic TwiML for restart or
confirmed destructive replacement.

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

## Mission Control

Mission Control is at `https://dogwalk.tools/admin`. The interim username
is `adam`; its generated password is stored in macOS Keychain:

```bash
security find-generic-password -a adam -s dogwalk-admin -w
```

The dashboard receives authenticated server-sent events from
`/admin/api/events` every ten seconds. Streams close after five minutes so the
browser reconnects and revalidates authentication automatically.

Live calls are keyed by Twilio Call SID and display a bounded, source-aware
activity timeline. The reserved sources are `voice`, `access`, `hosting`,
`menu`, and `acp`. Mission Control starts in demo-safe mode: phone numbers,
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
