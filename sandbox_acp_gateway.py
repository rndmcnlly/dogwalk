#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.12,<4"]
# ///
"""Expose one sandbox-local ACP Agent over the ACP WebSocket transport."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import shlex
import shutil
import signal
import socket
import sys
import re
import tempfile
import time
import uuid
from pathlib import Path
from aiohttp import ClientSession, WSMsgType, WSServerHandshakeError, web


LOG = logging.getLogger("acp_gateway")
MAX_MESSAGE_BYTES = 1024 * 1024


def command_arguments(command: str, workspace: Path) -> list[str]:
    return [part.replace("{cwd}", str(workspace)) for part in shlex.split(command)]


def executable_available(command: str, workspace: Path) -> bool:
    arguments = command_arguments(command, workspace)
    if not arguments:
        return False
    executable = Path(arguments[0]).expanduser()
    if executable.is_absolute() or "/" in arguments[0]:
        if not executable.is_absolute():
            executable = workspace / executable
        return executable.is_file() and os.access(executable, os.X_OK)
    return shutil.which(arguments[0]) is not None


# Why the Agent process ended, read from cgroup v2. The gateway's own cgroup and its
# ancestors are checked, because the memory limit may sit on a parent (the container).
def cgroup_dirs(root: Path, proc_cgroup: Path) -> list[Path]:
    try:
        relative = next(
            line[3:] for line in proc_cgroup.read_text().splitlines() if line.startswith("0::")
        ).strip()
    except (OSError, StopIteration):
        relative = "/"
    current = root / relative.lstrip("/")
    found = []
    while True:
        if (current / "memory.events").is_file():
            found.append(current)
        if current == root or root not in current.parents:
            return found
        current = current.parent


def oom_kills(dirs: list[Path]) -> dict[Path, int]:
    counts = {}
    for directory in dirs:
        with contextlib.suppress(OSError, ValueError):
            for line in (directory / "memory.events").read_text().splitlines():
                key, _, value = line.partition(" ")
                if key == "oom_kill":
                    counts[directory] = int(value)
    return counts


def memory_limit(dirs: list[Path]) -> int | None:
    limits = []
    for directory in dirs:
        with contextlib.suppress(OSError, ValueError):
            limits.append(int((directory / "memory.max").read_text().strip()))
    return min(limits) if limits else None


def agent_termination(
    returncode: int | None, before: dict[Path, int], after: dict[Path, int], limit: int | None
) -> dict:
    """A small, neutral account of how the Agent process ended, for the ACP client."""
    result: dict = {"reason": "exited", "exit_status": returncode}
    if returncode is not None and returncode < 0:
        with contextlib.suppress(ValueError):
            result["signal"] = signal.Signals(-returncode).name
        result["reason"] = "killed"
    # A wrapper (opencode acp runs a server child) can exit 1 after its child is OOM-killed,
    # so the counter decides, not the signal.
    if any(after.get(directory, 0) > count for directory, count in before.items()):
        result["reason"] = "out_of_memory"
    if limit is not None:
        result["limit_bytes"] = limit
    if result["reason"] == "out_of_memory":
        size = f" ({limit / 2**30:.3g} GiB limit)" if limit else ""
        result["detail"] = f"The ACP Agent was killed because the sandbox ran out of memory{size}."
    elif result["reason"] == "killed":
        result["detail"] = f"The ACP Agent was killed by {result.get('signal', 'a signal')}."
    else:
        result["detail"] = f"The ACP Agent exited with status {returncode}."
    return result


def request_key(message: object) -> str | None:
    if isinstance(message, dict) and "id" in message:
        return json.dumps(message["id"])
    return None


TRACEPARENT = re.compile(r"00-(?!0{32})([0-9a-f]{32})-(?!0{16})([0-9a-f]{16})-[0-9a-f]{2}")
LOG_TOKEN = re.compile(r"[A-Za-z0-9_./-]{1,64}")


def trace_fields(message: object) -> dict | None:
    """The W3C trace context a client message carries in `_meta.traceparent` (ACP extensibility),
    as metadata only. None when there is no traceparent; an invalid one is reported, never echoed."""
    if not isinstance(message, dict) or not isinstance(message.get("params"), dict):
        return None
    meta = message["params"].get("_meta")
    if not isinstance(meta, dict) or "traceparent" not in meta:
        return None
    # Only bounded scalars reach the log: an id or method of another shape, or oversized, is a marker.
    rpc_id, method = message.get("id"), message.get("method")
    fields: dict = {
        "rpc_id": rpc_id if (isinstance(rpc_id, int) and not isinstance(rpc_id, bool))
        or (isinstance(rpc_id, str) and LOG_TOKEN.fullmatch(rpc_id)) else "invalid",
        "method": method if isinstance(method, str) and LOG_TOKEN.fullmatch(method) else "invalid",
    }
    # fullmatch, not match: `$` would accept a trailing newline the Worker's parser rejects.
    match = TRACEPARENT.fullmatch(meta["traceparent"]) if isinstance(meta["traceparent"], str) else None
    if match:
        fields.update(trace_id=match[1], span_id=match[2])
    else:
        fields["traceparent"] = "invalid"
    return fields


class AcpGateway:
    """Own one WebSocket-to-stdio ACP Agent connection at a time."""

    def __init__(
        self,
        workspace: Path,
        agent_command: str,
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        proc_cgroup: Path = Path("/proc/self/cgroup"),
    ) -> None:
        self.workspace = workspace.resolve()
        self.agent_command = agent_command
        self.cgroups = cgroup_dirs(cgroup_root, proc_cgroup)
        self._connection_lock = asyncio.Lock()
        self._active = False
        self._process: asyncio.subprocess.Process | None = None

    async def health(self, request: web.Request) -> web.Response:
        process = self._process
        return web.json_response(
            {
                "ok": True,
                "connection": "active" if self._active else "idle",
                "agent": "running"
                if process is not None and process.returncode is None
                else "stopped",
            }
        )

    async def ready(self, request: web.Request) -> web.Response:
        workspace_ready = self.workspace.is_dir()
        agent_ready = executable_available(self.agent_command, self.workspace)
        status = 200 if workspace_ready and agent_ready else 503
        return web.json_response(
            {
                "ok": status == 200,
                "workspace": "ready" if workspace_ready else "missing",
                "agent_executable": "ready" if agent_ready else "missing",
            },
            status=status,
        )

    async def websocket(self, request: web.Request) -> web.StreamResponse:
        connection_id = str(uuid.uuid4())
        websocket = web.WebSocketResponse(
            max_msg_size=MAX_MESSAGE_BYTES,
            heartbeat=30,
        )
        websocket.headers["Acp-Connection-Id"] = connection_id

        async with self._connection_lock:
            if self._active:
                return web.json_response(
                    {"error": "An ACP connection is already active."}, status=409
                )
            self._active = True

        try:
            await websocket.prepare(request)
            LOG.info("connection_opened connection_id=%s", connection_id)
            oom_before = oom_kills(self.cgroups)
            process = await asyncio.create_subprocess_exec(
                *command_arguments(self.agent_command, self.workspace),
                cwd=self.workspace,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_MESSAGE_BYTES + 1,
            )
            self._process = process
            await self._bridge(websocket, process, oom_before, connection_id)
        except Exception:
            LOG.exception("connection_failed connection_id=%s", connection_id)
            if not websocket.closed:
                await websocket.close(code=1011, message=b"ACP Agent connection failed")
        finally:
            try:
                if self._process is not None:
                    await self._stop_process(self._process)
            finally:
                self._process = None
                async with self._connection_lock:
                    self._active = False
            LOG.info("connection_closed connection_id=%s", connection_id)
        return websocket

    async def _bridge(
        self,
        websocket: web.WebSocketResponse,
        process: asyncio.subprocess.Process,
        oom_before: dict[Path, int],
        connection_id: str,
    ) -> None:
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        # Client requests the Agent has not answered, so its end can answer them instead.
        pending: dict[str, object] = {}

        def parse(text: str) -> object:
            try:
                return json.loads(text)
            except ValueError:
                return None

        async def websocket_to_agent() -> None:
            async for message in websocket:
                if message.type == WSMsgType.TEXT:
                    received_ms = time.time_ns() // 1_000_000
                    encoded = message.data.encode("utf-8")
                    if len(encoded) > MAX_MESSAGE_BYTES:
                        await websocket.close(code=1009, message=b"Message too large")
                        return
                    parsed = parse(message.data)
                    key = request_key(parsed)
                    if key is not None and "method" in parsed:
                        pending[key] = parsed["id"]
                    # Forwarded as received, `_meta` included: the bridge observes trace
                    # context but is not a policy point, so even an invalid value passes on.
                    process.stdin.write(encoded + b"\n")
                    await process.stdin.drain()
                    if (trace := trace_fields(parsed)) is not None:
                        LOG.info("acp_trace_context %s", json.dumps({
                            "connection_id": connection_id, **trace,
                            "received_ms": received_ms, "forwarded_ms": time.time_ns() // 1_000_000,
                        }, separators=(",", ":")))
                elif message.type == WSMsgType.ERROR:
                    raise websocket.exception() or RuntimeError("WebSocket failed")

        async def agent_to_websocket() -> None:
            while line := await process.stdout.readline():
                if len(line) > MAX_MESSAGE_BYTES:
                    raise ValueError("ACP Agent message exceeds the size limit")
                payload = line.rstrip(b"\r\n")
                if payload:
                    text = payload.decode("utf-8")
                    parsed = parse(text)
                    key = request_key(parsed)
                    if key is not None and "method" not in parsed:
                        pending.pop(key, None)
                    await websocket.send_str(text)

        async def agent_ended() -> None:
            # Drain what the Agent wrote before it went, then say why it went: an error for
            # each unanswered request, and the same reason in the close frame.
            await asyncio.wait({downstream, exited}, timeout=2)
            if downstream.done() and not downstream.cancelled() and downstream.exception():
                raise downstream.exception()
            termination = agent_termination(
                process.returncode, oom_before, oom_kills(self.cgroups), memory_limit(self.cgroups)
            )
            LOG.warning("agent_terminated %s", json.dumps(termination))
            if websocket.closed:
                return
            for request_id in pending.values():
                await websocket.send_str(json.dumps({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32603, "message": termination["detail"], "data": termination},
                }))
            pending.clear()
            if termination["reason"] == "exited" and termination["exit_status"] == 0:
                return
            # A close reason holds at most 123 bytes: the detail stays in the request errors.
            brief = {key: termination[key] for key in ("reason", "signal", "exit_status", "limit_bytes") if key in termination}
            await websocket.close(code=1011, message=json.dumps(brief, separators=(",", ":")).encode())

        async def log_agent_stderr() -> None:
            while line := await process.stderr.readline():
                LOG.warning("agent_stderr %s", line.decode("utf-8", "replace").rstrip())

        upstream = asyncio.create_task(websocket_to_agent())
        downstream = asyncio.create_task(agent_to_websocket())
        stderr = asyncio.create_task(log_agent_stderr())
        exited = asyncio.create_task(process.wait())
        bridge_tasks = {upstream, downstream, exited}
        try:
            done, _ = await asyncio.wait(
                bridge_tasks, return_when=asyncio.FIRST_COMPLETED
            )
            if upstream in done:
                exception = upstream.exception()
                if exception is not None:
                    raise exception
            else:
                await agent_ended()
        finally:
            for task in bridge_tasks | {stderr}:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*bridge_tasks, stderr, return_exceptions=True)
            if not websocket.closed:
                await websocket.close()

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await process.stdin.wait_closed()
        if process.returncode is not None:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()


def create_app(gateway: AcpGateway) -> web.Application:
    app = web.Application(client_max_size=MAX_MESSAGE_BYTES)
    app.add_routes(
        [
            web.get("/healthz", gateway.health),
            web.get("/readyz", gateway.ready),
            web.get("/acp", gateway.websocket),
        ]
    )
    return app


async def fake_agent() -> None:
    while line := await asyncio.to_thread(sys.stdin.buffer.readline):
        if stdin_log := os.environ.get("DOGWALK_TEST_STDIN_LOG"):
            with open(stdin_log, "ab") as file:
                file.write(line)
        message = json.loads(line)
        events = os.environ.get("DOGWALK_TEST_MEMORY_EVENTS")
        if message.get("method") == "session/prompt" and events:
            # Stand in for the kernel: count an OOM kill on the cgroup, then SIGKILL.
            Path(events).write_text("low 0\nhigh 0\nmax 3\noom 1\noom_kill 1\n")
            os.kill(os.getpid(), signal.SIGKILL)
        print("fake agent diagnostic", file=sys.stderr, flush=True)
        print(json.dumps(message, separators=(",", ":")), flush=True)


async def run_test() -> None:
    script = Path(__file__).resolve()
    fake_command = shlex.join([sys.executable, str(script), "fake-agent"])
    # A cgroup v2 tree whose limit sits on the parent of the gateway's own cgroup.
    cgroups = Path(tempfile.mkdtemp(prefix="acp-gateway-cgroup-"))
    (cgroups / "gateway").mkdir()
    (cgroups / "memory.events").write_text("low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n")
    (cgroups / "memory.max").write_text("1073741824\n")
    (cgroups / "gateway" / "memory.events").write_text("oom_kill 0\n")
    (cgroups / "gateway" / "memory.max").write_text("max\n")
    (cgroups / "proc-cgroup").write_text("0::/gateway\n")
    os.environ["DOGWALK_TEST_MEMORY_EVENTS"] = str(cgroups / "memory.events")
    stdin_log = cgroups / "agent-stdin"
    os.environ["DOGWALK_TEST_STDIN_LOG"] = str(stdin_log)
    logged: list[str] = []
    capture = logging.Handler()
    capture.emit = lambda record: logged.append(record.getMessage())
    LOG.addHandler(capture)
    gateway = AcpGateway(Path.cwd(), fake_command, cgroups, cgroups / "proc-cgroup")
    assert gateway.cgroups == [cgroups / "gateway", cgroups]
    runner = web.AppRunner(create_app(gateway))
    await runner.setup()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    site = web.SockSite(runner, listener)
    await site.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        async with ClientSession() as session:
            async with session.get(f"{base_url}/healthz") as response:
                health = await response.json()
                assert response.status == 200 and health["connection"] == "idle"
            async with session.get(f"{base_url}/readyz") as response:
                readiness = await response.json()
                assert response.status == 200 and readiness["ok"] is True

            websocket = await session.ws_connect(f"{base_url}/acp")
            connection_id = websocket._response.headers.get("Acp-Connection-Id")
            assert connection_id
            try:
                await session.ws_connect(f"{base_url}/acp")
            except WSServerHandshakeError as error:
                assert error.status == 409
            else:
                raise AssertionError("A second ACP connection was accepted")

            initialize = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": 1},
            }
            await websocket.send_json(initialize)
            echoed = await websocket.receive_json(timeout=3)
            assert echoed == initialize

            await websocket.send_bytes(b"ignored")
            follow_up = {"jsonrpc": "2.0", "method": "initialized"}
            await websocket.send_json(follow_up)
            echoed = await websocket.receive_json(timeout=3)
            assert echoed == follow_up

            # Trace context in `_meta` reaches the Agent byte for byte, beside unrelated keys,
            # and the bridge logs metadata about it; an invalid value is logged and still sent.
            trace_id, span_id = "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7"
            traced = ('{"jsonrpc":"2.0","id":"t-1","method":"session/new","params":{"cwd":"/w",'
                      f'"_meta": {{"vendor.example/x": [1, 2], "traceparent": "00-{trace_id}-{span_id}-01"}}}}}}')
            invalid = ('{"jsonrpc":"2.0","id":2,"method":"session/list","params":'
                       '{"_meta":{"traceparent":"00-SECRET-not-a-span-01"}}}')
            for text in (traced, invalid):
                await websocket.send_str(text)
                await websocket.receive_json(timeout=3)
            assert stdin_log.read_bytes().splitlines()[-2:] == [traced.encode(), invalid.encode()]
            records = [json.loads(line.split(" ", 1)[1]) for line in logged if line.startswith("acp_trace_context ")]
            assert len(records) == 2, logged
            valid_record, invalid_record = records
            assert valid_record.keys() == {"connection_id", "rpc_id", "method", "trace_id", "span_id", "received_ms", "forwarded_ms"}
            assert (valid_record["connection_id"], valid_record["rpc_id"], valid_record["method"]) == (connection_id, "t-1", "session/new")
            assert (valid_record["trace_id"], valid_record["span_id"]) == (trace_id, span_id)
            assert valid_record["forwarded_ms"] >= valid_record["received_ms"] > 0
            assert invalid_record["traceparent"] == "invalid" and "trace_id" not in invalid_record
            assert (invalid_record["rpc_id"], invalid_record["method"]) == (2, "session/list")
            assert "SECRET" not in "\n".join(logged) and "vendor.example" not in "\n".join(logged)
            await websocket.close()

            for _ in range(30):
                try:
                    replacement = await session.ws_connect(f"{base_url}/acp")
                    await replacement.close()
                    break
                except WSServerHandshakeError as error:
                    assert error.status == 409
                    await asyncio.sleep(0.1)
            else:
                raise AssertionError("ACP connection slot was not released")

            # The Agent is OOM-killed mid-turn: its unanswered request gets a structured
            # error, and the close frame carries the same reason.
            for _ in range(30):
                try:
                    websocket = await session.ws_connect(f"{base_url}/acp")
                    break
                except WSServerHandshakeError:
                    await asyncio.sleep(0.1)
            await websocket.send_json({"jsonrpc": "2.0", "id": 7, "method": "session/prompt", "params": {}})
            failed = await websocket.receive_json(timeout=5)
            assert failed["id"] == 7, failed
            data = failed["error"]["data"]
            assert data["reason"] == "out_of_memory" and data["signal"] == "SIGKILL", data
            assert data["limit_bytes"] == 1073741824 and "1 GiB" in failed["error"]["message"], failed
            closing = await websocket.receive(timeout=5)
            assert closing.type == WSMsgType.CLOSE and websocket.close_code == 1011, closing
            assert json.loads(closing.extra)["reason"] == "out_of_memory", closing
    finally:
        LOG.removeHandler(capture)
        await runner.cleanup()
        shutil.rmtree(cgroups, ignore_errors=True)
    # Same acceptance rule as the Worker's parseTraceparent, and only bounded scalars logged.
    good = "00-" + "4bf92f3577b34da6a3ce929d0e0e4736" + "-" + "00f067aa0ba902b7" + "-01"
    traced = lambda value, **top: trace_fields({"jsonrpc": "2.0", "id": 1, "method": "session/new", **top,
                                                "params": {"_meta": {"traceparent": value}}})
    assert traced(good)["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    for bad in (good + "\n", good + "\r", good.upper(), "00-" + "0" * 32 + "-00f067aa0ba902b7-01",
                good[:36] + "0" * 16 + good[52:], "ff" + good[2:], good[:-2] + "0g", 7):
        assert traced(bad)["traceparent"] == "invalid" and "trace_id" not in traced(bad), bad
    secret = "SECRET_SENTINEL_" + "x" * 80
    odd = traced(good, id={"nested": secret}, method=secret)
    assert odd["rpc_id"] == "invalid" and odd["method"] == "invalid" and secret not in json.dumps(odd), odd
    assert traced(good, id=True)["rpc_id"] == "invalid" and traced(good, id="req-7")["rpc_id"] == "req-7"
    print("sandbox ACP gateway: 22 checks passed, plus traceparent and log-field rules")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subcommands = result.add_subparsers(dest="command")
    serve = subcommands.add_parser("serve", help="Serve the ACP WebSocket gateway")
    serve.add_argument(
        "--host", default=os.environ.get("DOGWALK_ACP_GATEWAY_HOST", "0.0.0.0")
    )
    serve.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("DOGWALK_ACP_GATEWAY_PORT", "8765")),
    )
    serve.add_argument(
        "--workspace",
        type=Path,
        default=Path(os.environ.get("DOGWALK_WORKSPACE", Path.cwd())),
    )
    serve.add_argument(
        "--agent-command",
        default=os.environ.get(
            "DOGWALK_AGENT_COMMAND", "opencode acp"
        ),
    )
    subcommands.add_parser("test", help="Run the transport integration test")
    subcommands.add_parser("fake-agent", help=argparse.SUPPRESS)
    return result


def main() -> None:
    arguments = parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if arguments.command == "test":
        asyncio.run(run_test())
        return
    if arguments.command == "fake-agent":
        asyncio.run(fake_agent())
        return
    if arguments.command != "serve":
        parser().error("a command is required")
    gateway = AcpGateway(arguments.workspace, arguments.agent_command)
    web.run_app(create_app(gateway), host=arguments.host, port=arguments.port)


if __name__ == "__main__":
    main()
