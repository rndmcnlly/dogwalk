# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.11,<4"]
# ///
"""Deterministic unit + localhost WS tests, all downstream/inference FAKE.

Run: uv run --script test_frontend_server.py
No API keys, production endpoints, ACP sessions, or Android audio required.
"""
import asyncio
import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientSession, WSMsgType, WSServerHandshakeError, web

from frontend_server import (
    DEFAULT_MODEL,
    FIXTURE_PROMPT,
    Frontend,
    OpenRouterModel,
    SafeLog,
    Usage,
    make_app,
    parse_args,
    private_token,
)


async def eventually(predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.001)


class ScriptModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def decide(self, messages):
        self.calls.append(messages)
        response = next(self.responses)
        if isinstance(response, asyncio.Future):
            return await response
        return response


class FakeUpstream:
    def __init__(self):
        self.sent = []

    async def send(self, event):
        self.sent.append(event)


class FrontendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.emitted = []
        self.upstream = FakeUpstream()
        self.log = SafeLog()
        self.frontends = []

    async def asyncTearDown(self):
        for frontend in self.frontends:
            await frontend.close()

    def frontend(self, model):
        async def emit(event):
            self.emitted.append(event)
        frontend = Frontend(emit, self.upstream, model, FIXTURE_PROMPT, self.log)
        self.frontends.append(frontend)
        return frontend

    async def test_idle_and_background_do_not_call_model(self):
        model = ScriptModel([])
        f = self.frontend(model)
        await f.worker({"type": "session.thinking.append", "content": "Following: still working."})
        await f.worker({"type": "session.instructions.append", "content": "Be concise."})
        await asyncio.sleep(.02)
        self.assertEqual(model.calls, [])

    async def test_no_transcript_releases_floor_for_later_async_commentary(self):
        model = ScriptModel([{"action": "reply", "text": "The test failed."}])
        f = self.frontend(model)
        await f.android({"type": "user_speech_started"})
        await f.android({"type": "user_speech_ended", "outcome": "no_transcript"})
        await asyncio.sleep(.01)
        self.assertFalse(f.speaking)
        self.assertEqual(model.calls, [])
        await f.worker({"type": "session.commentary.append", "content": "Tell the user the test failed."})
        await eventually(lambda: self.emitted)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(self.emitted[0]["text"], "The test failed.")
        self.assertIsNone(self.emitted[0]["turn_id"])
        self.assertEqual(self.upstream.sent, [])

    async def test_no_transcript_wakes_commentary_queued_while_speaking(self):
        model = ScriptModel([{"action": "reply", "text": "Progress update."}])
        f = self.frontend(model)
        await f.android({"type": "user_speech_started"})
        await f.worker({"type": "session.commentary.append", "content": "Share progress."})
        await asyncio.sleep(.01)
        self.assertEqual(model.calls, [])
        await f.android({"type": "user_speech_ended", "outcome": "no_transcript"})
        await eventually(lambda: self.emitted)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(self.upstream.sent, [])

    async def test_no_transcript_resumes_unresolved_user_without_reviving_stale_result(self):
        delayed = asyncio.get_running_loop().create_future()
        model = ScriptModel([delayed, {"action": "handoff", "text": "I'll check."}])
        f = self.frontend(model)
        await f.android({"type": "user_transcript", "turn_id": "original", "text": "Inspect the error."})
        await eventually(lambda: model.calls)
        await f.android({"type": "user_speech_started"})
        await f.android({"type": "user_speech_ended", "outcome": "no_transcript"})
        delayed.set_result({"action": "reply", "text": "Stale answer."})
        await eventually(lambda: self.emitted)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(self.emitted[0]["text"], "Let me think.")
        self.assertEqual(self.emitted[0]["turn_id"], "original")
        self.assertEqual([event["type"] for event in self.upstream.sent],
                         ["session.input_transcript.delta", "session.delegation.created"])
        self.assertTrue(any(row["event"] == "model.stale_discarded" for row in self.log.rows))

    async def test_idle_no_transcript_cycles_do_not_call_model_and_validate_outcome(self):
        model = ScriptModel([])
        f = self.frontend(model)
        for _ in range(3):
            await f.android({"type": "user_speech_started"})
            await f.android({"type": "user_speech_ended", "outcome": "no_transcript"})
        await asyncio.sleep(.01)
        self.assertEqual(model.calls, [])
        self.assertEqual(self.upstream.sent, [])
        await f.android({"type": "user_speech_started"})
        for event in ({"type": "user_speech_ended"},
                      {"type": "user_speech_ended", "outcome": "completed"}):
            with self.assertRaises(ValueError):
                await f.android(event)
            self.assertTrue(f.speaking)

    async def test_handoff_is_actual_transcript_and_only_playback_is_spoken(self):
        model = ScriptModel([{"action": "handoff", "text": "I'll check."}])
        f = self.frontend(model)
        await f.android({"type": "user_transcript", "turn_id": "t1", "text": "How is Juniper doing?"})
        await eventually(lambda: len(self.emitted) == 1)
        self.assertEqual([e["type"] for e in self.upstream.sent],
                         ["session.input_transcript.delta", "session.delegation.created"])
        self.assertEqual(self.upstream.sent[0]["delta"].strip(), "How is Juniper doing?")
        self.assertGreater(self.upstream.sent[0]["end_ms"], 0)
        self.assertEqual(self.upstream.sent[1]["delegation"]["target"], "client")
        speech = self.emitted[0]
        self.assertEqual(speech["turn_id"], "t1")
        await f.android({"type": "speech_started", "speech_id": speech["speech_id"]})
        self.assertEqual(len(self.upstream.sent), 2)
        event = {"type": "speech_finished", "speech_id": speech["speech_id"],
                 "outcome": "completed", "spoken_chars": None}
        await f.android(event)
        await f.android(event)
        self.assertEqual(len(self.upstream.sent), 3)
        self.assertEqual(self.upstream.sent[-1]["delta"].strip(), "Let me think.")

    async def test_handoff_never_speaks_model_scheduling_or_session_claims(self):
        unsafe = "Juniper is still finishing its current build, but I'll call it back to check that error as soon as it's done."
        f = self.frontend(ScriptModel([{"action": "handoff", "text": unsafe}]))
        transcript = "Actually inspect the error first."
        await f.android({"type": "user_transcript", "turn_id": "correction", "text": transcript})
        await eventually(lambda: self.emitted)
        self.assertEqual(self.emitted[0]["text"], "Let me think.")
        self.assertNotIn(unsafe, json.dumps(self.emitted))
        self.assertEqual(self.upstream.sent[0]["delta"].strip(), transcript)
        self.assertEqual([event["type"] for event in self.upstream.sent],
                         ["session.input_transcript.delta", "session.delegation.created"])

    async def test_new_utterance_invalidates_inflight_generation(self):
        delayed = asyncio.get_running_loop().create_future()
        model = ScriptModel([delayed, {"action": "handoff", "text": "Checking the correction."}])
        f = self.frontend(model)
        await f.android({"type": "user_transcript", "turn_id": "old", "text": "Start it."})
        await eventually(lambda: len(model.calls) == 1)
        await f.android({"type": "user_speech_started"})
        await f.android({"type": "user_transcript", "turn_id": "new", "text": "Actually, just inspect it."})
        delayed.set_result({"action": "handoff", "text": "Wrong stale answer."})
        await eventually(lambda: len(self.emitted) == 1)
        self.assertEqual(self.emitted[0]["turn_id"], "new")
        delegations = [e for e in self.upstream.sent if e["type"] == "session.delegation.created"]
        self.assertEqual(len(delegations), 1)
        self.assertFalse(any("cancel" in e["type"] for e in self.upstream.sent))

    async def test_progress_coalesces_while_speaking_and_survives_interruption(self):
        model = ScriptModel([{"action": "reply", "text": "The latest progress."},
                             {"action": "reply", "text": "Rephrased progress."}])
        f = self.frontend(model)
        await f.android({"type": "user_speech_started"})
        for text in ("First progress", "Latest progress"):
            await f.worker({"type": "session.commentary.append", "content": text})
        await asyncio.sleep(.01)
        self.assertEqual(model.calls, [])
        await f.android({"type": "user_transcript", "turn_id": "t", "text": "Go on."})
        await eventually(lambda: len(self.emitted) == 1)
        sid = self.emitted[-1]["speech_id"]
        await f.android({"type": "speech_started", "speech_id": sid})
        await f.android({"type": "user_speech_started"})
        await f.android({"type": "speech_finished", "speech_id": sid,
                         "outcome": "interrupted", "spoken_chars": 4})
        self.assertEqual(len(f.pending), 2)
        self.assertEqual(self.upstream.sent[-1]["delta"], "The \n")
        await f.android({"type": "user_transcript", "turn_id": "t2", "text": "Say that again."})
        await eventually(lambda: len(self.emitted) == 2)
        await f.android({"type": "speech_finished", "speech_id": self.emitted[-1]["speech_id"],
                         "outcome": "completed", "spoken_chars": None})
        self.assertEqual(len(f.pending), 0)
        state = json.loads(model.calls[-1][-1]["content"])
        self.assertEqual(len(state["speech_requests"]), 2)

    async def test_unknown_interrupted_position_does_not_claim_heard(self):
        f = self.frontend(ScriptModel([{"action": "reply", "text": "Hello."}]))
        await f.android({"type": "user_transcript", "turn_id": "t", "text": "Hello"})
        await eventually(lambda: self.emitted)
        await f.android({"type": "speech_finished", "speech_id": self.emitted[0]["speech_id"],
                         "outcome": "interrupted", "spoken_chars": None})
        self.assertEqual(len(self.upstream.sent), 1)

    async def test_correction_can_handoff_with_pending_commentary(self):
        model = ScriptModel([{"action": "handoff", "text": "I'll pass on the correction."}])
        f = self.frontend(model)
        await f.android({"type": "user_speech_started"})
        await f.worker({"type": "session.commentary.append", "content": "The old build is running."})
        await f.android({"type": "user_transcript", "turn_id": "correction",
                         "text": "Stop that and inspect the error instead."})
        await eventually(lambda: self.emitted)
        self.assertEqual(self.upstream.sent[-1]["type"], "session.delegation.created")
        # An acknowledgement is not fulfillment of the earlier speech request.
        await f.android({"type": "speech_finished", "speech_id": self.emitted[0]["speech_id"],
                         "outcome": "interrupted", "spoken_chars": None})
        self.assertEqual(len(f.pending), 1)

    async def test_commentary_inflight_survives_user_speech(self):
        delayed = asyncio.get_running_loop().create_future()
        model = ScriptModel([delayed, {"action": "reply", "text": "Current progress."}])
        f = self.frontend(model)
        await f.worker({"type": "session.commentary.append", "content": "Share the latest progress."})
        await eventually(lambda: model.calls)
        await f.android({"type": "user_speech_started"})
        delayed.set_result({"action": "reply", "text": "Stale progress."})
        await asyncio.sleep(.01)
        self.assertEqual(self.emitted, [])
        self.assertEqual(len(f.pending), 1)
        await f.android({"type": "user_transcript", "turn_id": "t", "text": "Now go on."})
        await eventually(lambda: self.emitted)
        self.assertEqual(self.emitted[0]["text"], "Current progress.")

    async def test_context_race_retries_with_latest_authority(self):
        delayed = asyncio.get_running_loop().create_future()
        model = ScriptModel([delayed, {"action": "wait", "text": ""}])
        f = self.frontend(model)
        await f.android({"type": "user_transcript", "turn_id": "t", "text": "Status?"})
        await eventually(lambda: model.calls)
        await f.worker({"type": "session.thinking.append", "content": "Following: test FAILED."})
        delayed.set_result({"action": "reply", "text": "The test passed."})
        await eventually(lambda: len(model.calls) == 2)
        self.assertEqual(self.emitted, [])
        self.assertIn("test FAILED", model.calls[-1][-1]["content"])

    async def test_stop_during_generation_never_emits_or_handoffs(self):
        delayed = asyncio.get_running_loop().create_future()
        model = ScriptModel([delayed])
        f = self.frontend(model)
        await f.android({"type": "user_transcript", "turn_id": "t", "text": "Build it"})
        await eventually(lambda: model.calls)
        await f.android({"type": "session_stop"})
        delayed.set_result({"action": "handoff", "text": "Sure"})
        await asyncio.sleep(.01)
        self.assertEqual(self.emitted, [])
        self.assertEqual(len(self.upstream.sent), 1)


class LocalSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_worker_ws_adapter_uses_supplied_canonical_prompt(self):
        # Local fake Worker exercises the transport hook, NOT real Orchestrator/ACP.
        captured = asyncio.Queue()
        canonical = "CANONICAL TEST PROMPT: frontend role, no tools, concise speech."
        async def fake_worker(request):
            self.assertEqual(request.headers["Authorization"], "Basic YWRhbTpmYWtlLXBhc3N3b3Jk")
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            await socket.send_json({"type": "dev.frontend.ready", "instructions": canonical})
            async for message in socket:
                if message.type == WSMsgType.TEXT:
                    await captured.put(json.loads(message.data))
            return socket
        worker_app = web.Application()
        worker_app.router.add_get("/dev/voice-frontend", fake_worker)
        worker_runner = web.AppRunner(worker_app, access_log=None)
        await worker_runner.setup()
        worker_site = web.TCPSite(worker_runner, "127.0.0.1", 0)
        await worker_site.start()
        worker_port = worker_site._server.sockets[0].getsockname()[1]
        model = ScriptModel([{"action": "handoff", "text": "Checking."}])
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-not-used"}):
                args = parse_args(["--orchestrator-url",
                    f"ws://127.0.0.1:{worker_port}/dev/voice-frontend?phone=%2B15555550100",
                    "--tools", directory])
            args.admin_token = "fake-password"
            app = make_app(args, "fake-client-token", model_factory=lambda http, usage: model)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            try:
                async with ClientSession() as http:  # noqa: SIM117  # Keep setup readable.
                    async with http.ws_connect(f"http://127.0.0.1:{port}/ws",
                        headers={"Authorization": "Bearer fake-client-token"}) as socket:
                        ready = await socket.receive_json(timeout=2)
                        self.assertEqual(ready["type"], "ready")
                        await socket.send_json({"type": "user_transcript", "turn_id": "t",
                                                "text": "Check the work."})
                        speech = await socket.receive_json(timeout=2)
                        self.assertEqual(speech["type"], "speak")
                        transcript = await asyncio.wait_for(captured.get(), 2)
                        handoff = await asyncio.wait_for(captured.get(), 2)
                        self.assertEqual(transcript["delta"].strip(), "Check the work.")
                        self.assertGreater(transcript["end_ms"], 0)
                        self.assertEqual(handoff["delegation"]["target"], "client")
                        self.assertTrue(model.calls[0][0]["content"].startswith(canonical))
                        self.assertNotIn("LOCAL FIXTURE ONLY", model.calls[0][0]["content"])
                        await socket.send_json({"type": "session_stop"})
                        await socket.receive(timeout=2)
            finally:
                await runner.cleanup()
                await worker_runner.cleanup()

    async def test_fake_downstream_localhost_auth_and_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            args = parse_args(["--fixture-orchestrator", "--fixture-model", "--tools", directory])
            token = private_token(Path(directory))
            runner = web.AppRunner(make_app(args, token), access_log=None)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            url = f"http://127.0.0.1:{port}"
            try:
                async with ClientSession() as http:
                    with self.assertRaises(WSServerHandshakeError) as ctx:
                        await http.ws_connect(url + "/ws")
                    self.assertEqual(ctx.exception.status, 401)
                    async with http.ws_connect(url + "/ws", headers={"Authorization": "Bearer " + token}) as ws:
                        ready = await ws.receive_json(timeout=2)
                        self.assertEqual(ready["mode"], "fixture")
                        self.assertEqual(ready["frontend_model"], "fixture/fake-inference")
                        await ws.send_json({"type": "user_transcript", "turn_id": "t", "text": "Status?"})
                        speak = await ws.receive_json(timeout=2)
                        self.assertEqual(speak["type"], "speak")
                        self.assertIn("fixture", speak["text"])
                        await ws.send_json({"type": "speech_started", "speech_id": speak["speech_id"]})
                        await ws.send_json({"type": "speech_finished", "speech_id": speak["speech_id"],
                                            "outcome": "completed", "spoken_chars": None})
                        await ws.send_json({"type": "session_stop"})
                        await ws.receive(timeout=2)
            finally:
                await runner.cleanup()
            logs = list(Path(directory).glob("frontend-*.jsonl"))
            self.assertEqual(len(logs), 1)
            text = logs[0].read_text()
            self.assertNotIn(token, text)
            self.assertNotIn("Status?", text)
            self.assertIn("speech.completed", text)
            self.assertEqual(stat.S_IMODE(logs[0].stat().st_mode), 0o600)


class ConfigAndUsageTests(unittest.TestCase):
    def test_fail_closed_and_loopback_default(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for argv in ([], ["--fixture-model"],
                         ["--fixture-orchestrator", "--fixture-model", "--bind", "0.0.0.0"]):
                with self.assertRaises(SystemExit):
                    parse_args(argv)
        args = parse_args(["--fixture-orchestrator", "--fixture-model"])
        self.assertEqual(args.bind, ["127.0.0.1"])

    def test_token_private_and_rotates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            first = private_token(path)
            self.assertNotEqual(first, private_token(path))
            self.assertEqual(stat.S_IMODE((path / "frontend.token").stat().st_mode), 0o600)

    def test_unknown_usage_and_explicit_estimates(self):
        log = SafeLog()
        usage = Usage(log)
        usage.record(DEFAULT_MODEL, .1)
        self.assertIsNone(log.rows[-1]["cost_usd"])
        self.assertIsNone(log.rows[-1]["input_tokens"])
        usage.record(DEFAULT_MODEL, .2, {"usage": {"prompt_tokens": 100,
            "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 10},
            "completion_tokens_details": {"reasoning_tokens": 5}}})
        self.assertAlmostEqual(log.rows[-1]["cost_usd"], .000055)
        self.assertEqual(log.rows[-1]["cost_source"], "estimated_list_price")
        self.assertEqual(log.rows[-1]["cached_tokens"], 10)
        usage.record(DEFAULT_MODEL, .1, {"usage": {"cost": .0001}})
        self.assertEqual(log.rows[-1]["cost_source"], "provider")
        self.assertIsNone(usage.summary()["short_run_hourly_projection_usd"])


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_denial_no_retry_or_policy_fallback(self):
        calls = []
        class Response:
            status = 403
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
        class HTTP:
            def post(self, url, **kwargs):
                calls.append(kwargs)
                return Response()
        log = SafeLog()
        usage = Usage(log)
        model = OpenRouterModel(HTTP(), "never-print", DEFAULT_MODEL, usage)
        with self.assertRaises(RuntimeError):
            await model.decide([])
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["json"]["provider"]["zdr"])
        self.assertEqual(usage.requests, 1)
        self.assertIsNone(log.rows[-1]["cost_usd"])
        self.assertTrue(log.rows[-1]["failed"])

    async def test_provider_policy_and_schema_never_weaken(self):
        captured = []
        class Response:
            status = 200
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            async def json(self):
                return {"choices": [{"message": {"content": '{"action":"wait","text":""}'}}]}
        class HTTP:
            def post(self, url, **kwargs):
                captured.append(kwargs)
                return Response()
        log = SafeLog()
        model = OpenRouterModel(HTTP(), "secret-not-logged", DEFAULT_MODEL, Usage(log))
        await model.decide([])
        self.assertEqual(captured[0]["json"]["provider"],
                         {"data_collection": "deny", "zdr": True, "require_parameters": True})
        self.assertTrue(captured[0]["json"]["response_format"]["json_schema"]["strict"])
        self.assertNotIn("secret-not-logged", json.dumps(log.rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
