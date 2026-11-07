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
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from websockets.asyncio.server import serve

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

    async def handler(self, socket) -> None:
        if socket.request.path == "/acp":
            self.acp_connected.set()
            async for raw in socket:
                message = json.loads(raw)
                self.acp_seen.append(message)
                await self.acp_messages.put(message)
                request_id = message.get("id")
                method = message.get("method")
                if request_id is None:
                    continue
                if method == "initialize":
                    result = {"protocolVersion": 1, "agentCapabilities": {}}
                elif method == "session/new":
                    result = {"sessionId": "acp-session-test"}
                elif method == "session/load":
                    result = {}
                elif method == "session/list":
                    result = {
                        "sessions": [
                            {
                                "sessionId": "acp-session-test",
                                "cwd": "/home/daytona",
                                "title": "Tiny verified artifact",
                                "updatedAt": "2026-11-07T10:00:00Z",
                            },
                            {
                                "sessionId": PERSISTED_SESSION_ID,
                                "cwd": "/home/daytona",
                                "title": PERSISTED_TITLE,
                                "updatedAt": "2026-11-06T18:30:00Z",
                            },
                        ]
                    }
                elif method == "session/prompt":
                    await asyncio.sleep(0.2)
                    await socket.send(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "method": "session/update",
                                "params": {
                                    "sessionId": "acp-session-test",
                                    "update": {
                                        "sessionUpdate": "agent_message_chunk",
                                        "content": {
                                            "type": "text",
                                            "text": "Bridge report complete.",
                                        },
                                    },
                                },
                            }
                        )
                    )
                    result = {"stopReason": "end_turn"}
                else:
                    result = {}
                await socket.send(
                    json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})
                )
            return

        if socket.request.path == "/live/sessions/live-test/attach":
            self.live = socket
            self.live_connected.set()
            async for raw in socket:
                message = json.loads(raw)
                self.seen.append(message)
                await self.live_messages.put(message)
            return

        await socket.close(1008, "unknown mock path")

    async def send_live(self, message: dict) -> None:
        assert self.live is not None
        await self.live.send(json.dumps(message))


CALL_BRIEF = "Call started with no Managed Sessions."
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
    ("call_started", lambda ctx: [call("cs-brief", "brief", text=CALL_BRIEF, delegation_id=None)]),
    ("[handoff del-1]", lambda ctx: [
        call("h1-brief", "brief", text=WORK_BRIEF, delegation_id="del-1"),
        call("h1-create", "create_managed_session", alias="Juniper"),
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
    ("[handoff del-3]", lambda ctx: [call("h3-say", "say", text=LEASH_SAY, delegation_id="del-3")]),
    ("[handoff del-4]", lambda ctx: [
        call("h4-say", "say", text=FAREWELL_SAY, delegation_id="del-4"),
        call("h4-end", "end_call"),
    ]),
]
STEP_IDS = [
    "cs-brief", "h1-brief", "h1-create", "h1-begin", "h1-say-url", "h1-say",
    "ts-say", "h2-inspect", "h2-say",
    "h3-discover", "h3-load", "h3-retag", "h3-close", "h3-list", "h3-say",
    "h4-say", "h4-end",
]


class OrchestratorMock(BaseHTTPRequestHandler):
    """A scripted /v1/responses endpoint standing in for the Session Orchestrator model."""

    requests: list[tuple[dict[str, str], dict]] = []
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
        body = json.dumps({"output": output, "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
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
VALUES ('CA_VOICE_TEST', '{TEST_PHONE}', 'in-progress', unixepoch(), unixepoch());
"""
    subprocess.run(
        [wrangler, "d1", "execute", "dogwalk", "--local", "--command", sql],
        cwd=WORKER_DIR,
        check=True,
        stdout=subprocess.DEVNULL,
    )


async def run_protocol(identity: str) -> None:
    mocks = SocketMocks()
    async with serve(mocks.handler, "127.0.0.1", SOCKET_PORT):
        call_sid = "CA_VOICE_TEST"
        expires_at = int(time.time()) + 300
        content = f"{call_sid}.{expires_at}"
        signature = base64.urlsafe_b64encode(
            hmac.new(
                IDENTITY_SECRET.encode(),
                f"sip-context-v1:{content}".encode(),
                hashlib.sha256,
            ).digest()
        ).decode().rstrip("=")
        context = f"{content}.{signature}"
        event = {
            "object": "event",
            "id": "evt_live_test",
            "type": "live.transport.incoming",
            "data": {
                "type": "sip",
                "session_id": "live-test",
                "sip_headers": [{"name": "X-Dogwalk-Context", "value": context}],
            },
        }
        body = json.dumps(event, separators=(",", ":"))
        webhook_id = "wh_live_test"
        timestamp = str(int(time.time()))
        webhook_signature = base64.b64encode(
            hmac.new(
                OPENAI_WEBHOOK_KEY,
                f"{webhook_id}.{timestamp}.{body}".encode(),
                hashlib.sha256,
            ).digest()
        ).decode()

        def post_webhook(signature_header: str) -> int:
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

        assert await asyncio.to_thread(post_webhook, "v1,invalid") == 400
        assert not DaytonaMock.openai_requests
        assert await asyncio.to_thread(post_webhook, f"v1,{webhook_signature}") == 204
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
        assert "never mention a backend" in frontend
        assert "backend" not in (
            frontend.replace("Backend tools:", "").replace("never mention a backend", "").lower()
        )
        await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "initialize"
        )
        greeting = await next_matching(
            mocks.live_messages,
            lambda item: item.get("type") == "session.commentary.append",
        )
        assert greeting["content"] == "Dogwalk. Where to?"
        call_brief = await next_matching(mocks.live_messages, appended("thinking", CALL_BRIEF))
        assert call_brief["delegation_id"] is None

        # Handoff 1: the Voice Frontend delegates; the task is the transcript.
        await mocks.send_live(transcript("input", "Start a session called Juniper ", 0, 900))
        await mocks.send_live(transcript("input", "to build a tiny verified artifact.", 900, 2100))
        await mocks.send_live(transcript("output", "On it.", 2200, 2600))
        await mocks.send_live(delegation("del-1", 2150))
        work_brief = await next_matching(mocks.live_messages, appended("thinking", WORK_BRIEF))
        assert work_brief["delegation_id"] == "del-1"
        await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/new"
        )
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
        assert OrchestratorMock.tool_output("h1-create")["session"]["alias"] == "Juniper"
        assert OrchestratorMock.tool_output("h1-begin")["turn_state"] == "in_progress"

        # Handoff 2: a follow-up question answered from session inspection.
        await mocks.send_live(transcript("input", "What did it report?", 5000, 6200))
        await mocks.send_live(delegation("del-2", 6250))
        reported = await next_matching(mocks.live_messages, appended("commentary", REPORTED_SAY))
        assert reported["delegation_id"] == "del-2"
        handoff = OrchestratorMock.user_message("[handoff del-2]")
        assert "Caller: What did it report?" in handoff and "Juniper" not in handoff
        inspected = OrchestratorMock.tool_output("h2-inspect")
        assert inspected["alias"] == "Juniper" and inspected["stop_reason"] == "end_turn"

        # Handoff 3: leash earlier work, retag it, let it go home, and list what remains.
        await mocks.send_live(transcript("input", "Pick up that parser work from yesterday ", 7000, 8000))
        await mocks.send_live(transcript("input", "as Birch, call it Rowan, then let it go.", 8000, 8800))
        await mocks.send_live(delegation("del-3", 8850))
        listed = await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/list"
        )
        assert listed["params"] == {"cwd": "/home/daytona"}
        resumed = await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/resume"
        )
        assert resumed["params"] == {
            "sessionId": PERSISTED_SESSION_ID, "cwd": "/home/daytona", "mcpServers": [],
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
            {"ref": "r1", "title": PERSISTED_TITLE, "updated_at": "2026-11-06T18:30:00Z"}
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

        # Handoff 4: farewell and end_call in one response; hangup follows the farewell.
        await mocks.send_live(transcript("input", "Thanks, hang up now.", 9000, 10000))
        await mocks.send_live(delegation("del-4", 10050))
        farewell = await next_matching(mocks.live_messages, appended("commentary", FAREWELL_SAY))
        assert farewell["delegation_id"] == "del-4"
        async with asyncio.timeout(10):
            while not any(path.endswith("/hangup") for path, _ in DaytonaMock.openai_requests):
                await asyncio.sleep(0.05)
        assert OrchestratorMock.tool_output("h4-end")["status"] == "ending call"

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
            assert {tool["name"] for tool in payload["tools"]} >= {
                "brief", "say", "create_managed_session", "begin_prompt_turn", "end_call",
                "discover_persisted_sessions", "load_persisted_session", "set_alias",
                "close_managed_session",
            }
        fed_back = {
            item["call_id"]
            for _, payload in requests
            for item in payload["input"]
            if item.get("type") == "function_call_output"
        }
        assert fed_back == set(STEP_IDS)


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
