# Dogwalk PSTN Architecture

## Status

This document records the deployed architecture. `DOMAIN.md` defines the
normative domain language; `cf_worker/README.md` is the operational runbook.

Dogwalk is a PSTN interface to a multi-session ACP Client. A deliberately
engineering-weak Voice Agent lets a User supervise concurrent ACP Agents without
looking at a screen. Dog-walking language is optional presentation flavor, not
part of the protocol, tool, storage, or session-management vocabulary.

## Topology

```text
Phone
  -> Twilio PSTN and deterministic access TwiML (Sandbox start requested, not awaited)
  -> OpenAI GPT-Live over secure SIP (Voice Frontend: speech, no tools)
       -> sideband WebSocket to sandbox-keyed VoiceSession Durable Object
            -> Session Orchestrator (Responses API tool loop, gpt-6.1-sol)
       -> HTTP (/api/*) and an event WebSocket (/events) through Daytona preview proxy
            -> sandbox_agent_gateway.py
            -> opencode serve --service (the Sandbox's shared OpenCode service)
                 -> many OpenCode sessions
```

One Durable Object coordinates one retained Agent Connection and many Managed
Sessions for a Sandbox. A Voice Call temporarily attaches a GPT-Live control
sideband to that object; Twilio and OpenAI carry the media directly over SIP and
SRTP. Ending a call does not cancel Prompt Turns or close ACP Sessions.

## Responsibilities

### Cloudflare Worker

- Validates signed Twilio webhooks.
- Validates signed OpenAI incoming-session webhooks and short-lived SIP call
  correlation capabilities.
- Registers callers through speakable Invite Codes stored in D1.
- Resolves, creates, starts, and reconciles durable Daytona Sandboxes, and
  dials the caller to the Voice Agent without waiting for the Sandbox to start.
- Prewarms the Durable Object's Agent Connection as soon as a registered call
  arrives.
- Accepts GPT-Live sessions with a bounded state snapshot and routes their
  sidebands to the Durable Object keyed by Sandbox assignment.
- Provides deterministic TwiML recovery when realtime voice is unavailable.
- Serves the read-only Diagnostics surface, the public landing page and
  earcon soundboard (`/sounds`), and the Paired Browser page (`/pair`); consumes
  claimed Pairing Codes and routes browser sockets by credential identity.
- Scores audio against Voice References through the `SPEAKER` Service Binding to
  `dogwalk-speaker` (`speaker_worker/`), a stateless WASM speaker-embedding Worker
  with no public routes. Only operator calibration routes use it so far.
- Runs Voice Donations (`/voice/donation/*`): deterministic consent and rounds in
  TwiML, each round's caller-only Media Stream received by the Worker itself and
  written to the private R2 bucket `dogwalk-voice-research` (`RESEARCH_AUDIO`)
  with no link to the caller. See `cf_worker/README.md`, Voice Donations.

### VoiceSession Durable Object

- Owns the GPT-Live sideband and ACP WebSockets, but no telephone audio. The
  Agent Connection is established in the background with retries; a call talks
  while it is pending and survives losing it.
- Speaks a deterministic Opening and runs the keypad conventions and the in-call
  System Menu (restart and recovery only) without a model.
- Allocates a fresh spoken number-word-word Pairing Code on request, takes the caller's independent keypad acceptance, and
  serves Paired Browsers read-only over hibernatable WebSockets.
- Runs the Session Orchestrator: answers each GPT-Live client delegation from
  the call transcript, with neutral Managed Session and Prompt Turn tools, and
  persists its context across calls.
- Multiplexes multiple ACP Sessions over one Agent Connection.
- Persists Alias and Managed Session projections across Voice Calls.
- Preserves Permission Request choices exactly as supplied by the ACP Agent.
- Treats harness events as non-user context and addresses resulting speech only
  to the caller.

### Daytona Sandbox

- Provides the filesystem, process, network, and credential isolation required
  by coding Agents.
- Runs the Agent Gateway and OpenCode 2 from an immutable versioned snapshot.
- Receives Agent inference credentials only as Daytona Secret placeholders.
- Auto-stops after idle time and auto-archives after extended inactivity, but is
  never automatically deleted.
- Retains files and Agent-held session history across calls and restarts.

The Agent Gateway is deliberately narrow glue: it keeps the Sandbox's shared
OpenCode service running (starting `opencode serve --service` when none answers,
restarting it after an abnormal exit), proxies `/api/*` to it with the service's
credentials, and relays its `/api/event` stream to each `/events` WebSocket. Client
disconnects never stop the service, so Agent work outlives the connection, and a
TUI in a Takeover Terminal attaches to the same service. It does not implement
session semantics (`SANDBOX_AGENT_GATEWAY.md`).

## Persistence

D1 stores phone Registration, Invite Codes, Sandbox Assignments, audit and call
activity, call handoffs, and unclaimed Pairing Code hashes. A provider
ID is a cached hosting reference rather than durable identity. Confirmed provider
deletion clears the stale assignment and permits replacement while retaining the
phone Registration.

The Durable Object stores the live session projection. OpenCode retains ACP
Session history inside the durable Sandbox filesystem. These stores have
different lifecycles and must not be collapsed into one notion of a session.

## Access And Secrets

The deployment is operator-managed but the repository is public. Public
hostnames, resource IDs, local Keychain service names, and commands that retrieve
local credentials are documentation, not secrets. Secret values, private keys,
capability tokens, signed links, and sensitive production payloads never belong
in Git.

Caller identity is the normalized registered phone number. The Diagnostics
surface at `https://dogwalk.tools/admin` defaults to server-side redaction and
currently uses interim HTTP Basic authentication. Cloudflare Access JWT support
exists but is disabled until a path-scoped Access application is configured.

## Known Rough Edges

- The GPT-Live direct-SIP migration was deployed on 2026-09-15, but an
  end-to-end production call never worked; the cause was not diagnosed. On
  2026-11-07 the voice layer moved to client delegation with a Session
  Orchestrator and the Sandbox to OpenCode 2. A real call has not yet been
  verified on the new design.
- The talk-first front door, Opening, keypad conventions, and System Menu
  (2026-11-07) are verified only against local mocks; whether GPT-Live keeps
  delivering DTMF while input is muted, and whether a silent fixture reliably
  cuts off speech, need a real call.
- A completely silent Prompt Turn can outlive Cloudflare's outbound WebSocket
  retention guarantee. A sandbox-initiated connection is the hardening path.
- Browser pairing uses a spoken code typed into `/pair`, with ninety-second
  expiry feedback and independent keypad confirmation (one accepts, star
  refuses). It needs no acoustic decoder or browser microphone. End-to-end
  spoken-code usability over a real call still needs verification.
- Session deletion, Session Config Options, and ACP Elicitation are not yet exposed
  through the Voice Agent; discovery, load, and close are.
- The Diagnostics surface is read-only infrastructure, not a required user interface.

These are bounded gaps in the accepted architecture, not competing product
directions.
