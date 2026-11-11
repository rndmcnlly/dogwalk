#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets==15.0.1"]
# ///
"""Local-only alternate frontend test: actual Worker/DO, fake model and ACP.

GET /dev/voice-frontend?phone=<E.164>, existing admin authorization, explicit
ENABLE_TEXT_FRONTEND_SPIKE=true. Ready's opening is metadata; speak the following
commentary append, not both. Close the socket to detach, without cancelling work.
Normal end_call closes 1000 / "development frontend hangup". Invalid or unsupported
frames close 1008 / "invalid development frontend event". Provider lifecycle and
audio events are intentionally not part of this transport.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import sys
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, InvalidStatus

import voice_session_test as v


AUTH = {"Authorization": "Basic " + base64.b64encode(
    f"adam:{v.LOCAL_ADMIN_PASSWORD}".encode()).decode()}
URL = "ws://127.0.0.1:8787/dev/voice-frontend?" + urllib.parse.urlencode({"phone": v.TEST_PHONE})
UNSUPPORTED = ["start_recording", "play_audio", "pair_browser", "set_earcon_style", "set_theme", "open_recovery_menu"]


async def rejected(status, headers=AUTH, url=URL):
    try:
        async with connect(url, additional_headers=headers):
            raise AssertionError("unexpected socket attachment")
    except InvalidStatus as error:
        assert error.response.status_code == status, error


@asynccontextmanager
async def frontend(url=URL):
    # The peer sees close before asynchronous transcript persistence finishes.
    async with asyncio.timeout(10):
        while True:
            try:
                socket = await connect(url, additional_headers=AUTH)
                break
            except InvalidStatus as error:
                assert error.response.status_code == 409, error
                await asyncio.sleep(0.05)
    try:
        yield socket
    finally:
        await socket.close()


async def received(socket, predicate):
    async with asyncio.timeout(20):
        while True:
            message = json.loads(await socket.recv())
            if predicate(message):
                return message


def conversation():
    req = urllib.request.Request(
        "http://127.0.0.1:8787/admin/api/conversations?verbose=1", headers=AUTH)
    with urllib.request.urlopen(req) as response:
        return json.load(response)[0]


async def protocol():
    mocks = v.SocketMocks()
    v.ORCHESTRATOR_SCRIPT = [
        ("call_started", lambda ctx: [v.call("dev-opening", "brief", text="Local frontend attached.")]),
        ("[harness event] agent_connected", lambda ctx: [v.call("dev-ready", "brief", text="Local Agent ready.")]),
        ("[handoff dev-work]", lambda ctx: [v.call("dev-create", "create_managed_session", alias="Juniper", directory="/home/daytona")]),
        ("[handoff dev-work]", lambda ctx: [v.call("dev-begin", "begin_prompt_turn", alias="Juniper", prompt="Build the local test artifact.")]),
        ("[handoff dev-work]", lambda ctx: [{**v.call("dev-skill", "load_voice_agent_skill"), "arguments": json.dumps({"name": "precise-transcription"})}]),
        ("[handoff dev-work]", lambda ctx: [v.call("dev-say", "say", text="Local work started.", delegation_id="dev-work")]),
        ("turn_stopped", lambda ctx: [v.call("dev-finished", "say", text="Local work finished.")]),
        ("[handoff dev-unsupported]", lambda ctx: [v.call(f"unsupported-{name}", name) for name in UNSUPPORTED]),
        ("[handoff dev-unsupported]", lambda ctx: [v.call("dev-unsupported-say", "say", text="Those audio operations are unavailable.")]),
        ("[handoff dev-end]", lambda ctx: [v.call("dev-bye", "say", text="Goodbye."), v.call("dev-end", "end_call")]),
    ]
    async with serve(mocks.handler, "127.0.0.1", v.SOCKET_PORT):
        await rejected(401, {})
        await rejected(400, url=URL.split("?")[0] + "?phone=invalid")
        await rejected(404, url=URL.split("?")[0] + "?phone=%2B15555550199")
        async with frontend(URL + "&sandbox_id=untrusted&identity=untrusted") as socket:
            ready = json.loads(await socket.recv())
            assert ready["type"] == "dev.frontend.ready" and ready["protocol"] == 1, ready
            assert ready["call_id"].startswith("DEV_TEXT_")
            assert ready["theme"] == "park" and "Generate <|SpawnThinking|>" in ready["instructions"]
            # Ignored local artifact: the actual canonical prompt received over this adapter.
            artifact = Path(__file__).resolve().parents[1] / "prototypes/android_voice_probe/.tools/canonical-frontend-instructions.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(ready["instructions"], encoding="utf-8")
            print(f"Canonical frontend prompt artifact ({ready['theme']}): {artifact}")
            assert ready["opening"].startswith("Development frontend. No caller-ID verification.")
            opening = json.loads(await socket.recv())
            assert opening == {"type": "session.commentary.append", "delegation_id": None, "content": ready["opening"]}
            await rejected(409)
            await received(socket, v.appended("thinking", "Local Agent ready."))
            await socket.send(json.dumps(v.transcript("input", "Build an artifact locally.", 0, 1000)))
            await socket.send(json.dumps(v.transcript("output", "Let me think.", 1000, 1200)))
            await socket.send(json.dumps(v.delegation("dev-work", 1250)))
            guidance = await received(socket, lambda item: item.get("type") == "session.instructions.append")
            assert guidance["content"] and guidance["delegation_id"] is None
            started = await received(socket, v.appended("commentary", "Local work started."))
            assert started["delegation_id"] == "dev-work"
            await v.wait_for(v.outputs_back("dev-say"), 10)
            requests_before_progress = len(v.OrchestratorMock.requests)
            await received(socket, lambda item: item.get("type") == "session.thinking.append"
                           and item.get("content", "").startswith("Following Juniper:"))
            assert len(v.OrchestratorMock.requests) == requests_before_progress
            await received(socket, v.appended("commentary", "Local work finished."))
            assert "Caller: Build an artifact locally." in v.OrchestratorMock.user_message("[handoff dev-work]")
            assert "Voice: Let me think." in v.OrchestratorMock.user_message("[handoff dev-work]")
            assert v.OrchestratorMock.tool_output("dev-create")["session"]["alias"] == "Juniper"
            assert any(item.get("method") == "session/prompt" for item in mocks.acp_seen)
            await socket.send(json.dumps(v.transcript("input", "Try unsupported operations.", 2000, 2100)))
            await socket.send(json.dumps(v.delegation("dev-unsupported", 2150)))
            await received(socket, v.appended("commentary", "Those audio operations are unavailable."))
            for name in UNSUPPORTED:
                result = v.OrchestratorMock.tool_output(f"unsupported-{name}")
                assert result["ok"] is False and "unsupported" in result["error"], result
            # All appends came from the actual Orchestrator loop, no Live media connection.
            assert not mocks.live_connected.is_set() and not v.DaytonaMock.openai_requests
            await socket.send(json.dumps(v.transcript("input", "End this conversation.", 3000, 3100)))
            await socket.send(json.dumps(v.delegation("dev-end", 3150)))
            await received(socket, v.appended("commentary", "Goodbye."))
            try:
                async with asyncio.timeout(10):
                    while True:
                        await socket.recv()
            except ConnectionClosed as error:
                assert error.rcvd.code == 1000 and error.rcvd.reason == "development frontend hangup", error
            assert not v.DaytonaMock.openai_requests

        # Cleanup persists transcript/context; unsupported provider/audio events never ack.
        async with asyncio.timeout(10):
            while True:
                saved = await asyncio.to_thread(conversation)
                if any(item["call_sid"] == ready["call_id"] for item in saved["call_transcripts"]):
                    break
                await asyncio.sleep(0.05)
        assert saved["current_transcript"] is None
        for invalid in [
            {"type": "session.output_audio.fixture.played"},
            {"type": "session.created"},
            {"type": "session.updated"},
            {"type": "session.input_transcript.delta", "delta": "No timestamps"},
            {"type": "session.delegation.created", "offset_ms": 0, "delegation": {"id": "bad id", "target": "client"}},
        ]:
            async with frontend() as socket:
                assert json.loads(await socket.recv())["type"] == "dev.frontend.ready"
                await socket.send(json.dumps(invalid))
                try:
                    async with asyncio.timeout(10):
                        while True:
                            await socket.recv()
                except ConnectionClosed as error:
                    assert error.rcvd.code == 1008, error
        assert not mocks.live_connected.is_set() and not v.DaytonaMock.openai_requests

        # Simulated PSTN concurrency, also entirely local: both directions must reject.
        async with frontend() as socket:
            assert json.loads(await socket.recv())["type"] == "dev.frontend.ready"
            body, webhook_id, signature = v.signed_webhook("CA_VOICE_TEST", "live-conflict")
            assert await asyncio.to_thread(v.post_webhook, body, webhook_id, signature) == 204
            assert v.DaytonaMock.openai_requests[-1][0].endswith("/reject")
            assert not mocks.live_connected.is_set()
        # Give asynchronous close cleanup a bounded opportunity to finish.
        async with frontend() as socket:
            assert json.loads(await socket.recv())["type"] == "dev.frontend.ready"
        await asyncio.sleep(0.2)
        body, webhook_id, signature = v.signed_webhook("CA_VOICE_TEST", "live-local-only")
        assert await asyncio.to_thread(v.post_webhook, body, webhook_id, signature) == 204
        await asyncio.wait_for(mocks.live_connected.wait(), 10)
        await rejected(409)
        await mocks.send_live({"type": "session.closed", "reason": "local concurrency test complete"})


def main():
    subprocess.run(["node", "text_frontend_race_test.cjs"], cwd=v.WORKER_DIR, check=True)
    wrangler = v.project_wrangler()
    if not wrangler:
        return 2
    v.prepare_local_fixture(wrangler)
    identity = hmac.new(v.IDENTITY_SECRET.encode(), v.TEST_PHONE.encode(), hashlib.sha256).hexdigest()
    v.configure_assignment(wrangler, identity)
    v.DaytonaMock.exists = True
    v.DaytonaMock.generation = 1
    v.DaytonaMock.openai_requests = []
    v.DaytonaMock.execute_hooks = [v.sandbox_toolbox]
    v.DaytonaMock.get_hooks = [v.sandbox_files]
    servers = [
        v.ThreadingHTTPServer(("127.0.0.1", v.DAYTONA_MOCK_PORT), v.DaytonaMock),
        v.ThreadingHTTPServer(("127.0.0.1", v.ORCHESTRATOR_MOCK_PORT), v.OrchestratorMock),
    ]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    process = v.start_wrangler(wrangler)
    try:
        asyncio.run(rejected(404))
        v.stop_wrangler(process)
        process = v.start_wrangler(wrangler, text_frontend=True)
        asyncio.run(protocol())
        print("development text frontend checks passed (local mocks only)")
        return 0
    except Exception:
        print("Local scripted tools issued:", v.OrchestratorMock.issued, file=sys.stderr)
        for tool in ["dev-create", "dev-begin"]:
            try:
                print(tool, v.OrchestratorMock.tool_output(tool), file=sys.stderr)
            except AssertionError:
                pass
        raise
    finally:
        v.stop_wrangler(process)
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    sys.exit(main())
