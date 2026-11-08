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

The Voice Agent is one character performed by two components: a Voice Frontend that holds the conversation and a Session Orchestrator that exercises engineering judgment and operates Managed Sessions. The User experiences a single character. The Voice Frontend may refer to the Session Orchestrator through the presentation fiction of its own head or mind ("let me think"), which keeps an account of its limits honest without naming models; when the User asks about the architecture, it explains the split candidly.

Dogwalk presents as an agentic development environment driven by a coworker. ACP Agents should see ordinary requests from a colleague: they need not know they are supervised by voice, and anything specific to calls or speech stays inside Dogwalk.

## Modeling Principles

- ACP terminology governs protocol integration and anchors the session-management model.
- Dogwalk is a product codename, not the source of its domain vocabulary.
- Canine language is optional presentation flavor in UI copy and Voice Agent narration. It must not appear in tool names, operation names, protocol payloads, persisted schemas, or prompts sent to ACP Agents.
- The core model must not depend on OpenCode, a particular model provider, a particular voice transport, or Daytona.
- A protocol entity, an application projection, and its spoken presentation are different things even when they currently have a one-to-one relationship.
- Derived state must not be presented as ACP-defined state. Dogwalk may infer that a session is ready or that a turn needs attention, but those are Client projections.
- Opaque protocol identifiers stay out of speech. The Voice Agent refers to work by purpose, recency, and pronounceable Alias.
- The current process topology is an implementation choice. ACP permits one Agent connection to support multiple concurrent sessions.

## Bounded Contexts

### Voice Interaction

Owns the live conversation, Voice Agent personality, spoken reference resolution, optional narration flavor, earcons, interruption, muting, and speech-safe summaries. Canine language may appear here as an acknowledged metaphor, but is never canonical domain language.

### Session Management

Owns Managed Sessions, Prompt Turns, workspaces, aliases, projected state, concurrency, queues, and User attention. It exposes neutral operations that any interaction surface could use.

### ACP Integration

Owns ACP initialization, capability negotiation, sessions, prompts, updates, stop reasons, permissions, configuration, and protocol-version translation. It speaks ACP's terms exactly and shields Session Management from SDK and wire-version details.

### Agent Hosting

Owns how an ACP Agent is located, launched, connected, authenticated, isolated, and stopped. A local OpenCode subprocess and a remote sandbox-hosted Agent are alternate hosting arrangements.

### Voice Transport

Owns realtime audio transport, speech turn detection, browser or telephone media, and provider-specific function calling. OpenAI GPT-Live over SIP is the current adapter, not the definition of the Voice Agent.

### Access Control

Owns phone Registration and Invite Codes at the PSTN boundary. A registered
phone number identifies a User for access purposes; Dogwalk does not maintain a
separate personal name in this context. Telephony providers and sandbox hosts
are adapters, not identity authorities.

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

The single character the User talks to: socially fluent, a little modest about engineering detail, and authoritative about session state. It may use Dogwalk's canine metaphor sparingly in narration. It is composed of a Voice Frontend and a Session Orchestrator.

**Not:** An ACP Agent, coding harness, Agent host, or a single model.

### Voice Frontend

**Context:** Voice Interaction

The realtime speech component of the Voice Agent. It owns turn-taking, interruption, conversational repair, persona, and spoken phrasing. It has no session tools: when the User asks for or about work, it raises a Handoff and waits for the Session Orchestrator rather than guessing. It treats Orchestrator context as background and Orchestrator speech requests as things to say in its own words.

**Not:** An engineering authority or a source of session state.

### Session Orchestrator

**Context:** Session Management and Voice Interaction

The reasoning component of the Voice Agent. It reads Handoffs and harness events, resolves references, writes engineering briefs for ACP Agents, invokes the neutral operations, and decides what the Voice Frontend should know (unspoken context) or say now (speech requests). Its conversation context persists across Voice Calls; between calls it accumulates events but takes no action.

It is a client of Session Management, never a replacement for it: Managed Session and Prompt Turn projections remain authoritative, and the Orchestrator reports completion only from them.

**Not:** An ACP Agent, the Session Manager, or a store of record.

### ACP Client

**Context:** ACP Integration

The protocol role that initializes connections, creates or resumes sessions, sends prompts, receives updates, presents permission choices, and controls access to Client capabilities.

Dogwalk is the ACP Client. The Voice Agent may initiate Client operations through Dogwalk's neutral tool surface, but the Voice Agent itself is not the complete protocol implementation.

### ACP Agent

**Context:** ACP Integration

An ACP-compatible coding harness that owns conversation context, processes prompts, uses models and tools, reports updates, and returns a stop reason for each Prompt Turn.

OpenCode is the current Agent implementation. Other ACP-compatible implementations must be substitutable in principle.

**Not:** A model, subprocess, session, Prompt Turn, or task.

## Presentation Fiction (provisional)

The canine fiction exists only above ACP, to make the agentic development environment legible by voice. The Voice Frontend and Session Orchestrator may both use it; ACP prompts, tools, schemas, and anything in the Sandbox never do. Nothing here is committed yet: a `?` marks a mapping still under exploration, and none of these words are canonical domain terms.

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

The Voice Agent mirrors the caller's sophistication, especially on technical topics: modest and plain with a newcomer, fluent with someone who already speaks about agents, sessions, and delegation, so existing agentic literacy amplifies the experience.

## Core Concepts

### Registration

**Context:** Access Control

Authorization for one phone number to use Dogwalk. The normalized phone number
is the Registration's identity and is unique. Registration records which Invite
Code authorized it and when it occurred, but does not contain a personal name,
sandbox identity, or hosting policy.

### Invite Code

**Context:** Access Control

A speakable capability that authorizes Registration. An Invite Code may have an
expiration time and a maximum number of uses. No expiration means it remains
valid indefinitely; no maximum means unlimited registrations. A code is usable
only while unexpired and below its maximum, and each phone number may register
only once regardless of how many codes it knows.

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

The Session Orchestrator may read the current User's recent Call Activity to explain interruptions, and each call begins with a since-last-call summary (how the previous call ended, including provider errors such as moderation, recovery actions, failed Prompt Turns, and its last words) so the Voice Agent can reorient the User after a drop. A provider ending the voice connection is not a User hanging up, and neither cancels Prompt Turns; only a Sandbox restart or replacement interrupts Agent work. A Voice Call may contain several voice sessions (after a drop, the recovery path can start another). A voice session ends at the first of the provider's close event or the loss of the control sideband; the Voice Call ends only when the telephony provider reports it complete, and that report also closes out any voice session whose end was never observed.

### Agent Implementation

**Context:** Agent Hosting

A concrete program or service that implements the ACP Agent role, such as OpenCode. Its command line, authentication, configuration, and compatibility quirks belong to an adapter.

### Agent Connection

**Context:** ACP Integration

An initialized transport channel between Dogwalk and an ACP Agent, with a negotiated protocol version and capabilities. One connection may support multiple concurrent ACP sessions. A host may instead choose one connection per session.

**Not:** A voice call or Managed Session.

### ACP Session

**Context:** ACP Integration

An independent conversation or thread between Client and Agent. It has an opaque Session ID, its own context and history, a working directory, and optional additional directories and MCP servers.

The Session ID is protocol identity. It is never a spoken name.

### Managed Session

**Context:** Session Management

Dogwalk's application record for one ACP Session. It projects protocol updates into state useful to interaction surfaces and associates the session with an Agent connection, Workspace, local Alias, Agent-supplied metadata, Prompt Turns, and pending Attention Requests.

**Maps to:** Exactly one ACP Session while attached.

**Not:** An Agent process, Prompt Turn, or long-running task.

### Alias

**Context:** Session Management and Voice Interaction

A mutable, pronounceable, locally assigned handle by which the Voice Agent and User refer to a Managed Session.

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

Only the operator capability resolves the tree. What a viewer may see is decided
by capability, not by client-side hiding.

**Not:** A control surface, a required User interface, or the Voice Interaction
itself. "Mission Control" is superseded historical language for this surface.

### Handoff

**Context:** Voice Interaction

The Voice Frontend's request for Session Orchestrator help, carrying the recent call transcript rather than a structured task. A later Handoff may continue, correct, cancel, or be independent of an earlier one; the Orchestrator decides which, preserving unrelated work. Speech or context sent in answer to a Handoff references it. Handoffs have no completion event: the Orchestrator's final speech request is the answer.

**Not:** A Prompt Turn or an Assignment. One Handoff may start several Prompt Turns or none.

### Client-Provided Tool

**Context:** ACP Integration

A tool Dogwalk offers an ACP Agent through ACP's Client-supplied MCP servers, stacked on the Agent Implementation's own tools. Client-provided tools bind to the attachment that supplied them (session creation, load, or resume over one Agent Connection), not to the ACP Session: another Client attaching the same session sees its own tools instead. They are optional affordances an Agent may use when present, never a dependency of its plan.

### Audio Clip

**Context:** Voice Interaction

Waveform audio played into a Voice Call: a short synthesized earcon for an event (call start, a Prompt Turn stopping during a lull) or a Workspace file an ACP Agent produced, played on request. It is the eyes-free analogue of showing an image. Earcons come in two styles: tonal note patterns, and verbose spoken labels (synthesized speech naming the event, for diagnosis); they queue for lulls rather than interrupting speech. The caller cannot interrupt a clip and the Voice Frontend does not hear it, so clips are short or announced, and the Session Orchestrator tells the Voice Frontend what was played. Telephone audio is narrowband.

### Voice Recording

**Context:** Voice Interaction

The caller's own audio captured during a Voice Call into a Workspace file, such as a voice note, a hummed melody, or a sound for an ACP Agent to work with. Recording follows voicemail conventions: pound saves, star discards, and a length limit or a long silence also ends it. The Voice Frontend hears silence while recording, so it does not answer the caller mid-note. A saved recording is an ordinary Workspace file; what happens to it next is an Assignment like any other.

### Voice Agent Skill

**Context:** Voice Interaction

Version-controlled guidance the Session Orchestrator loads on demand when a request matches the skill's description (its trigger), such as precise transcription or self-knowledge about Dogwalk. A skill has a body for the Orchestrator and may carry speech guidance applied to the Voice Frontend for the rest of the call. Voice Agent Skills change only through Dogwalk's repository; nothing at runtime writes them. Self-knowledge skills embed the project's own documents rather than paraphrasing them.

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

The first prompt content Dogwalk constructs from the User's Assignment plus role, safety, workspace, and reporting context. It uses neutral ACP and session vocabulary and does not brief the ACP Agent into Dogwalk's optional canine narration. Subsequent prompts continue the same Managed Session without recreating the brief unless required by an Agent adapter.

### Activity

**Context:** Session Management and Voice Interaction

A concise, User-safe projection of current Agent updates, usually plans and tool-call status. Activity is observable progress, not private reasoning and not a second Agent turn.

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

Dogwalk connects to an Agent, initializes ACP, negotiates protocol version and capabilities, uses the connection for zero or more sessions, and eventually disconnects. Connection failure can make associated sessions unavailable without changing their conceptual identity.

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
| Create Managed Session | Establish a new ACP Session and local projection |
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
- The Session Orchestrator is not the Session Manager; its memory is not a store of record.
- Client-provided tools bind to an attachment, not to an ACP Session.

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
- Which Agent plan and tool-call changes deserve proactive speech, an earcon, or silent state projection?
- How should the Voice Agent resolve ambiguous spoken "stop" among stopping speech, cancelling one Prompt Turn, closing one Managed Session, stopping all Agent work, and ending the voice call?
- Which parts of Assignment history belong to Dogwalk versus the ACP Agent's retained session context?

- Should the Session Orchestrator ever act without a live User utterance, for example starting a follow-up Prompt Turn after a failure? That is an authorization question first, and the current answer is no.
- How should the Session Orchestrator's persistent context be compacted, and which parts of it, if any, should become User-visible notes in the Workspace, the way a coworker's notebook would be?
- When a newer Handoff arrives while work for an older one is in flight, which supersession rules belong in deterministic code rather than Orchestrator judgment?

- Notes: a caller may call in only to leave a note on the Sandbox or to hear notes other sessions left. Notes go through ACP (a dog writes or reads them in a Prompt Turn); where they live, such as a Workspace folder or a long-lived note-keeping session, is undecided.
- Could Access Control identify a User by something spoken, such as a passphrase in the style of Invite Codes, so a call from any telephone, even a payphone, works without caller ID?
- Is there a limit on how many Managed Sessions the Voice Agent attends at once, and who decides when to let one go?

- Should the Voice Frontend also receive recent conversation directly (as provider session history at call start), rather than only through the Session Orchestrator's reorienting speech?

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
