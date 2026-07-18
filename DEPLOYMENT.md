# Deploying Dogwalk

Dogwalk is a small web service that runs beside one workspace and the ACP agents
that operate on it. It does not provision, wake, stop, or identify Daytona
sandboxes. Daytona, a local shell, a container runtime, or another control plane
launches Dogwalk and exposes its HTTP port.

## Requirements

- Python 3.11 or newer and `uv`
- An ACP agent executable, currently `opencode acp`
- A writable workspace and sufficient capacity for concurrent agent processes
- `OPENAI_API_KEY` for voice calls
- Outbound HTTPS and WebSocket access for model providers
- One inbound HTTP port, normally placed behind an authenticated HTTPS proxy

Run locally:

```bash
uv run --script webrtc_spike.py
```

Run the latest revision directly from GitHub without cloning or publishing to
PyPI:

```bash
uvx --from git+https://github.com/rndmcnlly/dogwalk.git dogwalk
```

Append `@<tag-or-commit>` to the Git URL for a reproducible deployment. Dogwalk
uses the command's current directory as its default workspace and reads `.env`
and writes `logs/` there.

Run inside a sandbox or container:

```bash
DOGWALK_HOST=0.0.0.0 \
DOGWALK_PORT=8765 \
DOGWALK_WORKSPACE=/workspace/project \
uv run --script /opt/dogwalk/webrtc_spike.py
```

Open `/webrtc_spike.html` on the resulting origin. `/healthz` reports process
health, and `/readyz` verifies that the workspace and configured agent executable
are available.

## PSTN (phone) deployment

The diagnostic `webrtc_spike.py` service is for harness development, not the
hands-free user. The user-facing transport is a Twilio PSTN bridge split into
two processes that talk over the manifest-driven backend protocol:

1. **ACP backend** (`voice_acp_backend.py`): owns the workspace, spawns
   `opencode acp` subprocesses, and exposes `/manifest`, `/tool/:name`, and
   `/events` SSE. This is where Dogs live.
2. **Voice bridge** (`voice_bridge.py serve`): holds the OpenAI Realtime key
   and Twilio credentials, terminates the phone call, and relays tool calls
   plus async notifications to the backend.

```bash
# Terminal 1: ACP backend (defaults to port 8799)
uv run --script voice_acp_backend.py --workspace /workspace/project

# Terminal 2: Twilio bridge, exposed publicly so Twilio can reach /voice
uv run --script voice_bridge.py serve \
  --backend http://127.0.0.1:8799 \
  --public-url https://<public-host>
```

Expose the bridge port publicly (for example with `tailscale funnel`) and
point a Twilio number's voice webhook at `https://<public-host>/voice`.
Twilio's native 8kHz mulaw audio passes through to OpenAI's `g711_ulaw`
format with no resampling.

For text-only iteration against any backend without a phone call:

```bash
uv run --script voice_bridge.py simulate --backend http://127.0.0.1:8799
```

For a dogwalk-themed stub backend (no real ACP):

```bash
uv run --script voice_bridge.py mock-backend --port 8799
```

## Configuration

| Environment variable | Default | Meaning |
|---|---|---|
| `DOGWALK_HOST` | `127.0.0.1` | HTTP bind address |
| `DOGWALK_PORT` | `8765` | HTTP port |
| `DOGWALK_WORKSPACE` | Current directory | Workspace given to Dogs |
| `DOGWALK_AGENT_COMMAND` | `opencode acp --pure --cwd {cwd}` | Local ACP command; `{cwd}` expands to the workspace |
| `DOGWALK_LOG_DIR` | `logs/` in current directory | Structured service and ACP logs |
| `DOGWALK_CALL_LEASE_SECONDS` | `15` | Time before a disconnected Walker lease expires |
| `OPENAI_API_KEY` | none | Realtime API credential |
| `TWILIO_ACCOUNT_SID` | none | Twilio account SID (enables outbound `/call`) |
| `TWILIO_AUTH_TOKEN` | none | Twilio auth token (also used for webhook signature validation) |
| `TWILIO_PHONE_NUMBER` | none | Twilio caller number |
| `TWILIO_CLIENT_SECRET` | none | Alternate secret for webhook signature validation |
| `PUBLIC_URL` | none | Public HTTPS URL for the `serve` bridge; Twilio webhook target |

Equivalent command-line flags are available through `webrtc_spike.py --help`
and `voice_bridge.py serve --help`. The agent command is parsed as arguments
without invoking a shell.

## Lifecycle

One Dogwalk process owns one pack of Dogs. The diagnostic page acquires an
expiring read-only observer capability so it can display Managed Sessions without
starting a voice call. A browser call separately acquires an exclusive,
short-lived Walker lease and sends its opaque token on mutating or consumptive
sideband requests. A second Walker is rejected so it cannot consume the first
Walker's completion or decision queues. Clean disconnects release the lease
immediately; dropped calls release it after the heartbeat timeout.

Dogs are not tied to that lease. They retain their local ACP processes and
sessions across browser calls until called off or the Dogwalk process stops.
After a Dogwalk process restart, Agent-held sessions can be discovered and loaded
under fresh Dog aliases when the configured Agent supports `session/list` and
`session/load`. Dogwalk does not yet persist aliases or other local projections.

## Daytona

Inside Daytona, bind Dogwalk to `0.0.0.0` and expose its port with a private or
signed preview URL. The preview layer should supply HTTPS and authentication;
Dogwalk's tools can execute agents with workspace and terminal access and must
not be published anonymously. A separate launcher or custom preview proxy may
wake the sandbox, start Dogwalk, wait for `/readyz`, and return the preview URL.

Dogwalk currently uses local ACP over stdio. This co-location is deliberate:
remote ACP HTTP/WebSocket transport remains a draft, and local stdio keeps agent
process lifetime and filesystem access aligned with the workspace. A future
remote ACP backend can fit behind the same Dog-management surface without making
Daytona part of Dogwalk itself.
