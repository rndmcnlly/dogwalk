# Dogwalk

Dogwalk aims to provide the capability surface of an agentic coding-session manager, including juggling many concurrent sessions, entirely through hands-free voice with zero dependence on visual user interaction. A deliberately engineering-weak Voice Agent coordinates stronger ACP Agents running in isolated sandboxes; the diagnostic web UI exists only for iterative harness development, telemetry, and debugging, not as a required user interface.

## Accessibility Ethos

Dogwalk's central promise is access to a highly capable team of agents, and through them much more of the web and computing, through an ordinary voice telephone call. The benchmark is what someone could do from a payphone, a borrowed handset, or a satellite phone: sophisticated infrastructure behind an extremely modest interface. This is not merely a convenient alternative to a screen. It expands who can act, from where, and with what equipment.

- Put complexity in the infrastructure, not on the caller. Preserve a useful path requiring only a low-bandwidth voice connection, without a smartphone, installed app, browser, display, or companion device.
- Treat eyes-free operation as an end-to-end requirement, including authentication, permissions, interruptions, error recovery, and returning to ongoing work. Do not quietly introduce a visual prerequisite at the difficult parts.
- Prefer natural conversation over memorized commands, passwords spoken aloud, or mentally demanding challenge–response rituals. Authentication still needs an explicit threat model and honest limits; accessibility is not a reason to silently bypass security.
- Design for imperfect connections, background noise, latency, and interrupted calls. A borrowed or unfamiliar phone should not be excluded merely because it is unfamiliar.
- Rich clients, including Android clients and experiments with alternative voice models or providers, are welcome as parallel paths. They must not redefine the minimum required equipment or displace the ordinary-telephone accessibility goal. The goal is not loyalty to PSTN or a particular provider: it is preserving access without requiring sophisticated equipment at the caller's end.

When weighing a feature or architecture change, ask: does this make more capability available through an ordinary call, or does it move a new burden onto the caller?

## Deployment and Development

The production path is Twilio PSTN -> Cloudflare Worker access and Sandbox wake-up -> OpenAI GPT-Live over secure SIP as the Voice Frontend, with a client-delegation sideband to the sandbox-keyed Durable Object, which runs the Session Orchestrator -> ACP-over-WebSocket -> the Daytona-hosted ACP Gateway and OpenCode 2. `orchestrator_spike.py` is a text-only harness for judging Orchestrator behavior against local `opencode acp`. Run `uv run --script cf_worker/smoke_test.py`, `uv run --script cf_worker/voice_session_test.py`, and `uv run --script sandbox_acp_gateway.py test`; run `npm run typecheck` from `cf_worker/`. `cf_worker/README.md` is the operational runbook and `PSTN_CLOUDFLARE.md` records the deployed architecture. To review a call or playtest, run `uv run --script playtest.py` and follow the `call-analysis` skill (`.agents/skills/call-analysis/SKILL.md`), which holds the review checklist and the rule for when a check moves into the CLI. For speaker verification, Voice Donations, or a voice score that looks wrong, follow the `voice-research` skill (`.agents/skills/voice-research/SKILL.md`) and start from its soundboard. Before tuning a timer or window that depends on how GPT-Live behaves, measure it with the `provider-probe` skill (`.agents/skills/provider-probe/SKILL.md`) and `delegation_probe.py`.

Changes land through pull requests so review bots can comment while other work continues. Greptile reviews each PR once, when it opens (`greptile.json`); each review costs real money, so never request a Greptile re-review or mention it after the PR is open. CodeRabbit reviews follow-up commits. Address findings on the branch, reply on each thread, and merge when the remaining findings are fixed or answered. Run a branch's suites with nothing else running before merging, and give parallel worktrees separate `DOGWALK_TEST_PORT_BASE` values.

This is a public repository for one operator-managed deployment. It is acceptable to document public hostnames, provider resource IDs, local Keychain service names, and commands that retrieve secrets on Adam's laptop. Never commit secret values, local `.env` or `.dev.vars` files, private keys, capability tokens, signed preview URLs, or production request payloads containing them.

Adam is the only user. Prefer destructive back-patches of D1 and Durable Object storage (a migration file, a one-time fix, or letting state be recreated) over backwards-compatibility code paths; when a schema or stored shape changes, migrate the data and delete the compatibility branch.

A Managed Session wraps one retained ACP Session. Its mutable Alias is pronounceable, while the ACP Session ID remains an opaque implementation identifier.

## Domain Language

`DOMAIN.md` is the canonical Ubiquitous Language and is normative for new design and refactoring. Read it before changing session lifecycle, Agent integration, Voice Agent tools, state names, or domain terminology. It takes precedence over historical language preserved in Git and accidental structure in the current implementation.

Core distinctions:

- Dogwalk is an eyes-free ACP Client. An ACP Agent is a coding harness such as OpenCode, distinct from the Voice Agent.
- A Managed Session wraps one ACP Session and owns its pronounceable Alias.
- A Prompt Turn completes or is cancelled; its Managed Session can remain available for later turns.
- Cancelling a Prompt Turn, closing an active session, and deleting persisted session history are distinct operations.
- Permission Requests authorize actions. Elicitations ask for information. Do not collapse them into one protocol response type.
- OpenCode, OpenAI GPT-Live, and Daytona are adapters or deployment choices, not domain concepts.
- Dogwalk is a product codename. A Theme (currently Park, the canine fiction) may flavor UI copy and Voice Agent speech, but it is not domain vocabulary and carries no rules: every Voice Agent rule is stated neutrally in the core prompts, and `themes.ts` only presents it.
- Voice Agent tool names, payloads, logs, schemas, tests, ACP prompts, and sandbox internals use neutral ACP and session-management terms.

When introducing or changing a domain concept, use the canonical term from `DOMAIN.md` in core code and tests, update `DOMAIN.md` in the same change, and record unresolved ambiguity under Open Questions rather than silently choosing a new meaning.
