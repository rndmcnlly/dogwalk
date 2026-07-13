# Dogwalk

Dogwalk aims to provide the capability surface of an agentic coding-session manager, including juggling many concurrent sessions, entirely through hands-free voice with zero dependence on visual user interaction. A deliberately engineering-weak voice agent called Walker coordinates stronger coding agents called Dogs; the diagnostic web UI exists only for iterative harness development, telemetry, and debugging, not as a required user interface.

Run the scripted non-audio integration test with `uv run --script text_spike.py test collaboration`; run the diagnostic voice server with `uv run --script webrtc_spike.py`, then open `http://127.0.0.1:8765/webrtc_spike.html`. Prefer isolated YAML scenarios and inspect `logs/` only when a concise test result fails.

A Dog is Dogwalk's named, voice-friendly wrapper around one retained ACP session; Walker uses ACP `session/new`, repeated `session/prompt`, updates, and cancellation through Dogwalk's tool surface. Dog names are mutable vocal aliases, while ACP session IDs remain opaque implementation identifiers.
