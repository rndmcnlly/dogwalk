# Access Threat Model

A decision record for authentication, authorization, and accounting (AAA) at the
PSTN boundary, from issue #47. It records what an admitted Voice Call may do, what
the admission ceremony does and does not prove, what is true of the deployment as
of 2026-11-12, and which risks are accepted. `DOMAIN.md` remains normative for
terms and rules (Access Control, Identity Check, Roaming, Paired Browser); where
this record and `DOMAIN.md` disagree, `DOMAIN.md` wins until it is amended.

**Success is not merely "the owner can get in." It is a comprehensible agreement
about who may open a shared call, what that call can do, and what remains
possible after it ends.**

## Decisions

1. **Home versus Roaming changes the ceremony, not the capabilities.** A User
   admitted from any telephone gets the same Sandbox, Managed Sessions, and
   Workspace powers. This is already the Roaming rule in `DOMAIN.md`; this record
   makes the reason explicit. A Home phone can be on speakerphone with a group and
   a borrowed phone can be held by the User alone, so telephone provenance and
   group sharing are separate dimensions. More importantly, OpenCode tool
   permissions do not subtract authority from a programmable Sandbox: a credential
   in a file or environment stays usable through the shell even when a particular
   tool is denied. A real capability split would need separate credentials,
   separate execution environments, or an external authorization boundary, not a
   prompt instruction or a tool denylist.

2. **Admission is call-scoped, not speaker-scoped.** Admission binds to one Voice
   Call. Everyone the User lets hear and speak on that call may direct the Voice
   Agent within the User's Workspace. Speaker changes are expected (the primary
   scenario below), so Dogwalk does not continuously verify the speaker. The
   calling number gains nothing reusable from carrying an admitted call.

3. **The Identity Check is evidence of casual-impersonation resistance, not
   authentication assurance.** NIST SP 800-63B-4 §3.2.3 says "Biometric
   comparison based on voice SHALL NOT be used," and §3.1.3.3 treats PSTN
   out-of-band authentication as restricted. These guidelines do not bind this
   deployment, but Dogwalk does not present voice matching as established
   authentication practice. Genuine-caller tests show usability, not a false-accept
   rate against impostors or voice conversion.

4. **Limiting connected powers is the main mitigation.** Because admission is
   weak, what the Workspace can do is the real control. Owners connect scoped,
   revocable, budgeted powers (a repository-specific token, a limited service
   account, an inference project with a budget), not their personal credential
   collection. The plain statement Dogwalk should give owners (not yet delivered;
   see Open question 6) is: **anyone admitted to your call can exercise every
   capability you connected to this Workspace.**
   Limiting powers lowers the cost of impersonation; it does not strengthen the
   ceremony.

5. **Using current capabilities is distinct from creating durable access.** An
   admitted call may do work; actions that leave access behind after the call
   (pairing a browser, minting Invitations, renaming a Handle, Re-enrollment, or a
   new credential written into the Sandbox) are a separate class. Apart from
   Re-enrollment's own enforced check, none is gated outside the model today:
   pairing once needed a keypad press, removed in issue #59 because it protected
   nothing an Agent with `sudo` and egress lacks (see Accepted risks). With unrestricted shell and `sudo` Dogwalk cannot
   promise that any such path is preventable.

## Primary scenario: a long drive with friends

The User is driving with friends and wants to research something together. A
friend calls Dogwalk on their own phone, connected to the car's Bluetooth. The
User claims their account by Handle and, because the phone is unfamiliar, takes
the Roaming ceremony: three Identity Challenges, spoken by the driver, with no
screen and no device in hand. Once admitted, the group gets the User's existing
Sandbox and sessions, and any of them may talk to the Voice Agent. Hanging up ends
instructions through that call; the friend's number calling back must be admitted
again. Anything overheard during admission (Handle, phrases) should not become a
reusable credential: fresh phrases satisfy this against a fixed recording, but not
against fresh synthesized speech.

## Evidence: what each signal establishes

| Evidence | Establishes | Does not establish |
| --- | --- | --- |
| Valid Twilio webhook signature | Request origin and integrity at the provider boundary | Who is calling |
| Caller ID | Which Handle to check first (Home) | The speaker's identity |
| STIR/SHAKEN A attestation | The originating carrier vouches for its customer's right to the number | Which person speaks, or that they own the account |
| Callback to a bound number | Someone can answer there | Freedom from forwarding, shared handsets, or number takeover |
| Public Handle | Which account is claimed | Anything about the claimant |
| Correct fresh phrase | Responsiveness to a challenge, not a fixed recording | Clone resistance or speaker identity |
| Speaker similarity score | Similarity under the tested recording conditions | A calibrated identity probability |
| Reusable spoken secret | Knowledge of the secret | Safety from overhearing, replay, or phishing |
| A spoken "yes" on the same call | Intent | Any additional identity evidence |

Three voice matches are one factor sampled three times, not three factors.

## Assets

- Workspace files, Agent Session histories, Orchestrator Memory, and project metadata.
- Credentials and external powers reachable from the Sandbox.
- Account continuity: Handle, Home, Voice Reference, Invitations, browser pairing.
- Paid resources: inference, telephony minutes, Sandbox compute, storage, egress.
- Caller identity data: phone numbers, Voice References, call transcripts.

## Adversaries and failure modes

- A caller who knows the User's Handle and Home number (the casual impersonator
  `DOMAIN.md` targets).
- Someone briefly holding the User's phone, or controlling a forwarded, reassigned,
  or taken-over number. Caller ID spoofing alone admits a Home call today.
- Someone with recordings: replay, synthesized speech, or real-time voice
  conversion, which narrowband telephone audio helps disguise. Challenge Practice
  and Roaming's spoken levels are an oracle for tuning such an attack.
- Someone who overheard a legitimate admission and tries to reuse it.
- A caller, or text injected into an Agent's context, persuading the Voice Agent or
  an Agent that a claimed identity or spoken confirmation is authorization.
- Someone attacking the side doors (enrollment, Re-enrollment, recovery,
  Invitations, pairing) instead of the ordinary ceremony.
- A resource abuser who never needs an existing account to run up cost.
- Accidents or malice by a legitimately admitted group member. Owner supervision
  and hangup do not undo completed actions.

## Current state (2026-11-14)

Re-verify these before relying on them; they describe the code at commit
`1da7666`, plus the Upload Access Element (issue #63, 2026-11-14), plus Voice
Transfer, the once-a-day Voice Reference limit, and the Opening notices (2026-11-27).

### Authentication

- **Home calls are shadow-only.** The Identity Check runs and is scored, but a Home
  call connects whatever the result (`cf_worker/src/identity_check.ts`,
  `cf_worker/src/index.ts`). In effect, caller ID admits a Home call, so a spoofed
  Home number admits.
- **Roaming admission enforces** and is on in this deployment
  (`ROAMING_ADMISSION = "on"` in `cf_worker/wrangler.toml`). Three matched
  challenges admit for the rest of the call; failures pause a Handle (3 per day)
  and a calling number (5 per day).
- **Re-enrollment enforces** its own check even while Home is in shadow mode. It is
  reachable from a Home call or a Roaming-admitted call, replaces a Voice Reference at
  most once a day (counted from `voice_references.enrolled_at`, which first enrollment also
  sets), and a different voice becomes a **Voice Transfer**: the current voice must pass one
  more challenge after hearing that the new voice gets the whole account. For three days
  after a replacement, every Opening says so, derived from a Call Activity event committed with the replacement, not a
  consumable flag (`cf_worker/src/reenrollment.ts`, `voiceProfileNotice` in
  `cf_worker/src/voice_session.ts`).
- **The Roaming notice** is marked told only on a call whose own voice check matched, so a
  spoofed Home call hears it but cannot clear it.
- **STIR/SHAKEN** attestation is recorded and spoken in the Opening, never enforced.
- **No speaker re-check after admission.** Admission is keyed to the call
  (`voice_calls.identity`), consistent with decision 2.

### Authorization: powers inside the Sandbox

- **Inference key:** `OPENAI_API_KEY` is a Daytona secret placeholder;
  Daytona's egress proxy substitutes the real key only toward `api.openai.com`.
  The Agent cannot read the key but can spend it. `SANDBOX_AGENT_GATEWAY.md`
  recommends a dedicated OpenAI project with a budget; whether one is configured is
  an OpenAI-side setting not recorded here.
- **No other provisioned credentials:** Dogwalk provisions no GitHub token, git
  credential, or search key. Twilio, Daytona, identity, and admin secrets stay in
  the Worker.
- **Caller-connected credentials:** a caller may sign a Sandbox tool in to an
  account of their own through a device authorization flow (the
  `device-authorization` Voice Agent Skill): the Orchestrator relays only the
  public verification page and one-time code, never a token. The credential then
  lives in the Sandbox, and every Agent there can use it, including one acting on
  injected instructions, until the tool is signed out and the grant revoked at the
  provider. The skill asks for the smallest scope and tells the caller this before
  starting; nothing outside the model enforces either. First done 2026-11-14
  (playtest CAcca1c9d6): `gh` signed in as the operator's GitHub account with
  `repo`, `read:org`, and `gist`, so the Agent can write to every repository that
  account can, including this one.
- **`sudo` and open egress:** the `daytona` user has passwordless `sudo`; the
  Sandbox create request sets no egress allowlist, so egress beyond Daytona's
  account defaults is open.
- **No OpenCode permission policy:** `sandbox_opencode.json` has no `permission`
  block, so OpenCode's defaults apply.
- **Permission Requests are not a security boundary.** The OpenCode service
  password lives in `~/.local/state/opencode/service.json`, readable by the same
  user as the Agent's shell. An Agent could likely call OpenCode's API directly,
  including answering its own Permission Requests (inferred, not tested).
  Permission Requests express the User's intent to a cooperative Agent; they do
  not confine an uncooperative one.

### Authorization: durable access from an admitted call

| Path | Triggered by | Lifetime | Outlives the call |
| --- | --- | --- | --- |
| Paired Browser (any number, each on a named Display) | `pair_browser`, then `accept_browser` on the caller's spoken yes (a model can complete it; convenience, not a boundary) | 1 h idle, 12 h total; shows boards, opens nothing by itself | Yes |
| Access Grant: Terminal, Desktop, Preview, SSH Access, Download, Upload | Drawn on a board during a call; opened by a click on that Display while the Sandbox runs | 1 h default, up to 12 h on request; ends with its Display or `revoke_access`; Terminal and SSH give a full shell with `sudo`; Upload is a capped write path into `~/uploads/` that accepts files after the call | Yes |
| Invitation | `create_invitation` voice tool | 24 h, single use; `revoke_invitation` exists | Yes |
| Handle rename | `offer_handles`, `set_handle` | Permanent; old Handle quarantined 90 days | Yes |
| Re-enrollment or Voice Transfer | `start_reenrollment`, own enforced check; a transfer is confirmed by the current voice; once a day | Permanent | Yes |
| Anything the Agent writes | An Agent with shell and `sudo` (SSH keys, tokens, tunnels) | Until the Sandbox is replaced | Yes |

Access Grants are the largest gap: for up to twelve hours after a call, an
Access Element drawn during it reaches a `sudo` shell (a Terminal, or SSH Access,
which works outside any browser and is accepted without a spoken confirmation).
A grant needs the Voice Agent to draw it, so a model can create one, on a Display
the Voice Agent accepted on a spoken yes; a model can do both.
Narrowing it from the previous design: a grant is scoped to one Display and one
target, has its own expiry, can be revoked by voice (`revoke_access`,
`unpair_display`), and nothing on a board wakes a sleeping Sandbox.

Terminal, Desktop and Preview opens use port-scoped, expiring Daytona signed links. A
Terminal reaches ttyd in the Sandbox (port 7681) and a Desktop noVNC (6080), each a full
shell or screen with no login of its own, run only while a grant is open and reachable only
through a signed link (or by a process already in the Sandbox). These links are bearer
capabilities, not browser-bound credentials: anyone who obtains one can use it while it is
valid, without a Daytona login. The API key and Sandbox-wide preview token stay server-side.

Daytona v2 revokes less than v0 did (measured 2026-11-25), and Dogwalk makes up what it can:

- **Signed links** are revoked only for a whole Sandbox, and open sockets survive
  revocation. Dogwalk also ends the ttyd or noVNC server, which closes live Terminal and
  Desktop pages at once, on revocation and when any link to that server expires. An Agent with `sudo`
  could start its own server on that port, but it needs no grant to serve a shell. Ending one
  Display's links ends every Display's; they reopen under their own grants.
- **Preview:** new opens are refused, but an app's open socket is not closed (accepted: it
  reaches one port the User showed).
- **SSH tokens cannot be revoked**, even by rotating the Sandbox's signing key. Each lasts
  five minutes at most rather than the grant's length, and `revoke_access` and
  `unpair_display` report that a session may be open, whenever a command has been opened since
  the last restart. A session already open lasts until it disconnects or the Sandbox
  restarts, whatever happens to its grant: a full shell outliving its authority, accepted
  only with the voice `restart_sandbox` as the certain end.
- **A paused Sandbox** freezes its takeover servers; they are ended when it next wakes, and
  no link to them is valid meanwhile.

Sandboxes share one Daytona project with the operator's other work, so its API key reaches
those Sandboxes and theirs could be given Dogwalk's secret by name. Nothing on a board wakes
a Sandbox: `wake_on_request` is off, so neither does a request to a leaked link.

Mint completion rechecks stored authorization atomically with credential
registration, so revocation cannot miss a token still being minted. Failed
provider revocations are kept in a durable outbox and retried by alarm; explicit
revocation reports pending cleanup rather than claiming success. The alarm also
expires unattended browsers and ends a Display when its last browser expires.
Authority removal and durable credential capture share a transaction for explicit
revocation, board eviction, Display removal, and Sandbox replacement. The same
commit arms an alarm, so a crash before provider cleanup cannot forget the
credentials or leave retries dependent on another caller action.

Upload is the only Access Element that writes. It mints no provider credential:
each file is one `POST /pair/upload` under the pairing token, streamed through
the Worker and Durable Object to Daytona's toolbox with a fixed length, never
buffered whole. Files land only under `~/uploads/<folder>/` (at most three plain
segments; every step below the root must be a real directory, not a symlink,
the root's real path must be the root, and the name is created exclusively, with
the same accepted swap-after-check race as reads), never in a project
directory, so a planted `AGENTS.md` or dotfile cannot reach an Agent's context
or a tool automatically. Names are flattened to one segment, never a dotfile,
never overwrite (a taken name gets ` (2)`), and are written without the execute
bit. The grant caps 25 MB per file, 20 files, and 100 MB in all, reserved
before bytes move; a name claimed for a file that never arrived is removed (the
reservation comes back only once it is), and a browser that stops sending is
dropped after 30 seconds. These are per grant, not a Workspace budget: each
Upload drawn gets its own allowance, and redrawing one restores it without
removing earlier files, so nothing bounds total upload storage but the
Sandbox's disk. `~/uploads/` is excluded from project discovery and refused as
a Managed Session's directory, so an uploaded `AGENTS.md` never becomes an
Agent's instructions; but it is an ordinary Workspace folder, and a process an
Agent left watching it would see new files. Someone holding a stolen Paired Browser token while an
Upload grant is live and the Sandbox runs can therefore plant capped,
non-executable, non-dot files in that folder only. Every file produces a harness
event, and so do failed attempts up to 20 per grant (more are refused silently,
so retries cannot flood the Session Orchestrator's queue); an upload accepted
between calls is heard only at the next call. A file's contents are an injection
risk only once the Session Orchestrator or the User has an Agent read them, but
its name (up to 200 bytes of letters, digits and punctuation) and declared type
reach the Orchestrator's context directly. Revoking a grant refuses new files; a
file already streaming finishes. An upload is caller input, never authorization.

### Hangup

- Instructions through the call end. Outstanding Pairing Codes and unanswered
  browser claims expire.

- Prompt Turns keep running; ending access and cancelling work are distinct
  operations.
- Pending Permission Requests stay pending: never auto-approved, never
  auto-rejected. The next call or a co-present client may answer them.
- The Session Orchestrator does not act without a live caller, apart from writing
  the next Opening line (no tools) and finishing a Compaction already in progress.
- Hangup cannot undo disclosed information, deployments, or other external effects,
  and does not revoke a Paired Browser, an Access Grant, an Invitation, or anything an Agent wrote.
- There is no spoken way to end the group's access short of hanging up, and
  Dogwalk cannot tell that the User left the car while the call stayed connected.

### Accounting

- **No per-User spending caps** on inference, telephony, or compute. Every Sandbox's
  Agents share one key, as do all calls' Session Orchestrators and GPT-Live sessions.
- **One deployment-wide inference cap (issue #57):** each component bills to its own key
  in the OpenAI Dogwalk project (`dogwalk-voice-frontend`, `dogwalk-orchestrator`,
  `dogwalk-sandbox-agents`, `dogwalk-dev-laptop`), and the project has a hard $80 monthly
  limit, below the organization's, verified on the Platform 2026-11-13. Reaching it
  refuses every Dogwalk request, calls included, until the month resets: an Agent with
  `sudo` spending through the Sandbox key can exhaust it for everyone, but cannot spend
  much past it (enforcement is not instantaneous, so recorded spend can overshoot the
  limit slightly) or into other projects. The organization's prepaid credit is a second, shared stop.
- **No call time limit** is set on the Twilio `<Dial>`, so the provider default
  applies.
- **Bounds that exist:** one live call per Sandbox; 24 Orchestrator steps per run;
  Sandboxes auto-stop after 15 idle minutes and are reaped after 30 days without
  calls; Users are reaped after 365 days without calls (shipping as a dry run), which
  quarantines the Handle and deletes the Voice Reference, so an abandoned identity
  stops being a Roaming target; pairing guess budgets; 3 active Invitations per User;
  a 24-hour Registration cooldown per number (no cap on Users per phone, so a
  willing inviter and one phone can still make one User a day; the Diagnostics
  Surface lists such numbers); Roaming pause limits.
- **Records:** `voice_calls` (duration, User), `call_activity` (per-call timeline),
  `audit_log` (phone number, no User column), and `spend`, the Worker's estimate per
  component, Sandbox, and call (`cf_worker/src/spend.ts`). Spend can be totalled per
  Sandbox, so per User, but nothing caps or reports it that way yet.

## Accepted risks

Accepted for one operator-managed deployment whose only Users are invited:

- A determined attacker with recordings of the User and a voice converter can pass
  Roaming. Mitigation is the narrow credential portfolio, not the ceremony.
- A spoofed Home number admits while Home stays in shadow mode.
- Anyone admitted to a call, and anyone who can speak into it, can create durable
  access through a Paired Browser and the Access Grants drawn on it.
- A model can complete a pairing (issue #59): the Voice Agent accepts a browser
  on the caller's spoken yes, so a confused or prompt-injected Session
  Orchestrator could accept one unasked. Accepted because this is convenience,
  not a boundary, for two reasons. First, the Agent in the Sandbox already holds
  `sudo` and unrestricted egress, so a prompt-injected Agent can open its own
  reverse channel without any Paired Browser; guarding the Orchestrator's pairing
  tool while the Agent holds a root shell with network access protects nothing an
  attacker needs. Second, the keypad press only ever confirmed the system's
  description of the claiming browser and its rough place ("Chrome on a Mac near
  Santa Cruz"). An attacker close enough to hear the code and type it first is,
  almost by construction, on a similar browser in the same place, so the
  description the caller approves would match theirs. The press cost the caller
  a ritual and the harness a special workflow for that protection. What remains
  bounds online guessing, not overhearing: single-use codes, a ninety-second
  window to claim, Nameplates, guess budgets, and claims ending with the call.
  A claim made within that window stays acceptable for the rest of the call
  (until answered or replaced by a new code), and Dogwalk does not check that
  the claiming page is still open; a browser accepted after its page gave up
  holds an uncollected credential that lapses with the idle expiry.
- An Upload grant accepts files after the call ends, so a holder of the pairing
  token can plant capped files in `~/uploads/<folder>/` between calls, heard
  only at the next call. Accepted because files never land in a project
  directory, are never a Managed Session's root, and reach an Agent only when
  someone asks it to read them (or a process an Agent started watches the
  folder). Total upload storage is bounded only by the Sandbox's disk.
- An uploaded file's name and declared type are text chosen by whoever holds
  the page, and reach the Session Orchestrator's context as part of the
  harness event: a prompt-injection channel bounded by the name rules and the
  failure cap. Accepted on the same terms as a Whiteboard Answer's free text.
- A spoofed Home number reaches Challenge Practice, which speaks a level for up to ten
  scored rounds per run, with no limit on runs and no notice to the User: an oracle for
  tuning a voice converter against the Voice Reference. Accepted because Practice is how
  genuine Users learn how well they are recognized, and an attacker with a working
  converter needs no oracle; revisit when Home enforcement makes the oracle the only way in.
- Voice Transfer means a caller who can pass as the current voice (three challenges and one
  more) can give the account to their own voice in one call, locking the owner out of
  Roaming. The once-a-day limit stops walking the reference in small steps, not this. The
  owner still reaches the account from Home while Home is in shadow mode, hears the
  transfer in the Opening for three days, and recovers through the operator. This widens
  the already-accepted cloning risk from access to takeover.
- When the current voice is gone and cannot agree (a sudden illness), no voice path
  transfers the account: the operator deletes the Voice Reference and decides what to do
  about the Home, so the next Home call enrolls whoever calls. Home enforcement would make
  this the only way for a caretaker to take over; keep a non-voice way in at Home.
- An Agent with `sudo` can create durable access, spend the inference budget, and
  likely self-approve Permission Requests. The Sandbox is the trust boundary, and
  everything inside it is trusted equally.
- Until User reaping leaves its dry run, and for a year after a User's last call
  either way, a dormant Handle with an old Voice Reference stays claimable by Roaming.
  If Home enforcement makes that target more valuable, shorten the reap age.
- No per-User spending cap exists. The OpenAI Dogwalk project's hard $80 monthly
  limit (verified 2026-11-13) bounds inference spend for the whole deployment, so one
  User's Agents can exhaust it for every User; idle timeouts stop idle Sandboxes
  without limiting spend during active work.

Each acceptance holds only while the portfolio stays narrow. Connecting a
production deployment credential, a personal GitHub token, or a payment method to
a Sandbox reopens this record. That happened on 2026-11-14 (see Caller-connected
credentials): the operator's Sandbox holds a broad personal GitHub token, so a
misled Agent can now reach the operator's repositories, not only the Sandbox. The
record stays open until that token is signed out or replaced by a fine-grained
token scoped to chosen repositories.

## Alternatives considered, none selected

- **Voice verification alone:** the best interaction fit; weak against cloning, and
  contrary to current standards guidance.
- **Reusable spoken secret or static card number:** accessible, but overhearing
  creates future access.
- **One-time codes or an accessible physical token:** reduce replay, but need an
  eyes-free, driver-friendly reading and a recovery story, and remain open to
  real-time relay.
- **Approval from another authenticated person or device, bound to this call:** a
  useful optional path; must not become a required smartphone.
- **Declared lower assurance for a narrow portfolio:** the current position
  (Accepted risks), honest only if the portfolio stays narrow and owners are told
  what they are connecting.

## Open questions

These are mirrored briefly under Open Questions in `DOMAIN.md`.

1. What impostor success rate, against which attacks, is acceptable for the
   portfolio owners actually connect?
2. Is there an overhear-safe, hands-free admission ceremony a driver can perform
   without a personal smartphone, screen, or cognitively demanding ritual? This
   record does not compare complete ceremonies. Answering it with a comparison of
   two or three concrete ceremonies against the driving scenario (criteria fixed
   in advance, including setup and recovery burden) is a prerequisite for
   enforcing the Identity Check on Home calls or changing the ceremony.
3. What does first-call enrollment establish, and how do Re-enrollment, Voice Transfer, and
   re-invitation avoid becoming weaker side doors?
4. Which durable-access operations can be separated from ordinary Sandbox work?
   Candidates: pairing or granting access only on a call whose Identity Check
   matched, Access Grants ending when the call that drew them ends, and the
   OpenCode service password moved out of the Agent's reach. (Voice unpairing
   and revocation exist: `unpair_display`, `revoke_access`.)
5. What exactly ends on hangup, and should the User have a spoken "end everyone's
   access" short of hanging up?
6. How does Dogwalk explain connected powers and residual risk by voice, concretely
   enough for informed provisioning?
7. Should accounting gain per-User caps (call time limit, inference budget per
   Sandbox, a User column in `audit_log`) before more Users are invited?

## Next work

- Before enabling enforcement on Home calls, evaluate held-out genuine callers,
  human impostors, replay, and voice conversion over real telephone audio, with
  acceptance criteria fixed before thresholds are tuned (`voice-research` skill).
- Compare two or three concrete admission ceremonies against the driving scenario,
  including setup and recovery burden.
- Test call-specific admission, reconnect, hangup, group participation, and
  attempted durable access as explicit scenarios.
- Confirm the unknowns above: OpenCode 2.2.7's default permissions, Daytona's
  egress defaults, Twilio's default call limit, whether pending Permission
  Requests time out, and whether the OpenAI project has a budget.

## Sources

Reviewed in November 2026; upstream behavior may change.

- [NIST SP 800-63B-4, §3.2.3 Biometrics](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/#biometrics)
- [Twilio: trusted calling with SHAKEN/STIR](https://www.twilio.com/docs/voice/trusted-calling-with-shakenstir)
- [OpenCode permissions](https://opencode.ai/docs/permissions): shell runs with
  the host user's filesystem, process, and network authority.
- [OpenClaw Voice Call Realtime, caller verification](https://github.com/TristanBrotherton/openclaw-voice-call-realtime#inbound-calls--caller-verification):
  allowlists, STIR/SHAKEN, optional passphrase; a precedent, not an audit.
- [Claude Call inbound implementation](https://github.com/PatterAI/awesome-claude-call/blob/1a9ae84b497ce3613b6c862f2a77683dc0231b99/server/src/inbound.ts):
  takes messages only, so its simpler admission matches its smaller authority.
