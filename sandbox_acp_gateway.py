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
import socket
import sys
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


class AcpGateway:
    """Own one WebSocket-to-stdio ACP Agent connection at a time."""

    def __init__(self, workspace: Path, agent_command: str) -> None:
        self.workspace = workspace.resolve()
        self.agent_command = agent_command
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
            process = await asyncio.create_subprocess_exec(
                *command_arguments(self.agent_command, self.workspace),
                cwd=self.workspace,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_MESSAGE_BYTES + 1,
            )
            self._process = process
            await self._bridge(websocket, process)
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
        self, websocket: web.WebSocketResponse, process: asyncio.subprocess.Process
    ) -> None:
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None

        async def websocket_to_agent() -> None:
            async for message in websocket:
                if message.type == WSMsgType.TEXT:
                    encoded = message.data.encode("utf-8")
                    if len(encoded) > MAX_MESSAGE_BYTES:
                        await websocket.close(code=1009, message=b"Message too large")
                        return
                    process.stdin.write(encoded + b"\n")
                    await process.stdin.drain()
                elif message.type == WSMsgType.ERROR:
                    raise websocket.exception() or RuntimeError("WebSocket failed")

        async def agent_to_websocket() -> None:
            while line := await process.stdout.readline():
                if len(line) > MAX_MESSAGE_BYTES:
                    raise ValueError("ACP Agent message exceeds the size limit")
                payload = line.rstrip(b"\r\n")
                if payload:
                    await websocket.send_str(payload.decode("utf-8"))

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
            for task in done:
                exception = task.exception()
                if exception is not None:
                    raise exception
            if exited in done and process.returncode:
                raise RuntimeError(f"ACP Agent exited with status {process.returncode}")
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
        message = json.loads(line)
        print("fake agent diagnostic", file=sys.stderr, flush=True)
        print(json.dumps(message, separators=(",", ":")), flush=True)


async def run_test() -> None:
    script = Path(__file__).resolve()
    fake_command = shlex.join([sys.executable, str(script), "fake-agent"])
    gateway = AcpGateway(Path.cwd(), fake_command)
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
    finally:
        await runner.cleanup()
    print("sandbox ACP gateway: 8 checks passed")


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
            "DOGWALK_AGENT_COMMAND", "opencode acp --pure --cwd {cwd}"
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
