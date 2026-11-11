# Dogwalk Domain Language

## Status and Authority

This document is Dogwalk's canonical Ubiquitous Language. It is normative for new design and refactoring. A mismatch in implementation code is debt, not an alternate domain definition.

The project uses this order of authority:

1. `AGENTS.md` supplies the operating contract and irreducible domain guardrails to every development session.
2. `DOMAIN.md` defines canonical terms, boundaries, relationships, and lifecycle distinctions.
3. Tests and scenarios specify behavior executably.
4. Git history preserves experiments and superseded terminology.

Update this document in the same change that introduces or changes a domain concept. Put unresolved modeling choices under Open Questions rather than allowing code to choose a meaning implicitly.

## Product Definition

Dogwalk is an eyes-free ACP Client and multi-session coding manager. It gives a User the capability surface of a visual multi-session coding client through hands-free voice, without requiring access to a screen, mouse, or keyboard.

The Voice Agent is the realtime conversational interface through which the User supervises coding work. It translates conversational intent into precise, neutrally named session-manager operations. Coding work is performed by ACP Agents such as OpenCode, not by the Voice Agent.

The Voice Agent is one character performed by two components: a Voice Frontend that holds the conversation and a Session Orchestrator that exercises engineering judgment and operates Managed Sessions. The User experiences a single character. The Voice Frontend may refer to the Session Orchestrator through its Theme's fiction for that split (in Park, its own head or mind: "let me think"), which keeps an account of its limits honest without naming models; when the User asks about the architecture, it explains the split candidly.

Dogwalk presents as an agentic development environment driven by a coworker. ACP Agents should see ordinary requests from a colleague: they need not know they are supervised by voice, and anything specific to calls or speech stays inside Dogwalk.

## Modeling Principles

- ACP terminology governs protocol integration and anchors the session-management model.
- Dogwalk is a product codename, not the source of its domain vocabulary.
- Theme language (such as Park's canine fiction) is optional presentation in UI copy, Voice Agent narration, and deterministic speech. It must not appear in tool names, operation names, harness events, earcon names, protocol payloads, persisted schemas, or prompts sent to ACP Agents.
- Every Voice Agent rule is stated in neutral terms. A Theme presents rules and never carries one: removing the Theme must remove no behavior.
- The core model must not depend on OpenCode, a particular model provider, a particular voice transport, or Daytona.
- A protocol entity, an application projection, and its spoken presentation are different things even when they currently have a one-to-one relationship.
- Derived state must not be presented as ACP-defined state. Dogwalk may infer that a session is ready or that a turn needs attention, but those are Client projections.
- Opaque protocol identifiers stay out of speech. The Voice Agent refers to work by purpose, recency, and pronounceable Alias.
- Whatever the Voice Agent can change, it can also sense without changing it: every setter (a preference, a pairing, what a Paired Browser shows, which session is followed) has a read-only counterpart, and the state that matters at the start of a Voice Call (including a Paired Browser and what its page showed recently) arrives with the call.
- The current process topology is an implementation choice. ACP permits one Agent connection to support multiple concurrent sessions.

## Bounded Contexts

### Voice Interaction

Owns the live conversation, Voice Agent personality, spoken reference resolution, optional narration flavor, earcons, interruption, muting, and speech-safe summaries. A Theme's language may appear here as an acknowledged metaphor, but is never canonical domain language.

### Session Management

Owns Managed Sessions, Prompt Turns, workspaces, aliases, projected state, concurrency, queues, and User attention. It exposes neutral operations that any interaction surface could use.

### ACP Integration

Owns ACP initialization, capability negotiation, sessions, prompts, updates, stop reasons, permissions, configuration, and protocol-version translation. It speaks ACP's terms exactly and shields Session Management from SDK and wire-version details.

### Agent Hosting

Owns how an ACP Agent is located, launched, connected, authenticated, isolated, and stopped. A local OpenCode subprocess and a remote sandbox-hosted Agent are alternate hosting arrangements.

### Voice Transport

Owns realtime audio transport, speech turn detection, browser or telephone media, and provider-specific function calling. OpenAI GPT-Live over SIP is the current adapter, not the definition of the Voice Agent.

### Access Control

Owns phone Registration, Invite Codes, and Invitations at the PSTN boundary. A
registered phone number identifies a User for access purposes; Dogwalk does not
maintain a separate personal name in this context. Telephony providers and
sandbox hosts are adapters, not identity authorities. Phone numbers and the
invitation graph stay here and in the caller's Voice Session state: they never
reach ACP Agents, Sandbox files, or hosting-provider labels.

## Context Map

```text
User speech
    |
    v
Voice Frontend / Voice Interaction
    |  Handoffs (transcript) up; context and speech requests down
    v
Session Orchestrator
    |  neutral commands; receives harness events
    v
Session Management
    |  translates managed lifecycle into ACP operations
    v
ACP Integration
    |
    v
ACP Agent

Agent Hosting supplies connections and isolation to ACP Integration.
Voice Transport carries the conversation used by Voice Interaction. A direct
provider-to-model media path may be controlled through a separate application
sideband; the media path and control path remain parts of one Voice Call.
```

## Roles

### User

**Context:** Voice Interaction

The human principal whose intent, authorization, and attention govern the work. The User may speak imprecisely and should not need to pronounce technical identifiers.

### Voice Agent

**Context:** Voice Interaction

The single character the User talks to: socially fluent, a little modest about engineering detail, and authoritative about session state. It presents itself through a Theme (DOMAIN.md, Themes), at an Intensity it adjusts to the User. It is composed of a Voice Frontend and a Session Orchestrator.

**Not:** An ACP Agent, coding harness, Agent host, or a single model.

### Voice Frontend

**Context:** Voice Interaction

The realtime speech component of the Voice Agent. It owns turn-taking, interruption, conversational repair, persona, and spoken phrasing. It has no session tools: when the User asks for or about work, it raises a Handoff and waits for the Session Orchestrator rather than guessing. It treats Orchestrator context as background and Orchestrator speech requests as things to say in its own words.

**Not:** An engineering authority or a source of session state.

### Session Orchestrator

**Context:** Session Management and Voice Interaction

The reasoning component of the Voice Agent. It reads Handoffs and harness events, resolves references, writes engineering briefs for ACP Agents, invokes the neutral operations, and decides what the Voice Frontend should know (unspoken context) or say now (speech requests). Its conversation context persists across Voice Calls; between calls it accumulates events but takes no action.

It is a client of Session Management, never a replacement for it: Managed Session and Prompt Turn projections remain authoritative, and the Orchestrator reports completion only from them.

Its context is bounded by its own judgment: it compacts by rewriting its Memory, which drops earlier conversation from its context (see Memory).

**Not:** An ACP Agent, the Session Manager, or a store of record.

### Memory

**Context:** Voice Interaction

The Session Orchestrator's curated, bounded note of what matters across Voice Calls: the User's preferences, ongoing projects, what each Managed Session is for, commitments, and loose ends. The Orchestrator alone writes it, and rewrites it whole rather than appending: a **Compaction** replaces the Memory and removes the conversation before a safe point (the start of the current Voice Call, or a recent stretch when the call is long) from the Orchestrator's context, archiving it for diagnostics. Compaction is voluntary; the Orchestrator senses its context size and decides when, typically as a chapter of work closes, and does not mention it to the User unless asked. The Memory is pinned at the head of every Orchestrator model call and informs the Opening's welcome line. A backstop drops the oldest context when the Orchestrator neglects to compact and its context grows near the model's limit, and records that it happened.

The User does not edit the Memory directly; they may ask the Voice Agent to remember or forget something, which the Orchestrator honors at its next Compaction. It is readable, read-only, on the Diagnostics Surface and on a Paired Browser.

**Not:** The Orchestrator's conversation history, an ACP Session's history, a Voice Agent Skill, or a note in the Workspace.

### ACP Client

**Context:** ACP Integration

The protocol role that initializes connections, creates or resumes sessions, sends prompts, receives updates, presents permission choices, and controls access to Client capabilities.

Dogwalk is the ACP Client. The Voice Agent may initiate Client operations through Dogwalk's neutral tool surface, but the Voice Agent itself is not the complete protocol implementation.

### ACP Agent

**Context:** ACP Integration

An ACP-compatible coding harness that owns conversation context, processes prompts, uses models and tools, reports updates, and returns a stop reason for each Prompt Turn.

OpenCode is the current Agent implementation. Other ACP-compatible implementations must be substitutable in principle.

**Not:** A model, subprocess, session, Prompt Turn, or task.

## Themes (provisional)

### Theme

**Context:** Voice Interaction

A presentation of the neutral session model for the voice surface. A Theme is a **conceit** (the mapping from domain concepts to the Theme's words, including how the Voice Frontend speaks of the Session Orchestrator) played in a **genre** (the storyteller's stance toward that material), at a **register** that moves along an Intensity ladder, **cast** with a GPT-Live voice and **scored** with earcons. It also words the deterministic speech (the first-call welcome, the keypad status readout).

Themes exist only above ACP; ACP prompts, tools, schemas, harness events, and anything in the Sandbox never use them. A Theme carries no rules, so the Voice Agent behaves the same under any Theme. Each Theme keeps authoring notes outside the standing prompts, recording the intent its surface must keep serving, for its authors and for Runtime Dramaturgy: why the User and the Voice Agent are distinct, what authority the Voice Agent holds over sessions and Workspaces, what a closer look (Paired Browser, Whiteboard, Human Takeover) means in the conceit, how power stays where it is, and which tropes it draws on. `cf_worker/src/themes.ts` holds the Themes and their notes.

| Theme | Conceit | Genre |
|---|---|---|
| Park (default) | a dog walk: sessions are dogs, the Voice Agent is the walker | magical realism, delivered deadpan: the impossible made mundane |
| Survey | a survey ship in orbit: sessions are away teams, Workspaces are planets, the User is the captain | space opera and planetary romance, in measured wonder: the mundane made marvelous |
| Patrol | a present-day cyber operation under mission command: sessions are operatives, tool calls are their drones, the User is Actual | military science fiction, laconic over hyperviolent vocabulary aimed only at code: the mundane made lethal |
| Plain | none: the domain vocabulary shows through | plain |

Plain is both a User choice and the regression fixture that shows the rules never live in a Theme. Park is the default. The User switches by asking, and the choice is kept with their Sandbox's Voice Session state like the Earcon Style. GPT-Live fixes a voice for the life of a voice session, so a switch bounces the Voice Call: the Session Orchestrator writes the new voice's first line in the new Theme, re-presenting where things stand, then the voice session ends and the same Voice Call reconnects to a fresh one, which opens with that line and no caller-ID clause. The register carries across the bounce: the Session Orchestrator names the rung the conversation stood at, the line is written at it, and the fresh Voice Frontend is told to start there. Aliases stay across a switch; the User may ask for sessions to be renamed in the new conceit's naming style, and the Voice Agent does so only then. The public pages stay Park for now.

**Not:** A rule set, a persona with its own capabilities, or a visual color scheme.

### Intensity

**Context:** Voice Interaction

The ladder a Theme's register moves along, from plain words to the conceit at full strength, and the Voice Agent's current position on it. The Voice Agent chooses it rather than the User setting it: it rises when the User plays along and falls when the User is frustrated or deep in a problem. An explicit request to lean into the Theme climbs at once, and a request to drop it goes to plain words. Intensity is independent of length: brevity governs how much is said and the register governs which words, so a standing preference for short replies never lowers the register, and a short line can sit at the top rung. The clarity floor holds at every rung: Permission Request choices, failures, destructive actions, numbers, and anything the User must act on are stated plainly; the conceit may frame a fact but never replace it.

Moving the register along the ladder is drama management.

**Not:** A setting, or the Voice Agent's matching of the User's technical sophistication, which is independent of Intensity.

### Runtime Dramaturgy

**Context:** Voice Interaction

The performing Voice Agent consulting its Theme's genre sources (the tropes the Theme draws on) and authoring notes during a live Voice Call, to interpret the conceit faithfully: how a moment the conceit never anticipated should be presented, or what a closer look means in this fiction. Only the Session Orchestrator can consult; the Voice Frontend asks it by thinking it over, like any other work, when the User asks to lean into the Theme or a moment falls outside the conceit. It arrives as the `runtime-dramaturgy` Voice Agent Skill, rendered from the active Theme's authoring notes (Plain has no conceit to interpret). The Session Orchestrator may ask a Managed Session for ordinary research on a genre source, such as summarizing a trope page, as a coworker would, without mentioning the Theme; ACP Agents never perform the Theme.

**Not:** Drama management, which adapts the register to the audience (Intensity). Dramaturgy answers to the work; drama management answers to the User.

### Park

Park's conceit, kept here because it grew up with the domain model: the dogs are not ordinary dogs, but the walker finds them perfectly normal and never explains the magic. Nothing here is committed yet: a `?` marks a mapping still under exploration, and none of these words are canonical domain terms.

| Fiction | Domain concept | Status |
|---|---|---|
| Dog | Managed Session (wrapping one ACP Session) | likely |
| Tag | Alias; tags can be changed | likely |
| Untagged dog the walker knows about | Persisted Session discovered through ACP but not loaded | ? |
| On the leash | Managed Sessions the Voice Agent is actively attending | ? |
| Letting a dog go | Close Managed Session; its history remains and it can be leashed again with a new tag | ? |
| Archived dog | perhaps simply "known but not on the leash"; ACP has no archive operation | ? |
| Collar | the Orchestrator's per-session card (tag, errand, last result) that bounds its context, or the limit on dogs per leash | ? |
| The walker's voice | Voice Frontend: quick, conversational, can run ahead of its head and should say so | likely |
| The walker's head (mind) | Session Orchestrator: thinks, remembers, holds the leash; handing off is "thinking it over" | likely |
| Walker | the Voice Agent as a whole; it sends, checks, and recalls dogs and never claims to do their work | likely |
| Walker skill | Voice Agent Skill, as opposed to a dog's own skills in the Sandbox | likely |
| Sniffing, digging, fetching | Agent work inside a Prompt Turn, done by dogs, not the walker | likely |
| Following a dog | Followed Session: the walker keeps up with one dog and narrates it; observing, not commanding | likely |
| Ranging | a working Managed Session that is not followed: out of earshot until it bids for attention or comes back | likely |
| Pulling at the leash | Bid: a ranging dog asking for the walker's attention | likely |
| Waiting at a gate | Permission Request: a dog waiting for the User's say-so | likely |
| Looking back to ask which way | Elicitation: a dog asking the User for information | ? |

Every Theme must keep power where it is, and Park shows how: dogs (ACP Agents) act in the Sandbox and often take initiative; the User holds authority (intent and Permission Requests); the walker holds attention and translation. The walker suggests errands and follows dogs; it does not command them, and a dog may pull it along.

The Voice Agent mirrors the caller's sophistication, especially on technical topics: modest and plain with a newcomer, fluent with someone who already speaks about agents, sessions, and delegation, so existing agentic literacy amplifies the experience.

## Core Concepts

### Registration

**Context:** Access Control

Authorization for one phone number to use Dogwalk. The normalized phone number
is the Registration's identity and is unique. Registration records which Invite
Code authorized it and when it occurred, but does not contain a personal name,
sandbox identity, or hosting policy. When the Invite Code belongs to an
Invitation, the Registration is the durable inviter -> Invitation -> invitee
edge, and the invitee can ask the Voice Agent who invited them (the inviter's
number).

### Invite Code

**Context:** Access Control

A speakable capability that authorizes Registration. An Invite Code may have an
expiration time and a maximum number of uses. No expiration means it remains
valid indefinitely; no maximum means unlimited registrations. A code is usable
only while unexpired and below its maximum, and each phone number may register
only once regardless of how many codes it knows. An operator mints Invite Codes
directly; a User mints one by creating an Invitation.

### Invitation

**Context:** Access Control

A User-created, single-use Invite Code with provenance: the inviting User
(resolved from the authenticated Voice Call, never from what a model claims),
an optional bound recipient number, and a lifecycle of active, then redeemed,
expired (24 hours after creation), or revoked. A User may hold at most three
active Invitations. Unbound, the words work for whichever unregistered phone
redeems them first; bound, only for that number. The inviter binds or changes
the recipient only while it is unredeemed, and only after the Voice Agent read
the number back and the inviter confirmed it; this records an intended
recipient and does not verify that the recipient controls the number, which
only their own Voice Call does. Redemption goes through the ordinary
Registration ritual and applies expiration, single use, revocation, and the
bound number in one transaction: a failed claim consumes nothing and records no
edge. Removing an Invitation from the User's list revokes it if still active
and hides it; nothing is deleted, so provenance and the Registration it
produced survive. The inviter hears each Invitation's status, when it was
redeemed, and by which number; the invitee may hear the inviter's number. The
operator-wide graph is visible only on the Diagnostics Surface or in D1. Its
read-only Access Control projection includes hidden Invitations, distinguishes
intended recipients from registered invitees, and links each Invitation to its
inviter and redeeming Registration across generations. The default projection
masks phone numbers and omits Invite Code words; the operator's explicit verbose
opt-in reveals them.

**Not:** An operator Invite Code (no inviter, any expiry or use count), a
Registration, or proof of identity.

### Voice Call

**Context:** Voice Transport

One provider-mediated live audio connection between a User and Dogwalk. A Voice
Call has transport identity and lifecycle independent of Agent Connections,
Managed Sessions, and Prompt Turns. Diagnostic Call Activity records the
ordered registration, hosting, and menu events observed during that call; it is
an operational projection, not conversation history. ACP Integration may later
project concise protocol envelopes into the same timeline when they are
correlated with a Voice Call. Full prompts, model output, and tool payloads do
not belong inline in Call Activity when they are large; store bounded diagnostic
details or references. The Diagnostics Surface presents a redacted projection by
default and requires an explicit, session-only opt-in to reveal verbose details.

The Session Orchestrator may read the current User's recent Call Activity to explain interruptions, and each call begins with a since-last-call summary (how the previous call ended, including provider errors such as moderation, recovery actions, failed Prompt Turns, and its last words) as context for the Opening.

A registered caller talks first: the Voice Call connects to the Voice Agent at once while the Sandbox is started in parallel, and the Agent Connection is established in the background. Until it is ready, Session Management answers inspection and listing from its persisted projection, operations that need the ACP Agent fail fast with a neutral "still starting" error rather than waiting, and the Session Orchestrator keeps conversing and acts on held requests once the connection is ready. Losing the Agent Connection mid-call makes Managed Sessions unavailable but does not end the Voice Call. Only a Sandbox the provider reports broken sends the caller to deterministic recovery before the Voice Agent. A provider ending the voice connection is not a User hanging up, and neither cancels Prompt Turns; only a Sandbox restart or replacement interrupts Agent work. A Voice Call may contain several voice sessions (after a drop, the recovery path can start another, and a Theme switch bounces to a fresh one with the new voice). A voice session ends at the first of the provider's close event or the loss of the control sideband; the Voice Call ends only when the telephony provider reports it complete, and that report also closes out any voice session whose end was never observed. Each voice session's transcript is saved as it grows, so one whose end was never observed (the Durable Object reset mid-call) still files its transcript and last words when the telephony report arrives or the next voice session attaches.

### Opening

**Context:** Voice Interaction

The first words of a Voice Call, composed deterministically by Dogwalk with no model call on the critical path: a terse caller-ID verification clause (from the carrier's STIR/SHAKEN attestation), the product name, a welcome line, and an invitation to speak. The welcome line was written by one short Session Orchestrator model call when the previous Voice Call ended; deterministic clauses about Managed Session changes since then (a Prompt Turn stopped or failed, a Permission Request arrived, a recording was saved) and about an abnormal previous ending are added at connection. A User's first call after Registration gets a fixed welcome instead, and the Session Orchestrator follows it with a short spoken tour while the new Sandbox is created. The Session Orchestrator is told the Opening's exact words and does not repeat them.

### Keypad Conventions

**Context:** Voice Interaction

Telephone keypad presses have fixed meanings that depend on the mode of the Voice Call, and the fixed meanings bypass the Session Orchestrator:

| Mode | Key | Meaning |
|---|---|---|
| Conversation | star | Open the System Menu |
| Conversation | pound | Stop talking: the Voice Frontend stops speaking and listens |
| Conversation | one | Instant status: a deterministic readout of Managed Sessions from the persisted projection |
| Conversation | zero | Repeat the Voice Frontend's last utterance |
| Conversation | other digits | Caller input, sent to the Session Orchestrator with the conversation around it |
| Voice Recording | pound | Save the recording |
| Voice Recording | star | Discard the recording |
| Pairing confirmation | one | Accept the browser that claimed the Pairing Code |
| Pairing confirmation | star | Refuse it |
| Pairing Tones | any | Ignored, so an echo of the tones read back as presses cannot act |
| System Menu | see below | Menu choices |

### System Menu

**Context:** Voice Interaction, with Agent Hosting actions

A deterministic in-call menu opened with star: no model interprets it, the Voice Frontend does not hear the caller while it is open, and pre-rendered prompts speak it. It holds only what must work when the Voice Agent cannot hear the User or cannot think straight: one restarts the Sandbox after a second press of one confirms that running work stops; two hands the call to the deterministic recovery menu; star, or fifteen seconds without a key after the prompt, returns to the conversation. Preferences and routine operations go by voice instead: status (also keypad one outside the menu), hanging up, the Theme, the Earcon Style, pairing a browser, and a restart the User confirms aloud (the Session Orchestrator's restart tool, which shares the menu's restart). The recovery menu covers GPT-Live being unavailable and is the only route to replacing a Sandbox; a dead Sandbox alone is a voice matter, since the Session Orchestrator runs without it. The Session Orchestrator learns of a keypad restart through a harness event. Menu actions are recorded as Call Activity.

**Not:** The recovery menu outside the Voice Agent, which is reached when realtime voice is unavailable.

### Earcon Style

**Context:** Voice Interaction

The User's choice of how earcons sound, kept with their Sandbox's Voice Session state: tonal, spoken, or none. Tonal, the semantic chime set designed in `earcon_lab.py`, is what a User gets without choosing; spoken plays a short spoken label for each event; none plays no event sounds, though the recording beep still plays. Tonal and spoken mark the same events; the System Menu's own prompts are always spoken. The User changes it by asking; the Session Orchestrator stores the preference and previews it with the opening sound (no preview for none). The public soundboard at `/sounds` plays every earcon in both sounding styles, as a cheat sheet.

### Agent Implementation

**Context:** Agent Hosting

A concrete program or service that implements the ACP Agent role, such as OpenCode. Its command line, authentication, configuration, and compatibility quirks belong to an adapter.

### Agent Connection

**Context:** ACP Integration

An initialized transport channel between Dogwalk and an ACP Agent, with a negotiated protocol version and capabilities. One connection may support multiple concurrent ACP sessions. A host may instead choose one connection per session.

When the Agent process ends, Agent Hosting reports an Agent Termination reason (`out_of_memory`, `killed` by a signal, or `exited` with a status, plus the memory limit when known) to the ACP Client: as JSON-RPC error data on each unanswered request and in the connection's close reason. Session Management carries it into the failed Turn Result (including a turn the Agent failed with a bare error moments before), so the User hears why the work stopped, not only that the connection dropped.

**Not:** A voice call or Managed Session.

### ACP Session

**Context:** ACP Integration

An independent conversation or thread between Client and Agent. It has an opaque Session ID, its own context and history, a working directory, and optional additional directories and MCP servers.

The Session ID is protocol identity. It is never a spoken name.

### Managed Session

**Context:** Session Management

Dogwalk's application record for one ACP Session. It projects protocol updates into state useful to interaction surfaces and associates the session with an Agent connection, Workspace, local Alias, Agent-supplied metadata, Prompt Turns, and pending Attention Requests.

**Maps to:** Exactly one ACP Session while attached.

A Managed Session is rooted in one Workspace directory chosen when it is created, and keeps it for life: loading or resuming its ACP Session must reuse that directory, because the Agent binds the session (and the project instructions, skills, and configuration it applies) to it. When the recorded directory is missing or the Agent reports a different one, Session Management repairs the record from the Agent's session listing rather than leaving the session unavailable. The Session Orchestrator roots a Managed Session in the project directory the work concerns, never in home with instructions to work elsewhere; a project that must first be cloned is cloned by one Managed Session and worked on by a new one rooted in the clone.

**Not:** An Agent process, Prompt Turn, or long-running task.

### Alias

**Context:** Session Management and Voice Interaction

A mutable, pronounceable, locally assigned handle by which the Voice Agent and User refer to a Managed Session. The Voice Agent chooses one freely when it creates or loads a session, and changes an existing one only when the User asks or agrees. It names only Aliases that exist: prompt examples use a placeholder, never a plausible name.

An Alias is distinct from both the opaque ACP Session ID and an Agent-supplied Session Title.

Aliases remain attached to live Managed Sessions across voice calls while the
Session Manager remains active. An Agent-discovered persisted ACP Session has no
Alias until Dogwalk loads it into a new Managed Session; loading assigns a fresh
Alias rather than treating its Session Title as a spoken identity.

### Session Title

**Context:** ACP Integration and Session Management

Human-readable session metadata supplied by the Agent, often generated from the conversation. A title describes the work, while an Alias supports compact spoken reference. Changing one does not implicitly change the other.

### Workspace

**Context:** Session Management

The filesystem scope made available to an ACP Session. It consists of the primary working directory (`cwd`) and any negotiated additional directories.

**Not:** A sandbox, repository, project identity, or process working directory, although those may coincide in a deployment.

### Sandbox

**Context:** Agent Hosting

An optional execution and security boundary around Agents, processes, files, network access, and credentials. Daytona is one possible sandbox provider.

**Not:** An ACP concept or synonym for Workspace.

### Sandbox Assignment

**Context:** Agent Hosting

Dogwalk's association between an authorized User identity and one provider-hosted
Sandbox. It records provider identity and lifecycle projection separately from
Registration: registering a phone does not create or identify a Sandbox. Agent
Hosting may derive a non-reversible provider label from the phone identity, but
must not expose the phone number to the provider as a sandbox name or label.

The provider ID is a cached reference, not durable identity. If the provider
reports that a Sandbox was deleted, Dogwalk invalidates the reference and may
create a replacement Sandbox for the same User. Registration survives; files,
Agent-held session history, and other state in the deleted Sandbox do not.
Replacement is exceptional reconciliation after explicit provider-side deletion,
not normal lifecycle. Stopped and archived Sandboxes retain identity and durable
filesystem state and are resumed in place, potentially across months.

### Diagnostics Surface

**Context:** Access Control, with projections from every other context

The read-only, passive web projection of Dogwalk's runtime state, used for
harness development, telemetry, and debugging. It is never a required interaction
surface and never mutates domain state; all authorization and action happen over
voice. The Diagnostics Surface renders one runtime tree whose nesting follows
object containment (Agent Connection, Managed Session by Alias, Prompt Turn,
Attention Request), with Voice Calls, Sandbox Assignments, Access
Control records, and the audit tail as sibling roots. Bounded context is a label
on each node, not the organizing structure.

The operator may also inspect a live sequence diagram: a chronological projection
of observed Voice Frontend, Session Orchestrator, session-management, and ACP
messages and spans, including millisecond server timings where recorded. It is
independent of the Paired Browser and requires operator authentication on every
request, so observing a Voice Call does not require pairing with its User. A lane
or arrow represents observed identity or a correlated message, never an invented
Agent or nested session. Missing instrumentation remains explicitly unknown.
Provider media offsets, server observation time, and audible telephone delivery
are distinct clocks and must not be presented as interchangeable measurements.

External observations preserve both the originating provider's occurrence time
(when known) and Dogwalk's receipt time, with clock identity, timestamp precision,
and source provenance. Late observations may be placed before earlier-received
events on the diagnostic timeline, including before Dogwalk first received the
Voice Call. Their call-leg parent relationships and sequence identifiers are
evidence, not permission to invent unobserved message exchanges. A reporting
interval across clocks includes clock uncertainty and is not automatically a
network-latency measurement. Historical enrichment is diagnostic only: it cannot
revive a completed Voice Call, replay actions, or change Managed Sessions.

Only the operator capability resolves the tree. What a viewer may see is decided
by capability, not by client-side hiding.

**Not:** A control surface, a required User interface, or the Voice Interaction
itself. "Mission Control" is superseded historical language for this surface.

The operator may reveal human-readable content independently of low-level
metadata: caller/Voice Agent transcripts, Handoffs, speech/context requests,
engineering briefs, and Agent messages at observed boundaries. This is a
presentation preference within the operator capability, not a separate permission
or a Paired Browser entitlement. Historical content retains its actual provenance:
provider media offsets, exact identifier-correlated observations, or unknown
timing, never a fabricated wall-clock position. This diagnostic presentation can
inform future User-facing designs without exposing other Users' calls to them.

### Paired Browser

**Context:** Access Control, with projections from Session Management

An optional, read-only second surface for the User: a browser tab that shows the live Managed Session projection (Aliases, Prompt Turn state, plans, recent tool calls, Reports, pending Permission Requests) and reads Workspace files. It never starts, steers, or authorizes anything; every action stays in the Voice Call. It complements the eyes-free norm for the moments a User wants to read rather than listen, such as reading a Report, thinking, and calling back with feedback.

Pairing is acoustic and confirmed by keypad. On request the Session Orchestrator plays a **Pairing Code** into the call as **Pairing Tones**: eighty random bits plus Reed-Solomon parity, sent as keypad (DTMF) tones. A browser within earshot, on a different device (phones give browsers silence during a call), decodes and claims the code; Dogwalk then has the Voice Frontend name the browser and its rough location, and the User presses one to accept or star to refuse. Playing tones authorizes nothing, so neither a model nor text an Agent produced can pair a browser; only the User's key press can. A Pairing Code works once and lapses after ninety seconds.

A pairing attempt never ends in silence. When the tones have played and no browser claims the code within ten seconds, the Session Orchestrator receives a `pairing_unclaimed` harness event and answers the User at once: offering slower tones when pages reported **Decode Failures**, otherwise suggesting speakerphone, volume, and distance, then offering to play them again. A Decode Failure is a pairing page's anonymous report that it heard Pairing Tones but could not decode them (coarse counts: the stage that failed, symbols heard, erasures, the closest symbol rate, never code bits). Before a claim nothing ties a page to a call, so the event's count covers every report since the attempt began: advisory, and adequate while Dogwalk has one operator. Reports are kept only while some Pairing Code is outstanding and trimmed to the newest few hundred.

At most one browser is paired at a time: accepting a new one unpairs the previous one and ends what it launched. The Session Orchestrator knows when the paired page is open and can show the User something on it: a Workspace file or folder, a Managed Session, the call timeline, its Memory, or a web page, or offer a Human Takeover. Files, folders, and views open on the page at once; anything that needs a new tab (a takeover, a web page) waits for the User's click. HTML files render isolated: scripts run at an opaque origin with no network access, so reports meant for the page are self-contained. The one exception is made for them: an image whose source is a Workspace path (absolute, or relative to the report) is read when the report is shown and embedded in it, a snapshot as of then.

An accepted browser holds a credential that names its Sandbox Assignment's Voice Session and a secret checked only there, kept in that browser tab's session storage. It outlasts the Voice Call and lapses after an hour without use, twelve hours in all, when the User unpairs it from the page, or when the Sandbox is replaced. Its connection never keeps Agent work or the Sandbox awake.

The page carries a build identity (a hash of its source) and sends it when it connects, so a tab loaded before a deploy is known to be **stale**: it lacks newer features, such as Whiteboard components. A reload keeps the pairing, so a stale page reloads itself once nothing would be lost (no field in use, no half-answered board, no Whiteboard tab, which only a click reopens). A page too old to know its build cannot, so the Session Orchestrator hears `browser_page_stale` (and `paired_browser.page_stale` in inspection) and asks the User to reload; a Whiteboard error from a stale page says the page is out of date.

The page shows the files' content as it is (rendered Markdown, images, PDF, audio, video, tables, code) and a live feed of the Voice Call: the caller's and the Voice Frontend's words, the Session Orchestrator's briefs and the prompts it sends to ACP Agents, Agent remarks, tool calls and Reports, and harness events, so the User can see how the page relates to the call.

**Not:** The Diagnostics Surface (operator-only, all Users and calls), a control surface, a required interface, or a terminal. Hands-on work launched from it is a Human Takeover; answers it collects are Whiteboard Answers. It succeeds the retired Scoped Diagnostic View, with the call itself replacing SMS as the channel that proves the User is present.

### Whiteboard

**Context:** Voice Interaction, shown on a Paired Browser

A board the Session Orchestrator draws on the Paired Browser in a small declarative UI language (openui-lang): a quick diagram, table, chart, comparison, or form, or a set of small decisions such as proposed edits shown as before and after, each to accept or reject. It is the escape hatch for moments that outgrow voice, so the User can keep moving until they reach a full computer.

A Whiteboard can be answered on the page (choices, text, buttons). A **Whiteboard Answer** is caller input, like keypad digits or the answer to an Elicitation: the Session Orchestrator takes it into account, and an earcon confirms in the call that it arrived. It is never authorization: it cannot resolve a Permission Request, and the Session Orchestrator confirms consequential answers aloud before acting on them. The User may always answer by voice instead, and that counts the same. The Voice Agent says aloud what it drew, so the call remains complete without the page.

A board may show an image an ACP Agent left in the Workspace (PNG, JPEG, GIF, WebP, or SVG), named by its Workspace path: the program carries the path, never the bytes or a URL. The image is a snapshot: when the Session Orchestrator draws the board (in a call, when the Sandbox is normally awake), Dogwalk reads the file once and keeps its bytes as a **Board Asset**, stored by content hash with the board and shared by every retained board that shows the same bytes. Viewing a board reads only its Board Assets, through the pairing, so it never wakes the Sandbox, and what is drawn stays on the board while the Sandbox sleeps or after the file changes or goes. To show a file as it is now, draw the board again. An image that could not be captured (missing, too large for a board, not the image its name says, outside the Workspace, or the Sandbox unreachable) shows its description and the reason, the rest of the board still works, and the Session Orchestrator learns which images failed from the draw itself. A Board Asset lives exactly as long as some retained board refers to it, is served only to that Sandbox Assignment's Paired Browser, and goes with the boards when the Sandbox is replaced. Images are a presentation of ordinary files, outside ACP; no Agent learns that a board showed one.

**Not:** A control surface for Agents, a Permission Request, or a required interface.

### Human Takeover

**Context:** Agent Hosting, launched through Access Control

The User working in the Sandbox directly, for what ACP Agents cannot do themselves: solving a CAPTCHA, completing a login or consent page, driving a GUI, or running a terminal program such as an ACP Agent's own interface. It is launched from a Paired Browser into a new full-screen browser tab and ends with that Paired Browser (unpairing, expiry, or Sandbox replacement end it).

A **Takeover Terminal** relays a terminal multiplexer session in the Sandbox, so closing the tab detaches rather than kills it, and reopening redraws it. The tab receives a single-use launch ticket in its URL fragment and exchanges it for its own reconnect token; Sandbox provider credentials never reach the browser. A **Takeover Desktop** is the Sandbox's virtual desktop (with a browser that the User and, later, ACP Agents can share), viewed through a provider-signed link whose lifetime caps the session and which is expired on revocation.

Unlike a Paired Browser, a Human Takeover in use keeps the Sandbox awake, bounded by the launch's lifetime. It is a deliberate exception to the voice-only control rule: authorization still happens over voice (pairing requires the User's key press), but the actions taken during a takeover are the User's own, outside Session Management, so Managed Sessions do not see them except through their effects on the Workspace.

**Not:** A Managed Session, an ACP Session, or a Prompt Turn. The Session Orchestrator does not operate it.

### Handoff

**Context:** Voice Interaction

The Voice Frontend's request for Session Orchestrator help, carrying the recent call transcript rather than a structured task. A later Handoff may continue, correct, cancel, or be independent of an earlier one; the Orchestrator decides which, preserving unrelated work. Speech or context sent in answer to a Handoff references it. Handoffs have no completion event: the Orchestrator's final speech request is the answer.

**Not:** A Prompt Turn or an Assignment. One Handoff may start several Prompt Turns or none.

### Client-Provided Tool

**Context:** ACP Integration

A tool Dogwalk offers an ACP Agent through ACP's Client-supplied MCP servers, stacked on the Agent Implementation's own tools. Client-provided tools bind to the attachment that supplied them (session creation, load, or resume over one Agent Connection), not to the ACP Session: another Client attaching the same session sees its own tools instead. They are optional affordances an Agent may use when present, never a dependency of its plan.

### Audio Clip

**Context:** Voice Interaction

Waveform audio played into a Voice Call: a short synthesized earcon for an event (call start, a Prompt Turn stopping during a lull) or a Workspace file an ACP Agent produced, played on request. It is the eyes-free analogue of showing an image. Earcons sound in the caller's Earcon Style: tonal chimes, or short spoken labels (synthesized speech naming the event); they queue for lulls rather than interrupting speech. The caller cannot interrupt a clip and the Voice Frontend does not hear it, so clips are short or announced, and the Session Orchestrator tells the Voice Frontend what was played. Telephone audio is narrowband.

### Voice Recording

**Context:** Voice Interaction

The caller's own audio captured during a Voice Call into a Workspace file, such as a voice note, a hummed melody, or a sound for an ACP Agent to work with. Recording follows voicemail conventions: pound saves, star discards, and a length limit or a long silence also ends it. The Voice Frontend hears silence while recording, so it does not answer the caller mid-note. A saved recording is an ordinary Workspace file; what happens to it next is an Assignment like any other.

### Voice Agent Skill

**Context:** Voice Interaction

Version-controlled guidance the Session Orchestrator loads on demand when a request matches the skill's description (its trigger), such as precise transcription or self-knowledge about Dogwalk. A skill has a body for the Orchestrator and may carry speech guidance applied to the Voice Frontend for the rest of the call. Voice Agent Skills change only through Dogwalk's repository; nothing at runtime writes them. Self-knowledge skills embed the project's own documents rather than paraphrasing them, and a themed skill (Runtime Dramaturgy) renders the active Theme's authoring notes when it loads.

**Not:** A skill held by an ACP Agent in the Sandbox, which the Agent may create and maintain itself, or a Client-Provided Tool.

### Prompt Turn

**Context:** ACP Integration and Session Management

One complete interaction cycle beginning with `session/prompt` and ending when the Agent returns a stop reason. A Prompt Turn may include many model exchanges, plans, tool calls, updates, Permission Requests, and Elicitations.

A completed or cancelled Prompt Turn does not imply that its ACP Session has closed. Another Prompt Turn may continue the same session context.

**Not:** An ACP Session, Agent, or necessarily the whole engineering task.

### Assignment

**Context:** Voice Interaction

The Voice Agent's plain-language presentation of what the User wants an ACP Agent to do in a Managed Session. An Assignment commonly produces one Prompt Turn but may require follow-up turns.

Assignment is interaction language, not a protocol lifecycle entity.

### Initial Brief

**Context:** Session Management

The first prompt content Dogwalk constructs from the User's Assignment plus role, safety, workspace, and reporting context. It uses neutral ACP and session vocabulary and does not brief the ACP Agent into the Theme's narration. Subsequent prompts continue the same Managed Session without recreating the brief unless required by an Agent adapter.

### Activity

**Context:** Session Management and Voice Interaction

A concise, User-safe projection of current Agent updates: tool calls (kind, status, locations, timing), the Agent Plan, and the Agent's interim remarks (message text between tool calls). Activity is observable progress, not private reasoning and not a second Agent turn; thought chunks are never projected. Dogwalk keeps Activity structured and turns it into speech, earcons, or inspection results at the point of use.

### Followed Session

**Context:** Voice Interaction

The one Managed Session, if any, whose Activity the Voice Agent keeps up with during a Voice Call. Beginning a Prompt Turn follows that session; following another replaces it; Stop Following leaves it working unobserved. Following lasts for one Voice Call and survives the followed session's Prompt Turn ending.

Following observes; it never directs the Agent.

**Not:** A session state, a lock, or Attention.

### Progress Beat

**Context:** Voice Interaction

One sentence about a Followed Session's Activity, built deterministically without a model: a change of phase (looking around, changing files, running something), the first change of a turn, a failed step (failures within two seconds of each other, such as parallel tool calls failing together, make one beat carrying the latest streak), a long-running command, a new plan step, an interim remark, or a nearly full context window. Beats within a second and a half of each other make one beat (the weightiest: a full context window or a failure streak long enough to bid, then the Agent's own words, then a step, a first change, or a shorter failure streak, then a phase change; else the latest), and the beat waiting to be spoken is the weightiest since the last narration, so the Agent's own words are not displaced by a phase change that follows them. A failure beat says what the latest failed step was doing when its kind is known. The Agent's own words between tool calls are the richest source of beats; the Sandbox's global OpenCode rules ask it to write one before each tool call or batch, because Dogwalk relays its text and never its tool arguments. A tool of no known kind makes no beat, and neither a tool's own name nor a command's text appears in one: a command is named by its phase (running the tests, installing packages, building, working with Git). A failed step is not a failed turn: its beat says the turn is still going. When a Prompt Turn ends or fails, its unfinished tool calls end with it, and no beat about that turn (a failed step, a long-running command) comes after; the turn event speaks instead. An interim remark keeps the Agent's words and tense, quoted, so a plan stays a plan. Beats reach the Voice Frontend as unspoken context once a burst settles, and the latest is spoken in a quiet moment, spaced well apart (twenty seconds or more), never twice running without the User speaking or a Prompt Turn starting or ending in between (a failure may break that), and dropped when stale. A beat's text is neutral; the Voice Frontend restates it in the Theme's words at the current register, never reciting it or chaining several.

### Bid

**Context:** Voice Interaction

A Managed Session that is not followed asking for the Voice Agent's attention: a Permission Request, a Prompt Turn ending, or repeated failed tool calls. The Voice Agent mentions a Bid and offers to follow the session; it does not follow it without the User's agreement.

**Not:** An Attention Request. A Bid is a presentation of events; some are Attention Requests and some (a turn ending, a failure streak) are not.

### Report

**Context:** Session Management and Voice Interaction

A concise result projected from Agent messages and the Prompt Turn outcome for the Voice Agent to relay. A Report may state that a turn stopped without establishing success. It must not erase the stop reason or failure state from the underlying Turn Result.

### Turn Result

**Context:** Session Management

The durable outcome of one Prompt Turn, including its stop reason, final Agent messages, relevant tool outcomes, usage, and any local failure information. `end_turn` means the Agent ended normally; it does not by itself prove that the requested engineering result succeeded.

### Attention Request

**Context:** Session Management

A Client-side union used to route something requiring the User's attention. Every Attention Request retains its precise subtype and response semantics.

Current subtypes are Permission Request and Elicitation. They may share delivery infrastructure but are not interchangeable protocol concepts.

### Permission Request

**Context:** ACP Integration

An Agent request for the User to authorize or reject an operation, usually a tool call. Dogwalk presents exactly the options supplied by the Agent and returns the selected option.

Rejecting an offered operation is not the same as cancelling the Prompt Turn. ACP's cancelled permission outcome is reserved for cancellation of the containing turn.

Dogwalk does not define a read-only ACP Session mode. Workspace isolation and
write capability belong to Agent Hosting or Sandbox policy; Permission Requests
remain operation-specific ACP authorization rather than a session-wide mode.

### Elicitation

**Context:** ACP Integration

A transient Agent request for structured User information or an out-of-band interaction. Dogwalk preserves the difference among accepting, declining, and cancelling an Elicitation.

Elicitation is an evolving ACP capability and must be capability-gated at the adapter boundary.

**Not:** A Permission Request or persistent Session Config Option.

### Session Config Option

**Context:** ACP Integration

Agent-advertised, persistent session configuration such as model, mode, reasoning level, or autonomy. Dogwalk treats the Agent's advertised options as authoritative and degrades gracefully when an option type or category is unsupported.

### Agent Plan

**Context:** ACP Integration and Session Management

An Agent-reported execution strategy whose entries and status can change during a Prompt Turn. Plans are useful input for spoken progress summaries but are not commands issued by the Voice Agent.

## Lifecycles

### Connection Lifecycle

Dogwalk connects to an Agent, initializes ACP, negotiates protocol version and capabilities, uses the connection for zero or more sessions, and eventually disconnects. Connection failure can make associated sessions unavailable without changing their conceptual identity. On reconnecting, Dogwalk restores its Managed Sessions with `session/resume` when the Agent advertises it, which restores context without replaying history, and falls back to `session/load` otherwise.

### Managed Session Lifecycle

```text
creating -> ready -> closing -> closed
               \-> unavailable
```

- **Creating:** Dogwalk is establishing or restoring the ACP Session.
- **Ready:** The ACP Session is established and retained. Prompt Turn activity is modeled separately.
- **Closing:** Dogwalk is asking the Agent to release active session resources.
- **Closed:** The active session is no longer available through this attachment.
- **Unavailable:** The session cannot currently be used because its Agent connection or host failed.

These are Dogwalk projections, not ACP-defined session statuses.

### Prompt Turn Lifecycle

```text
queued -> in_progress -> stopped
                    \-> failed
```

- **Queued:** Dogwalk has accepted a prompt but has not sent it to the Agent.
- **In Progress:** `session/prompt` is outstanding.
- **Stopped:** The Agent returned a stop reason such as `end_turn`, `max_tokens`, `refusal`, or `cancelled`.
- **Failed:** The turn ended without a valid Prompt Response because of a local, transport, or Agent error.

A Permission Request or Elicitation can make an in-progress turn need User attention. That attention condition does not create a new Prompt Turn.

The Voice Agent may describe a ready session as "resting" or an in-progress session as "working." Those are conversational projections, not core lifecycle values.

### Attention Lifecycle

An Attention Request is received from the Agent, queued for the appropriate interaction surface, presented to the User, resolved with subtype-correct semantics, and returned to the Agent. Cancelling a Prompt Turn also resolves or cancels its pending protocol requests as ACP requires.

## Operations

| Neutral operation | Meaning |
|---|---|
| Create Managed Session | Establish a new ACP Session, rooted in one existing Workspace directory, and its local projection |
| List Workspace Projects | Read which directories in the Workspace are projects (repositories or directories with Agent instructions), to choose where to root a Managed Session |
| Begin Prompt Turn | Send prompt content to a Managed Session |
| Inspect Managed Session | Read projected state without starting another turn |
| List Managed Sessions | Discover sessions currently known to Dogwalk |
| Discover Persisted Sessions | List Agent-held session history without attaching it |
| Load Persisted Session | Attach an Agent-held ACP Session as a new Managed Session with a fresh Alias |
| Set Alias | Change the local pronounceable handle |
| Cancel Prompt Turn | Stop current Agent work while retaining the session |
| Close Managed Session | Release the active ACP Session |
| Delete Persisted Session | Remove Agent-held session history when supported |
| Resolve Permission | Select one Agent-supplied permission option |
| Resolve Elicitation | Accept, decline, or cancel requested input |
| Set Session Config Option | Change an Agent-advertised session setting |
| Follow Managed Session | Make one session the Followed Session for this Voice Call, replacing any other |
| Stop Following | Leave the Followed Session working unobserved |

These canonical terms name Voice Agent tools as well as core methods. Narration may add metaphor after a tool result, but tool names and arguments remain neutral.

## Explicit Non-Synonyms

- An Agent Implementation is not necessarily one Agent process.
- An Agent Connection is not an ACP Session.
- An ACP Session is not a Prompt Turn.
- A Prompt Turn is not necessarily the whole Assignment.
- A completed Prompt Turn does not mean its Managed Session is closed.
- `end_turn` does not prove engineering success.
- Cancelling a Prompt Turn is not closing a Managed Session.
- Closing an active session is not deleting persisted history.
- Permission rejection is not Prompt Turn cancellation.
- A Permission Request is not an Elicitation.
- An Elicitation is not a Session Config Option.
- An Alias is not a Session Title or Session ID.
- A Workspace is not a Sandbox.
- A voice call is not an Agent Connection.
- A Handoff is not a Prompt Turn.
- The System Menu is not the recovery menu, and neither is the Session Orchestrator.
- A Voice Call connecting is not the Agent Connection being ready.
- The Session Orchestrator is not the Session Manager; its memory is not a store of record.
- Client-provided tools bind to an attachment, not to an ACP Session.
- Following a session is not directing it, and Stop Following is not closing or cancelling anything.
- A Bid is not an Attention Request.

## Adapter Vocabulary

The following names may appear in adapter configuration and deployment documentation but must not define core domain types:

- **OpenCode:** Current ACP Agent Implementation.
- **OpenAI GPT-Live:** Current Voice Frontend model and Voice Transport. Twilio
  hands the active media leg to it over SIP; Dogwalk attaches a sideband and uses
  client delegation, so a GPT-Live delegation is the adapter form of a Handoff
  and its thinking and commentary appends carry Orchestrator context and speech.
- **Responses API:** Current Session Orchestrator model protocol. Any compatible
  endpoint, including open-weight servers, may host the Orchestrator model.
- **Daytona Secret:** Current mechanism for giving sandbox Agents inference
  credentials through an egress proxy without placing the value in the Sandbox.
- **Daytona:** Current remote Sandbox provider.
- **stdio, WebSocket, HTTP:** Transport mechanisms.
- **ACP Python SDK:** Current protocol library.

Adapter-specific limitations should be represented as negotiated capabilities, adapter behavior, or compatibility notes rather than generalized into Dogwalk's domain model.

## Architectural Consequences

- Voice Agent tools expose neutral Session Management operations directly.
- ACP SDK and wire-version types stop at the ACP Integration boundary.
- Agent launch commands and process topology belong to Agent Hosting adapters.
- Managed Session state and Prompt Turn state are represented separately.
- Permission Requests and Elicitations may share an Attention queue but retain distinct types and response rules.
- Dogwalk stores Alias separately from ACP Session ID and Agent-supplied Session Title.
- Agent-discovered sessions remain unattached history until explicitly loaded; loading creates a Managed Session and assigns a fresh Alias.
- Speech-safe Activity and Reports are projections of richer protocol state, not replacements for it.

These consequences constrain responsibilities and naming. They do not prescribe a module tree, class hierarchy, persistence technology, or number of source files.

## Historical Language

The following language appears in Git history and is not canonical for production interfaces:

- **Walker:** An early proper name for the Voice Agent. Production code uses Voice Agent; "dog walker" may survive as opening copy or a joke.
- **Dog:** An early persona for a Managed Session, ACP Agent process, or task. Production code uses the precise concept instead.
- **Pack:** An early spoken label for a set of sessions. Production tools list Managed Sessions.
- **Sic, relay, revive, and call off:** Early Voice Agent tool verbs. Production tools use create, prompt, load, cancel, and close.
- **Working/resting Dog:** Optional narration over Prompt Turn activity and session readiness, not lifecycle state.
- **Mission Control:** Superseded name for the Diagnostics Surface. Production code and docs use Diagnostics.
- **Review Bundle:** Agent-published files for later visual review, delivered by texted link. Retired 2026-11-07 with SMS for a voice-only focus.
- **Ephemeral Service:** A registered Sandbox port reachable through a texted signed preview link. Retired 2026-11-07 with SMS for a voice-only focus.
- **Scoped Diagnostic View:** A texted, per-call read-only caller lens on the Diagnostics Surface. Retired 2026-11-07 with SMS for a voice-only focus.
- **Delegated backend:** The September 2026 arrangement where GPT-Live's managed Responses delegation ran session tools. Superseded by the Session Orchestrator under client delegation.

## Open Questions

- Should queued follow-up prompts be first-class Prompt Turns immediately, or become Prompt Turns only when sent to the Agent?
- Proactive speech about Activity is now limited to the Followed Session's Progress Beats, and attention from other sessions arrives as Bids. Still open: which beats deserve an earcon or a per-session sound instead of speech, whether a failure streak is a good enough sign that a session is stuck, and whether the beat thresholds (4 s of quiet, 20 s between narrations) hold up in playtests. Also open: whether the production model's reasoning text (ACP `agent_thought_chunk`, now counted per turn in `acp.turn.stopped` and `acp.turn.failed` as `thoughts`) is worth narrating.
- How should the Voice Agent resolve ambiguous spoken "stop" among stopping speech, cancelling one Prompt Turn, closing one Managed Session, stopping all Agent work, and ending the voice call?
- Which parts of Assignment history belong to Dogwalk versus the ACP Agent's retained session context?

- Should the Session Orchestrator ever act without a live User utterance, for example starting a follow-up Prompt Turn after a failure? That is an authorization question first, and the current answer is no.
- Resolved: the Session Orchestrator's persistent context is compacted voluntarily into its Memory (see Memory), readable on a Paired Browser. Still open: should parts of the Memory also become notes in the Workspace, the way a coworker's notebook would be, and should a Sandbox replacement reset it?
- When a newer Handoff arrives while work for an older one is in flight, which supersession rules belong in deterministic code rather than Orchestrator judgment?

- Notes: a caller may call in only to leave a note on the Sandbox or to hear notes other sessions left. Notes go through ACP (a dog writes or reads them in a Prompt Turn); where they live, such as a Workspace folder or a long-lived note-keeping session, is undecided.
- Invitations: should a successful Registration by Invitation continue straight into the new User's first call rather than hanging up? Should the Diagnostics Surface draw the invitation graph, and should an operator be able to raise or lower a User's active-Invitation limit?
- Could Access Control identify a User by something spoken, such as a passphrase in the style of Invite Codes, so a call from any telephone, even a payphone, works without caller ID?
- Is there a limit on how many Managed Sessions the Voice Agent attends at once, and who decides when to let one go?

- Should the Voice Frontend also receive recent conversation directly (as provider session history at call start), rather than only through the Session Orchestrator's reorienting speech?

- Pound now means stop talking in conversation, so a caller who presses pound to end a recording that the Voice Frontend only claimed to start no longer signals the Session Orchestrator. Should a pound press shortly after recording talk also reach it?
- Should a System Menu restart wait for or inform in-flight Prompt Turns beyond the harness event, and should a restart be refused while a Permission Request is pending?
- The Opening's welcome line is written when a call ends; how stale may it become (for example after days, or after work that changed what is unfinished) before Dogwalk prefers the neutral fallback?
- A Managed Session's directory is fixed at creation and Dogwalk does not yet negotiate additional directories, so work spanning two projects needs two Managed Sessions or a shared parent directory. Should Create Managed Session accept additional directories when the Agent advertises them?
- Is a project an Agent Hosting fact (a directory found in the Sandbox) or a Session Management concept with its own identity, for example so a Managed Session survives a clone being moved or re-cloned?
- Is the caller-ID clause permanent product behavior or a diagnostic to remove once STIR/SHAKEN attestation is understood across carriers?

- Paired Browser: should the Session Orchestrator learn which files the User opened, so a callback can start from what they read? Should unpairing also be possible by voice? Pairing Tones decode through simulated speech codecs and reverberant rooms but are not yet verified over a real carrier. Decode Failures cannot be attributed to a call before a claim; if Dogwalk ever has more than one active User, should a page learn something per attempt (without weakening the one-press authorization) so the count stops being global?
- Whiteboard and shown-report images are snapshots taken when drawn or shown (resolved: viewing never wakes the Sandbox; redraw or reshow to refresh). Should a board say when a captured file has since changed? Export or sharing of a board with its images is not supported.
- Human Takeover: should the Session Orchestrator learn that a takeover happened (and hand off to it, "the desktop is ready for you"), or summon one ("a dog is stuck on a CAPTCHA; pair a browser and open the desktop")? Should ACP Agents drive the same desktop through the provider's computer-use API, so human and Agent take turns on one screen? Is a one-hour desktop link the right cap, and should a terminal idle out?

## ACP References

- [Architecture](https://agentclientprotocol.com/get-started/architecture)
- [Initialization and capabilities](https://agentclientprotocol.com/protocol/v1/initialization)
- [Session setup](https://agentclientprotocol.com/protocol/v1/session-setup)
- [Prompt turns](https://agentclientprotocol.com/protocol/v1/prompt-turn)
- [Session list and metadata](https://agentclientprotocol.com/protocol/v1/session-list)
- [Tool calls and permissions](https://agentclientprotocol.com/protocol/v1/tool-calls)
- [Session config options](https://agentclientprotocol.com/protocol/v1/session-config-options)
- [Agent plans](https://agentclientprotocol.com/protocol/v1/agent-plan)
- [Elicitation RFD](https://agentclientprotocol.com/rfds/elicitation)
