# Dogwalk

Dogwalk aims to provide the capability surface of an agentic coding-session manager, including juggling many concurrent sessions, entirely through hands-free voice with zero dependence on visual user interaction. A deliberately engineering-weak Voice Agent coordinates stronger ACP Agents running in isolated sandboxes; the diagnostic web UI exists only for iterative harness development, telemetry, and debugging, not as a required user interface.

The production path is Twilio PSTN -> Cloudflare Worker and sandbox-keyed Durable Object -> OpenAI Realtime plus ACP-over-WebSocket -> the Daytona-hosted ACP Gateway and OpenCode. Run `uv run --script cf_worker/smoke_test.py`, `uv run --script cf_worker/voice_session_test.py`, and `uv run --script sandbox_acp_gateway.py test`; run `npm run typecheck` from `cf_worker/`. `cf_worker/README.md` is the operational runbook and `PSTN_CLOUDFLARE.md` records the deployed architecture.

This is a public repository for one operator-managed deployment. It is acceptable to document public hostnames, provider resource IDs, local Keychain service names, and commands that retrieve secrets on Adam's laptop. Never commit secret values, local `.env` or `.dev.vars` files, private keys, capability tokens, signed preview URLs, or production request payloads containing them.

A Managed Session wraps one retained ACP Session. Its mutable Alias is pronounceable, while the ACP Session ID remains an opaque implementation identifier.

## Domain Language

`DOMAIN.md` is the canonical Ubiquitous Language and is normative for new design and refactoring. Read it before changing session lifecycle, Agent integration, Voice Agent tools, state names, or domain terminology. It takes precedence over historical language preserved in Git and accidental structure in the current implementation.

Core distinctions:

- Dogwalk is an eyes-free ACP Client. An ACP Agent is a coding harness such as OpenCode, distinct from the Voice Agent.
- A Managed Session wraps one ACP Session and owns its pronounceable Alias.
- A Prompt Turn completes or is cancelled; its Managed Session can remain available for later turns.
- Cancelling a Prompt Turn, closing an active session, and deleting persisted session history are distinct operations.
- Permission Requests authorize actions. Elicitations ask for information. Do not collapse them into one protocol response type.
- OpenCode, OpenAI Realtime, and Daytona are adapters or deployment choices, not domain concepts.
- Dogwalk is a product codename. Canine language may flavor UI copy, an opening line, or occasional Voice Agent narration, but it is not domain vocabulary.
- Voice Agent tool names, payloads, logs, schemas, tests, ACP prompts, and sandbox internals use neutral ACP and session-management terms.

When introducing or changing a domain concept, use the canonical term from `DOMAIN.md` in core code and tests, update `DOMAIN.md` in the same change, and record unresolved ambiguity under Open Questions rather than silently choosing a new meaning.
