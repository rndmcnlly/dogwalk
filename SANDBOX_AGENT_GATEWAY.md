# Sandbox Agent Gateway

Status: replaces the ACP Gateway (issue #28); not yet in production.

`build_sandbox_snapshot.py` builds `dogwalk-agent-20261112-v1` with this gateway and
OpenCode `2.2.7`. Production still runs `dogwalk-acp-20261108-v2` (the ACP Gateway)
through `DAYTONA_SNAPSHOT` in `cf_worker/wrangler.toml`, and adopts the new snapshot
only once the Worker speaks OpenCode's native API. The ACP Gateway's last version is
`sandbox_acp_gateway.py` in Git history.

`sandbox_agent_gateway.py` keeps the Sandbox's shared OpenCode service running and
exposes its native HTTP API to Dogwalk. It is a supervisor and authenticated proxy
only: it does not interpret requests or events, manage sessions, construct prompts,
or know about Voice Agent narration.

## Boundary

```text
Dogwalk (Durable Object)
    |
    | HTTP /api/*            WebSocket /events
    v                        v
sandbox_agent_gateway.py (port 8765)
    |  Basic auth added      | one GET /api/event (SSE) per client
    v                        v
opencode serve --service (the shared service, 127.0.0.1:49374 by default)
```

The service is OpenCode's own shared background service: it runs in the foreground,
changes to `$HOME`, reads its hostname, port, password, and environment from
`~/.config/opencode/service.json` (generating and saving a password there if none;
it ignores `OPENCODE_PASSWORD`), and once listening registers itself in
`~/.local/state/opencode/service.json` (`$XDG_STATE_HOME` if set) as
`{id, version, url, pid, password}`. It re-reads that state file every 5 s and shuts
down if the file no longer describes it, so the gateway only ever reads it.

### Supervision

The gateway resolves the service from the state file and checks `GET /api/info` with
HTTP Basic `opencode:<password>`. If nothing healthy answers, it starts the Agent
command (`opencode serve --service`) as its own child, with its own user, `HOME`, and
environment, and waits for registration.

- Client disconnects never stop the service; Agent work lives there, not in a
  connection.
- A child that exits 0 found a healthy service already answering: someone else owns
  it (`agent_handoff` in the log). The gateway adopts that service and checks it every
  5 s (each probe allowed 3 s). It starts its own child at once when the state file is
  missing or names a dead pid, but a live one must fail three probes in a row
  (`agent_probe_failed`): starting a second server on one slow answer would rewrite the
  state file and split Dogwalk and a TUI onto different services.
- A child that has not registered within 60 s is stopped (`agent_start_timeout`) and
  recorded as a `killed` Agent Termination with `"stalled":"startup"`, then retried with
  the same backoff as below, which a stalled start does not reset.
- A child that registered is probed on the same schedule. Three failures in a row while
  its process lives (`agent_probe_failed`, then `agent_unresponsive`) stop it, recorded as
  a `killed` Agent Termination with `"stalled":"unresponsive"`, and it is replaced.
- A child that exits otherwise is an Agent Termination. The gateway reads cgroup v2
  `memory.events` (its own cgroup and ancestors, since the limit may sit on the
  container) to tell an OOM kill from another signal or exit, logs `agent_terminated`,
  keeps it as `last_termination` in `/healthz`, and restarts the service after a
  backoff of 1 s, doubling to 30 s, reset after a minute of uptime.
- The gateway stops its child when it shuts down itself.

### Endpoints

| Endpoint | Contract |
|---|---|
| `GET /healthz` | Gateway up; `service` (`resolved` or `unresolved`), `service_url`, `service_pid`, `child_pid` (null when another process owns the service), `event_clients`, `last_termination` |
| `GET /readyz` | 200 when the Workspace exists, the Agent executable is present, and the service answers `/api/info`; 503 otherwise |
| `ANY /api/*` | Reverse proxy to the service |
| `GET /events` with WebSocket upgrade | The service's live events, one text frame per event |

`/api/*` forwards method, path, query, and body (at most 1 MiB, else 413) and returns
status, `Content-Type`, and the body, streamed, so `GET /api/event` also works through
it. The client's `Authorization`, `Cookie`, hop-by-hop, and `x-daytona-*` headers are
dropped; the gateway adds the service's Basic credentials. A refused connection or a
401 re-resolves the service once and retries (the service restarted with a new
password, or another owner took over). While no service answers, a request waits up
to 15 s for one (a call that wakes the Sandbox arrives before OpenCode is ready), then
gets 503; a service that drops the request gets 502.

`/events` opens its own `GET /api/event` per client, so any number of clients may
connect. Each SSE event's `data:` payload (OpenCode's JSON, starting with
`{"type":"server.connected",...}`) is sent unchanged as one text frame; inbound frames
are ignored. The close tells the client what happened:

| Close | Meaning |
|---|---|
| 1011, reason `{"reason":"out_of_memory","signal":"SIGKILL","exit_status":-9,"limit_bytes":4294967296}` | The service this gateway started died abnormally. `reason` is `out_of_memory`, `killed`, or `exited`; `signal`, `exit_status`, and `limit_bytes` appear when known, and `stalled` when the gateway stopped it. Compact JSON, within the 123-byte close-reason limit; the Worker parses it with `parseAgentTermination`. |
| 1012 `service restarting` | The stream ended otherwise: the service closed it, exited 0, or was owned by another process. Reconnect. |
| 1012 `agent service stalled` | Nothing arrived for 45 s. OpenCode `2.2.7` writes an SSE `: heartbeat` comment every 15 s (measured 2026-11-12 against a local `opencode serve --service`), so three missed heartbeats mean a wedged service holding its socket open. The gateway forgets the resolved service so the next request re-probes it. Reconnect. |
| 1013 `agent service unavailable` | No service answered within 15 s, or `/api/event` did not answer 200. Retry later. |
| 1009 `event too large` | One event exceeded 16 MiB. |
| 1001 `gateway stopping` | The gateway is shutting down. |

### Trace context

A proxied request may carry a W3C `traceparent` header, forwarded upstream unchanged.
For each such request the gateway logs one metadata-only line once the service has
answered, for example

```text
INFO agent_trace_context {"method":"POST","path":"/api/session/ses_1/prompt","trace_id":"…","span_id":"…","status":200,"received_ms":1794379289234,"forwarded_ms":1794379289240}
```

An invalid value (anything but a version-00 traceparent with nonzero IDs) is logged as
`"traceparent":"invalid"`, never echoed, and still forwarded, since the gateway
observes rather than enforces. Bodies, queries, and credentials are never logged; a
method or path that is not a short plain token is logged as `"invalid"`. The access
log likewise omits query strings, which may name Workspace paths. The gateway has no
OpenTelemetry SDK or exporter, and nothing in this repository gives the Sandbox an
OTLP endpoint.

## Co-presence

A plain `opencode` started in the Sandbox, for example in a Takeover Terminal's tmux,
finds the shared service through the same state file, checks it, and attaches to it:
the TUI and Dogwalk see the same sessions and the same live events, and either can
prompt. Nothing needs flags or a URL. This works because the gateway and the
Takeover Terminal run as the same user with the same `HOME`: the snapshot's image
sets `USER daytona` and `HOME=/home/daytona`, the gateway is the image entrypoint, and
Daytona's toolbox starts the PTY as the image user (inferred from the image and from
Sandbox files being `daytona`-owned; not yet checked with `id` in a Takeover Terminal).
Neither sets `XDG_STATE_HOME`.

A TUI whose OpenCode version differs from the service's kills and replaces it. The
snapshot ships one binary (`/usr/local/bin/opencode`), so this happens only if someone
installs another (an `npm i -g opencode-ai`, a self-update). The replaced child then
shows up as a `killed` Agent Termination and the gateway adopts the new service, but
the replacement runs whatever version that TUI was.

## Run

Inside a Sandbox:

```bash
uv run --script sandbox_agent_gateway.py serve \
  --workspace /home/daytona \
  --agent-command 'opencode serve --service'
```

Defaults are `0.0.0.0:8765`, the current directory as Workspace, and
`opencode serve --service`. The service's own address and password come from its
state file, `~/.local/state/opencode/service.json`, never from the gateway's arguments. The environment variables
are `DOGWALK_AGENT_GATEWAY_HOST`, `DOGWALK_AGENT_GATEWAY_PORT`, `DOGWALK_WORKSPACE`, and
`DOGWALK_AGENT_COMMAND`.

## Daytona

Expose port 8765 only through a non-public Daytona standard preview URL. The control
plane sends the fresh `x-daytona-preview-token` returned after each Sandbox start and
`X-Daytona-Skip-Preview-Warning: true`; the gateway strips both before forwarding. Do
not use a signed browser URL for the gateway.

The snapshot installs OpenCode `2.2.7` from its GitHub release into `/usr/local/bin`.
OpenCode 1.x through `1.18.4` served the 2026-07 deployments and `2.2.5` the ACP
snapshots; 2.x prints `opencode v2.2.7` for `--version`, which the build check accounts
for. The build also starts `opencode serve --service` as `daytona`, checks `/api/info`
with the registered password, and stops it; it then deletes the state file and the
saved password in `~/.config/opencode/service.json` that check created, so each
Sandbox generates its own.

A Sandbox from an ACP-era snapshot (`dogwalk-acp-*`) still starts
`/opt/dogwalk/sandbox_acp_gateway.py ... --agent-command "/usr/local/bin/opencode acp"`
from its image entrypoint, which cannot change. `deploy.py --patch-sandboxes --apply`
migrates it in place: OpenCode `2.2.7`, this gateway, and a shim at the old path that
execs this gateway with `opencode serve --service` (`cf_worker/README.md`, Cutting
Over to the Native API).

OpenCode keeps temporary tool output in `$TMPDIR/opencode` (`/tmp/opencode`;
`opencode debug paths` shows it) and creates it on first run. The build's
`opencode --version` check runs as root, so snapshots through
`dogwalk-acp-20261108-v1` ship a root-owned `/tmp/opencode` the `daytona` user
cannot write; on 2026-11-08 an Agent script lost a paid transcription response
to that. The build now deletes it before the image is sealed. The existing
Sandbox was fixed in place on 2026-11-08 (`sudo chown daytona:daytona
/tmp/opencode`); a fresh Sandbox needs the next snapshot or the same fix.

### Inference

OpenCode uses `openai/gpt-6.1-sol`, with `openai/gpt-6-luna` as its title model,
through the built-in OpenAI provider. Sandboxes receive `OPENAI_API_KEY` from the
org Daytona Secret `dogwalk-openai`, allowlisted to `api.openai.com`. Daytona's
egress proxy substitutes the real key into HTTPS request headers, so processes in
the Sandbox, including an Agent with `sudo`, see only a `dtn_secret_` placeholder.
The Agent can still *use* the key against the allowlisted host, so the secret holds
`dogwalk-sandbox-agents`, a key of its own in the OpenAI Dogwalk project, whose hard
monthly limit bounds what it can spend (`cf_worker/README.md`, OpenAI Keys and Budget). Daytona already points
`NODE_EXTRA_CA_CERTS` and `SSL_CERT_FILE` at its proxy CA, and OpenCode needed
no TLS changes. Verified 2026-11-07: a fresh Sandbox from `dogwalk-acp-20261107-v2`
completed a real Prompt Turn through the ACP Gateway. The shared service inherits the
gateway's environment, so it receives the same placeholder.

### Web search

`sandbox_opencode.json` sets `"websearch": {"provider": "exa"}`. Without a
configured provider, OpenCode's first `websearch` asks the client to choose one
through an Elicitation; Dogwalk's ACP client advertised no Elicitation capability,
so the tool failed with "Web search cancelled". Exa's hosted MCP endpoint
(`mcp.exa.ai`) works without a key; OpenCode sends one only if `EXA_API_KEY` is
set, which would raise its rate limits. TinyFish also has a keyless mode and is
the fallback if Exa starts refusing anonymous searches. The existing Sandbox
received the key in place on 2026-11-08 and an `opencode run` there completed a
`websearch` with results.

### Binary guard

OpenCode 2.2.5's `read` returns PNG, JPEG, GIF, WebP, and PDF files up to 20 MiB
as base64 data URIs. On 2026-11-08 an 8.3 MiB PDF became an 11.7 MB tool event,
over the 10 MiB SSE limit OpenCode's ACP adapter then had between itself and its
server; the Prompt Turn failed and the session history kept the payload. Events now
reach Dogwalk through `/events`, capped at 16 MiB each, so the guard still matters.
`sandbox_opencode_binary_guard.ts`, installed as
`~/.config/opencode/plugins/binary-guard.ts` (discovered without a config entry),
wraps the built-in `read` executor through `ctx.tool.transform`, so it decides before
anything is loaded or encoded. Text files and PNG, JPEG, GIF, or WebP images up to
2 MiB pass through unchanged. Any other binary, including every PDF, returns a clue of
a few KB: size, `file(1)` type, a 64-byte hex dump with ASCII, and commands to consume
it (`pdftotext`, `pdftoppm`, `ffmpeg` downscaling, `unzip -l`, `tar tf`, `sqlite3`,
`ffprobe`, `xxd`), each naming its apt package. The guard reads the path itself, so it
does not consult OpenCode's external-directory permission; the Agent already has shell
and sudo. The snapshot build adds `file poppler-utils unzip sqlite3 xxd`. The existing
Sandbox received the plugin and those packages (except `xxd`, already present) in
place on 2026-11-08; an `opencode run` there read the 8.3 MiB PDF as a 3.2 KB event, a
small PNG as an image, and a text file unchanged. Whether 2.2.7's `read` behaves the
same has not been checked.

The 2026-07 free-model bootstrap (`opencode/deepseek-v4-flash-free`) ended when
that model left the free lineup.

If a Sandbox is manually deleted, the next call invalidates its stale provider
ID, searches once more by identity label, and provisions a fresh Sandbox from
the configured snapshot. The phone Registration remains valid, but deletion is
destructive to the old Workspace and the Agent-held session history.

This is an operator-recovery path, not routine lifecycle. User sandboxes normally
stop and archive in place, retaining their filesystem and identity indefinitely.

The snapshot grants the isolated `daytona` user passwordless `sudo` and includes
`ping`, DNS, and socket diagnostics.

Since `dogwalk-acp-20261108-v1` it also includes `tmux` and the X11, xfce4,
x11vnc, and noVNC packages that Daytona's computer-use plugin requires (without
them its toolbox endpoints answer 503), plus Debian's Chromium.
`/etc/chromium.d/dogwalk` adds `--no-sandbox --test-type --disable-dev-shm-usage`,
which Chromium needs inside the container. `POST /computeruse/start` on the
toolbox launches Xvfb, xfce4, x11vnc on 5901, and noVNC on 6080; the image sets
`VNC_RESOLUTION=1280x800`, which Daytona reads only when a Sandbox is created.
The packages add about 0.6 GB on disk. Snapshots from `-20261108-v2` on default to 2 vCPU,
4 GiB memory, and a 10 GB disk: at 1 GiB a `pnpm install` was OOM-killed, and the desktop,
ffmpeg, and ImageMagick need the room. Sandboxes created from `-20261107-v2` received the same packages and
flags file in place, keeping their 1024x768 resolution.

## Verification

Run the self-contained test locally or in a Sandbox:

```bash
uv run --script sandbox_agent_gateway.py test
```

It writes a small fake OpenCode service (stdlib Python) and runs it as the Agent
command against a temporary `XDG_STATE_HOME` and cgroup tree. It checks health and
readiness; proxying with auth injection, header stripping, query and body
preservation, 204 and 404 passthrough, and the trace-context log line (valid and
invalid, with nothing secret logged); re-resolving after a 401; the body cap; events
to two concurrent clients, unchanged, including a multi-line `data:` event; a client
disconnect leaving the service running; a plain 1012 close when the stream ends; a
heartbeating stream staying open past the idle limit and a wedged one closing with
1012 `agent service stalled`; an OOM kill reported in the 1011 close reason, then a
restart; an exit status reported the same way; an exit-0 handoff to an externally
owned service; that service's death closing with 1012 and the gateway taking over
again; a child that never registers stopped at the startup deadline and retried; a
live service that stops answering kept through two failed probes and replaced on the
third, and a dead one replaced at once; and the traceparent rules. The fake sends
heartbeats like OpenCode, and the test shortens every interval.

On 2026-11-12 the gateway also ran locally against a real OpenCode `2.2.7`
(`opencode serve --service` with a temporary `HOME`): `/readyz` turned ready,
`/api/info` answered through the proxy, `/events` delivered `server.connected`, and
stopping the gateway stopped its child. The build's service check passed the same way.
Nothing has yet run in a Daytona Sandbox.

## Deliberate Limits

- `/api/event` is volatile by OpenCode's contract: events during a disconnection are
  lost and the gateway does not replay them. A client catches up by subscribing first,
  then reading snapshots (messages, active sessions, pending permissions, inbox). The
  durable session log (`/api/experimental/session/{id}/log`) returns no events in
  2.2.7.
- Only the death of a service this gateway started can be explained. A service
  someone else started ends streams with a plain 1012.
- Events reach Dogwalk over the WebSocket; SSE through Daytona's preview proxy
  (`/api/event` via the proxy) is untested.
- The gateway injects the service's password into every request it forwards, so it
  has no authentication of its own: anyone who can reach port 8765 controls the
  Agent's API, which means running commands and editing files as `daytona`. Today that
  is whoever holds a valid token for the private Daytona preview, plus any process in
  the Sandbox. Making the preview public would hand the Agent to the internet. Inside
  the Sandbox this is no new exposure, since the same user can read `service.json`
  and talk to the service directly. The password itself never leaves the Sandbox.
- The native API is a vendor surface (V1 to V2 broke it; `log` and `client-mcp` are
  experimental), so the OpenCode pin in `build_sandbox_snapshot.py` moves only
  deliberately.
