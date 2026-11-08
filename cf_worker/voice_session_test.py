#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets==15.0.1"]
# ///
"""End-to-end local test of GPT-Live client delegation, the Session Orchestrator, and ACP."""

import asyncio
import base64
import hashlib
import hmac
import json
import math
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from http.server import BaseHTTPRequestHandler

from smoke_test import (
    DAYTONA_MOCK_PORT,
    DaytonaMock,
    LOCAL_ADMIN_PASSWORD,
    LOCAL_TOKEN,
    TEST_PHONE,
    ThreadingHTTPServer,
    prepare_local_fixture,
    project_wrangler,
    wait_port,
)

WORKER_DIR = Path(__file__).resolve().parent
SOCKET_PORT = 8791
ORCHESTRATOR_MOCK_PORT = 8792
IDENTITY_SECRET = f"local-identity-secret-{secrets.token_hex(8)}"
OPENAI_WEBHOOK_KEY = secrets.token_bytes(32)
OPENAI_WEBHOOK_SECRET = "whsec_" + base64.b64encode(OPENAI_WEBHOOK_KEY).decode()


class SocketMocks:
    def __init__(self) -> None:
        self.live = None
        self.acp_messages: asyncio.Queue[dict] = asyncio.Queue()
        self.live_messages: asyncio.Queue[dict] = asyncio.Queue()
        self.acp_connected = asyncio.Event()
        self.live_connected = asyncio.Event()
        self.seen: list[dict] = []
        self.acp_seen: list[dict] = []
        self.acp = None
        self.acp_connections = 0
        # The Agent answers initialize late, so the call starts before the dogs are ready.
        self.init_delay = 1.5
        self.advertise_resume = False
        # Like OpenCode, the Agent reloads a session only from the directory it belongs to.
        self.session_cwds = {
            PERSISTED_SESSION_ID: "/home/daytona/parser",
            "ses_elsewhere0outside": "/tmp/elsewhere",
        }

    def session_entries(self) -> list[dict]:
        return [
            {
                "sessionId": session_id,
                "cwd": cwd,
                "title": PERSISTED_TITLE if session_id == PERSISTED_SESSION_ID else "Other work",
                "updatedAt": "2026-11-06T18:30:00Z",
            }
            for session_id, cwd in self.session_cwds.items()
        ]

    async def handler(self, socket) -> None:
        if socket.request.path == "/acp":
            self.acp = socket
            self.acp_connections += 1
            self.acp_connected.set()
            async for raw in socket:
                message = json.loads(raw)
                self.acp_seen.append(message)
                await self.acp_messages.put(message)
                request_id = message.get("id")
                method = message.get("method")
                if request_id is None:
                    continue
                params = message.get("params") or {}
                error = None
                if method == "initialize":
                    await asyncio.sleep(self.init_delay)
                    capabilities = {"sessionCapabilities": {"resume": {}}} if self.advertise_resume else {}
                    result = {"protocolVersion": 1, "agentCapabilities": capabilities}
                elif method == "session/new":
                    cwd = params["cwd"]
                    session_id = "acp-session-test" if cwd == "/home/daytona" else f"acp-session-{Path(cwd).name}"
                    self.session_cwds[session_id] = cwd
                    result = {"sessionId": session_id}
                elif method in ("session/load", "session/resume"):
                    result = {}
                    expected = self.session_cwds.get(params["sessionId"], params["cwd"])
                    if params["cwd"] != expected:
                        error = {"code": -32603, "message": "persisted session belongs to another directory"}
                elif method == "session/list":
                    # Two entries per page, so discovery must follow nextCursor.
                    start = int(params.get("cursor") or 0)
                    entries = self.session_entries()
                    result = {"sessions": entries[start:start + 2]}
                    if start + 2 < len(entries):
                        result["nextCursor"] = str(start + 2)
                elif method == "session/prompt":
                    await asyncio.sleep(0.2)
                    for update in PROMPT_UPDATES:
                        await socket.send(json.dumps({
                            "jsonrpc": "2.0",
                            "method": "session/update",
                            "params": {"sessionId": "acp-session-test", "update": update},
                        }))
                    result = {"stopReason": "end_turn"}
                else:
                    result = {}
                outcome = {"error": error} if error else {"result": result}
                await socket.send(
                    json.dumps({"jsonrpc": "2.0", "id": request_id, **outcome})
                )
            return

        path = socket.request.path
        if path.startswith("/live/sessions/") and path.endswith("/attach"):
            self.live = socket
            self.live_connected.set()
            async for raw in socket:
                message = json.loads(raw)
                self.seen.append(message)
                await self.live_messages.put(message)
                # GPT-Live acknowledges each output fixture once its mixer drains.
                if message.get("type") == "session.output_audio.fixture.play":
                    asyncio.create_task(self.ack_fixture(socket))
            return

        await socket.close(1008, "unknown mock path")

    async def ack_fixture(self, socket) -> None:
        await asyncio.sleep(0.05)
        await socket.send(json.dumps({"type": "session.output_audio.fixture.played"}))

    async def send_live(self, message: dict) -> None:
        assert self.live is not None
        await self.live.send(json.dumps(message))


def message_chunk(text: str) -> dict:
    return {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}


# One Prompt Turn's traffic, shaped like OpenCode's: a plan, a read whose locations arrive
# with in_progress, the Agent narrating between tool calls, then the first edit.
PROMPT_UPDATES = [
    {"sessionUpdate": "plan", "entries": [
        {"content": "Find the parser", "priority": "high", "status": "in_progress"},
        {"content": "Write the artifact", "priority": "medium", "status": "pending"},
    ]},
    {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "read", "kind": "read", "status": "pending"},
    {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "in_progress",
     "locations": [{"path": "/home/daytona/src/parser.py"}]},
    {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"},
    message_chunk("The parser lives in **src**. "),
    {"sessionUpdate": "tool_call", "toolCallId": "t2", "title": "write", "kind": "edit", "status": "pending"},
    {"sessionUpdate": "tool_call_update", "toolCallId": "t2", "status": "in_progress",
     "locations": [{"path": "/home/daytona/artifact.txt"}]},
    {"sessionUpdate": "tool_call_update", "toolCallId": "t2", "status": "completed"},
    message_chunk("Bridge report complete."),
]

CALL_BRIEF = "Call started with no Managed Sessions."
READY_BRIEF = "The dogs are awake."
KEYPAD_SAY = "Seven noted."
NEXT_GREETING = "Last time Juniper built a tiny verified artifact."
FIRST_OPENING = (
    "Caller ID unverified. Dogwalk. Welcome! I'm your walker; your sandbox is "
    "being set up, which takes a minute or two."
)
RETURN_OPENING = (
    f"Verified. Dogwalk. {NEXT_GREETING} Since then, Juniper asked for permission. Where to?"
)
WORK_BRIEF = "Starting Juniper on a tiny verified artifact."
AGENT_PROMPT = (
    "Goal: create a tiny artifact and verify it. Finish with a short summary "
    "of what changed and how it was verified."
)
STARTED_SAY = "Juniper is on it."
FINISHED_SAY = "Juniper finished; it reports the artifact is verified."
REPORTED_SAY = "Juniper says the artifact is verified."
FAREWELL_SAY = "Talk soon."
PERSISTED_SESSION_ID = "ses_earlier0parser"
PERSISTED_TITLE = "Earlier parser work"
LEASH_SAY = "Rowan has gone home; Juniper is still with us."


def call(call_id: str, name: str, **arguments) -> dict:
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments),
    }


def created_alias(context: list[dict]) -> str:
    output = next(
        item for item in context
        if item.get("type") == "function_call_output" and item["call_id"] == "h1-create"
    )
    return json.loads(output["output"])["session"]["alias"]


# Each step fires once its trigger appears in a user message, in order, after the
# previous step's outputs are back. Steps that answer a handoff use its delegation id.
ORCHESTRATOR_SCRIPT = [
    # Session tools fail fast while the Sandbox is still starting.
    ("call_started", lambda ctx: [
        call("cs-brief", "brief", text=CALL_BRIEF, delegation_id=None),
        call("cs-create", "create_managed_session", alias="Early", directory="."),
        call("cs-list", "list_managed_sessions"),
    ]),
    ("[harness event] dogs_ready", lambda ctx: [call("dr-brief", "brief", text=READY_BRIEF, delegation_id=None)]),
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
    ("[handoff del-3]", lambda ctx: [call("h3-say", "say", text=LEASH_SAY, delegation_id="del-3")]),
    ("[handoff del-4]", lambda ctx: [
        call("h4-say", "say", text=FAREWELL_SAY, delegation_id="del-4"),
        call("h4-end", "end_call"),
    ]),
    # Second call: digits without a system meaning still reach the Orchestrator.
    ('"key":"7"', lambda ctx: [call("k7-say", "say", text=KEYPAD_SAY, delegation_id=None)]),
    # Preferences and pairing are voice operations; keypad digits stand in for asking here.
    ('"key":"6"', lambda ctx: [call("k6-style", "set_earcon_style", style="tonal")]),
    ('"key":"8"', lambda ctx: [call("k8-pair", "pair_browser")]),
]
STEP_IDS = [
    "cs-brief", "cs-create", "cs-list", "dr-brief", "h1-brief", "h1-create", "h1-begin", "h1-say-url", "h1-say",
    "ts-say", "h2-inspect", "h2-unfollow", "h2-say",
    "h3-discover", "h3-load", "h3-retag", "h3-close", "h3-list",
    "h3-projects", "h3-dozer", "h3-ghost", "h3-escape", "h3-say",
    "h4-say", "h4-end", "k7-say", "k6-style", "k8-pair",
]


class OrchestratorMock(BaseHTTPRequestHandler):
    """A scripted /v1/responses endpoint standing in for the Session Orchestrator model."""

    requests: list[tuple[dict[str, str], dict]] = []
    greeting_requests: list[dict] = []
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
        self.respond({"output": output, "usage": {"input_tokens": 1, "output_tokens": 1}})

    def respond(self, value: dict) -> None:
        body = json.dumps(value).encode()
        self.send_response(200)
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
INSERT INTO registrations (phone_number, invite_code, registered_at, last_seen_at)
VALUES ('{TEST_PHONE}', 'ahead almighty apple', unixepoch(), unixepoch());
INSERT INTO sandbox_assignments
  (phone_number, provider_id, identity_hash, state, provisioning_started_at, last_checked_at)
VALUES ('{TEST_PHONE}', 'mock-sandbox-1', '{identity}', 'started', unixepoch(), unixepoch());
INSERT INTO voice_calls (call_sid, phone_number, status, started_at, last_activity_at)
VALUES ('CA_VOICE_TEST', '{TEST_PHONE}', 'in-progress', unixepoch(), unixepoch()),
       ('CA_VOICE_TEST2', '{TEST_PHONE}', 'in-progress', unixepoch(), unixepoch());
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
        "http://127.0.0.1:8787/openai/live",
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


def call_activity(call_sid: str) -> list[str]:
    credentials = base64.b64encode(f"adam:{LOCAL_ADMIN_PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        "http://127.0.0.1:8787/admin/api/calls?verbose=1&limit=10",
        headers={"authorization": f"Basic {credentials}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        calls = json.load(response)
    return [item["event"] for call in calls if call["call_sid"] == call_sid for item in call["activity"]]


def latest_user_text() -> str:
    if not OrchestratorMock.requests:
        return ""
    return "\n".join(
        str(item["content"]) for item in OrchestratorMock.requests[-1][1]["input"] if item.get("role") == "user"
    )


# --- Paired Browser helpers ----------------------------------------------------

CHROME_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
DTMF_ROWS = [697, 770, 852, 941]
DTMF_COLS = [1209, 1336, 1477, 1633]
DTMF_KEYPAD = ["123A", "456B", "789C", "*0#D"]
PAIRING_ALPHABET = "0123456789ABCD*#"
REPORT_TEXT = "# Parser report\n\nAll green.\n"


def decode_pairing_tones(audio_b64: str) -> str | None:
    """Read the Pairing Code from a played fixture, as a browser would; None if it is not tones.

    The fixture is exactly clocked (24 kHz, 0.3 s lead, 62.5 ms symbols), so a Goertzel
    window in the middle of each slot suffices; real browsers need the symbol clock and ECC.
    """
    raw = base64.b64decode(audio_b64)
    samples = [int.from_bytes(raw[i:i + 2], "little", signed=True) / 32768 for i in range(0, len(raw) - 1, 2)]
    lead, slot, window = 7200, 1500, 1000
    if len(samples) < lead + 32 * slot:
        return None
    symbols = []
    for index in range(32):
        x = samples[lead + index * slot + 250: lead + index * slot + 250 + window]
        energy = sum(v * v for v in x) / window
        powers = []
        for frequency in DTMF_ROWS + DTMF_COLS:
            c = 2 * math.cos(2 * math.pi * frequency / 24000)
            s1 = s2 = 0.0
            for v in x:
                s1, s2 = v + c * s1 - s2, s1
            powers.append(2 * (s1 * s1 + s2 * s2 - c * s1 * s2) / (window * window))
        row = max(range(4), key=lambda i: powers[i])
        col = max(range(4), key=lambda i: powers[4 + i])
        if not energy or powers[row] + powers[4 + col] < 0.8 * energy:
            return None
        symbols.append(DTMF_KEYPAD[row][col])
    nibbles = [PAIRING_ALPHABET.index(symbol) for symbol in symbols[:20]]
    return bytes(nibbles[i] << 4 | nibbles[i + 1] for i in range(0, 20, 2)).hex()


def pair_http(method: str, path: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"http://127.0.0.1:8787{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


async def recv_type(socket, kind: str, timeout: float = 8) -> dict:
    """The next message of a kind, skipping the status pushes that may arrive in between."""
    async with asyncio.timeout(timeout):
        while True:
            message = json.loads(await socket.recv())
            if message.get("type") == kind:
                return message


def sandbox_files(path: str):
    """Toolbox file reads: home holds a report; anything else does not exist."""
    prefix = "/toolbox/mock-sandbox-1/files"
    if path == f"{prefix}?path=%2Fhome%2Fdaytona":
        listing = [{"name": "REPORT.md", "isDir": False, "size": len(REPORT_TEXT), "modTime": "2026-11-08T10:00:00Z"},
                   {"name": "parser", "isDir": True, "size": 4096, "modTime": "2026-11-08T09:00:00Z"}]
        return 200, json.dumps(listing).encode(), "application/json"
    if path == f"{prefix}/info?path=%2Fhome%2Fdaytona%2FREPORT.md":
        return 200, json.dumps({"name": "REPORT.md", "isDir": False, "size": len(REPORT_TEXT)}).encode(), "application/json"
    if path == f"{prefix}/download?path=%2Fhome%2Fdaytona%2FREPORT.md":
        return 200, REPORT_TEXT.encode(), "application/octet-stream"
    return None


def dtmf(key: str) -> dict:
    return {"type": "transport.dtmf.received", "event": key}


def of_type(kind: str):
    return lambda item: item.get("type") == kind


async def wait_for(condition, timeout: float = 8) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.05)


async def run_protocol(identity: str) -> None:
    mocks = SocketMocks()
    async with serve(mocks.handler, "127.0.0.1", SOCKET_PORT):
        body, webhook_identity, signature = signed_webhook("CA_VOICE_TEST", "live-test")
        assert await asyncio.to_thread(post_webhook, body, webhook_identity, "v1,invalid") == 400
        assert not DaytonaMock.openai_requests
        assert await asyncio.to_thread(post_webhook, body, webhook_identity, signature) == 204
        await asyncio.wait_for(mocks.acp_connected.wait(), 8)
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
        # The split-brain fiction: the voice defers to its head and names no backend
        # except in GPT-Live's delegation-policy label and the one sentence forbidding it.
        assert "Your head (a working fiction" in frontend
        assert "never need to mention a backend" in frontend
        assert "backend" not in (
            frontend.replace("Backend tools:", "").replace("never need to mention a backend", "").lower()
        )

        # Talk first: the opening is spoken before the Agent has even initialized.
        greeting = await next_matching(mocks.live_messages, of_type("session.commentary.append"))
        assert greeting["content"] == FIRST_OPENING and greeting["delegation_id"] is None
        await next_matching(mocks.acp_messages, lambda item: item.get("method") == "initialize")
        call_brief = await next_matching(mocks.live_messages, appended("thinking", CALL_BRIEF))
        assert call_brief["delegation_id"] is None
        started_event = OrchestratorMock.user_message("call_started")
        assert '"first_call":true' in started_event and FIRST_OPENING in started_event
        assert '"caller_id":null' in started_event
        assert "dogs_waking" in started_event
        await next_matching(mocks.live_messages, appended("thinking", READY_BRIEF))
        early = OrchestratorMock.tool_output("cs-create")
        assert early["ok"] is False and "still starting" in early["error"], early
        assert OrchestratorMock.tool_output("cs-list") == []
        assert '"managed_session_count":0' in OrchestratorMock.user_message("[harness event] dogs_ready")

        # Handoff 1: the Voice Frontend delegates; the task is the transcript.
        await mocks.send_live(transcript("input", "Start a session called Juniper ", 0, 900))
        await mocks.send_live(transcript("input", "to build a tiny verified artifact.", 900, 2100))
        await mocks.send_live(transcript("output", "On it.", 2200, 2600))
        await mocks.send_live(delegation("del-1", 2150))
        work_brief = await next_matching(mocks.live_messages, appended("thinking", WORK_BRIEF))
        assert work_brief["delegation_id"] == "del-1"
        created = await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/new"
        )
        assert created["params"] == {"cwd": "/home/daytona", "mcpServers": []}
        prompt_request = await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/prompt"
        )
        assert prompt_request["params"]["sessionId"] == "acp-session-test"
        assert prompt_request["params"]["prompt"] == [{"type": "text", "text": AGENT_PROMPT}]
        started = await next_matching(mocks.live_messages, appended("commentary", STARTED_SAY))
        assert started["delegation_id"] == "del-1"
        finished = await next_matching(mocks.live_messages, appended("commentary", FINISHED_SAY))
        assert finished["delegation_id"] is None
        handoff = OrchestratorMock.user_message("[handoff del-1]")
        assert "Caller: Start a session called Juniper to build a tiny verified artifact." in handoff
        assert "Voice: On it." in handoff
        stopped = OrchestratorMock.user_message("turn_stopped")
        assert '"stop_reason":"end_turn"' in stopped and "Bridge report complete." in stopped
        rejected = OrchestratorMock.tool_output("h1-say-url")
        assert rejected["ok"] is False and "URL" in rejected["error"]
        juniper = OrchestratorMock.tool_output("h1-create")["session"]
        assert juniper["alias"] == "Juniper" and juniper["directory"] == "."
        begun = OrchestratorMock.tool_output("h1-begin")
        assert begun["turn_state"] == "in_progress" and begun["following"] == "Juniper", begun
        assert '"followed":true' in stopped

        # Sending Juniper off followed it: progress beats reach the Voice Frontend as
        # thinking, built from ACP updates without a model call.
        # Beats a few seconds apart are coalesced into one append.
        def beats() -> str:
            return "\n".join(
                m["content"] for m in mocks.seen
                if m.get("type") == "session.thinking.append" and m["content"].startswith("Following Juniper:")
            )

        expected_beats = [
            "Juniper is working", "step 1 of 2: Find the parser.", "Juniper is reading parser.py.",
            "Juniper says: The parser lives in src.", "started changing files: editing artifact.txt.",
        ]
        await wait_for(lambda: all(fragment in beats() for fragment in expected_beats))

        # Handoff 2: a follow-up question answered from session inspection.
        await mocks.send_live(transcript("input", "What did it report?", 5000, 6200))
        await mocks.send_live(delegation("del-2", 6250))
        reported = await next_matching(mocks.live_messages, appended("commentary", REPORTED_SAY))
        assert reported["delegation_id"] == "del-2"
        handoff = OrchestratorMock.user_message("[handoff del-2]")
        assert "Caller: What did it report?" in handoff and "Juniper" not in handoff
        inspected = OrchestratorMock.tool_output("h2-inspect")
        assert inspected["alias"] == "Juniper" and inspected["stop_reason"] == "end_turn"
        assert inspected["followed"] is True and inspected["remark"] == "The parser lives in src."
        assert [(c["kind"], c["status"], c["paths"]) for c in inspected["recent_tool_calls"]] == [
            ("read", "completed", ["src/parser.py"]), ("edit", "completed", ["artifact.txt"]),
        ], inspected
        assert OrchestratorMock.tool_output("h2-unfollow") == {"ok": True, "stopped_following": "Juniper"}
        events = await asyncio.to_thread(call_activity, "CA_VOICE_TEST")
        for expected in ("session.followed", "session.beat", "session.unfollowed"):
            assert expected in events, (expected, events)

        # Handoff 3: leash earlier work, retag it, let it go home, and list what remains.
        await mocks.send_live(transcript("input", "Pick up that parser work from yesterday ", 7000, 8000))
        await mocks.send_live(transcript("input", "as Birch, call it Rowan, then let it go.", 8000, 8800))
        await mocks.send_live(delegation("del-3", 8850))
        # Discovery lists every directory (following nextCursor) and resumes from the
        # session's own directory.
        listed = await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/list"
        )
        assert listed["params"] == {}
        listed = await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/list"
        )
        assert listed["params"] == {"cursor": "2"}
        resumed = await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/resume"
        )
        assert resumed["params"] == {
            "sessionId": PERSISTED_SESSION_ID, "cwd": "/home/daytona/parser", "mcpServers": [],
        }
        closed = await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/close"
        )
        assert closed["params"] == {"sessionId": PERSISTED_SESSION_ID}
        leashed = await next_matching(mocks.live_messages, appended("commentary", LEASH_SAY))
        assert leashed["delegation_id"] == "del-3"
        discovered = OrchestratorMock.tool_output("h3-discover")
        assert discovered["ok"] is True
        assert discovered["sessions"] == [
            {"ref": "r1", "title": PERSISTED_TITLE, "updated_at": "2026-11-06T18:30:00Z", "directory": "parser"}
        ]
        discovered_raw = json.dumps(discovered)
        assert "sessionId" not in discovered_raw and "ses_" not in discovered_raw
        assert "acp-session-test" not in discovered_raw
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
        news = [m["params"]["cwd"] for m in mocks.acp_seen if m.get("method") == "session/new"]
        assert news == ["/home/daytona", "/home/daytona/honeydozer"], news
        ghost = OrchestratorMock.tool_output("h3-ghost")
        assert ghost["ok"] is False and "does not exist" in ghost["error"], ghost
        escape = OrchestratorMock.tool_output("h3-escape")
        assert escape["ok"] is False and "inside the Workspace" in escape["error"], escape
        assert any("test -d" in command and "honeydozer" in command for command in DaytonaMock.executed)

        # Handoff 4: farewell and end_call in one response; hangup follows the farewell.
        await mocks.send_live(transcript("input", "Thanks, hang up now.", 9000, 10000))
        await mocks.send_live(delegation("del-4", 10050))
        farewell = await next_matching(mocks.live_messages, appended("commentary", FAREWELL_SAY))
        assert farewell["delegation_id"] == "del-4"
        await wait_for(lambda: any(path == "/live/sessions/live-test/hangup" for path, _ in DaytonaMock.openai_requests), 10)
        assert OrchestratorMock.tool_output("h4-end")["status"] == "ending call"

        # Between calls: the provider closes the sideband, which ends the voice session and
        # writes the next welcome; the Agent asks for permission while nobody is on the line;
        # then the Agent Connection drops.
        await mocks.live.close()
        await wait_for(lambda: OrchestratorMock.greeting_requests, 10)
        greeting_request = OrchestratorMock.greeting_requests[-1]
        assert greeting_request["tools"] and greeting_request["model"] == "gpt-6.1-sol"
        assert "call_ended" in json.dumps(greeting_request["input"])
        await mocks.acp.send(json.dumps({
            "jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission",
            "params": {
                "sessionId": "acp-session-test",
                "toolCall": {"title": "Run the test suite"},
                "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"}],
            },
        }))
        await asyncio.sleep(0.5)
        # As in production, the Agent has since associated Juniper with a project directory,
        # so resuming from the recorded home directory fails until Dogwalk repairs it.
        mocks.session_cwds["acp-session-test"] = "/home/daytona/honeydozer"
        await mocks.acp.close()
        await asyncio.sleep(0.3)

        # Second call: a returning caller with verified caller ID. The Agent now advertises
        # resume, so the reconnect restores Juniper without replaying its history.
        mocks.advertise_resume = True
        mocks.live_connected.clear()
        body, webhook_identity, signature = signed_webhook("CA_VOICE_TEST2", "live-test-2")
        assert await asyncio.to_thread(post_webhook, body, webhook_identity, signature) == 204
        await asyncio.wait_for(mocks.live_connected.wait(), 8)
        opening = await next_matching(mocks.live_messages, appended("commentary", RETURN_OPENING))
        assert opening["delegation_id"] is None
        def resume_of(session_id: str):
            return lambda item: item.get("method") == "session/resume" and item["params"]["sessionId"] == session_id

        restored = await next_matching(mocks.acp_messages, resume_of("acp-session-test"))
        assert restored["params"] == {"sessionId": "acp-session-test", "cwd": "/home/daytona", "mcpServers": []}
        await next_matching(mocks.acp_messages, lambda item: item.get("method") == "session/list")
        repaired = await next_matching(mocks.acp_messages, resume_of("acp-session-test"))
        assert repaired["params"]["cwd"] == "/home/daytona/honeydozer", repaired
        dozer_restored = await next_matching(mocks.acp_messages, resume_of("acp-session-honeydozer"))
        assert dozer_restored["params"] == {
            "sessionId": "acp-session-honeydozer", "cwd": "/home/daytona/honeydozer", "mcpServers": [],
        }
        assert not any(m.get("method") == "session/load" for m in mocks.acp_seen)
        assert mocks.acp_connections == 2 and DaytonaMock.preview_reads == 1, (
            "the ACP preview URL was not reused", mocks.acp_connections, DaytonaMock.preview_reads,
        )
        await wait_for(lambda: latest_user_text().count("[harness event] dogs_ready") >= 2, 10)
        second_start = next(
            str(item["content"]) for item in OrchestratorMock.requests[-1][1]["input"]
            if item.get("role") == "user" and RETURN_OPENING in str(item["content"])
        )
        assert "first_call" not in second_start and "[harness event] dogs_waking" in second_start
        assert '"stir_verstat":"TN-Validation-Passed-A"' in second_start

        # Keypad conventions, all handled without the Orchestrator.
        await mocks.send_live(dtmf("1"))
        status = await next_matching(mocks.live_messages, lambda item: item.get("type") == "session.commentary.append" and "Juniper" in item.get("content", ""))
        assert status["content"].startswith("Juniper is resting") and status["delegation_id"] is None, status
        await mocks.send_live(dtmf("#"))
        await next_matching(mocks.live_messages, of_type("session.output_audio.fixture.play"))
        stop = await next_matching(mocks.live_messages, of_type("session.instructions.append"))
        assert "pressed pound" in stop["content"]
        await mocks.send_live(transcript("output", "The parser handles nested quotes now.", 20000, 22000))
        await mocks.send_live(dtmf("0"))
        repeat = await next_matching(mocks.live_messages, lambda item: item.get("type") == "session.commentary.append" and "word for word" in item.get("content", ""))
        assert "The parser handles nested quotes now." in repeat["content"]

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

        # Sound style is a voice tool: it stores the preference and previews the opening sound.
        await mocks.send_live(dtmf("6"))
        await next_matching(mocks.live_messages, of_type("session.output_audio.fixture.play"), 15)
        await wait_for(lambda: "k6-style" in {item.get("call_id") for _, payload in OrchestratorMock.requests for item in payload["input"] if item.get("type") == "function_call_output"}, 10)

        # Pairing a browser (pair_browser, asked for by voice): the tones carry a Pairing Code, a
        # browser claims it, the Voice Frontend names the browser, and a press of one accepts.
        # Then the browser reads status and files.
        await mocks.send_live(dtmf("8"))
        code = None
        async with asyncio.timeout(20):
            while code is None:
                played = await next_matching(mocks.live_messages, of_type("session.output_audio.fixture.play"))
                code = decode_pairing_tones(played["audio"])
        status, claim = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": code}, {"user-agent": CHROME_MAC})
        assert status == 200 and claim["ticket"].startswith(identity + "."), (status, claim)
        named = await next_matching(mocks.live_messages, lambda m: m.get("type") == "session.commentary.append" and "pairing tones" in m.get("content", ""))
        assert "Chrome on a Mac" in named["content"] and "press one" in named["content"]
        status, _ = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": code})
        assert status == 404, "a Pairing Code works once"
        ticket = {"authorization": f"Bearer {claim['ticket']}"}
        assert (await asyncio.to_thread(pair_http, "GET", "/pair/claim/status", None, ticket))[1] == {"state": "waiting"}
        # Keys count again only once echoes of the tones have cleared (half a second).
        await asyncio.sleep(0.6)
        await mocks.send_live(dtmf("1"))
        await next_matching(mocks.live_messages, lambda m: m.get("type") == "session.commentary.append" and "browser is paired" in m.get("content", ""))
        accepted = (await asyncio.to_thread(pair_http, "GET", "/pair/claim/status", None, ticket))[1]
        assert accepted["state"] == "accepted" and accepted["token"].startswith(identity + ".")
        assert (await asyncio.to_thread(pair_http, "GET", "/pair/claim/status", None, ticket))[1] == {"state": "accepted"}, "the token is handed out once"
        token = accepted["token"]
        async with connect(f"ws://127.0.0.1:8787/pair/ws?for={identity}") as browser:
            await browser.send(json.dumps({"type": "auth", "token": token}))
            hello = await recv_type(browser, "hello")
            assert hello["device"] == "Chrome on a Mac" and hello["idle_expires_at"] > time.time() * 1000
            snapshot = await recv_type(browser, "status")
            assert any(item["alias"] == "Juniper" for item in snapshot["sessions"]) and snapshot["sandbox"]["call_attached"]
            await browser.send(json.dumps({"type": "list", "id": 1, "path": ""}))
            listing = await recv_type(browser, "list")
            assert listing["id"] == 1 and listing["path"] == "/home/daytona"
            assert [entry["name"] for entry in listing["entries"]] == ["parser", "REPORT.md"], "directories first"
            await browser.send(json.dumps({"type": "read", "id": 2, "path": "REPORT.md"}))
            report = await recv_type(browser, "file")
            assert report["text"] == REPORT_TEXT and not report["truncated"]
            await browser.send(json.dumps({"type": "read", "id": 3, "path": "../../etc/passwd"}))
            escaped = await recv_type(browser, "error")
            assert escaped["id"] == 3 and "/home/daytona" in escaped["error"]
            await browser.send(json.dumps({"type": "ping"}))
            await recv_type(browser, "pong")
        # A wrong secret for the right identity is refused and closed with 4001.
        async with connect(f"ws://127.0.0.1:8787/pair/ws?for={identity}") as stranger:
            await stranger.send(json.dumps({"type": "auth", "token": identity + "." + "0" * 48}))
            await recv_type(stranger, "error")
            try:
                await stranger.recv()
            except ConnectionClosed as closed:
                assert closed.rcvd and closed.rcvd.code == 4001
        # Unpairing ends the credential: the next connection is refused.
        async with connect(f"ws://127.0.0.1:8787/pair/ws?for={identity}") as browser:
            await browser.send(json.dumps({"type": "auth", "token": token}))
            await recv_type(browser, "hello")
            await browser.send(json.dumps({"type": "unpair"}))
            await recv_type(browser, "unpaired")
        async with connect(f"ws://127.0.0.1:8787/pair/ws?for={identity}") as browser:
            await browser.send(json.dumps({"type": "auth", "token": token}))
            await recv_type(browser, "error")

        # Star then two hands the call to the recovery menu, which ends the GPT-Live leg.
        await mocks.send_live(dtmf("*"))
        await next_matching(mocks.live_messages, of_type("session.input_audio.mute"))
        await mocks.send_live(dtmf("2"))
        await wait_for(lambda: any(path == "/live/sessions/live-test-2/hangup" for path, _ in DaytonaMock.openai_requests), 10)
        events = await asyncio.to_thread(call_activity, "CA_VOICE_TEST2")
        for expected in ("menu.opened", "menu.closed", "menu.restart.requested", "menu.recovery", "voice.keypad.stop", "voice.keypad.status",
                         "voice.earcons", "pairing.started", "pairing.claimed", "pairing.accepted"):
            assert expected in events, (expected, events)
        handoff_rows = OrchestratorMock.user_message('"key":"7"')
        assert '"key":"1"' not in handoff_rows and '"key":"*"' not in handoff_rows

        # The canine fiction stays above ACP: no Agent prompt mentions dogs.
        prompts = [m for m in mocks.acp_seen if m.get("method") == "session/prompt"]
        assert prompts
        for message in prompts:
            for block in message["params"]["prompt"]:
                assert "dog" not in block.get("text", "").lower()

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
            assert "The walk (a working fiction)" in payload["instructions"]
            assert "first-call-tour" in payload["instructions"]
            assert {tool["name"] for tool in payload["tools"]} >= {
                "brief", "say", "create_managed_session", "begin_prompt_turn", "end_call",
                "discover_persisted_sessions", "load_persisted_session", "set_alias",
                "close_managed_session", "list_workspace_projects",
            }
            assert "Never create a dog in home and tell it to work in another directory" in payload["instructions"]
        fed_back = {
            item["call_id"]
            for _, payload in requests
            for item in payload["input"]
            if item.get("type") == "function_call_output"
        }
        assert fed_back == set(STEP_IDS)


def sandbox_toolbox(command: str) -> dict | None:
    """Sandbox commands: honeydozer is the only project directory besides home."""
    if "test -d" in command:
        return {"result": "", "exitCode": 0 if "/home/daytona/honeydozer" in command else 1}
    if "remote.origin.url" in command:
        rows = [
            ".\t0\t1\t",
            "./honeydozer\t1\t1\thttps://x-access-token:ghp_mocksecret@github.com/rndmcnlly/honeydozer.git",
        ]
        return {"result": "\n".join(rows) + "\n", "exitCode": 0}
    return None


def main() -> int:
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
    DaytonaMock.get_hooks = [sandbox_files]
    daytona = ThreadingHTTPServer(("127.0.0.1", DAYTONA_MOCK_PORT), DaytonaMock)
    thread = threading.Thread(target=daytona.serve_forever, daemon=True)
    thread.start()
    orchestrator = ThreadingHTTPServer(("127.0.0.1", ORCHESTRATOR_MOCK_PORT), OrchestratorMock)
    threading.Thread(target=orchestrator.serve_forever, daemon=True).start()
    process = subprocess.Popen(
        [
            wrangler,
            "dev",
            "--port",
            "8787",
            "--ip",
            "127.0.0.1",
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
        ],
        cwd=WORKER_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_port("127.0.0.1", 8787, timeout=20):
            raise RuntimeError("wrangler dev did not start")
        asyncio.run(run_protocol(identity))
        print("voice session delegation checks passed")
        return 0
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=10)
        daytona.shutdown()
        daytona.server_close()
        orchestrator.shutdown()
        orchestrator.server_close()


if __name__ == "__main__":
    sys.exit(main())
