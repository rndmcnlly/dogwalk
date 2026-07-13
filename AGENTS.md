# Dogwalk

Dogwalk aims to provide the capability surface of an agentic coding-session manager, including juggling many concurrent sessions, entirely through hands-free voice with zero dependence on visual user interaction. A deliberately engineering-weak voice agent called Walker coordinates stronger coding agents called Dogs; the diagnostic web UI exists only for iterative harness development, telemetry, and debugging, not as a required user interface.

Run the scripted non-audio integration test with `uv run --script text_spike.py test collaboration`; run the diagnostic voice server with `uv run --script webrtc_spike.py`, then open `http://127.0.0.1:8765/webrtc_spike.html`. Prefer isolated YAML scenarios and inspect `logs/` only when a concise test result fails.

A Managed Session wraps one retained ACP session. A Dog is Walker's voice-facing persona for that Managed Session; its mutable name is a vocal alias, while the ACP session ID remains an opaque implementation identifier.

## Domain Language

`DOMAIN.md` is the canonical Ubiquitous Language and is normative for new design and refactoring. Read it before changing session lifecycle, Agent integration, Walker tools, state names, or domain terminology. It takes precedence over historical language in `PITCH.md`, spike notes, and accidental structure in the current implementation.

Core distinctions:

- Dogwalk is an eyes-free ACP Client. An ACP Agent is a coding harness such as OpenCode; an Agent is not a Dog.
- A Managed Session wraps one ACP session. A Dog is Walker's voice-facing persona for that Managed Session.
- A Prompt Turn completes or is cancelled; its Managed Session can remain available for later turns.
- Cancelling a Prompt Turn, closing an active session, and deleting persisted session history are distinct operations.
- Permission Requests authorize actions. Elicitations ask for information. Do not collapse them into one protocol response type.
- OpenCode, OpenAI Realtime, and Daytona are adapters or deployment choices, not domain concepts.
- Canine terms such as Dog, Pack, sic, relay, and call off belong to Walker's voice UX. Use neutral ACP and session-management terms in core plumbing.

When introducing or changing a domain concept, use the canonical term from `DOMAIN.md` in core code and tests, update `DOMAIN.md` in the same change, and record unresolved ambiguity under Open Questions rather than silently choosing a new meaning.
