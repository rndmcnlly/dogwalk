# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.11,<4"]
# ///
"""Local text-only Voice Frontend gateway. No production endpoint or audio transport.

Fixture smoke: uv run --script frontend_server.py --fixture-orchestrator --fixture-model
Real model, fake downstream: omit --fixture-model, set OPENROUTER_API_KEY.
Worker dev hook: --orchestrator-url ws://localhost:8787/dev/voice-frontend
requires DOGWALK_ADMIN_PASSWORD or macOS Keychain service dogwalk-admin.
The Worker hook additionally requires ?phone=URL_ENCODED_REGISTERED_NUMBER.

Privacy: .tools/frontend.token and JSONL logs are private (0600). Logs contain
event metadata and model usage, NOT conversation text or credentials. The local
homepage contains a capability link: do not share it or expose it via a proxy.
Recent conversation exists only in process memory and the explicit upstream.
Only one Android connection is accepted per process. No replay/reconnect magic.
Limits: 24 recent history entries / 24k characters; latest context 16k; 32 pending
speech requests. Over-limit canonical instructions or speech requests fail closed.
Playback positions are Android callback estimates, not proof of acoustic hearing.
Costs exclude the Orchestrator/ACP; short-run hourly projections are not measured
connected-hour costs. Idle sockets and heartbeats never call the model.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hmac
import json
import os
import secrets
import subprocess
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

DEFAULT_MODEL = "google/gemini-3.1-flash-lite"
FIXTURE_PROMPT = """LOCAL FIXTURE ONLY, fake downstream. You are a Voice Frontend.
Speak briefly and naturally. You have no session tools or engineering authority.
Handoff requests about coding work or session state to the external Orchestrator.
Never invent session facts. Following context is authoritative. Background context
is unspoken. Phrase speech requests in your own words, preserving their meaning.
Do not greet unless asked. Do not invent an opening or task."""
JSON_MAPPING = """
TEXT ADAPTER: do not emit the custom <|SpawnThinking|> token. Return exactly the
structured JSON decision requested by the schema. action=handoff means raise that
signal, using the actual recent user transcript (the adapter supplies it), not a
fabricated task or engineering brief. action=reply supplies short spoken text;
action=wait supplies empty text. For action=handoff supply empty text: the adapter
uses the fixed acknowledgement "Let me think." rather than model-authored prose.
Never claim that work started, changed, or completed. No tools are available to you.
Speech requests below are requests to phrase speech, not facts to extrapolate.
When speech requests are pending, a reply must cover them, coalescing superseded
progress into the latest authoritative update but preserving distinct questions.
If a new user correction requires a Handoff, prioritize that over pending speech.
Treat prior speech marked interrupted as partial; only completed speech is final.
"""
DECISION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"action": {"type": "string", "enum": ["reply", "handoff", "wait"]},
                   "text": {"type": "string"}}, "required": ["action", "text"],
}


class SafeLog:
    def __init__(self, path: Path | None = None):
        self.file = None
        if path:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            self.file = os.fdopen(fd, "w")
        self.rows = []

    def write(self, event, **metadata):
        row = {"event": event, "at": time.time(), **metadata}
        self.rows.append(row)
        # Bound in-memory diagnostics as well as conversation state.
        self.rows = self.rows[-1000:]
        if self.file:
            self.file.write(json.dumps(row) + "\n")
            self.file.flush()

    def close(self):
        if self.file:
            self.file.close()


class Usage:
    def __init__(self, log):
        self.log = log
        self.started = time.monotonic()
        self.requests = 0
        self.known_cost = 0.0
        self.unknown_cost_requests = 0

    def record(self, model, elapsed, response=None, failed=False):
        self.requests += 1
        usage = (response or {}).get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        output_details = usage.get("completion_tokens_details") or {}
        cost = usage.get("cost")
        source = "provider" if isinstance(cost, (int, float)) else "unknown"
        if source == "unknown" and model == DEFAULT_MODEL:
            inp, out = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if isinstance(inp, int) and isinstance(out, int):
                # Explicit list-price estimate, no undocumented cache discount.
                cost, source = (inp * .25 + out * 1.50) / 1_000_000, "estimated_list_price"
        if source == "unknown":
            self.unknown_cost_requests += 1
            cost = None
        else:
            self.known_cost += cost
        self.log.write("model.usage", model=model, duration_ms=round(elapsed * 1000),
                       input_tokens=usage.get("prompt_tokens"),
                       output_tokens=usage.get("completion_tokens"),
                       cached_tokens=details.get("cached_tokens"),
                       reasoning_tokens=output_details.get("reasoning_tokens"),
                       cost_usd=cost, cost_source=source, failed=failed)

    def summary(self):
        seconds = time.monotonic() - self.started
        return {"requests": self.requests, "connected_seconds": round(seconds, 3),
                    "known_cost_usd": self.known_cost,
                    "unknown_cost_requests": self.unknown_cost_requests,
                    "short_run_hourly_projection_usd": (self.known_cost * 3600 / seconds
                        if seconds and not self.unknown_cost_requests else None),
                    "projection_label": "short-run projection, not measured hourly cost; excludes upstream"}


class OpenRouterModel:
    def __init__(self, http, key, model, usage):
        self.http, self.key, self.model, self.usage = http, key, model, usage

    async def decide(self, messages):
        started, data = time.monotonic(), None
        failed = True
        try:
            async with self.http.post("https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": "Bearer " + self.key},
                json={"model": self.model, "messages": messages, "max_tokens": 220,
                      "provider": {"data_collection": "deny", "zdr": True,
                                   "require_parameters": True},
                      "response_format": {"type": "json_schema", "json_schema": {
                          "name": "frontend_decision", "strict": True,
                          "schema": DECISION_SCHEMA}}},
                timeout=ClientTimeout(total=35)) as response:
                if response.status != 200:
                    # Provider error bodies can contain request text or credentials.
                    raise RuntimeError(f"inference HTTP {response.status}")
                data = await response.json()
            result = json.loads(data["choices"][0]["message"]["content"])
            if (set(result) != {"action", "text"} or
                    result["action"] not in ("reply", "handoff", "wait") or
                    not isinstance(result["text"], str) or len(result["text"]) > 2000):
                raise ValueError("invalid model decision")
            failed = False
            return result
        finally:
            self.usage.record(self.model, time.monotonic() - started, data, failed)


class FixtureModel:
    """Deterministic fake inference, never represented as model integration."""
    async def decide(self, messages):
        state = json.loads(messages[-1]["content"])
        if state["speech_requests"]:
            return {"action": "reply", "text": state["speech_requests"][-1]["text"]}
        return {"action": "handoff", "text": "I'll check."}


class FixtureOrchestrator:
    """Explicit fake downstream, no ACP sessions or tasks."""
    def __init__(self):
        self.sent = []
        self.receive = None

    async def send(self, event):
        self.sent.append(event)
        self.sent = self.sent[-100:]
        if event["type"] == "session.delegation.created":
            await self.receive({"type": "session.thinking.append",
                                "content": "Fixture only. No real Managed Sessions exist."})
            await self.receive({"type": "session.commentary.append",
                                "content": "This is the local fixture. No real session was contacted."})


@dataclass
class Speech:
    text: str
    turn_id: str | None
    requests: tuple[str, ...]
    state: str = "queued"
    start_ms: int | None = None


class Frontend:
    def __init__(self, emit, upstream, model, prompt, log, mode="fixture"):
        if not isinstance(prompt, str) or not prompt or len(prompt) > 64000:
            raise ValueError("canonical frontend instructions missing or too large")
        self.emit, self.upstream, self.model, self.prompt, self.log = emit, upstream, model, prompt, log
        self.mode = mode
        self.history = deque(maxlen=24)
        self.context = ""
        self.additions = ""
        self.pending = deque()
        self.speeches = {}
        self.turn_id = None
        self.pending_user = False
        self.seen_turns = deque(maxlen=128)
        self.speaking = False
        self.closed = False
        self.generating = False
        self.epoch = 0
        self.dirty = asyncio.Event()
        self.started = time.monotonic()
        self.last_offset = 0
        self.task = asyncio.create_task(self.run())

    def offset(self):
        self.last_offset = max(self.last_offset + 1, round((time.monotonic() - self.started) * 1000))
        return self.last_offset

    def remember(self, role, text, **metadata):
        self.history.append({"role": role, "text": text, **metadata})
        while sum(len(item["text"]) for item in self.history) > 24000:
            self.history.popleft()

    def invalidate(self):
        self.epoch += 1
        # Android locally stops queued/active speech on user speech start. Retain
        # records for late terminal acknowledgements, but never call enqueue heard.
        for sid, speech in self.speeches.items():
            if speech.state in ("queued", "started"):
                speech.state = "invalidated"
                self.log.write("speech.invalidated", speech_id=sid,
                               playback_outcome="unknown until Android acknowledgement")

    async def android(self, message):
        kind = message.get("type")
        if kind == "user_speech_started":
            self.speaking = True
            self.invalidate()
        elif kind == "user_speech_ended":
            if message.get("outcome") != "no_transcript":
                raise ValueError("invalid user speech outcome")
            self.speaking = False
            # Release the floor without inventing a user turn. Speech start has
            # already invalidated older generations; only unresolved work wakes.
            if self.pending or self.pending_user:
                self.dirty.set()
        elif kind == "user_transcript":
            text, turn = message.get("text"), message.get("turn_id")
            if not isinstance(text, str) or not text.strip() or len(text) > 8000:
                raise ValueError("invalid user transcript")
            if not isinstance(turn, str) or not turn or len(turn) > 200:
                raise ValueError("invalid turn id")
            if turn in self.seen_turns:
                return
            self.seen_turns.append(turn)
            self.invalidate()
            self.speaking = False
            self.turn_id = turn
            self.pending_user = True
            self.remember("user", text)
            offset = self.offset()
            await self.upstream.send({"type": "session.input_transcript.delta",
                                      "delta": text + "\n", "offset_ms": offset,
                                      "start_ms": offset, "end_ms": offset})
            self.dirty.set()
        elif kind == "speech_started":
            speech = self.speeches.get(message.get("speech_id"))
            if speech and speech.state == "queued":
                speech.state = "started"
                speech.start_ms = self.offset()
                self.log.write("speech.started", speech_id=message["speech_id"])
        elif kind == "speech_finished":
            await self.finish_speech(message)
        elif kind == "session_stop":
            self.closed = True
            self.invalidate()
        else:
            raise ValueError("unsupported Android event")

    async def finish_speech(self, message):
        sid = message.get("speech_id")
        speech = self.speeches.get(sid)
        if not speech or speech.state in ("completed", "interrupted", "error"):
            return
        outcome = message.get("outcome")
        if outcome not in ("completed", "interrupted", "error"):
            raise ValueError("invalid playback outcome")
        chars = message.get("spoken_chars")
        if chars is not None and (type(chars) is not int or chars < 0):
            raise ValueError("invalid playback position")
        speech.state = outcome
        spoken = speech.text if outcome == "completed" else speech.text[:min(chars or 0, len(speech.text))]
        self.log.write("speech." + outcome, speech_id=sid, acknowledged_chars=len(spoken),
                       position_source="Android callback, approximate" if outcome != "completed" else "completion callback")
        if spoken:
            self.remember("assistant", spoken, playback=outcome)
            offset = self.offset()
            await self.upstream.send({"type": "session.output_transcript.delta",
                                      "delta": spoken + "\n", "offset_ms": offset,
                                      "start_ms": speech.start_ms or offset, "end_ms": offset})
        if outcome == "completed":
            self.pending = deque(p for p in self.pending if p["id"] not in speech.requests)
        # Interrupted instructions remain pending, and are reconsidered after the
        # next user transcript. Do not immediately talk over the interruption.
        if outcome == "completed" and self.pending and not self.speaking:
            self.dirty.set()

    async def worker(self, message):
        kind, content = message.get("type"), message.get("content")
        if kind not in ("session.thinking.append", "session.commentary.append", "session.instructions.append"):
            return
        if not isinstance(content, str) or len(content) > 16000:
            raise ValueError("invalid Orchestrator text event")
        if kind == "session.thinking.append":
            self.context = content  # Latest Following/background state wins, unspoken.
        elif kind == "session.instructions.append":
            if len(self.additions) + len(content) > 32000:
                raise ValueError("instruction additions limit reached")
            self.additions += "\n" + content
        else:
            if len(self.pending) >= 32:
                raise ValueError("pending commentary limit reached")
            self.pending.append({"id": uuid.uuid4().hex, "text": content})
        # New authoritative context invalidates in-flight phrasing, not a Prompt Turn.
        self.epoch += 1
        if kind == "session.commentary.append" or self.pending or self.generating:
            self.dirty.set()

    def messages(self, requests):
        return [{"role": "system", "content": self.prompt + "\n" + self.additions + JSON_MAPPING},
                {"role": "user", "content": json.dumps({
                    "recent_transcript": list(self.history),
                    "latest_unspoken_context": self.context,
                    "speech_requests": requests,
                    "new_user_utterance": self.pending_user,
                    "trigger": "user_utterance" if self.pending_user else "speech_request"})}]

    async def run(self):
        while not self.closed:
            await self.dirty.wait()
            self.dirty.clear()
            if self.closed or self.speaking:
                continue
            if any(s.state in ("queued", "started") for s in self.speeches.values()):
                continue
            epoch = self.epoch
            requests = list(self.pending)
            try:
                self.generating = True
                decision = await self.model.decide(self.messages(requests))
                self.generating = False
                if self.closed or epoch != self.epoch or self.speaking:
                    self.log.write("model.stale_discarded")
                    # The event that invalidated the generation owns the next wake.
                    continue
                action, text = decision["action"], decision["text"].strip()
                had_user = self.pending_user
                self.pending_user = False
                if action == "handoff" and had_user:
                    # A Handoff authorizes reasoning, not session changes or a
                    # scheduling promise. Never voice arbitrary model claims here.
                    text = "Let me think."
                    # Transcript has already been sent verbatim. No model-authored
                    # engineering task crosses this boundary.
                    await self.upstream.send({"type": "session.delegation.created",
                        "delegation": {"id": uuid.uuid4().hex, "target": "client"},
                        "offset_ms": self.offset()})
                if text and action != "wait" and epoch == self.epoch:
                    sid = uuid.uuid4().hex
                    speech_turn = self.turn_id if had_user else None
                    self.speeches[sid] = Speech(text, speech_turn,
                        tuple(r["id"] for r in requests) if action == "reply" else ())
                    # Bound terminal bookkeeping without dropping unresolved playback.
                    for old in list(self.speeches):
                        if len(self.speeches) <= 128:
                            break
                        if self.speeches[old].state not in ("queued", "started", "invalidated"):
                            del self.speeches[old]
                    if len(self.speeches) > 256:
                        raise ValueError("unacknowledged playback limit reached")
                    await self.emit({"type": "speak", "speech_id": sid,
                                     "turn_id": speech_turn, "text": text})
                    self.log.write("speech.queued", speech_id=sid, chars=len(text))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001  # Sanitize errors at this privacy boundary.
                self.log.write("frontend.error", error_class=type(exc).__name__)
                await self.emit({"type": "error", "message": "Voice Frontend unavailable; try again"})
            finally:
                self.generating = False

    async def close(self):
        self.closed = True
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task


class WorkerSocket:
    def __init__(self, socket):
        self.socket = socket

    async def send(self, event):
        await self.socket.send_json(event)


def private_token(directory):
    directory.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path = directory / "frontend.token"
    # Refuse symlinks and ensure old files cannot retain loose permissions.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(token)
    return token


def admin_token():
    if os.environ.get("DOGWALK_ADMIN_PASSWORD"):
        return os.environ["DOGWALK_ADMIN_PASSWORD"]
    result = subprocess.run(["security", "find-generic-password", "-s", "dogwalk-admin", "-w"],
                            capture_output=True, text=True, check=False)
    if result.returncode or not result.stdout.strip():
        raise ValueError("upstream admin credential unavailable")
    return result.stdout.strip()


def make_app(args, token, model_factory=None):
    app = web.Application(client_max_size=64000)
    active = False

    async def home(request):
        # No logging of request paths/headers. No token in the page URL.
        host = request.host
        allowed = {f"{bind}:{args.port}" for bind in args.bind}
        if host not in allowed and host != f"localhost:{args.port}":
            raise web.HTTPForbidden()
        endpoint = f"ws://{host}/ws"
        link = "dogwalk-audio://connect?" + urlencode({"endpoint": endpoint, "token": token})
        import html
        return web.Response(text=("<!doctype html><meta name=referrer content=no-referrer>"
            "<title>Local Voice Frontend</title><h1>Local Voice Frontend</h1>"
            f"<p>{'FIXTURE: fake downstream' if args.fixture_orchestrator else 'External dev Orchestrator'}</p>"
            f'<a href="{html.escape(link, quote=True)}">Configure Android (private capability)</a>'
            "<p>From an emulator use ws://10.0.2.2:8768/ws. Blank app endpoint keeps probe mode.</p>"),
            content_type="text/html", headers={"Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'none'"})

    async def websocket(request):
        nonlocal active
        if not hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + token):
            raise web.HTTPUnauthorized()
        if active:
            raise web.HTTPConflict(text="one Android client per gateway")
        active = True
        ws = web.WebSocketResponse(heartbeat=25, max_msg_size=64000)
        frontend, worker_task, upstream_socket = None, None, None
        log = SafeLog(args.tools / f"frontend-{uuid.uuid4().hex}.jsonl")
        usage = Usage(log)
        try:
            await ws.prepare(request)
            async with ClientSession() as http:
                if args.fixture_orchestrator:
                    upstream = FixtureOrchestrator()
                    prompt = FIXTURE_PROMPT
                else:
                    upstream_socket = await asyncio.wait_for(http.ws_connect(args.orchestrator_url,
                        headers={"Authorization": "Basic " + base64.b64encode(
                            ("adam:" + args.admin_token).encode()).decode()}, heartbeat=25,
                        max_msg_size=128000), 15)
                    ready = await asyncio.wait_for(upstream_socket.receive_json(), 15)
                    if ready.get("type") != args.ready_type:
                        raise ValueError("upstream did not send dev-ready")
                    prompt = ready.get(args.instructions_field)
                    upstream = WorkerSocket(upstream_socket)
                model = (model_factory(http, usage) if model_factory else FixtureModel()
                    if args.fixture_model else OpenRouterModel(http, os.environ["OPENROUTER_API_KEY"], args.model, usage))
                frontend = Frontend(ws.send_json, upstream, model, prompt, log,
                                    "fixture" if args.fixture_orchestrator else "live")
                if args.fixture_orchestrator:
                    upstream.receive = frontend.worker
                else:
                    async def read_worker():
                        try:
                            async for event in upstream_socket:
                                if event.type == WSMsgType.TEXT:
                                    await frontend.worker(json.loads(event.data))
                        finally:
                            # Fail closed on loss of authoritative downstream.
                            await ws.close(code=1011, message=b"Orchestrator disconnected")
                    worker_task = asyncio.create_task(read_worker())
                await ws.send_json({"type": "ready", "protocol": 1,
                    "session_id": uuid.uuid4().hex, "mode": frontend.mode,
                    "frontend_model": "fixture/fake-inference" if args.fixture_model else args.model})
                async for event in ws:
                    if event.type == WSMsgType.TEXT:
                        await frontend.android(json.loads(event.data))
                        if frontend.closed:
                            break
                    elif event.type == WSMsgType.ERROR:
                        break
                    elif event.type == WSMsgType.BINARY:
                        await ws.close(code=1003, message=b"Text/control only; no audio")
                        break
        except Exception as exc:  # noqa: BLE001  # Credential-bearing transport errors must be sanitized.
            log.write("connection.error", error_class=type(exc).__name__)
            if ws.prepared and not ws.closed:
                await ws.send_json({"type": "error", "message": "Voice Frontend unavailable; try again"})
        finally:
            if frontend:
                await frontend.close()
            if worker_task:
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await worker_task
            if upstream_socket:
                await upstream_socket.close()
            await ws.close()
            log.write("usage.summary", **usage.summary())
            log.close()
            active = False
        return ws

    app.router.add_get("/", home)
    app.router.add_get("/ws", websocket)
    return app


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", action="append", choices=["127.0.0.1", "100.85.3.93"])
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--fixture-orchestrator", action="store_true")
    parser.add_argument("--fixture-model", action="store_true")
    parser.add_argument("--orchestrator-url")
    parser.add_argument("--allow-remote-dev-upstream", action="store_true",
                        help="explicit permission for non-loopback dev Worker, never production default")
    parser.add_argument("--model", default=os.environ.get("FRONTEND_MODEL", DEFAULT_MODEL))
    parser.add_argument("--ready-type", default="dev.frontend.ready")
    parser.add_argument("--instructions-field", default="instructions")
    parser.add_argument("--tools", type=Path, default=Path(__file__).resolve().parent / ".tools")
    args = parser.parse_args(argv)
    args.bind = args.bind or ["127.0.0.1"]
    if args.fixture_orchestrator == bool(args.orchestrator_url):
        parser.error("choose explicit --fixture-orchestrator or --orchestrator-url; no default upstream")
    if args.fixture_model and not args.fixture_orchestrator:
        parser.error("fake model only allowed with fake downstream")
    if args.orchestrator_url:
        parsed = urlparse(args.orchestrator_url)
        if (parsed.scheme not in ("ws", "wss") or parsed.username or parsed.password
                or parsed.fragment or set(parse_qs(parsed.query)) - {"phone"}):
            parser.error("upstream must be ws(s), no embedded credentials, only phone query allowed")
        if (parsed.hostname not in ("localhost", "127.0.0.1", "::1") and
                (not args.allow_remote_dev_upstream or parsed.scheme != "wss")):
            parser.error("non-loopback upstream requires wss and --allow-remote-dev-upstream")
    if not args.fixture_model and not os.environ.get("OPENROUTER_API_KEY"):
        parser.error("OPENROUTER_API_KEY required (or explicit --fixture-model)")
    return args


def main():
    args = parse_args()
    args.admin_token = admin_token() if args.orchestrator_url else None
    token = private_token(args.tools)
    print("READY configuration: FIXTURE fake downstream" if args.fixture_orchestrator
          else "READY configuration: external dev Orchestrator (not yet connected)")
    print("Private client token: .tools/frontend.token; private configuration link at local homepage")
    web.run_app(make_app(args, token), host=args.bind, port=args.port,
                access_log=None, print=lambda _: None)


if __name__ == "__main__":
    main()
