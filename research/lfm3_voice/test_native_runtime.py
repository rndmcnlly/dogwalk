"""Native adapter's host bookkeeping, no network or inference."""
import json
import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from harness import Artifacts
from native_runtime import NativeRuntime
from live_probe import save_wav


class NativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = NativeRuntime("http://unused", Artifacts(Path(self.temp.name)))
        self.runtime.send = AsyncMock()
        self.runtime.until = AsyncMock(return_value={})
        self.runtime.user_text = AsyncMock()

    async def asyncTearDown(self):
        await self.runtime.close()
        self.temp.cleanup()

    async def test_actual_assistant_text_not_replayed_on_live_connection(self):
        self.runtime.last_text = "Audible text."
        await self.runtime.input_message({"role": "assistant", "content": "Audible text."})
        self.runtime.user_text.assert_not_called()

    async def test_reconstructed_assistant_history_explicitly_labelled(self):
        await self.runtime.input_message({"role": "assistant", "content": "Earlier speech."}, replay=True)
        payload = self.runtime.user_text.call_args.args[0]
        self.assertTrue(payload.startswith("HOST_HISTORY "))
        self.assertTrue(json.loads(payload.removeprefix("HOST_HISTORY "))["historical_not_a_new_caller_request"])

    async def test_no_forged_native_calls_on_fresh_connection(self):
        with self.assertRaisesRegex(ValueError, "cannot be forged"):
            await self.runtime.input_message({"role": "assistant", "tool_calls": [{}]}, replay=True)

    async def test_receipt_requires_actual_call_and_consumes_it_once(self):
        message = {"role": "tool", "tool_call_id": "host-prefix-actual-id", "content": "accepted"}
        with self.assertRaisesRegex(ValueError, "no observed call"):
            await self.runtime.input_message(message)
        self.runtime.call_ids["actual-id"] = {}
        await self.runtime.input_message(message)
        self.assertEqual(self.runtime.send.call_args.args[0]["item"]["call_id"], "actual-id")
        with self.assertRaisesRegex(ValueError, "no observed call"):
            await self.runtime.input_message(message)

    async def test_unsupported_role_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, "Unsupported realtime replay role"):
            await self.runtime.input_message({"role": "developer", "content": "policy"})

    async def test_current_audio_is_not_marked_historical_during_reconstruction(self):
        path = Path(self.temp.name) / "caller.wav"
        save_wav(path, b"\0\0" * 160, 16000)
        data = base64.b64encode(path.read_bytes()).decode()
        self.runtime.current_audio_data = [data]
        message = {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": data}}]}
        await self.runtime.input_message(message, replay=True)
        self.runtime.user_text.assert_not_called()
        self.runtime.current_audio_data = []
        await self.runtime.input_message(message, replay=True)
        self.assertIn("historical caller", self.runtime.user_text.call_args.args[0])

    async def test_idle_realtime_slot_cannot_deadlock_an_http_generation(self):
        fake = AsyncMock()
        self.runtime.ws = fake
        with self.assertRaisesRegex(RuntimeError, "One-slot server"):
            await self.runtime.request("/tts", {"input": "hello"})
        await self.runtime.reset_context()
        fake.close.assert_awaited_once()
