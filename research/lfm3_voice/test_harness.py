"""CPU-only tests: uv run --with httpx python -m unittest discover -s research/lfm3_voice -p test_harness.py."""
import asyncio
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
import wave

import httpx
from harness import Artifacts, Backend, Runtime, audio_part, narrowband, parse_calls, replay, speech_script, summarize, wav_info


def wav():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 1600)
    return stream.getvalue()


class PureTests(unittest.TestCase):
    def test_not_run_points_cannot_shrink_the_recorded_denominator(self):
        result = summarize([{"steps": [{"status": "not_run_after_prior_failure", "routing_pass": False}]}])
        self.assertEqual(result["points_recorded"], 1)
        self.assertEqual(result["points_observed"], 0)
        self.assertEqual(result["points_not_run"], 1)
        self.assertEqual(result["routing_pass"], 0)

    def test_exact_tool_contract(self):
        for name, args, valid in [("delegate", "{}", True), ("delegate", '{"task":"x"}', False),
                                  ("other", "{}", False), ("delegate", "[]", False),
                                  ("delegate", "null", False), ("delegate", "bad", False)]:
            with self.subTest(name=name, args=args):
                calls = parse_calls({"tool_calls": [{"function": {"name": name, "arguments": args}}]})
                self.assertEqual(calls[0]["valid"], valid)

    def test_liquid_empty_xml(self):
        self.assertTrue(parse_calls({"content": "<tool_call><function=delegate>\n</function></tool_call>"})[0]["valid"])
        self.assertFalse(parse_calls({"content": "<tool_call><function=delegate>\n</function></tool_call>"})[0]["dispatchable"])
        self.assertFalse(parse_calls({"content": "<tool_call><function=delegate><parameter=task>x</parameter></function></tool_call>"})[0]["valid"])

    def test_wav(self):
        self.assertAlmostEqual(wav_info(wav())["duration_s"], .1)
        with self.assertRaises(ValueError):
            wav_info(b"not audio")

    def test_acceptance_is_not_completion(self):
        backend = Backend()
        history = [{"role": "caller", "text": "do work"}]
        receipt = backend.accept({"id": "x"}, history, 3)
        history.append({"role": "caller", "text": "correction"})
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(receipt["transcript_cut"], 1)
        self.assertEqual(len(backend.pending[receipt["receipt_id"]]["transcript"]), 1)

    def test_markup_not_spoken(self):
        self.assertEqual(speech_script("<think>private</think> Hello")[0], "")
        self.assertEqual(speech_script("<tool_call><function=delegate></function></tool_call> Okay")[0], "Okay")

    def test_narrowband_duration_and_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Artifacts(Path(directory))
            source = artifacts.path / "source.wav"
            source.write_bytes(wav())
            path, info = narrowband(source, artifacts)
            self.assertEqual(info["delivered_rate"], 8000)
            self.assertAlmostEqual(wav_info(path.read_bytes())["duration_s"], .1)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.artifacts = Artifacts(Path(self.temp.name))
        self.requests = []

    async def asyncTearDown(self):
        self.temp.cleanup()

    def runtime(self, handler, **kwargs):
        return Runtime("http://fake", self.artifacts, transport=httpx.MockTransport(handler), **kwargs)

    async def test_tts_cache_key_voice_and_fingerprint(self):
        def handler(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(200, content=wav(), headers={"content-type": "audio/wav"})
        runtime = self.runtime(handler, fingerprint="model-runtime-a")
        try:
            a = await runtime.tts("Hello", "female")
            b = await runtime.tts("Hello", "female")
            c = await runtime.tts("Hello", "male")
            self.assertFalse(a["cache_hit"])
            self.assertTrue(b["cache_hit"])
            self.assertNotEqual(a["audio_path"], c["audio_path"])
            self.assertEqual(len(self.requests), 2)
            self.assertEqual(self.requests[0]["n_predict"], 256)
            self.assertTrue(list(self.artifacts.path.glob("*-response.bin")))
        finally:
            await runtime.close()

    async def test_chat_does_not_invent_native_audio(self):
        runtime = self.runtime(lambda request: httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "Hello"}}]}))
        try:
            result = await runtime.chat([{"role": "user", "content": "Hi"}], native_audio=True)
            self.assertEqual(result["audio_status"], "unsupported_or_not_emitted")
            self.assertEqual(result["predicted_text"], "Hello")
            self.assertNotIn("audio_path", result)
        finally:
            await runtime.close()

    async def test_unsupported_and_timeout_are_explicit(self):
        runtime = self.runtime(lambda request: httpx.Response(501, json={"error": "no audio"}))
        try:
            self.assertEqual((await runtime.tts("Hi"))["status"], "unsupported")
        finally:
            await runtime.close()
        def timeout(request):
            raise httpx.ReadTimeout("test timeout")
        runtime = self.runtime(timeout)
        try:
            self.assertEqual((await runtime.request("/props"))["status"], "timeout")
        finally:
            await runtime.close()

    async def test_audio_input_not_text_substitution(self):
        audio = self.artifacts.path / "input.wav"
        audio.write_bytes(wav())
        def handler(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "recognized"}}]})
        runtime = self.runtime(handler)
        try:
            result = await runtime.stt(audio)
            self.assertEqual(result["predicted_text"], "recognized")
            messages = self.requests[0]["messages"]
            self.assertEqual(messages[0]["content"], "Perform ASR in English.")
            part = messages[1]["content"][0]
            self.assertEqual(part["type"], "input_audio")
            self.assertEqual(wav_info(base64.b64decode(part["input_audio"]["data"]))["sample_rate"], 16000)
            self.assertEqual(result["source_audio_path"], str(audio))
        finally:
            await runtime.close()

    async def test_historical_raw_asr_is_an_explicit_reproduction_condition(self):
        audio = self.artifacts.path / "raw-input.wav"
        audio.write_bytes(wav())
        def handler(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "recognized"}}]})
        runtime = self.runtime(handler)
        runtime.raw_asr = True
        try:
            result = await runtime.stt(audio)
            self.assertEqual(self.requests[0]["messages"][1]["content"], [audio_part(audio)])
            self.assertEqual(result["asr_input_mode"], "raw_wav")
        finally:
            await runtime.close()

    async def test_replay_batch_context_receipts_and_matched_input_arms(self):
        # All routes are fake, including TTS and recognition. No local model required.
        for mode in ("native_audio", "oracle_text", "lfm_stt"):
          with self.subTest(mode=mode):
            decisions = []
            def handler(request):
                body = json.loads(request.content)
                if request.url.path == "/tts":
                    return httpx.Response(200, content=wav())
                if body["messages"][0].get("content") == "Perform ASR in English.":
                    return httpx.Response(200, json={"choices": [{"message": {
                        "role": "assistant", "content": "Observed request"}}]})
                decisions.append(body)
                message = {"role": "assistant", "content": "I will pass that on."}
                if len(decisions) == 1:
                    message["tool_calls"] = [{"id": "real-call", "type": "function", "function": {
                        "name": "delegate", "arguments": "{}"}}]
                return httpx.Response(200, json={"choices": [{"message": message}]})
            runtime = self.runtime(handler, fingerprint="fake-runtime")
            scenario = {"id": "mock", "steps": [
                {"id": "request", "events": [{"kind": "caller", "text": "Fixture request"}],
                 "expect": {"delegate": True, "silence": False, "forbidden": ["CANARY_GRADER"]}},
                {"id": "progress", "events": [
                    {"kind": "backend_result", "handoff": "request", "text": "Cedar is checking."},
                    {"kind": "commentary", "handoff": "request", "text": "Say checking now."}],
                 "expect": {"delegate": False, "silence": False}}]}
            try:
                reports = await replay(runtime, [scenario], "tool_envelope", input_mode=mode)
                self.assertEqual(len(decisions), 2)  # Not three responses for three input events.
                serialized = json.dumps(decisions)
                self.assertNotIn("CANARY_GRADER", serialized)
                first_caller = decisions[0]["messages"][1]["content"]
                if mode == "native_audio":
                    self.assertEqual(first_caller[0]["type"], "input_audio")
                    self.assertNotIn("Fixture request", json.dumps(first_caller))
                    self.assertNotIn("Observed request", json.dumps(first_caller))
                else:
                    self.assertEqual(first_caller, [{"type": "text", "text":
                        "Fixture request" if mode == "oracle_text" else "Observed request"}])
                messages = decisions[1]["messages"]
                self.assertTrue(any(m["role"] == "tool" and "accepted" in m["content"] for m in messages))
                receipt = reports[0]["steps"][0]["call_receipts"][0]
                self.assertEqual(receipt["output"]["handoff_id"], "request")
                self.assertEqual(receipt["host_transcript"][0]["text"], "Observed request")
                self.assertEqual(receipt["output"]["transcript_cut"], 1)
                self.assertEqual(summarize(reports)["routing_pass"], 2)
            finally:
                await runtime.close()


if __name__ == "__main__":
    unittest.main()
