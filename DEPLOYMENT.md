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

## Configuration

| Environment variable | Default | Meaning |
|---|---|---|
| `DOGWALK_HOST` | `127.0.0.1` | HTTP bind address |
| `DOGWALK_PORT` | `8765` | HTTP port |
| `DOGWALK_WORKSPACE` | Dogwalk source directory | Workspace given to Dogs |
| `DOGWALK_AGENT_COMMAND` | `opencode acp --pure --cwd {cwd}` | Local ACP command; `{cwd}` expands to the workspace |
| `DOGWALK_LOG_DIR` | `logs/` beside Dogwalk | Structured service and ACP logs |
| `DOGWALK_CALL_LEASE_SECONDS` | `15` | Time before a disconnected Walker lease expires |
| `OPENAI_API_KEY` | none | Realtime API credential |

Equivalent command-line flags are available through `webrtc_spike.py --help`.
The agent command is parsed as arguments without invoking a shell.

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
