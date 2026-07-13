# dogwalk

*A pitch, not a plan. Written in a research session to be picked up cold later.*

## The premise

Do real software work while away from the keyboard, hands-free and eyes-free.
The motivating scene: Adam on the exercise bike in the garage, phone screen
locked, bluetooth headphones in. He loads `https://dogwalk.adamsmith.as`, starts
an audio session, and just talks. He asks about projects, reviews git history,
proposes changes, kicks off work, and hears back about the results, all in plain
language, never speaking a technical identifier precisely.

This is the thing Simon Willison keeps gesturing at: coding by voice while
walking the dog, treating the model as a fast intern you supervise by ear, where
verification (tests pass, artifact runs) replaces line-by-line review as the
supervision mechanism. Adam has no dog. The dog is beside the point. The point
is hands-free supervision of real engineering work.

- Simon's lived proof-of-concept: <https://fedi.simonwillison.net/@simon/112147026040154264>
  (context: <https://simonwillison.net/2024/Sep/20/using-llms-for-code/>)

## The roles (this is the load-bearing idea)

Three entities, with a deliberately **inverted competence gradient**.

| Role | Is | Runs where | Speaks |
|------|-----|-----------|--------|
| **Adam** | the human, hands-free, imprecise on purpose | garage bike | plain language |
| **Walker** | voice conversationalist, engineering-*weak*, self-aware about it | gpt-realtime (cloud) | voice to Adam; ACP client to Dogs |
| **Dog(s)** | strong coding harness (opencode, Claude Code, ...) | subprocess in the sandbox | ACP agent over stdio |
| the sandbox | Daytona VM, hosts the pack + files + `~/.dogwalk` | cloud | n/a |

**Walker** is the one out on the walk holding the leash and the conversation.
Great company, reads the room, translates intent. It does **not** speak code
aloud and does **not** make engineering judgments. It negotiates and relays.

**Dog** is the strongest LLM in the system, and that is the joke: raw capability
in the role that needs *external guidance and restraint*. A Dog goes off
sniffing, follows its own interests, is brilliant at the labor, but you never let
it off-leash. Each Dog is a real coding-harness process inside the sandbox,
driven over ACP. The pack is literal: N subprocesses, each on its own leash (its
own ACP session), each scoped to a project directory (`cwd`).

**The leash is the safety model, not just a metaphor.** Conventional
orchestration puts the smartest model on top as manager. dogwalk inverts it: the
weaker, socially-fluent model is the interface *because* it is cheap enough to
run at low latency in a realtime voice loop, and *safe* because its very
limitation (no engineering judgment) forces every consequential decision to route
through Adam. The Walker cannot go rogue on architecture because it genuinely
does not know enough to. The Dog *could*, which is exactly why it never talks to
Adam directly and never acts without a relayed instruction.

### Vocabulary

- **Relay** is the verb. The Walker *relays* intent down to a Dog and *relays*
  results back up to Adam. Not "delegate" (no false hierarchy: the Dog is
  smarter), not "command."
- **Sic** a Dog: spawn a fresh coding-harness subprocess and give it a task.
- Dogs are **ephemeral per-task**: sicced fresh for a job, work, report, released.
- Dogs get **pronounceable names coined on the fly**, riffing on Adam's own
  language for the task (a scout became "Ranger", a narrow bug-fix became
  "Fixer"). The Walker may seed from a few example names but should name dynamic
  Dogs playfully from context.
- A **pack of zero or more Dogs**; usually one at a time for conversational
  clarity, but bust past that when it helps. The Walker may wink at how far the
  metaphor is stretching.

## Architecture

### The three borrowed technologies

1. **gpt-realtime** (OpenAI Realtime API) is the Walker's voice: a stateful
   speech-to-speech session with server-side VAD, barge-in, sub-second latency,
   and **function calling mid-turn**. The function-call seam is where the Walker
   dispatches work.
   - <https://developers.openai.com/api/docs/models/gpt-realtime>
   - <https://developers.openai.com/api/docs/guides/realtime>
   - <https://developers.openai.com/api/docs/guides/realtime-conversations>

2. **Agent Client Protocol (ACP)** is how the Walker drives Dogs: JSON-RPC 2.0,
   modeled on LSP, decoupling coding *agents* from the *clients* that host them.
   The Walker implements the **client** role; each Dog is an ACP **agent**
   subprocess. opencode speaks ACP (`opencode acp`), as do Claude Code, Codex
   CLI, Gemini CLI, and others. Multi-agent orchestrators built exactly this way
   already exist (Jockey, AgentPool). dogwalk reimplements none of the
   engineering loop; it *spawns* mature ones and multiplexes them.
   - <https://agentclientprotocol.com>
   - opencode's server: <https://opencode.ai/docs/acp/>

3. **Daytona** is the sandbox: a full composable computer (kernel, fs, network,
   resources) spun up in <90ms, driven by SDK/REST/CLI, hosting many concurrent
   processes (the pack) and the project files. Woken on session start.
   - <https://www.daytona.io/docs/en/>
   - <https://www.daytona.io/docs/en/architecture/>

### The ACP/realtime seam (the one real wrinkle)

ACP's stable transport is **stdio to a local subprocess**; remote HTTP/WebSocket
transport is still WIP. So the ACP client must live *close to* the Dogs, i.e.
inside or adjacent to the sandbox, **not** up in the gpt-realtime cloud session.
That splits the Walker into two pieces:

- **Walker-voice**: the gpt-realtime session (Adam's phone <-> OpenAI).
- **Walker-hands**: a server-side process (the realtime "sideband") that holds
  the ACP client, sics Dogs in the sandbox, and exposes `relay_to_dog` /
  `check_dog` / `sic_dog` / `call_off_dog` as realtime **function tools**.

The gpt-realtime function-call mechanism connects the two: Walker-voice emits a
`function_call`, Walker-hands executes it as ACP traffic, results stream back up
as `function_call_output`. This is the sideband architecture the realtime docs
describe, now with a concrete job. Keeping tool defs / API keys / Dog wiring
server-side (never on the device) is a bonus of the sideband pattern.

### ACP events map cleanly to conversational beats

The Walker narrates a Dog's work by translating ACP notifications into speech:

| ACP | Walker says (roughly) |
|-----|----------------------|
| `session/new` + `session/prompt` | "Siccing a Dog on it, I'll call it ..." |
| `session/update` (progress) | "It's working... here's the shape of it" |
| `session/request_permission` | "The Dog's asking permission before it does X. That's your call." |
| `usage_update` | (track cost/tokens; surface only if asked) |
| `stopReason` | "Dog's done / off duty." |
| `session/cancel` | "Calling it off." |

### Onboarding

On session start the sandbox wakes and the Walker receives an onboarding message
assembled from files in `~/.dogwalk` (project inventory, how Adam refers to
things, house rules). This is what lets the Walker resolve plain-language
reference ("the maps thing", "the distance thing") to a real project by
*purpose*, then confirm, without Adam ever speaking a filename. Latency of the
cold start is masked by the Walker talking immediately and folding the
onboarding in mid-conversation when it lands.

## What the design commits to (stress-test these)

1. The Walker **never speaks code** and **never makes an engineering judgment**.
   Every consequential choice (add a dependency or not, save work or not, keep
   going or stop) bounces to Adam, framed as *shape and consequence*, not code.
2. **Reference by purpose, not identifier.** Plain-language handles resolved via
   onboarding knowledge plus confirmation. No filenames spoken aloud.
3. The engineering intelligence is **entirely borrowed** (opencode/ACP). dogwalk's
   own code is: a realtime voice front-end + an ACP client multiplexer + Daytona
   lifecycle + onboarding assembly.

## Open gaps (what a build session must confront)

- **Long Dog tasks over voice.** Every transcript beat so far kept tasks short
  enough to hold a single voice turn. A multi-minute Dog needs: preamble to mask
  latency, then either hold-the-turn, or "I'll get back to you" + an outbound
  notification when `stopReason` arrives. Design the async-then-callback shape.
- **Realtime session persistence across a long ride.** gpt-realtime sessions are
  stateful but **not** persistent; a dropped socket loses state. Need reconnect +
  `session.update` re-send + context rehydration from an owned store.
- **"Loose vs saved" <-> git state.** The fiction used "loose / unsaved / saved";
  pin the precise mapping to working tree / staged / committed, and how the Walker
  describes it without git vocabulary.
- **How the Walker knows what "the bug" was.** In the fiction it stayed honest
  ("I don't know what the bug was"). Decide how much project memory lives in
  `~/.dogwalk` vs. must be re-derived by a Dog each session.
- **Pack concurrency in speech.** One-at-a-time is conversationally clean; decide
  how to narrate two Dogs working at once without confusing Adam.
- **ACP fidelity varies by harness/version.** opencode's ACP server historically
  flagged gaps (streaming updates, full tool-call reporting, session
  persistence). Verify against the actual build before relying on any event.
- **Security.** ACP assumes a *trusted* agent (client hands over fs/terminal/MCP
  access). The Daytona sandbox boundary is doing real security work, not just
  tidiness. Blast radius = the sandbox.

## Inference provider

For now, OpenAI for both the Walker (gpt-realtime) and the Dogs (whatever model
the coding harness runs). Open-weight substitution is a later concern; the ACP
and realtime seams are provider-agnostic in principle.

## Appendix: a transcript (design fiction)

*Adam on the bike, screen locked, headphones in. Session opens with a chime.*

**Walker:** Morning, Adam. I'm here. Give me a beat, the workspace is still
stretching and yawning, machine's coming up from cold. While it does: what's on
your mind for this ride? You can just talk at me, I'll sort out the details.

**Adam:** yeah uh I want to look at that project from last week, the one with the
maps. the distance thing.

**Walker:** Maps, distances. Okay. I don't have the workspace open yet so I'm
going from memory of how you talk about things, not from the actual files. Hang
on... there it is, the file cabinet just rolled in. Onboarding's loading.
*(beat)* Got it. There are two things that could be "the maps thing." One's a
little service that computes distances between coordinates, last touched
Thursday. The other's older, a mapping UI you haven't opened in a month. You said
"the distance thing," so I'm betting the first one. Sound right?

**Adam:** yeah that one. what's the state of it.

**Walker:** Let me send a Dog in to look. I'm not going to eyeball this myself,
I'd get it wrong. Spinning one up now, pointing it at that project... I'll call
this one **Ranger**, it's just going to scout, not touch anything. *(pause)*
Ranger's back. Here's the shape of it, in plain terms: the project's in decent
order. There's work committed and saved up through Thursday afternoon. But
there's a batch of changes sitting on top that were never saved, they're just...
loose. Ranger says it's mostly one file, the part that does the actual distance
math, and it looks half-finished. Like you stopped mid-thought.

**Adam:** right right. the haversine thing. was I in the middle of fixing the bug?

**Walker:** I don't know what the bug was, that's past my depth, but I can have
Ranger check whether the tests pass right now, which'll tell us if it's broken or
just unfinished. Want me to?

**Adam:** yeah do that.

**Walker:** Sending it. ... Okay. Ranger ran the tests. Most pass. One fails, and
it's the one about distances near the poles, the far north-south edges. So: your
loose changes didn't break the working stuff, but the thing you were fixing is
still broken. You were in the middle of it, like you thought.

**Adam:** ok. can you have it try to fix that one failing test. but don't let it
go crazy rewriting everything.

**Walker:** Good, because left off the leash a Dog absolutely will rewrite
everything, that's the whole reason I hold the leash. I'll give it a tight
instruction: fix only the failing polar-distance case, smallest change that
works, don't touch anything that's already passing. Fresh Dog for this since
Ranger was just a scout, I'll call this one **Fixer**. Turning it loose now,
narrow scope. *(work; a soft tone)* Fixer's asking permission before it does
something. It wants to add a small dependency, a math library, to handle the edge
case. That's a real choice and it's yours, not mine. In plain terms: it can
either pull in an outside tool, or write the fix by hand with what's already
there. Outside tool is faster and probably more correct; by-hand keeps the
project lean with nothing new added. Which way?

**Adam:** ugh. no new dependencies. do it by hand.

**Walker:** By hand, no new dependencies. Telling Fixer. ... It's working. It
rewrote the polar case using what's already in the project. Reran the tests. All
green now, including the one that was failing. It made one change to one file,
and it left everything else alone, like I asked. Those changes are still loose,
though, not saved. Want me to have it save the work, or leave it loose so you can
look at it properly on a real screen later?

**Adam:** leave it loose. but write me a note about what it did so I remember.

**Walker:** Leaving it unsaved. And I'll have Fixer drop a short note in the
project, plain English, saying what changed and why, so future-you isn't
confused. ... Done, note's written. Fixer's off duty. That's the pack empty
again, no Dogs running.

**Adam:** cool. anything else look scary in there?

**Walker:** Nothing screaming at me. The onboarding flagged one thing worth a
sentence: there's another project with unsaved work sitting around from a while
back, so it's not just this one. But nothing's on fire. You want me to send a Dog
sniffing through the other projects for anything half-finished, or is that a
rabbit hole for another day?

**Adam:** another day. I'm almost done pedaling anyway.

**Walker:** Then let's leave it clean. Quick recap for your ears: the maps
distance project had a broken polar-distance case, we fixed just that one thing
by hand with no new dependencies, tests are all passing now, the work's left
unsaved on purpose, and there's a note explaining it. Everything else untouched.
When you're back at a real keyboard, you review the loose changes and decide
whether to keep them.

**Adam:** perfect. bye.

**Walker:** Nice ride. I'll shut the workspace down so it's not burning time
idle. Later, Adam.

*(chime; session closes)*
