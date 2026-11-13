#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets==15.0.1", "aiohttp==3.12.15"]
# ///
"""End-to-end local test of GPT-Live client delegation, the Session Orchestrator, and the Agent."""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import WSMsgType, web
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from http.server import BaseHTTPRequestHandler

from smoke_test import (
    DAYTONA_MOCK_PORT,
    INSPECTOR_PORT,
    PORT_BASE,
    WORKER_PORT,
    DEFAULT_LOCAL,
    DaytonaMock,
    LOCAL_ADMIN_PASSWORD,
    LOCAL_TOKEN,
    TEST_PHONE,
    ThreadingHTTPServer,
    prepare_local_fixture,
    spoken_handle,
    post,
    project_wrangler,
    wait_port,
)

WORKER_DIR = Path(__file__).resolve().parent
# GPT-Live's sideband and the Agent Gateway's preview URL (smoke_test.DaytonaMock) share this port.
SOCKET_PORT = PORT_BASE + 4
ORCHESTRATOR_MOCK_PORT = PORT_BASE + 5
# wrangler dev's output, kept per port base and shown when the suite fails.
WRANGLER_LOG = Path(tempfile.gettempdir()) / f"dogwalk-voice-session-{PORT_BASE}.log"
IDENTITY_SECRET = f"local-identity-secret-{secrets.token_hex(8)}"
OPENAI_WEBHOOK_KEY = secrets.token_bytes(32)
OPENAI_WEBHOOK_SECRET = "whsec_" + base64.b64encode(OPENAI_WEBHOOK_KEY).decode()
TRACEPARENT = re.compile(r"00-(?!0{32})[0-9a-f]{32}-(?!0{16})[0-9a-f]{16}-01")


def now_ms() -> int:
    return int(time.time() * 1000)


class LiveSocket:
    """The attached GPT-Live sideband, as the test drives it."""

    def __init__(self, ws: web.WebSocketResponse) -> None:
        self.ws = ws

    async def send(self, text: str) -> None:
        await self.ws.send_str(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self.ws.close(code=code, message=reason.encode())


class Envelope(dict):
    """A whole response body, for routes whose answer has more than `data` (a page's cursor)."""


class AgentGatewayMock:
    """The Sandbox Agent Gateway in front of a fake OpenCode 2.2.7 (SANDBOX_AGENT_GATEWAY.md):
    the native `/api/*` routes the adapter calls, answered as `{"data": ...}`, and `/events`,
    one OpenCode event per text frame after `server.connected`.

    Prompt Turns are scripted by prompt text (`scripts`); any other prompt starts an execution
    and holds it open for the test to drive with `emit`, `text`, `tool`, `finish`, and
    `close_events`. Like the real stream, events sent while no client listens are lost; the
    snapshots (`active`, sessions, messages, permissions, inbox) are what a client catches up from.
    """

    def __init__(self) -> None:
        self.requests: asyncio.Queue[dict] = asyncio.Queue()
        self.seen: list[dict] = []
        self.events: list[web.WebSocketResponse] = []
        self.events_connected = asyncio.Event()
        self.events_connections = 0
        # The service answers late, so the call starts before the dogs are ready.
        self.hello_delay = 1.5
        # While set, /events upgrades are refused, as while the service restarts.
        self.refuse_events = False
        # The next prompt meets the preview proxy's 401 (an expired preview token), or loses its
        # connection unanswered, which leaves whether OpenCode admitted it unknown.
        self.reject_next_prompt = False
        self.drop_next_prompt = False
        # The next prompt is admitted, then its connection drops before the answer.
        self.drop_admitted_prompt = False
        # This many next interrupts fail at the proxy (502), leaving the turn running.
        self.fail_interrupts = 0
        # One-shot response delays by path, in seconds.
        self.delay_next: dict[str, float] = {}
        # While a list, events are held back here instead of sent (release sends them).
        self.held: list[str] | None = None
        updated = int(datetime(2026, 11, 6, 18, 30, tzinfo=timezone.utc).timestamp() * 1000)
        # Like OpenCode, each session belongs to the directory it was created in.
        self.sessions: dict[str, dict] = {
            PERSISTED_SESSION_ID: {"id": PERSISTED_SESSION_ID, "title": PERSISTED_TITLE,
                                   "location": {"directory": "/home/daytona/parser"}, "time": {"created": updated, "updated": updated}},
            "ses_elsewhere0outside": {"id": "ses_elsewhere0outside", "title": "Other work",
                                      "location": {"directory": "/tmp/elsewhere"}, "time": {"created": updated - 1, "updated": updated - 1}},
        }
        self.active: set[str] = set()
        self.permissions: dict[str, list[dict]] = {}
        self.inbox: dict[str, list[dict]] = {}
        # Tool calls still running, which an interrupt aborts.
        self.open_tools: dict[str, list[str]] = {}
        # Newest first, as `?order=desc` lists them.
        self.messages: dict[str, list[dict]] = {}
        self.reports: dict[str, str] = {}
        self.scripts: dict[str, object] = {}
        self.created = 0
        self.counter = 0
        self.tasks: set[asyncio.Task] = set()

    # --- What the test drives ----------------------------------------------------

    async def emit(self, kind: str, created: int | None = None, **data) -> None:
        sid = data.get("sessionID")
        if kind == "session.tool.input.started":
            self.open_tools.setdefault(sid, []).append(data["id"])
        elif kind in ("session.tool.success", "session.tool.failed"):
            self.open_tools[sid] = [item for item in self.open_tools.get(sid, []) if item != data["id"]]
        elif kind.startswith("session.execution.") and kind != "session.execution.started":
            self.open_tools.pop(sid, None)
        self.counter += 1
        frame = json.dumps({"id": f"evt_{self.counter:06d}", "type": kind, "created": created or now_ms(), "data": data})
        if self.held is not None:
            self.held.append(frame)
            return
        await self.send_frame(frame)

    async def send_frame(self, frame: str) -> None:
        for ws in list(self.events):
            if not ws.closed:
                await ws.send_str(frame)

    async def release(self) -> None:
        """Sends the events held back, in order, as one late burst."""
        held, self.held = self.held or [], None
        for frame in held:
            await self.send_frame(frame)

    async def enqueue(self, sid: str, inbox_id: str, text: str) -> None:
        """An inbox item, as OpenCode 2.2.7 lists it and announces it."""
        item = {"type": "user", "payload": {"text": text}, "delivery": "steer"}
        self.inbox.setdefault(sid, []).append({"id": inbox_id, **item})
        await self.emit("session.inbox.enqueued", sessionID=sid, inboxID=inbox_id, item=item)

    async def close_events(self, code: int, reason: str) -> None:
        if code == 1011:  # an Agent Termination: the service and its tools are gone
            self.open_tools.clear()
        sockets, self.events = self.events, []
        self.events_connected.clear()
        for ws in sockets:
            await ws.close(code=code, message=reason.encode())

    async def start(self, sid: str, inbox_id: str | None = None) -> None:
        self.active.add(sid)
        self.sessions[sid].pop("outcome", None)
        self.reports[sid] = ""
        await self.emit("session.execution.started", sessionID=sid)
        if inbox_id:
            await self.emit("session.inbox.delivered", sessionID=sid, inboxID=inbox_id)
        await self.emit("session.step.started", sessionID=sid)

    async def text(self, sid: str, text: str) -> None:
        self.reports[sid] = self.reports.get(sid, "") + text
        await self.emit("session.text.delta", sessionID=sid, delta=text)

    async def tool(self, sid: str, tool_id: str, name: str, tool_input: dict | None = None, end: str | None = "success") -> None:
        """input.started is the only event naming the tool; called carries its input."""
        await self.emit("session.tool.input.started", sessionID=sid, id=tool_id, name=name)
        if tool_input is not None:
            await self.emit("session.tool.called", sessionID=sid, id=tool_id, input=tool_input)
        if end:
            await self.emit(f"session.tool.{end}", sessionID=sid, id=tool_id)

    def settle(self, sid: str, outcome: str) -> None:
        """A turn's end as the snapshots record it, whether or not anyone hears the event: the
        session's outcome, and an idle entry after the assistant's message, both stamped."""
        self.active.discard(sid)
        self.permissions.pop(sid, None)
        self.sessions[sid]["outcome"] = outcome
        self.sessions[sid]["time"]["updated"] = now_ms()
        self.messages.setdefault(sid, [])[:0] = [
            {"type": "idle", "outcome": outcome, "time": {"created": now_ms()}},
            {"type": "assistant", "content": [{"type": "text", "text": self.reports.get(sid, "")}],
             "finish": "stop" if outcome == "succeeded" else "error", "time": {"created": now_ms()}},
        ]

    async def finish(self, sid: str, outcome: str = "succeeded", **data) -> None:
        await self.emit("session.step.ended", sessionID=sid, tokens={"input": 1200, "output": 80, "cache": {"read": 300, "write": 0}})
        self.settle(sid, outcome)
        await self.emit(f"session.execution.{outcome}", sessionID=sid, **data)

    async def ask_permission(self, sid: str, request_id: str, action: str, resources: list[str], save: list[str]) -> None:
        request = {"id": request_id, "sessionID": sid, "action": action, "resources": resources, "save": save}
        self.permissions.setdefault(sid, []).append(request)
        await self.emit("permission.asked", **request)

    def mark(self) -> None:
        """Forget requests not yet waited for, so the next wait sees only what follows."""
        while not self.requests.empty():
            self.requests.get_nowait()

    async def next_request(self, predicate, timeout: float = 15) -> dict:
        return await next_matching(self.requests, predicate, timeout)

    def session_for(self, directory: str) -> str:
        return next(sid for sid, item in self.sessions.items() if item["location"]["directory"] == directory)

    # --- The gateway ---------------------------------------------------------------

    async def handle(self, request: web.Request) -> web.StreamResponse:
        if request.path == "/events":
            return await self.serve_events(request)
        body = await request.json() if request.can_read_body else None
        record = {"method": request.method, "path": request.path, "query": dict(request.query), "body": body,
                  "traceparent": request.headers.get("traceparent"),
                  "preview_token": request.headers.get("x-daytona-preview-token"), "status": None}
        self.seen.append(record)
        if request.path.endswith("/prompt") and (body or {}).get("delivery") != "steer":
            if self.reject_next_prompt:
                self.reject_next_prompt = False
                record["status"] = 401
                await self.requests.put(record)
                return web.Response(status=401, text="preview token expired")
            if self.drop_next_prompt:
                self.drop_next_prompt = False
                record["status"] = "dropped"
                await self.requests.put(record)
                request.transport.abort()
                return web.Response(status=500)
        if request.path in self.delay_next:
            # Announced on arrival, so the test can act while the answer is held.
            await self.requests.put({**record, "status": "held"})
            await asyncio.sleep(self.delay_next.pop(request.path))
        if request.path.endswith("/interrupt") and self.fail_interrupts:
            self.fail_interrupts -= 1
            record["status"] = 502
            await self.requests.put(record)
            return web.Response(status=502, text="bad gateway")
        status, data = await self.route(request.method, request.path, request.query, body or {})
        record["status"] = status
        if request.path.endswith("/prompt") and self.drop_admitted_prompt:
            self.drop_admitted_prompt = False
            record["status"] = "dropped"
            await self.requests.put(record)
            await asyncio.sleep(0.2)  # Admitted and started; only the answer is lost.
            request.transport.abort()
            return web.Response(status=500)
        await self.requests.put(record)
        if status == 204:
            return web.Response(status=204)
        if status >= 400:
            return web.json_response(data, status=status)
        # /api/info answers at the top level, like OpenCode's (sandbox_agent_gateway.py's fake too).
        if isinstance(data, Envelope) or request.path == "/api/info":
            return web.json_response(dict(data), status=status)
        return web.json_response({"data": data}, status=status)

    async def route(self, method: str, path: str, query, body: dict) -> tuple[int, object]:
        if (method, path) == ("GET", "/api/info"):
            return 200, {"version": "2.2.7"}
        if (method, path) == ("GET", "/api/model/default"):
            return 200, {"id": "gpt-6.1-sol", "limit": {"context": 200_000, "output": 32_000}}
        if (method, path) == ("GET", "/api/session/active"):
            return 200, {sid: {"type": "running"} for sid in self.active}
        if (method, path) == ("POST", "/api/session"):
            directory = body["location"]["directory"]
            self.created += 1
            name = re.sub(r"[^a-z0-9]", "", Path(directory).name.lower()) if directory != "/home/daytona" else "home"
            sid = f"ses_{name}{self.created}test"
            self.sessions[sid] = {"id": sid, "title": "New session", "location": {"directory": directory},
                                  "time": {"created": now_ms(), "updated": now_ms()}}
            return 200, self.sessions[sid]
        if (method, path) == ("GET", "/api/session"):
            # Two per page, newest first, so discovery must follow cursor.next.
            listed = sorted(self.sessions.values(), key=lambda item: -item["time"]["updated"])
            start = int(query.get("cursor") or 0)
            page = {"data": listed[start:start + 2]}
            if start + 2 < len(listed):
                page["cursor"] = {"next": str(start + 2)}
            return 200, Envelope(page)
        match = re.fullmatch(r"/api/session/(ses_[A-Za-z0-9]+)(/.*)?", path)
        if not match:
            return 404, {"_tag": "NotFound", "message": f"no route {method} {path}"}
        sid, rest = match.group(1), match.group(2) or ""
        if sid not in self.sessions:
            return 404, {"_tag": "SessionNotFoundError", "message": f"Session not found: {sid}"}
        session = self.sessions[sid]
        if (method, rest) == ("GET", ""):
            return 200, session
        if (method, rest) == ("GET", "/message"):
            return 200, self.messages.get(sid, [])[:int(query.get("limit") or 50)]
        if (method, rest) == ("GET", "/permission"):
            return 200, self.permissions.get(sid, [])
        if (method, rest) == ("GET", "/inbox"):
            return 200, self.inbox.get(sid, [])
        if method == "DELETE" and rest.startswith("/inbox/"):
            inbox_id = rest.removeprefix("/inbox/")
            items = self.inbox.get(sid, [])
            if not any(item["id"] == inbox_id for item in items):
                return 404, {"_tag": "InboxItemNotFoundError", "message": inbox_id}
            self.inbox[sid] = [item for item in items if item["id"] != inbox_id]
            await self.emit("session.inbox.cancelled", sessionID=sid, inboxID=inbox_id)
            return 204, None
        if (method, rest) == ("POST", "/prompt"):
            self.counter += 1
            inbox_id = f"inb_{self.counter:06d}"
            session["time"]["updated"] = now_ms()
            if body.get("delivery") == "steer" and sid in self.active:
                await self.enqueue(sid, inbox_id, body["text"])
                return 200, {"id": inbox_id}
            # As measured on 2.2.7, steering an idle session starts an execution with it at once.
            self.messages.setdefault(sid, []).insert(0, {"type": "user", "content": [{"type": "text", "text": body["text"]}],
                                                         "time": {"created": now_ms()}})
            await self.emit("session.inbox.enqueued", sessionID=sid, inboxID=inbox_id,
                            item={"type": "user", "payload": {"text": body["text"]}, "delivery": body.get("delivery", "steer")})
            script = self.scripts.get(body["text"], hold_turn) if body.get("delivery") != "steer" else continue_turn
            task = asyncio.create_task(script(self, sid, inbox_id))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return 200, {"id": inbox_id}
        if (method, rest) == ("POST", "/interrupt"):
            # 2.2.7 answers {"interrupted": false} when nothing runs. An interrupt also rejects the
            # Permission Requests the step was waiting on.
            if sid not in self.active:
                return 200, Envelope({"interrupted": False})
            for item in self.permissions.pop(sid, []):
                await self.emit("permission.replied", sessionID=sid, requestID=item["id"], reply="reject")
            # As measured on 2.2.7, the step under way ends as a failed tool whose error says aborted.
            for tool_id in self.open_tools.pop(sid, []):
                await self.emit("session.tool.failed", sessionID=sid, id=tool_id,
                                error={"type": "aborted", "message": "Tool execution interrupted"})
            created = now_ms()
            if query.get("resume") == "true":
                # A Redirect: the step under way stops and the next execution starts in the
                # same millisecond, reading the pending steering at once.
                await self.emit("session.execution.interrupted", created=created, sessionID=sid, reason="user")
                await self.emit("session.execution.started", created=created, sessionID=sid)
                for item in self.inbox.pop(sid, []):
                    await self.emit("session.inbox.delivered", sessionID=sid, inboxID=item["id"])
            else:
                self.settle(sid, "interrupted")
                await self.emit("session.execution.interrupted", created=created, sessionID=sid, reason="user")
            return 200, Envelope({"interrupted": True})
        reply = re.fullmatch(r"/permission/(per_[A-Za-z0-9]+)/reply", rest)
        if method == "POST" and reply:
            pending = self.permissions.get(sid, [])
            if not any(item["id"] == reply.group(1) for item in pending):
                return 404, {"_tag": "PermissionNotFoundError", "message": reply.group(1)}
            self.permissions[sid] = [item for item in pending if item["id"] != reply.group(1)]
            await self.emit("permission.replied", sessionID=sid, requestID=reply.group(1), reply=body["decision"])
            if body["decision"] == "reject" and sid in self.active:
                self.settle(sid, "interrupted")
                await self.emit("session.execution.interrupted", sessionID=sid, reason="shutdown")
            return 204, None
        return 404, {"_tag": "NotFound", "message": f"no route {method} {path}"}

    async def serve_events(self, request: web.Request) -> web.StreamResponse:
        if self.refuse_events:
            return web.Response(status=503, text="agent service unavailable")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.events_connections += 1
        await asyncio.sleep(self.hello_delay)
        if ws.closed:
            return ws
        await ws.send_str(json.dumps({"type": "server.connected", "properties": {}}))
        self.events.append(ws)
        self.events_connected.set()
        async for _ in ws:
            pass  # The gateway ignores inbound frames.
        if ws in self.events:
            self.events.remove(ws)
        return ws


async def hold_turn(agent: AgentGatewayMock, sid: str, inbox_id: str) -> None:
    """Any unscripted prompt: the execution starts and waits for the test to end it."""
    await agent.start(sid, inbox_id)


async def continue_turn(agent: AgentGatewayMock, sid: str, inbox_id: str) -> None:
    """Steering an idle session: a new execution starts with it, keeping the report so far."""
    report = agent.reports.get(sid, "")
    await agent.start(sid, inbox_id)
    agent.reports[sid] = report


# Beats closer together than BEAT_COALESCE_MS (1.5 s) coalesce; the margin absorbs the
# Durable Object's own processing time.
async def settle() -> None:
    await asyncio.sleep(2.2)


UNKNOWN_TOOL = "context7_lookup"


async def bridge_turn(agent: AgentGatewayMock, sid: str, inbox_id: str) -> None:
    """One Prompt Turn's traffic, shaped like OpenCode's: a plan, a read, the Agent narrating
    between tool calls, then the first edit, each its own beat. Then a burst: a remark, a tool of
    no known kind, and a command at once."""
    await asyncio.sleep(0.2)
    await agent.start(sid, inbox_id)
    await settle()
    await agent.tool(sid, "call_t0", "todowrite", {"todos": [
        {"content": "Find the parser", "priority": "high", "status": "in_progress"},
        {"content": "Write the artifact", "priority": "medium", "status": "pending"},
    ]})
    await settle()
    await agent.tool(sid, "call_t1", "read", {"filePath": "/home/daytona/src/parser.py"})
    await settle()
    await agent.text(sid, "The parser lives in **src**. ")
    await agent.tool(sid, "call_t2", "write", None, end=None)
    await settle()
    await agent.emit("session.tool.called", sessionID=sid, id="call_t2", input={"filePath": "/home/daytona/artifact.txt", "content": "ok\n"})
    await agent.emit("session.tool.success", sessionID=sid, id="call_t2")
    await settle()
    await agent.text(sid, "I'll check the tests next, which should take about a minute. ")
    await agent.tool(sid, "call_t3", UNKNOWN_TOOL, {})
    await agent.tool(sid, "call_t4", "bash", {"command": "python -m pytest -q"})
    await agent.text(sid, "Bridge report complete.")
    await agent.finish(sid)


FAIL_PROMPT = "Check the repository's state and summarize it."
SHELL_COMMAND = "git status --short --branch && git log --oneline -3"


async def failing_turn(agent: AgentGatewayMock, sid: str, inbox_id: str) -> None:
    """A command starts and never finishes; three steps fail in a burst; then the turn fails."""
    await agent.start(sid, inbox_id)
    await settle()
    await agent.tool(sid, "call_g1", "bash", {"command": SHELL_COMMAND}, end=None)
    await settle()
    for index in range(3):
        await agent.tool(sid, f"call_gf{index}", "websearch", None, end="failed")
    await asyncio.sleep(3.0)
    await agent.finish(sid, "failed", error={"type": "ProviderError", "message": "SseError: the event stream ended"})


OOM_PROMPT = "Install the dependencies with pnpm."
OOM_CLOSE = {"reason": "out_of_memory", "signal": "SIGKILL", "exit_status": -9, "limit_bytes": 1073741824}
OOM_SENTENCE = "The Agent was killed because the sandbox ran out of memory (1 GiB limit)."


async def oom_turn(agent: AgentGatewayMock, sid: str, inbox_id: str) -> None:
    """The service is OOM-killed mid-command: no terminal event, only the gateway's 1011 close."""
    await agent.start(sid, inbox_id)
    await agent.tool(sid, "call_o1", "bash", {"command": "pnpm install"}, end=None)
    await asyncio.sleep(0.25)
    agent.active.discard(sid)
    await agent.close_events(1011, json.dumps(OOM_CLOSE, separators=(",", ":")))


class SocketMocks:
    """GPT-Live's sideband and the Agent Gateway, on one port."""

    def __init__(self) -> None:
        self.live: LiveSocket | None = None
        self.live_event = 0
        # Set to have GPT-Live reject the next output fixture, naming it by event_id.
        self.reject_next_fixture = False
        self.live_messages: asyncio.Queue[dict] = asyncio.Queue()
        self.live_connected = asyncio.Event()
        self.seen: list[dict] = []
        self.agent = AgentGatewayMock()
        self.agent.scripts = {AGENT_PROMPT: bridge_turn, FAIL_PROMPT: failing_turn, OOM_PROMPT: oom_turn}

    @contextlib.asynccontextmanager
    async def serving(self):
        app = web.Application(client_max_size=4 * 1024 * 1024)
        app.router.add_route("*", "/{tail:.*}", self.handler)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", SOCKET_PORT).start()
        try:
            yield self
        finally:
            await runner.cleanup()

    async def handler(self, request: web.Request) -> web.StreamResponse:
        path = request.path
        if not (path.startswith("/live/sessions/") and path.endswith("/attach")):
            return await self.agent.handle(request)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        socket = self.live = LiveSocket(ws)
        self.live_connected.set()
        async for raw in ws:
            if raw.type != WSMsgType.TEXT:
                continue
            message = json.loads(raw.data)
            self.seen.append(message)
            await self.live_messages.put(message)
            # GPT-Live accepts each output fixture, then reports when its mixer drains;
            # appends are acknowledged on the session timeline. Each ack echoes event_id.
            if message.get("type") == "session.output_audio.fixture.play":
                asyncio.create_task(self.ack_fixture(socket, message.get("event_id")))
            elif message.get("type", "").endswith(".append"):
                self.live_event += 1
                await socket.send(json.dumps({
                    "type": message["type"].replace(".append", ".appended"), "event_id": f"evt-ack-{self.live_event}",
                    "client_event_id": message.get("event_id"), "start_ms": 600, "end_ms": 800,
                }))
        return ws

    async def ack_fixture(self, socket: LiveSocket, client_event_id: str | None) -> None:
        self.live_event += 1
        if self.reject_next_fixture:
            self.reject_next_fixture = False
            await socket.send(json.dumps({"type": "error", "event_id": f"evt-error-{self.live_event}", "error": {
                "type": "invalid_request_error", "code": "fixture_rejected", "message": "Fixture rejected.",
                "client_event_id": client_event_id}}))
            return
        await socket.send(json.dumps({"type": "session.output_audio.fixture.playing",
                                      "event_id": f"evt-playing-{self.live_event}", "client_event_id": client_event_id}))
        await asyncio.sleep(0.05)
        await socket.send(json.dumps({"type": "session.output_audio.fixture.played",
                                      "event_id": f"evt-played-{self.live_event}", "client_event_id": client_event_id}))

    async def send_live(self, message: dict) -> None:
        assert self.live is not None
        await self.live.send(json.dumps(message))

CALL_BRIEF = "Call started with no Managed Sessions."
READY_BRIEF = "The sandbox is ready."
# Every theme's lexicon in src/themes.ts: words that would show a theme leaking into an Agent prompt.
THEME_LEXICON = [
    word
    for words in re.findall(r"lexicon: \[(.*?)\]", (Path(__file__).parent / "src" / "themes.ts").read_text())
    for word in re.findall(r'"([^"]+)"', words)
]
assert "dog" in THEME_LEXICON and "callsign" in THEME_LEXICON
KEYPAD_SAY = "Seven noted."
NEXT_GREETING = "Last time Juniper built a tiny verified artifact."
# Roaming as the caller since their last call from home follows the caller-ID clause, even on a
# first call (configure_assignment seeds it), and is told once.
ROAMING_CLAUSE = ("Since your last call from home, someone got in as you from another phone once, "
                  "and someone failed the voice check as you from another phone twice.")
FIRST_OPENING = (
    f"Caller ID unverified. {ROAMING_CLAUSE} Dogwalk. Welcome! I'm your walker; your sandbox is "
    "being set up, which takes a minute or two."
)
RETURN_OPENING = (
    f"Verified. Dogwalk. {NEXT_GREETING} Since then, Juniper asked for permission. Where to?"
)
REAPED_CLAUSE = "Your sandbox was cleared after a long time without calls, so this one starts fresh."
WORK_BRIEF = "Starting Juniper on a tiny verified artifact."
AGENT_PROMPT = (
    "Goal: create a tiny artifact and verify it. Finish with a short summary "
    "of what changed and how it was verified."
)
STARTED_SAY = "Juniper is on it."
FINISHED_SAY = "Juniper finished; it reports the artifact is verified."
REPORTED_SAY = "Juniper says the artifact is verified."
SWITCH_SAY = "Switching themes; a new voice will pick up in a moment."
PERSISTED_SESSION_ID = "ses_earlier0parser"
PERSISTED_TITLE = "Earlier parser work"
LEASH_SAY = "Rowan has gone home; Juniper is still with us."
# Prompt Turns the Agent Gateway holds open for the test to drive (AgentGatewayMock.hold_turn).
AWAY_PROMPT = "Tidy the honeydozer build and report what changed."
AWAY_REPORT = "The honeydozer build is tidy."
STEER_PROMPT = "Add a docstring to the parser's entry point."
STEER_GUIDANCE = "Also mention the nested-quote fix in it."
STEERED_REPORT = "Docstring written, nested quotes included."
REDIRECT_PROMPT = "Profile the parser on the large fixture."
REDIRECT_GUIDANCE = "Stop profiling; just time the small fixture instead."
REDIRECTED_REPORT = "The small fixture parses in four milliseconds."
CANCEL_PROMPT = "Rewrite the tokenizer from scratch."
CANCEL_GUIDANCE = "Keep the old tokenizer's error messages."
CANCEL_REPORT = "Started on the tokenizer."
DROP_PROMPT = "Run the full parser test matrix."
DROP_REPORT = "The matrix ran clean on every interpreter."
RETRY_PROMPT = "Summarize the parser's open issues."
RETRY_REPORT = "Two open issues remain."
LOST_PROMPT = "Draft release notes for the parser."
ELSEWHERE_REPORT = "Done from the terminal."
STUCK_PROMPT = "Bisect the parser regression."
STUCK_GUIDANCE = "Skip the vendored commits."
STUCK_REPORT = "The regression came in with the lexer change."
LATE_PROMPT = "Rename the parser's internal helpers."
LATE_GUIDANCE = "Also rename the test helpers to match."
LATE_PROMPT_REPORT = "Parser helpers renamed."
LATE_REPORT = "Helpers renamed in the parser and the tests."
ADMITTED_PROMPT = "Count the parser's public functions."
ADMITTED_REPORT = "Seven public functions."
RESUMED_REPORT = "Resumed after the terminal's correction."
PARKED_REPORT = "Stopped from the terminal."
# Run in the Orchestrator mock's thread just before its next reply (before_reply).
LATE_HOOK: list = []


def before_reply(hooks: list, output: list[dict]) -> list[dict]:
    while hooks:
        hooks.pop(0)()
    return output
FORM_TITLE = "Which search provider should I use?"
REPLAY_SAY = "That pairing code expired; here is a fresh one."


def call(call_id: str, name: str, **arguments) -> dict:
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments),
    }


def tool_result(context: list[dict], call_id: str) -> dict:
    return json.loads(next(
        item for item in context
        if item.get("type") == "function_call_output" and item["call_id"] == call_id
    )["output"])


def invite_words(context: list[dict], call_id: str) -> str:
    output = next(
        item for item in context
        if item.get("type") == "function_call_output" and item["call_id"] == call_id
    )
    return json.loads(output["output"])["invitation"]["words"]


def created_alias(context: list[dict]) -> str:
    output = next(
        item for item in context
        if item.get("type") == "function_call_output" and item["call_id"] == "h1-create"
    )
    return json.loads(output["output"])["session"]["alias"]


# Each step fires once its trigger appears in a user message, in order, after the
# previous step's outputs are back. Steps that answer a handoff use its delegation id.
# The provider's usage and cache diagnosis, and what Dogwalk records from them: token counts,
# cache writes, and the miss reason survive (#54, #58), anything else is dropped.
MOCK_USAGE = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
              "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 1, "unlisted": 9,
                                       "cache_write_tokens_details": {"ttl_30m_tokens": 1, "ttl_12h_tokens": 0}},
              "output_tokens_details": {"reasoning_tokens": 1}}
RECORDED_USAGE = {"input_tokens": 1, "output_tokens": 1,
                  "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 1,
                                           "cache_write_tokens_details": {"ttl_30m_tokens": 1, "ttl_12h_tokens": 0}},
                  "output_tokens_details": {"reasoning_tokens": 1}}
MOCK_CACHE_DIAGNOSIS = {"type": "cache_miss", "reason": "input_changed", "comparison_reusable_tokens": 7,
                        "cache_missed_tokens": 5, "detail": "dropped"}
RECORDED_CACHE_DIAGNOSIS = {"type": "cache_miss", "reason": "input_changed", "comparison_reusable_tokens": 7,
                            "cache_missed_tokens": 5}

ORCHESTRATOR_SCRIPT = [
    # Session tools fail fast while the Sandbox is still starting.
    ("call_started", lambda ctx: [
        call("cs-brief", "brief", text=CALL_BRIEF, delegation_id=None),
        call("cs-create", "create_managed_session", alias="Early", directory="."),
        call("cs-list", "list_managed_sessions"),
    ]),
    ("[harness event] agent_connected", lambda ctx: [call("dr-brief", "brief", text=READY_BRIEF, delegation_id=None)]),
    ("[handoff del-1]", lambda ctx: [
        call("h1-brief", "brief", text=WORK_BRIEF, delegation_id="del-1"),
        call("h1-create", "create_managed_session", alias="Juniper", directory="."),
    ]),
    ("[handoff del-1]", lambda ctx: [
        call("h1-begin", "begin_prompt_turn", alias=created_alias(ctx), prompt=AGENT_PROMPT),
    ]),
    ("[handoff del-1]", lambda ctx: [
        call("h1-say-url", "say", text="Watch it at https://example.com", delegation_id="del-1"),
    ]),
    ("[handoff del-1]", lambda ctx: [call("h1-say", "say", text=STARTED_SAY, delegation_id="del-1")]),
    ("turn_stopped", lambda ctx: [call("ts-say", "say", text=FINISHED_SAY, delegation_id=None)]),
    ("[handoff del-2]", lambda ctx: [
        call("h2-inspect", "inspect_managed_session", alias=created_alias(ctx)),
    ]),
    ("[handoff del-2]", lambda ctx: [call("h2-unfollow", "stop_following")]),
    ("[handoff del-2]", lambda ctx: [call("h2-say", "say", text=REPORTED_SAY, delegation_id="del-2")]),
    # Handoff 3: persisted-session lifecycle (discover, load, retag, close, list).
    ("[handoff del-3]", lambda ctx: [call("h3-discover", "discover_persisted_sessions")]),
    ("[handoff del-3]", lambda ctx: [
        call("h3-load", "load_persisted_session", ref="r1", alias="Birch"),
    ]),
    ("[handoff del-3]", lambda ctx: [
        call("h3-retag", "set_alias", alias="Birch", new_alias="Rowan"),
    ]),
    ("[handoff del-3]", lambda ctx: [call("h3-close", "close_managed_session", alias="Rowan")]),
    ("[handoff del-3]", lambda ctx: [call("h3-list", "list_managed_sessions")]),
    # Sessions are rooted in project directories, which must exist inside the Workspace.
    ("[handoff del-3]", lambda ctx: [call("h3-projects", "list_workspace_projects")]),
    ("[handoff del-3]", lambda ctx: [
        call("h3-dozer", "create_managed_session", alias="Dozer", directory="honeydozer"),
    ]),
    ("[handoff del-3]", lambda ctx: [
        call("h3-ghost", "create_managed_session", alias="Ghost", directory="missing"),
        call("h3-escape", "create_managed_session", alias="Escape", directory="../etc"),
    ]),
    # A chapter closes as the first call winds down: the Orchestrator compacts, and the Memory
    # writer is still running when the caller asks to hang up.
    ("[handoff del-3]", lambda ctx: [
        call("h3-say", "say", text=LEASH_SAY, delegation_id="del-3"),
        call("h3-compact", "compact_context", focus=EARLY_FOCUS),
    ]),
    # Dozer is sent off just before hanging up; its turn ends while nobody is listening.
    ("[handoff del-4]", lambda ctx: [call("h4-dozer", "begin_prompt_turn", alias="Dozer", prompt=AWAY_PROMPT)]),
    ("[handoff del-4]", lambda ctx: [call("h4-end", "end_call")]),
    # Second call: a turn that fails mid-command: its running command ends with it.
    ("[handoff del-f]", lambda ctx: [call("f-begin", "begin_prompt_turn", alias="Juniper", prompt=FAIL_PROMPT)]),
    ("turn_failed", lambda ctx: [call("f-inspect", "inspect_managed_session", alias="Juniper")]),
    # The Agent is OOM-killed mid-turn: the reason follows the bare error.
    ("[handoff del-o]", lambda ctx: [call("o-begin", "begin_prompt_turn", alias="Juniper", prompt=OOM_PROMPT)]),
    ('"agent_termination":"out_of_memory"', lambda ctx: [call("o-inspect", "inspect_managed_session", alias="Juniper")]),
    # Steering: a Steer waits for the next step, a Redirect interrupts and resumes at once, and
    # cancelling withdraws steering the Agent has not read.
    ("[handoff del-s1]", lambda ctx: [call("s1-begin", "begin_prompt_turn", alias="Juniper", prompt=STEER_PROMPT)]),
    ("[handoff del-s1]", lambda ctx: [call("s1-steer", "steer_prompt_turn", alias="Juniper", guidance=STEER_GUIDANCE, when="next_step")]),
    ("[handoff del-s2]", lambda ctx: [call("s2-begin", "begin_prompt_turn", alias="Juniper", prompt=REDIRECT_PROMPT)]),
    ("[handoff del-s2]", lambda ctx: [call("s2-steer", "steer_prompt_turn", alias="Juniper", guidance=REDIRECT_GUIDANCE, when="now")]),
    # A Redirect whose interrupt fails still has its steering admitted.
    ("[handoff del-s3]", lambda ctx: [call("s3-begin", "begin_prompt_turn", alias="Juniper", prompt=STUCK_PROMPT)]),
    ("[handoff del-s3]", lambda ctx: [call("s3-steer", "steer_prompt_turn", alias="Juniper", guidance=STUCK_GUIDANCE, when="now")]),
    # Steering sent as the last execution ends, which the Durable Object has not heard yet.
    ("[handoff del-s4]", lambda ctx: [call("s4-begin", "begin_prompt_turn", alias="Juniper", prompt=LATE_PROMPT)]),
    ("[handoff del-s4b]", lambda ctx: before_reply(LATE_HOOK, [call("s4-steer", "steer_prompt_turn", alias="Juniper", guidance=LATE_GUIDANCE, when="next_step")])),
    ("[handoff del-c]", lambda ctx: [call("c-begin", "begin_prompt_turn", alias="Juniper", prompt=CANCEL_PROMPT)]),
    ("[handoff del-c]", lambda ctx: [call("c-steer", "steer_prompt_turn", alias="Juniper", guidance=CANCEL_GUIDANCE, when="next_step")]),
    ("[handoff del-c]", lambda ctx: [call("c-cancel", "cancel_prompt_turn", alias="Juniper")]),
    # The event stream drops twice under a running turn; the second time the turn ends unheard.
    ("[handoff del-d]", lambda ctx: [call("d-begin", "begin_prompt_turn", alias="Juniper", prompt=DROP_PROMPT)]),
    # A prompt refused by the preview proxy is retried; one whose answer is lost is not.
    ("[handoff del-g]", lambda ctx: [call("g-begin", "begin_prompt_turn", alias="Juniper", prompt=RETRY_PROMPT)]),
    ("[handoff del-g2]", lambda ctx: [call("g2-begin", "begin_prompt_turn", alias="Juniper", prompt=LOST_PROMPT)]),
    ("[handoff del-g3]", lambda ctx: [call("g3-begin", "begin_prompt_turn", alias="Juniper", prompt=ADMITTED_PROMPT)]),
    # An interrupt that fails leaves the turn running and the session open.
    ("[handoff del-x]", lambda ctx: [call("x-cancel", "cancel_prompt_turn", alias="Juniper")]),
    ("[handoff del-x]", lambda ctx: [call("x-close", "close_managed_session", alias="Juniper")]),
    ("[handoff del-x]", lambda ctx: [call("x-inspect", "inspect_managed_session", alias="Juniper")]),
    # Second call: digits without a system meaning still reach the Orchestrator.
    ('"key":"7"', lambda ctx: [call("k7-say", "say", text=KEYPAD_SAY, delegation_id=None)]),
    # Invitations: the caller (invited by SEED_INVITER) invites friends, by voice alone.
    ("[handoff del-i1]", lambda ctx: [
        call("i1-create", "create_invitation", recipient_phone=None, caller_confirmed_readback=False),
        call("i1-who", "inspect_registration"),
        call("i1-foreign", "revoke_invitation", invitation=FOREIGN_WORDS.upper()),
    ]),
    # A number is read back before it binds anything.
    ("[handoff del-i1]", lambda ctx: [call("i2-readback", "create_invitation", recipient_phone="555 555 0102", caller_confirmed_readback=False)]),
    ("[handoff del-i1]", lambda ctx: [call("i2-create", "create_invitation", recipient_phone="(555) 555-0102", caller_confirmed_readback=True)]),
    ("[handoff del-i1]", lambda ctx: [call("i3-create", "create_invitation", recipient_phone=None, caller_confirmed_readback=False)]),
    ("[handoff del-i1]", lambda ctx: [call("i4-limit", "create_invitation", recipient_phone=None, caller_confirmed_readback=False)]),
    ("[handoff del-i1]", lambda ctx: [
        call("i3-abroad", "set_invitation_recipient", invitation=invite_words(ctx, "i3-create"), recipient_phone="+44 20 7946 0958", caller_confirmed_readback=False),
        call("i3-bare", "set_invitation_recipient", invitation=invite_words(ctx, "i3-create"), recipient_phone="020 7946 0958", caller_confirmed_readback=True),
    ]),
    ("[handoff del-i1]", lambda ctx: [
        call("i-say", "say", text=f"The words are {invite_words(ctx, 'i1-create')}. Their number is 555 555 0102: plus one, five five five, five five five, zero one zero two.", delegation_id="del-i1"),
        call("i-leak", "begin_prompt_turn", alias="Juniper", prompt="Text my friend at +1 (555) 555-0102 about the parser."),
    ]),
    # Bound after a claim began: the claim's confirmation must now fail.
    ("[handoff del-i2]", lambda ctx: [
        call("i3-bind", "set_invitation_recipient", invitation=invite_words(ctx, "i3-create"), recipient_phone="+1 555 555 0104", caller_confirmed_readback=True),
    ]),
    ("[handoff del-i3]", lambda ctx: [call("i-list", "list_invitations")]),
    ("[handoff del-i3]", lambda ctx: [
        call("i3-revoke", "revoke_invitation", invitation=invite_words(ctx, "i3-create")),
        call("i1-hide", "revoke_invitation", invitation=invite_words(ctx, "i1-create")),
    ]),
    ("[handoff del-i3]", lambda ctx: [call("i-relist", "list_invitations")]),
    # Renaming: Dogwalk offers Handles; only an offered one can be chosen, from any spin.
    ("[handoff del-r1]", lambda ctx: [
        call("r1-offer", "offer_handles"),
        call("r1-bogus", "set_handle", handle="zebra zeppelin"),
    ]),
    ("[handoff del-r1]", lambda ctx: [call("r1-offer2", "offer_handles"), call("r1-offer3", "offer_handles")]),
    ("[handoff del-r1]", lambda ctx: [call("r1-offer4", "offer_handles")]),
    ("[handoff del-r1]", lambda ctx: [call("r1-set", "set_handle", handle=tool_result(ctx, "r1-offer")["options"][1].upper())]),
    ("[handoff del-r1]", lambda ctx: [call("r1-who", "inspect_registration")]),
    # Preferences and pairing are voice operations; keypad digits stand in for asking here.
    ('"key":"6"', lambda ctx: [call("k6-style", "set_earcon_style", style="tonal")]),
    ('"key":"8"', lambda ctx: [call("k8-pair", "pair_browser")]),
    ('"key":"8"', lambda ctx: [call("k8-say", "say", text=f"Pairing code {tool_result(ctx, 'k8-pair')['code']}", delegation_id=None)]),
    # No browser claimed the first spoken code: offer a fresh one after expiry.
    ("[harness event] pairing_unclaimed", lambda ctx: [
        call("pu-say", "say", text=REPLAY_SAY, delegation_id=None),
        call("pu-pair", "pair_browser"),
    ]),
    ("[harness event] pairing_unclaimed", lambda ctx: [call("pu-code-say", "say", text=f"Pairing code {tool_result(ctx, 'pu-pair')['code']}", delegation_id=None)]),
    ('"key":"4"', lambda ctx: [call("k4-state", "inspect_call_state")]),
    ('"key":"5"', lambda ctx: [call("k5-board", "draw_on_whiteboard", program=BOARD_PROGRAM, note="pick one")]),
    ('"key":"5"', lambda ctx: [call("k5-checks", "draw_on_whiteboard", program=CHECKS_PROGRAM, note="file checks")]),
    # A chapter closes: the Orchestrator compacts. An over-long focus is refused first.
    ("[harness event] whiteboard_answer", lambda ctx: [call("cc-long", "compact_context", focus="x" * 501)]),
    ("[harness event] whiteboard_answer", lambda ctx: [call("cc-memory", "compact_context")]),
    # Five more boards push the first two out of the Display's retention, with their grants.
    ('"key":"A"', lambda ctx: [call(f"kA-evict-{n}", "draw_on_whiteboard", program=ICON_PROGRAM, note=f"icon {n}") for n in range(1, 6)]),
    # A second Display: pairing it leaves the first paired; drawing must then name one.
    ("[handoff del-wb-slides]", lambda ctx: [call("wb-pair", "pair_browser", display="Slides!")]),
    ("[handoff del-wb-slides]", lambda ctx: [call("wb-say", "say", text=f"Pairing code {tool_result(ctx, 'wb-pair')['code']}", delegation_id="del-wb-slides")]),
    ('browser_pairing: {"outcome":"accepted","display":"slides"', lambda ctx: [
        call("wb-unnamed", "draw_on_whiteboard", program=SLIDES_PROGRAM),
        call("wb-ghost", "draw_on_whiteboard", program=SLIDES_PROGRAM, display="kitchen"),
    ]),
    ('browser_pairing: {"outcome":"accepted","display":"slides"', lambda ctx: [call("wb-slides", "draw_on_whiteboard", program=SLIDES_PROGRAM, display="Slides", note="first slide")]),
    # A browser paired to a Display that has one mirrors it.
    ("[handoff del-wb-mirror]", lambda ctx: [call("wm-pair", "pair_browser", display="main")]),
    ("[handoff del-wb-mirror]", lambda ctx: [call("wm-say", "say", text=f"Pairing code {tool_result(ctx, 'wm-pair')['code']}", delegation_id="del-wb-mirror")]),
    ("[handoff del-wb-end]", lambda ctx: [call("we-rename", "rename_display", display="slides", new_name="Projector")]),
    ("[handoff del-wb-end]", lambda ctx: [
        call("we-preview", "draw_on_whiteboard", program=PROJECTOR_PROGRAM, display="projector"),
        call("we-say", "say", text="The dev server is on the projector.", delegation_id="del-wb-end"),
    ]),
    # Access Elements: drawn in the call, granted for 90 minutes, on main only.
    ('"key":"9"', lambda ctx: [call("k9-access", "draw_on_whiteboard", program=ACCESS_PROGRAM, display="main", access_minutes=90, note="hands on")]),
    ('"key":"9"', lambda ctx: [call("k9-state", "inspect_call_state")]),
    ('"key":"B"', lambda ctx: [call("kB-revoke", "revoke_access", display="main")]),
    ('"key":"B"', lambda ctx: [call("kB-state", "inspect_call_state")]),
    ("[handoff del-wb-end2]", lambda ctx: [call("we-unpair", "unpair_display", display="projector")]),
    ("[handoff del-wb-end2]", lambda ctx: [
        call("we-state", "inspect_call_state"),
        call("we-say2", "say", text="The projector is unpaired.", delegation_id="del-wb-end2"),
    ]),
    ("[handoff del-pair-disconnect]", lambda ctx: [call("dc-pair", "pair_browser")]),
    ("[handoff del-pair-disconnect]", lambda ctx: [call("dc-say", "say", text=f"Pairing code {tool_result(ctx, 'dc-pair')['code']}", delegation_id="del-pair-disconnect")]),
    ('"key":"3"', lambda ctx: [
        call("k3-say", "say", text=SWITCH_SAY, delegation_id=None),
        call("k3-theme", "set_theme", theme="survey", register="high"),
    ]),
    # After the bounce, Runtime Dramaturgy renders the active theme's authoring notes.
    ('"key":"2"', lambda ctx: [{
        **call("k2-dramaturgy", "load_voice_agent_skill"),
        "arguments": json.dumps({"name": "runtime-dramaturgy"}),
    }]),
    # Asking to donate voice samples hands the call to the deterministic Voice Donation flow.
    ('"key":"C"', lambda ctx: [call("kC-donate", "start_voice_donation")]),
    # Asking to test their voice hands the call to deterministic Challenge Practice.
    ('"key":"D"', lambda ctx: [call("kD-practice", "start_challenge_practice")]),
]
# Invitation fixtures (test numbers only). SEED_INVITER invited TEST_PHONE; FOREIGN is
# SEED_INVITER's own, unbound and active; EXPIRED is TEST_PHONE's and lapsed.
SEED_INVITER = "+15555550109"
SEED_HANDLE, CALLER_HANDLE = "gecko lantern", "walrus kayak"
WELCOME_WORDS = "acme bravado cobra"
FOREIGN_WORDS = "absurd caravan tiger"
EXPIRED_WORDS = "adult escapade tonic"
BOUND_PHONE = "+15555550102"
INVITEE_PHONE = "+15555550103"
REBOUND_PHONE = "+15555550104"
RACE_PHONE = "+15555550105"
LATE_PHONE = "+15555550106"
RIVAL_PHONE = "+15555550107"
TEST_NUMBERS = [TEST_PHONE, SEED_INVITER, BOUND_PHONE, INVITEE_PHONE, REBOUND_PHONE, RACE_PHONE, LATE_PHONE, RIVAL_PHONE]
MEMORY_TEXT = "Caller prefers terse updates. Juniper fixes the parser; Dozer works in honeydozer."
EARLY_MEMORY = "Juniper fixes the parser."
EARLY_FOCUS = "Juniper's parser work"
COMPACTION_BRIEF = "Tell the caller briefly that you are tidying your notes, and that they can keep talking meanwhile."
BOARD_PROGRAM = (
    'root = Stack([fig, big, report, chart, pick, shell, docs])\nfig = Image("diagram.svg", "The parser\'s two stages", "Drawn by an agent", "small")\n'
    'big = Image(\'big.png\', "A generated chart")\n'
    'report = Document("REPORT.md", "The report")\nchart = Document("report.html", "The chart report")\n'
    'pick = Choice("approach", ["Quick fix", "Refactor"], "Which approach?")\n'
    'shell = Terminal()\ndocs = Preview(3000, "The docs")'
)
# Every file here fails its draw-time check, each for its own reason. huge.png comes last: the
# Worker cancels its capped download, and against this HTTP/1.0 mock the next toolbox fetch in
# the same draw has failed with "Network connection lost." (reported, not yet explained).
CHECKS_PROGRAM = (
    'root = Stack([Image("notes.png", "a"), Image("wide.png", "c"), Image("pixels.png", "d"), '
    'Image("escape.png", "e"), Image("missing.png", "f"), Image("https://example.com/a.png", "g"), '
    'Image("//example.com/a.png", "h"), Document("missing.md", "i"), Image("huge.png", "b")])'
)
ICON_PROGRAM = 'root = Stack([Image (asset, "", null, "icon"), Text("Example: Image(\'missing.png\', \'x\')")])\nasset = "/home/daytona/chart.png"'
SLIDES_PROGRAM = 'root = Stack([Image("chart.png", "Slide one")])'
PROJECTOR_PROGRAM = 'root = Stack([Preview(5173, "The dev server")])'
ACCESS_PROGRAM = (
    'root = Stack([gw, app, ssh, dl, desk, term, notes])\ngw = Preview(8765, "The gateway")\napp = Preview(5173, "The dev server")\n'
    'ssh = SshAccess("Copy this")\ndl = Download("REPORT.md", "The report")\ndesk = Desktop()\nterm = Terminal("build")\n'
    'notes = Document("REPORT.md", "Read it here")'
)
STEP_IDS = [
    "cs-brief", "cs-create", "cs-list", "dr-brief", "h1-brief", "h1-create", "h1-begin", "h1-say-url", "h1-say",
    "ts-say", "h2-inspect", "h2-unfollow", "h2-say", "f-begin", "f-inspect", "o-begin", "o-inspect",
    "h3-discover", "h3-load", "h3-retag", "h3-close", "h3-list",
    "h3-projects", "h3-dozer", "h3-ghost", "h3-escape", "h3-say", "h3-compact",
    "h4-dozer", "h4-end", "s1-begin", "s1-steer", "s2-begin", "s2-steer", "c-begin", "c-steer", "c-cancel",
    "s3-begin", "s3-steer", "s4-begin", "s4-steer", "d-begin", "g-begin", "g2-begin", "g3-begin",
    "x-cancel", "x-close", "x-inspect", "k7-say", "i1-create", "i1-who", "i1-foreign", "i2-readback", "i2-create", "i3-create", "i4-limit",
    "i3-abroad", "i3-bare", "i-say", "i-leak", "i3-bind", "i-list", "i3-revoke", "i1-hide", "i-relist",
    "r1-offer", "r1-bogus", "r1-offer2", "r1-offer3", "r1-offer4", "r1-set", "r1-who", "k6-style", "k8-pair", "pu-say", "pu-pair", "k4-state", "k5-board", "k5-checks", "cc-long", "cc-memory", "kA-evict-1", "kA-evict-2", "kA-evict-3", "kA-evict-4", "kA-evict-5",
    "wb-pair", "wb-say", "wb-unnamed", "wb-ghost", "wb-slides", "wm-pair", "wm-say", "we-rename", "we-preview", "we-say",
    "k9-access", "k9-state", "kB-revoke", "kB-state", "we-unpair", "we-state", "we-say2", "k3-say", "k3-theme",
    "k8-say", "pu-code-say", "dc-pair", "dc-say", "k2-dramaturgy", "kC-donate", "kD-practice",
]


class OrchestratorMock(BaseHTTPRequestHandler):
    """A scripted /v1/responses endpoint standing in for the Session Orchestrator model."""

    requests: list[tuple[dict[str, str], dict]] = []
    greeting_requests: list[dict] = []
    # The Memory writer's requests, answered in order; clearing the gate holds them, as a slow
    # model writing a long Memory would (CAa3722805: 51.7 s).
    memory_requests: list[dict] = []
    memory_replies = [EARLY_MEMORY, MEMORY_TEXT]
    memory_gate = threading.Event()
    issued: list[tuple[str, int]] = []
    lock = threading.Lock()

    def log_message(self, *args) -> None:
        pass

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("content-length", "0")))
        payload = json.loads(raw)
        headers = {key.lower(): value for key, value in self.headers.items()}
        if self.path != "/v1/responses":
            self.send_error(404)
            return
        if str(payload["input"][-1].get("content", "")).startswith("[write memory]"):
            with OrchestratorMock.lock:
                OrchestratorMock.memory_requests.append(payload)
                reply = OrchestratorMock.memory_replies[len(OrchestratorMock.memory_requests) - 1]
            if not OrchestratorMock.memory_gate.wait(60):
                # A gate never released would let compaction finish early and prove nothing.
                self.send_error(504, "memory gate never released")
                return
            self.respond({"output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": reply}]}]})
            return
        if payload.get("tool_choice") == "none":
            # The end-of-call request that writes the next call's welcome line.
            OrchestratorMock.greeting_requests.append(payload)
            self.respond({"output": [{
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": NEXT_GREETING}],
            }]})
            return
        with OrchestratorMock.lock:
            OrchestratorMock.requests.append((headers, payload))
            context = payload["input"]
            user_text = "\n".join(
                str(item["content"]) for item in context if item.get("role") == "user"
            )
            output = []
            step = len({index for _, index in OrchestratorMock.issued})
            if step < len(ORCHESTRATOR_SCRIPT) and ORCHESTRATOR_SCRIPT[step][0] in user_text:
                output = ORCHESTRATOR_SCRIPT[step][1](context)
                OrchestratorMock.issued.extend((item["call_id"], step) for item in output)
        # Provider correlation metadata, derived from the client's ID so the test can match rows.
        client = headers.get("x-client-request-id", "").replace("-", "")
        self.respond({"id": f"resp_{client}", "model": "mock-model-2026-11-10", "output": output,
                      "usage": MOCK_USAGE,
                      "prompt_cache_diagnostics": MOCK_CACHE_DIAGNOSIS}, {"x-request-id": f"req_{client}"})

    def respond(self, value: dict, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(value).encode()
        self.send_response(200)
        for key, header in (extra_headers or {}).items():
            self.send_header(key, header)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @classmethod
    def user_message(cls, marker: str) -> str:
        for _, payload in cls.requests:
            for item in payload["input"]:
                if item.get("role") == "user" and marker in str(item["content"]):
                    return str(item["content"])
        raise AssertionError(f"orchestrator never received {marker}")

    @classmethod
    def tool_output(cls, call_id: str) -> dict:
        for _, payload in reversed(cls.requests):
            for item in payload["input"]:
                if item.get("type") == "function_call_output" and item["call_id"] == call_id:
                    return json.loads(item["output"])
        raise AssertionError(f"orchestrator never received output for {call_id}")


def transcript(direction: str, delta: str, start_ms: int, end_ms: int) -> dict:
    return {
        "type": f"session.{direction}_transcript.delta",
        "delta": delta,
        "start_ms": start_ms,
        "end_ms": end_ms,
    }


def delegation(delegation_id: str, offset_ms: int) -> dict:
    return {
        "type": "session.delegation.created",
        "offset_ms": offset_ms,
        "delegation": {"id": delegation_id, "type": "delegation", "target": "client"},
    }


def appended(lane: str, content: str):
    return lambda item: (
        item.get("type") == f"session.{lane}.append" and item.get("content") == content
    )


async def next_matching(
    queue: asyncio.Queue[dict], predicate, timeout: float = 8
) -> dict:
    deferred = []
    async with asyncio.timeout(timeout):
        while True:
            message = await queue.get()
            if predicate(message):
                for item in deferred:
                    queue.put_nowait(item)
                return message
            deferred.append(message)


def configure_assignment(wrangler: str, identity: str) -> None:
    sql = f"""
INSERT INTO invite_codes (code_words, expires_at, max_uses)
VALUES ('{WELCOME_WORDS}', unixepoch() + 86400, 1), ('{FOREIGN_WORDS}', unixepoch() + 86400, 1),
       ('{EXPIRED_WORDS}', unixepoch() - 60, 1)
ON CONFLICT(code_words) DO UPDATE SET expires_at = excluded.expires_at, max_uses = 1;
INSERT INTO users (handle, identity_hash, invite_code, registered_at, last_seen_at, home_phone, home_used_at)
VALUES ('{SEED_HANDLE}', 'seed-inviter-{identity}', 'ahead almighty apple', unixepoch() - 86400, NULL, '{SEED_INVITER}', unixepoch() - 86400),
       ('{CALLER_HANDLE}', '{identity}', '{WELCOME_WORDS}', unixepoch(), unixepoch(), '{TEST_PHONE}', unixepoch());
INSERT INTO invitations (code_words, inviter_user_id, redeemed_by_user_id, redeemed_from, redeemed_at)
VALUES ('{WELCOME_WORDS}', (SELECT id FROM users WHERE handle = '{SEED_HANDLE}'),
        (SELECT id FROM users WHERE handle = '{CALLER_HANDLE}'), '{TEST_PHONE}', unixepoch()),
       ('{FOREIGN_WORDS}', (SELECT id FROM users WHERE handle = '{SEED_HANDLE}'), NULL, NULL, NULL),
       ('{EXPIRED_WORDS}', (SELECT id FROM users WHERE handle = '{CALLER_HANDLE}'), NULL, NULL, NULL);
INSERT INTO sandbox_assignments (user_id, provider_id, state, provisioning_started_at, last_checked_at)
VALUES ((SELECT id FROM users WHERE identity_hash = '{identity}'), 'mock-sandbox-1', 'started', unixepoch(), unixepoch());
-- CA_VOICE_TEST carries front-door trace context; the others are calls from before it existed.
INSERT INTO voice_calls (call_sid, phone_number, status, started_at, last_activity_at, trace_id, span_id, user_id)
SELECT call_sid, '{TEST_PHONE}', 'in-progress', unixepoch(), unixepoch(), trace_id, span_id,
       (SELECT id FROM users WHERE identity_hash = '{identity}')
  FROM (SELECT 'CA_VOICE_TEST' AS call_sid, '{CALL_TRACE_ID}' AS trace_id, '{CALL_SPAN_ID}' AS span_id
        UNION ALL SELECT 'CA_VOICE_TEST2', NULL, NULL UNION ALL SELECT 'CA_VOICE_TEST3', NULL, NULL);
-- How CA_VOICE_TEST's caller got in: the shadow Identity Check's record (identity_check.ts).
UPDATE voice_calls SET identity = '{{"path":"home","user_id":2,"outcome":"match","level":"Voice matched.","enforcement":"admit","shadow":true}}'
 WHERE call_sid = 'CA_VOICE_TEST';
-- Roaming as the caller from other phones (identity_check.ts): one admission and two failed
-- attempts are news for the next Home Opening; one already told and one that neither got in
-- nor failed are not.
INSERT INTO roaming_attempts (call_sid, user_id, phone_number, started_at, failures, admitted_at, noticed_at)
SELECT call_sid, (SELECT id FROM users WHERE identity_hash = '{identity}'), '+15555550188', unixepoch() - 600, failures, admitted_at, noticed_at
  FROM (SELECT 'CA_ROAM_IN' AS call_sid, 0 AS failures, unixepoch() - 600 AS admitted_at, NULL AS noticed_at
        UNION ALL SELECT 'CA_ROAM_FAIL1', 1, NULL, NULL UNION ALL SELECT 'CA_ROAM_FAIL2', 1, NULL, NULL
        UNION ALL SELECT 'CA_ROAM_TOLD', 1, NULL, unixepoch() - 900 UNION ALL SELECT 'CA_ROAM_QUIT', 0, NULL, NULL);
INSERT INTO call_activity (call_sid, ts, source, direction, event, detail)
VALUES ('CA_VOICE_TEST2', unixepoch(), 'voice', 'inbound', 'call.attestation',
        '{{"stir_verstat":"TN-Validation-Passed-A","caller_name_present":false,"from_country":"US","parameters":[]}}');
"""
    subprocess.run(
        [wrangler, "d1", "execute", "dogwalk", "--local", "--command", sql],
        cwd=WORKER_DIR,
        check=True,
        stdout=subprocess.DEVNULL,
    )


def signed_webhook(call_sid: str, live_session_id: str) -> tuple[str, str, str]:
    expires_at = int(time.time()) + 300
    content = f"{call_sid}.{expires_at}"
    signature = base64.urlsafe_b64encode(
        hmac.new(
            IDENTITY_SECRET.encode(),
            f"sip-context-v1:{content}".encode(),
            hashlib.sha256,
        ).digest()
    ).decode().rstrip("=")
    event = {
        "object": "event",
        "id": f"evt_{live_session_id}",
        "type": "live.transport.incoming",
        "data": {
            "type": "sip",
            "session_id": live_session_id,
            "sip_headers": [{"name": "X-Dogwalk-Context", "value": f"{content}.{signature}"}],
        },
    }
    body = json.dumps(event, separators=(",", ":"))
    webhook_id = f"wh_{live_session_id}"
    timestamp = str(int(time.time()))
    webhook_signature = base64.b64encode(
        hmac.new(
            OPENAI_WEBHOOK_KEY,
            f"{webhook_id}.{timestamp}.{body}".encode(),
            hashlib.sha256,
        ).digest()
    ).decode()
    return body, f"{webhook_id}|{timestamp}", f"v1,{webhook_signature}"


def post_webhook(body: str, identity: str, signature_header: str) -> int:
    webhook_id, timestamp = identity.split("|")
    request = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}/openai/live",
        data=body.encode(),
        headers={
            "content-type": "application/json",
            "webhook-id": webhook_id,
            "webhook-timestamp": timestamp,
            "webhook-signature": signature_header,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


CALL_TRACE_ID, CALL_SPAN_ID = "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7"  # W3C spec example IDs


def call_activity_rows(call_sid: str, timing: bool = False) -> list[dict]:
    credentials = base64.b64encode(f"adam:{LOCAL_ADMIN_PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}/admin/api/calls?verbose=1&limit=10" + ("&timing=1" if timing else ""),
        headers={"authorization": f"Basic {credentials}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        calls = json.load(response)
    return [item for call in calls if call["call_sid"] == call_sid for item in call["activity"]]


def activity_line(row: dict) -> str:
    """One activity row for failure output: what happened, to which tool, first; the rest cut."""
    try:
        detail = json.loads(row.get("detail") or "{}")
    except ValueError:
        detail = {}
    if not isinstance(detail, dict):
        detail = {}
    head = [detail.pop(key, None) for key in ("event", "name", "tool_call_id", "outcome", "ms")]
    head = " ".join(str(field) for field in [row.get("ts"), row.get("event"), *head] if field is not None)
    return f"{head} {json.dumps(detail)[:160]}"


def call_activity(call_sid: str) -> list[str]:
    return [row["event"] for row in call_activity_rows(call_sid)]


def operator_timing_page(call_sid: str, after: int = 0) -> dict:
    credentials = base64.b64encode(f"adam:{LOCAL_ADMIN_PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}/admin/api/timing?call_sid={call_sid}&after={after}",
        headers={"authorization": f"Basic {credentials}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def operator_content(call_sid: str) -> dict:
    credentials = base64.b64encode(f"adam:{LOCAL_ADMIN_PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}/admin/api/timing/content?call_sid={call_sid}",
        headers={"authorization": f"Basic {credentials}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def conversations() -> list[dict]:
    credentials = base64.b64encode(f"adam:{LOCAL_ADMIN_PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}/admin/api/conversations?verbose=1",
        headers={"authorization": f"Basic {credentials}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def operator_access_state(verbose: bool = False) -> dict:
    credentials = base64.b64encode(f"adam:{LOCAL_ADMIN_PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}/admin/api/state" + ("?verbose=1" if verbose else ""),
        headers={"authorization": f"Basic {credentials}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def agent_connected_count() -> int:
    """agent_connected events the model has read, plus any snoozed in the queue (#58)."""
    queued = sum(text.count("[harness event] agent_connected") for item in conversations() for text in item.get("orchestrator_queue") or [])
    return latest_user_text().count("[harness event] agent_connected") + queued


def latest_user_text() -> str:
    if not OrchestratorMock.requests:
        return ""
    return "\n".join(
        str(item["content"]) for item in OrchestratorMock.requests[-1][1]["input"] if item.get("role") == "user"
    )


# --- Paired Browser helpers ----------------------------------------------------

CHROME_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
REPORT_TEXT = "# Parser report\n\nAll green.\n"


def pair_http(method: str, path: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def pair_raw(path: str, headers: dict) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(f"http://127.0.0.1:{WORKER_PORT}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


async def recv_type(socket, kind: str, timeout: float = 8) -> dict:
    """The next message of a kind, skipping the status pushes that may arrive in between."""
    async with asyncio.timeout(timeout):
        while True:
            message = json.loads(await socket.recv())
            if message.get("type") == kind:
                return message


PNG_BYTES = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII=")
SVG_BYTES = b'<?xml version="1.0"?>\n<!-- drawn by an agent -->\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><script>alert(1)</script><rect width="10" height="10"/></svg>'
# Workspace images: name -> (bytes, size the file info reports). huge.png understates its size,
# so only the cap on downloaded bytes can catch it.
IMAGE_FILES = {
    "chart.png": (PNG_BYTES, len(PNG_BYTES)),
    "diagram.svg": (SVG_BYTES, len(SVG_BYTES)),
    "notes.png": (b"just some text\n", 15),
    "huge.png": (PNG_BYTES + bytes(8 * 1024 * 1024), 1000),
    "escape.png": (PNG_BYTES, len(PNG_BYTES)),
    "big.png": (PNG_BYTES + bytes(1_600_000), len(PNG_BYTES) + 1_600_000),
    "wide.png": (PNG_BYTES[:16] + (9000).to_bytes(4, "big") + PNG_BYTES[20:], len(PNG_BYTES)),
    "pixels.png": (PNG_BYTES[:16] + (5000).to_bytes(4, "big") * 2 + PNG_BYTES[24:], len(PNG_BYTES)),
}
# An HTML report: a Workspace image (relative) is inlined, a web image and one outside the Workspace are not.
REPORT_HTML = (
    '<!doctype html><h1>Chart</h1><img src="chart.png" srcset="chart.png 2x" alt="The chart">'
    '<img src="https://example.com/a.png" alt="web"><img src="/etc/x.png" alt="Outside"><img src="data:image/gif;base64,R0lG" alt="inline">'
)


def sandbox_files(path: str):
    """Toolbox file reads: home holds a report and some images; anything else does not exist."""
    prefix = "/toolbox/mock-sandbox-1/files"
    for name, (body, size) in IMAGE_FILES.items():
        if path == f"{prefix}/info?path=%2Fhome%2Fdaytona%2F{name}":
            return 200, json.dumps({"name": name, "isDir": False, "size": size}).encode(), "application/json"
        if path == f"{prefix}/download?path=%2Fhome%2Fdaytona%2F{name}":
            return 200, body, "application/octet-stream"
    if path == f"{prefix}?path=%2Fhome%2Fdaytona":
        listing = [{"name": "REPORT.md", "isDir": False, "size": len(REPORT_TEXT), "modTime": "2026-11-08T10:00:00Z"},
                   {"name": "parser", "isDir": True, "size": 4096, "modTime": "2026-11-08T09:00:00Z"}]
        return 200, json.dumps(listing).encode(), "application/json"
    if path == f"{prefix}/info?path=%2Fhome%2Fdaytona%2Freport.html":
        return 200, json.dumps({"name": "report.html", "isDir": False, "size": len(REPORT_HTML)}).encode(), "application/json"
    if path == f"{prefix}/download?path=%2Fhome%2Fdaytona%2Freport.html":
        return 200, REPORT_HTML.encode(), "application/octet-stream"
    if path == f"{prefix}/info?path=%2Fhome%2Fdaytona%2FREPORT.md":
        return 200, json.dumps({"name": "REPORT.md", "isDir": False, "size": len(REPORT_TEXT)}).encode(), "application/json"
    if path == f"{prefix}/download?path=%2Fhome%2Fdaytona%2FREPORT.md":
        return 200, REPORT_TEXT.encode(), "application/octet-stream"
    return None


# What Access Grants minted and revoked at Daytona: signed preview links, their expiries, SSH
# tokens and their deletions, and the terminal PTYs the relay asked for.
PROVIDER: dict[str, list] = {"signed": [], "expired": [], "ssh": [], "pty": []}


def provider_gets(path: str):
    match = re.fullmatch(r"/api/sandbox/mock-sandbox-1/ports/(\d+)/signed-preview-url\?expiresInSeconds=(\d+)", path)
    if not match:
        return None
    token = f"signed-token-{len(PROVIDER['signed']) + 1}"
    PROVIDER["signed"].append({"port": int(match[1]), "seconds": int(match[2]), "token": token})
    return 200, json.dumps({"url": f"https://{match[1]}-{token}.preview.test/", "token": token}).encode(), "application/json"


def provider_posts(path: str, payload: dict):
    if match := re.fullmatch(r"/api/sandbox/mock-sandbox-1/ports/(\d+)/signed-preview-url/([^/]+)/expire", path):
        PROVIDER["expired"].append((int(match[1]), match[2]))
        return {}
    if match := re.fullmatch(r"/api/sandbox/mock-sandbox-1/ssh-access\?expiresInMinutes=(\d+)", path):
        token = f"ssh-token-{len(PROVIDER['ssh']) + 1}"
        PROVIDER["ssh"].append({"minutes": int(match[1]), "token": token})
        return {"token": token, "sshCommand": f"ssh {token}@ssh.preview.test", "expiresAt": "2026-11-12T12:00:00Z"}
    if path == "/toolbox/mock-sandbox-1/process/pty":
        PROVIDER["pty"].append(payload.get("id"))  # still refused: the mock toolbox has no PTY
    return None


def provider_deletes(path: str):
    return 200 if path.startswith("/api/sandbox/mock-sandbox-1/ssh-access?token=") else None


def dtmf(key: str) -> dict:
    return {"type": "transport.dtmf.received", "event": key}


def of_type(kind: str):
    return lambda item: item.get("type") == kind


async def wait_for(condition, timeout: float = 8) -> None:
    try:
        async with asyncio.timeout(timeout):
            while not condition():
                await asyncio.sleep(0.05)
    except TimeoutError:
        # A stalled Worker and a script that stopped matching look alike without the last steps.
        issued = [call_id for call_id, _ in OrchestratorMock.issued[-4:]]
        raise TimeoutError(f"condition unmet after {timeout} s; last Orchestrator calls issued: {issued}") from None


async def run_protocol(identity: str) -> None:
    mocks = SocketMocks()
    agent = mocks.agent
    async with mocks.serving():
        body, webhook_identity, signature = signed_webhook("CA_VOICE_TEST", "live-test")
        assert await asyncio.to_thread(post_webhook, body, webhook_identity, "v1,invalid") == 400
        assert not DaytonaMock.openai_requests
        assert await asyncio.to_thread(post_webhook, body, webhook_identity, signature) == 204
        await asyncio.wait_for(agent.events_connected.wait(), 8)
        await asyncio.wait_for(mocks.live_connected.wait(), 8)
        accept_path, accept_payload = next(
            item for item in DaytonaMock.openai_requests if item[0].endswith("/accept")
        )
        assert accept_path == "/live/sessions/live-test/accept"
        session = accept_payload["session"]
        assert session["type"] == "live" and session["model"] == "gpt-live-1"
        assert "audio" in session and "format" not in session["audio"]
        assert "Generate <|SpawnThinking|> when" in session["instructions"]
        assert session["delegation"] == {"type": "client", "acknowledgement_delay_ms": 400}
        assert "tools" not in session
        frontend = session["instructions"]
        assert "Match the caller's sophistication" in frontend
        assert "press star for a menu" in frontend and "pound to make you stop talking" in frontend
        # A neutral core holds the rules; the Park theme only presents them. The voice
        # defers to its head and names no backend except in GPT-Live's delegation-policy
        # label and the one sentence forbidding it.
        assert "The session model:" in frontend and "Whatever the theme and intensity" in frontend
        assert "The walk (Park theme)" in frontend and "your head (your mind)" in frontend
        # Examples narrate a placeholder, never a plausible alias the voice might repeat.
        assert "<alias>" in frontend and not any(name in frontend for name in ("Cedar", "Biscuit", "Kappa", "Dozer")), frontend
        assert "name only aliases the Session Orchestrator" in frontend
        assert "An intention or estimate is not an action or a limit" in frontend
        assert "A failed step is not a failed turn" in frontend
        assert "Never mention a backend" in frontend
        # Delegated work is never claimed before the Orchestrator says a turn began.
        assert "unless the Session Orchestrator told you a turn began for it" in frontend
        # Pairing is thinking work: the Voice Frontend hands off rather than standing by.
        delegated = frontend.split("Backend tools:", 1)[1].split("\n", 1)[0]
        assert "pairing a browser" in delegated and "paired screen" in delegated and "whiteboard" in delegated, delegated
        assert "inviting someone" in delegated and "who invited the caller" in delegated, delegated
        assert "backend" not in (
            frontend.replace("Backend tools:", "").replace("Never mention a backend", "").lower()
        )

        # Talk first: the opening is spoken before the Agent has even initialized.
        greeting = await next_matching(mocks.live_messages, of_type("session.commentary.append"))
        assert greeting["content"] == FIRST_OPENING and greeting["delegation_id"] is None
        # Subscribed first, then caught up from snapshots.
        await agent.next_request(lambda item: item["path"] == "/api/session/active")
        call_brief = await next_matching(mocks.live_messages, appended("thinking", CALL_BRIEF))
        assert call_brief["delegation_id"] is None
        started_event = OrchestratorMock.user_message("call_started")
        assert '"first_call":true' in started_event and FIRST_OPENING in started_event
        assert '"caller_id":null' in started_event
        # The Session Orchestrator learns how the caller got in; the spoken opening never recites it.
        assert '"identity":{"via":"home","outcome":"match","level":"Voice matched.","would_have":"admit","shadow":true}' in started_event, started_event
        assert "matched" not in greeting["content"].lower() and "user_id" not in started_event
        assert "agent_connecting" in started_event
        await next_matching(mocks.live_messages, appended("thinking", READY_BRIEF))
        early = OrchestratorMock.tool_output("cs-create")
        assert early["ok"] is False and "still starting" in early["error"], early
        assert OrchestratorMock.tool_output("cs-list") == []
        assert '"managed_session_count":0' in OrchestratorMock.user_message("[harness event] agent_connected")

        # Handoff 1: the Voice Frontend delegates; the task is the transcript.
        await mocks.send_live(transcript("input", "Start a session called Juniper ", 0, 900))
        await mocks.send_live(transcript("input", "to build a tiny verified artifact.", 900, 2100))
        await mocks.send_live(transcript("output", "On it.", 2200, 2600))
        await mocks.send_live(delegation("del-1", 2150))
        work_brief = await next_matching(mocks.live_messages, appended("thinking", WORK_BRIEF))
        assert work_brief["delegation_id"] == "del-1"
        # Every sideband command carries an event_id, so an error names the command it rejects.
        assert isinstance(work_brief.get("event_id"), str), work_brief
        await mocks.send_live({"type": "error", "event_id": "evt-error-1", "error": {
            "type": "invalid_request_error", "code": "append_rejected", "message": "Append rejected.",
            "client_event_id": work_brief["event_id"]}})
        await mocks.send_live({"type": "session.usage.updated", "event_id": "evt-usage-1",
                               "usage": {"seconds": 6.0}, "context_window": {"usage_ratio": 0.0087}})
        created = await agent.next_request(lambda item: (item["method"], item["path"]) == ("POST", "/api/session"))
        assert created["body"] == {"location": {"directory": "/home/daytona"}}, created
        juniper_id = agent.session_for("/home/daytona")
        prompt_request = await agent.next_request(lambda item: item["path"].endswith("/prompt"))
        assert prompt_request["path"] == f"/api/session/{juniper_id}/prompt"
        assert prompt_request["body"] == {"text": AGENT_PROMPT}, prompt_request
        started = await next_matching(mocks.live_messages, appended("commentary", STARTED_SAY))
        assert started["delegation_id"] == "del-1"
        finished = await next_matching(mocks.live_messages, appended("commentary", FINISHED_SAY), 20)
        assert finished["delegation_id"] is None
        handoff = OrchestratorMock.user_message("[handoff del-1]")
        assert "Caller: Start a session called Juniper to build a tiny verified artifact." in handoff
        # The Voice spoke after the offset, so its words wait for the next handoff.
        assert "Voice: On it." not in handoff
        stopped = OrchestratorMock.user_message("turn_stopped")
        assert '"outcome":"completed"' in stopped and "Bridge report complete." in stopped
        rejected = OrchestratorMock.tool_output("h1-say-url")
        assert rejected["ok"] is False and "URL" in rejected["error"]
        juniper = OrchestratorMock.tool_output("h1-create")["session"]
        assert juniper["alias"] == "Juniper" and juniper["directory"] == "."
        begun = OrchestratorMock.tool_output("h1-begin")
        assert begun["turn_state"] == "in_progress" and begun["following"] == "Juniper", begun
        assert '"followed":true' in stopped

        # Sending Juniper off followed it: progress beats reach the Voice Frontend as
        # thinking, built from Agent events without a model call.
        # Beats a few seconds apart are joined into one append; beats in one burst make one beat.
        def beats() -> str:
            return "\n".join(
                m["content"] for m in mocks.seen
                if m.get("type") == "session.thinking.append" and m["content"].startswith("Following Juniper:")
            )

        expected_beats = [
            "Juniper is working", "step 1 of 2: Find the parser.", "Juniper is reading parser.py.",
            'Juniper says: "The parser lives in src."', "started changing files: editing artifact.txt.",
            # A remark keeps the Agent's words and tense: a plan and an estimate, not an action.
            "Juniper says: \"I'll check the tests next, which should take about a minute.\"",
        ]
        await wait_for(lambda: all(fragment in beats() for fragment in expected_beats))
        # The burst made one beat: the remark outweighed the command started with it, and the
        # tool of no known kind made none, so its name never reached the Voice Frontend.
        assert "running a command" not in beats() and UNKNOWN_TOOL not in beats(), beats()
        assert "using" not in beats(), beats()
        # Tool calls failing after the turn ended make no beat: the turn event already spoke.
        for index in range(3):
            await agent.tool(juniper_id, f"call_f{index}", "websearch", None, end="failed")
        await asyncio.sleep(4)
        assert "failed for Juniper" not in beats(), beats()

        # Handoff 2: a follow-up question answered from session inspection.
        await mocks.send_live(transcript("input", "What did it report?", 5000, 6200))
        await mocks.send_live(delegation("del-2", 6250))
        reported = await next_matching(mocks.live_messages, appended("commentary", REPORTED_SAY))
        assert reported["delegation_id"] == "del-2"
        handoff = OrchestratorMock.user_message("[handoff del-2]")
        assert "Caller: What did it report?" in handoff and "Juniper" not in handoff
        # Each word is handed over once: the earlier request is not resent.
        assert "Voice: On it." in handoff and "Start a session" not in handoff, handoff
        # The caller goes on past the offset and no new handoff comes: the rest still
        # reaches the Orchestrator, under the handoff it continues.
        await mocks.send_live(transcript("input", " And whether the tests passed.", 6400, 7400))
        await wait_for(lambda: any("[continued del-2]" in str(item.get("content"))
                                   for _, payload in OrchestratorMock.requests for item in payload["input"]))
        continued = OrchestratorMock.user_message("[continued del-2]")
        assert "Caller: And whether the tests passed." in continued and "What did it report" not in continued, continued
        inspected = OrchestratorMock.tool_output("h2-inspect")
        assert inspected["alias"] == "Juniper" and inspected["outcome"] == "completed"
        # The failed searches after the turn made its closing words the latest remark.
        assert inspected["followed"] is True and inspected["remark"] == "Bridge report complete.", inspected
        assert [(c["kind"], c["status"], c["paths"]) for c in inspected["recent_tool_calls"]] == [
            ("edit", "completed", ["artifact.txt"]), ("other", "completed", []), ("execute", "completed", []),
            ("fetch", "failed", []), ("fetch", "failed", []), ("fetch", "failed", []),
        ], inspected
        assert OrchestratorMock.tool_output("h2-unfollow") == {"ok": True, "stopped_following": "Juniper"}
        events = await asyncio.to_thread(call_activity, "CA_VOICE_TEST")
        for expected in ("session.followed", "session.beat", "session.unfollowed"):
            assert expected in events, (expected, events)

        # The trace is opt-in, millisecond-stamped, and never retains raw audio.
        await mocks.send_live({"type": "session.output_audio.delta", "delta": "PRIVATE_AUDIO_BASE64_SENTINEL", "start_ms": 0, "end_ms": 200})
        await asyncio.sleep(1.2)  # one off-path telemetry flush
        rows = await asyncio.to_thread(call_activity_rows, "CA_VOICE_TEST", True)
        timed = [json.loads(row["detail"]) for row in rows if row["event"] == "voice.timing"]
        assert "voice.timing" not in events
        assert timed and all(isinstance(row["ts_ms"], int) and row["live_session_id"] == "live-test" for row in timed)
        assert all(not ({"audio", "content", "delta"} & row.keys()) for row in timed)
        assert "PRIVATE_AUDIO_BASE64_SENTINEL" not in json.dumps(timed), "audio delta was mistaken for transcript content"
        kinds = {row["event"] for row in timed}
        assert {"live.received", "live.sent", "handoff.started", "handoff.queued", "model.started",
                "model.headers", "model.completed", "tool.started", "tool.completed", "agent.sent", "agent.received"} <= kinds, kinds
        # Sideband correlation (#29): sends carry event_ids, acks and errors are tied back to them.
        sent = [row for row in timed if row["event"] == "live.sent"]
        assert sent and all(isinstance(row.get("event_id"), str) for row in sent), sent
        acked = [row for row in timed if row["event"] == "live.received" and row["type"] == "session.thinking.appended"
                 and row.get("client_event_id") == work_brief["event_id"]]
        assert acked and acked[0]["command_delegation_id"] == "del-1" and acked[0]["start_ms"] == 600, acked
        assert any(row["event"] == "live.received" and row["type"] == "session.output_audio.fixture.playing"
                   and row.get("command_type") == "session.output_audio.fixture.play" for row in timed)
        details = {row["event"]: json.loads(row["detail"]) for row in rows}
        rejected_append = details["voice.live.command.rejected"]
        assert rejected_append["type"] == "session.thinking.append" and rejected_append["delegation_id"] == "del-1", rejected_append
        assert rejected_append["code"] == "append_rejected", rejected_append
        assert details["voice.live.usage"]["seconds"] == 6.0 and details["voice.live.usage"]["usage_ratio"] == 0.0087
        assert "voice.live.error" not in events, "a rejected command is not a call error"
        # Producer identity and tool outcomes are recorded as rows are written.
        assert all(row.get("deployment_environment") == "local-test" for row in timed), "rows not stamped with their environment"
        agent_rows = [row for row in timed if row["event"] in {"agent.sent", "agent.received"}]
        assert agent_rows and all(isinstance(row.get("agent_connection_id"), str) for row in agent_rows), "Agent rows lack Agent Connection identity"
        assert all(row.get("outcome") in {"ok", "error_result", "returned"} for row in timed if row["event"] == "tool.completed")
        # Live trace context (issue #8, B1): the front door mints the Voice Call's context, and
        # Orchestrator work, Prompt Turns, and Agent requests carry what they minted at start.
        call_trace = {"trace_id": CALL_TRACE_ID, "span_id": CALL_SPAN_ID}
        orchestrated = [row for row in timed if row["event"].startswith(("model.", "tool."))]
        assert orchestrated and all(row.get("trace_id") == call_trace["trace_id"]
                                    and row.get("parent_span_id") == call_trace["span_id"] for row in orchestrated)
        spans_by_request = {}
        for row in orchestrated:
            spans_by_request.setdefault(row.get("request_id") or row.get("tool_call_id"), set()).add(row["span_id"])
        assert all(len(spans) == 1 for spans in spans_by_request.values()), "one span per model request or tool call"
        turns = [json.loads(row["detail"]) for row in rows if row["event"] == "agent.turn.started"]
        assert turns and all(t.get("trace_id") != call_trace["trace_id"] and t.get("link_trace_id") == call_trace["trace_id"]
                             and t.get("link_span_id") == call_trace["span_id"] for t in turns), "turns are linked work traces"
        prompts = [row for row in timed if row["event"] == "agent.sent" and row.get("route") == "/api/session/{id}/prompt"]
        assert prompts and all(any(p.get("trace_id") == t["trace_id"] and p.get("parent_span_id") == t["span_id"] for t in turns)
                               for p in prompts), "the prompt request is a child of its Prompt Turn"
        assert all(p.get("content_kind") == "agent_prompt" for p in prompts), prompts
        requests = {row["agent_request_id"]: row for row in timed if row["event"] == "agent.sent"}
        answers = [row for row in timed if row["event"] == "agent.received" and row.get("agent_request_id") in requests]
        assert answers and all(a.get("span_id") == requests[a["agent_request_id"]].get("span_id")
                               and isinstance(a.get("http_status"), int) for a in answers), "responses name their request"
        # Agent propagation (issue #8, B3): each request carries its own journaled span as a
        # traceparent header (so the prompt's, matching its row above, is a child of the Prompt
        # Turn); events carry none of ours.
        wire = [item["traceparent"] for item in agent.seen]
        assert all(TRACEPARENT.fullmatch(value or "") for value in wire), wire
        for row in requests.values():
            assert f"00-{row['trace_id']}-{row['span_id']}-01" in wire, row
        assert {"/api/session", "/api/session/{id}/prompt", "/api/session/active", "/api/info"} <= {row["route"] for row in requests.values()}
        assert all(item["preview_token"] == "local-preview-token" for item in agent.seen), "the gateway was reached without the preview token"
        # Responses propagation (issue #8, B2): each model request carries its own span as a
        # version-00 traceparent and its request_id as X-Client-Request-Id; the provider's
        # correlation metadata, never content, is journaled on the headers and completed rows.
        sent = {headers.get("x-client-request-id"): headers for headers, _ in OrchestratorMock.requests}
        model_done = [row for row in timed if row["event"] == "model.completed"]
        assert model_done
        for done in model_done:  # Completed rows only: a request may still be in flight.
            start = next(row for row in timed if row["event"] == "model.started" and row["request_id"] == done["request_id"])
            headers = sent.get(start["request_id"])
            assert headers, f"no request carried X-Client-Request-Id {start['request_id']}"
            assert headers.get("traceparent") == f"00-{start['trace_id']}-{start['span_id']}-01", headers.get("traceparent")
            assert "tracestate" not in headers and "baggage" not in headers
            assert (start["request_model"], start["server_address"], start["server_port"], start["reasoning_level"]) == (
                "gpt-6.1-sol", "127.0.0.1", ORCHESTRATOR_MOCK_PORT, "low"), start
            client = start["request_id"].replace("-", "")
            heard = next(row for row in timed if row["event"] == "model.headers" and row["request_id"] == start["request_id"])
            assert heard["provider_request_id"] == f"req_{client}", heard
            assert (done["response_id"], done["response_model"], done["usage"]) == (
                f"resp_{client}", "mock-model-2026-11-10", RECORDED_USAGE), done
            assert done["prompt_cache"] == RECORDED_CACHE_DIAGNOSIS, done
        assert not any({"input", "output", "instructions"} & row.keys() for row in timed if row["event"].startswith("model."))
        # OpenAI-only request options stay off any other Responses-compatible endpoint, like this mock.
        assert not any("prompt_cache_options" in payload or "context" in payload["reasoning"]
                       for _, payload in OrchestratorMock.requests), "OpenAI-only options sent to a compatible endpoint"
        received = next(row for row in timed if row["event"] == "live.received" and row.get("type") == "session.input_transcript.delta")
        assert received["end_ms"] >= received["start_ms"] and received["queue_ms"] >= 0
        assert isinstance(received.get("content_text"), str) and received.get("content_kind") == "caller"
        for row in (row for row in timed if row["event"] == "model.completed"):
            start = next(start for start in timed if start["event"] == "model.started" and start["request_id"] == row["request_id"])
            assert row["ts_ms"] >= start["ts_ms"] and row["ms"] >= 0
        queued = next(row for row in timed if row["event"] == "handoff.queued" and row["delegation_id"] == "del-2")
        started = next(row for row in timed if row["event"] == "handoff.started" and row["delegation_id"] == "del-2")
        # No transcript wait: GPT-Live sends the transcript before the delegation.
        assert 0 <= queued["ts_ms"] - started["ts_ms"] < 300, (started, queued)
        content = await asyncio.to_thread(operator_content, "CA_VOICE_TEST")
        kinds = {entry["kind"] for entry in content["entries"]}
        assert {"caller", "voice", "handoff", "say", "agent_prompt", "agent_report"} <= kinds, kinds
        assert all(entry["source"] in ("saved_transcript", "orchestrator_history") for entry in content["entries"])
        assert any(entry["observed_at_ms"] is not None for entry in content["entries"]), "known tool/handoff IDs lost timing correlation"
        tools = [row for row in timed if row["event"] == "agent.received" and row.get("tool_call_id")]
        assert tools and any(row.get("tool_status") == "completed" for row in tools), tools

        # Handoff 3: leash earlier work, retag it, let it go home, and list what remains.
        await mocks.send_live(transcript("input", "Pick up that parser work from yesterday ", 7000, 8000))
        await mocks.send_live(transcript("input", "as Birch, call it Rowan, then let it go.", 8000, 8800))
        OrchestratorMock.memory_gate.clear()
        await mocks.send_live(delegation("del-3", 8850))
        # Discovery lists every directory (following cursor.next); loading only confirms the
        # session still exists, and closing an idle one asks nothing of the Agent.
        listed = await agent.next_request(lambda item: item["path"] == "/api/session" and item["method"] == "GET")
        assert listed["query"] == {"limit": "50", "order": "desc"}, listed
        listed = await agent.next_request(lambda item: item["path"] == "/api/session" and item["method"] == "GET")
        assert listed["query"] == {"limit": "50", "order": "desc", "cursor": "2"}, listed
        await agent.next_request(lambda item: item["path"] == f"/api/session/{PERSISTED_SESSION_ID}")
        leashed = await next_matching(mocks.live_messages, appended("commentary", LEASH_SAY))
        assert leashed["delegation_id"] == "del-3"
        discovered = OrchestratorMock.tool_output("h3-discover")
        assert discovered["ok"] is True
        assert discovered["sessions"] == [
            {"ref": "r1", "title": PERSISTED_TITLE, "updated_at": "2026-11-06T18:30:00.000Z", "directory": "parser"}
        ]
        discovered_raw = json.dumps(discovered)
        assert "sessionId" not in discovered_raw and "ses_" not in discovered_raw
        assert juniper_id not in discovered_raw
        loaded = OrchestratorMock.tool_output("h3-load")
        assert loaded["ok"] is True and loaded["session"]["alias"] == "Birch"
        assert PERSISTED_SESSION_ID not in json.dumps(loaded)
        retagged = OrchestratorMock.tool_output("h3-retag")
        assert retagged == {"ok": True, "previous": "Birch", "alias": "Rowan"}
        assert OrchestratorMock.tool_output("h3-close")["alias"] == "Rowan"
        remaining = OrchestratorMock.tool_output("h3-list")
        assert [item["alias"] for item in remaining] == ["Juniper"]
        assert "Rowan" not in json.dumps(remaining)
        projects = OrchestratorMock.tool_output("h3-projects")
        assert projects["ok"] is True and projects["projects"] == [
            {"directory": ".", "has_agents_md": True, "has_git": False, "origin": None},
            {
                "directory": "honeydozer", "has_agents_md": True, "has_git": True,
                "origin": "https://github.com/rndmcnlly/honeydozer.git",
            },
        ], projects
        assert "ghp_" not in json.dumps(projects)
        dozer = OrchestratorMock.tool_output("h3-dozer")
        assert dozer["ok"] is True and dozer["session"]["directory"] == "honeydozer", dozer
        news = [m["body"]["location"]["directory"] for m in agent.seen if (m["method"], m["path"]) == ("POST", "/api/session")]
        assert news == ["/home/daytona", "/home/daytona/honeydozer"], news
        ghost = OrchestratorMock.tool_output("h3-ghost")
        assert ghost["ok"] is False and "does not exist" in ghost["error"], ghost
        escape = OrchestratorMock.tool_output("h3-escape")
        assert escape["ok"] is False and "inside the Workspace" in escape["error"], escape
        assert any("test -d" in command and "honeydozer" in command for command in DaytonaMock.executed)

        # compact_context answered at once and briefed the Voice Frontend; its Memory writer is
        # a separate request, still held when the caller asks to hang up.
        await wait_for(outputs_back("h3-compact"))
        assert OrchestratorMock.tool_output("h3-compact")["ok"] is True
        await next_matching(mocks.live_messages, appended("commentary", COMPACTION_BRIEF))
        await wait_for(lambda: OrchestratorMock.memory_requests, 10)
        writer = OrchestratorMock.memory_requests[0]
        assert writer["tool_choice"] == "none" and EARLY_FOCUS in writer["input"][-1]["content"], writer["input"][-1]
        assert any(item.get("call_id") == "h3-compact" and item.get("type") == "function_call_output" for item in writer["input"])

        # Handoff 4: the Voice Frontend already said goodbye, so end_call hangs up at once,
        # with no farewell of the Orchestrator's own. The delegation arrives three times: a
        # replay after an attach keeps its event_id, and a repeat under a new one is still
        # the same delegation; each is one handoff.
        await mocks.send_live(transcript("input", "Have Dozer tidy its build. Thanks, hang up now.", 9000, 10000))
        await mocks.send_live({**delegation("del-4", 10050), "event_id": "evt-del-4"})
        await mocks.send_live({**delegation("del-4", 10050), "event_id": "evt-del-4"})
        await mocks.send_live({**delegation("del-4", 10050), "event_id": "evt-del-4-again"})
        await wait_for(lambda: any(path == "/live/sessions/live-test/hangup" for path, _ in DaytonaMock.openai_requests), 8)
        await wait_for(outputs_back("h4-end"))
        dozer_id = agent.session_for("/home/daytona/honeydozer")
        assert OrchestratorMock.tool_output("h4-dozer")["turn_state"] == "in_progress"
        assert agent.active == {dozer_id}, agent.active
        assert OrchestratorMock.tool_output("h4-end")["status"] == "ending call"
        assert not any(m.get("type") == "session.commentary.append" and m.get("delegation_id") == "del-4"
                       for m in mocks.seen), "end_call spoke a farewell of its own"
        assert not OrchestratorMock.memory_gate.is_set(), "the hangup waited for the Memory writer"

        # Between calls: the provider closes the sideband, which ends the voice session and
        # writes the next welcome; the Agent asks for permission while nobody is on the line;
        # then the Agent Connection drops.
        await mocks.live.close()
        await wait_for(lambda: OrchestratorMock.greeting_requests, 10)
        greeting_request = OrchestratorMock.greeting_requests[-1]
        assert greeting_request["tools"] and greeting_request["model"] == "gpt-6.1-sol"
        assert "call_ended" in json.dumps(greeting_request["input"])
        handoffs = [item for item in greeting_request["input"]
                    if item.get("role") == "user" and "[handoff del-4]" in str(item.get("content"))]
        assert len(handoffs) == 1, handoffs
        # The writer finishes after the call: the next call starts with its Memory.
        OrchestratorMock.memory_gate.set()
        await wait_for(lambda: any((item.get("memory") or {}).get("text") == EARLY_MEMORY for item in conversations()), 15)
        await agent.ask_permission(juniper_id, "per_between1", "bash", ["python -m pytest"], ["python *"])
        await asyncio.sleep(0.5)
        # As in production, the Agent has since associated Juniper with a project directory,
        # which the next catch-up adopts.
        agent.sessions[juniper_id]["location"]["directory"] = "/home/daytona/honeydozer"
        # The event stream drops between calls, and nobody reconnects it; meanwhile Dozer's turn
        # ends unheard. Its end reaches the next call through catching up, once.
        await agent.close_events(1012, "service restarting")
        await asyncio.sleep(0.3)
        await agent.text(dozer_id, AWAY_REPORT)
        agent.settle(dozer_id, "succeeded")

        # Second call: a returning caller with verified caller ID. The Agent's work never
        # depended on the event stream, so reconnecting only catches up.
        mocks.live_connected.clear()
        body, webhook_identity, signature = signed_webhook("CA_VOICE_TEST2", "live-test-2")
        assert await asyncio.to_thread(post_webhook, body, webhook_identity, signature) == 204
        await asyncio.wait_for(mocks.live_connected.wait(), 8)
        opening = await next_matching(mocks.live_messages, appended("commentary", RETURN_OPENING))
        assert opening["delegation_id"] is None
        await agent.next_request(lambda item: item["path"] == f"/api/session/{dozer_id}/message", 10)
        assert agent.events_connections == 2 and DaytonaMock.preview_reads == 1, (
            "the Agent Gateway preview URL was not reused", agent.events_connections, DaytonaMock.preview_reads,
        )
        # Dozer's unheard end was finished from the snapshot and reached the Orchestrator.
        dozer_marker = 'turn_stopped: {"alias":"Dozer","outcome":"completed"'
        await wait_for(lambda: dozer_marker in latest_user_text(), 10)
        # A returning caller's call_started and agent_connected snooze (#58): they reach the model
        # with the event that woke it (here the Permission Request asked between calls, whose wake
        # waited for this call, or Dozer's end), or wait in the queue for the next, never alone.
        returning = [str(payload["input"][-1].get("content")) for _, payload in OrchestratorMock.requests
                     if RETURN_OPENING in str(payload["input"][-1].get("content"))]
        assert returning and all("permission_requested:" in text or dozer_marker in text for text in returning), returning
        await wait_for(lambda: agent_connected_count() >= 2, 10)
        assert AWAY_REPORT in OrchestratorMock.user_message(dozer_marker)
        second_rows = await asyncio.to_thread(call_activity_rows, "CA_VOICE_TEST2")
        caught_up = [json.loads(row["detail"]) for row in second_rows if row["event"] == "agent.turn.caught_up"]
        assert [(item["alias"], item["outcome"], item["session_outcome"]) for item in caught_up] == [("Dozer", "completed", "succeeded")], caught_up
        repaired = [json.loads(row["detail"]) for row in second_rows if row["event"] == "agent.session.directory_repaired"]
        assert [(item["alias"], item["from"], item["to"]) for item in repaired] == [("Juniper", ".", "honeydozer")], repaired
        second_start = next(
            str(item["content"]) for item in OrchestratorMock.requests[-1][1]["input"]
            if item.get("role") == "user" and RETURN_OPENING in str(item["content"])
        )
        assert "first_call" not in second_start and "[harness event] agent_connecting" in second_start
        assert '"stir_verstat":"TN-Validation-Passed-A"' in second_start
        # The Roaming notice was told once, in the first Opening: every reported attempt is now noticed.
        unnoticed = await asyncio.to_thread(d1, "SELECT call_sid FROM roaming_attempts WHERE noticed_at IS NULL ORDER BY call_sid")
        assert [row["call_sid"] for row in unnoticed] == ["CA_ROAM_QUIT"], unnoticed

        # Keypad conventions, all handled without the Orchestrator.
        await mocks.send_live(dtmf("1"))
        status = await next_matching(mocks.live_messages, lambda item: item.get("type") == "session.commentary.append" and "Juniper" in item.get("content", ""))
        # The Permission Request asked between calls is still pending with the Agent, so catching
        # up kept it.
        assert status["content"].startswith("Juniper needs permission. Dozer is resting") and status["delegation_id"] is None, status
        # GPT-Live rejects the silence fixture by its event_id: the held instruction goes out
        # at once, well inside the 2.1 s unconfirmed-playback fallback.
        mocks.reject_next_fixture = True
        await mocks.send_live(dtmf("#"))
        await next_matching(mocks.live_messages, of_type("session.output_audio.fixture.play"))
        stop = await next_matching(mocks.live_messages, of_type("session.instructions.append"), 1)
        assert "pressed pound" in stop["content"]
        def fixture_rejections() -> list[dict]:
            return [json.loads(row["detail"]) for row in call_activity_rows("CA_VOICE_TEST2")
                    if row["event"] == "voice.fixture.rejected"]
        await wait_for(fixture_rejections)
        rejected_fixture = fixture_rejections()
        assert rejected_fixture and rejected_fixture[-1]["label"] == "stop_talking", rejected_fixture
        assert rejected_fixture[-1]["message"] == "Fixture rejected.", rejected_fixture
        await mocks.send_live(transcript("output", "The parser handles nested quotes now.", 20000, 22000))
        await mocks.send_live(dtmf("0"))
        repeat = await next_matching(mocks.live_messages, lambda item: item.get("type") == "session.commentary.append" and "word for word" in item.get("content", ""))
        assert "The parser handles nested quotes now." in repeat["content"]

        # A turn that fails mid-command. Its command is named by phase, never by its text; steps
        # failing in a burst make one beat that says the turn goes on; and once the turn has
        # failed its command is closed, so nothing about it is still running.
        await mocks.send_live(transcript("input", "Have Juniper check the repository.", 22500, 23100))
        await mocks.send_live(delegation("del-f", 23150))
        await wait_for(lambda: "Juniper is working with Git." in beats(), 10)
        await wait_for(lambda: "3 steps in a row failed for Juniper, the latest while looking something up on the web; its turn is still going." in beats(), 10)
        await wait_for(lambda: any(item.get("type") == "function_call_output" and item.get("call_id") == "f-inspect" for _, payload in OrchestratorMock.requests for item in payload["input"]), 15)
        failed_at = len(beats())
        # The compaction the first call's hangup did not wait for leads the second call's context.
        second = next(payload["input"] for _, payload in OrchestratorMock.requests
                      if any("[handoff del-f]" in str(item.get("content")) for item in payload["input"]))
        assert second[0] == {"role": "developer", "content": f"[memory] {EARLY_MEMORY}"}, second[0]
        assert "git status" not in beats() and "--short" not in beats() and "(" not in beats(), beats()
        assert '"followed":true' in OrchestratorMock.user_message("turn_failed")
        failed = OrchestratorMock.tool_output("f-inspect")
        assert failed["turn_state"] == "failed" and "SseError" in failed["failure"], failed
        assert [(c["kind"], c["status"]) for c in failed["recent_tool_calls"]] == [("execute", "failed")] + [("fetch", "failed")] * 3, failed
        await asyncio.sleep(4)
        assert not re.search(r"running|working with|still|failed for", beats()[failed_at:]), beats()[failed_at:]

        # An out-of-memory kill reaches the Orchestrator as a sayable reason, and the
        # Agent Connection comes back.
        await mocks.send_live(transcript("input", "Have Juniper install the dependencies.", 23500, 24100))
        await mocks.send_live(delegation("del-o", 24150))
        await wait_for(lambda: any(item.get("type") == "function_call_output" and item.get("call_id") == "o-inspect" for _, payload in OrchestratorMock.requests for item in payload["input"]), 15)
        unavailable = OrchestratorMock.user_message('"failed_turns":["Juniper"]')
        assert OOM_SENTENCE in unavailable and '"agent_termination":"out_of_memory"' in unavailable, unavailable
        assert '"agent_termination":"out_of_memory"' in OrchestratorMock.user_message("turn_failed: {\"alias\":\"Juniper\",\"followed\":true,\"error\":\"The Agent was killed")
        killed = OrchestratorMock.tool_output("o-inspect")
        assert killed["turn_state"] == "failed" and killed["failure"] == f"Prompt Turn failed: {OOM_SENTENCE}", killed
        await wait_for(lambda: agent_connected_count() >= 3, 15)
        await run_turn_control(mocks, juniper_id, beats)

        # Star opens the System Menu: muted input, a prompt clip, star goes back. Status and
        # sound style are no longer in it.
        await mocks.send_live(dtmf("*"))
        await next_matching(mocks.live_messages, of_type("session.input_audio.mute"))
        await next_matching(mocks.live_messages, of_type("session.output_audio.fixture.play"))
        await mocks.send_live(dtmf("*"))
        await next_matching(mocks.live_messages, of_type("session.input_audio.unmute"))
        # Menu one asks for a second press before restarting; anything else backs out.
        await mocks.send_live(dtmf("*"))
        await next_matching(mocks.live_messages, of_type("session.input_audio.mute"))
        await mocks.send_live(dtmf("1"))
        await mocks.send_live(dtmf("2"))
        await next_matching(mocks.live_messages, of_type("session.input_audio.unmute"))

        # Other digits still reach the Orchestrator with their conversational context.
        await mocks.send_live(dtmf("7"))
        await next_matching(mocks.live_messages, appended("commentary", KEYPAD_SAY))
        bound_invitee = await run_invitations(mocks)

        # Sound style is a voice tool: it stores the preference and previews the opening sound.
        await mocks.send_live(dtmf("6"))
        await next_matching(mocks.live_messages, of_type("session.output_audio.fixture.play"), 15)
        await wait_for(lambda: "k6-style" in {item.get("call_id") for _, payload in OrchestratorMock.requests for item in payload["input"] if item.get("type") == "function_call_output"}, 10)

        # The tool returns a spoken code, never an audio fixture. Read its result through the
        # Orchestrator's actual tool-response lane, not from internal VoiceSession storage.
        async def next_pairing_code(call_id: str) -> str:
            await wait_for(outputs_back(call_id), 15)
            result = OrchestratorMock.tool_output(call_id)
            assert result["ok"] is True and result["expires_in_seconds"] == 90, result
            code = result["code"]
            assert re.fullmatch(r"[1-9]\d{0,2}-[a-z]+-[a-z]+", code), result
            source = (WORKER_DIR / "src" / "pairing.ts").read_text()
            odd = re.search(r"PAIRING_ODD_WORDS\s*=\s*\(`([^`]+)`", source).group(1).split()
            even = re.search(r"PAIRING_EVEN_WORDS\s*=\s*\(`([^`]+)`", source).group(1).split()
            assert len(set(odd)) == len(odd) == 256 and len(set(even)) == len(even) == 256
            _, first, second = code.split("-")
            assert first in odd and second in even, "PGP odd word precedes even word"
            return code

        fixture_count = sum(m.get("type") == "session.output_audio.fixture.play" for m in mocks.seen)
        pairing_started = time.monotonic()
        await mocks.send_live(dtmf("8"))
        unclaimed_code = await next_pairing_code("k8-pair")
        await next_matching(mocks.live_messages, appended("commentary", f"Pairing code {unclaimed_code}"))
        assert sum(m.get("type") == "session.output_audio.fixture.play" for m in mocks.seen) == fixture_count
        # Exercise the real expiry timer: changing D1 alone would not advance the object's
        # in-memory clock. This intentionally adds about 90 seconds to the suite.
        await wait_for(lambda: "[harness event] pairing_unclaimed" in latest_user_text(), 100)
        assert time.monotonic() - pairing_started >= 89, "no premature unclaimed notification"
        unclaimed = OrchestratorMock.user_message("[harness event] pairing_unclaimed")
        assert '"expired":true' in unclaimed and '"lifetime_seconds":90' in unclaimed, unclaimed
        assert not any(key in unclaimed for key in ("baud", "decode_failures", "symbols_heard")), unclaimed
        await next_matching(mocks.live_messages, appended("commentary", REPLAY_SAY))
        code = await next_pairing_code("pu-pair")
        await next_matching(mocks.live_messages, appended("commentary", f"Pairing code {code}"))
        assert sum(m.get("type") == "session.output_audio.fixture.play" for m in mocks.seen) == fixture_count
        assert code != unclaimed_code
        nameplate = code.split("-")[0]
        # A fresh attempt may legitimately reuse the public nameplate. In that case the
        # stale secret is a guess against the new code; expiry is also tested below in D1.
        if unclaimed_code.split("-")[0] != nameplate:
            status, _ = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": unclaimed_code})
            assert status == 404, "an expired Pairing Code cannot be claimed"
        for malformed in (None, 123, "", "0-adroitness-aardvark", "1000-adroitness-aardvark", "1-one", "1-one-two-three", "x-one-two", "x" * 101):
            status, body = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": malformed})
            assert status == 400, (malformed, status, body)
        assert (await asyncio.to_thread(d1, f"SELECT attempts FROM pairing_codes WHERE nameplate = '{nameplate}'")) == [{"attempts": 0}]
        # Unknown words are guesses too, not a free dictionary oracle. Four failures leave
        # the fifth guess available; concurrent correct claims can consume it only once.
        for attempt in range(1, 5):
            status, body = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": f"{nameplate}-notaword-notawordeither"})
            assert status == 404, (status, body)
            assert (await asyncio.to_thread(d1, f"SELECT attempts FROM pairing_codes WHERE nameplate = '{nameplate}'")) == [{"attempts": attempt}]
        normalized = "  " + code.upper().replace("-", " -  ") + "  "
        claims = await asyncio.gather(*(
            asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": value}, {"user-agent": CHROME_MAC})
            for value in (normalized, code.upper().replace("-", " "))
        ))
        assert sorted(status for status, _ in claims) == [200, 404], claims
        status, claim = next(result for result in claims if result[0] == 200)
        assert status == 200 and claim["ticket"].startswith(identity + "."), (status, claim)
        named = await next_matching(mocks.live_messages, lambda m: m.get("type") == "session.commentary.append" and "submitted the pairing code" in m.get("content", ""))
        assert "Chrome on a Mac" in named["content"] and "press one" in named["content"]
        status, _ = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": code})
        assert status == 404, "a Pairing Code works once"
        ticket = {"authorization": f"Bearer {claim['ticket']}"}
        assert (await asyncio.to_thread(pair_http, "GET", "/pair/claim/status", None, ticket))[1] == {"state": "waiting"}
        await mocks.send_live(dtmf("1"))
        def heard(fragment: str) -> bool:
            """Delivered to the Orchestrator: read in a step, or snoozed in its queue (#58)."""
            return any(fragment in str(item.get("content")) for _, payload in OrchestratorMock.requests for item in payload["input"]) \
                or any(fragment in line for line in queued_lines())

        paired_note = (await next_matching(mocks.live_messages, lambda m: m.get("type") == "session.commentary.append" and "browser is paired" in m.get("content", "")))["content"]
        assert 'as the display "main"' in paired_note and "mirroring" not in paired_note, paired_note
        first_pairing = OrchestratorMock.tool_output("pu-pair")
        assert first_pairing["display"] == "main" and first_pairing["joins_existing_display"] is False, first_pairing
        accepted = (await asyncio.to_thread(pair_http, "GET", "/pair/claim/status", None, ticket))[1]
        assert accepted["state"] == "accepted" and accepted["token"].startswith(identity + ".")
        assert (await asyncio.to_thread(pair_http, "GET", "/pair/claim/status", None, ticket))[1] == {"state": "accepted"}, "the token is handed out once"
        token = accepted["token"]
        await wait_for(lambda: heard('browser_pairing: {"outcome":"accepted","display":"main"'), 10)
        # The page carries its build identity and sends it when it connects.
        page = await asyncio.to_thread(lambda: urllib.request.urlopen(f"http://127.0.0.1:{WORKER_PORT}/pair", timeout=10).read().decode())
        build = re.search(r'const PAIR_BUILD = "([0-9a-f]{8})";', page).group(1)
        assert "__PAIR_BUILD__" not in page

        async def open_page(credential: str, display: str):
            """A paired page joins its Display: hello, then that Display's boards, and nothing else."""
            page = await connect(f"ws://127.0.0.1:{WORKER_PORT}/pair/ws?for={identity}")
            await page.send(json.dumps({"type": "auth", "token": credential, "build": build}))
            hello = json.loads(await page.recv())
            assert hello["type"] == "hello" and hello["display"] == display, hello
            boards = json.loads(await page.recv())
            assert boards["type"] == "boards", boards
            return page, hello, boards["boards"]

        async def pong_after(page) -> list[dict]:
            """What a page received before the answer to a ping sent now."""
            await page.send(json.dumps({"type": "ping"}))
            before = []
            async with asyncio.timeout(8):
                while (message := json.loads(await page.recv()))["type"] != "pong":
                    before.append(message)
            return before

        async def ask(page, message: dict) -> dict:
            """A page request and its answer (opened, file, or error), by id."""
            await page.send(json.dumps(message))
            async with asyncio.timeout(15):
                while True:
                    reply = json.loads(await page.recv())
                    if reply.get("id") == message["id"] and reply.get("type") in ("opened", "file", "error"):
                        return reply

        def file(path: str, board: str, credential: str | None = None, **params) -> tuple[int, dict, bytes]:
            """A board's file, with the credential in a header (never the URL)."""
            query = urllib.parse.urlencode({"path": path, "board": board, **params})
            status, raw, body = pair_raw(f"/pair/file?{query}", {"authorization": f"Bearer {credential or token}"})
            return status, {key.lower(): value for key, value in raw.items()}, body

        terminal_url = f"ws://127.0.0.1:{WORKER_PORT}/pair/terminal/ws?for={identity}"

        async def terminal_ready(credential: dict) -> dict:
            async with connect(terminal_url) as terminal:
                await terminal.send(json.dumps({**credential, "type": "auth", "cols": 100, "rows": 30}))
                reply = await recv_type(terminal, "ready")
                # The mock toolbox has no PTY, so the relay reports why and closes.
                assert "terminal" in (await recv_type(terminal, "notice"))["text"]
                return reply

        async def terminal_refused(credential: dict) -> None:
            async with connect(terminal_url) as terminal:
                await terminal.send(json.dumps({**credential, "type": "auth"}))
                assert "expired" in (await recv_type(terminal, "error"))["error"]

        browser, hello, boards = await open_page(token, "main")
        assert hello["device"] == "Chrome on a Mac" and hello["idle_expires_at"] > time.time() * 1000
        assert hello["build"] == build and hello["stale"] is False, hello
        assert boards == [], boards
        # The page has no views of its own: no listing, no reading what no board names, no launching.
        await browser.send(json.dumps({"type": "list", "id": 1, "path": ""}))
        await browser.send(json.dumps({"type": "launch", "id": 2, "kind": "terminal"}))
        assert await pong_after(browser) == [], "removed requests answer nothing"
        unnamed = await ask(browser, {"type": "read", "id": 3, "path": "REPORT.md"})
        assert unnamed["type"] == "error" and "no board on this display" in unnamed["error"], unnamed
        # The Orchestrator learns the Display's page is open.
        await wait_for(lambda: heard('browser_connected: {"display":"main"}'), 10)
        # A tab loaded before a deploy: one with another build is told to reload itself; one
        # from before builds existed cannot, so the Orchestrator hears it must ask the caller.
        async with connect(f"ws://127.0.0.1:{WORKER_PORT}/pair/ws?for={identity}") as newer:
            await newer.send(json.dumps({"type": "auth", "token": token, "build": "00000000"}))
            told = await recv_type(newer, "hello")
            assert told["stale"] is True and told["build"] == build and told["display"] == "main", told
        await wait_for(lambda: heard('browser_page_stale: {"display":"main","reloads_itself":true}'), 10)
        stale = await connect(f"ws://127.0.0.1:{WORKER_PORT}/pair/ws?for={identity}")
        await stale.send(json.dumps({"type": "auth", "token": token}))
        assert (await recv_type(stale, "hello"))["stale"] is True
        await wait_for(lambda: heard('browser_page_stale: {"display":"main","reloads_itself":false}'), 10)
        await mocks.send_live(dtmf("4"))
        await wait_for(outputs_back("k4-state"), 15)
        state = OrchestratorMock.tool_output("k4-state")
        assert "paired_browser" not in state and "whiteboard" not in state, state
        [main_display] = state["displays"]
        assert main_display["name"] == "main" and main_display["page_open"] and main_display["page_stale"] is True, main_display
        assert [item["device"] for item in main_display["browsers"]] == ["Chrome on a Mac"], main_display
        assert main_display["boards"] == 0 and main_display["latest"] is None and main_display["access"] == [], main_display
        assert 55 <= main_display["expires_in_minutes"] <= 60, main_display
        assert state["earcon_style"] == "tonal", state
        assert state["context_tokens"] == 1 and state["memory"]["chars"] == len(EARLY_MEMORY) and "compacting" not in state["memory"], state
        # The Orchestrator draws a board; the caller answers it on the page; it hears the answer.
        await mocks.send_live(dtmf("5"))
        board = (await recv_type(browser, "board", 15))["board"]
        assert board["program"] == BOARD_PROGRAM and board["status"] == "open" and board["note"] == "pick one" and board["display"] == "main", board
        # Dogwalk keeps the program, never copies of the files it shows: only which failed, and why.
        assert "images" not in board and list(board["failed"]) == ["big.png"] and "WebP" in board["failed"]["big.png"], board
        assert set(board["access"]) == {"terminal:", "preview:3000"}, board["access"]
        assert all(59.9 < (grant["expires_at"] - board["at"]) / 60_000 <= 60.1 for grant in board["access"].values()), board["access"]
        checks = (await recv_type(browser, "board", 15))["board"]
        await wait_for(outputs_back("k5-board", "k5-checks"), 15)
        drawn = OrchestratorMock.tool_output("k5-board")
        assert drawn["display"] == "main" and drawn["page_open"] is True and "not_seen" not in drawn, drawn
        assert "page_stale" in drawn, "draw_on_whiteboard warns of the stale page"
        assert list(drawn["files_failed"]) == ["big.png"] and "WebP" in drawn["files_failed"]["big.png"], drawn
        assert sorted((item["kind"], item["target"], item["minutes"]) for item in drawn["access"]) == [("preview", "3000", 60), ("terminal", "", 60)], drawn
        assert "access_refused" not in drawn, drawn
        # Each file is checked when drawn: present, in the Workspace, the image its name says, not too large.
        failed = OrchestratorMock.tool_output("k5-checks")["files_failed"]
        reasons = {
            "notes.png": "not a PNG", "huge.png": "WebP", "wide.png": "megapixels", "pixels.png": "megapixels",
            "escape.png": "outside", "missing.png": "does not exist", "https://example.com/a.png": "never a URL",
            "//example.com/a.png": "never a URL", "missing.md": "does not exist",
        }
        assert set(failed) == set(reasons) and checks["failed"] == failed, failed
        for path, why in reasons.items():
            assert why in failed[path], (path, failed[path])
        assert not any("example.com" in command for command in DaytonaMock.executed)
        # The stale page cannot render Image; its error says the page is out of date.
        await stale.send(json.dumps({"type": "board_error", "id": board["id"], "error": "line 2: unknown component Image (left out)"}))
        await wait_for(lambda: any("whiteboard_error" in str(item.get("content")) and "out of date" in str(item.get("content")) for _, payload in OrchestratorMock.requests for item in payload["input"]), 10)
        await stale.close()
        OrchestratorMock.memory_gate.clear()
        await browser.send(json.dumps({"type": "board_answer", "id": board["id"], "answer": {"button": "Send", "values": {"approach": "Refactor"}, "note": ""}}))
        answered = (await recv_type(browser, "board"))["board"]
        assert answered["status"] == "answered" and answered["answer"]["values"] == {"approach": "Refactor"}, answered
        await wait_for(lambda: heard("whiteboard_answer") and heard("Refactor"), 15)
        assert '"display":"main"' in OrchestratorMock.user_message("[harness event] whiteboard_answer")
        # The Orchestrator compacts after the answer: an over-long focus is refused, then the
        # compaction starts and the Voice Frontend is briefed; its Memory writer is held while
        # the call goes on.
        await wait_for(lambda: any(item.get("call_id") == "cc-memory" and item.get("type") == "function_call_output" for _, payload in OrchestratorMock.requests for item in payload["input"]), 15)
        refused_focus = OrchestratorMock.tool_output("cc-long")
        assert refused_focus["ok"] is False and "at most 500" in refused_focus["error"], refused_focus
        await next_matching(mocks.live_messages, appended("commentary", COMPACTION_BRIEF))
        await wait_for(lambda: len(OrchestratorMock.memory_requests) == 2, 10)
        assert OrchestratorMock.memory_requests[1]["input"][0] == {"role": "developer", "content": f"[memory] {EARLY_MEMORY}"}
        # A second answer to the same board is ignored.
        await browser.send(json.dumps({"type": "board_answer", "id": board["id"], "answer": {"values": {"approach": "Quick fix"}}}))
        # A board's files are read from the Sandbox when shown, with the credential in a header,
        # and only as the board names them: a Document as written, an Image as an image.
        status, headers, body = await asyncio.to_thread(file, "REPORT.md", board["id"])
        assert status == 200 and body == REPORT_TEXT.encode(), (status, body[:80])
        assert headers.get("content-type") == "application/octet-stream" and headers.get("content-disposition", "").startswith("inline")
        assert "sandbox" in headers.get("content-security-policy", "")
        for path, board_id, params in [
            ("REPORT.md", "", {}), ("/etc/passwd", board["id"], {}), ("/home/daytona/REPORT.md", board["id"], {}),
            ("diagram.svg", board["id"], {}), ("REPORT.md", board["id"], {"as": "image"}), ("REPORT.md", board["id"], {"download": "1"}),
            ("REPORT.md", checks["id"], {}),
        ]:
            status, _, body = await asyncio.to_thread(file, path, board_id, **params)
            assert status == 404 and b"no board on this display" in body, (path, board_id, params, status, body)
        assert (await asyncio.to_thread(file, "REPORT.md", board["id"], f"{identity}.{'0' * 48}"))[0] == 401
        # Images: the type comes from the bytes, and an SVG opened alone runs and loads nothing.
        status, headers, body = await asyncio.to_thread(file, "diagram.svg", board["id"], **{"as": "image"})
        assert status == 200 and body == SVG_BYTES and headers.get("content-type") == "image/svg+xml", (status, headers)
        assert headers.get("x-content-type-options") == "nosniff" and "no-store" in headers.get("cache-control", "")
        csp = headers.get("content-security-policy", "")
        assert "default-src 'none'" in csp and "style-src 'unsafe-inline'" in csp and "img-src data:" in csp, csp
        # A file that failed when drawn says why.
        for path, board_id, why in [("big.png", board["id"], b"WebP"), ("notes.png", checks["id"], b"not a PNG")]:
            status, _, body = await asyncio.to_thread(file, path, board_id, **{"as": "image"})
            assert status == 404 and why in body, (path, status, body)
        # Never copied: a file gone since the draw is gone from the board too.
        removed = IMAGE_FILES.pop("diagram.svg")
        try:
            assert (await asyncio.to_thread(file, "diagram.svg", board["id"], **{"as": "image"}))[0] == 404
        finally:
            IMAGE_FILES["diagram.svg"] = removed
        # Documents are read over the page's socket, likewise only as a board names them.
        report = await ask(browser, {"type": "read", "id": 4, "path": "REPORT.md", "board_id": board["id"]})
        assert report["type"] == "file" and report["text"] == REPORT_TEXT and not report["truncated"], report
        for path, board_id in [("../../etc/passwd", board["id"]), ("diagram.svg", board["id"]), ("REPORT.md", checks["id"]), ("REPORT.md", "bnope")]:
            refused = await ask(browser, {"type": "read", "id": 5, "path": path, "board_id": board_id})
            assert refused["type"] == "error" and "no board on this display" in refused["error"], (path, refused)
        # An HTML report's Workspace images are inlined when it is read for the page.
        html = (await ask(browser, {"type": "read", "id": 9, "path": "report.html", "board_id": board["id"]}))["text"]
        assert f'src="data:image/png;base64,{base64.b64encode(PNG_BYTES).decode()}"' in html and "srcset" not in html, html[:400]
        assert 'src="https://example.com/a.png"' in html and 'src="data:image/gif;base64,R0lG"' in html, html
        assert 'src="/etc/x.png" alt="Outside (image not shown: only files under /home/daytona can be read)"' in html, html
        # Human Takeover: the board's Terminal opens with a one-time ticket in a URL fragment.
        opened = await ask(browser, {"type": "open", "id": 7, "board_id": board["id"], "key": "terminal:"})
        assert opened["type"] == "opened" and f"/pair/terminal#t={identity}." in opened["url"], opened
        ticket_credential = opened["url"].split("#t=", 1)[1]
        ready = await terminal_ready({"ticket": ticket_credential})
        terminal_token = ready["token"]
        assert terminal_token.startswith(identity + ".") and terminal_token != ticket_credential
        assert ready["expires_at"] == board["access"]["terminal:"]["expires_at"], "the terminal lasts as long as its grant"
        assert PROVIDER["pty"][-1] == "human", PROVIDER["pty"]
        await terminal_refused({"ticket": ticket_credential})  # a launch ticket works once
        assert (await terminal_ready({"token": terminal_token}))["token"] == terminal_token, "a reload reconnects with its token"
        # A Preview opens as a provider-signed link, bounded by its grant.
        opened = await ask(browser, {"type": "open", "id": 8, "board_id": board["id"], "key": "preview:3000"})
        docs_link = PROVIDER["signed"][-1]
        assert docs_link["port"] == 3000 and 3500 <= docs_link["seconds"] <= 3600, docs_link
        assert opened == {"type": "opened", "id": 8, "url": f"https://3000-{docs_link['token']}.preview.test"}, opened
        # What was never granted, or granted on another board, opens nothing.
        for key, board_id, why in [("preview:8080", board["id"], "not granted"), ("desktop:", board["id"], "not granted"),
                                   ("terminal:", checks["id"], "not granted"), ("terminal:", "bnope", "no longer on this display")]:
            refused = await ask(browser, {"type": "open", "id": 6, "board_id": board_id, "key": key})
            assert refused["type"] == "error" and why in refused["error"], (key, refused)
        # Five newer boards retire the first two from the Display, with the grants they carried.
        await mocks.send_live(dtmf("A"))
        icons = [(await recv_type(browser, "board", 15))["board"] for _ in range(5)]
        await wait_for(outputs_back("kA-evict-5"), 15)
        icon = OrchestratorMock.tool_output("kA-evict-5")
        assert icon["ok"] and "files_failed" not in icon and "access" not in icon, icon
        # Five boards drawn while the writer was held: the cut, once it lands, keeps them and
        # sheds only what the writer read before the current call.
        assert not OrchestratorMock.memory_gate.is_set()
        OrchestratorMock.memory_gate.set()
        # A compaction of its own (no focus) snoozes: its outcome waits for the next woken step (#58).
        await wait_for(lambda: any("context_compacted" in line for line in queued_lines()), 15)
        compacted = next(item for item in await asyncio.to_thread(conversations) if item.get("memory"))["orchestrator_context"]
        assert not any(item.get("call_id") == "h1-create" for item in compacted), "the first call survived compaction"
        assert compacted[0].get("role") == "user", compacted[0]
        calls_in = {item["call_id"] for item in compacted if item.get("type") == "function_call"}
        outputs_in = {item["call_id"] for item in compacted if item.get("type") == "function_call_output"}
        assert calls_in == outputs_in and {"cc-memory", "kA-evict-1", "kA-evict-5"} <= calls_in, (calls_in ^ outputs_in, calls_in)
        diagnostics = next(item for item in await asyncio.to_thread(conversations) if item.get("memory"))
        assert diagnostics["memory"]["text"] == MEMORY_TEXT and diagnostics["memory_versions"][-1]["chars"] == len(MEMORY_TEXT), diagnostics["memory_versions"]
        assert diagnostics["context_archives"][-1]["reason"] == "compaction", diagnostics["context_archives"]
        assert not any(item.get("call_id") == "h1-create" for item in diagnostics["orchestrator_context"])
        assert (3000, docs_link["token"]) in PROVIDER["expired"], "a dropped board's signed link is expired"
        await terminal_refused({"token": terminal_token})
        assert (await asyncio.to_thread(file, "REPORT.md", board["id"]))[0] == 404, "a dropped board's files are no longer read"

        # A second Display: pairing it leaves the first paired and its page open.
        async def pair_another(call_id: str) -> tuple[str, str]:
            code = await next_pairing_code(call_id)
            await next_matching(mocks.live_messages, appended("commentary", f"Pairing code {code}"))
            status, claim = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": code}, {"user-agent": CHROME_MAC})
            assert status == 200, (status, claim)
            await next_matching(mocks.live_messages, lambda m: m.get("type") == "session.commentary.append" and "submitted the pairing code" in m.get("content", ""))
            await mocks.send_live(dtmf("1"))
            said = await next_matching(mocks.live_messages, lambda m: m.get("type") == "session.commentary.append" and "browser is paired" in m.get("content", ""))
            accepted = (await asyncio.to_thread(pair_http, "GET", "/pair/claim/status", None, {"authorization": f"Bearer {claim['ticket']}"}))[1]
            assert accepted["state"] == "accepted", accepted
            return accepted["token"], said["content"]

        await mocks.send_live(transcript("input", "Pair my laptop as the slides display.", 100000, 101000))
        await mocks.send_live(delegation("del-wb-slides", 101050))
        slides_token, said = await pair_another("wb-pair")
        assert OrchestratorMock.tool_output("wb-pair")["display"] == "slides" and not OrchestratorMock.tool_output("wb-pair")["joins_existing_display"]
        assert 'as the display "slides"' in said and "mirroring" not in said, said
        assert await pong_after(browser) == [], "the first browser stays paired, its page open"
        await wait_for(outputs_back("wb-unnamed", "wb-ghost", "wb-slides"), 15)
        unnamed = OrchestratorMock.tool_output("wb-unnamed")
        assert unnamed["ok"] is False and "name the display" in unnamed["error"] and "main, slides" in unnamed["error"], unnamed
        assert 'no display called "kitchen"' in OrchestratorMock.tool_output("wb-ghost")["error"]
        slide = OrchestratorMock.tool_output("wb-slides")
        assert slide["ok"] and slide["display"] == "slides" and slide["page_open"] is False and "not_seen" in slide, slide
        assert not any(item["type"] == "board" for item in await pong_after(browser)), "a board goes only to its own Display"
        slides, hello, slide_boards = await open_page(slides_token, "slides")
        assert [item["id"] for item in slide_boards] == [slide["board_id"]] and slide_boards[0]["display"] == "slides", slide_boards
        await wait_for(lambda: heard('browser_connected: {"display":"slides"}'), 10)
        # A browser reads only what a board on its own Display names.
        status, headers, body = await asyncio.to_thread(file, "/home/daytona/chart.png", icons[-1]["id"], **{"as": "image"})
        assert status == 200 and body == PNG_BYTES and headers.get("content-type") == "image/png", (status, headers)
        assert (await asyncio.to_thread(file, "/home/daytona/chart.png", icons[-1]["id"], slides_token, **{"as": "image"}))[0] == 404
        assert (await asyncio.to_thread(file, "chart.png", slide["board_id"], slides_token, **{"as": "image"}))[0] == 200
        assert (await asyncio.to_thread(file, "chart.png", slide["board_id"], **{"as": "image"}))[0] == 404
        # A browser paired to a Display that has one mirrors it, boards and all.
        await mocks.send_live(transcript("input", "And pair my phone to the main display too.", 102000, 103000))
        await mocks.send_live(delegation("del-wb-mirror", 103050))
        mirror_token, said = await pair_another("wm-pair")
        assert OrchestratorMock.tool_output("wm-pair")["joins_existing_display"] is True and "mirroring" in said, said
        mirror, hello, mirror_boards = await open_page(mirror_token, "main")
        assert [item["id"] for item in mirror_boards] == [item["id"] for item in icons], mirror_boards
        # Renaming keeps a Display's browsers, boards, and grants.
        await mocks.send_live(transcript("input", "Call the slides display the projector, and put the dev server on it.", 104000, 105000))
        await mocks.send_live(delegation("del-wb-end", 105050))
        assert (await recv_type(slides, "display", 15))["name"] == "projector"
        projector_board = (await recv_type(slides, "board", 15))["board"]
        assert projector_board["display"] == "projector" and set(projector_board["access"]) == {"preview:5173"}, projector_board
        await wait_for(outputs_back("we-rename", "we-preview"), 15)
        assert OrchestratorMock.tool_output("we-rename") == {"ok": True, "display": "projector"}
        assert OrchestratorMock.tool_output("we-preview")["page_open"] is True
        assert not heard('browser_disconnected: {"display":"slides"}'), "a rename is not a disconnection"
        opened = await ask(slides, {"type": "open", "id": 1, "board_id": projector_board["id"], "key": "preview:5173"})
        projector_link = PROVIDER["signed"][-1]
        assert opened["url"] == f"https://5173-{projector_link['token']}.preview.test", opened
        # Access Elements: drawn in the call, they are granted on that Display for the minutes asked.
        await mocks.send_live(dtmf("9"))
        access_board = (await recv_type(browser, "board", 15))["board"]
        assert (await recv_type(mirror, "board", 15))["board"]["id"] == access_board["id"], "a Display's browsers mirror it"
        await wait_for(outputs_back("k9-access", "k9-state"), 15)
        drawn = OrchestratorMock.tool_output("k9-access")
        granted = [("desktop", ""), ("download", "REPORT.md"), ("preview", "5173"), ("ssh", ""), ("terminal", "build")]
        assert sorted((item["kind"], item["target"]) for item in drawn["access"]) == granted and {item["minutes"] for item in drawn["access"]} == {90}, drawn
        [gateway] = drawn["access_refused"]
        assert gateway["key"] == "preview:8765" and "Agent Gateway" in gateway["reason"], drawn
        assert set(access_board["access"]) == {f"{kind}:{target}" for kind, target in granted}, access_board["access"]
        assert all(89.9 < (grant["expires_at"] - access_board["at"]) / 60_000 <= 90.1 for grant in access_board["access"].values())
        displays = {item["name"]: item for item in OrchestratorMock.tool_output("k9-state")["displays"]}
        assert set(displays) == {"main", "projector"} and len(displays["main"]["browsers"]) == 2, displays
        assert displays["main"]["latest"]["board_id"] == access_board["id"] and displays["main"]["boards"] == 5, displays["main"]
        assert sorted((item["kind"], item["target"]) for item in displays["main"]["access"]) == granted, displays["main"]
        assert [(item["kind"], item["target"]) for item in displays["projector"]["access"]] == [("preview", "5173")], displays
        # The mirror opens the Preview and SSH Access: a fresh link, and a command rather than a link.
        opened = await ask(mirror, {"type": "open", "id": 1, "board_id": access_board["id"], "key": "preview:5173"})
        app_link = PROVIDER["signed"][-1]
        assert app_link["port"] == 5173 and 5300 <= app_link["seconds"] <= 5400 and app_link["token"] != projector_link["token"], app_link
        assert opened["url"] == f"https://5173-{app_link['token']}.preview.test", opened
        opened = await ask(mirror, {"type": "open", "id": 2, "board_id": access_board["id"], "key": "ssh:"})
        ssh_access = PROVIDER["ssh"][-1]
        assert opened["ssh"]["command"] == f"ssh {ssh_access['token']}@ssh.preview.test" and "url" not in opened, opened
        assert ssh_access["minutes"] in (89, 90) and opened["ssh"]["expires_at"] <= access_board["access"]["ssh:"]["expires_at"], (ssh_access, opened)
        # A desktop reports why it could not start (the mock has no computer use).
        refused = await ask(browser, {"type": "open", "id": 3, "board_id": access_board["id"], "key": "desktop:"})
        assert refused["type"] == "error" and "desktop" in refused["error"], refused
        for key, why in [("preview:8765", "not granted"), ("download:REPORT.md", "from its button")]:
            refused = await ask(browser, {"type": "open", "id": 4, "board_id": access_board["id"], "key": key})
            assert refused["type"] == "error" and why in refused["error"], (key, refused)
        # A Download is read when opened, as an attachment, under its grant.
        status, headers, body = await asyncio.to_thread(file, "REPORT.md", access_board["id"], download="1")
        assert status == 200 and body == REPORT_TEXT.encode() and headers.get("content-disposition", "").startswith("attachment"), (status, headers)
        assert headers.get("content-type") == "application/octet-stream", headers
        assert (await asyncio.to_thread(file, "REPORT.md", icons[-1]["id"], download="1"))[0] == 404
        # A named Terminal is its own tmux session, lasting as long as its grant.
        opened = await ask(browser, {"type": "open", "id": 5, "board_id": access_board["id"], "key": "terminal:build"})
        ready = await terminal_ready({"ticket": opened["url"].split("#t=", 1)[1]})
        build_token = ready["token"]
        assert ready["expires_at"] == access_board["access"]["terminal:build"]["expires_at"], ready
        assert PROVIDER["pty"][-1] == "human-build", PROVIDER["pty"]
        # No credential reaches the Orchestrator.
        context = json.dumps([payload["input"] for _, payload in OrchestratorMock.requests])
        for secret in (app_link["token"], projector_link["token"], ssh_access["token"], build_token.split(".", 1)[1], "preview.test"):
            assert secret not in context, secret
        # Revoking ends every live grant on the Display at once; the boards stay, marked revoked.
        await mocks.send_live(dtmf("B"))
        revoked_board = (await recv_type(browser, "board", 15))["board"]
        assert revoked_board["id"] == access_board["id"] and all(grant.get("revoked") for grant in revoked_board["access"].values()), revoked_board
        await recv_type(mirror, "board", 15)
        await wait_for(outputs_back("kB-revoke", "kB-state"), 15)
        assert OrchestratorMock.tool_output("kB-revoke") == {"ok": True, "display": "main", "grants_revoked": 5}
        displays = {item["name"]: item for item in OrchestratorMock.tool_output("kB-state")["displays"]}
        assert displays["main"]["access"] == [] and len(displays["projector"]["access"]) == 1, displays
        assert (5173, app_link["token"]) in PROVIDER["expired"], PROVIDER["expired"]
        assert (5173, projector_link["token"]) not in PROVIDER["expired"], "another Display's grant stands"
        assert f"/api/sandbox/mock-sandbox-1/ssh-access?token={ssh_access['token']}" in DaytonaMock.deleted, DaytonaMock.deleted
        await terminal_refused({"token": build_token})
        refused = await ask(mirror, {"type": "open", "id": 6, "board_id": access_board["id"], "key": "preview:5173"})
        assert refused["type"] == "error" and "revoked" in refused["error"], refused
        status, _, body = await asyncio.to_thread(file, "REPORT.md", access_board["id"], download="1")
        assert status == 400 and b"revoked" in body, (status, body)
        # Unpairing a Display ends it: its browsers, boards, and grants.
        await mocks.send_live(transcript("input", "Unpair the projector.", 106000, 107000))
        await mocks.send_live(delegation("del-wb-end2", 107050))
        await recv_type(slides, "unpaired", 15)
        await wait_for(outputs_back("we-unpair", "we-state"), 15)
        assert OrchestratorMock.tool_output("we-unpair") == {"ok": True, "display": "projector", "browsers_unpaired": 1}
        assert [item["name"] for item in OrchestratorMock.tool_output("we-state")["displays"]] == ["main"]
        assert (5173, projector_link["token"]) in PROVIDER["expired"], "an ended Display's grants are revoked"
        await wait_for(lambda: heard('browser_disconnected: {"display":"projector"}'), 10)
        await slides.close()
        assert (await asyncio.to_thread(file, "chart.png", slide["board_id"], slides_token, **{"as": "image"}))[0] == 401
        # Nothing on a board wakes the Sandbox: while it sleeps, files and Documents say so.
        await asyncio.sleep(15.5)  # past the Worker's brief memory of a running Sandbox
        DaytonaMock.force_state = "stopped"
        try:
            status, _, body = await asyncio.to_thread(file, "/home/daytona/chart.png", icons[-1]["id"], **{"as": "image"})
            assert status == 503 and json.loads(body).get("asleep") is True, (status, body)
            asleep = await ask(browser, {"type": "read", "id": 7, "path": "REPORT.md", "board_id": access_board["id"]})
            assert asleep["type"] == "error" and asleep.get("asleep") is True and "asleep" in asleep["error"], asleep
        finally:
            DaytonaMock.force_state = None
        await mirror.close()
        # A wrong secret for the right identity is refused and closed with 4001.
        async with connect(f"ws://127.0.0.1:{WORKER_PORT}/pair/ws?for={identity}") as stranger:
            await stranger.send(json.dumps({"type": "auth", "token": identity + "." + "0" * 48}))
            await recv_type(stranger, "error")
            try:
                await stranger.recv()
            except ConnectionClosed as closed:
                assert closed.rcvd and closed.rcvd.code == 4001
        # Unpairing from the page ends that browser's credential; the mirror keeps the Display.
        await browser.send(json.dumps({"type": "unpair"}))
        await recv_type(browser, "unpaired")
        await browser.close()
        async with connect(f"ws://127.0.0.1:{WORKER_PORT}/pair/ws?for={identity}") as browser:
            await browser.send(json.dumps({"type": "auth", "token": token}))
            await recv_type(browser, "error")
        assert (await asyncio.to_thread(file, "/home/daytona/chart.png", icons[-1]["id"], mirror_token, **{"as": "image"}))[0] in (200, 503), "still paired"

        # A theme switch bounces the call: the new theme's greeting is written before the
        # GPT-Live leg hangs up, then the same CallSid returns with the new voice and conceit.
        # A waiting pairing claim must expire at disconnect, not remain available
        # for a later status-key press to approve without a fresh announcement.
        await mocks.send_live(transcript("input", "Pair a browser before switching themes.", 130000, 131000))
        await mocks.send_live(delegation("del-pair-disconnect", 131050))
        disconnect_code = await next_pairing_code("dc-pair")
        await next_matching(mocks.live_messages, appended("commentary", f"Pairing code {disconnect_code}"))
        status, pending = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": disconnect_code})
        assert status == 200, (status, pending)
        pending_ticket = {"authorization": f"Bearer {pending['ticket']}"}
        assert (await asyncio.to_thread(pair_http, "GET", "/pair/claim/status", None, pending_ticket))[1] == {"state": "waiting"}
        await next_matching(mocks.live_messages, lambda m: m.get("type") == "session.commentary.append" and "submitted the pairing code" in m.get("content", ""))
        greetings = len(OrchestratorMock.greeting_requests)
        await mocks.send_live(dtmf("3"))
        await next_matching(mocks.live_messages, appended("commentary", SWITCH_SAY))
        await wait_for(lambda: len(OrchestratorMock.greeting_requests) > greetings, 10)
        switch_instructions = OrchestratorMock.greeting_requests[-1]["instructions"]
        assert "The survey (Survey theme)" in switch_instructions and "switched to this theme mid-call" in switch_instructions
        assert "at the high rung" in switch_instructions, switch_instructions
        assert OrchestratorMock.greeting_requests[-1]["input"][0]["content"] == f"[memory] {MEMORY_TEXT}"
        await wait_for(lambda: any(path == "/live/sessions/live-test-2/hangup" for path, _ in DaytonaMock.openai_requests), 10)
        assert OrchestratorMock.tool_output("k3-theme")["bouncing"] is True
        await mocks.live.close()
        mocks.live_connected.clear()
        for _ in range(100):
            pending_state = (await asyncio.to_thread(pair_http, "GET", "/pair/claim/status", None, pending_ticket))[1]
            if pending_state == {"state": "expired"}:
                break
            await asyncio.sleep(0.05)
        assert pending_state == {"state": "expired"}, "disconnect expires approval before its 30-second timeout"
        # The event stream drops between the legs, and /prepare reconnects before GPT-Live
        # accepts: that Agent Connection is recorded into the call at attach, marked as made
        # before it, with its setup requests' timing rows.
        await agent.close_events(1012, "service restarting")
        agent.hello_delay, DaytonaMock.live_accept_delay = 0, 2.0
        body, webhook_identity, signature = signed_webhook("CA_VOICE_TEST2", "live-test-3")
        assert await asyncio.to_thread(post_webhook, body, webhook_identity, signature) == 204
        await asyncio.wait_for(mocks.live_connected.wait(), 8)
        agent.hello_delay, DaytonaMock.live_accept_delay = 1.5, 0.0
        async def leg_rows() -> list[tuple[str, dict]]:
            rows = await asyncio.to_thread(call_activity_rows, "CA_VOICE_TEST2", True)
            return [(row["event"], json.loads(row["detail"])) for row in rows
                    if json.loads(row["detail"]).get("live_session_id") == "live-test-3"]
        await asyncio.sleep(1.2)  # one off-path telemetry flush
        leg = await leg_rows()
        [attached] = [detail for event, detail in leg if event == "voice.live.attached"]
        [connected] = [detail for event, detail in leg if event == "agent.connected"]
        assert attached["agent_ready"] is True and connected["before_call"] is True, (attached, connected)
        assert connected["agent_version"] == "2.2.7" and connected["session_count"] >= 1, connected
        assert connected["started_ms"] <= connected["connected_ms"] < attached["ts_ms"], (connected, attached)
        early = [detail for event, detail in leg if event == "voice.timing" and detail.get("before_call")]
        assert any(row["event"] == "agent.sent" and row["route"] == "/api/info" for row in early), early
        assert all(connected["started_ms"] <= row["ts_ms"] < attached["ts_ms"] for row in early), early
        rejoined = next(payload for path, payload in DaytonaMock.openai_requests if path == "/live/sessions/live-test-3/accept")
        assert rejoined["session"]["audio"]["output"]["voice"] == "sage", rejoined["session"]["audio"]
        assert "The survey (Survey theme)" in rejoined["session"]["instructions"]
        await next_matching(mocks.live_messages, appended("commentary", f"Dogwalk. {NEXT_GREETING}"))
        # The register carries across the bounce as unspoken context for the new voice.
        await next_matching(mocks.live_messages, appended("thinking", "The caller had the register at high; start there."))
        await mocks.send_live(dtmf("1"))
        assert (await asyncio.to_thread(pair_http, "GET", "/pair/claim/status", None, pending_ticket))[1] == {"state": "expired"}, "a rejoined call cannot approve the old claim"
        await mocks.send_live(dtmf("2"))
        await wait_for(lambda: '"call_id": "k2-dramaturgy"' in json.dumps([p["input"] for _, p in OrchestratorMock.requests]), 10)
        dramaturgy = OrchestratorMock.tool_output("k2-dramaturgy")["guidance"]
        assert "Planetary Romance" in dramaturgy and "Survey theme" in dramaturgy, dramaturgy

        # start_voice_donation leaves a donation handoff and ends the GPT-Live leg, so the Dial
        # action sends the call to the Voice Donation flow.
        hangups = lambda: sum(path == "/live/sessions/live-test-3/hangup" for path, _ in DaytonaMock.openai_requests)
        await mocks.send_live(dtmf("C"))
        await wait_for(lambda: hangups() == 1, 10)
        assert OrchestratorMock.tool_output("kC-donate")["status"] == "handing the call to voice donation"
        assert await asyncio.to_thread(d1, "SELECT action FROM call_handoffs WHERE call_sid = 'CA_VOICE_TEST2'") == [{"action": "donation"}]
        status, body = await asyncio.to_thread(post, DEFAULT_LOCAL, "/voice/after-live", {
            "From": TEST_PHONE, "CallSid": "CA_VOICE_TEST2"}, LOCAL_TOKEN)
        assert status == 200 and '<Redirect method="POST">/voice/donation</Redirect>' in body, body

        # start_challenge_practice leaves a practice handoff the same way; practice itself
        # returns the call to /voice, which reconnects the Voice Agent.
        await mocks.send_live(dtmf("D"))
        await wait_for(lambda: hangups() == 2, 10)
        assert OrchestratorMock.tool_output("kD-practice")["status"] == "handing the call to challenge practice"
        assert await asyncio.to_thread(d1, "SELECT action FROM call_handoffs WHERE call_sid = 'CA_VOICE_TEST2'") == [{"action": "practice"}]
        status, body = await asyncio.to_thread(post, DEFAULT_LOCAL, "/voice/after-live", {
            "From": TEST_PHONE, "CallSid": "CA_VOICE_TEST2"}, LOCAL_TOKEN)
        assert status == 200 and '<Redirect method="POST">/voice/practice</Redirect>' in body, body

        # GPT-Live refusing the accept (an account out of credit) is recorded on the call as
        # voice.live.accept.failed, and the fallback menu says why instead of "Voice dropped".
        await asyncio.to_thread(d1, "INSERT INTO audit_log (ts, event, phone_number, call_sid, detail) VALUES "
                                f"(unixepoch(), 'voice.live.accept.failed', '{TEST_PHONE}', 'CA_REFUSED', "
                                "'{\"status\":429,\"code\":\"insufficient_quota\"}')")
        status, body = await asyncio.to_thread(post, DEFAULT_LOCAL, "/voice/after-live", {
            "From": TEST_PHONE, "CallSid": "CA_REFUSED"}, LOCAL_TOKEN)
        assert status == 200 and "out of credit" in body and "Voice dropped" not in body and "Press 1 to reconnect" in body, body

        # Practice and Re-enrollment return the call to /voice, and the reconnected Voice Agent's
        # call_started says how each ended: outcome names only, read once.
        async def return_from(flow: str, live_id: str) -> dict:
            status, body = await asyncio.to_thread(post, DEFAULT_LOCAL, f"/voice/{flow}", {
                "From": TEST_PHONE, "CallSid": "CA_VOICE_TEST2", "AccountSid": "AC" + "0" * 32}, LOCAL_TOKEN)
            assert status == 200 and body.endswith('<Redirect method="POST">/voice</Redirect></Response>'), body
            await mocks.live.close()
            mocks.live_connected.clear()
            body, webhook_identity, signature = signed_webhook("CA_VOICE_TEST2", live_id)
            assert await asyncio.to_thread(post_webhook, body, webhook_identity, signature) == 204
            await asyncio.wait_for(mocks.live_connected.wait(), 8)
            marker = f'"returned_from":{{"flow":"{flow}"'
            # A returning call_started snoozes (#58): the flow already told the caller, so it
            # waits in the queue for whatever next wakes the model.
            await wait_for(lambda: heard_lines(marker), 10)
            started = heard_lines(marker)[0]
            returned = json.loads(started.split("call_started: ", 1)[1])["returned_from"]
            assert not {"score", "cosine", "level", "phrase", "transcript", "embedding"} & returned.keys(), returned
            return returned
        assert await return_from("practice", "live-test-4") == {"flow": "practice", "outcome": "unavailable", "reason": "no_voice_reference"}
        assert await return_from("reenrollment", "live-test-5") == {"flow": "reenrollment", "outcome": "unavailable", "reason": "no_voice_reference"}
        assert await asyncio.to_thread(d1, "SELECT returned_from FROM voice_calls WHERE call_sid = 'CA_VOICE_TEST2'") == [{"returned_from": None}]
        # The inviter hears how the bound invitee's Registration went (run_invitations) once: in
        # the first call_started after the invitee's call ended, by Handle, never a number or the words.
        told = set(heard_lines('"invitees":'))
        assert len(told) == 1, told
        invitees = json.loads(next(iter(told)).split("call_started: ", 1)[1])["invitees"]
        assert invitees == [{"handle": bound_invitee, "invited_days_ago": 0, "how_it_went":
            "registered, but voice enrollment could not run; "
            "their next call from their home phone offers voice enrollment again"}], invitees
        assert await asyncio.to_thread(d1, "SELECT COUNT(*) AS n FROM invitations WHERE onboarding_noticed IS NOT NULL") == [{"n": 1}]
        # Seeded as Re-enrollment writes a replacement (smoke_test.py drives the real ritual): a
        # third reconnect reports it, as the caller in CAa3722805 needed.
        await asyncio.to_thread(d1, """UPDATE voice_calls SET returned_from = '{"flow":"reenrollment","outcome":"reenrolled"}' WHERE call_sid = 'CA_VOICE_TEST2'""")
        await mocks.live.close()
        mocks.live_connected.clear()
        body, webhook_identity, signature = signed_webhook("CA_VOICE_TEST2", "live-test-6")
        assert await asyncio.to_thread(post_webhook, body, webhook_identity, signature) == 204
        await asyncio.wait_for(mocks.live_connected.wait(), 8)
        await wait_for(lambda: heard_lines('"returned_from":{"flow":"reenrollment","outcome":"reenrolled"}'), 10)

        # Star then two hands the call to the recovery menu, which ends the GPT-Live leg.
        await mocks.send_live(dtmf("*"))
        await next_matching(mocks.live_messages, of_type("session.input_audio.mute"))
        await mocks.send_live(dtmf("2"))
        await wait_for(lambda: any(path == "/live/sessions/live-test-6/hangup" for path, _ in DaytonaMock.openai_requests), 10)
        events = await asyncio.to_thread(call_activity, "CA_VOICE_TEST2")
        # Drain the operator API by insertion cursor, independently of event-time
        # sorting and without pairing to either caller's browser.
        pages, observed, cursor = 0, [], 0
        while True:
            page = await asyncio.to_thread(operator_timing_page, "CA_VOICE_TEST", cursor)
            pages += 1
            assert len(page["events"]) <= 500
            observed.extend(page["events"])
            assert page["cursor"] >= cursor
            cursor = page["cursor"]
            if not page["has_more"]:
                break
            assert pages < 30, "operator timing pagination did not terminate"
        assert observed and len({row["id"] for row in observed}) == len(observed)
        assert [row["id"] for row in observed] == sorted(row["id"] for row in observed)
        turn = next(row for row in observed if row["event"] == "agent.turn.stopped")
        assert turn["detail"]["agent_session_id"] and turn["detail"]["ms"] >= 0
        for expected in ("menu.opened", "menu.closed", "menu.restart.requested", "menu.recovery", "voice.keypad.stop", "voice.keypad.status",
                         "voice.earcons", "voice.theme", "pairing.started", "pairing.unclaimed", "pairing.claimed", "pairing.accepted", "pairing.page_stale",
                         "whiteboard.drawn", "access.opened", "access.revoked", "display.renamed", "display.ended"):
            assert expected in events, (expected, events)
        handoff_rows = OrchestratorMock.user_message('"key":"7"')
        assert '"key":"1"' not in handoff_rows and '"key":"*"' not in handoff_rows

        # Themes and phone numbers stay above the Agent: no Agent prompt uses the theme's words or
        # any number Access Control knows.
        prompts = [m["body"]["text"] for m in mocks.agent.seen if m["path"].endswith("/prompt")]
        assert len(prompts) > 10, prompts
        for text in map(str.lower, prompts):
            assert not any(word in text for word in THEME_LEXICON), text
            digits = re.sub(r"\D", "", text)
            assert not any(number[-10:] in digits for number in TEST_NUMBERS), text

        # Nothing with a URL reached the caller-facing lane.
        spoken = [m for m in mocks.seen if m.get("type") == "session.commentary.append"]
        assert spoken and not any("http" in m["content"] for m in spoken)

        requests = OrchestratorMock.requests
        assert {step for step, _ in OrchestratorMock.issued} == set(STEP_IDS)
        for headers, payload in requests:
            assert headers.get("authorization") == "Bearer local-openai-token"
            assert payload["model"] == "gpt-6.1-sol"
            assert payload["store"] is False and payload["parallel_tool_calls"] is True
            assert "Session Orchestrator" in payload["instructions"]
            assert "The session model:" in payload["instructions"]
            assert "first-call-tour" in payload["instructions"]
            assert {tool["name"] for tool in payload["tools"]} >= {
                "brief", "say", "create_managed_session", "begin_prompt_turn", "end_call",
                "discover_persisted_sessions", "load_persisted_session", "set_alias",
                "close_managed_session", "list_workspace_projects", "create_invitation", "list_invitations",
                "set_invitation_recipient", "revoke_invitation", "inspect_registration", "start_voice_donation",
                "start_challenge_practice", "start_reenrollment",
            }
            pairing_tool = next(tool for tool in payload["tools"] if tool["name"] == "pair_browser")
            assert set(pairing_tool["parameters"].get("properties", {})) == {"display"}, pairing_tool
            assert not pairing_tool["parameters"].get("required"), pairing_tool
            names = {tool["name"] for tool in payload["tools"]}
            assert {"draw_on_whiteboard", "rename_display", "unpair_display", "revoke_access"} <= names and "show_in_browser" not in names, names
            assert "start_voice_donation together in the same response" in payload["instructions"]
            assert "start_challenge_practice together in the same response" in payload["instructions"]
            assert "start_reenrollment together in the same response" in payload["instructions"]
            assert "invite-someone" in payload["instructions"]
            assert "Never create a session in home and tell it to work in another directory" in payload["instructions"]
            assert "from what I know, unverified" in payload["instructions"]
            assert "unless its Prompt Turn is already running or you begin it in the same response" in payload["instructions"]
            assert "browser_page_stale" in payload["instructions"]
            assert not any(name in payload["instructions"] for name in ("Cedar", "Biscuit", "Kappa", "Dozer"))
            assert "rename an existing session only when the caller asks" in payload["instructions"]
            assert "where something is written down, update the Memory" in payload["instructions"]
        # The Orchestrator presents the caller's theme: Park until the switch, Survey after it.
        assert "The walk (Park theme)" in requests[0][1]["instructions"]
        assert "The survey (Survey theme)" in requests[-1][1]["instructions"]
        fed_back = {
            item["call_id"]
            for _, payload in requests
            for item in payload["input"]
            if item.get("type") == "function_call_output"
        }
        assert fed_back == set(STEP_IDS)


def ritual(phone: str, call_sid: str, words: str, confirm: bool = True) -> str:
    """An unregistered caller says invite words and, if they matched, confirms the readback."""
    status, body = post(DEFAULT_LOCAL, "/voice/claim?attempts=1", {
        "From": phone, "CallSid": call_sid, "SpeechResult": words, "Confidence": "0.9",
    }, LOCAL_TOKEN)
    assert status == 200, (status, body)
    if "don't match" in body or not confirm:
        return body
    return confirm_claim(phone, call_sid)


def confirm_claim(phone: str, call_sid: str) -> str:
    status, body = post(DEFAULT_LOCAL, "/voice/confirm?attempts=1", {
        "From": phone, "CallSid": call_sid, "SpeechResult": "yes", "Confidence": "0.9",
    }, LOCAL_TOKEN)
    assert status == 200, (status, body)
    return body


def d1(sql: str) -> list[dict]:
    result = subprocess.run(
        [project_wrangler(), "d1", "execute", "dogwalk", "--local", "--json", "--command", sql],
        cwd=WORKER_DIR, capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError(f"wrangler d1 execute exited {result.returncode}: {result.stderr.strip()[-2000:]}")
    return json.loads(result.stdout)[0]["results"]


async def pairing_claim_checks(identity: str) -> None:
    """D1-backed abuse/expiry cases after all call tests, with no production test hooks.

    Failed claims never reach the VoiceSession, so seed rows directly to exercise the
    public claim route without keeping another call open or spending another 90 seconds.
    """
    async def seed(nameplate: int, expired: bool = False) -> str:
        code = f"{nameplate}-adroitness-aardvark"
        digest = hashlib.sha256(code.encode()).hexdigest()
        await asyncio.to_thread(d1, f"""
            DELETE FROM pairing_codes WHERE nameplate = '{nameplate}';
            INSERT INTO pairing_codes (code_hash, nameplate, identity_hash, expires_at, attempts)
            VALUES ('{digest}', '{nameplate}', '{identity}', unixepoch() {'-' if expired else '+'} 90, 0)
        """)
        return code

    await asyncio.to_thread(d1, "DELETE FROM pairing_claim_budget")
    try:
        expired = await seed(997, expired=True)
        status, body = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": expired})
        assert status == 404, (status, body)

        exhausted = await seed(998)
        for _ in range(5):
            status, body = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": "998-unknownword-anotherunknownword"})
            assert status == 404, (status, body)
        assert await asyncio.to_thread(d1, "SELECT attempts FROM pairing_codes WHERE nameplate = '998'") == []
        status, body = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": exhausted})
        assert status == 404, (status, body)

        await seed(999)
        guesses = await asyncio.gather(*(
            asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": "999-unknownword-anotherunknownword"})
            for _ in range(8)
        ))
        assert all(status == 404 for status, _ in guesses), guesses
        assert await asyncio.to_thread(d1, "SELECT attempts FROM pairing_codes WHERE nameplate = '999'") == []

        # The 120th global request is allowed, the 121st is not. Seed the counter rather
        # than issue 119 irrelevant requests. Avoid the minute edge while checking it.
        if time.time() % 60 > 55:
            await asyncio.sleep(61 - time.time() % 60)
        await asyncio.to_thread(d1, "DELETE FROM pairing_claim_budget; INSERT INTO pairing_claim_budget (minute, attempts) VALUES (unixepoch() / 60, 119)")
        status, body = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": "996-unknownword-anotherunknownword"})
        assert status == 404, (status, body)
        limited = await asyncio.gather(*(
            asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": "996-unknownword-anotherunknownword"}, {
                "cf-connecting-ip": f"192.0.2.{index}", "x-forwarded-for": f"198.51.100.{index}",
            }) for index in range(1, 9)
        ))
        assert all(status == 429 for status, _ in limited), limited
    finally:
        # Do not poison other suites sharing this local fixture.
        await asyncio.to_thread(d1, "DELETE FROM pairing_claim_budget; DELETE FROM pairing_codes WHERE nameplate IN ('997', '998', '999')")


def outputs_back(*call_ids: str):
    return lambda: set(call_ids) <= {
        item.get("call_id") for _, payload in OrchestratorMock.requests for item in payload["input"]
        if item.get("type") == "function_call_output"
    }


async def run_invitations(mocks: SocketMocks) -> str:
    """Invitations end to end: tools on the inviter's call, redemption through the ritual."""
    await mocks.send_live(transcript("input", "Invite my friend; he's listening.", 30000, 31500))
    await mocks.send_live(delegation("del-i1", 31600))
    await wait_for(outputs_back("i-say", "i-leak"), 15)
    out = OrchestratorMock.tool_output
    first = out("i1-create")["invitation"]
    assert first["status"] == "active" and first["recipient"] is None and first["expires_in_minutes"] == 24 * 60, first
    assert len(first["words"].split()) == 3 and first["words"].replace(" ", "").isalpha(), first
    # The caller learns who invited them; another User's invitation is not theirs to touch.
    assert out("i1-who") == {
        "ok": True, "handle": CALLER_HANDLE, "registered_days_ago": 0, "via": "invitation", "invited_by": SEED_HANDLE,
    }, out("i1-who")
    assert out("i1-foreign")["ok"] is False and "no such invitation" in out("i1-foreign")["error"]
    # Without a confirmed readback nothing is created; the call is answered with what to read back.
    readback = out("i2-readback")
    assert readback["ok"] is False and "invitation" not in readback, readback
    assert readback["readback"] == {
        "phone": BOUND_PHONE, "spoken": "plus one, five five five, five five five, zero one zero two", "assumed_country_code": True,
    }, readback
    bound = out("i2-create")["invitation"]
    assert bound["recipient"]["phone"] == BOUND_PHONE, bound
    assert out("i3-create")["ok"] is True
    limited = out("i4-limit")
    assert limited["ok"] is False and "3 active invitations" in limited["error"], limited
    abroad = out("i3-abroad")["readback"]
    assert abroad["phone"] == "+442079460958" and abroad["spoken"] == "plus four four two, zero seven nine, four six zero, nine five eight", abroad
    assert out("i3-bare")["ok"] is False and "country code" in out("i3-bare")["error"], out("i3-bare")
    # Invite words and a number in digit groups may be spoken; a number never reaches an Agent.
    assert out("i-say")["ok"] is True, out("i-say")
    assert out("i-leak")["ok"] is False and "phone number" in out("i-leak")["error"], out("i-leak")
    third = out("i3-create")["invitation"]["words"]

    # A claim begins on an unbound invitation, then the inviter binds it elsewhere: the
    # confirmation fails and consumes nothing.
    assert "I heard" in await asyncio.to_thread(ritual, RACE_PHONE, "CA_INV_RACE", third, False)
    await mocks.send_live(transcript("input", "Tie that last one to my other friend's number.", 32000, 33500))
    await mocks.send_live(delegation("del-i2", 33600))
    await wait_for(outputs_back("i3-bind"), 15)
    assert out("i3-bind")["invitation"]["recipient"]["phone"] == REBOUND_PHONE
    assert "no longer available" in await asyncio.to_thread(confirm_claim, RACE_PHONE, "CA_INV_RACE")

    # Unbound: any unregistered phone redeems it, once.
    invitee_handle = spoken_handle(await asyncio.to_thread(ritual, INVITEE_PHONE, "CA_INV_1", first["words"]))
    assert "don't match" in await asyncio.to_thread(ritual, LATE_PHONE, "CA_INV_2", first["words"])
    # Bound: the wrong number cannot even claim it; the right one registers.
    assert "don't match" in await asyncio.to_thread(ritual, REBOUND_PHONE, "CA_INV_3", bound["words"])
    bound_handle = spoken_handle(await asyncio.to_thread(ritual, BOUND_PHONE, "CA_INV_4", bound["words"]))
    assert "don't match" in await asyncio.to_thread(ritual, LATE_PHONE, "CA_INV_5", EXPIRED_WORDS)
    # Two claims on one single-use invitation, confirmed at once: exactly one registers.
    assert "I heard" in await asyncio.to_thread(ritual, LATE_PHONE, "CA_INV_6", FOREIGN_WORDS, False)
    assert "I heard" in await asyncio.to_thread(ritual, RIVAL_PHONE, "CA_INV_7", FOREIGN_WORDS, False)
    raced = await asyncio.gather(
        asyncio.to_thread(confirm_claim, LATE_PHONE, "CA_INV_6"),
        asyncio.to_thread(confirm_claim, RIVAL_PHONE, "CA_INV_7"),
    )
    assert sorted("Registered" in body for body in raced) == [False, True], raced
    winner = LATE_PHONE if "Registered" in raced[0] else RIVAL_PHONE
    winner_handle = spoken_handle(raced[0] if winner == LATE_PHONE else raced[1])

    await mocks.send_live(transcript("input", "Did my friends make it in?", 34000, 35500))
    await mocks.send_live(delegation("del-i3", 35600))
    await wait_for(outputs_back("i-relist"), 15)
    listed = {item["words"]: item for item in out("i-list")["invitations"]}
    assert FOREIGN_WORDS not in listed and set(listed) == {first["words"], bound["words"], third, EXPIRED_WORDS}, listed
    # redeemed_by names the invitee by Handle; redeemed_from is the number that redeemed it.
    assert listed[first["words"]]["status"] == "redeemed" and listed[first["words"]]["redeemed_by"] == invitee_handle
    assert listed[first["words"]]["redeemed_from"]["phone"] == INVITEE_PHONE, listed[first["words"]]
    assert listed[first["words"]]["matched_recipient"] is None and listed[first["words"]]["redeemed_minutes_ago"] == 0
    assert listed[bound["words"]]["redeemed_by"] == bound_handle and listed[bound["words"]]["matched_recipient"] is True
    assert listed[bound["words"]]["redeemed_from"]["phone"] == BOUND_PHONE
    assert listed[third]["status"] == "active" and listed[third]["recipient"]["phone"] == REBOUND_PHONE
    assert listed[EXPIRED_WORDS]["status"] == "expired"
    assert out("i-list")["active"] == 1, out("i-list")
    assert out("i3-revoke")["revoked"] is True and out("i1-hide") | {"note": None} == {
        "ok": True, "words": first["words"], "was": "redeemed", "revoked": False, "note": None,
    }, out("i1-hide")
    assert {item["words"] for item in out("i-relist")["invitations"]} == {bound["words"], EXPIRED_WORDS}
    assert "don't match" in await asyncio.to_thread(ritual, REBOUND_PHONE, "CA_INV_8", third)

    # Hiding and revoking keep the graph: every edge, across generations.
    edges = await asyncio.to_thread(d1, """SELECT inviter.handle AS inviter, invitee.handle AS invitee,
               invitee.home_phone AS home, v.redeemed_from
        FROM invitations v JOIN users inviter ON inviter.id = v.inviter_user_id
        JOIN users invitee ON invitee.id = v.redeemed_by_user_id""")
    edges = [e for e in edges if e["redeemed_from"].startswith("+1555555")]
    assert all(e["home"] == e["redeemed_from"] for e in edges), edges
    assert sorted((e["inviter"], e["invitee"]) for e in edges) == sorted([
        (CALLER_HANDLE, bound_handle), (CALLER_HANDLE, invitee_handle), (SEED_HANDLE, CALLER_HANDLE), (SEED_HANDLE, winner_handle),
    ]), edges
    assert next(e for e in edges if e["invitee"] == winner_handle)["redeemed_from"] == winner
    unredeemed = await asyncio.to_thread(d1, f"SELECT redeemed_by_user_id, redeemed_from, revoked_at, hidden_at FROM invitations WHERE code_words = '{third}'")
    assert unredeemed[0]["redeemed_by_user_id"] is None and unredeemed[0]["redeemed_from"] is None, unredeemed
    assert unredeemed[0]["revoked_at"] and unredeemed[0]["hidden_at"], unredeemed

    # Operator-only graph projection includes hidden history across generations. The
    # intended recipient of an unused Invitation is not presented as a registered invitee.
    state = await asyncio.to_thread(operator_access_state, True)
    invitations = {v["code_words"]: v for v in state["invitations"]}
    assert invitations[first["words"]]["hidden_at"] and invitations[first["words"]]["status"] == "redeemed"
    assert invitations[third]["status"] == "revoked" and invitations[third]["hidden_at"]
    assert invitations[third]["recipient_phone"] == REBOUND_PHONE and invitations[third]["invitee_phone"] is None
    assert invitations[EXPIRED_WORDS]["status"] == "expired"
    registrations = {r["diagnostic_id"]: r for r in state["registrations"]}
    projected_edges = []
    for v in invitations.values():
        assert v["inviter_id"].startswith("user-") and registrations[v["inviter_id"]]["handle"] == v["inviter_handle"]
        if v["invitee_id"]:
            assert registrations[v["invitee_id"]]["handle"] == v["invitee_handle"]
            assert registrations[v["invitee_id"]]["phone_number"] == v["invitee_phone"]
            projected_edges.append((v["inviter_handle"], v["invitee_handle"]))
    assert sorted(e for e in projected_edges if None not in e) == sorted((e["inviter"], e["invitee"]) for e in edges)
    safe = await asyncio.to_thread(operator_access_state)
    assert not safe["verbose"]
    assert {v["diagnostic_id"] for v in safe["invitations"]} == {v["diagnostic_id"] for v in invitations.values()}
    for v in safe["invitations"]:
        assert "code_words" not in v
        assert "inviter_phone" not in v
        assert all(v[field] is None or "*" in v[field] for field in ("recipient_phone", "invitee_phone"))
    safe_json = json.dumps(safe)
    assert all(words not in safe_json for words in invitations)
    assert all(r["phone_number"] not in safe_json for r in registrations.values() if r["phone_number"])
    # Handles are not masked: the safe lens names Users by them.
    assert {CALLER_HANDLE, SEED_HANDLE, invitee_handle} <= {r["handle"] for r in safe["registrations"]}

    # Renaming: three spins of three Dogwalk-chosen Handles; the caller picks one from any spin.
    await mocks.send_live(transcript("input", "Can I get a new name?", 36000, 37000))
    await mocks.send_live(delegation("del-r1", 37100))
    await wait_for(outputs_back("r1-who"), 15)
    offer = out("r1-offer")
    assert offer["current"] == CALLER_HANDLE and offer["spins_left"] == 2 and offer["earlier_options"] == [], offer
    options = offer["options"] + out("r1-offer2")["options"] + out("r1-offer3")["options"]
    assert len(set(options)) == 9 and all(len(h.split()) == 2 and CALLER_HANDLE != h for h in options), options
    assert out("r1-offer3")["earlier_options"] == offer["options"] + out("r1-offer2")["options"]
    assert out("r1-bogus")["ok"] is False and "not offered" in out("r1-bogus")["error"], out("r1-bogus")
    assert out("r1-offer4")["ok"] is False and "no spins left" in out("r1-offer4")["error"], out("r1-offer4")
    renamed = offer["options"][1]
    assert out("r1-set") | {"note": None} == {"ok": True, "handle": renamed, "previous": CALLER_HANDLE, "note": None}, out("r1-set")
    assert out("r1-who")["handle"] == renamed, out("r1-who")
    # Identity is unchanged: same User, its old Handle quarantined, its invitations still its own.
    rows = await asyncio.to_thread(d1, f"""SELECT u.id, u.handle, r.user_id AS retired_by FROM users u
        JOIN retired_handles r ON r.handle = '{CALLER_HANDLE}' WHERE u.handle = '{renamed}'""")
    assert len(rows) == 1 and rows[0]["retired_by"] == rows[0]["id"], rows
    renamed_state = await asyncio.to_thread(operator_access_state, True)
    assert all(v["inviter_handle"] != CALLER_HANDLE for v in renamed_state["invitations"])
    assert any(v["inviter_handle"] == renamed for v in renamed_state["invitations"])

    # The bound invitee's call ends (the ritual here sends no AccountSid, so its enrollment could
    # not run): news for the inviter's next call_started, settled only once that call has ended.
    status, _ = await asyncio.to_thread(post, DEFAULT_LOCAL, "/voice/status", {
        "From": BOUND_PHONE, "CallSid": "CA_INV_4", "CallStatus": "completed", "CallDuration": "40"}, LOCAL_TOKEN)
    assert status == 204, status
    return bound_handle


def harness_count(*fragments: str) -> int:
    """Harness event lines delivered to the Orchestrator containing every fragment, whether in its
    latest context or snoozed in its queue (#58): a turn's end delivered twice shows up as two lines."""
    read = [line for _, payload in OrchestratorMock.requests[-1:] for item in payload["input"] if item.get("role") == "user"
            for line in str(item["content"]).splitlines()]
    return sum(all(fragment in line for fragment in fragments) for line in read + queued_lines())


def heard_lines(fragment: str) -> list[str]:
    """Harness event lines containing fragment that reached the Orchestrator: read in any step,
    or snoozed in its queue (#58)."""
    read = [line for _, payload in OrchestratorMock.requests for item in payload["input"] if item.get("role") == "user"
            for line in str(item.get("content")).splitlines()]
    return [line for line in read + queued_lines() if fragment in line]


def queued_lines() -> list[str]:
    """Events snoozed in the Orchestrator's queue, waiting for the next woken step."""
    return [line for item in conversations() for text in item.get("orchestrator_queue") or [] for line in text.splitlines()]


async def activity_count(event: str) -> int:
    return sum(row["event"] == event for row in await asyncio.to_thread(call_activity_rows, "CA_VOICE_TEST2"))


async def wait_for_activity(event: str, count: int, timeout: float = 10) -> None:
    async with asyncio.timeout(timeout):
        while await activity_count(event) < count:
            await asyncio.sleep(0.2)


def requested(agent: AgentGatewayMock, method: str, path: str) -> list[int]:
    """Positions of the Agent requests with this method and path, in arrival order."""
    return [index for index, item in enumerate(agent.seen) if (item["method"], item["path"]) == (method, path)]


async def run_turn_control(mocks: SocketMocks, sid: str, beats) -> None:
    """Steering, cancelling, a dropped event stream, Co-presence, an Elicitation, and prompt
    delivery through the gateway, against OpenCode's native API. Juniper is idle and followed."""
    agent = mocks.agent
    stopped = 'turn_stopped: {"alias":"Juniper"'
    failed_before = harness_count('turn_failed: {"alias":"Juniper"')

    # A Steer waits for the step under way: the Agent reads it at the next step boundary
    # (inbox.delivered), which is a beat, and the turn ends once, completed.
    agent.mark()
    await mocks.send_live(transcript("input", "Have Juniper document the parser's entry point.", 24500, 25100))
    await mocks.send_live(delegation("del-s1", 25150))
    steer = await agent.next_request(lambda item: (item["body"] or {}).get("text") == STEER_GUIDANCE)
    assert (steer["path"], steer["body"]) == (f"/api/session/{sid}/prompt", {"text": STEER_GUIDANCE, "delivery": "steer"}), steer
    await wait_for(outputs_back("s1-steer"), 10)
    assert OrchestratorMock.tool_output("s1-steer")["when"] == "next_step"
    assert not requested(agent, "POST", f"/api/session/{sid}/interrupt"), "a Steer interrupted the step under way"
    [pending] = agent.inbox.pop(sid)
    await agent.emit("session.step.ended", sessionID=sid, tokens={"input": 900, "output": 40})
    await agent.emit("session.inbox.delivered", sessionID=sid, inboxID=pending["id"])
    await agent.emit("session.step.started", sessionID=sid)
    await wait_for(lambda: "Juniper has the correction." in beats(), 10)
    await agent.text(sid, STEERED_REPORT)
    checks = len(requested(agent, "GET", "/api/session/active"))
    await agent.finish(sid)
    await wait_for(lambda: harness_count(stopped, '"outcome":"completed"', STEERED_REPORT) == 1, 10)
    # A success with nothing undelivered is final: it ends the turn at once, without a check.
    assert len(requested(agent, "GET", "/api/session/active")) == checks, "a plain success was checked"
    rows = await asyncio.to_thread(call_activity_rows, "CA_VOICE_TEST2")
    delivered = [json.loads(row["detail"]) for row in rows if row["event"] == "agent.turn.steer_delivered"]
    assert [(item["inbox_id"], item["when"]) for item in delivered] == [(pending["id"], "next_step")], delivered

    # A Redirect interrupts with resume: the interrupted execution and the resumed one share a
    # millisecond, and the check the interrupt asks for finds the session running, which does not
    # end the turn; the later success does, once.
    ended = harness_count(stopped)
    superseded = await activity_count("agent.turn.end_superseded")
    agent.mark()
    await mocks.send_live(transcript("input", "Have Juniper profile the parser.", 25500, 26100))
    await mocks.send_live(delegation("del-s2", 26150))
    # The step under way waits on a Permission Request, which the Redirect's interrupt rejects:
    # Dogwalk caused that answer, so it is not reported as answered elsewhere. The interrupt
    # aborts the running command, which is not a failed step: no failure beat.
    await agent.next_request(lambda item: item["body"] == {"text": REDIRECT_PROMPT})
    redirected_at = len(beats())
    await agent.tool(sid, "call_profile1", "bash", {"command": "py-spy record"}, end=None)
    await agent.ask_permission(sid, "per_profile1", "bash", ["py-spy record"], [])
    redirect = await agent.next_request(lambda item: item["path"] == f"/api/session/{sid}/interrupt")
    assert redirect["query"] == {"resume": "true"}, redirect
    [guided] = [index for index, item in enumerate(agent.seen) if (item["body"] or {}).get("text") == REDIRECT_GUIDANCE]
    assert guided < agent.seen.index(redirect), "the Redirect interrupted before its steering was queued"
    await wait_for(lambda: beats().count("Juniper has the correction.") == 2, 10)
    await wait_for_activity("agent.turn.end_superseded", superseded + 1)
    assert sid in agent.active and harness_count(stopped) == ended, "the Redirect ended the turn"
    await asyncio.sleep(2.5)  # past the failure burst
    assert "failed" not in beats()[redirected_at:], beats()[redirected_at:]
    timed = [json.loads(row["detail"]) for row in await asyncio.to_thread(call_activity_rows, "CA_VOICE_TEST2", True)
             if row["event"] == "voice.timing"]
    # The Redirect aborted only the step under way.
    assert [row["tool_call_id"] for row in timed if row.get("tool_status") == "interrupted"] == ["call_profile1"], timed
    await agent.text(sid, REDIRECTED_REPORT)
    await agent.finish(sid)
    await wait_for(lambda: harness_count(stopped, '"outcome":"completed"', REDIRECTED_REPORT) == 1, 10)
    assert OrchestratorMock.tool_output("s2-steer")["when"] == "now"
    assert harness_count("permission_requested:", "py-spy record") == 1
    assert harness_count("permission_resolved:") == 0, "the Redirect's own rejection was reported as another client's"

    # A Redirect whose interrupt fails: the steering was admitted, so the tool says so rather
    # than failing (the model would send it again), and it lands at the next step.
    agent.fail_interrupts = 1
    agent.mark()
    await mocks.send_live(transcript("input", "Have Juniper bisect the regression.", 26200, 26400))
    await mocks.send_live(delegation("del-s3", 26450))
    await wait_for(outputs_back("s3-steer"), 15)
    stuck = OrchestratorMock.tool_output("s3-steer")
    assert stuck["ok"] is True and "do not send it again" in stuck["delivery"], stuck
    assert [item["status"] for item in agent.seen if item["path"] == f"/api/session/{sid}/interrupt"][-1] == 502
    [pending] = agent.inbox.pop(sid)
    await agent.emit("session.inbox.delivered", sessionID=sid, inboxID=pending["id"])
    await agent.text(sid, STUCK_REPORT)
    await agent.finish(sid)
    await wait_for(lambda: harness_count(stopped, '"outcome":"completed"', STUCK_REPORT) == 1, 10)

    # Steering sent as the last execution ends: OpenCode has finished (succeeded, then the steer
    # enqueued and a new execution started with it, as measured on 2.2.7), but the Durable Object
    # hears it all only after the steer is sent. The old turn ends at once, and the steer's
    # execution is a new Prompt Turn whose assignment is the guidance, not another client's.
    agent.mark()
    await mocks.send_live(transcript("input", "Have Juniper rename the parser helpers.", 26500, 26700))
    await mocks.send_live(delegation("del-s4", 26750))
    await agent.next_request(lambda item: item["body"] == {"text": LATE_PROMPT})
    await wait_for(outputs_back("s4-begin"), 10)
    await agent.text(sid, LATE_PROMPT_REPORT)
    loop = asyncio.get_running_loop()

    def finish_unheard() -> None:
        agent.held = []
        asyncio.run_coroutine_threadsafe(agent.finish(sid), loop).result(5)

    LATE_HOOK.append(finish_unheard)
    ended = harness_count(stopped)
    observed = harness_count("turn_started_elsewhere:")
    agent.mark()
    await mocks.send_live(transcript("input", "And the test helpers too.", 26800, 27000))
    await mocks.send_live(delegation("del-s4b", 27050))
    await agent.next_request(lambda item: (item["body"] or {}).get("text") == LATE_GUIDANCE)
    await wait_for(lambda: sid in agent.active, 10)
    await agent.release()
    await wait_for(lambda: harness_count(stopped, '"outcome":"completed"', LATE_PROMPT_REPORT) == 1, 10)
    await wait_for(lambda: harness_count('turn_started_by_steer: {"alias":"Juniper"', LATE_GUIDANCE) == 1, 10)
    assert OrchestratorMock.tool_output("s4-steer")["ok"] is True
    assert harness_count("turn_started_elsewhere:") == observed, "the steer's execution was taken for another client's"
    await agent.text(sid, LATE_REPORT)
    await agent.finish(sid)
    await wait_for(lambda: harness_count(stopped, '"outcome":"completed"', LATE_REPORT) == 1, 10)
    rows = await asyncio.to_thread(call_activity_rows, "CA_VOICE_TEST2")
    assert not [row for row in rows if row["event"] == "agent.turn.steer_delivered" and json.loads(row["detail"])["when"] == "next_step"][1:], \
        "the steer's assignment was reported as a correction"
    assert [json.loads(row["detail"]).get("admitted_by") for row in rows if row["event"] == "agent.turn.started"].count("steer") == 1

    # Cancelling withdraws steering the Agent has not read, then interrupts without resume.
    agent.mark()
    await mocks.send_live(transcript("input", "Have Juniper rewrite the tokenizer.", 26500, 27100))
    await mocks.send_live(delegation("del-c", 27150))
    await agent.next_request(lambda item: item["body"] == {"text": CANCEL_PROMPT})
    await agent.text(sid, CANCEL_REPORT)
    interrupt = await agent.next_request(lambda item: item["path"] == f"/api/session/{sid}/interrupt")
    assert interrupt["query"] == {}, interrupt
    [steered] = [index for index, item in enumerate(agent.seen) if (item["body"] or {}).get("text") == CANCEL_GUIDANCE]
    withdrawn = [index for index, item in enumerate(agent.seen)
                 if item["method"] == "DELETE" and item["path"].startswith(f"/api/session/{sid}/inbox/")]
    listed = requested(agent, "GET", f"/api/session/{sid}/inbox")
    assert len(withdrawn) == 1 and listed and steered < listed[-1] < withdrawn[0] < agent.seen.index(interrupt), (steered, listed, withdrawn)
    assert agent.seen[withdrawn[0]]["status"] == 204 and not agent.inbox.get(sid), agent.inbox
    await wait_for(lambda: harness_count(stopped, '"outcome":"cancelled"', CANCEL_REPORT) == 1, 10)
    assert OrchestratorMock.tool_output("c-cancel")["cancellation"] == "requested"

    # A plain close (1012) is not an Agent Termination: the running turn survives the reconnect.
    agent.mark()
    await mocks.send_live(transcript("input", "Have Juniper run the whole test matrix.", 27500, 28100))
    await mocks.send_live(delegation("del-d", 28150))
    await agent.next_request(lambda item: item["body"] == {"text": DROP_PROMPT})
    await wait_for(outputs_back("d-begin"), 10)
    await agent.ask_permission(sid, "per_matrix1", "bash", ["tox -e all"], [])
    await wait_for(lambda: harness_count("permission_requested:", "tox -e all") == 1, 10)
    connected = agent_connected_count()
    await agent.close_events(1012, "service restarting")
    await wait_for(lambda: agent_connected_count() > connected, 15)
    assert sid in agent.active and harness_count(stopped, DROP_REPORT) == 0
    # Still pending with the Agent, and presented once: the reconnect does not ask again.
    assert harness_count("permission_requested:", "tox -e all") == 1, "a reconnect presented a pending Permission Request again"
    assert harness_count('turn_failed: {"alias":"Juniper"') == failed_before, "a reconnect failed the turn"
    await agent.text(sid, "Halfway through the matrix. ")
    # Down again, and refused while the turn ends unheard: catching up finishes it, once.
    connected = agent_connected_count()
    agent.refuse_events = True
    await agent.close_events(1012, "service restarting")
    await asyncio.sleep(0.5)
    await agent.text(sid, DROP_REPORT)
    agent.settle(sid, "succeeded")
    await asyncio.sleep(2.0)
    agent.refuse_events = False
    await wait_for(lambda: agent_connected_count() > connected, 20)
    await wait_for(lambda: harness_count(stopped, '"outcome":"completed"', DROP_REPORT) == 1, 10)
    assert harness_count('turn_failed: {"alias":"Juniper"') == failed_before
    rows = await asyncio.to_thread(call_activity_rows, "CA_VOICE_TEST2")
    caught_up = [json.loads(row["detail"])["alias"] for row in rows if row["event"] == "agent.turn.caught_up"]
    assert caught_up == ["Dozer", "Juniper"], caught_up

    # Co-presence: a turn another client began is observed, and ends like any other.
    agent.reports[sid] = ""
    await agent.start(sid)
    await wait_for(lambda: harness_count('turn_started_elsewhere: {"alias":"Juniper"}') == 1, 10)
    # It snoozes: no model step starts for it; it waits for the turn's end, which wakes one.
    assert any('turn_started_elsewhere: {"alias":"Juniper"}' in line for line in queued_lines()), queued_lines()
    await agent.text(sid, ELSEWHERE_REPORT)
    await agent.finish(sid)
    await wait_for(lambda: harness_count(stopped, '"outcome":"completed"', '"started_elsewhere":true', ELSEWHERE_REPORT) == 1, 10)
    # A Permission Request answered in that other client stops being presented here.
    await agent.ask_permission(sid, "per_elsewhere1", "edit", ["src/parser.py"], [])
    await wait_for(lambda: harness_count('permission_requested: {"alias":"Juniper"', "edit: src/parser.py") == 1, 10)
    agent.permissions[sid] = [item for item in agent.permissions[sid] if item["id"] != "per_elsewhere1"]
    for _ in range(2):  # Once forgotten, a repeat is nothing new.
        await agent.emit("permission.replied", sessionID=sid, requestID="per_elsewhere1", reply="once")
    await wait_for(lambda: harness_count('permission_resolved: {"alias":"Juniper","reply":"once","elsewhere":true}') >= 1, 10)
    await asyncio.sleep(1.0)
    assert harness_count("permission_resolved:") == 1, harness_count("permission_resolved:")

    # A Redirect made in that other client, whose interrupt is checked against a running list
    # taken before the resume: the check is sent off the event chain, so the resumed
    # execution.started is handled while it waits, and the execution count tells the answer it
    # is stale. The resumed turn goes on.
    observed = harness_count("turn_started_elsewhere:")
    agent.reports[sid] = ""
    await agent.start(sid)
    await wait_for(lambda: harness_count("turn_started_elsewhere:") == observed + 1, 10)
    ended = harness_count(stopped)
    superseded = await activity_count("agent.turn.end_superseded")
    agent.mark()
    agent.active.discard(sid)
    agent.delay_next["/api/session/active"] = 1.0
    await agent.emit("session.execution.interrupted", sessionID=sid, reason="user")
    await agent.next_request(lambda item: item["path"] == "/api/session/active" and item["status"] == "held", 10)
    await agent.emit("session.execution.started", sessionID=sid)
    await wait_for_activity("agent.turn.end_superseded", superseded + 1)
    assert harness_count(stopped) == ended, "a stale check closed the resumed turn"
    agent.active.add(sid)
    await agent.text(sid, RESUMED_REPORT)
    await agent.finish(sid)
    await wait_for(lambda: harness_count(stopped, '"outcome":"completed"', '"started_elsewhere":true', RESUMED_REPORT) == 1, 10)

    # A bare interrupt in that other client leaves its steering parked: the check finds the
    # session idle, so the turn ends cancelled, and the parked item does not hold it open.
    agent.reports[sid] = ""
    await agent.start(sid)
    await wait_for(lambda: harness_count("turn_started_elsewhere:") == observed + 2, 10)
    await agent.enqueue(sid, "inb_parked1", "Use the slower algorithm.")
    await agent.text(sid, PARKED_REPORT)
    agent.settle(sid, "interrupted")
    await agent.emit("session.execution.interrupted", sessionID=sid, reason="user")
    await wait_for(lambda: harness_count(stopped, '"outcome":"cancelled"', '"started_elsewhere":true', PARKED_REPORT) == 1, 10)
    agent.inbox.pop(sid)  # Withdrawn by that client.
    await agent.emit("session.inbox.cancelled", sessionID=sid, inboxID="inb_parked1")

    # A turn that left no ending (lost with the service) is not passed off as completed by an
    # earlier turn's outcome: catching up fails it as unknown.
    await agent.start(sid)
    await wait_for(lambda: harness_count("turn_started_elsewhere:") == observed + 3, 10)
    agent.refuse_events = True
    await agent.close_events(1012, "service restarting")
    agent.active.discard(sid)
    await asyncio.sleep(1.0)
    agent.refuse_events = False
    await wait_for(lambda: harness_count('turn_failed: {"alias":"Juniper"', '"started_elsewhere":true', "outcome unknown") == 1, 20)
    failed_before += 1

    # An Elicitation cannot be answered by voice yet; the caller hears that one waits.
    await agent.emit("form.created", form={"id": "frm_000001", "sessionID": sid, "title": FORM_TITLE, "fields": [{"type": "select"}]})
    await wait_for(lambda: harness_count('elicitation_waiting: {"alias":"Juniper"', FORM_TITLE) == 1, 10)

    # A prompt the preview proxy refuses (an expired preview token) never reached OpenCode, so
    # it is sent again through a fresh preview URL: admitted once.
    reads = DaytonaMock.preview_reads
    agent.reject_next_prompt = True
    agent.mark()
    await mocks.send_live(transcript("input", "Have Juniper list the open issues.", 28500, 29100))
    await mocks.send_live(delegation("del-g", 29150))
    await wait_for(outputs_back("g-begin"), 15)
    attempts = [item["status"] for item in agent.seen if item["body"] == {"text": RETRY_PROMPT}]
    assert attempts == [401, 200] and DaytonaMock.preview_reads == reads + 1, (attempts, DaytonaMock.preview_reads, reads)
    assert OrchestratorMock.tool_output("g-begin")["ok"] is True
    await agent.text(sid, RETRY_REPORT)
    await agent.finish(sid)
    await wait_for(lambda: harness_count(stopped, '"outcome":"completed"', RETRY_REPORT) == 1, 10)
    # A prompt whose answer is lost may have been admitted, so it is never sent again.
    agent.drop_next_prompt = True
    agent.mark()
    await mocks.send_live(transcript("input", "Have Juniper draft the release notes.", 29500, 30100))
    await mocks.send_live(delegation("del-g2", 30150))
    await wait_for(outputs_back("g2-begin"), 15)
    await asyncio.sleep(1.0)
    attempts = [item["status"] for item in agent.seen if item["body"] == {"text": LOST_PROMPT}]
    assert attempts == ["dropped"], attempts
    assert OrchestratorMock.tool_output("g2-begin")["ok"] is False, OrchestratorMock.tool_output("g2-begin")

    assert requested(agent, "GET", "/api/session/active")[-1] > agent.seen.index(next(
        item for item in agent.seen if item["body"] == {"text": LOST_PROMPT})), "the lost answer was not checked against the running sessions"
    # A prompt whose answer is lost after it was admitted: the session is running, so the turn goes on.
    agent.drop_admitted_prompt = True
    agent.mark()
    await mocks.send_live(transcript("input", "Have Juniper count the public functions.", 30500, 31100))
    await mocks.send_live(delegation("del-g3", 31150))
    await wait_for(outputs_back("g3-begin"), 15)
    admitted = OrchestratorMock.tool_output("g3-begin")
    assert admitted["ok"] is True and admitted["turn_state"] == "in_progress", admitted
    assert [item["status"] for item in agent.seen if item["body"] == {"text": ADMITTED_PROMPT}] == ["dropped"]
    rows = await asyncio.to_thread(call_activity_rows, "CA_VOICE_TEST2")
    assert any(json.loads(row["detail"]).get("admitted_by") == "active_check" for row in rows if row["event"] == "agent.turn.started")

    # Cancelling and closing whose interrupts fail: the turn keeps running and the session stays open.
    agent.fail_interrupts = 2
    agent.mark()
    await mocks.send_live(transcript("input", "Stop Juniper and close it.", 31500, 32100))
    await mocks.send_live(delegation("del-x", 32150))
    await wait_for(outputs_back("x-inspect"), 15)
    assert OrchestratorMock.tool_output("x-cancel")["ok"] is False and OrchestratorMock.tool_output("x-close")["ok"] is False
    kept = OrchestratorMock.tool_output("x-inspect")
    assert (kept["session_state"], kept["turn_state"]) == ("ready", "in_progress"), kept
    await agent.text(sid, ADMITTED_REPORT)
    await agent.finish(sid)
    await wait_for(lambda: harness_count(stopped, '"outcome":"completed"', ADMITTED_REPORT) == 1, 10)

    # Every turn above ended exactly once, and only the lost one failed.
    for report in (STEERED_REPORT, REDIRECTED_REPORT, STUCK_REPORT, LATE_PROMPT_REPORT, LATE_REPORT, CANCEL_REPORT, DROP_REPORT,
                   ELSEWHERE_REPORT, RESUMED_REPORT, PARKED_REPORT, RETRY_REPORT, ADMITTED_REPORT):
        assert harness_count(stopped, report) == 1, (report, harness_count(stopped, report))
    assert harness_count('turn_failed: {"alias":"Juniper"') == failed_before


async def run_unobserved_end(restart) -> None:
    """The Durable Object resets mid-call: the saved transcript survives and is filed at call end."""
    mocks = SocketMocks()
    spoken = ["Is the brake noise worth worrying about?", "Let me think that over."]
    # Since the last call, a reap took an old Sandbox out of service (and its deletion is done):
    # this Opening says so, once, in place of the welcome written about the old Sandbox.
    await asyncio.to_thread(d1, f"""INSERT INTO sandbox_deletions (provider_id, user_id, reaped_at, next_attempt_at, deleted_at)
        SELECT 'mock-reaped-old', id, unixepoch() - 60, unixepoch(), unixepoch() FROM users WHERE home_phone = '{TEST_PHONE}'""")
    async with mocks.serving():
        body, webhook_identity, signature = signed_webhook("CA_VOICE_TEST3", "live-test-4")
        assert await asyncio.to_thread(post_webhook, body, webhook_identity, signature) == 204
        await asyncio.wait_for(mocks.live_connected.wait(), 8)
        cleared = await next_matching(mocks.live_messages, lambda item: item.get("type") == "session.commentary.append"
                                      and REAPED_CLAUSE in item.get("content", ""))
        assert cleared["content"].endswith(f"{REAPED_CLAUSE} Welcome back. Where to?"), cleared["content"]
        notice = await asyncio.to_thread(d1, "SELECT noticed_at FROM sandbox_deletions WHERE provider_id = 'mock-reaped-old'")
        assert notice[0]["noticed_at"] is not None, "the reap notice was not consumed, so a later Opening would repeat it"
        await mocks.send_live(transcript("input", spoken[0], 0, 1500))
        await mocks.send_live(transcript("output", spoken[1], 1600, 2400))
        async with asyncio.timeout(10):
            while not (await asyncio.to_thread(conversations))[0]["current_transcript"]:
                await asyncio.sleep(0.2)
        # Restarting wrangler resets the object with the sideband still open: no cleanup runs.
        # The mock sockets then drop without a close frame, which is expected here.
        logging.getLogger("aiohttp").setLevel(logging.CRITICAL)
        await asyncio.to_thread(restart)
    state = (await asyncio.to_thread(conversations))[0]
    assert state["current_transcript"]["call_sid"] == "CA_VOICE_TEST3", state["current_transcript"]
    assert not any(item["call_sid"] == "CA_VOICE_TEST3" for item in state["call_transcripts"]), "cleanup ran"
    status, _ = await asyncio.to_thread(post, DEFAULT_LOCAL, "/voice/status", {
        "From": TEST_PHONE, "CallSid": "CA_VOICE_TEST3", "CallStatus": "completed", "CallDuration": "30",
    }, LOCAL_TOKEN)
    assert status == 204, status
    state = (await asyncio.to_thread(conversations))[0]
    assert state["current_transcript"] is None
    filed = state["call_transcripts"][-1]
    assert filed["call_sid"] == "CA_VOICE_TEST3" and [entry["text"] for entry in filed["entries"]] == spoken, filed
    # last_call carries the recovered last words into the next call's since_last_call.
    ended = state["orchestrator_queue"] + [json.dumps(item) for item in state["orchestrator_context"]]
    assert any("voice end not observed" in item for item in ended), ended[-3:]


def sandbox_toolbox(command: str) -> dict | None:
    """Sandbox commands: honeydozer is the only project directory besides home; escape.png links outside it."""
    if "realpath -e --" in command:
        path = re.search(r"/home/daytona[^'\\]*", command).group(0)
        if path.endswith("/escape.png"):
            return {"result": "/etc/shadow\n", "exitCode": 0}
        known = path.endswith("/REPORT.md") or path.endswith("/report.html") or path.rsplit("/", 1)[-1] in IMAGE_FILES
        return {"result": f"{path}\n", "exitCode": 0} if known else {"result": "", "exitCode": 1}
    if "test -d" in command:
        return {"result": "", "exitCode": 0 if "/home/daytona/honeydozer" in command else 1}
    if "remote.origin.url" in command:
        rows = [
            ".\t0\t1\t",
            "./honeydozer\t1\t1\thttps://x-access-token:ghp_mocksecret@github.com/rndmcnlly/honeydozer.git",
        ]
        return {"result": "\n".join(rows) + "\n", "exitCode": 0}
    return None


def whiteboard_parser_checks() -> None:
    """The page's openui-lang parser, extracted from pair_page.ts and run in Node: Image takes a
    Workspace path and nothing that could reach another origin."""
    page = (WORKER_DIR / "src" / "pair_page.ts").read_text()
    resolve = re.search(r"function resolvePath\(.*?\n}\n", page, re.S).group(0)
    parser = page[page.index("// ---- Tokens:"):page.index("// ---- Rendering.")]
    cases = [
        'root = Stack([fig, t])\nfig = Image("bayleaf/diagram.svg", "Chain path", "Generated, unverified", "large")\nt = Text("hi")',
        'root = Stack([Image("https://example.com/a.png", "x")])',
        'root = Stack([Image("data:image/png;base64,AAAA", "x")])',
        'root = Stack([Image("../../etc/a.png", "x")])',
        'root = Stack([Image("notes.txt", "x")])',
        'root = Stack([Image("/home/daytona/icon.png", "", null, "huge")])',
        'root = Stack([Image (path, "Plot")])\npath = alias\nalias = "chart.png"',
    ]
    script = (
        'const HOME = "/home/daytona";\n' + resolve + parser
        + f"\nconsole.log(JSON.stringify({json.dumps(cases)}.map((source) => {{ const r = wbProgram(source);"
        + " return { errors: r.errors, children: r.root ? r.root.props.children.map((c) => c.props) : null }; })));"
    )
    results = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    drawn, url, data, escape, text, size, reference = results
    assert not drawn["errors"] and drawn["children"][0] == {
        "path": "/home/daytona/bayleaf/diagram.svg", "alt": "Chain path", "caption": "Generated, unverified", "size": "large",
    }, drawn
    for refused, why in [(url, "never a URL"), (data, "never a URL"), (escape, "under /home/daytona"), (text, ".png")]:
        assert refused["children"] == [] and any(why in error for error in refused["errors"]), refused
    assert size["children"] == [{"path": "/home/daytona/icon.png", "alt": ""}] and "must be one of icon" in size["errors"][0], size
    assert not reference["errors"] and reference["children"] == [{"path": "/home/daytona/chart.png", "alt": "Plot"}], reference


def main() -> int:
    whiteboard_parser_checks()
    wrangler = project_wrangler()
    if not wrangler:
        print("wrangler is not installed", file=sys.stderr)
        return 2
    prepare_local_fixture(wrangler)
    identity = hmac.new(
        IDENTITY_SECRET.encode(), TEST_PHONE.encode(), hashlib.sha256
    ).hexdigest()
    configure_assignment(wrangler, identity)
    DaytonaMock.exists = True
    DaytonaMock.generation = 1
    DaytonaMock.openai_requests = []
    DaytonaMock.preview_reads = 0
    DaytonaMock.executed = []
    DaytonaMock.execute_hooks = [sandbox_toolbox]
    DaytonaMock.get_hooks = [sandbox_files, provider_gets]
    DaytonaMock.post_hooks = [provider_posts]
    DaytonaMock.delete_hooks = [provider_deletes]
    daytona = ThreadingHTTPServer(("127.0.0.1", DAYTONA_MOCK_PORT), DaytonaMock)
    thread = threading.Thread(target=daytona.serve_forever, daemon=True)
    thread.start()
    orchestrator = ThreadingHTTPServer(("127.0.0.1", ORCHESTRATOR_MOCK_PORT), OrchestratorMock)
    threading.Thread(target=orchestrator.serve_forever, daemon=True).start()
    WRANGLER_LOG.write_text("")
    processes = [start_wrangler(wrangler)]

    def restart() -> None:
        stop_wrangler(processes[-1])
        processes.append(start_wrangler(wrangler))

    try:
        asyncio.run(run_protocol(identity))
        asyncio.run(run_unobserved_end(restart))
        asyncio.run(pairing_claim_checks(identity))
        print("voice session delegation checks passed")
        return 0
    except BaseException:
        # The known flake is one Worker call stalling under host load; these show which one.
        tail = WRANGLER_LOG.read_text(errors="replace").splitlines()[-60:]
        print(f"--- last lines of {WRANGLER_LOG}", *tail, sep="\n", file=sys.stderr)
        # A tool.started with no tool.completed names the Worker call that stalled.
        for call_sid in ("CA_VOICE_TEST", "CA_VOICE_TEST2", "CA_VOICE_TEST3"):
            try:
                rows = [activity_line(row) for row in call_activity_rows(call_sid, timing=True)[-20:]]
            except Exception as error:
                rows = [f"unavailable: {error!r}"]
            print(f"--- last activity of {call_sid}", *rows, sep="\n", file=sys.stderr)
        raise
    finally:
        stop_wrangler(processes[-1])
        daytona.shutdown()
        daytona.server_close()
        orchestrator.shutdown()
        orchestrator.server_close()


def stop_wrangler(process: subprocess.Popen) -> None:
    process.send_signal(signal.SIGTERM)
    process.wait(timeout=10)
    # The port frees once workerd exits, which may lag wrangler itself.
    deadline = time.time() + 10
    while time.time() < deadline and wait_port("127.0.0.1", WORKER_PORT, timeout=0.2):
        time.sleep(0.2)


def start_wrangler(wrangler: str) -> subprocess.Popen:
    process = subprocess.Popen(
        [
            wrangler,
            "dev",
            "--port",
            str(WORKER_PORT),
            "--inspector-port",
            str(INSPECTOR_PORT),
            "--ip",
            "127.0.0.1",
            "--var",
            "DEPLOYMENT_ENVIRONMENT:local-test",
            "--var",
            f"TWILIO_AUTH_TOKEN:{LOCAL_TOKEN}",
            "--var",
            "DAYTONA_API_KEY:local-daytona-token",
            "--var",
            f"DOGWALK_IDENTITY_SECRET:{IDENTITY_SECRET}",
            "--var",
            f"DAYTONA_API_BASE:http://127.0.0.1:{DAYTONA_MOCK_PORT}/api",
            "--var",
            "OPENAI_API_KEY:local-openai-token",
            "--var",
            "OPENAI_PROJECT_ID:proj_test",
            "--var",
            f"OPENAI_WEBHOOK_SECRET:{OPENAI_WEBHOOK_SECRET}",
            "--var",
            f"OPENAI_LIVE_API_BASE:http://127.0.0.1:{DAYTONA_MOCK_PORT}/live",
            "--var",
            f"OPENAI_LIVE_ATTACH_BASE:http://127.0.0.1:{SOCKET_PORT}/live",
            "--var",
            f"ORCHESTRATOR_API_BASE:http://127.0.0.1:{ORCHESTRATOR_MOCK_PORT}/v1",
            "--var",
            f"ADMIN_PASSWORD:{LOCAL_ADMIN_PASSWORD}",
            # Registration and invitation rituals here assume shadow Roaming, whatever wrangler.toml deploys.
            "--var",
            "ROAMING_ADMISSION:off",
        ],
        cwd=WORKER_DIR,
        stdout=WRANGLER_LOG.open("a"),
        stderr=subprocess.STDOUT,
    )
    if not wait_port("127.0.0.1", WORKER_PORT, timeout=20):
        process.kill()
        raise RuntimeError("wrangler dev did not start")
    return process


if __name__ == "__main__":
    sys.exit(main())
