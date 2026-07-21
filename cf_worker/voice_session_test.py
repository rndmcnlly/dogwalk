#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets==15.0.1"]
# ///
"""End-to-end local test of the Durable Object voice and ACP bridge."""

import asyncio
import hashlib
import hmac
import json
import secrets
import signal
import subprocess
import sys
import threading
from pathlib import Path

from websockets.asyncio.client import connect
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
    sign,
    wait_port,
)

WORKER_DIR = Path(__file__).resolve().parent
SOCKET_PORT = 8791
IDENTITY_SECRET = f"local-identity-secret-{secrets.token_hex(8)}"


class SocketMocks:
    def __init__(self) -> None:
        self.realtime = None
        self.acp_messages: asyncio.Queue[dict] = asyncio.Queue()
        self.realtime_messages: asyncio.Queue[dict] = asyncio.Queue()
        self.acp_connected = asyncio.Event()
        self.realtime_connected = asyncio.Event()
        self.response_number = 0

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

        if socket.request.path == "/realtime":
            self.realtime = socket
            self.realtime_connected.set()
            async for raw in socket:
                message = json.loads(raw)
                await self.realtime_messages.put(message)
                if message.get("type") == "session.update":
                    await socket.send(json.dumps({"type": "session.updated"}))
                elif message.get("type") == "response.create":
                    self.response_number += 1
                    response = {"id": f"response-{self.response_number}"}
                    await socket.send(
                        json.dumps({"type": "response.created", "response": response})
                    )
                    await socket.send(
                        json.dumps({"type": "response.done", "response": response})
                    )
            return

        await socket.close(1008, "unknown mock path")

    async def send_realtime(self, message: dict) -> None:
        assert self.realtime is not None
        await self.realtime.send(json.dumps(message))


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
        path = f"/voice/stream/{identity}/"
        public_url = f"wss://dogwalk.tools{path}"
        async with connect(
            f"ws://127.0.0.1:8787{path}",
            additional_headers={
                "x-twilio-signature": sign(public_url, {}, LOCAL_TOKEN)
            },
        ) as twilio:
            await twilio.send(
                json.dumps(
                    {"event": "connected", "protocol": "Call", "version": "1.0.0"}
                )
            )
            await twilio.send(
                json.dumps(
                    {
                        "event": "start",
                        "start": {
                            "streamSid": "MZ_TEST",
                            "callSid": "CA_VOICE_TEST",
                            "mediaFormat": {
                                "encoding": "audio/x-mulaw",
                                "sampleRate": 8000,
                                "channels": 1,
                            },
                        },
                    }
                )
            )
            await asyncio.wait_for(mocks.acp_connected.wait(), 8)
            await asyncio.wait_for(mocks.realtime_connected.wait(), 8)
            session_update = await next_matching(
                mocks.realtime_messages,
                lambda item: item.get("type") == "session.update",
            )
            assert (
                session_update["session"]["audio"]["input"]["format"]["type"]
                == "audio/pcmu"
            )
            assert (
                "Harness session events are not user speech"
                in session_update["session"]["instructions"]
            )
            assert (
                "Session inspection and listing are lightweight"
                in session_update["session"]["instructions"]
            )
            assert {tool["name"] for tool in session_update["session"]["tools"]} >= {
                "create_managed_session",
                "begin_prompt_turn",
                "resolve_permission",
            }
            await next_matching(
                mocks.acp_messages, lambda item: item.get("method") == "initialize"
            )
            await next_matching(
                mocks.realtime_messages,
                lambda item: item.get("type") == "response.create",
            )

            await mocks.send_realtime(
                {"type": "response.output_audio.delta", "delta": "dGVzdC1tdWxhdw=="}
            )
            outbound = json.loads(await asyncio.wait_for(twilio.recv(), 5))
            assert outbound["event"] == "media"
            assert outbound["media"]["payload"] == "dGVzdC1tdWxhdw=="
            await mocks.send_realtime({"type": "input_audio_buffer.speech_started"})
            cleared = json.loads(await asyncio.wait_for(twilio.recv(), 5))
            assert cleared == {"event": "clear", "streamSid": "MZ_TEST"}

            await mocks.send_realtime(
                {"type": "response.created", "response": {"id": "tool-create"}}
            )
            await mocks.send_realtime(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "name": "create_managed_session",
                        "call_id": "call-create",
                        "arguments": json.dumps({"alias": "Juniper"}),
                    },
                }
            )
            await mocks.send_realtime(
                {"type": "response.done", "response": {"id": "tool-create"}}
            )
            created = await next_matching(
                mocks.realtime_messages,
                lambda item: item.get("item", {}).get("call_id") == "call-create",
            )
            assert json.loads(created["item"]["output"])["ok"] is True
            await next_matching(
                mocks.acp_messages, lambda item: item.get("method") == "session/new"
            )

            await mocks.send_realtime(
                {"type": "response.created", "response": {"id": "tool-prompt"}}
            )
            await mocks.send_realtime(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "name": "begin_prompt_turn",
                        "call_id": "call-prompt",
                        "arguments": json.dumps(
                            {
                                "alias": "Juniper",
                                "prompt": "Create a tiny verified artifact.",
                            }
                        ),
                    },
                }
            )
            await mocks.send_realtime(
                {"type": "response.done", "response": {"id": "tool-prompt"}}
            )
            prompted = await next_matching(
                mocks.realtime_messages,
                lambda item: item.get("item", {}).get("call_id") == "call-prompt",
            )
            assert json.loads(prompted["item"]["output"])["turn_state"] == "in_progress"
            prompt_request = await next_matching(
                mocks.acp_messages, lambda item: item.get("method") == "session/prompt"
            )
            assert prompt_request["params"]["sessionId"] == "acp-session-test"
            completion = await next_matching(
                mocks.realtime_messages,
                lambda item: "Prompt Turn stopped" in json.dumps(item),
            )
            assert completion["type"] == "conversation.item.create"
            assert "not user speech" in completion["item"]["content"][0]["text"]

            await mocks.send_realtime(
                {"type": "response.created", "response": {"id": "tool-text"}}
            )
            await mocks.send_realtime(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "name": "text_ephemeral_service",
                        "call_id": "call-text",
                        "arguments": json.dumps({"service_id": "service-test"}),
                    },
                }
            )
            await mocks.send_realtime(
                {"type": "response.done", "response": {"id": "tool-text"}}
            )
            queued = await next_matching(
                mocks.realtime_messages,
                lambda item: item.get("item", {}).get("call_id") == "call-text",
            )
            assert json.loads(queued["item"]["output"])["status"] == "queued"
            assert DaytonaMock.sms_messages[-1]["To"] == TEST_PHONE
            assert "signed-service-token" in DaytonaMock.sms_messages[-1]["Body"]
            assert (
                DaytonaMock.sms_messages[-1]["StatusCallback"]
                == "https://dogwalk.tools/sms/status"
            )

            await mocks.send_realtime(
                {"type": "response.created", "response": {"id": "tool-end"}}
            )
            await mocks.send_realtime(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "name": "end_call",
                        "call_id": "call-end",
                        "arguments": "{}",
                    },
                }
            )
            await mocks.send_realtime(
                {"type": "response.done", "response": {"id": "tool-end"}}
            )
            await asyncio.wait_for(twilio.wait_closed(), 5)


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
            f"OPENAI_REALTIME_URL:http://127.0.0.1:{SOCKET_PORT}/realtime",
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
        print("voice session bridge checks passed")
        return 0
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=10)
        daytona.shutdown()
        daytona.server_close()


if __name__ == "__main__":
    sys.exit(main())
