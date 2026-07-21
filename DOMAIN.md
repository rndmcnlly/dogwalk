# Dogwalk Domain Language

## Status and Authority

This document is Dogwalk's canonical Ubiquitous Language. It is normative for new design and refactoring, although the current spikes may not yet conform to it. A mismatch in spike code is implementation debt, not an alternate domain definition.

The project uses this order of authority:

1. `AGENTS.md` supplies the operating contract and irreducible domain guardrails to every development session.
2. `DOMAIN.md` defines canonical terms, boundaries, relationships, and lifecycle distinctions.
3. Tests and scenarios specify behavior executably.
4. Spike notes describe experiments and may retain historical terminology.

Update this document in the same change that introduces or changes a domain concept. Put unresolved modeling choices under Open Questions rather than allowing code to choose a meaning implicitly.

## Product Definition

Dogwalk is an eyes-free ACP Client and multi-session coding manager. It gives a User the capability surface of a visual multi-session coding client through hands-free voice, without requiring access to a screen, mouse, or keyboard.

Walker is the realtime voice agent through which the User supervises coding work. Walker presents managed coding sessions as named Dogs and translates between conversational intent and precise session-manager operations. Coding work is performed by ACP Agents such as OpenCode, not by Walker.

## Modeling Principles

- ACP terminology governs protocol integration and anchors the session-management model.
- Canine terminology belongs to Walker's voice interaction layer and may be expressive there.
- The core model must not depend on OpenCode, a particular model provider, a particular voice transport, or Daytona.
- A protocol entity, an application projection, and its spoken presentation are different things even when they currently have a one-to-one relationship.
- Derived state must not be presented as ACP-defined state. Dogwalk may infer that a session is ready or that a turn needs attention, but those are Client projections.
- Opaque protocol identifiers stay out of speech. Walker refers to work by purpose, recency, and pronounceable alias.
- The current process topology is an implementation choice. ACP permits one Agent connection to support multiple concurrent sessions.

## Bounded Contexts

### Voice Interaction

Owns the live conversation, Walker's personality, spoken reference resolution, Dog aliases, Pack presentation, earcons, interruption, muting, and speech-safe summaries. This is the only context in which canine terminology is canonical.

### Session Management

Owns Managed Sessions, Prompt Turns, workspaces, aliases, projected state, concurrency, queues, and User attention. It exposes neutral operations that any interaction surface could use.

### ACP Integration

Owns ACP initialization, capability negotiation, sessions, prompts, updates, stop reasons, permissions, configuration, and protocol-version translation. It speaks ACP's terms exactly and shields Session Management from SDK and wire-version details.

### Agent Hosting

Owns how an ACP Agent is located, launched, connected, authenticated, isolated, and stopped. A local OpenCode subprocess and a remote sandbox-hosted Agent are alternate hosting arrangements.

### Voice Transport

Owns realtime audio transport, speech turn detection, browser or telephone media, and provider-specific function calling. OpenAI Realtime is the current adapter, not the definition of Walker.

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
Walker / Voice Interaction
    |  translates Dog language into neutral commands and events
    v
Session Management
    |  translates managed lifecycle into ACP operations
    v
ACP Integration
    |
    v
ACP Agent

Agent Hosting supplies connections and isolation to ACP Integration.
Voice Transport carries the conversation used by Voice Interaction.
```

## Roles

### User

**Context:** Voice Interaction

The human principal whose intent, authorization, and attention govern the work. The User may speak imprecisely and should not need to pronounce technical identifiers.

### Walker

**Context:** Voice Interaction

The socially fluent, deliberately engineering-weak voice agent that maintains the conversation, resolves references, invokes session-manager operations, and presents results in plain language. Walker does not replace the ACP Client as a whole: Walker is one interaction component within Dogwalk.

**Not:** An ACP Agent, coding harness, Agent host, or engineering authority.

### ACP Client

**Context:** ACP Integration

The protocol role that initializes connections, creates or resumes sessions, sends prompts, receives updates, presents permission choices, and controls access to Client capabilities.

Dogwalk is the ACP Client. Walker may initiate Client operations through Dogwalk's tool surface, but Walker itself is not the complete protocol implementation.

### ACP Agent

**Context:** ACP Integration

An ACP-compatible coding harness that owns conversation context, processes prompts, uses models and tools, reports updates, and returns a stop reason for each Prompt Turn.

OpenCode is the current Agent implementation. Other ACP-compatible implementations must be substitutable in principle.

**Not:** A Dog, model, subprocess, session, Prompt Turn, or task.

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
details or references. Mission Control presents a redacted projection by
default and requires an explicit, session-only opt-in to reveal verbose details.

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

**Not:** A Dog, Agent process, Prompt Turn, or long-running task.

### Dog

**Context:** Voice Interaction

Walker's named voice persona for one Managed Session. The Dog makes a session easy to refer to aloud and gives Walker's presentation its characteristic flavor.

**Identity:** A Dog is addressed through a mutable, pronounceable Alias. The Alias is not persistence identity and is not the ACP Session ID.

**Maps to:** Exactly one Managed Session while attached.

**Not:** An ACP Agent, Agent process, model, Prompt Turn, or task.

**Implementation rule:** Core ACP and Session Management types should not use `Dog` in their names. Walker prompts, Walker tools, speech projections, and voice-specific tests may use Dog terminology freely.

### Pack

**Context:** Voice Interaction

Walker's spoken presentation of the set of Dogs currently known to the conversation. It may include Dogs whose sessions are active, ready for another turn, or in need of User attention.

**Not:** An ACP connection pool, process supervisor, or canonical name for the Session Manager.

### Alias

**Context:** Session Management and Voice Interaction

A mutable, pronounceable, locally assigned handle by which Walker and the User refer to a Managed Session as a Dog.

An Alias is distinct from both the opaque ACP Session ID and an Agent-supplied Session Title.

Aliases remain attached to live Managed Sessions across voice calls while the
Session Manager remains active. An Agent-discovered persisted ACP Session has no
Dog or Alias until Dogwalk loads it into a new Managed Session; revival assigns a
fresh Alias rather than treating its Session Title as a spoken identity.

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

### Prompt Turn

**Context:** ACP Integration and Session Management

One complete interaction cycle beginning with `session/prompt` and ending when the Agent returns a stop reason. A Prompt Turn may include many model exchanges, plans, tool calls, updates, Permission Requests, and Elicitations.

A completed or cancelled Prompt Turn does not imply that its ACP Session has closed. Another Prompt Turn may continue the same session context.

**Not:** An ACP Session, Dog, Agent, or necessarily the whole engineering task.

### Assignment

**Context:** Voice Interaction

Walker's plain-language presentation of what the User wants a Dog to do. An Assignment commonly produces one Prompt Turn but may require follow-up turns.

Assignment is interaction language, not a protocol lifecycle entity.

### Initial Brief

**Context:** Session Management

The first prompt content Dogwalk constructs from the User's Assignment plus role, safety, workspace, and reporting context. Subsequent prompts continue the same Managed Session without recreating the brief unless required by an Agent adapter.

### Activity

**Context:** Session Management and Voice Interaction

A concise, User-safe projection of current Agent updates, usually plans and tool-call status. Activity is observable progress, not private reasoning and not a second Agent turn.

### Report

**Context:** Session Management and Voice Interaction

A concise result projected from Agent messages and the Prompt Turn outcome for Walker to relay. A Report may state that a turn stopped without establishing success. It must not erase the stop reason or failure state from the underlying Turn Result.

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

An Agent-reported execution strategy whose entries and status can change during a Prompt Turn. Plans are useful input for spoken progress summaries but are not commands issued by Walker.

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

Walker may call a ready Dog "resting" and an in-progress Dog "working." Those are voice projections, not core lifecycle values.

### Attention Lifecycle

An Attention Request is received from the Agent, queued for the appropriate interaction surface, presented to the User, resolved with subtype-correct semantics, and returned to the Agent. Cancelling a Prompt Turn also resolves or cancels its pending protocol requests as ACP requires.

## Operations and Voice Translation

| Neutral operation | Meaning | Walker language |
|---|---|---|
| Create Managed Session | Establish a new ACP Session and local projection | Sic a new Dog |
| Begin Prompt Turn | Send prompt content to a Managed Session | Give a Dog an Assignment; relay a follow-up |
| Inspect Managed Session | Read projected state without starting another turn | Check a Dog |
| List Managed Sessions | Discover sessions currently known to Dogwalk | List the Pack |
| Discover Persisted Sessions | List Agent-held session history without attaching it | Recall Dogs from previous calls |
| Load Persisted Session | Attach an Agent-held ACP Session as a new Managed Session with a fresh Alias | Revive an old Dog under a new name |
| Set Alias | Change the local pronounceable handle | Name or rename a Dog |
| Cancel Prompt Turn | Stop current Agent work while retaining the session | Stop what the Dog is doing |
| Close Managed Session | Release the active ACP Session and detach its Dog | Call off the Dog |
| Delete Persisted Session | Remove Agent-held session history when supported | Forget the session, with explicit confirmation |
| Resolve Permission | Select one Agent-supplied permission option | Give or refuse permission |
| Resolve Elicitation | Accept, decline, or cancel requested input | Answer or decline the Dog's question |
| Set Session Config Option | Change an Agent-advertised session setting | Change how this Dog works |

Voice phrases are not method names for the core. The Voice Interaction adapter translates them into neutral operations with precise lifecycle effects.

## Explicit Non-Synonyms

- An ACP Agent is not a Dog.
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
- A Pack is not an ACP connection pool.
- A voice call is not an Agent Connection.

## Adapter Vocabulary

The following names may appear in adapter configuration and deployment documentation but must not define core domain types:

- **OpenCode:** Current ACP Agent Implementation.
- **OpenAI Realtime:** Current Voice Transport and realtime model provider.
- **Daytona:** Candidate remote Sandbox provider.
- **stdio, WebSocket, HTTP:** Transport mechanisms.
- **ACP Python SDK:** Current protocol library.

Adapter-specific limitations should be represented as negotiated capabilities, adapter behavior, or compatibility notes rather than generalized into Dogwalk's domain model.

## Architectural Consequences

- Walker tools translate Dog terminology into neutral Session Management operations.
- ACP SDK and wire-version types stop at the ACP Integration boundary.
- Agent launch commands and process topology belong to Agent Hosting adapters.
- Managed Session state and Prompt Turn state are represented separately.
- Permission Requests and Elicitations may share an Attention queue but retain distinct types and response rules.
- Dogwalk stores Alias separately from ACP Session ID and Agent-supplied Session Title.
- Agent-discovered sessions remain unattached history until explicitly loaded; loading creates a Managed Session and assigns a fresh Alias.
- Speech-safe Activity and Reports are projections of richer protocol state, not replacements for it.

These consequences constrain responsibilities and naming. They do not prescribe a module tree, class hierarchy, persistence technology, or number of source files.

## Historical Language

The following language appears in current spikes or design fiction and should be interpreted carefully:

- **Dog as subprocess:** The current spike starts one OpenCode process and Agent Connection per Dog. This is hosting policy, not Dog identity.
- **Dog as task:** Early design fiction described Dogs as ephemeral per task. The established model retains a Managed Session across multiple Prompt Turns.
- **Working/resting Dog:** Useful spoken projections of turn activity and session readiness, not ACP lifecycle states.
- **Dog completed:** Means the current Prompt Turn stopped. The Dog's Managed Session may remain ready for another turn.
- **Call off as cancellation:** Voice intent must distinguish stopping an active Prompt Turn from closing the Managed Session.
- **Dog status as lifecycle:** Early spikes stored `working`, `resting`, and `cancelled` as one status. These are now voice projections over separate Managed Session and Prompt Turn state.

## Open Questions

- Should queued follow-up prompts be first-class Prompt Turns immediately, or become Prompt Turns only when sent to the Agent?
- Which Agent plan and tool-call changes deserve proactive speech, an earcon, or silent state projection?
- How should Walker resolve ambiguous spoken "stop" among stopping speech, cancelling one Prompt Turn, closing one Managed Session, stopping all Agent work, and ending the voice call?
- Which parts of Assignment history belong to Dogwalk versus the ACP Agent's retained session context?

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
