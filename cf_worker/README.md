# Dogwalk Cloudflare Worker

This Worker provides phone registration, Daytona sandbox create/wake, a TwiML
status menu, and read-only Mission Control.

## Setup

```bash
npm install
npm run db:init:remote
npx wrangler secret put TWILIO_AUTH_TOKEN
npx wrangler secret put DAYTONA_API_KEY
npx wrangler secret put DOGWALK_IDENTITY_SECRET
npx wrangler secret put ADMIN_PASSWORD
npm run deploy
```

`DOGWALK_IDENTITY_SECRET` should be a stable random value. Changing it prevents
recovery by provider label for assignments that have not yet stored a Daytona
ID.

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
uv run --script smoke_test.py
```

It checks registration, retry limits, sandbox creation and readiness, status
execution, identity isolation, webhook signatures, and fail-closed admin access.

## Mission Control

Mission Control is at `https://dogwalk.lathe.tools/admin`. The interim username
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
https://dogwalk.lathe.tools/voice/status
```

Use `POST`. Terminal callbacks close live-call cards; a five-minute activity
window is the fallback for missed callbacks.

The Worker also supports Cloudflare Access JWT verification through
`ACCESS_TEAM_DOMAIN` and `ACCESS_AUD`. Those variables remain empty until a
path-scoped Access application is configured for `/admin` and `/admin/*`.
