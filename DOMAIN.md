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

Dogwalk is an eyes-free coding-agent client and multi-session coding manager. It gives a User the capability surface of a visual multi-session coding client through hands-free voice, without requiring access to a screen, mouse, or keyboard.

The Voice Agent is the realtime conversational interface through which the User supervises coding work. It translates conversational intent into precise, neutrally named session-manager operations. Coding work is performed by Agents such as OpenCode, not by the Voice Agent.

The Voice Agent is one character performed by two components: a Voice Frontend that holds the conversation and a Session Orchestrator that exercises engineering judgment and operates Managed Sessions. The User experiences a single character. The Voice Frontend speaks of the Session Orchestrator as its own thinking ("let me think"), which keeps an account of its limits honest without naming models; when the User asks about the architecture, it explains the split candidly.

Dogwalk presents as an agentic development environment driven by a coworker. Agents should see ordinary requests from a colleague: they need not know they are supervised by voice, and anything specific to calls or speech stays inside Dogwalk.

## Modeling Principles

- Dogwalk's session-management vocabulary is its own, historically anchored in ACP (see Agent References). The Agent's API terms (executions, inbox, delivery) stop at the Agent Integration boundary, except where one determines a domain rule, which then says so.
- Dogwalk is a product codename, not the source of its domain vocabulary, and it implies no fiction: the Voice Agent speaks the domain's own terms, plainly and directly.
- A Voice Call is complete in words. Every piece of information reaches the User as speech; no sound stands in for one. The only tones are ritual cues that carry nothing speech does not also say: the recording beep follows a spoken "after the beep", the Identity Check's round tone follows the spoken instructions, and its received cue fills the wait for a spoken verdict.
- Dogwalk depends on OpenCode deliberately: its UX, AX, and AI/HCI research moves faster than standards do, and it already depends on specific providers. The Agent Implementation is therefore a chosen dependency, pinned and upgraded explicitly. Model providers, the voice transport, and Daytona remain adapters.
- An Agent entity, an application projection, and its spoken presentation are different things even when they currently have a one-to-one relationship.
- Derived state must not be presented as Agent-defined state. Dogwalk may infer that a session is ready or that a turn needs attention, but those are Client projections.
- Opaque Agent identifiers stay out of speech. The Voice Agent refers to work by purpose, recency, and pronounceable Alias.
- Whatever the Voice Agent can change, it can also sense without changing it: every setter (a pairing, what a Display shows, an Access Grant, which session is followed) has a read-only counterpart, and the state that matters at the start of a Voice Call (including each Display, whether a browser has it open, what it showed recently, and its live Access Grants) arrives with the call.
- The current process topology is an implementation choice. One Agent service in the Sandbox serves all of a User's sessions and any number of clients.

## Bounded Contexts

### Voice Interaction

Owns the live conversation, Voice Agent personality, spoken reference resolution, interruption, muting, and speech-safe summaries.

### Session Management

Owns Managed Sessions, Prompt Turns, workspaces, aliases, projected state, concurrency, Attention routing, and User attention. It exposes neutral operations that any interaction surface could use.

### Agent Integration

Owns the Agent's API and events, Agent Sessions, prompts, Steering, interrupts, permissions, configuration, catch-up after reconnecting, and tracking of the Agent's version. It speaks the Agent's terms at its boundary and shields Session Management from them.

### Agent Hosting

Owns how the Agent is located, launched, supervised, connected, authenticated, isolated, and stopped. A local OpenCode server and the Sandbox's shared OpenCode service behind the Agent Gateway are alternate hosting arrangements.

### Voice Transport

Owns realtime audio transport, speech turn detection, browser or telephone media, and provider-specific function calling. OpenAI GPT-Live over SIP is the current adapter, not the definition of the Voice Agent.

### Access Control

Owns Registration, Handles, Homes, Voice References and Re-enrollment, the Identity Check,
Challenge Practice, Invite Codes, and Invitations at the PSTN boundary. A User is identified by a
spoken Handle, never by a personal name, birth date, or account number, and
proves it by voice in an Identity Check before the Voice Agent answers. Caller
ID only suggests which Handle to check. Telephony providers and sandbox hosts
are adapters, not identity authorities. Phone numbers, Voice References, the
invitation graph, and identity audio stay here and in the caller's Voice Session
state: they never reach Agents, Sandbox files, or hosting-provider labels.

The access rules aim at casual impersonation: friends, pranksters, and
opportunists who know a Handle or briefly hold a User's phone. They do not stop
a determined attacker with a recording of the User, who can speak each fresh
challenge phrase through a real-time voice converter that narrowband telephone
audio helps disguise. Dogwalk says so rather than presenting the Identity Check
as a password replacement. `ACCESS_THREAT_MODEL.md` records the threat model, what
each kind of evidence establishes, the current Sandbox powers and durable-access
paths, and the accepted risks; admission is call-scoped, and limiting what a
Workspace can do is the main mitigation.

Every way out of an identity ends at the same way in: a User who loses their
Home, fails their Identity Check, or is reaped can be invited again by any
active User and register as a new User, through the same ritual every newcomer
uses. Like crash-only software, the recovery path is the ordinary path, so it is
exercised constantly and needs no special machinery. Durable work belongs in
places that outlive a Sandbox, such as Git remotes.

### Voice Research

Owns Voice Donations: engineering voice data that a caller gives on purpose so
Dogwalk's speaker verification, signal processing, and embeddings can be tuned
and compared. Everywhere else, operational audio is transient (challenge audio
is discarded once scored, and Voice References are embeddings, never audio). A
Voice Donation is the one place Dogwalk keeps raw caller audio, and only by
spoken consent, with no link to the caller: no phone number, Voice Call
identifier, User, or Handle is stored with it. Voice Research reads nothing from
Access Control and writes nothing back; any caller may donate, registered or
not, and donating changes nothing about their access.

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
    |  translates managed lifecycle into Agent operations
    v
Agent Integration
    |
    v
Agent

Agent Hosting supplies connections and isolation to Agent Integration.
Voice Transport carries the conversation used by Voice Interaction. A direct
provider-to-model media path may be controlled through a separate application
sideband; the media path and control path remain parts of one Voice Call.
```

## Roles

### User

**Context:** Voice Interaction

The human principal whose intent, authorization, and attention govern the work. The User may speak imprecisely and should not need to pronounce technical identifiers. A User is an account with continuity (Handle, Sandbox, Memory, Invitations), usually one person's; a Voice Transfer can pass it to another person, as when a caretaker hands an account back to a recovered patient.

### Voice Agent

**Context:** Voice Interaction

The single character the User talks to: socially fluent, a little modest about engineering detail, and authoritative about session state. It speaks plainly and directly in the domain's own terms, without metaphor or persona: it says what happened, what is still uncertain, and what the User must decide, and never softens a failure. Permission Request choices, failures, destructive actions, numbers, and anything the User must act on are stated exactly. It mirrors the User's sophistication, especially on technical topics: modest and plain with a newcomer, fluent with someone who already speaks about agents, sessions, and delegation, so existing agentic literacy amplifies the experience. It keeps power where it is: Agents act in the Sandbox and often take initiative; the User holds authority (intent and Permission Requests); the Voice Agent holds attention and translation, suggests work, and never claims an Agent's work as its own. It is composed of a Voice Frontend and a Session Orchestrator, and speaks with one GPT-Live voice.

**Not:** An Agent, coding harness, Agent host, or a single model.

### Voice Frontend

**Context:** Voice Interaction

The realtime speech component of the Voice Agent. It owns turn-taking, interruption, conversational repair, persona, and spoken phrasing. It has no session tools: when the User asks for or about work, it raises a Handoff and waits for the Session Orchestrator rather than guessing. It treats Orchestrator context as background and Orchestrator speech requests as things to say in its own words.

**Not:** An engineering authority or a source of session state.

### Session Orchestrator

**Context:** Session Management and Voice Interaction

The reasoning component of the Voice Agent. It reads Handoffs and harness events, resolves references, writes engineering briefs for Agents, invokes the neutral operations, and decides what the Voice Frontend should know (unspoken context) or say now (speech requests). Its conversation context persists across Voice Calls; between calls it accumulates events but takes no action.

It is a client of Session Management, never a replacement for it: Managed Session and Prompt Turn projections remain authoritative, and the Orchestrator reports completion only from them.

Its context is bounded by its own judgment: it compacts by rewriting its Memory, which drops earlier conversation from its context (see Memory).

It **snoozes**: it thinks only when woken, by a Handoff or its Continuation, or by a harness event that plausibly requires steering the Voice Frontend or the Sandbox again. Waking events are caller input (keypad digits, a Whiteboard Answer, the last file of an Upload drop, whether it landed or failed), a browser claiming a Pairing Code (the User is waiting to be asked whether it is theirs), a Whiteboard error to redraw, a Permission Request or Elicitation, a Prompt Turn's end or failure, a Bid, a recording saved or failed, the Agent becoming unavailable or a Sandbox restart failing, the Agent Connection arriving when work waits on it (a session operation failed fast while it was starting, or the Orchestrator restarted the Sandbox itself), a Pairing Code that lapsed unclaimed, the first call's tour, and the outcome of a Compaction the User asked about (one given a focus). Every other event (a call starting or ending, earlier files of an Upload drop, a browser connecting or going stale, a Permission Request answered elsewhere, a turn begun elsewhere or by Steering, a discarded recording, the Agent Connection starting, a keypad restart, a voluntary Compaction's outcome) is appended in order and reaches it with the next woken step, as background that may be stale. A woken run ends when a step makes no tool call, or only speech requests, none of which declares that more follows.

Its context is **append-only**: each model call is its instructions (fixed), its pinned Memory, then its conversation, and every step only appends to what the previous one sent, so the provider's prompt cache serves everything before. Earlier items, including the provider's opaque reasoning items, are never rewritten, reordered, or removed between steps; only a Compaction or the backstop cuts the conversation, and each starts a fresh cache. Every model call the Orchestrator makes (a step, the Memory writer, the next call's welcome line) shares this prefix and differs only in what follows the context.

**Not:** An Agent, the Session Manager, or a store of record.

### Memory

**Context:** Voice Interaction

The Session Orchestrator's curated, bounded note of what matters across Voice Calls: the User's preferences, ongoing projects, what each Managed Session is for, commitments, and loose ends. The Orchestrator alone writes it, and rewrites it whole rather than appending: a **Compaction** replaces the Memory and removes the conversation before a safe point (the start of the current Voice Call, or a recent stretch when the call is long) from the Orchestrator's context, archiving it for diagnostics. Compaction is the Orchestrator's own choice during a call: it senses its context size and decides when, typically as a chapter of work closes. When a Voice Call ends with a large context, Dogwalk compacts it through the end of that call, so the next call starts from the Memory, the closing words, and the Opening; the provider's cache has usually lapsed by the next call, so the shorter context costs nothing to reuse and less to carry. During a Voice Call the User is told briefly that the Voice Agent is tidying its notes and can keep talking: the Memory is written apart from the Orchestrator's ordinary work, which never waits on it (a Handoff or a hangup goes ahead), and the conversation it removes is only what it summarized, never what was added meanwhile. A Compaction outlives a call that ends while it runs, since a hangup may be accidental and the User may call back expecting the Voice Agent to remember. The Memory is pinned at the head of every Orchestrator model call and informs the Opening's welcome line. A backstop drops the oldest context when the Orchestrator neglects to compact and its context grows near the model's limit, and records that it happened.

The User does not edit the Memory directly; they may ask the Voice Agent to remember or forget something, which the Orchestrator honors at its next Compaction. It is readable, read-only, on the Diagnostics Surface, and the Session Orchestrator can draw it on a Display.

The Memory has no expiry of its own: it lives exactly as long as its User and is wiped when the User is reaped. The Opening tells the Orchestrator how long it has been since the last call, so an old Memory is treated as old rather than lost.

**Not:** The Orchestrator's conversation history, an Agent Session's history, a Voice Agent Skill, or a note in the Workspace.

### Agent Client

**Context:** Agent Integration

The role that connects to the Agent, creates or loads sessions, sends prompts and Steering, receives events, presents permission choices, and controls access to Client-Provided Tools.

Dogwalk is an Agent Client. The Voice Agent may initiate Client operations through Dogwalk's neutral tool surface, but the Voice Agent itself is not the complete client. Other clients may be co-present (see Co-presence).

### Agent

**Context:** Agent Integration

The coding harness that owns conversation context, processes prompts, uses models and tools, and reports events, including the outcome of each Prompt Turn. It is distinct from the Voice Agent.

OpenCode is the Agent Implementation, through its native server API. Replacing it is possible but would mean a new adapter, and is not a design goal.

**Not:** A model, subprocess, session, Prompt Turn, or task.

## Core Concepts

### Registration

**Context:** Access Control

The creation of a User by redeeming an Invite Code. Registration assigns the
new User a Handle, makes the calling number their Home, and ends with enrollment:
the new User repeats a few phrases like Identity Challenges, and their Voice
Reference is computed from them (see Voice Reference, first-call enrollment). A
caller who skips it, or whose line yields too little speech, enrolls on their
next Home call instead. Right after the caller confirms their invite words,
before their Handle, they hear the shape of what is left (their name, then
five short sentences, about two minutes), because an invitee is often alone on
the line without the inviter there to say it. It records which Invite Code authorized it
and when, but contains no personal name, sandbox identity, or hosting policy.
When the Invite Code belongs to an Invitation, the Registration is the durable
inviter -> Invitation -> invitee edge, and the invitee can ask the Voice Agent
who invited them (the inviter's Handle). A User who lost their identity
registers again as a new User; nothing links the two.

Registration records the number it came from. There is no cap on Users per
phone: a shared household line, a shelter's phone, or a payphone is first-class,
and each Registration from a Home already displaces the previous User to
Roaming, so a phone is Home to one User at a time. Instead, a **Registration
cooldown** stops a number that registered a User in the last 24 hours (deployment
configuration) from registering another; the caller hears why before their
invite words are read back, so the words are not spent, and can register from a
different phone at once. Invitations remain the real bound (each Registration
through an Invitation spends it, attributed to its inviter; operator Invite Codes
are the operator's own decision), and the Diagnostics Surface lists numbers
that registered several Users in the last 30 days for the operator to judge.

### Handle

**Context:** Access Control

A User's spoken, nonsecret, unique name: two words, such as "brindle whippet",
assigned at Registration. It is a display name over a stable identity that is
never spoken, so changing it changes nothing else: invitations, the Sandbox,
and history follow the User, not the Handle. A caller says it to claim an
identity; the Identity Check proves the claim. A User may rename themselves at
any time through a ritual: Dogwalk offers three available Handles, and the User
picks one, spins again (three spins per call), or keeps their name. The Voice
Agent can set only a Handle Dogwalk offered in that call, so nobody chooses a
name freely. A Handle left behind by renaming or reaping is quarantined for 90
days, so a stale name reaches nobody for a while, and then returns to the pool. Handles draw from EFF short wordlist 2,
whose longer words survive narrowband audio and whose unique three-letter
prefixes tolerate speech-recognition errors, minus grim words, every word of
the Invite Code vocabulary (the PGP word list), and the Identity Check's escape
words. Two words against three, from disjoint vocabularies, lets one transcript
tell a Handle from an Invite Code.

**Not:** A credential, an Alias, or an Invite Code.

### Home

**Context:** Access Control

The one phone number Dogwalk associates with a User for convenience. A User has
at most one Home and a number is Home to at most one User. A call from a Home
starts the Identity Check with that User's Handle and one Identity Challenge, or,
soon after the User's last call, the name path (see Identity Check).
A Home expires 90 days after its last successful call. Any User who passes the
Identity Check may make the calling number their Home, displacing that number's
previous User, who still gets in by naming their Handle. Re-homing is rare; the
usual alternative is Roaming. Only Registration re-homes today; re-homing after a
Roaming admission, at the User's explicit spoken request, is not built.

**Not:** The User's identity or proof of it. Caller ID only selects which
Handle the Identity Check starts with.

### Voice Reference

**Context:** Access Control

The enrolled speaker embedding against which a User's Identity Challenges are
scored, tagged with the embedding space (model weights, preprocessing, and
normalization) that produced it. It is never audio, and challenge audio is
discarded once scored. It is deleted when its User is reaped.

A Voice Reference never learns from ordinary calls: passing an Identity Check
changes nothing, so it cannot drift toward an impostor or a passing condition.
It changes only through Re-enrollment, which the User starts, like re-homing.
Just as a User can call from a strange number by passing challenges, they can
call from a strange acoustic setting (a car, new earbuds, a noisy room) by
passing challenges, and only if that setting becomes their new normal do they
move their Voice Reference there.

A User without a Voice Reference (registered before enrollment existed, or who
skipped it) gets **first-call enrollment** on their next Home call in place of
the Identity Challenge: its shape first (five short sentences, about a minute),
a short spoken explanation (Dogwalk keeps a voice
profile, never a recording, to recognize them from any phone; it never learns
from calls; they can redo it later), then five phrases in the Identity
Challenge's shape with no level spoken. Progress is marked only near the end
("Two more.", "Last one."), counting collected answers, so a wrong phrase or
too little speech never moves it; Re-enrollment's phrases are marked the same
way. Each matched answer is embedded, the
Voice Reference is the mean of the answers' normalized embeddings with enough
speech, in one embedding space, and fewer than three such answers stores
nothing, so enrollment is offered again on the next call. Saying stop skips it.
Enrollment never replaces an existing Voice Reference: that is Re-enrollment.

### Re-enrollment

**Context:** Access Control

The User-initiated ritual that replaces a Voice Reference, for example after a
new phone, or when earbuds or a car become the User's usual way of calling. The
User passes an Identity Check first, then repeats a fresh set of enrollment
phrases in the new setting, and the new reference replaces the old one
outright: earlier enrollment does not blend in. It is the voice counterpart of
re-homing a number, and neither happens automatically. A Voice Reference changes
at most once a day (counted from when it was last set, by first-call enrollment
too), so a caller cannot walk it toward another voice in a series of small
steps; a caller whose profile changed in the last day hears so and keeps it.

The User asks the Voice Agent for it in conversation ("re-enroll my voice"),
which hands the call to a deterministic, speech-only ritual and gets the call
back when it ends, knowing how it ended (whether the Voice Reference was
replaced, or why not), so it can say so plainly when asked. Re-enrollment replaces the credential every later Identity
Check is scored against, so it is never weaker than what it protects, and its
check is enforced even while the Identity Check is in shadow mode. It is
reached from an admitted call (a Home call or one admitted by Roaming), and asks as many
Identity Challenges as Roaming, each of which must match the current Voice
Reference: one that does not ends it. Wrong phrases and too little speech never
count as failures, as in any Identity Check. Then the User repeats first-call
enrollment's phrases from the new setting. When the new Voice Reference still
matches the old one at the Identity Challenge's threshold, it replaces it; when
it does not, the answers are a different voice, and the ritual becomes a Voice
Transfer. Any other end (stop, silence, too few answers, a verifier error, a
reference in another embedding space) leaves the Voice Reference unchanged, and
the caller hears so. For three days after a replacement, the User's Openings
say that their voice profile was replaced, or given to a different voice, and
when; this is derived from Call Activity on every call, so no call can consume it. A User without a Voice Reference has nothing to replace and gets
first-call enrollment on their next Home call instead. Like a Voice Reference
itself, Re-enrollment keeps no audio, and its collected embeddings are deleted
when it ends.

**Not:** Adaptive enrollment, first-call enrollment, Challenge Practice (which
never changes a Voice Reference), or a Voice Donation (which never touches a
Voice Reference).

### Voice Transfer

**Context:** Access Control

Re-enrollment that hands a User to a different voice, on purpose: a caretaker
who ran an account for a recovering patient hands it back once the patient's
voice returns, or the reverse before planned surgery. The current voice passes
Re-enrollment's check, the new voice repeats the enrollment phrases, and when
those do not match the current Voice Reference, the caller hears what
continuing means (the new voice gets the account, with its files, Memory, and
connected accounts) and that the current owner must repeat one more sentence to
agree. That one Identity Challenge, against the current Voice Reference, shows
the current voice was still present after hearing the consequence; it replaces
the earlier rule that a different speaker could never take over after someone
else passed the check. A match replaces the Voice Reference with the new voice's;
anything else (no match, stop, silence) leaves it unchanged. The Voice Agent
starts one only when asked and never suggests it.

When the current voice is gone and cannot agree (a sudden illness), there is no
voice path: the operator deletes the Voice Reference, decides what to do about
the Home, and the next Home call enrolls whoever calls. Deciding that is the
operator's judgment, not a ritual.

**Not:** Handoff (the Voice Frontend's request to the Session Orchestrator),
re-homing, or recovery by a new Registration (which starts a new User).

### Identity Check

**Context:** Access Control

The deterministic, speech-only stage of a Voice Call before the Voice Agent
answers. No model interprets it and the keypad is never required. A call from a
Home hears the Handle and one Identity Challenge; any other call, or a caller
who says they are someone else, names a Handle and takes three Identity
Challenges. Each answer's transcript is classified deterministically: the
challenge phrase (its audio is then scored against the Voice Reference), a
Handle (switch to that identity), an Invite Code or a mention of an invite
(Registration), a mention of donating (Voice Donation), or anything else (ask again). Only a matched phrase in the
wrong voice counts as a failure, so noise and bad lines never lock a caller
out. A matched phrase with too little speech to judge (as the verifier measures
it; the floor is configuration) is neither a pass nor a failure: the caller gets
another phrase. A User whose Home check fails moves to the three-challenge path, never to
a dead end. Repeated failures for one Handle pause checks for that Handle for a
while, and the User hears about the attempts in their next Opening. The check
runs while the Sandbox wakes, and the Session Orchestrator learns how the
caller passed it. After each scored challenge the caller hears a terse level of
voice match, as the Opening reports caller-ID attestation: how strongly their
voice matched, not the raw score. This makes the score something a caller can
climb by trying again; that is accepted for now, in favor of telling Users how
well Dogwalk recognizes them.

A Home call hears its Handle with full instructions (repeat the sentence after
the tone; say so if you are someone else) until the User has completed a few
scored Home challenges, then only a short prompt ("Your sentence."). The count is
Access Control state kept with the User, never the Session Orchestrator's
Memory. A caller who says they are someone else without naming a Handle is asked
who is calling: a Handle, an Invite Code, or donating; anything else ends the
call politely. A caller who names the Handle they are already being checked as
hears the same sentence again. The check runs once per Voice Call: returning to
the Voice Agent later in the call (after Challenge Practice, a dropped voice
connection, or recovery) does not repeat it.

The **name path** makes daily use smoother: a Home call within a configured
window of the User's last Voice Call (as that User, from any phone: Home or an
admitted Roaming call) hears its Handle and is asked to say it in a carrier
phrase instead of a sentence ("Dogwalk, for anchor antelope." then the tone and
"Say: this is anchor antelope."); the carrier lengthens the speech, since a bare
Handle (about a second) often falls under the verifier's floor. The answer is
classified like a phrase whose content words are the Handle, so "this is anchor
antelope", "it's anchor antelope", and the bare Handle all match; a matched one
is scored against the Voice Reference like any challenge and recorded as the
name path's, but the caller hears no level and is never asked again: the answer
is under two seconds of speech, under the floor a level needs, where genuine
scores run low, so a spoken level would be wrong more often
than informative. Thin speech, a verifier refusal, or no score at all connects
as any result does. Anything but the Handle (nothing heard, other words, too
many requests to repeat) falls back to one Identity Challenge, taught as on a
call past the window; someone else, another Handle, an invite, or donating
escape as from a sentence. Name answers are not completed challenges, so the
sentence's teaching count does not move. The name path is weaker evidence than a
sentence by design: it trades security for smoothness through the window, a
tunable parameter (see Open Questions).

**Shadow mode** is the current deployment state. The check runs, is scored, and
is reported in Call Activity and to the Session Orchestrator, with what an
enforcing check would have done (admit, require Roaming, deny, or undecided),
but it never withholds entry from a Home call: whatever the level, a failed or
unscorable challenge, a verifier error, or the caller saying stop, the call
connects to the Voice Agent as before. Unless Roaming admission is on (see
Roaming), a caller who names another User's Handle takes that User's three
Identity Challenges, hears each level, is told that calling as themselves from
another phone is coming soon, and the call ends; they are never admitted as that
User. Shadow Roaming records no attempts and pauses nothing.

### Identity Challenge

**Context:** Access Control

One fresh, short, grammatical sentence the caller repeats at once in their own
voice, such as "The golden kettle belongs in the kitchen." Nothing is remembered
for later. Phrases use familiar syntax and concrete words, and avoid digit
strings, tongue twisters, disturbing content, Handle words, and escape words.
Phrase correctness and speaker similarity are separate signals, and both must
pass. Correctness is deterministic and strict: every content word of the phrase
(all but articles, possessives, and auxiliaries; prepositions count) must appear
in the transcript, in order, with only plural inflection forgiven and extra
words allowed between them. A mismatch, whether the caller misheard the prompt
or the recognizer misheard the caller, is a wrong phrase: any score of its audio
is discarded, never used, spoken, or logged, and it is never a voice failure.
Scoring may begin as soon as the answer ends, before the transcript arrives,
because a score decides nothing until the phrase matches; if the caller keeps
talking, only a score of the whole answer counts. The recognizer is hinted with the phrase's words, so a
correct repetition comes back as them while a different word still comes
through. A caller who asks to hear the sentence again ("repeat", "say that
again", "pardon") in a short utterance hears the same phrase again, counted as
neither an answer nor a failure; past a few replays the request earns a fresh
phrase instead, still not an answer. Every Identity Challenge has the same spoken
shape: a tone, the sentence, the caller's answer, a received cue the moment an
answer is captured, then the verdict. The received cue (two falling blips, unlike
the single tone) tells the caller their answer arrived, so the seconds spent
judging it are not silence. It follows every captured answer, scored or a wrong
phrase, so the ritual never changes shape, and never silence or a request to
repeat, which are not answers. Like the recording beep, it belongs to the ritual:
the spoken verdict that follows says in words what the cue anticipates. Fresh
phrases defeat replayed recordings, not voice cloning. The
similarity threshold is configuration, not a domain rule. It starts at a cosine
of 0.5: synthetic impostors through a telephone path scored at most 0.40, and
the operator's own voice in an unenrolled setting (car hands-free) at least
0.50. It is adjusted as real calls and Voice Donations accumulate.

### Challenge Practice

**Context:** Access Control

A User's rehearsal of Identity Challenges, asked for in conversation ("challenge
me", "test my voice"): the Voice Agent hands the call to a deterministic,
speech-only loop that offers fresh phrases until the caller says stop or a round
limit is reached, then returns the call to the Voice Agent, which learns how it
ended (never a level). Each round follows
the Identity Challenge's shape (tone, sentence, answer, received cue, level) and
the Identity Check's rules: a transcript that does not match the phrase has its
score discarded, too little speech to judge earns another phrase without counting, a
request to hear the sentence again replays it, and a
scored round is spoken only as a level of voice match (strong, matched, or not
matched), never as the raw score. The levels come from configured thresholds, the
lower one being the Identity Check's. Practice never gates entry, keeps no audio
(each round is scored in memory and discarded), and never changes the Voice
Reference; Call Activity keeps each round's outcome, score, and speech duration
for calibration. A User without a Voice Reference hears that practice needs an
enrolled voice.

**Not:** An Identity Check, Re-enrollment, or a Voice Donation.

### Roaming

**Context:** Access Control

Using Dogwalk from a number that is not the User's Home, such as an office
phone, a friend's phone, or a payphone: the caller names their Handle and takes
three Identity Challenges. Roaming grants the same access as a Home call and
leaves the Home unchanged. In shadow mode (see Identity Check) the challenges
run and are recorded, but Roaming admits nobody.

**Roaming admission** is configuration, off by default, and the first place the
Identity Check withholds or grants access. When it is on, an unregistered
caller's greeting asks for a two-word Dogwalk name as well as invite words, and
a caller who names a Handle there (or at another User's Home check) takes that
User's three challenges, hearing each level. All three must match: the first
unmatched voice on a matched phrase ends the attempt, since no later round could
change the decision and each would be one more level to tune an impersonation
against. Passing admits the caller as that User for the rest of the Voice Call,
and returning to the Voice Agent later in the call does not check again. Wrong
phrases, thin speech, silence, and stop end or prolong an attempt but are never
failures. Failed attempts in the last day pause Roaming for that Handle (three)
and from that calling number across Handles (five); the Home call is never
paused. A pause that begins during an attempt also stops its admission. An
attempt is recorded before its first challenge, so hanging up after hearing a
level cannot hide a failure, and a Handle takes one attempt at a time, so calls
in parallel cannot all start before any has failed; an attempt that outlives its hold on the Handle (minutes) ends without a verdict. Only an attempt recorded
under admission can admit. The User's next call that was not itself admitted by
Roaming (their first call too) says, after the caller-ID clause, how many calls
got in as them from other phones and how many voice checks as them failed, since
their last call from home; never which numbers or when. The notice is marked
told only on a call whose own voice check matched, so a spoofed Home number
(which connects whatever its check) cannot clear it; until then every such call
repeats it. An Opening that never reached the caller leaves the notice for the next call.

**Not:** A reduced access tier, or re-homing.

### Invite Code

**Context:** Access Control

A speakable capability that authorizes Registration. An Invite Code may have an
expiration time and a maximum number of uses. No expiration means it remains
valid indefinitely; no maximum means unlimited registrations. A code is usable
only while unexpired and below its maximum. Registering from a number that is
already a Home displaces it, as re-homing does. An operator mints Invite Codes
directly; a User mints one by creating an Invitation.

Hearing an Invite Code tolerates how speech recognition writes it: the code may
be any run of the words heard (a caller adds words around it), its spaces are
ignored (recognition splits compounds such as breadline into "bread line"), and
a near miss of up to two letters counts when exactly one usable code is that
close. The readback and the caller's yes still confirm every match.

### Invitation

**Context:** Access Control

A User-created, single-use Invite Code with provenance: the inviting User
(resolved from the authenticated Voice Call, never from what a model claims),
an optional bound recipient number, and a lifecycle of active, then redeemed,
expired (24 hours after creation), or revoked. A User may hold at most three
active Invitations. Unbound, the words work for whichever caller redeems them
first; bound, only from that number. The inviter binds or changes
the recipient only while it is unredeemed, and only after the Voice Agent read
the number back and the inviter confirmed it; this records an intended
recipient and does not verify that the recipient controls the number, which
only their own Voice Call does. Redemption goes through the ordinary
Registration ritual and applies expiration, single use, revocation, and the
bound number in one transaction: a failed claim consumes nothing and records no
edge. Removing an Invitation from the User's list revokes it if still active
and hides it; nothing is deleted, so provenance and the Registration it
produced survive. The inviter hears each Invitation's status, when it was
redeemed, and by which Handle; the invitee may hear the inviter's Handle.

The invitee's own call records on the Invitation how far their Registration
got: the words read back, registered, how many enrollment answers were
collected, and how enrollment ended (enrolled, said stop, went quiet, too
little speech, could not run because of an error, or a hangup). Once that call has ended, or the
invitee has a Voice Reference, the inviter's next Voice Call tells the
Session Orchestrator in one sentence per invitee (for example, registered,
then hung up partway through voice enrollment, two of five sentences done),
named by Handle or by how many days ago the Invitation was made, never by
number or invite words, so the inviter can follow up in person. Each distinct
sentence is told once, marked only after the Session Orchestrator has read it
(a model step that read it succeeded), since the Orchestrator, not the Opening,
says it; a later change, such as enrolling on their first Home
call, is told again. Only Invitations from the last 14 days, not revoked or
removed from the list, are told. The
operator-wide graph is visible only on the Diagnostics Surface or in D1. Its
read-only Access Control projection includes hidden Invitations, distinguishes
intended recipients from registered invitees, and links each Invitation to its
inviter and redeeming Registration across generations. The default projection
masks phone numbers and omits Invite Code words; the operator's explicit verbose
opt-in reveals them.

**Not:** An operator Invite Code (no inviter, any expiry or use count), a
Registration, or proof of identity.

### Voice Donation

**Context:** Voice Research

A caller's spoken consent to give raw voice samples for engineering, and the
deterministic, speech-only flow that collects them. An unregistered caller says
"donate" at the registration prompt; a User asks the Voice Agent, which hands the
call to the same flow. A short consent script says what is recorded (the caller
repeating short phrases and a little free speech), why (to tune voice
recognition for Dogwalk), where (raw, on the operator's storage, not linked to
their phone number), that saying stop ends it at any time, and that deletion is
requested by telling the operator roughly when they called. Only a spoken yes
starts recording; anything else records nothing. Each round repeats a fresh
challenge phrase (the same generator as Identity Challenges) or asks for free
speech. Saying stop keeps what was already recorded and ends the call. The
keypad is never required.

**Not:** Registration (it creates no User and needs no Invite Code), an
Identity Check, or research under an ethics board's protocol.

### Donation Session

**Context:** Voice Research

One Voice Donation's stored result, named only by a fresh random id and dated by
when it started: the consent version, each round's prompt and kind, the
telephony provider's transcript and confidence for it, and the round's audio
exactly as the provider delivered it (decoded to linear PCM, with any lost
frames filled with silence and counted). Call Activity may note that a donation
happened on a call, but never which Donation Session it produced; the operator
finds a session for deletion by its time.

**Not:** A Voice Recording (it never enters a Workspace) or a Voice Reference.

### Voice Call

**Context:** Voice Transport

One provider-mediated live audio connection between a User and Dogwalk. A Voice
Call has transport identity and lifecycle independent of Agent Connections,
Managed Sessions, and Prompt Turns. Diagnostic Call Activity records the
ordered registration, hosting, and menu events observed during that call; it is
an operational projection, not conversation history. Agent Integration may later
project concise Agent events into the same timeline when they are
correlated with a Voice Call. Full prompts, model output, and tool payloads do
not belong inline in Call Activity when they are large; store bounded diagnostic
details or references. The Diagnostics Surface presents a redacted projection by
default and requires an explicit, session-only opt-in to reveal verbose details.

The Session Orchestrator may read the current User's recent Call Activity to explain interruptions, and each call begins with a since-last-call summary (how the previous call ended, including provider errors such as moderation, recovery actions, failed Prompt Turns, and its last words) as context for the Opening.

A caller who passes the Identity Check talks first: the Voice Call connects to the Voice Agent at once while the Sandbox is started in parallel, and the Agent Connection is established in the background. Until it is ready, Session Management answers inspection and listing from its persisted projection, operations that need the Agent fail fast with a neutral "still starting" error rather than waiting, and the Session Orchestrator keeps conversing and acts on held requests once the connection is ready. Losing the Agent Connection mid-call makes Managed Sessions unavailable but does not end the Voice Call. Only a Sandbox the provider reports broken sends the caller to deterministic recovery before the Voice Agent. A provider ending the voice connection is not a User hanging up, and neither cancels Prompt Turns; Agent work continues without the Agent Connection, and only an Agent Termination or a Sandbox restart or replacement interrupts it. A Voice Call may contain several voice sessions (after a drop, the recovery path can start another, and Challenge Practice or Re-enrollment returns to a fresh one). A voice session ends at the first of the provider's close event or the loss of the control sideband; the Voice Call ends only when the telephony provider reports it complete, and that report also closes out any voice session whose end was never observed. Each voice session's transcript is saved as it grows, so one whose end was never observed (the Durable Object reset mid-call) still files its transcript and last words when the telephony report arrives or the next voice session attaches.

### Opening

**Context:** Voice Interaction

The first words of a Voice Call, composed deterministically by Dogwalk with no model call on the critical path, after the Identity Check: a terse identity clause (the carrier's STIR/SHAKEN attestation), any Roaming as the User since their last call from home (calls admitted and voice checks failed, counted, on a call not itself admitted by Roaming; see Roaming), a replacement of their voice profile in the last three days (see Re-enrollment), the product name, a welcome line, and an invitation to speak. How the caller got in (Home or Roaming, the challenge's outcome and the level the caller already heard, or that they enrolled just now, and in shadow mode what enforcement would have done) is told to the Session Orchestrator with the Opening as context, never spoken by the Voice Frontend: the caller heard their level moments before, and the Session Orchestrator mentions it only when asked. The welcome line was written by one short Session Orchestrator model call when the previous Voice Call ended; deterministic clauses about Managed Session changes since then (a Prompt Turn stopped or failed, a Permission Request arrived, a recording was saved) and about an abnormal previous ending are added at connection, as is a budget clause once Dogwalk's estimated spend this month passes three quarters of its monthly inference budget (how much is used, or that it is exhausted and calls may stop working), so the caller hears it before asking for costly work rather than meeting a refused call. A User's first call after Registration gets a fixed welcome instead, and the Session Orchestrator follows it with a short spoken tour while the new Sandbox is created. The Session Orchestrator is told the Opening's exact words and does not repeat them.

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
| System Menu | see below | Menu choices |

### System Menu

**Context:** Voice Interaction, with Agent Hosting actions

A deterministic in-call menu opened with star: no model interprets it, the Voice Frontend does not hear the caller while it is open, and pre-rendered prompts speak it. It holds only what must work when the Voice Agent cannot hear the User or cannot think straight: one restarts the Sandbox after a second press of one confirms that running work stops; two hands the call to the deterministic recovery menu; star, or fifteen seconds without a key after the prompt, returns to the conversation. Routine operations go by voice instead: status (also keypad one outside the menu), hanging up, pairing a browser, and a restart the User confirms aloud (the Session Orchestrator's restart tool, which shares the menu's restart). The recovery menu covers GPT-Live being unavailable and is the only route to replacing a Sandbox; a dead Sandbox alone is a voice matter, since the Session Orchestrator runs without it. The Session Orchestrator learns of a keypad restart through a harness event. Menu actions are recorded as Call Activity.

**Not:** The recovery menu outside the Voice Agent, which is reached when realtime voice is unavailable.

### Agent Implementation

**Context:** Agent Hosting

The concrete program or service that plays the Agent role: OpenCode, through its native server API (`opencode serve`, the Sandbox's shared OpenCode service). Its command line, authentication, configuration, and compatibility quirks belong to an adapter. Dogwalk pins its version and upgrades it explicitly.

### Agent Gateway

**Context:** Agent Hosting

The Sandbox process that supervises the shared OpenCode service and proxies authenticated requests and its event stream to Dogwalk. Because it supervises the service, it is the one that observes an Agent Termination. It replaces the ACP Gateway, a stdio bridge that killed the Agent when its WebSocket dropped.

### Agent Connection

**Context:** Agent Integration

Dogwalk's live link to the Agent service through the Agent Gateway: an event subscription plus requests. One Agent service serves all of a User's Agent Sessions, so one connection covers them all.

Agent work does not depend on the connection. Prompt Turns continue across a dropped connection, a deploy, or eviction of the Durable Object; only an Agent Termination or a Sandbox restart interrupts Agent work. On connecting or reconnecting, Dogwalk subscribes to events first and then catches up from snapshots (each session's status and the outcome of its latest Prompt Turn, pending Permission Requests, the latest Agent messages), so a turn that ended while Dogwalk was away is reported when the next Voice Call attaches.

When the Agent service ends, the Agent Gateway reports an Agent Termination reason (`out_of_memory`, `killed` by a signal, or `exited` with a status, plus the memory limit when known) as the close reason of the event subscription. Session Management carries it into the failed Turn Result (including a turn the Agent failed with a bare error moments before), so the User hears why the work stopped, not only that the connection dropped.

**Not:** A voice call, Managed Session, or the Agent's work.

### Agent Session

**Context:** Agent Integration

The Agent's retained, persisted conversation. It has an opaque Session ID, its own context and history, and a working directory.

The Session ID is the Agent's identity for it. It is never a spoken name.

### Co-presence

**Context:** Agent Integration

Other clients, such as an OpenCode TUI in a Terminal opened from a Whiteboard or OpenChamber, may attach to the same Agent service and act on the same Agent Sessions in real time. Dogwalk observes their effects: a Permission Request answered elsewhere stops being presented, and Steering or prompts from elsewhere show up as Activity. Every surface belongs to the same User. No second client may become required: each is a parallel path, never a substitute for the call (see the Accessibility Ethos in `AGENTS.md`).

**Not:** Another User, a Paired Browser, or a Client-Provided Tool.

### Managed Session

**Context:** Session Management

Dogwalk's application record for one Agent Session. It projects Agent events into state useful to interaction surfaces and associates the session with the Agent Connection, Workspace, local Alias, Agent-supplied metadata, Prompt Turns, and pending Attention Requests.

**Maps to:** Exactly one Agent Session while attached.

A Managed Session is rooted in one Workspace directory chosen when it is created, and keeps it for life: loading its Agent Session must reuse that directory, because the Agent binds the session (and the project instructions, skills, and configuration it applies) to it. When the recorded directory is missing or the Agent reports a different one, Session Management repairs the record from the Agent's session listing rather than leaving the session unavailable. The Session Orchestrator roots a Managed Session in the project directory the work concerns, never in home with instructions to work elsewhere; a project that must first be cloned is cloned by one Managed Session and worked on by a new one rooted in the clone.

**Not:** An Agent process, Prompt Turn, or long-running task.

### Alias

**Context:** Session Management and Voice Interaction

A mutable, pronounceable, locally assigned handle by which the Voice Agent and User refer to a Managed Session. The Voice Agent chooses one freely when it creates or loads a session, and changes an existing one only when the User asks or agrees. It names only Aliases that exist: prompt examples use a placeholder, never a plausible name.

An Alias is distinct from both the opaque Agent Session ID and an Agent-supplied Session Title.

Aliases remain attached to live Managed Sessions across voice calls while the
Session Manager remains active. An Agent-discovered persisted Agent Session has no
Alias until Dogwalk loads it into a new Managed Session; loading assigns a fresh
Alias rather than treating its Session Title as a spoken identity.

### Session Title

**Context:** Agent Integration and Session Management

Human-readable session metadata supplied by the Agent, often generated from the conversation. A title describes the work, while an Alias supports compact spoken reference. Changing one does not implicitly change the other.

### Workspace

**Context:** Session Management

The filesystem scope made available to an Agent Session: its working directory, fixed when the session is created.

**Not:** A sandbox, repository, project identity, or process working directory, although those may coincide in a deployment.

### Sandbox

**Context:** Agent Hosting

An optional execution and security boundary around Agents, processes, files, network access, and credentials. Daytona is one possible sandbox provider.

**Not:** An Agent concept or synonym for Workspace.

### Sandbox Assignment

**Context:** Agent Hosting

Dogwalk's association between an authorized User identity and one provider-hosted
Sandbox. It records provider identity and lifecycle projection separately from
Registration: registering does not create or identify a Sandbox. Agent Hosting
may derive a non-reversible provider label from the User, but must not expose a
phone number or Handle to the provider as a sandbox name or label.

The provider ID is a cached reference, not durable identity. If the provider
reports that a Sandbox was deleted, Dogwalk invalidates the reference and may
create a replacement Sandbox for the same User. Registration survives; files,
Agent-held session history, and other state in the deleted Sandbox do not.
Replacement after explicit provider-side deletion is exceptional reconciliation.
Paused and stopped Sandboxes retain identity and durable filesystem state and
are resumed in place, until reaped (see Identity and Hosting Lifecycle).

A reaped assignment keeps its row with no provider ID and the state `reaped`,
so the next call provisions a fresh Sandbox exactly as after a replacement. Any
refresh of an assignment from the provider applies only while the assignment
still names the Sandbox that was read, so a slow read never restores a Sandbox
that was replaced or reaped meanwhile.

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
of observed Voice Frontend, Session Orchestrator, session-management, and Agent
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

An optional second surface for the User: a browser tab joined to one Display, showing only that Display's Whiteboards. It never starts, steers, or authorizes anything; every action stays in the Voice Call. It complements the eyes-free norm for the moments a User wants to read rather than listen, such as reading a Report, thinking, and calling back with feedback, or wants their hands on the Sandbox (see Human Takeover).

Pairing uses a spoken **Pairing Code** and the User's spoken confirmation. On request, the Session Orchestrator creates a fresh code in the Wormhole-style shape `number-word-word`: a numeric prefix from 1 to 999 and two random words. The prefix is a **Nameplate**, unique among outstanding codes, which routes a claim without revealing the words. The Orchestrator has the Voice Frontend speak the returned code clearly for the User to type into `/pair`. No microphone, acoustic decoding, speakerphone, or separate device is required. When a browser claims the code, a `pairing_claimed` harness event wakes the Session Orchestrator, which has the Voice Frontend name the claiming browser and its rough location and ask whether it is the User's; it accepts the browser (`accept_browser`) on a yes and refuses it (`refuse_browser`) on a no. Knowing or speaking a code authorizes nothing; the User's spoken confirmation, acted on by the Voice Agent, accepts a browser. That is a convenience, not a security boundary: a model can complete a pairing, which adds nothing a prompt-injected Agent lacks, since the Agent in the Sandbox already holds `sudo` and unrestricted egress (see `ACCESS_THREAT_MODEL.md`). A Pairing Code works once and must be claimed within ninety seconds; a claim made in time waits for the User's answer until it is accepted or refused, a new code replaces it, or the call ends. Creating a new code replaces the User's previous outstanding code and any claim waiting on it. The code is a rendezvous secret, not an implementation of the Magic Wormhole protocol.

A pairing attempt never ends in silence. If no browser claims the code before its ninety-second expiry, the Session Orchestrator is woken by a `pairing_unclaimed` harness event, tells the User that it expired, and offers a fresh code. Code text is kept out of diagnostic events, Memory, and Workspace files; only its hash and Nameplate are stored for claim verification, apart from unavoidable spoken and model conversation content.

Ending or disconnecting the call expires its outstanding code and any waiting browser claim. A later call cannot approve that claim, including a voice-session reconnection within the same call. Already accepted browser credentials survive as before.

The two words are selected independently from the complete alternating PGP wordlists (three syllables, then two), providing sixteen secret bits; the Nameplate contributes no secret entropy. Claims travel over HTTPS. At most five guesses may target an outstanding code across all browsers and source addresses, and a shared service-wide budget allows 120 claim requests per fixed minute. Exhausting a code's guess budget retires it; the caller may ask for a fresh code. These controls limit online guessing, not denial of service or someone who overhears the code, and the User's confirmation remains necessary even after a correct guess (though, as above, it is not a boundary against someone near enough to hear the code).

Several browsers may be paired at once, each accepted by its own confirmation and each joined to a Display named during pairing (see Display). Accepting a browser never unpairs another. The User may unpair a browser from its page, or a whole Display by voice, which unpairs every browser joined to it and ends its Access Grants.

An accepted browser holds a credential that names its Sandbox Assignment's Voice Session and a secret checked only there, kept in that browser tab's session storage. It outlasts the Voice Call and lapses after an hour without use, twelve hours in all, when the User unpairs it from the page, or when the Sandbox is replaced. Its connection never keeps Agent work or the Sandbox awake.

The page carries a build identity (a hash of its source) and sends it when it connects, so a tab loaded before a deploy is known to be **stale**: it lacks newer features, such as Whiteboard components. A reload keeps the pairing, so a stale page reloads itself once nothing would be lost (no field in use, no half-answered board, no open Access Element, which only a click reopens). A page too old to know its build cannot, so the Session Orchestrator hears `browser_page_stale` with its next woken step (and `paired_browser.page_stale` in inspection; a draw on that page reports it too) and asks the User to reload; a Whiteboard error from a stale page says the page is out of date.

The page has no views of its own: no file browser, Managed Session view, call timeline, or live feed of the call. Anything the User should see is drawn as a Whiteboard; the call's own record stays on the Diagnostics Surface.

**Not:** The Diagnostics Surface (operator-only, all Users and calls), a control surface, a required interface, or a Display (a browser joins one). Hands-on work opened from it is a Human Takeover; answers it collects are Whiteboard Answers. It succeeds the retired Scoped Diagnostic View, with the call itself replacing SMS as the channel that proves the User is present.

### Display

**Context:** Voice Interaction, shown on Paired Browsers

A named place the Session Orchestrator draws Whiteboards, so a User with several screens can keep a report on one and a preview on another. The name is pronounceable and chosen in the pairing ceremony: the Voice Agent proposes one (from the kind of device, or the next of a short list) and the User may say another; it can be renamed by voice. A Display is a channel, not a device: every browser joined to it shows the same board, so a phone and a laptop on the same Display mirror each other, and a browser paired to a Display that still has a browser joined picks up its boards. Names usually name roles rather than hardware: a User giving a talk might pair a projector as "slides" and a laptop as "notes", and ask for the next slide and its notes together. A User may have any number of Displays; each takes its own pairing ceremony, so in practice there are few. A Display ends when its last browser's pairing ends (see Paired Browser for those lifetimes), when the User unpairs it, or when the Sandbox is replaced, and its boards and Access Grants go with it.

The Session Orchestrator draws on a Display by name, and senses every Display without changing it: its name, whether any joined browser has the page open, a summary of what it shows, and its live Access Grants. Drawing on a Display no browser has open is allowed (the board waits there), and the Orchestrator says so rather than implying the User can see it.

**Not:** A Paired Browser (a browser joins a Display), a Whiteboard (a Display shows a sequence of them), or a monitor arrangement Dogwalk knows about.

### Whiteboard

**Context:** Voice Interaction, shown on a Display

A board the Session Orchestrator draws on a Display in a small declarative UI language (openui-lang): a quick diagram, table, chart, comparison, or form, or a set of small decisions such as proposed edits shown as before and after, each to accept or reject. It is the escape hatch for moments that outgrow voice, so the User can keep moving until they reach a full computer.

A Whiteboard can be answered on the page (choices, text, buttons). A **Whiteboard Answer** is caller input, like keypad digits or the answer to an Elicitation: the Session Orchestrator takes it into account and is woken by it, so the call hears in words that it arrived. It is never authorization: it cannot resolve a Permission Request, and the Session Orchestrator confirms consequential answers aloud before acting on them. The User may always answer by voice instead, and that counts the same. The Voice Agent says aloud what it drew, so the call remains complete without the page.

A board may show an image an Agent left in the Workspace (PNG, JPEG, GIF, WebP, or SVG), named by its Workspace path: the program carries the path, never the bytes or a URL. The Sandbox is the only store of Workspace content: Dogwalk keeps a board's program, not copies of the files it shows. A browser reads each image through its pairing when it shows the board, and only while the Sandbox is running; while it sleeps, an image not already loaded shows its description and that a call wakes the Sandbox. A page left open keeps what it has loaded. The image is the file as it is when loaded, so it can differ from what the Voice Agent described if the file has changed since. When the Session Orchestrator draws a board, Dogwalk checks each path (present, inside the Workspace, the image its name says, not too large), and the Orchestrator learns which images failed from the draw itself; a failed image shows its description and the reason, and the rest of the board still works. Images are a presentation of ordinary files, outside Agent Integration; no Agent learns that a board showed one.

A board may likewise show a **Document**, a Workspace file read the same way (rendered Markdown, PDF, audio, video, tables, code, or an HTML report). HTML renders isolated: scripts run at an opaque origin with no network access, so reports meant for a board are self-contained, except that an image whose source is a Workspace path (absolute, or relative to the report) is read with it.

A board may show a **QR code** of text the program writes (`QrCode(text, label)`): a bridge from a Display to a second device, so a User can open a public URL on a phone without typing or hearing it. The page encodes it locally, with no image in the Workspace and no remote service, and shows the label and the text under it for anyone who cannot scan. A QR code is shown, not opened: it is not an Access Element and carries no grant. Anyone who can see the screen can scan it, so its text is never a credential, a Pairing Code, or an Invitation code. The Voice Agent says what the code opens, never the text itself.

The program never carries a credential or a signed URL; it names what it means. Nothing on a board wakes the Sandbox: images and Documents load only while it runs, and Access Elements open only under a grant (see Access Grant). What a User can get from a board after the Sandbox sleeps is what the page already holds; more takes a call.

**Not:** A control surface for Agents, a Permission Request, or a required interface.

### Access Grant

**Context:** Access Control, drawn on a Whiteboard

The authority behind an **Access Element**, a board element that reaches into the running Sandbox: a **Terminal** (a browser shell in the Sandbox), a **Desktop** (the Sandbox's virtual desktop), a **Preview** (a port an Agent's service listens on, such as a development server or a file server it started, shown inline or in a new tab), **SSH Access** (a copyable command or configuration, not a link), a **Download** (a Workspace file read at the moment it is opened), or an **Upload** (a drop zone that writes the User's files into the Sandbox). The program names only the element and its target (`Preview(8080)`, `Terminal()`, `Download("out/report.zip")`, `Upload("talk-slides")`, where `Upload()` means the upload root); Dogwalk holds the authority. Terminal, Desktop and SSH Access have no target; their optional argument is a label, not a session selector.

Drawing an Access Element during a Voice Call creates its Access Grant: scoped to that Display and that target, with an expiry the board shows: an hour unless the User asks for longer ("for the talk", "for the afternoon"), never beyond twelve hours or its Display. SSH Access needs no confirmation beyond the request that drew it. Opening the element from a browser joined to the Display within the grant mints a short-lived credential (a provider-signed link or an SSH token) for that open; the credential never reaches the Voice Agent, the Session Orchestrator's context, Memory, the Workspace, or diagnostic events. A signed link is a bearer capability: anyone holding it can use it until it expires or is revoked; it is not bound to that browser's identity. Nothing on a Whiteboard wakes the Sandbox: opening an Access Element while the Sandbox is stopped shows that it is asleep and that a call wakes it, and the grant is unaffected. An open Access Element keeps a running Sandbox awake, since idle pausing would otherwise end the User's work under them. Credentials are minted fresh on each open, so a Preview's address is not stable across opens or grants. A grant ends at its expiry, when the User revokes it by voice, when its board leaves the few its Display retains, when its Display ends, or when the Sandbox is replaced; a credential minted under it is revoked with it where the provider allows, and otherwise lapses by its own shorter expiry. Ending a grant closes its Terminal and Desktop pages (other Displays' too, which may open theirs again) and stops its links opening, but is not a complete disconnect guarantee: an app already open in a Preview may stay connected, and SSH Access cannot be revoked, so each command connects for minutes and a session already open lasts until it disconnects or the Sandbox restarts. An expired element stays on the board, marked expired; drawing it again in a call creates a new grant.

An Upload is the one write path. Files land only in a folder under the Sandbox's upload root (`~/uploads/`), never in a project directory, where a new `AGENTS.md` or dotfile would be picked up by an Agent or a tool; `~/uploads/` is never a project and never a Managed Session's directory, so Agents copy files out deliberately. Like a Download it mints no credential. Its grant carries caps (25 MB per file, 20 files, 100 MB in all) and accepts files for its whole lifetime, including after the call, but only while the Sandbox runs. A file never overwrites (a taken name gets a numbered copy), never becomes a dotfile, and is written without the execute bit. Each file reaches the Session Orchestrator as a harness event (path, size, declared type, never the bytes), and so does a failed attempt, up to a cap per grant; the last file of a drop wakes it. Revoking the grant refuses new files; one already arriving finishes. The Orchestrator, not the harness, decides whether to tell an Agent (a new prompt, or a Steer when the User was waiting on that session) and says aloud what arrived. No Agent learns that a board delivered a file. An upload is caller input, never authorization, and a convenience: never the only way to get a file into the Workspace.

Drawing is the authorization, and it happens over voice in a call, the same channel that admits the User; a Whiteboard Answer cannot create a grant. What is on the far end is whatever the Agent started: Dogwalk owns the access, not the application.

**Not:** A Permission Request, an image or Document on a board (which is shown, not opened), or a Paired Browser credential (which shows boards and opens nothing by itself).

### Human Takeover

**Context:** Agent Hosting, opened through an Access Grant

The User working in the Sandbox directly, for what Agents cannot do themselves: solving a CAPTCHA, completing a login or consent page, driving a GUI, browsing files, or running a terminal program such as an Agent's own interface (an OpenCode TUI attached to the shared service is co-present; see Co-presence). It is an activity, not a destination: the User opens an Access Element the Session Orchestrator drew, and the takeover lasts as long as its Access Grant. Dogwalk does not build file browsers or other tools for it; an Agent starts whatever the task needs and the Orchestrator draws a Preview of it.

A Terminal opens a shell served from the Sandbox through an expiring, revocable signed link. Each open starts a fresh shell; Dogwalk does not relay terminal traffic or automatically select or reattach a multiplexer session. For work that must survive closing the tab, the User can run tmux and reattach from a later shell. A Desktop is the Sandbox's virtual desktop (with a browser that the User and, later, Agents can share), likewise viewed through a provider-signed link. Neither sends the provider's API key or Sandbox-wide preview token to the browser.

A Human Takeover in use keeps the Sandbox awake, bounded by its Access Grant. It is a deliberate exception to the voice-only control rule: authorization still happens over voice (pairing requires the User's spoken confirmation, and the grant is drawn in a call)
, but the actions taken during a takeover are the User's own, outside Session Management, so Managed Sessions do not see them except through their effects on the Workspace and, from a co-present Agent client, on Agent Sessions.

**Not:** A Managed Session, an Agent Session, or a Prompt Turn. The Session Orchestrator does not operate it.

### Handoff

**Context:** Voice Interaction

The Voice Frontend's request for Session Orchestrator help, carrying the recent call transcript rather than a structured task. A later Handoff may continue, correct, cancel, or be independent of an earlier one; the Orchestrator decides which, preserving unrelated work. Speech or context sent in answer to a Handoff references it. Handoffs have no completion event: the Orchestrator's final speech request is the answer.

A Handoff carries each transcribed word once: the words since the previous Handoff, up to the moment the Voice Frontend asked. The Voice Frontend may ask at a pause in the middle of a request and not ask again for the rest, so what the caller goes on to say after a Handoff, before the Voice Frontend answers and with no newer Handoff, reaches the Orchestrator as that Handoff's **Continuation**, once the caller pauses. A Continuation belongs to its Handoff: it is usually a correction or the rest of the request, and is answered under the same Handoff.

**Not:** A Prompt Turn or an Assignment. One Handoff may start several Prompt Turns or none. A Continuation is not a new Handoff: the Voice Frontend did not ask for it.

### Client-Provided Tool

**Context:** Agent Integration

A tool Dogwalk offers the Agent through OpenCode's client-hosted MCP (an MCP server hosted by the client over a WebSocket), stacked on the Agent Implementation's own tools. This mechanism is experimental in OpenCode. Client-provided tools bind to the attachment that supplied them, not to the Agent Session: a co-present client attaching the same session sees its own tools instead. They are optional affordances an Agent may use when present, never a dependency of its plan.

### Audio Clip

**Context:** Voice Interaction

Waveform audio played into a Voice Call: a Workspace file an Agent produced, played on request, or one of the few ritual tones (the recording beep). It is the eyes-free analogue of showing an image. No event is marked by a sound alone (see Modeling Principles). The caller cannot interrupt a clip and the Voice Frontend does not hear it, so clips are short or announced, and the Session Orchestrator tells the Voice Frontend what was played. Telephone audio is narrowband.

### Voice Recording

**Context:** Voice Interaction

The caller's own audio captured during a Voice Call into a Workspace file, such as a voice note, a hummed melody, or a sound for an Agent to work with. Recording follows voicemail conventions: pound saves, star discards, and a length limit or a long silence also ends it. The Voice Frontend hears silence while recording, so it does not answer the caller mid-note. A saved recording is an ordinary Workspace file; what happens to it next is an Assignment like any other.

### Voice Agent Skill

**Context:** Voice Interaction

Version-controlled guidance the Session Orchestrator loads on demand when a request matches the skill's description (its trigger), such as precise transcription or self-knowledge about Dogwalk. A skill has a body for the Orchestrator and may carry speech guidance applied to the Voice Frontend for the rest of the call. Voice Agent Skills change only through Dogwalk's repository; nothing at runtime writes them. They disclose progressively: the index (name and trigger) is always in the Session Orchestrator's instructions, a body loads when its trigger matches, and a skill's reference files are read one section at a time, since whatever the Orchestrator reads stays in its context. Self-knowledge rests on the project's own documents, bundled as references rather than paraphrased; the Voice Agent cannot inspect the deployment it runs on, so for questions the documents do not answer it has a Managed Session read the public source repository, the code that most likely powers the deployment, and says so.

**Not:** A skill held by an Agent in the Sandbox, which the Agent may create and maintain itself, or a Client-Provided Tool.

### Prompt Turn

**Context:** Agent Integration and Session Management

One complete interaction cycle. It begins when a prompt is admitted to an idle Managed Session and ends when the Agent's work on it ends and does not resume. A Prompt Turn may include many model exchanges, plans, tool calls, Steering, Permission Requests, and Elicitations. Steering joins the running turn rather than starting another.

An Agent execution (OpenCode's unit of running work) is not a Prompt Turn: a Redirect ends one execution and starts the next inside the same turn.

A Prompt Turn ends with an **outcome**:

- `completed`: the Agent finished normally. This does not by itself prove the requested task succeeded.
- `cancelled`: stopped by a User cancel, or by the Agent after a rejected Permission Request.
- `failed`: an Agent error, or an Agent Termination.

A completed or cancelled Prompt Turn does not imply that its Agent Session has closed. Another Prompt Turn may continue the same session context.

**Not:** An Agent Session, an Agent execution, the Agent, or necessarily the whole engineering task.

### Steering

**Context:** Agent Integration and Session Management

User input added to a running Prompt Turn to correct or extend it, at one of two speeds:

- **Steer:** delivered at the Agent's next step boundary. The step already under way (a model call and its tool) finishes first. It suits "also, when you get to it...".
- **Redirect:** the current step is interrupted at once and the Agent resumes immediately with the steering input. It suits "no, stop, do this instead".

Neither starts a new Prompt Turn. Cancelling a Prompt Turn discards Steering not yet delivered.

**Not:** Cancelling a Prompt Turn, or beginning a follow-up Prompt Turn.

### Assignment

**Context:** Voice Interaction

The Voice Agent's plain-language presentation of what the User wants an Agent to do in a Managed Session. An Assignment commonly produces one Prompt Turn but may require follow-up turns.

Assignment is interaction language, not an Agent lifecycle entity.

### Initial Brief

**Context:** Session Management

The first prompt content Dogwalk constructs from the User's Assignment plus role, safety, workspace, and reporting context. It uses neutral session vocabulary and says nothing of voice, phones, or the Voice Agent. Subsequent prompts continue the same Managed Session without recreating the brief unless required by an Agent adapter.

### Activity

**Context:** Session Management and Voice Interaction

A concise, User-safe projection of current Agent events: tool calls (kind, status, locations, timing), the Agent Plan, the Agent's interim remarks (message text between tool calls), and Steering or prompts that arrived from a co-present client. Activity is observable progress, not private reasoning and not a second Agent turn; thought chunks are never projected. Dogwalk keeps Activity structured and turns it into speech or inspection results at the point of use.

### Followed Session

**Context:** Voice Interaction

The one Managed Session, if any, whose Activity the Voice Agent keeps up with during a Voice Call. Beginning a Prompt Turn follows that session; following another replaces it; Stop Following leaves it working unobserved. Following lasts for one Voice Call and survives the followed session's Prompt Turn ending.

Following observes; it never directs the Agent.

**Not:** A session state, a lock, or Attention.

### Progress Beat

**Context:** Voice Interaction

One sentence about a Followed Session's Activity, built deterministically without a model: a change of phase (looking around, changing files, running something), the first change of a turn, a failed step (failures within two seconds of each other, such as parallel tool calls failing together, make one beat carrying the latest streak), a long-running command, a new plan step, an interim remark, or a nearly full context window. Beats within a second and a half of each other make one beat (the weightiest: a full context window or a failure streak long enough to bid, then the Agent's own words, then a step, a first change, or a shorter failure streak, then a phase change; else the latest), and the beat waiting to be spoken is the weightiest since the last narration, so the Agent's own words are not displaced by a phase change that follows them. A failure beat says what the latest failed step was doing when its kind is known. The Agent's own words between tool calls are the richest source of beats; the Sandbox's global OpenCode rules ask it to write one before each tool call or batch, because Dogwalk relays its text and never its tool arguments. A tool of no known kind makes no beat, and neither a tool's own name nor a command's text appears in one: a command is named by its phase (running the tests, installing packages, building, working with Git). A failed step is not a failed turn: its beat says the turn is still going. A step interrupted on purpose (by a Redirect, a cancel, or a rejected Permission Request) is not a failed step: it makes no failure beat and does not count toward a streak. When a Prompt Turn ends or fails, its unfinished tool calls end with it, and no beat about that turn (a failed step, a long-running command) comes after; the turn event speaks instead. An interim remark keeps the Agent's words and tense, quoted, so a plan stays a plan. A beat reaches the Voice Frontend only as a narration, since whatever the Voice Frontend is told about a session it may say aloud: once a burst settles, the weightiest beat waiting is spoken in a quiet moment, spaced well apart (twenty seconds or more), never twice running without the User speaking or a Prompt Turn starting or ending in between (a failure may break that), and dropped when stale. The latest narration is what the Voice Frontend knows of the session, so it answers "what's it doing?" from that at once and hands off for anything fresher. A beat's text is system text; the Voice Frontend restates it briefly in its own words, never reciting it or chaining several.

### Bid

**Context:** Voice Interaction

A Managed Session that is not followed asking for the Voice Agent's attention: a Permission Request, a Prompt Turn ending, or repeated failed tool calls. The Voice Agent mentions a Bid and offers to follow the session; it does not follow it without the User's agreement.

**Not:** An Attention Request. A Bid is a presentation of events; some are Attention Requests and some (a turn ending, a failure streak) are not.

### Report

**Context:** Session Management and Voice Interaction

A concise result projected from Agent messages and the Prompt Turn outcome for the Voice Agent to relay. A Report may state that a turn stopped without establishing success. It must not erase the outcome or failure state from the underlying Turn Result.

### Turn Result

**Context:** Session Management

The durable record of how one Prompt Turn ended, including its outcome, final Agent messages, relevant tool outcomes, usage, and any local failure information. `completed` means the Agent ended normally; it does not by itself prove that the requested engineering result succeeded.

### Attention Request

**Context:** Session Management

A Client-side union used to route something requiring the User's attention. Every Attention Request retains its precise subtype and response semantics.

Current subtypes are Permission Request and Elicitation. They may share delivery infrastructure but are not interchangeable concepts.

### Permission Request

**Context:** Agent Integration

The Agent asking to perform an action on resources, usually through a tool call. The User allows it once, allows it always (for a pattern the Agent suggests), or rejects it.

Rejecting is not cancelling the Prompt Turn. With OpenCode 2.2.7, though, the Agent then ends its current work, so the turn ends `cancelled`. A Permission Request may be answered from a co-present client, and then stops being presented.

Dogwalk does not define a read-only Agent Session mode. Workspace isolation and
write capability belong to Agent Hosting or Sandbox policy; Permission Requests
remain operation-specific authorization rather than a session-wide mode.

### Elicitation

**Context:** Agent Integration

A transient Agent request for structured User information or an out-of-band interaction. Dogwalk preserves the difference among accepting, declining, and cancelling an Elicitation.

Elicitations map to OpenCode session forms. Dogwalk does not answer them yet (see Open Questions).

**Not:** A Permission Request or persistent Session Config Option.

### Session Config Option

**Context:** Agent Integration

Agent-provided, persistent session configuration such as model, agent mode, reasoning level, or autonomy. Dogwalk treats the Agent's offered options as authoritative and degrades gracefully when an option type or category is unsupported.

### Agent Plan

**Context:** Agent Integration and Session Management

An Agent-reported execution strategy whose entries and status can change during a Prompt Turn. It comes from the Agent's todo tool when the Agent uses one, and may simply be absent (OpenCode 2.2.7's code-mode build exposed none in a local probe). Plans are useful input for spoken progress summaries but are not commands issued by the Voice Agent.

## Lifecycles

### Connection Lifecycle

Dogwalk connects to the Agent service through the Agent Gateway, subscribes to its events, catches up from snapshots, uses the connection for all of its Managed Sessions, and eventually disconnects. Losing the connection makes Managed Sessions unavailable to Dogwalk without interrupting Agent work or changing their identity. On reconnecting, Dogwalk again subscribes first and then catches up (see Agent Connection), so each Managed Session's latest turn outcome is reported. An earlier turn that finished during the disconnection may not be.

### Managed Session Lifecycle

```text
creating -> ready -> closing -> closed
               \-> unavailable
```

- **Creating:** Dogwalk is establishing or restoring the Agent Session.
- **Ready:** The Agent Session is established and retained. Prompt Turn activity is modeled separately.
- **Closing:** Dogwalk is ending its attachment, interrupting a running Prompt Turn.
- **Closed:** The session is no longer attached; the Agent Session and its history persist.
- **Unavailable:** The session cannot currently be used because its Agent connection or host failed.

These are Dogwalk projections, not Agent-defined session statuses.

### Prompt Turn Lifecycle

```text
in_progress -> stopped
           \-> failed
```

- **In Progress:** The Agent is working on the turn, across one or more executions.
- **Stopped:** The turn ended with outcome `completed` or `cancelled`.
- **Failed:** The turn ended with outcome `failed`: an Agent error, an Agent Termination, or a local error.

A Permission Request or Elicitation can make an in-progress turn need User attention, and Steering can extend it. Neither creates a new Prompt Turn.

The Voice Agent may describe a ready session as "resting" or an in-progress session as "working." Those are conversational projections, not core lifecycle values.

### Identity and Hosting Lifecycle

Inactivity reaps from the outside in, each layer more slowly:

- **Home:** expires 90 days after its last successful call. The User remains and can still get in by Roaming.
- **Sandbox:** reaped 30 days after its last Voice Call, deleting its files and Agent-held session history. The User's next call gets a fresh Sandbox.
- **User:** reaped 365 days after their last Voice Call, deleting the Handle's binding, Voice Reference, and Home, and then forgetting what Dogwalk kept of them (the Memory, transcripts, and the words in their calls' diagnostic records). Registrations and Invitations stay as provenance.

Reaping is ordinary lifecycle, not failure. Someone reaped, or locked out of their identity, is invited again and registers as a new User.

Sandbox reaping is a daily, bounded pass. A User's last Voice Call is the latest one that reached them (the Sandbox Assignment's creation if there is none); a Sandbox is never reaped while its User has a call in progress or one within the reap age, counting a call from their Home before it is linked to them. Reaping takes the Sandbox out of service at once: atomically with that check, Dogwalk records the Sandbox for deletion and the Sandbox Assignment lets go of it (it becomes reaped, with no Sandbox). From then on nothing reaches the old Sandbox, and the User's next call gets a fresh one by the ordinary path; a reap is never undone. Deleting the old Sandbox at the provider is cleanup, and only the pass does it, in the same pass and later ones, retrying a refused deletion with growing intervals and flagging it for the operator after repeated refusals. A deleted Sandbox stays recorded, so it is never adopted again even if the provider still lists it for a while. The reap age is deployment configuration (30 days by default), and the operator can pause reaping into a dry run that only records candidates; a pause starts no new reaps and defers provider deletion, but never returns a reaped Sandbox to service.

User reaping is the same daily, bounded pass, run before Sandbox reaping. A User's last Voice Call is measured as for a Sandbox (the latest call that reached them, else their Registration), and the same guards apply: a call in progress, one within the reap age, or one from their Home not yet linked to them spares the User. One transaction rechecks that and takes the identity out of service: the Handle enters the same 90-day quarantine as renaming, the Home and Voice Reference are deleted, outstanding Pairing Codes are dropped, any Sandbox still assigned is reaped as the Sandbox pass would reap it (normally none is, since 30 days is shorter than 365, but a paused or stuck Sandbox pass would otherwise leave a live Sandbox labelled for a dead identity), and the User is marked reaped. Nothing reaches the identity afterwards: every lookup skips a reaped User, and a new Registration always mints a fresh identity. Forgetting the User is cleanup after that, in the same pass and later ones until it succeeds: their Voice Session's storage is wiped whole (Memory and its versions, the Orchestrator's context and archives, recent transcripts, preferences, boards), after revoking any access a Paired Browser still holds, and the free text in their calls' diagnostic records (speech, Agent text, session names, directories, error messages) is removed while the timing stays. The operator then cannot review a reaped User's calls, by design. The reap age is deployment configuration, and reaping ships as a dry run that only records candidates; a dry run reaps no one and forgets no one. What survives is Access Control's own provenance: the User row with its Registration and Invitation edges, and the numbers recorded on calls, audits, and redemptions.

A reaped Sandbox is a different Workspace, handled as a replacement is: when the next call brings the fresh Sandbox, the Voice Session drops the old Sandbox's projection (Managed Sessions, pending Attention Requests, the Session Orchestrator's conversation, Whiteboard boards and Board Assets, and Paired Browsers with their Human Takeovers). The Memory survives, because it is the Session Orchestrator's knowledge of the User rather than state in the Sandbox; the Session Orchestrator is told the Sandbox was reaped, so it treats the Memory's sessions and files as gone. The Opening of the User's next call says the sandbox was cleared and starts fresh, in place of the welcome line written about the old Sandbox. That notice belongs to the reap itself, so it is given exactly once, however much later the provider finishes deleting the old Sandbox.

### Attention Lifecycle

An Attention Request is received from the Agent, queued for the appropriate interaction surface, presented to the User, resolved with subtype-correct semantics, and returned to the Agent, or withdrawn when a co-present client resolves it first. Cancelling a Prompt Turn also resolves or cancels its pending requests.

## Operations

| Neutral operation | Meaning |
|---|---|
| Create Managed Session | Establish a new Agent Session, rooted in one existing Workspace directory, and its local projection |
| List Workspace Projects | Read which directories in the Workspace are projects (repositories or directories with Agent instructions), to choose where to root a Managed Session |
| Begin Prompt Turn | Send prompt content to an idle Managed Session |
| Steer Prompt Turn | Add input to a running Prompt Turn, as a Steer (at the next step boundary) or a Redirect (interrupting the current step) |
| Inspect Managed Session | Read projected state without starting another turn |
| List Managed Sessions | Discover sessions currently known to Dogwalk |
| Discover Persisted Sessions | List Agent-held session history without attaching it |
| Load Persisted Session | Attach an Agent-held Agent Session as a new Managed Session with a fresh Alias |
| Set Alias | Change the local pronounceable handle |
| Cancel Prompt Turn | Stop current Agent work while retaining the session |
| Close Managed Session | End Dogwalk's attachment, interrupting a running turn; the Agent Session and its history persist |
| Delete Persisted Session | Remove Agent-held session history when supported |
| Resolve Permission | Select one Agent-supplied permission option |
| Resolve Elicitation | Accept, decline, or cancel requested input |
| Set Session Config Option | Change an Agent-provided session setting |
| Follow Managed Session | Make one session the Followed Session for this Voice Call, replacing any other |
| Stop Following | Leave the Followed Session working unobserved |

These canonical terms name Voice Agent tools as well as core methods. Narration may add metaphor after a tool result, but tool names and arguments remain neutral.

## Explicit Non-Synonyms

- An Agent Implementation is not necessarily one Agent process.
- An Agent Connection is not an Agent Session, and a dropped Agent Connection does not interrupt Agent work.
- An Agent Session is not a Prompt Turn.
- An Agent execution is not a Prompt Turn.
- A Prompt Turn is not necessarily the whole Assignment.
- A completed Prompt Turn does not mean its Managed Session is closed.
- `completed` does not prove engineering success.
- Cancelling a Prompt Turn is not closing a Managed Session.
- Steering is not a new Prompt Turn, and a Redirect is not a cancel.
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
- Client-provided tools bind to an attachment, not to an Agent Session.
- A co-present client is not a required interface.
- Following a session is not directing it, and Stop Following is not closing or cancelling anything.
- A Bid is not an Attention Request.
- A Handle is not a credential, and a Home is not an identity.
- Roaming is not re-homing, and grants no less access than a Home call.
- Passing the Identity Check is not Registration; failing it is not losing the identity.
- A Voice Donation is not Registration and creates no User; donating grants and changes no access.
- Donation audio is not a Voice Reference, and a Donation Session is not a Voice Recording.
- Challenge Practice is not an Identity Check: it grants, withholds, and counts nothing toward access.
- Challenge Practice is not Re-enrollment: a poor level never changes the Voice Reference, and a good one never reinforces it.
- Challenge Practice is not a Voice Donation: its audio is scored in memory and discarded, never kept.
- A shadow Identity Check is not enforcement: it records what it would have done and withholds nothing from a Home call.
- First-call enrollment is not Re-enrollment: it creates a User's first Voice Reference and never replaces one.
- Re-enrollment's check is not a shadow Identity Check: it decides whether the Voice Reference is replaced, even while the Identity Check only reports.
- Saying "someone else" is not naming a Handle: it only asks who is calling.
- The count of completed Home challenges is Access Control state, not Memory; it changes how the ritual is taught, never what it decides.

## Adapter Vocabulary

The following names may appear in adapter configuration and deployment documentation but must not define core domain types:

- **OpenCode:** The Agent Implementation, a chosen dependency spoken to through its native server API, pinned (currently 2.2.7) and upgraded explicitly.
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
- **Twilio:** Current telephony provider. The Identity Check is TwiML: a speech
  `<Gather>` for transcripts and a `<Start><Stream>` fork for challenge audio,
  stopped before the SIP dial. Never `<Record>`, whose media this account served
  without authentication.
- **Cloudflare R2:** Current store of Donation Sessions, in a private bucket
  reached only through the Worker's binding and the operator's own credentials.
  Each round's audio arrives over the same `<Start><Stream>` fork the Identity
  Check uses, never `<Record>`.
- **Speaker verification Worker:** Current Voice Reference scorer: a stateless
  WASM speaker-embedding model in its own Worker (Workers Paid), reached only
  through a Service Binding. It holds no per-User state.

Adapter-specific limitations should be represented as adapter capabilities, adapter behavior, or compatibility notes rather than generalized into Dogwalk's domain model.

## Architectural Consequences

- Voice Agent tools expose neutral Session Management operations directly.
- OpenCode API types stop at the Agent Integration boundary.
- Agent launch commands and process topology belong to Agent Hosting adapters.
- Managed Session state and Prompt Turn state are represented separately.
- Permission Requests and Elicitations may share an Attention queue but retain distinct types and response rules.
- Dogwalk stores Alias separately from Agent Session ID and Agent-supplied Session Title.
- Agent-discovered sessions remain unattached history until explicitly loaded; loading creates a Managed Session and assigns a fresh Alias.
- Speech-safe Activity and Reports are projections of richer Agent state, not replacements for it.

These consequences constrain responsibilities and naming. They do not prescribe a module tree, class hierarchy, persistence technology, or number of source files.

## Historical Language

The following language appears in Git history and is not canonical for production interfaces:

- **Walker:** An early proper name for the Voice Agent, later the Park Theme's. Production code uses Voice Agent; the landing page still draws the walker until its follow-up redesign.
- **Dog:** An early persona for a Managed Session, Agent process, or task, later the Park Theme's. Production code uses the precise concept instead.
- **Pack:** An early spoken label for a set of sessions. Production tools list Managed Sessions.
- **Sic, relay, revive, and call off:** Early Voice Agent tool verbs. Production tools use create, prompt, load, cancel, and close.
- **Working/resting Dog:** Optional narration over Prompt Turn activity and session readiness, not lifecycle state.
- **Mission Control:** Superseded name for the Diagnostics Surface. Production code and docs use Diagnostics.
- **Review Bundle:** Agent-published files for later visual review, delivered by texted link. Retired 2026-11-07 with SMS for a voice-only focus.
- **Ephemeral Service:** A registered Sandbox port reachable through a texted signed preview link. Retired 2026-11-07 with SMS for a voice-only focus.
- **Scoped Diagnostic View:** A texted, per-call read-only caller lens on the Diagnostics Surface. Retired 2026-11-07 with SMS for a voice-only focus.
- **ACP Client, ACP Agent, ACP Session, ACP Gateway, stop reason:** Dogwalk spoke the Agent Client Protocol until 2026-11-11 (issue #28). Production uses Agent Client, Agent, Agent Session, Agent Gateway, and outcome.
- **Theme, Intensity, Runtime Dramaturgy:** Presentations of the session model as a fiction (Park's dog walk, Survey's away teams, Patrol's operatives, and Plain), a register ladder within one, and consulting a fiction's genre sources mid-call. Retired 2026-11-14 for a phase of direct, transparent speech; Git keeps `themes.ts` and the notes behind them.
- **Earcon, Earcon Style:** Short sounds marking events (a turn ending, a Permission Request, a Progress Beat), as FM chimes or spoken labels, with a public soundboard at `/sounds`. Retired 2026-11-14: the call must be complete without sounds, so their information is spoken.
- **Delegated backend:** The September 2026 arrangement where GPT-Live's managed Responses delegation ran session tools. Superseded by the Session Orchestrator under client delegation.

## Open Questions

- Resolved: Dogwalk does not offer queued follow-up prompts. OpenCode runs a queued prompt inside the same execution as the turn before it, so the Session Orchestrator begins a follow-up Prompt Turn once the current one ends, and Steering covers input during a turn.
- Proactive speech about Activity is now limited to the Followed Session's Progress Beats, and attention from other sessions arrives as Bids. Still open: now that no sound marks a beat between narrations, whether the narration cadence should loosen, whether a failure streak is a good enough sign that a session is stuck, and whether the beat thresholds (4 s of quiet, 20 s between narrations) hold up in playtests. Also open: whether the production model's reasoning text (the Agent's reasoning events, counted per turn as `thoughts`) is worth narrating.
- How should the Voice Agent resolve ambiguous spoken "stop" among stopping speech, cancelling one Prompt Turn, closing one Managed Session, stopping all Agent work, and ending the voice call?
- Which parts of Assignment history belong to Dogwalk versus the Agent's retained session context?
- With Themes retired (2026-11-14), what personality remains in plain speech: how much warmth or humor a direct Voice Agent should keep, and whether a Theme could ever return as presentation without making any information depend on it.
- With earcons retired, some events have no spoken trace: a Prompt Turn begun by Steering just after a turn ended, Progress Beats between narrations, and the Sandbox finishing its start when nothing waits on it. Playtests should show whether any of these needs a spoken line.
- The public landing page still presents the architecture through Park's dog-walk scene; a follow-up replaces it with a plain account.

- Should the Session Orchestrator ever act without a live User utterance, for example starting a follow-up Prompt Turn after a failure? That is an authorization question first, and the current answer is no.
- Resolved: the Session Orchestrator learns how Re-enrollment or Challenge Practice ended when the call returns to it (the outcome's name, never a level or score).
- Resolved: the Session Orchestrator's persistent context is compacted voluntarily into its Memory (see Memory), readable on a Paired Browser. Also resolved: a Sandbox replacement or reaping keeps it (see Identity and Hosting Lifecycle), and it has no expiry of its own, living exactly as long as its User (see Memory). Still open: should parts of the Memory also become notes in the Workspace, the way a coworker's notebook would be? If playtests show a stale Memory misleading a User back after a long absence, a rewrite on the first call after a long gap is the proposed remedy, not expiry.
- Snoozing (Session Orchestrator): news carried by a returning call's call_started (invitees, a Challenge Practice outcome) now reaches the User at their first request rather than at connection. Should any of it wake the Orchestrator, and should events whose spoken effect needs no judgment (an unclaimed Pairing Code) be spoken by the harness, as Progress Beats are, instead of waking it?
- When a newer Handoff arrives while work for an older one is in flight, which supersession rules belong in deterministic code rather than Orchestrator judgment?

- Notes: a caller may call in only to leave a note on the Sandbox or to hear notes other sessions left. Notes go through an Agent (a session writes or reads them in a Prompt Turn); where they live, such as a Workspace folder or a long-lived note-keeping session, is undecided.
- Invitations: should a successful Registration by Invitation continue straight into the new User's first call rather than hanging up? Should the Diagnostics Surface draw the invitation graph, and should an operator be able to raise or lower a User's active-Invitation limit?
- Resolved: a spoken Handle plus Identity Challenges lets a User call from any telephone (see Roaming). Also resolved: an inactive User is reaped after 365 days (see Identity and Hosting Lifecycle), and Users per phone are not capped; a Registration cooldown slows one phone minting Users (see Registration). Still open: how many failed Identity Check attempts pause a Handle, and for how long.
- Challenge Practice tells a caller how their voice scores, round after round, so it is also an oracle for tuning an impersonation (a recording or voice converter) before a real Identity Check. It is reached from inside an admitted call, and while Home checks are in shadow mode a spoofed Home number is one (see `ACCESS_THREAT_MODEL.md`, Accepted risks); should practice rounds count toward the same attempt limits once enforcement exists?
- Resolved: reaping a User forgets their Voice Session and strips the words from their calls' diagnostic records (see Identity and Hosting Lifecycle). Still open: should call text expire independently of reaping, for every User (for example after 90 days)? Today a User who keeps calling has every call's words kept in the diagnostic records with no retention period. And when the Identity Check's enforcement makes an abandoned Handle a more useful impersonation target, should the User reap age shorten (180 days is the defensible shorter choice)?
- Should the Identity Check ever collect more voice evidence during the conversation, or does whole-call collection need its own consent and retention decision?
- Shadow Identity Check: what evidence (genuine and impostor levels across conditions, wrong-phrase and thin-speech rates, `wait_ms`) is enough to turn on enforcement, and does enforcement start with Home calls before Roaming exists? The registration prompt accepts a Handle only while Roaming admission is on; should it also do so in shadow mode, so a User calling from a phone that is not a Home can be challenged without being admitted? Should Roaming challenges against a User count toward that User's taught-once count, or Challenge Practice rounds toward it? Registration's enrollment does not yet create the new Sandbox while it runs, and does not use the spoken Invite Code as speech.
- The name path trades security for smoothness by a tunable window. In shadow mode it opens nothing new: every Home call connects whatever its score, and spoofing caller ID already skips the voice evidence. Once enforcement exists, its weaker evidence matters: a Handle is short and public to anyone the User has told it, and even with the carrier phrase ("this is anchor antelope") an answer is near the verifier's own speech floor (1 s measured); a bare Handle often falls below it and yields no score at all. What should an enforcing name path do with an unscored or thin answer (admit, or fall back to a sentence)? Should the window count only calls whose check admitted, since today any call attributed to the User renews it, including one placed with a spoofed caller ID? Name-path records carry `mode: "name"` so playtests can compare their cosines and speech seconds with sentences before deciding.
- Resolved: Re-enrollment is reachable after Roaming admission (its own three challenges are stronger than a shadow Home check), a Voice Reference changes at most once a day, and a different voice is a Voice Transfer the current voice confirms. A User whose voice no longer matches from any setting, with nobody whose voice does to agree, recovers through the operator (see Voice Transfer). Still open: should failed Re-enrollment checks count toward a Handle's attempt limits, since, like Challenge Practice, each one tells the caller a level? And a verifier whose new embedding space cannot compare with the old reference still leaves every User to the operator.
- Roaming admission: what evidence turns it on (it rests on the same small sample as the threshold: no human impostors or clone test yet)? Should several rounds combine into one decision (a mean or a majority over more, cheaper rounds) instead of every round passing on its own? Should Roaming speak a coarser level than Home, or none, since each level is feedback for tuning an impersonation? Are three failures per Handle and five per number per day right, given that pausing a Handle is also a way for anyone to keep its User from Roaming for a day, and that one attempt at a time lets a caller who holds a call open keep the User from Roaming for its length? A User who only roams never hears the Roaming notice, which waits for a call not admitted by Roaming; should it reach them some other way? Re-homing after a Roaming admission (explicit, spoken, confirmed) is not built.
- Access (see `ACCESS_THREAT_MODEL.md`): which durable-access operations (pairing, Invitations, Handle rename, Re-enrollment, credentials an Agent writes) can be separated from ordinary Sandbox work? Should a Paired Browser or an Access Grant need a matched Identity Check, or should a grant end when its call ends? Should the User have a spoken way to end a group's access short of hanging up? Should accounting gain per-User caps (a call time limit, an inference budget per Sandbox, a User in `audit_log`)?
- Voice Donations: how long is donated audio kept? Today it stays until the operator deletes it on request or decides otherwise; a fixed retention period, and whether the consent script should state it, is undecided. Should a donor be able to delete their own session by voice without an identifier being spoken to them?
- Is there a limit on how many Managed Sessions the Voice Agent attends at once, and who decides when to let one go?

- Should the Voice Frontend also receive recent conversation directly (as provider session history at call start), rather than only through the Session Orchestrator's reorienting speech?

- Pound now means stop talking in conversation, so a caller who presses pound to end a recording that the Voice Frontend only claimed to start no longer signals the Session Orchestrator. Should a pound press shortly after recording talk also reach it?
- Should a System Menu restart wait for or inform in-flight Prompt Turns beyond the harness event, and should a restart be refused while a Permission Request is pending?
- The Opening's welcome line is written when a call ends; how stale may it become (for example after days, or after work that changed what is unfinished) before Dogwalk prefers the neutral fallback?
- A Managed Session's directory is fixed at creation, so work spanning two projects needs two Managed Sessions or a shared parent directory. Should Create Managed Session accept additional directories if the Agent supports them?
- Is a project an Agent Hosting fact (a directory found in the Sandbox) or a Session Management concept with its own identity, for example so a Managed Session survives a clone being moved or re-cloned?
- Now that the Identity Check, not caller ID, establishes identity, should the Opening's STIR/SHAKEN attestation remain, or is it a diagnostic to remove once attestation is understood across carriers?

- Elicitations: how does an OpenCode session form reach the voice surface? Until Dogwalk answers forms, an unanswered form leaves its Prompt Turn waiting.
- Co-presence: should the Session Orchestrator be told about activity that originated from a co-present client, and how should a turn that ended between calls be reported beyond the next call's Opening?

- Displays: should the Session Orchestrator learn which Documents or Access Elements the User opened, so a callback can start from what they read? (Resolved: any number of Displays, each ending with its last pairing; unpairing a Display is possible by voice.)
- Whiteboard images and Documents are read when shown, only while the Sandbox runs (resolved: Board Assets, draw-time snapshots kept by Dogwalk, are retired; the Sandbox is the only store of Workspace content; a board does not watch for files changing after it is drawn). Export or sharing of a board with its images is not supported.
- QR codes carry only literal text the program writes (#64). Should an Access Element gain a QR form, for opening a Preview, a Download, or an Upload on a phone? One sketch is a Handoff Link: a short Dogwalk URL shown only after a click, usable once, for a few minutes, and only within its grant, which on use mints that kind's credential (a provider link to redirect to, a file, an upload page). It would let a device outside the pairing in, and anyone who can see the screen could scan it first, so it needs `ACCESS_THREAT_MODEL.md` reopened before it is built. For an Upload no QR form is needed: a phone paired to the same Display (Paired Browser) mirrors its boards and gets the drop zone and file picker under the same grant, with no credential leaving the pairing.
- Access Grants (resolved: an hour by default, up to twelve on request; no Whiteboard activity wakes the Sandbox; SSH Access needs no extra confirmation; Preview addresses are not stable). Should an open Access Element keep the Sandbox awake for the whole grant, or should a forgotten tab let it stop after some idle time? Should an Upload grant, the one write path, end with the call, so no file arrives unheard between calls (currently accepted; see `ACCESS_THREAT_MODEL.md`)?
- Human Takeover: should the Session Orchestrator learn that a takeover happened (and hand off to it, "the desktop is ready for you"), or summon one ("a session is stuck on a CAPTCHA; open the desktop on your laptop")? Should Agents drive the same desktop through the provider's computer-use API, so human and Agent take turns on one screen? Should a Terminal idle out before its grant ends?

## Agent References

- [OpenCode server API](https://opencode.ai/docs/api/)
- [OpenCode OpenAPI spec](https://opencode.ai/docs/openapi.json)
- [OpenCode permissions](https://opencode.ai/docs/permissions/)
- [Decision to speak OpenCode's API directly (issue #28)](https://github.com/rndmcnlly/dogwalk/issues/28)

### Historical: ACP

Dogwalk spoke the Agent Client Protocol until issue #28; its vocabulary (Prompt Turn, Permission Request, Elicitation) came from there.

- [Architecture](https://agentclientprotocol.com/get-started/architecture)
- [Initialization and capabilities](https://agentclientprotocol.com/protocol/v1/initialization)
- [Session setup](https://agentclientprotocol.com/protocol/v1/session-setup)
- [Prompt turns](https://agentclientprotocol.com/protocol/v1/prompt-turn)
- [Session list and metadata](https://agentclientprotocol.com/protocol/v1/session-list)
- [Tool calls and permissions](https://agentclientprotocol.com/protocol/v1/tool-calls)
- [Session config options](https://agentclientprotocol.com/protocol/v1/session-config-options)
- [Agent plans](https://agentclientprotocol.com/protocol/v1/agent-plan)
- [Elicitation RFD](https://agentclientprotocol.com/rfds/elicitation)
