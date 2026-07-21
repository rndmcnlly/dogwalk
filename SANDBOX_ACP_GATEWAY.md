# Sandbox ACP Gateway

Status: first production component.

The active production snapshot is `dogwalk-acp-20260721-v3`, built reproducibly
by `build_sandbox_snapshot.py`.

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
  --agent-command '/home/daytona/.opencode/bin/opencode acp --pure --cwd {cwd}'
```

Defaults are `0.0.0.0:8765`, the current directory as Workspace, and
`opencode acp --pure --cwd {cwd}`. The corresponding environment variables are
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

Provision a tested OpenCode version into a user-writable path and configure its
absolute path. The Daytona image tested on 2026-07-20 supplied OpenCode `1.1.35`,
which closed on the current ACP v1 handshake. `opencode upgrade` selected a
root-owned npm installation, printed a permissions failure, and still exited
zero. `opencode upgrade -m curl` installed working version `1.18.4` at
`/home/daytona/.opencode/bin/opencode`.

### Initial inference model

The initial snapshot needs no inference credential. Configure OpenCode with a
currently free Zen model, initially `opencode/deepseek-v4-flash-free`. On
2026-07-20 a clean Daytona sandbox with OpenCode `1.18.4`, zero entries in
`auth.json`, and no inference environment variable completed a real prompt with
that model.

Free availability is temporary and prompts may be used for provider improvement.
This bootstrap tier is not appropriate for confidential repositories. The
snapshot's pinned model must be monitored and updated when the free lineup
changes. If Dogwalk later uses a credentialed provider, mount its key through an
allowlisted Daytona Secret rather than baking it into the snapshot or workspace.

If a Sandbox is manually deleted, the next call invalidates its stale provider
ID, searches once more by identity label, and provisions a fresh Sandbox from
the configured snapshot. The phone Registration remains valid, but deletion is
destructive to the old Workspace and Agent-held ACP Session history.

This is an operator-recovery path, not routine lifecycle. User sandboxes normally
stop and archive in place, retaining their filesystem and identity indefinitely.

Snapshot v3 grants the isolated `daytona` user passwordless `sudo` and includes
`ping`, DNS, and socket diagnostics. It also installs the provider-neutral global
OpenCode tools `publish_review_bundle` and `register_ephemeral_service`. Their
Sandbox-scoped capability is provisioned after creation and is not present in the
snapshot.

## Verification

Run the self-contained fake-Agent transport test locally or in a sandbox:

```bash
uv run --script sandbox_acp_gateway.py test
```

It checks health, readiness, the connection ID header, single-connection
exclusion, bidirectional text forwarding, binary-frame handling, subprocess
stderr isolation, and connection-slot recovery. The first Daytona deployment
also passed a real authenticated-preview `initialize` and `session/new` round
trip against OpenCode `1.18.4`.

## Deliberate Limits

- WebSocket only; the Streamable HTTP/SSE profile is not implemented.
- One active Agent Connection per gateway.
- No transport-level message replay after disconnect.
- Authentication is delegated to the private Daytona preview proxy.
- The transport RFD is active rather than released SDK surface, so wire changes
  must be tracked explicitly.
