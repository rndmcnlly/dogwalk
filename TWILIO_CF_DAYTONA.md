# Twilio + Cloudflare + Daytona spike

Status: Registration, Daytona create/wake, the status menu, and read-only Mission
Control are implemented and deployed. Signed preview SMS and Cloudflare Access
remain deferred; Mission Control temporarily uses single-operator HTTP Basic
authentication. This slice cuts OpenAI and OpenCode entirely. The phone UX is
classical TwiML IVR, speech-gathered for hands-free use. Daytona proves two-way
communication with a sandbox, but does not yet host a Dog.

The implemented access model is simpler than the original fiction: a normalized
phone number is the identity, Registration contains no personal or hosting
metadata, and Invite Codes independently support optional expiration and use
limits. Sandbox association belongs to Agent Hosting and is deferred.

## Goal

Prove the Twilio <-> Cloudflare Worker <-> Daytona wiring end to end with zero
LLM cost and zero ACP/stdio-shim risk. A caller phones in, claims an invite,
gets their sandbox woken, and can ask trivial queries of it (uptime, memory,
disk). An admin web generates invite codes and watches the kennel.

## What is cut

- OpenAI Realtime (no audio stream, no WebSocket to a model)
- OpenCode and `opencode acp` (no ACP, no stdio<->WebSocket shim)
- The per-call Durable Object holding three sockets (nothing to hold)
- The bespoke `/manifest`, `/tool/:name`, `/events` protocol

## What is in

- Twilio `<Gather speech>` IVR, hands-free throughout (car Bluetooth in mind)
- PGP word list for invite codes (3 words, 24 bits), OOB verbal delivery
- D1 registrations keyed only by phone number, plus capability-style invite codes
- Claim ritual: caller speaks 3 words, 3 attempts then hang up
- Cold-start TwiML loop: "waking your workspace" + `<Redirect>`
- Trivial query surface via Daytona toolbox API (run a command, parse, `<Say>`)
- Signed preview URL SMS for per-user web peek at the sandbox
- Cloudflare Access admin web (Mission Control): codes, sandboxes, audit log

## Architecture

```text
Phone (car Bluetooth) -> Twilio -> CF Worker /voice  (D1 registration lookup)
                                  |
                                   +- unregistered number -> TwiML registration ritual
                                   +- registered, cold     -> TwiML "waking" + Redirect
                                   +- registered, warm     -> TwiML <Gather speech> menu
                                                         |
                                          Worker -> Daytona API
                                             |- start/create sandbox by label
                                             |- toolbox exec: uptime, free, df
                                             |- mint signed preview URL, SMS it

Admin:  CF Access -> /admin/*  -> Mission Control (reads D1, mints codes)
User:   SMS signed-preview link -> scoped peek at own sandbox
```

## Design-fiction call transcript

Three calls exercise the whole ritual. TwiML each step would emit is in
backticks. Word choices are load-bearing: every prompt is short, speakable, and
interruptible for a driver who knows what they want.

### Call 1: the claim (unknown number, first contact)

Adam has told Kathleen verbally: "call the number and say the three words I
gave you." She calls from her cell, hands-free in the car.

```
W (TTS):   "Dogwalk. Say your invite words."
K:         "aardvank mystic compass"           <- <Gather speech hints="...512 PGP words...">
W:         "I heard: aardvark, mystic, compass. Say yes to confirm, or again to retry."
K:         "yes"
W:         "Registered. This phone number can now use Dogwalk. Goodbye."
```

Notes:
- The invite code is *not* bound to a number at mint time. Registration records
  the caller ID Twilio already delivered in the webhook. Codes independently
  support optional expiration and optional use limits. Asking the caller to speak digits would invent the
  worst-case ASR task (digit strings in car noise) for zero information gain.
- The read-back-and-confirm loop is the PGPfone ritual with a machine endpoint.
  A misheard byte surfaces as a different word, not silence; the 2-3-2 syllable
  rhythm is the caller's own error detector.
- 3 failed confirmations -> hang up with "try again later." (Retry policy
  locked: 3 attempts.)
- Twilio accepts at most 500 `hints` phrases on `<Gather>`; generated codes must
  remain recognizable within that provider constraint.

### Call 2: cold start (registered number, sandbox absent)

The caller calls back. D1 has the phone number, with no sandbox running.

```
W:   "Welcome back. Waking your workspace, one moment."
       <- <Say> + <Pause length="10"/> + <Redirect>/voice</Redirect>
       (Worker kicks off Daytona create-or-start by phone identity, async)
W:   "Welcome back. Waking your workspace, one moment."   <- redirect lands, still cold
       <- <Pause> + <Redirect> again
W:   "Workspace is awake. Say status, web, or hang up."
       <- warm now; <Gather speech hints="status,web,hang up">
```

Notes:
- Classic TwiML polling loop, exactly as PSTN_CLOUDFLARE spike #2 describes.
  No WebSocket, no DO; just `<Redirect>` back to `/voice` until D1 says warm.
- Cap the redirect count (say 6 = ~60s) -> "workspace is taking longer than
  expected, we'll text you. Goodbye." and fire the signed URL by SMS when ready.
- The polling loop validates the sandbox is actually reachable before the menu
  opens, so the caller never lands on a dead kennel.

### Call 3: warm menu (query + web access)

The caller calls, with the sandbox warm.

```
W:   "Welcome back. Say status, web, or hang up."
K:   "status"
W:   "Your sandbox has been up two hours, fourteen minutes. Memory is thirty
     percent used. Disk is twelve percent used. Say status, web, or hang up."
K:   "web"
W:   "Sending a link to your phone now. It expires in an hour. Say status or
     hang up."
       <- Worker mints Daytona signed preview URL (24h cap), Twilio <Sms> to caller
```

Notes:
- "status" hits the Daytona toolbox exec API: run
  `uptime; free -m | grep Mem; df -h /`, parse, `<Say>`. Trivial two-way proof.
- "web" is the ObserverTokens concept promoted: a short-lived signed preview URL
  SMS'd to the caller ID. No app, no login, just a peek.
- Menu is depth over breadth: 3 speakable options max per node, no "press or
  say" dual language. Speech only, because hands-free.

## Suggested spikes (in order)

Each is independently demonstrable. None requires a car; a quiet room and a cell
phone suffice.

1. **Worker `/voice` + D1 registrations + registration ritual (no Daytona).**
   Stand up the Worker, the D1 schema, and the PGP word list as an embedded
   constant. Hardcode one invite code in D1. Drive the whole Call 1 transcript
   by phoning in. Proves TwiML `<Gather speech>` with PGP `hints`, the
   read-back-and-confirm loop, the 3-attempt cap, and number binding. Daytona
   is not touched.

2. **Cold-start TwiML loop + Daytona create-by-phone-identity.**
   Add the warm/cold branch. On a cold known number, the Worker calls Daytona
   to create-or-start a sandbox keyed from the phone number, then `<Redirect>`-polls until
   the sandbox answers a reachability probe (a trivial toolbox `uptime`).
   Proves the polling loop and that the Worker can drive Daytona lifecycle from
   a webhook. No menu yet.

3. **Warm menu + toolbox query + signed-preview SMS.**
   The Call 3 transcript. `<Gather speech>` menu of three options. "status"
   runs `uptime; free -m; df -h` via Daytona toolbox exec, parses, `<Say>`s it
   back. "web" mints a Daytona signed preview URL and `<Sms>`s it to the caller
   ID. Proves two-way comms with the sandbox and the per-user web-access
   ritual. This is the "plumbing demo" payoff: clear evidence the Worker can
   ask the sandbox something and hear back.

4. **Mission Control admin web behind Cloudflare Access.**
   Workers Static Assets serving a small admin app. `/admin/*` gated by CF
   Access (Google SSO). Reads D1 (registrations, audit log), mints invite codes (3
   random PGP words, displayed as text for the admin to read verbally),
   surfaces per-sandbox state. Proves the admin tier. No per-user web app yet,
   just the operator's view.

Spike 1 is the load-bearing one: if `<Gather speech>` + PGP `hints` misbehaves
in real Twilio ASR, the whole hands-free claim design needs to change before
anything else is worth building. Spike it first, in isolation.

## Deliberate changes from the original plan

Spike 1 established several decisions that supersede the earlier design
fiction:

- A normalized phone number is the User identity at the PSTN boundary. Dogwalk
  stores no personal name and does not depend on caller-name lookup.
- Registration is distinct from hosting. Access Control stores only the phone
  number, authorizing Invite Code, and timestamps. Sandbox identity and lifecycle
  belong to Agent Hosting rather than the registration record.
- Invite Codes are reusable capabilities with independent optional limits.
  `expires_at = NULL` means no expiration; `max_uses = NULL` means unlimited
  registrations. A phone number may register only once.
- Invite Codes are maintained directly in D1 for this friends-and-family spike.
  Mission Control observes registrations and sandboxes but does not mint codes
  or delegate invitation authority.
- Spoken codes are accepted only by exact normalized phrase match. The readback
  ritual handles ASR uncertainty; edit-distance matching does not weaken the
  bearer capability.
- Twilio permits at most 500 speech-hint phrases. The Worker hints currently
  usable Invite Code phrases instead of attempting to send the entire 512-word
  PGP vocabulary.
- Registration ends by confirming access. It does not promise that a workspace
  has already been reserved or provisioned; sandbox creation begins on the next
  registered call.
- Audit events retain operational outcomes but omit raw invite speech and Invite
  Code values. Phone-number and Call SID retention remains an explicit follow-up
  policy decision.
- Sandbox Assignment is a separate Agent Hosting projection. Daytona receives an
  HMAC-derived identity label rather than a phone number, and D1 stores the
  resulting provider ID for idempotent wake-up and recovery.
- The implemented warm menu offers `status` and `hang up`. Signed preview links,
  SMS delivery, and the `web` menu command remain deferred.
- Mission Control is read-only and does not mint Invite Codes. Until Cloudflare
  Access is configured, both the custom domain and alternate Worker hostname
  require the same generated HTTP Basic credential.
- Mission Control treats each live Voice Call as a correlation boundary and
  streams its ordered activity over SSE. Activity envelopes carry source and
  direction so voice, registration, hosting, menu, and future ACP traffic can
  share one timeline. Mission Control defaults to a demo-safe projection that
  masks identifiers and details; a session-only checkbox requests verbose
  telemetry. Large ACP payloads should be referenced rather than copied inline.
- Twilio posts terminal call lifecycle events to `/voice/status`. Mission Control
  removes completed calls immediately and also ignores inactive unterminated
  calls after five minutes as a missed-callback fallback.
