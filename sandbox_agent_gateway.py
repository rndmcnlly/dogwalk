#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.12,<4"]
# ///
"""Supervise the Sandbox's shared OpenCode service and expose its native API to Dogwalk."""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from aiohttp import (
    ClientConnectorError,
    ClientError,
    ClientResponse,
    ClientSession,
    ClientTimeout,
    WSMsgType,
    web,
)
from aiohttp.abc import AbstractAccessLogger
from yarl import URL


LOG = logging.getLogger("agent_gateway")
MAX_BODY_BYTES = 1024 * 1024
# One OpenCode event can carry a whole tool result or message part; the binary guard keeps
# reads small, but shell output is not bounded the same way.
MAX_EVENT_BYTES = 16 * 1024 * 1024
SERVICE_USER = "opencode"
# Never forwarded upstream: hop-by-hop headers, the client's own credentials (the gateway
# supplies the service's), and the body framing aiohttp recomputes. `x-daytona-*` go too.
DROPPED_REQUEST_HEADERS = frozenset({
    "authorization", "proxy-authorization", "cookie", "host", "connection", "keep-alive",
    "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade", "content-length",
    "accept-encoding",
})


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


# Where `opencode serve --service` registers itself once listening, and where a plain `opencode`
# TUI looks for it. The service rewrites or abandons this file itself; the gateway only reads it.
def state_file() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "opencode" / "service.json"


@dataclass(frozen=True)
class Service:
    url: str
    password: str
    pid: int | None

    def authorization(self) -> str:
        return "Basic " + base64.b64encode(f"{SERVICE_USER}:{self.password}".encode()).decode()


def read_state(path: Path) -> Service | None:
    try:
        state = json.loads(path.read_text())
        url, password = state["url"], state["password"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(url, str) or not isinstance(password, str) or not url.startswith(("http://", "https://")):
        return None
    pid = state.get("pid")
    return Service(url.rstrip("/"), password, pid if isinstance(pid, int) and not isinstance(pid, bool) else None)


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
    """A small, neutral account of how the Agent process ended, for Dogwalk."""
    result: dict = {"reason": "exited", "exit_status": returncode}
    if returncode is not None and returncode < 0:
        with contextlib.suppress(ValueError):
            result["signal"] = signal.Signals(-returncode).name
        result["reason"] = "killed"
    # A process can exit 1 after a child of its own is OOM-killed, so the counter decides,
    # not the signal.
    if any(after.get(directory, 0) > count for directory, count in before.items()):
        result["reason"] = "out_of_memory"
    if limit is not None:
        result["limit_bytes"] = limit
    if result["reason"] == "out_of_memory":
        size = f" ({limit / 2**30:.3g} GiB limit)" if limit else ""
        result["detail"] = f"The Agent was killed because the sandbox ran out of memory{size}."
    elif result["reason"] == "killed":
        result["detail"] = f"The Agent was killed by {result.get('signal', 'a signal')}."
    else:
        result["detail"] = f"The Agent exited with status {returncode}."
    return result


def termination_reason(termination: dict) -> bytes:
    # A close reason holds at most 123 bytes: the detail stays in the log and /healthz.
    brief = {key: termination[key] for key in ("reason", "signal", "exit_status", "limit_bytes", "stalled") if key in termination}
    return json.dumps(brief, separators=(",", ":")).encode()


TRACEPARENT = re.compile(r"00-(?!0{32})([0-9a-f]{32})-(?!0{16})([0-9a-f]{16})-[0-9a-f]{2}")
LOG_TOKEN = re.compile(r"[A-Za-z0-9_./-]{1,64}")
LOG_PATH = re.compile(r"/[A-Za-z0-9_./-]{0,255}")


def trace_fields(method: object, path: object, traceparent: object) -> dict:
    """The W3C trace context a proxied request carries in its `traceparent` header, as
    metadata only: an invalid one is reported, never echoed."""
    # Only bounded tokens reach the log: a method or path of another shape is a marker,
    # and the query string never appears.
    fields: dict = {
        "method": method if isinstance(method, str) and LOG_TOKEN.fullmatch(method) else "invalid",
        "path": path if isinstance(path, str) and LOG_PATH.fullmatch(path) else "invalid",
    }
    # fullmatch, not match: `$` would accept a trailing newline the Worker's parser rejects.
    match = TRACEPARENT.fullmatch(traceparent) if isinstance(traceparent, str) else None
    if match:
        fields.update(trace_id=match[1], span_id=match[2])
    else:
        fields["traceparent"] = "invalid"
    return fields


class ServiceUnavailable(Exception):
    pass


class EventTooLarge(Exception):
    pass


class EventStreamStalled(Exception):
    pass


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Another user's process: alive, just not ours to signal.
    return True


@dataclass
class Child:
    """An OpenCode service process this gateway started."""

    process: asyncio.subprocess.Process
    oom_before: dict[Path, int]
    started: float
    # Resolves to an Agent Termination, or None for a clean exit (another process owns the service).
    ended: asyncio.Task | None = None
    # Set when the gateway stopped it for never registering.
    startup_timed_out: bool = False


class AgentGateway:
    """Keep the shared OpenCode service running, proxy its API, and relay its events."""

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
        self.service: Service | None = None
        self.child: Child | None = None
        self.last_termination: dict | None = None
        self.event_clients: set[web.WebSocketResponse] = set()
        # How often a service someone else owns is checked, and how long a request waits
        # for one to come up (a call that wakes the Sandbox arrives before OpenCode is ready).
        self.poll_seconds = 5.0
        self.wait_seconds = 15.0
        self.probe_seconds = 3.0
        # A service whose process is alive is replaced only after this many probes in a row
        # fail: one slow answer must not start a second server and split Dogwalk from a TUI.
        self.probe_failures_to_replace = 3
        # A child that has not registered by then is stopped and retried with backoff.
        self.startup_seconds = 60.0
        # OpenCode 2.2.7 writes `: heartbeat` on `/api/event` every 15 s, so three missed
        # heartbeats mean the service is wedged even though its socket is open.
        self.event_idle_seconds = 45.0
        self._http: ClientSession | None = None
        self._supervisor: asyncio.Task | None = None

    async def start(self, app: web.Application) -> None:
        self._http = ClientSession(timeout=ClientTimeout(total=None, sock_connect=5))
        self._supervisor = asyncio.create_task(self.supervise())

    async def shutdown(self, app: web.Application) -> None:
        for websocket in list(self.event_clients):
            await websocket.close(code=1001, message=b"gateway stopping")

    async def cleanup(self, app: web.Application) -> None:
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
        if self.child is not None:
            await self.stop(self.child)
        if self._http is not None:
            await self._http.close()

    @staticmethod
    async def stop(child: Child) -> None:
        if child.process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            child.process.terminate()
        try:
            await asyncio.wait_for(child.process.wait(), timeout=5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                child.process.kill()
            await child.process.wait()

    # Service discovery

    async def probe(self, service: Service) -> bool:
        assert self._http is not None
        try:
            async with self._http.get(
                f"{service.url}/api/info",
                headers={"Authorization": service.authorization()},
                timeout=ClientTimeout(total=self.probe_seconds),
            ) as response:
                return response.status == 200
        except (ClientError, TimeoutError):
            return False

    async def refresh(self) -> Service | None:
        """Find the shared service through its state file, and keep it only if it answers."""
        found = read_state(state_file())
        self.service = found if found is not None and await self.probe(found) else None
        return self.service

    async def current(self) -> Service | None:
        deadline = time.monotonic() + self.wait_seconds
        while True:
            service = self.service or await self.refresh()
            if service is not None or time.monotonic() >= deadline:
                return service
            await asyncio.sleep(0.25)

    # Supervision

    async def supervise(self) -> None:
        delay = 1.0
        failures = 0
        while True:
            if await self.refresh() is not None:
                # Healthy, whoever owns it. A service this gateway started is watched through
                # its process instead, below; this branch covers one owned by someone else.
                failures = 0
                await asyncio.sleep(self.poll_seconds)
                continue
            # Spawn at once when nothing is registered or its process is gone. A live one that
            # failed a probe may only be slow, and a spawn would overwrite its state file.
            found = read_state(state_file())
            failures += 1
            if found is not None and (found.pid is None or process_alive(found.pid)) \
                    and failures < self.probe_failures_to_replace:
                LOG.warning("agent_probe_failed pid=%s failures=%s", found.pid, failures)
                await asyncio.sleep(self.poll_seconds)
                continue
            failures = 0
            try:
                child = await self.spawn()
            except OSError as error:
                LOG.error("agent_spawn_failed %s", error)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            assert child.ended is not None
            while not child.ended.done() and await self.refresh() is None:
                if time.monotonic() - child.started > self.startup_seconds:
                    LOG.warning("agent_start_timeout pid=%s seconds=%s", child.process.pid, self.startup_seconds)
                    child.startup_timed_out = True
                    await self.stop(child)
                    break
                await asyncio.wait({child.ended}, timeout=0.25)
            termination = await child.ended
            self.child = None
            self.service = None
            if termination is None and await self.refresh() is not None:
                # `opencode serve --service` exits 0 when a healthy service already answers.
                LOG.info("agent_handoff pid=%s service_pid=%s", child.process.pid, self.service and self.service.pid)
                continue
            # A child that served for a while earns a fresh backoff; one that never started does not.
            if not child.startup_timed_out and time.monotonic() - child.started > 60:
                delay = 1.0
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def spawn(self) -> Child:
        oom_before = oom_kills(self.cgroups)
        # Same user, HOME, and environment as the gateway, so the state file it writes is the
        # one a TUI in a Takeover Terminal reads. Its output joins the gateway's.
        process = await asyncio.create_subprocess_exec(
            *command_arguments(self.agent_command, self.workspace),
            cwd=self.workspace,
            stdin=asyncio.subprocess.DEVNULL,
        )
        child = Child(process, oom_before, time.monotonic())
        child.ended = asyncio.create_task(self._watch(child))
        self.child = child
        LOG.info("agent_started pid=%s", process.pid)
        return child

    async def _watch(self, child: Child) -> dict | None:
        returncode = await child.process.wait()
        if returncode == 0 and not child.startup_timed_out:
            LOG.info("agent_exited pid=%s exit_status=0", child.process.pid)
            return None
        termination = agent_termination(
            returncode, child.oom_before, oom_kills(self.cgroups), memory_limit(self.cgroups)
        )
        if child.startup_timed_out and termination["reason"] != "out_of_memory":
            # The gateway stopped it, so "killed" (a reason the Worker parses), with the cause.
            termination.update(
                reason="killed", signal=termination.get("signal", "SIGTERM"), stalled="startup",
                detail=f"The Agent did not start within {self.startup_seconds:g} s and was stopped.",
            )
        self.last_termination = termination
        LOG.warning("agent_terminated %s", json.dumps(termination))
        return termination

    # Endpoints

    async def health(self, request: web.Request) -> web.Response:
        service, child = self.service, self.child
        return web.json_response({
            "ok": True,
            "service": "resolved" if service else "unresolved",
            "service_url": service.url if service else None,
            "service_pid": service.pid if service else None,
            "child_pid": child.process.pid if child and child.process.returncode is None else None,
            "event_clients": len(self.event_clients),
            "last_termination": self.last_termination,
        })

    async def ready(self, request: web.Request) -> web.Response:
        workspace_ready = self.workspace.is_dir()
        agent_ready = executable_available(self.agent_command, self.workspace)
        service = self.service
        service_ready = (service is not None and await self.probe(service)) or await self.refresh() is not None
        status = 200 if workspace_ready and agent_ready and service_ready else 503
        return web.json_response(
            {
                "ok": status == 200,
                "workspace": "ready" if workspace_ready else "missing",
                "agent_executable": "ready" if agent_ready else "missing",
                "service": "ready" if service_ready else "unavailable",
            },
            status=status,
        )

    async def request_upstream(
        self, method: str, path_qs: str, headers: dict[str, str], body: bytes,
        timeout: ClientTimeout | None = None,
    ) -> ClientResponse:
        """Send one request with the service's credentials. A refused connection or a 401
        (the service restarted with a new password, or moved) re-resolves once and retries."""
        assert self._http is not None
        service = await self.current()
        for attempt in range(2):
            if service is None:
                raise ServiceUnavailable
            try:
                response = await self._http.request(
                    method,
                    URL(service.url + path_qs, encoded=True),
                    headers={**headers, "Authorization": service.authorization()},
                    data=body or None,
                    allow_redirects=False,
                    **({"timeout": timeout} if timeout is not None else {}),
                )
            except ClientConnectorError:
                if attempt:
                    raise
            else:
                if response.status != 401 or attempt:
                    return response
                response.release()
            self.service = None
            service = await self.current()
        raise AssertionError("unreachable")

    async def proxy(self, request: web.Request) -> web.StreamResponse:
        received_ms = time.time_ns() // 1_000_000
        body = await request.read()
        headers = {
            name: value for name, value in request.headers.items()
            if name.lower() not in DROPPED_REQUEST_HEADERS and not name.lower().startswith("x-daytona-")
        }
        try:
            upstream = await self.request_upstream(request.method, request.raw_path, headers, body)
        except ServiceUnavailable:
            return web.json_response({"error": "The Agent service is not available."}, status=503)
        except (ClientError, TimeoutError):
            return web.json_response({"error": "The Agent service did not answer."}, status=502)
        if "traceparent" in request.headers:
            LOG.info("agent_trace_context %s", json.dumps({
                **trace_fields(request.method, request.path, request.headers["traceparent"]),
                "status": upstream.status,
                "received_ms": received_ms, "forwarded_ms": time.time_ns() // 1_000_000,
            }, separators=(",", ":")))
        async with upstream:
            if upstream.status in (204, 304) or request.method == "HEAD":
                return web.Response(status=upstream.status)
            response = web.StreamResponse(status=upstream.status)
            if content_type := upstream.headers.get("Content-Type"):
                response.headers["Content-Type"] = content_type
            await response.prepare(request)
            # Streamed, so `/api/event` works through here too.
            try:
                async for chunk in upstream.content.iter_any():
                    await response.write(chunk)
                await response.write_eof()
            except (ClientError, ConnectionError, TimeoutError):
                # Either end went away. Drop the connection rather than end the body cleanly,
                # so the client cannot mistake a truncated body for a complete one.
                if request.transport is not None:
                    request.transport.abort()
            return response

    async def events(self, request: web.Request) -> web.StreamResponse:
        client_id = str(uuid.uuid4())
        websocket = web.WebSocketResponse(heartbeat=30)
        await websocket.prepare(request)
        self.event_clients.add(websocket)
        LOG.info("events_opened client_id=%s", client_id)
        try:
            await self._relay_events(websocket)
        except Exception:
            LOG.exception("events_failed client_id=%s", client_id)
            if not websocket.closed:
                await websocket.close(code=1011, message=b"event relay failed")
        finally:
            self.event_clients.discard(websocket)
            if not websocket.closed:
                await websocket.close()
            LOG.info("events_closed client_id=%s", client_id)
        return websocket

    async def _relay_events(self, websocket: web.WebSocketResponse) -> None:
        # sock_read bounds every read of the body, so silence past a few heartbeats ends it.
        idle = ClientTimeout(total=None, sock_connect=5, sock_read=self.event_idle_seconds)
        try:
            upstream = await self.request_upstream("GET", "/api/event", {"Accept": "text/event-stream"}, b"", idle)
        except (ServiceUnavailable, ClientError, TimeoutError):
            await websocket.close(code=1013, message=b"agent service unavailable")
            return
        # The process this stream comes from, when this gateway started it: only its death can
        # be explained. None for a service someone else owns.
        child = self.child
        async with upstream:
            if upstream.status != 200:
                await websocket.close(code=1013, message=b"agent service unavailable")
                return
            pump = asyncio.create_task(self._pump(upstream, websocket))
            inbound = asyncio.create_task(self._ignore_inbound(websocket))
            try:
                await asyncio.wait({pump, inbound}, return_when=asyncio.FIRST_COMPLETED)
                if inbound.done() or websocket.closed:
                    return
                if isinstance(pump.exception(), EventTooLarge):
                    await websocket.close(code=1009, message=b"event too large")
                    return
                if isinstance(pump.exception(), EventStreamStalled):
                    # Wedged with its socket open: drop the cached service so the next
                    # request re-probes it, and let the client reconnect.
                    LOG.warning("agent_events_stalled idle_seconds=%s", self.event_idle_seconds)
                    self.service = None
                    await websocket.close(code=1012, message=b"agent service stalled")
                    return
                # Upstream ended. If the service died abnormally, say how, in the format the
                # Worker parses; otherwise the client just reconnects.
                termination = None
                if child is not None and child.ended is not None:
                    await asyncio.wait({child.ended}, timeout=2)
                    if child.ended.done():
                        termination = child.ended.result()
                if termination is not None:
                    await websocket.close(code=1011, message=termination_reason(termination))
                else:
                    await websocket.close(code=1012, message=b"service restarting")
            finally:
                for task in (pump, inbound):
                    task.cancel()
                await asyncio.gather(pump, inbound, return_exceptions=True)

    @staticmethod
    async def _pump(upstream: ClientResponse, websocket: web.WebSocketResponse) -> None:
        """Forward each SSE event's data, unchanged, as one text frame."""
        buffer = bytearray()
        data: list[bytes] = []
        size = scanned = 0
        try:
            async for chunk in upstream.content.iter_any():
                buffer += chunk
                while (end := buffer.find(b"\n", scanned)) >= 0:
                    line = bytes(buffer[:end]).rstrip(b"\r")
                    del buffer[:end + 1]
                    scanned = 0
                    if not line:
                        if data:
                            await websocket.send_str(b"\n".join(data).decode("utf-8", "replace"))
                        data, size = [], 0
                    elif line.startswith(b"data:"):
                        value = line[6:] if line.startswith(b"data: ") else line[5:]
                        data.append(value)
                        size += len(value) + 1
                    # Comments, `event:`, `id:`, and `retry:` lines carry nothing OpenCode needs.
                scanned = len(buffer)
                if size + len(buffer) > MAX_EVENT_BYTES:
                    raise EventTooLarge
        except TimeoutError as error:
            # aiohttp's ServerTimeoutError is also a ClientError: the read timeout, not a drop.
            raise EventStreamStalled from error
        except (ClientError, ConnectionError):
            return

    @staticmethod
    async def _ignore_inbound(websocket: web.WebSocketResponse) -> None:
        async for _ in websocket:
            pass


class AccessLogger(AbstractAccessLogger):
    """One line per request without its query string, which may name Workspace paths."""

    def log(self, request: web.BaseRequest, response: web.StreamResponse, elapsed: float) -> None:
        self.logger.info("%s %s %s %.3fs", request.method, request.path, response.status, elapsed)


def create_app(gateway: AgentGateway) -> web.Application:
    app = web.Application(client_max_size=MAX_BODY_BYTES)
    app.on_startup.append(gateway.start)
    app.on_shutdown.append(gateway.shutdown)
    app.on_cleanup.append(gateway.cleanup)
    app.add_routes(
        [
            web.get("/healthz", gateway.health),
            web.get("/readyz", gateway.ready),
            web.get("/events", gateway.events),
            web.route("*", "/api/{tail:.*}", gateway.proxy),
        ]
    )
    return app


# A stand-in for `opencode serve --service`, stdlib only: the test writes it to a file and
# runs it as the Agent command.
FAKE_SERVICE = r'''
import base64, http.server, json, os, queue, signal, sys, threading, time, urllib.request
from pathlib import Path

STATE = Path(os.environ["XDG_STATE_HOME"]) / "opencode" / "service.json"


def basic(password):
    return "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()


def healthy():
    try:
        state = json.loads(STATE.read_text())
        request = urllib.request.Request(state["url"] + "/api/info", headers={"Authorization": basic(state["password"])})
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


if sys.argv[1:2] == ["--hold"]:
    while not Path(sys.argv[2]).exists():
        time.sleep(0.05)
if healthy():
    sys.exit(0)  # Like the real service: someone else already owns it.
password = os.urandom(8).hex()
subscribers = []
lock = threading.Lock()
# Like OpenCode's `: heartbeat` every 15 s, faster. A wedged service sends nothing.
HEARTBEAT_SECONDS = float(os.environ.get("DOGWALK_TEST_HEARTBEAT", "0.2"))
wedged = threading.Event()


def broadcast(payload):
    with lock:
        for subscriber in subscribers:
            subscriber.put(payload)
        return len(subscribers)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self):
        if self.headers.get("Authorization") != basic(password):
            return self.send_json(401, {"error": "unauthorized"})
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        path = self.path.split("?")[0]
        if path == "/api/info":
            return self.send_json(200, {"version": "fake", "pid": os.getpid(), "urls": []})
        if path == "/api/echo" and self.command == "DELETE":
            self.send_response(204)
            self.end_headers()
            return
        if path == "/api/echo":
            headers = {name.lower(): value for name, value in self.headers.items()}
            return self.send_json(200, {"method": self.command, "path": self.path, "body": body, "pid": os.getpid(), "headers": headers})
        if path == "/api/event":
            return self.stream()
        if path == "/api/test/emit":
            return self.send_json(200, {"subscribers": broadcast(body)})
        if path == "/api/test/end":
            return self.send_json(200, {"subscribers": broadcast(None)})
        if path == "/api/test/wedge":
            wedged.set()
            return self.send_json(200, {})
        if path == "/api/test/unwedge":
            wedged.clear()
            return self.send_json(200, {})
        if path == "/api/test/die":
            if "oom" in self.path:
                # Stand in for the kernel: count an OOM kill on the cgroup, then SIGKILL.
                events = Path(os.environ["DOGWALK_TEST_MEMORY_EVENTS"])
                lines = events.read_text().splitlines()
                lines = [f"oom_kill {int(line.split()[1]) + 1}" if line.startswith("oom_kill ") else line for line in lines]
                events.write_text("\n".join(lines) + "\n")
                os.kill(os.getpid(), signal.SIGKILL)
                time.sleep(5)  # Delivery can lag the call.
            os._exit(3)
        self.send_json(404, {"error": "not found"})

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_any

    def stream(self):
        events = queue.Queue()
        with lock:
            subscribers.append(events)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.write_event('{"type":"server.connected","properties":{}}')
            while True:
                try:
                    payload = events.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    if not wedged.is_set():
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                    continue
                if payload is None:
                    break
                self.write_event(payload)
        except OSError:
            pass
        finally:
            with lock:
                subscribers.remove(events)

    def write_event(self, payload):
        self.wfile.write(("".join(f"data: {line}\n" for line in payload.split("\n")) + "\n").encode())
        self.wfile.flush()


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
server.daemon_threads = True
STATE.parent.mkdir(parents=True, exist_ok=True)
pending = STATE.with_suffix(f".{os.getpid()}.tmp")
pending.write_text(json.dumps({
    "id": f"fake-{os.getpid()}", "version": "fake", "pid": os.getpid(), "password": password,
    "url": f"http://127.0.0.1:{server.server_address[1]}",
}))
pending.replace(STATE)
server.serve_forever()
'''


async def run_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="agent-gateway-test-"))
    # A cgroup v2 tree whose limit sits on the parent of the gateway's own cgroup.
    cgroups = root / "cgroup"
    (cgroups / "gateway").mkdir(parents=True)
    (cgroups / "memory.events").write_text("low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n")
    (cgroups / "memory.max").write_text("1073741824\n")
    (cgroups / "gateway" / "memory.events").write_text("oom_kill 0\n")
    (cgroups / "gateway" / "memory.max").write_text("max\n")
    (cgroups / "proc-cgroup").write_text("0::/gateway\n")
    os.environ["DOGWALK_TEST_MEMORY_EVENTS"] = str(cgroups / "memory.events")
    os.environ["XDG_STATE_HOME"] = str(root / "state")
    fake = root / "fake_service.py"
    fake.write_text(FAKE_SERVICE)
    fake_command = shlex.join([sys.executable, "-I", str(fake)])
    logged: list[str] = []
    capture = logging.Handler()
    capture.emit = lambda record: logged.append(record.getMessage())
    LOG.addHandler(capture)
    gateway = AgentGateway(root, fake_command, cgroups, cgroups / "proc-cgroup")
    assert gateway.cgroups == [cgroups / "gateway", cgroups]
    gateway.poll_seconds = 0.2
    gateway.event_idle_seconds = 1.0
    runner = web.AppRunner(create_app(gateway), access_log_class=AccessLogger)
    await runner.setup()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    site = web.SockSite(runner, listener)
    await site.start()
    base_url = f"http://127.0.0.1:{port}"
    external: subprocess.Popen | None = None
    passed: list[str] = []

    try:
        async with ClientSession() as session:
            async def health() -> dict:
                async with session.get(f"{base_url}/healthz") as response:
                    return await response.json()

            async def until(predicate, timeout: float = 15) -> None:
                deadline = time.monotonic() + timeout
                while not await predicate():
                    assert time.monotonic() < deadline, f"timed out: {await health()}"
                    await asyncio.sleep(0.1)

            async def health_is(check) -> bool:
                return check(await health())

            async def ready() -> bool:
                async with session.get(f"{base_url}/readyz") as response:
                    return response.status == 200

            async def post(path: str, data: str = "") -> int:
                async with session.post(f"{base_url}{path}", data=data) as response:
                    return response.status

            async def echo() -> dict:
                async with session.get(f"{base_url}/api/echo") as response:
                    assert response.status == 200, response.status
                    return await response.json()

            async def closed(websocket, code: int) -> str:
                message = await websocket.receive(timeout=5)
                assert message.type == WSMsgType.CLOSE and websocket.close_code == code, (message, websocket.close_code)
                return message.extra

            await until(ready)
            first = await health()
            assert first["service"] == "resolved" and first["child_pid"] == first["service_pid"] is not None, first
            assert first["last_termination"] is None
            passed.append("health and readiness")

            # The client's credentials and Daytona headers stay behind; the service's go up.
            trace_id, span_id = "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7"
            async with session.post(
                URL(f"{base_url}/api/echo?directory=%2Fhome%2Fx&y=two", encoded=True),
                data=b'{"text": "BODY_MARKER"}',
                headers={
                    "Content-Type": "application/json", "Authorization": "Bearer client-secret",
                    "X-Daytona-Preview-Token": "preview-secret", "X-Daytona-Skip-Preview-Warning": "true",
                    "traceparent": f"00-{trace_id}-{span_id}-01",
                },
            ) as response:
                assert response.status == 200 and response.content_type == "application/json"
                echoed = await response.json()
            assert echoed["method"] == "POST" and echoed["body"] == '{"text": "BODY_MARKER"}', echoed
            assert echoed["path"] == "/api/echo?directory=%2Fhome%2Fx&y=two", echoed
            assert echoed["headers"]["traceparent"] == f"00-{trace_id}-{span_id}-01"
            assert echoed["headers"]["authorization"].startswith("Basic ")
            assert not any(name.startswith("x-daytona-") for name in echoed["headers"]), echoed
            async with session.get(f"{base_url}/api/echo", headers={"traceparent": "00-SECRET-not-a-span-01"}) as response:
                assert response.status == 200
            async with session.get(f"{base_url}/api/missing") as response:
                assert response.status == 404 and (await response.json())["error"] == "not found"
            async with session.delete(f"{base_url}/api/echo") as response:
                assert response.status == 204
            records = [json.loads(line.split(" ", 1)[1]) for line in logged if line.startswith("agent_trace_context ")]
            assert len(records) == 2, logged
            valid_record, invalid_record = records
            assert valid_record.keys() == {"method", "path", "status", "trace_id", "span_id", "received_ms", "forwarded_ms"}
            assert (valid_record["method"], valid_record["path"], valid_record["status"]) == ("POST", "/api/echo", 200)
            assert (valid_record["trace_id"], valid_record["span_id"]) == (trace_id, span_id)
            assert valid_record["forwarded_ms"] >= valid_record["received_ms"] > 0
            assert invalid_record["traceparent"] == "invalid" and "trace_id" not in invalid_record
            text = "\n".join(logged)
            for secret in ("SECRET", "client-secret", "preview-secret", "directory", "BODY_MARKER"):
                assert secret not in text, secret
            passed.append("proxy with auth injection, header stripping, and trace log lines")

            # A cached password gone stale (the service restarted) is re-resolved once.
            assert gateway.service is not None
            gateway.service = Service(gateway.service.url, "stale", None)
            assert (await echo())["pid"] == first["child_pid"]
            passed.append("re-resolve after 401")

            async with session.post(f"{base_url}/api/echo", data=b"x" * (MAX_BODY_BYTES + 1)) as response:
                assert response.status == 413, response.status
            passed.append("request body cap")

            # Two clients see the same events, unchanged; one leaving stops nothing.
            connected = '{"type":"server.connected","properties":{}}'
            one = await session.ws_connect(f"{base_url}/events")
            two = await session.ws_connect(f"{base_url}/events")
            for websocket in (one, two):
                assert await websocket.receive_str(timeout=3) == connected
            for event in ('{"type":"session.updated", "data": {"id":"ses_1"}}', '{"type":"x",\n"data":1}'):
                async with session.post(f"{base_url}/api/test/emit", data=event) as response:
                    assert (await response.json())["subscribers"] == 2
                for websocket in (one, two):
                    assert await websocket.receive_str(timeout=3) == event
            await one.send_str("ignored")
            await one.close()
            await until(lambda: health_is(lambda h: h["event_clients"] == 1))
            assert (await health())["child_pid"] == first["child_pid"]
            # The fake notices the dropped upstream only when it next writes, as a real server would.
            assert await post("/api/test/emit", '{"type":"after"}') == 200
            assert await two.receive_str(timeout=3) == '{"type":"after"}'
            passed.append("events to two clients, and a disconnect leaves the service running")

            # The service ends the stream while staying up: a plain close, so the client reconnects.
            assert await post("/api/test/end") == 200
            assert await closed(two, 1012) == "service restarting"
            passed.append("plain close when the stream ends")

            # Heartbeats alone keep a quiet stream open; silence past the idle limit closes it.
            idle = await session.ws_connect(f"{base_url}/events")
            assert await idle.receive_str(timeout=3) == connected
            with contextlib.suppress(TimeoutError):
                message = await idle.receive(timeout=gateway.event_idle_seconds * 2)
                raise AssertionError(f"a heartbeating stream closed: {message}")
            assert await post("/api/test/wedge") == 200
            assert await closed(idle, 1012) == "agent service stalled"
            assert any(line.startswith("agent_events_stalled ") for line in logged), logged
            assert await post("/api/test/unwedge") == 200
            assert (await health())["child_pid"] == first["child_pid"]
            passed.append("event stream read timeout closes a wedged stream with 1012")

            # The service is OOM-killed: the close reason says so, and it comes back.
            three = await session.ws_connect(f"{base_url}/events")
            assert await three.receive_str(timeout=3) == connected
            assert await post("/api/test/die?oom=1") == 502
            reason = json.loads(await closed(three, 1011))
            assert reason == {"reason": "out_of_memory", "signal": "SIGKILL", "exit_status": -9, "limit_bytes": 1073741824}, reason
            await until(lambda: health_is(lambda h: h["child_pid"] not in (None, first["child_pid"]) and h["service"] == "resolved"))
            restarted = await health()
            assert restarted["last_termination"]["detail"].endswith("(1 GiB limit).")
            assert (await echo())["pid"] == restarted["child_pid"]
            passed.append("OOM reported in the close reason, then restart")

            # It exits with a status; its replacement finds a service someone else started
            # meanwhile, exits 0, and the gateway adopts that one.
            hold = root / "hold"
            gateway.agent_command = shlex.join([sys.executable, "-I", str(fake), "--hold", str(hold)])
            four = await session.ws_connect(f"{base_url}/events")
            assert await four.receive_str(timeout=3) == connected
            assert await post("/api/test/die?code=3") == 502
            assert json.loads(await closed(four, 1011)) == {"reason": "exited", "exit_status": 3, "limit_bytes": 1073741824}

            async def child_running() -> bool:
                return gateway.child is not None and gateway.child.process.returncode is None
            await until(child_running)
            external = subprocess.Popen([sys.executable, "-I", str(fake)])

            async def external_registered() -> bool:
                state = read_state(state_file())
                return state is not None and state.pid == external.pid
            await until(external_registered)
            hold.touch()
            await until(lambda: health_is(lambda h: h["child_pid"] is None and h["service_pid"] == external.pid))
            assert any(line.startswith("agent_handoff ") for line in logged), logged
            assert (await echo())["pid"] == external.pid
            passed.append("exit-0 handoff to an externally owned service")

            # A service the gateway does not own dies: nothing to explain, so a plain close,
            # and the gateway starts its own.
            gateway.agent_command = fake_command
            five = await session.ws_connect(f"{base_url}/events")
            assert await five.receive_str(timeout=3) == connected
            external.kill()
            external.wait()
            assert await closed(five, 1012) == "service restarting"
            await until(lambda: health_is(lambda h: h["child_pid"] is not None and h["service_pid"] == h["child_pid"]))
            assert await ready()
            passed.append("external service death closes plainly, then the gateway takes over")

            service = read_state(state_file())
            assert service is not None and service.password not in "\n".join(logged)
    finally:
        LOG.removeHandler(capture)
        await runner.cleanup()
        if external is not None and external.poll() is None:
            external.kill()
            external.wait()
        shutil.rmtree(root, ignore_errors=True)

    passed += await supervision_scenarios()

    # Same acceptance rule as the Worker's parseTraceparent, and only bounded tokens logged.
    good = "00-" + "4bf92f3577b34da6a3ce929d0e0e4736" + "-" + "00f067aa0ba902b7" + "-01"
    traced = lambda value, method="GET", path="/api/x": trace_fields(method, path, value)
    assert traced(good)["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    for bad in (good + "\n", good + "\r", good.upper(), "00-" + "0" * 32 + "-00f067aa0ba902b7-01",
                good[:36] + "0" * 16 + good[52:], "ff" + good[2:], good[:-2] + "0g", 7):
        assert traced(bad)["traceparent"] == "invalid" and "trace_id" not in traced(bad), bad
    secret = "SECRET_SENTINEL_" + "x" * 300
    odd = traced(good, method=secret, path="/api/" + secret)
    assert odd["method"] == "invalid" and odd["path"] == "invalid" and secret not in json.dumps(odd), odd
    assert traced(good, path="/api/a b")["path"] == "invalid"
    assert traced(good, path="/api/session/ses_1/prompt")["path"] == "/api/session/ses_1/prompt"
    passed.append("traceparent and log-field rules")
    print(f"sandbox agent gateway: {len(passed)} scenarios passed")
    for name in passed:
        print(f"  ok  {name}")


async def supervision_scenarios() -> list[str]:
    """Supervision without a working service: a child that never registers, and a registered
    service that stops answering probes while its process lives."""
    root = Path(tempfile.mkdtemp(prefix="agent-gateway-supervise-"))
    os.environ["XDG_STATE_HOME"] = str(root / "state")
    stuck = shlex.join([sys.executable, "-I", "-c", "import time; time.sleep(600)"])
    logged: list[str] = []
    capture = logging.Handler()
    capture.emit = lambda record: logged.append(record.getMessage())
    LOG.addHandler(capture)
    passed: list[str] = []
    holder = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(600)"])
    # Accepts connections into its backlog and never answers: every probe times out.
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(16)

    def gateway() -> AgentGateway:
        result = AgentGateway(root, stuck, root / "no-cgroup", root / "no-proc-cgroup")
        result.poll_seconds, result.probe_seconds, result.startup_seconds = 0.1, 0.2, 0.5
        return result

    async def until(predicate, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            assert time.monotonic() < deadline, logged
            await asyncio.sleep(0.05)

    def register(pid: int) -> None:
        state_file().parent.mkdir(parents=True, exist_ok=True)
        state_file().write_text(json.dumps({
            "url": f"http://127.0.0.1:{silent.getsockname()[1]}", "password": "x", "pid": pid,
        }))

    app = web.Application()
    current = gateway()
    try:
        # Never registers: stopped at the deadline, recorded, retried after backoff.
        await current.start(app)
        await until(lambda: current.child is not None)
        first_pid = current.child.process.pid
        await until(lambda: current.last_termination is not None)
        termination = current.last_termination
        assert termination["reason"] == "killed" and termination["signal"] == "SIGTERM", termination
        assert termination["stalled"] == "startup" and "did not start within" in termination["detail"]
        assert json.loads(termination_reason(termination))["stalled"] == "startup"
        assert any(line.startswith("agent_start_timeout ") for line in logged), logged
        await until(lambda: current.child is not None and current.child.process.pid != first_pid)
        await current.cleanup(app)
        passed.append("startup deadline stops a child that never registers, then retries")

        # A live owner that fails probes keeps its service until enough failures in a row.
        logged.clear()
        register(holder.pid)
        current = gateway()
        await current.start(app)
        await until(lambda: any("failures=2" in line for line in logged))
        assert current.child is None and read_state(state_file()).pid == holder.pid, logged
        await until(lambda: current.child is not None)
        assert sum(line.startswith("agent_probe_failed ") for line in logged) == 2, logged
        await current.cleanup(app)

        # A dead owner is replaced on the first failed probe.
        logged.clear()
        holder.kill()
        holder.wait()
        register(holder.pid)
        current = gateway()
        await current.start(app)
        await until(lambda: current.child is not None)
        assert not any(line.startswith("agent_probe_failed ") for line in logged), logged
        passed.append("a live service survives a failed probe; a dead one is replaced at once")
    finally:
        await current.cleanup(app)
        LOG.removeHandler(capture)
        if holder.poll() is None:
            holder.kill()
            holder.wait()
        silent.close()
        shutil.rmtree(root, ignore_errors=True)
    return passed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subcommands = result.add_subparsers(dest="command")
    serve = subcommands.add_parser("serve", help="Supervise OpenCode and serve the gateway")
    serve.add_argument(
        "--host", default=os.environ.get("DOGWALK_AGENT_GATEWAY_HOST", "0.0.0.0")
    )
    serve.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("DOGWALK_AGENT_GATEWAY_PORT", "8765")),
    )
    serve.add_argument(
        "--workspace",
        type=Path,
        default=Path(os.environ.get("DOGWALK_WORKSPACE", Path.cwd())),
    )
    serve.add_argument(
        "--agent-command",
        default=os.environ.get(
            "DOGWALK_AGENT_COMMAND", "opencode serve --service"
        ),
    )
    subcommands.add_parser("test", help="Run the self-contained integration test")
    return result


def main() -> None:
    arguments = parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if arguments.command == "test":
        asyncio.run(run_test())
        return
    if arguments.command != "serve":
        parser().error("a command is required")
    gateway = AgentGateway(arguments.workspace, arguments.agent_command)
    web.run_app(
        create_app(gateway), host=arguments.host, port=arguments.port, access_log_class=AccessLogger
    )


if __name__ == "__main__":
    main()
