# Sandbox ACP Gateway

Status: first production component.

The current snapshot is `dogwalk-acp-20261108-v1`, built reproducibly by
`build_sandbox_snapshot.py`; production adopts it through `DAYTONA_SNAPSHOT` in
`cf_worker/wrangler.toml`.

`sandbox_acp_gateway.py` exposes a sandbox-local ACP Agent through the
WebSocket profile of ACP's active Streamable HTTP and WebSocket Transport RFD.
It is a transport adapter only: it does not interpret ACP messages, manage
sessions, construct prompts, or know about Voice Agent narration.

## Boundary

```text
Dogwalk ACP Client
    |
    | ACP JSON-RPC in WebSocket text frames
    v
GET /acp
    |
    | ACP JSON-RPC, newline-delimited over stdio
    v
ACP Agent Implementation (currently OpenCode)
```

One gateway accepts one active Agent Connection and owns one Agent subprocess.
That connection may multiplex many ACP Sessions. A second WebSocket receives
HTTP 409. When the WebSocket disconnects, the gateway closes Agent stdin and
terminates the process if it does not exit promptly. Agent-persisted session
history can be loaded on a later connection; in-flight messages are not replayed.

The gateway forwards text frames without changing their JSON-RPC content,
ignores binary frames as required by the RFD, drains Agent stderr separately,
and caps each message at 1 MiB.

## Run

Inside a sandbox:

```bash
uv run --script sandbox_acp_gateway.py serve \
  --workspace /home/daytona \
  --agent-command 'opencode acp'
```

Defaults are `0.0.0.0:8765`, the current directory as Workspace, and
`opencode acp`. OpenCode 2 runs a private in-process server per `acp` process,
takes each session's directory from `cwd` in `session/new`, and rejects the
1.x `--pure` and `--cwd` flags. The corresponding environment variables are
`DOGWALK_ACP_GATEWAY_HOST`, `DOGWALK_ACP_GATEWAY_PORT`, `DOGWALK_WORKSPACE`, and
`DOGWALK_AGENT_COMMAND`. The product codename namespaces configuration; it does
not change ACP vocabulary.

Endpoints:

| Endpoint | Contract |
|---|---|
| `GET /healthz` | Gateway process, connection, and child-process state |
| `GET /readyz` | Workspace and configured Agent executable are present |
| `GET /acp` with WebSocket upgrade | One full-duplex ACP Agent Connection |

`/readyz` does not start or initialize the Agent. The remote ACP Client must
complete `initialize`; that round trip is the authoritative protocol readiness
check.

## Daytona

Expose port 8765 only through a non-public Daytona standard preview URL. The
control plane sends the fresh `x-daytona-preview-token` returned after each
sandbox start and `X-Daytona-Skip-Preview-Warning: true`. Do not use a signed
browser URL for the ACP endpoint.

The snapshot installs OpenCode `2.2.5` from its GitHub release into
`/usr/local/bin`. OpenCode 1.x through `1.18.4` served the 2026-07 deployments;
2.x prints `opencode v2.2.5` for `--version`, which the build check accounts for.

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
The Agent can still *use* the key against the allowlisted host; scope it to a
dedicated OpenAI project with a budget. Daytona already points
`NODE_EXTRA_CA_CERTS` and `SSL_CERT_FILE` at its proxy CA, and OpenCode needed
no TLS changes. Verified 2026-11-07: a fresh Sandbox from `-v2` completed a real
ACP Prompt Turn (`end_turn`) through the gateway.

### Web search

`sandbox_opencode.json` sets `"websearch": {"provider": "exa"}`. Without a
configured provider, OpenCode's first `websearch` asks the Client to choose one
through an ACP Elicitation; Dogwalk advertises no Elicitation capability, so the
tool failed with "Web search cancelled". Exa's hosted MCP endpoint
(`mcp.exa.ai`) works without a key; OpenCode sends one only if `EXA_API_KEY` is
set, which would raise its rate limits. TinyFish also has a keyless mode and is
the fallback if Exa starts refusing anonymous searches. The existing Sandbox
received the key in place on 2026-11-08 and an `opencode run` there completed a
`websearch` with results. Snapshot `dogwalk-acp-20261108-v1` predates it, so a
fresh Sandbox needs the next snapshot or the same in-place edit.

### Binary guard

OpenCode 2.2.5's `read` returns PNG, JPEG, GIF, WebP, and PDF files up to 20 MiB
as base64 data URIs. On 2026-11-08 an 8.3 MiB PDF became an 11.7 MB tool event,
over the 10 MiB SSE limit between `opencode acp` and its server; the Prompt Turn
failed and the session history kept the payload. `sandbox_opencode_binary_guard.ts`,
installed as `~/.config/opencode/plugins/binary-guard.ts` (discovered without a
config entry), wraps the built-in `read` executor through `ctx.tool.transform`,
so it decides before anything is loaded or encoded. Text files and PNG, JPEG,
GIF, or WebP images up to 2 MiB pass through unchanged. Any other binary,
including every PDF, returns a clue of a few KB: size, `file(1)` type, a 64-byte
hex dump with ASCII, and commands to consume it (`pdftotext`, `pdftoppm`,
`ffmpeg` downscaling, `unzip -l`, `tar tf`, `sqlite3`, `ffprobe`, `xxd`), each
naming its apt package. The guard reads the path itself, so it does not consult
OpenCode's external-directory permission; the Agent already has shell and sudo.
The snapshot build adds `file poppler-utils unzip sqlite3 xxd`. The existing
Sandbox received the plugin and those packages (except `xxd`, already present) in
place on 2026-11-08; an `opencode run` there read the 8.3 MiB PDF as a 3.2 KB
event, a small PNG as an image, and a text file unchanged. Snapshot
`dogwalk-acp-20261108-v1` predates it.

The 2026-07 free-model bootstrap (`opencode/deepseek-v4-flash-free`) ended when
that model left the free lineup.

If a Sandbox is manually deleted, the next call invalidates its stale provider
ID, searches once more by identity label, and provisions a fresh Sandbox from
the configured snapshot. The phone Registration remains valid, but deletion is
destructive to the old Workspace and Agent-held ACP Session history.

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

Run the self-contained fake-Agent transport test locally or in a sandbox:

```bash
uv run --script sandbox_acp_gateway.py test
```

It checks health, readiness, the connection ID header, single-connection
exclusion, bidirectional text forwarding, binary-frame handling, subprocess
stderr isolation, and connection-slot recovery. The first Daytona deployment
also passed a real authenticated-preview `initialize` and `session/new` round
trip against OpenCode `1.18.4`, and snapshot `dogwalk-acp-20261107-v2` passed a
full in-sandbox Prompt Turn against OpenCode `2.2.5`.

## Deliberate Limits

- WebSocket only; the Streamable HTTP/SSE profile is not implemented.
- One active Agent Connection per gateway.
- No transport-level message replay after disconnect.
- Authentication is delegated to the private Daytona preview proxy.
- The transport RFD is active rather than released SDK surface, so wire changes
  must be tracked explicitly.
