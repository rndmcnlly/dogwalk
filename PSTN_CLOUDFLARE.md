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
  -> Twilio PSTN and deterministic access/wake-up TwiML
  -> OpenAI GPT-Live over secure SIP (Voice Frontend: speech, no tools)
       -> sideband WebSocket to sandbox-keyed VoiceSession Durable Object
            -> Session Orchestrator (Responses API tool loop, gpt-6.1-sol)
       -> ACP over WebSocket through Daytona preview proxy
            -> sandbox_acp_gateway.py
            -> opencode acp over stdio
                 -> many ACP Sessions
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
- Resolves, creates, starts, and reconciles durable Daytona Sandboxes.
- Accepts GPT-Live sessions with a bounded state snapshot and routes their
  sidebands to the Durable Object keyed by Sandbox assignment.
- Provides deterministic TwiML recovery when realtime voice is unavailable.
- Serves Review Bundles, SMS callbacks, sandbox publication APIs, and Mission
  Control.

### VoiceSession Durable Object

- Owns the GPT-Live sideband and ACP WebSockets, but no telephone audio.
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
- Runs the ACP Gateway and OpenCode 2 from an immutable versioned snapshot.
- Receives Agent inference credentials only as Daytona Secret placeholders.
- Auto-stops after idle time and auto-archives after extended inactivity, but is
  never automatically deleted.
- Retains files and Agent-held session history across calls and restarts.

The ACP Gateway is deliberately narrow glue: it upgrades `GET /acp` to a
WebSocket, owns one `opencode acp` subprocess, and forwards newline-delimited ACP
JSON-RPC frames between WebSocket and stdio. It does not implement session
semantics.

## Persistence

D1 stores phone Registration, Invite Codes, Sandbox Assignments, audit and call
activity, publication metadata, SMS delivery state, and call handoffs. A provider
ID is a cached hosting reference rather than durable identity. Confirmed provider
deletion clears the stale assignment and permits replacement while retaining the
phone Registration.

The Durable Object stores the live session projection. OpenCode retains ACP
Session history inside the durable Sandbox filesystem. These stores have
different lifecycles and must not be collapsed into one notion of a session.

## Publication Surfaces

Review Bundles are bounded Agent-published files for later visual inspection.
Dogwalk currently stores them in D1 behind unguessable expiring bearer URLs.

Ephemeral Services are speech-safe registrations of live sandbox ports. Dogwalk
mints short-lived Daytona signed preview URLs only when the User requests one.
Links are sent directly by SMS and never enter Voice Agent or ACP Agent context.

Twilio accepting an outbound message means only `queued`. Signed status callbacks
update eventual delivery or failure state. US delivery from the current local
number additionally depends on approved A2P 10DLC registration.

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
- Review Bundle and Ephemeral Service tools are absent from the OpenCode 2
  snapshot until they return as Client-provided MCP tools.
- A completely silent Prompt Turn can outlive Cloudflare's outbound WebSocket
  retention guarantee. A sandbox-initiated connection is the hardening path.
- Session discovery/load, explicit close/delete/config operations, and ACP
  Elicitation are not yet exposed through the Voice Agent.
- Review Bundles use bounded D1 blob storage until R2 is enabled.
- SMS delivery remains unavailable until Twilio approves the A2P Brand,
  Campaign, and sender association.
- The Diagnostics surface is read-only infrastructure, not a required user interface.

These are bounded gaps in the accepted architecture, not competing product
directions.
