# Dogwalk WebRTC spike

This spike exercises the browser voice, Realtime, and ACP integration seam:

```text
browser microphone -> WebRTC -> gpt-realtime-2.1
browser speaker    <- WebRTC <- spoken response
                              |
                              +-> sideband tools -> ACP sessions
```

## Run it

The script reads `OPENAI_API_KEY` from the environment or the local `.env`.
Grant the browser microphone access when prompted.

```bash
uv run --script webrtc_spike.py
```

Open <http://127.0.0.1:8765/webrtc_spike.html> and start an audio session.
Use `uv run --script text_spike.py test collaboration` for agentic testing
without Realtime, WebRTC, a browser, microphone, or speaker.

## Logs

Every run writes `logs/<timestamp>-<mode>.jsonl`. Each line has a UTC timestamp
and a `kind`. Logs include:

- User and Walker transcripts
- speech start and stop boundaries
- complete tool arguments and results
- response status, token usage, and API errors
- session start, configuration, and end markers

Raw audio and base64 audio deltas are intentionally not logged. The JSONL is
small enough for a later coding agent to analyze turn-taking, misunderstood
requests, latency, tool selection, and prompt failures.

## Production direction

The phone version uses WebRTC for device audio and keeps API keys plus ACP tools
in a Python sideband server.

## WebRTC audio spike

`webrtc_spike.py` and `webrtc_spike.html` use browser-managed WebRTC. This is the
path to use for a phone or browser client:
the browser captures and plays audio directly, while Python holds the API key,
creates the Realtime session, runs tools, and records logs.

The spike uses OpenAI's `cedar` voice. A Realtime voice cannot change after a
session has emitted audio, so start a fresh browser session after changing it.

```bash
uv run --script webrtc_spike.py
```

Open <http://127.0.0.1:8765/webrtc_spike.html>, click **Start audio session**,
and grant the browser microphone permission. The page requests browser audio
processing: acoustic echo cancellation, noise suppression, and automatic gain
control. Its event panel is intentionally small: the durable account lives in
the JSONL log printed by the server.

The browser connects to the local server only for three control requests:

- `/session`: local Python sends the browser SDP plus session configuration to
  OpenAI's `/v1/realtime/calls` endpoint with the private API key.
- `/tool`: the browser relays function calls to Python, which dispatches them
  through the session manager and ACP adapter.
- `/event`: the browser records transcript, VAD, connection, tool, and actual
  negotiated microphone settings to JSONL.

Audio media never travels through Python. The browser has a direct WebRTC peer
connection to OpenAI, so its media subsystem can apply echo cancellation before
Realtime's VAD sees the microphone signal.

### Real local Dog

The WebRTC server uses the official `agent-client-protocol` Python SDK to spawn
`opencode acp` as a stdio subprocess. `sic_dog` returns a working handle at
once; ACP tool updates, streamed output, and the final report are recorded in
the same JSONL file. `check_dog` reads that owned Dog state and never triggers
another agent turn.

Dogs operate in their configured workspace and retain their ACP sessions after a
turn, receiving queued follow-up prompts until explicitly called off or
Walker-hands stops. ACP permission requests and elicitation questions pause the
Dog, are relayed to the User through Walker, and resume only after a User-selected
response. The scripted text harness uses a temporary workspace by default. This
local spike is not a sandbox; the remote version should keep the
`SessionManager.dispatch()` surface but place the ACP subprocess behind a
sandbox-side bridge.

Each new voice call receives a current Pack snapshot before Walker greets the
User, so Dogs retained across calls are already known by alias, state,
assignment, activity, and Agent-reported usage. ACP usage updates may include
cumulative cost as an amount plus ISO currency; `list_dogs` and `check_dog`
expose that optional cost without starting another Prompt Turn.

The bridge is exercised through declarative scenarios in `text_spike.py`. It still
needs a harness integration test that causes OpenCode itself to issue
`elicitation/create`: asking
a Dog to *write a question in its report* only produces a normal completed turn,
not an interactive ACP elicitation.

### Hands-free control

The WebRTC spike gives Walker two sideband controls:

- `end_call`: after speaking a brief farewell, Walker can close the browser's
  peer connection and microphone. The browser waits two seconds after the tool
  call so the farewell can finish playing.
- `set_timer`: Walker-hands owns the clock and queues a notification for the
  active browser session. On delivery, Walker tells the User the timer is due and
  may read a relevant Dog's projected status before reporting. Checking is
  side-effect free and requires no permission, but Walker does not tight-loop
  poll.

The timer event reaches the browser through a small local polling loop. It does
not wake a Dog or send any request to the model until the timer is actually due.

### Earcons

The WebRTC page synthesizes brief local tones using the browser Web Audio API.
They do not use model tokens or wait for model speech, and the browser logs each
emission as an `earcon` JSONL event:

- ascending two notes: a Dog was sicced
- one low tick: a Dog is being checked
- ascending three notes: a Dog completed
- two bright chimes: a timer is due
- descending two notes: Walker is ending the call
- warm ascending three notes: the audio session is open and Walker is about to greet the User
