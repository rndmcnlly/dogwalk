#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp", "agent-client-protocol==0.11.0"]
# ///
"""Real ACP backend for the voice-bridge protocol.

This is a drop-in replacement for ``voice_bridge.py mock-backend``. It exposes
the same HTTP surface (``/manifest``, ``/tool/:name``, ``/events`` SSE) but
tool dispatch and async notifications are driven by a real
``webrtc_spike.SessionManager`` that spawns ``opencode acp`` subprocesses and
speaks ACP. Dogs retain their sessions, stream real activity, surface real
permission requests and elicitations, and stop with real ACP stop reasons.

The adapter's job is to bridge two shapes:

- ``webrtc_spike.SessionManager`` is *poll-based*: the browser drains
  ``take_turn_results``, ``take_attention_requests``, and ``timers.take_due``
  on a polling loop.
- ``voice_bridge.py simulate`` is *push-based*: it listens on a single SSE
  stream and injects each ``{message, speak}`` event into the Realtime
  conversation.

An event pump drains the poll surfaces every ~200ms and fans each new event
out to every SSE subscriber, mirroring the exact notification wording the
browser injects (see webrtc_spike.html pullDogEvents / pullDecisions /
pullDueTimers).

Quick start:

  # Terminal 1: start the real ACP backend (defaults to port 8799)
  uv run --script voice_acp_backend.py --workspace ~/someproject

  # Terminal 2: run the bridge in text simulate mode against it
  uv run --script voice_bridge.py simulate --backend http://127.0.0.1:8799

  # Type: "sic Rex on reading the README"
  # Rex's opencode acp subprocess starts, streams activity, and the pump
  # pushes a completion notification back through SSE once it stops.

Protocol (see voice_bridge.py):
  GET  /manifest           -> {instructions, tools[], voice, greeting}
  POST /tool/:name         -> {call_id, args} -> {result} or {error}
  GET  /events?call_id=... -> SSE stream of {type, message, speak}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from aiohttp import web

ROOT = Path(__file__).parent

ACP_VOICE = "cedar"
ACP_GREETING = "I'll be your dog walker for today. What are we working on?"
PUMP_INTERVAL = 0.2


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class AcpBackend:
    """Expose one real SessionManager through the voice-bridge protocol."""

    def __init__(self, log: Any, manager: Any, timers: Any) -> None:
        from webrtc_spike import INSTRUCTIONS, TOOLS

        self.log = log
        self.manager = manager
        self.timers = timers
        self.instructions = INSTRUCTIONS
        self.tools = TOOLS
        self.subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in self.subscribers:
            queue.put_nowait(event)

    async def pump(self) -> None:
        """Drain poll surfaces and fan out as SSE notifications.

        Drains only while at least one subscriber is connected, so events
        accumulate in the manager (mirroring the browser-poll semantics) until
        a voice_bridge simulate session is listening.
        """
        while True:
            await asyncio.sleep(PUMP_INTERVAL)
            if not self.subscribers:
                continue
            for result in self.manager.take_turn_results():
                name = result.get("name", "A Dog")
                status = result.get("status", "resting")
                report = result.get("report", "")
                message = (
                    f"{name} finished with status {status}. Report: {report} "
                    "Give the User a brief plain-language result now."
                )
                self.publish(
                    {"type": "dog_completed", "message": message, "speak": True}
                )
            for decision in self.manager.take_attention_requests():
                self.publish(
                    {
                        "type": "decision_needed",
                        "message": self._decision_message(decision),
                        "speak": True,
                        "decision_id": decision.get("decision_id"),
                        "kind": decision.get("kind"),
                    }
                )
            for timer in self.timers.take_due():
                purpose = timer.get("purpose", "a timer")
                seconds = timer.get("seconds", 0)
                message = (
                    f'The {seconds}-second timer for "{purpose}" has ended. '
                    "Tell the User it is due. Checking Dog status is read-only, "
                    "so check a relevant Dog now if that would make the update useful."
                )
                self.publish({"type": "timer_due", "message": message, "speak": True})

    @staticmethod
    def _decision_message(decision: dict[str, Any]) -> str:
        dog = decision.get("dog", "A Dog")
        kind = decision.get("kind", "decision")
        message = decision.get("message", "")
        decision_id = decision.get("decision_id", "")
        if kind == "permission":
            options = decision.get("options", [])
            detail = "Options: " + "; ".join(
                f"{opt.get('option_id')}: {opt.get('name')}" for opt in options
            ) + "."
        else:
            schema = decision.get("schema")
            detail = f"Requested answer schema: {json.dumps(schema)}."
        return (
            f"{dog} needs a {kind} decision. {message} {detail} "
            f"Ask the User briefly, then use the matching decision tool "
            f"with decision_id {decision_id}."
        )

    async def handle_manifest(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "instructions": self.instructions,
                "tools": self.tools,
                "voice": ACP_VOICE,
                "greeting": ACP_GREETING,
            }
        )

    async def handle_tool(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        body = await request.json()
        call_id = body.get("call_id", "unknown")
        args = body.get("args", {})
        self.log.write("tool_call", tool=name, call_id=call_id, args=args)
        try:
            if name == "set_timer":
                result = self.timers.set(**args)
            elif name == "end_call":
                result = {"ok": True, "end_call": True, "delay_ms": 2000}
            else:
                result = await asyncio.to_thread(self.manager.dispatch, name, args)
        except Exception as exc:
            self.log.write("tool_error", tool=name, error=str(exc))
            return web.json_response(
                {"error": f"{type(exc).__name__}: {exc}"}, status=500
            )
        self.log.write("tool_result", tool=name, call_id=call_id, result=result)
        return web.json_response({"result": result})

    async def handle_events(self, request: web.Request) -> web.StreamResponse:
        call_id = request.query.get("call_id", "default")
        response = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )
        await response.prepare(request)
        queue = self.subscribe()
        self.log.write("sse_opened", call_id=call_id)
        try:
            while True:
                event = await queue.get()
                await response.write(f"data: {json.dumps(event)}\n\n".encode())
                self.log.write("sse_sent", call_id=call_id, event=event)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            self.unsubscribe(queue)
            self.log.write("sse_closed", call_id=call_id)
        return response


def main() -> None:
    from webrtc_spike import SessionLog, SessionManager, TimerQueue

    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("DOGWALK_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("DOGWALK_PORT", "8799"))
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(os.environ.get("DOGWALK_WORKSPACE", Path.cwd())),
    )
    parser.add_argument(
        "--agent-command",
        default=os.environ.get(
            "DOGWALK_AGENT_COMMAND", "opencode acp --pure --cwd {cwd}"
        ),
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(os.environ.get("DOGWALK_LOG_DIR", ROOT / "logs")),
    )
    args = parser.parse_args()

    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise SystemExit(f"Workspace is not a directory: {workspace}")
    if not args.agent_command.strip():
        raise SystemExit("DOGWALK_AGENT_COMMAND is empty.")

    log = SessionLog(directory=args.log_dir.expanduser().resolve(), mode="acp-backend")
    manager = SessionManager(log, workspace, agent_command=args.agent_command)
    timers = TimerQueue(log)
    backend = AcpBackend(log, manager, timers)

    app = web.Application()
    app["acp_backend"] = backend
    app.router.add_get("/manifest", backend.handle_manifest)
    app.router.add_post("/tool/{name}", backend.handle_tool)
    app.router.add_get("/events", backend.handle_events)

    async def start_pump(app: web.Application) -> None:
        app["pump_task"] = asyncio.create_task(backend.pump())

    async def stop_pump(app: web.Application) -> None:
        task: asyncio.Task = app["pump_task"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app.on_startup.append(start_pump)
    app.on_cleanup.append(stop_pump)

    log.write(
        "service_start",
        mode="acp-backend",
        host=args.host,
        port=args.port,
        workspace=str(workspace),
        agent_command=args.agent_command,
    )
    print(f"Dogwalk ACP backend: http://{args.host}:{args.port}")
    print(f"Workspace: {workspace}")
    print(f"Agent command: {args.agent_command}")
    print(f"Log: {log.path}")
    print(
        f"Run: uv run --script voice_bridge.py simulate "
        f"--backend http://{args.host}:{args.port}"
    )
    try:
        web.run_app(app, host=args.host, port=args.port, print=None)
    finally:
        manager.close()
        log.write("service_stop")
        log.file.close()


if __name__ == "__main__":
    main()
