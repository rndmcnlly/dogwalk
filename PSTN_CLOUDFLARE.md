# Dogwalk on Cloudflare: the PSTN deployment

Status: design frame, not an implementation plan. Written to orient a fresh
implementation session. Normative domain terms come from `DOMAIN.md`; this
document is an Agent Hosting / Voice Transport deployment proposal, not a change
to the domain model.

## What this is

The PSTN equivalent of what [lathe.tools](https://lathe.tools/) is for Open
WebUI: friends and family call a phone number and get eyes-free voice control
(Walker) over a Pack of coding Dogs running inside their own Daytona sandbox.
No screen required for the typical user. An admin/developer web view exists for
observability, not as the primary interface.

Not a company. Friends and family scale. If it sticks, a later agent-led reboot
can commercialize it; this design optimizes for a fast, legible proof of concept
over enterprise structure.

## The one load-bearing constraint

The ACP Agent (`opencode acp`) is a stdio subprocess that needs a real
filesystem, a long-lived process, and outbound network. A Cloudflare Worker
cannot host that. Daytona already hosts it well. Therefore:

> **Daytona sandboxes are the kennel where Dogs live. Cloudflare is the front
> door, the phonebook, and the switchboard, not the kennel.**

This lines up with `DOMAIN.md`'s bounded contexts exactly. Cloudflare owns Voice
Transport, the allowlist, and Voice Interaction routing. Daytona owns Agent
Hosting, ACP Integration, and Session Management.

## Resolved design questions

Four questions were investigated and settled before this document.

### 1. Can Twilio talk to OpenAI directly, skipping our server? No.

Twilio Media Streams always terminate at a WebSocket server *you* run; that
server forwards audio frames to OpenAI's Realtime WebSocket. OpenAI's
`TwilioRealtimeTransportLayer` is still your server, just with nicer ergonomics.
Someone always sits in the middle. So the Worker sits in the audio path.

Field caveat to verify early: some deployments route their Twilio WebSocket
hostname *around* the generic Cloudflare proxy ("WebSockets don't play nice with
proxied connections"). Cloudflare **Workers** are different: the Worker *is* the
WebSocket server via `WebSocketPair` / the `Upgrade` handler, not a proxy in
front of one. Confirm during the first spike, do not assume.

### 2. Is there a standard ACP-over-network transport? Yes, an Active RFD.

The ACP "Streamable HTTP & WebSocket Transport" RFD
(<https://agentclientprotocol.com/rfds/streamable-http-websocket-transport>)
defines a single `/acp` endpoint with `POST`/`GET`/`DELETE` plus a first-class
**WebSocket upgrade** (`GET /acp` with `Upgrade: websocket`) carrying raw
JSON-RPC text frames. One connection hosts many sessions. The RFD explicitly
blesses "servers MAY support only WebSocket" and calls out serverless/cloud
deployments as the motivating case.

Status: Active, targeted for v1 as additive; reference implementation in Goose;
SDK support not yet shipped. Real and converging, **not yet stabilized**. This
replaces the bespoke `/manifest`, `/tool/:name`, `/events` seam from
`voice_bridge.py`, which was a redundant hand-rolled re-encoding of what ACP
already expresses as JSON-RPC. Delete that seam.

### 3. Does OpenCode speak ACP over the network natively? No.

`opencode acp` is stdio-only newline-delimited JSON-RPC and stays alive only
while stdin is open (it self-disposes on stdin EOF). Its `--port`/`--hostname`/
`--cors` flags configure OpenCode's *internal* HTTP backend, not an ACP listener.
`opencode serve` is a *different* protocol (OpenCode's own REST/SSE), not ACP.

Consequence: the sandbox needs one small piece of glue, a **stdio<->WebSocket
shim**, described below. This is the only custom code at the DO<->sandbox
boundary.

### 4. How does the Worker reach a non-public sandbox? Daytona preview URL.

Two different Daytona preview mechanisms for two different consumers:

- **Standard preview URL** (`https://{port}-{sandboxId}.proxy.daytona.work` +
  `x-daytona-preview-token` header) is "for programmatic access where you
  control the HTTP headers." The Durable Object uses this for the ACP WebSocket.
  Token resets on sandbox restart, so the DO re-fetches via `get_preview_link()`
  after any start. Send `X-Daytona-Skip-Preview-Warning: true`.
- **Signed preview URL** (token embedded in URL, no headers, up to 24h) is "for
  sharing links with users who cannot set custom headers." This is the per-user
  web-access ritual: SMS a signed URL so a caller's phone browser can peek at
  their own sandbox.

No inbound sandbox exposure beyond Daytona's authenticated preview proxy. Raw
ACP-over-stdio never leaves the sandbox; only the shimmed WebSocket does. If
inbound exposure ever bothers us, the hardening path is a sandbox-initiated
outbound WebSocket up to the DO instead; documented, not built.

## Architecture

```text
Phone -> Twilio -> CF Worker /voice  (allowlist lookup in D1)
                      |
                      +- unknown number -> TwiML reject / request-access ritual
                      +- known, cold     -> TwiML "waking your workspace" + hold,
                      |                      async Daytona start/create, <Redirect> /voice
                      +- known, warm     -> TwiML <Connect><Stream> to Worker WS
                                               |
                                       Durable Object (one per active call / sandbox)
                                          |- WS <-> Twilio Media Stream (mulaw 8kHz)
                                          |- WS <-> OpenAI Realtime (Walker persona + tools)
                                          |- WS <-> sandbox /acp  (ACP JSON-RPC, standard transport)
                                          |     via Daytona standard preview URL + token
                                          |- holds CallLease; mints observer/web-access tokens
                                          |- projects Managed Sessions (alias, state, usage)
                                                |
                                    Daytona sandbox (looked up by label from phone number)
                                       |- stdio<->WebSocket ACP shim  (the only glue)
                                          |- opencode acp (stdio, stdin held open)
                                             +- Dogs = ACP sessions, multiplexed on one connection

Admin:  CF Access -> /admin/*  -> Mission Control (reads DOs + D1), multi-sandbox
User:   SMS signed-preview link -> scoped view of own sandbox's Dogs
```

### Audio path

`Twilio Media Stream WS  <->  Durable Object  <->  OpenAI Realtime WS`.
G.711 mulaw 8kHz passes through with no resampling (same as `voice_bridge.py
serve` does today). Barge-in via `input_audio_buffer.speech_started` ->
Twilio `clear`. The DO is the only place holding Twilio + OpenAI credentials.

### Control path (the ACP seam)

`Durable Object  <--ACP-over-WebSocket-->  sandbox shim  <--stdio-->  opencode acp`.

The DO is the transport half of the ACP Client. `SessionManager.dispatch()`
operations become ACP JSON-RPC messages directly, with no translation layer.
`session/update` notifications and `request_permission` server->client requests
flow natively over the WebSocket. The 200ms poll-then-fan-out pump in
`voice_acp_backend.py` disappears; this is a subtraction of code.

## Cloudflare building blocks

Stay Cloudflare-native. Convex was considered and rejected: it would be a second
control plane outside Cloudflare duplicating what DOs + D1 already provide, at
friends-and-family scale not worth the extra vendor.

- **D1** (SQLite): the "little database." Allowlist and audit log. A row is
  roughly `phone_number -> daytona_label` plus policy (ephemeral | persistent,
  permission autonomy level, display name, created_at, last_seen). Daytona
  labels are the join key; the Worker never hardcodes sandbox IDs. Sandbox
  lookup is "list sandboxes filtered by label," matching the stated core goal:
  "per-user Daytona sandboxes looked up by label."
- **Durable Objects**: per-call / per-sandbox live coordination. Holds the three
  WebSockets, the `CallLease`, and the Managed Session projection. This is the
  single biggest architectural win: the threading + asyncio hybrid that is the
  worst part of the current Python core (`AcpRuntime` background loop +
  `ThreadingHTTPServer`) **dissolves** into the DO's single-threaded event model.
  `CallLease` and `ObserverTokens` map onto DO-held state directly.
- **Cloudflare Access / Zero Trust**: gates `/admin/*` for the admin/dev tier.
- **Workers Static Assets**: serves the Mission Control web app.

## Onboarding and the cold-sandbox TwiML loop

The interesting sequencing problem: an allowlisted-but-unprovisioned friend calls
before their sandbox is warm. Daytona cold-start plus `opencode acp` init takes
seconds. Plain TwiML covers it:

1. Twilio hits Worker `/voice`. Worker looks up caller number in D1.
2. Unknown number -> TwiML `<Say>` rejection (or a "text to request access"
   ritual), hang up.
3. Known, sandbox running -> TwiML `<Connect><Stream>` to the DO's WS. Into
   Walker.
4. Known, sandbox cold/absent -> TwiML `<Say>` "waking your workspace, one
   moment" + `<Pause>`/hold, while the Worker kicks off Daytona start (or
   ephemeral create from a snapshot), then `<Redirect>` back to `/voice`, which
   now finds it warm and connects. Classic TwiML polling loop.

Validate the shim + `opencode acp` are actually ready (an ACP `initialize`
round-trip succeeds) before the redirect connects the caller, so Walker never
opens on a dead kennel.

## Two-tier auth

- **Admin / dev**: Cloudflare Access in front of `/admin/*` (Google/GitHub SSO).
  Full Mission Control across every sandbox: Dogs, Prompt Turns, telemetry, cost,
  ACP update stream. This is the existing `webrtc_spike.html` monitor, reskinned
  to read from DOs + D1 instead of polling a local Python process, and widened
  from one pack to many sandboxes.
- **User / the ritual**: a phone user proves control of their number to unlock a
  scoped read-only web view of *their own* sandbox. Natural flow: on a call the
  user asks Walker "give me web access"; a Walker tool has the Worker mint a
  short-lived Daytona **signed preview URL** (or a scoped app cookie) and SMS it
  to the caller ID. This is the existing `ObserverTokens` concept promoted to a
  first-class per-user web capability.

## What to keep, port, retire

Keep (runs in the sandbox, stays Python, proven core):
- `SessionManager.dispatch()` and its poll surfaces: the neutral operation
  surface matching `DOMAIN.md`'s operations table. Carry forward as-is.
- The ACP Integration adapter (`AcpClientAdapter`, session_update /
  request_permission / create_elicitation handling): the correct SDK boundary.
- The YAML scenario language (`text_spike.py`): a transport-independent
  executable spec; keep as the regression harness.

Port (to JS/TS on Cloudflare):
- `voice_bridge.py serve` role -> Worker + Durable Object: Twilio webhook,
  signature validation, Media Stream WS <-> OpenAI Realtime WS, plus the new
  allowlist / onboarding / label-resolution logic.
- The Walker persona: `INSTRUCTIONS`, `DOG_BRIEFING`, `TOOLS`. Single-source
  these; today the "a Dog completed" notification wording is duplicated across
  the HTML, the pump, and `INSTRUCTIONS` (shotgun-surgery hazard). One home.
- `webrtc_spike.html` Mission Control -> Worker static asset + DO-backed data,
  multi-sandbox.

Write new (small, the only glue):
- The sandbox-side **stdio<->WebSocket ACP shim**: accept `GET /acp` upgrade,
  spawn/hold `opencode acp` with stdin open, pipe JSON-RPC frames both ways.
  Closing the shim's stdin pipe is the clean "close Managed Session" signal.
  One shim per sandbox multiplexes all Dogs (one ACP connection, many sessions),
  which pays down the "one subprocess per Dog" debt `DOMAIN.md` flags as hosting
  policy rather than identity.

Retire / demote to dev-only:
- The bespoke `/manifest`, `/tool/:name`, `/events` protocol and
  `voice_acp_backend.py`: replaced by standard ACP-over-WebSocket.
- The browser WebRTC voice path in `webrtc_spike.py`, `mock-backend`, and the
  `afplay` `simulate` path: harness scaffolding, not production.

## Domain-model note

This deployment does not add domain concepts. It does make one existing
principle concrete: **one Agent Connection per sandbox, many Managed Sessions
multiplexed over it** (`DOMAIN.md` lines 30, 121). Daytona is an Agent Hosting
adapter and a Sandbox provider, not a domain concept. Twilio and Cloudflare are
Voice Transport / deployment choices. Keep canine terms in Walker's voice layer
and neutral ACP/session terms in the DO's core plumbing, as today.

Open modeling question surfaced here: today aliases and Managed Session
projections live only in memory (only OpenCode persists session history). A
Cloudflare deployment can finally persist the projection (alias, state, updates,
usage) in D1 / the DO, separating the concepts `DOMAIN.md` already distinguishes.
Decide whether the alias->sessionId binding is DO-durable or re-derived via
`session/list` + `session/load` on reconnect.

## Suggested first spikes (in order)

1. **Sandbox ACP shim + preview reachability**: in a Daytona sandbox, run the
   stdio<->WS shim over `opencode acp`; from a local script (standing in for the
   DO) open the ACP WebSocket via the standard preview URL + token, complete an
   `initialize` handshake and one `session/new` + `session/prompt`. Proves the
   whole control plane end to end with zero audio.
2. **Worker `/voice` + D1 allowlist + TwiML onboarding loop**: no audio yet.
   Prove unknown/warm/cold routing and the cold-start redirect.
3. **DO audio bridge**: Twilio Media Stream <-> DO <-> OpenAI Realtime, Walker
   persona, tool calls relayed to spike-1's ACP WebSocket.

Each spike is independently demonstrable and eyes-free-testable.
