#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets==15.0.1"]
# ///
"""End-to-end local test of GPT-Live sideband and ACP coordination."""

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

    async def handler(self, socket) -> None:
        if socket.request.path == "/acp":
            self.acp_connected.set()
            async for raw in socket:
                message = json.loads(raw)
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
                await self.live_messages.put(message)
            return

        await socket.close(1008, "unknown mock path")

    async def send_live(self, message: dict) -> None:
        assert self.live is not None
        await self.live.send(json.dumps(message))


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
INSERT INTO ephemeral_services
  (id, phone_number, provider_id, name, port, session_id, created_at, updated_at, active)
VALUES ('service-test', '{TEST_PHONE}', 'mock-sandbox-1', 'VS Code', 8000,
        'session-smoke', unixepoch(), unixepoch(), 1);
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
        assert "Harness session events are not user speech" in session["instructions"]
        delegated = session["delegation"]["responses"]
        assert "call-start snapshot is advisory" in delegated["instructions"]
        assert {tool["name"] for tool in delegated["tools"]} >= {
            "create_managed_session",
            "begin_prompt_turn",
            "resolve_permission",
        }
        await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "initialize"
        )
        greeting = await next_matching(
            mocks.live_messages,
            lambda item: item.get("type") == "session.commentary.append",
        )
        assert greeting["content"] == "Dogwalk. Where to?"

        await mocks.send_live(
                {
                    "type": "response.event",
                    "event": {"type": "response.output_item.done", "item": {
                        "type": "function_call",
                        "name": "create_managed_session",
                        "call_id": "call-create",
                        "arguments": json.dumps({"alias": "Juniper"}),
                    }},
                }
            )
        created = await next_matching(
                mocks.live_messages,
                lambda item: item.get("item", {}).get("call_id") == "call-create",
            )
        assert created["type"] == "response.item.create"
        assert json.loads(created["item"]["output"])["ok"] is True
        await next_matching(
                mocks.acp_messages, lambda item: item.get("method") == "session/new"
            )

        await mocks.send_live(
                {
                    "type": "response.event",
                    "event": {"type": "response.output_item.done", "item": {
                        "type": "function_call",
                        "name": "begin_prompt_turn",
                        "call_id": "call-prompt",
                        "arguments": json.dumps(
                            {
                                "alias": "Juniper",
                                "prompt": "Create a tiny verified artifact.",
                            }
                        ),
                    }},
                }
            )
        prompted = await next_matching(
                mocks.live_messages,
                lambda item: item.get("item", {}).get("call_id") == "call-prompt",
            )
        assert json.loads(prompted["item"]["output"])["turn_state"] == "in_progress"
        prompt_request = await next_matching(
                mocks.acp_messages, lambda item: item.get("method") == "session/prompt"
            )
        assert prompt_request["params"]["sessionId"] == "acp-session-test"
        completion = await next_matching(
                mocks.live_messages,
                lambda item: "Prompt Turn stopped" in json.dumps(item),
            )
        assert completion["type"] == "session.commentary.append"

        await mocks.send_live(
                {
                    "type": "response.event",
                    "event": {"type": "response.output_item.done", "item": {
                        "type": "function_call",
                        "name": "text_ephemeral_service",
                        "call_id": "call-text",
                        "arguments": json.dumps({"service_id": "service-test"}),
                    }},
                }
            )
        queued = await next_matching(
                mocks.live_messages,
                lambda item: item.get("item", {}).get("call_id") == "call-text",
            )
        assert json.loads(queued["item"]["output"])["status"] == "queued"
        assert DaytonaMock.sms_messages[-1]["To"] == TEST_PHONE
        assert "signed-service-token" in DaytonaMock.sms_messages[-1]["Body"]
        assert (
                DaytonaMock.sms_messages[-1]["StatusCallback"]
                == "https://dogwalk.tools/sms/status"
            )

        await mocks.send_live(
                {
                    "type": "response.event",
                    "event": {"type": "response.output_item.done", "item": {
                        "type": "function_call",
                        "name": "end_call",
                        "call_id": "call-end",
                        "arguments": "{}",
                    }},
                }
            )
        async with asyncio.timeout(5):
            while not any(path.endswith("/hangup") for path, _ in DaytonaMock.openai_requests):
                await asyncio.sleep(0.05)


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
            f"ADMIN_PASSWORD:{LOCAL_ADMIN_PASSWORD}",
            "--var",
            "TWILIO_ACCOUNT_SID:AC_TEST",
            "--var",
            "TWILIO_FROM_NUMBER:+18317775707",
            "--var",
            f"TWILIO_API_BASE:http://127.0.0.1:{DAYTONA_MOCK_PORT}/twilio",
        ],
        cwd=WORKER_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_port("127.0.0.1", 8787, timeout=20):
            raise RuntimeError("wrangler dev did not start")
        asyncio.run(run_protocol(identity))
        print("voice session sideband checks passed")
        return 0
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=10)
        daytona.shutdown()
        daytona.server_close()


if __name__ == "__main__":
    sys.exit(main())
