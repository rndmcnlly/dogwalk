"""Pure offline protocol/WAV tests. No network, server, GPU, or model calls."""
import base64
import asyncio
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import wave

spec = importlib.util.spec_from_file_location("live_probe", Path(__file__).with_name("live_probe.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class WavTests(unittest.TestCase):
    def test_pcm_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.wav"
            pcm = struct.pack("<hhhh", -32768, 0, 16384, 32767)
            probe.save_wav(path, pcm, 16000)
            actual, info = probe.wav_to_pcm16(path)
            self.assertEqual(actual, pcm)
            self.assertEqual(info["rate"], 16000)

    def test_stereo_float_resample(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.wav"
            fmt = struct.pack("<HHIIHH", 3, 2, 8000, 64000, 8, 32)
            data = struct.pack("<ffff", 0.5, -0.5, 1, 1)
            body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
            path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
            pcm, info = probe.wav_to_pcm16(path)
            self.assertEqual(struct.unpack("<hhhh", pcm), (0, 16384, 32767, 32767))
            self.assertEqual(info["source_encoding"], 3)

    def test_truncated_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.wav"
            path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEdata\xff\xff\xff\xff")
            with self.assertRaises(ValueError):
                probe.wav_to_pcm16(path)

    def test_output_rate(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "out.wav"
            probe.save_wav(path, b"\x00\x00" * 240)
            with wave.open(str(path)) as f:
                self.assertEqual((f.getframerate(), f.getnchannels(), f.getsampwidth(), f.getnframes()), (24000, 1, 2, 240))


class ProtocolTests(unittest.TestCase):
    def test_audio_redaction_preserves_text(self):
        b64 = base64.b64encode(b"\x00\x01" * 100).decode()
        event = {"type": "response.output_audio.delta", "delta": b64, "response_id": "r1"}
        meta = probe.event_metadata(event)
        self.assertEqual(meta["delta"]["bytes"], 200)
        self.assertNotIn(b64, json.dumps(meta))
        self.assertEqual(event["delta"], b64)
        text = {"type": "response.output_text.delta", "delta": "hello"}
        self.assertEqual(probe.event_metadata(text), text)

    def test_input_and_nested_audio_redaction(self):
        audio = base64.b64encode(b"12").decode()
        meta = probe.event_metadata({"type": "input_audio_buffer.append", "audio": audio})
        self.assertEqual(meta["audio"]["bytes"], 2)

    def test_generic_tool_and_prefix(self):
        self.assertEqual(probe.PREFIX, "Respond with function calls, and interleaved text and audio.\n\n")
        self.assertEqual(probe.TOOLS[0]["name"], "delegate")
        self.assertEqual(probe.TOOLS[0]["parameters"]["properties"], {})

    def test_recorder_no_base64_and_wav(self):
        with tempfile.TemporaryDirectory() as d:
            rec = probe.Recorder(None, Path(d), 1)
            rec.record("receive", {"type": "response.output_audio.delta", "response_id": "r",
                                  "delta": base64.b64encode(b"\x00\x00").decode()})
            artifacts = rec.close()
            self.assertEqual(artifacts[0]["bytes"], 2)
            self.assertTrue((Path(d) / "response-00.wav").exists())
            row = json.loads((Path(d) / "events.jsonl").read_text())
            self.assertEqual(row["event"]["delta"]["bytes"], 2)
            self.assertGreater(row["wall_time_ns"], 0)


class FakeSocket:
    def __init__(self, events):
        self.events = list(events)
        self.sent = []

    async def recv(self):
        if self.events:
            return json.dumps(self.events.pop(0))
        await asyncio.Future()

    async def send(self, message):
        self.sent.append(json.loads(message))


class AsyncProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_error_not_success_ack(self):
        with tempfile.TemporaryDirectory() as d:
            ws = FakeSocket([{"type": "error", "error": {"message": "instructions cannot change"}},
                             {"type": "session.updated", "session": {}}])
            rec = probe.Recorder(ws, Path(d), 0.05)
            try:
                with self.assertRaises(probe.Rejected):
                    await rec.exchange({"type": "session.update"}, "session.updated")
            finally:
                rec.close()

    async def test_deadline_and_response_lifecycle(self):
        with tempfile.TemporaryDirectory() as d:
            ws = FakeSocket([{"type": "response.created", "response": {"id": "r"}},
                             {"type": "response.done", "response": {"id": "r", "status": "cancelled"}}])
            rec = probe.Recorder(ws, Path(d), 0.01)
            try:
                await rec.until(lambda e: e["type"] == "response.created")
                self.assertTrue(rec.responding)
                await rec.until(lambda e: e["type"] == "response.done")
                self.assertFalse(rec.responding)
                with self.assertRaises(probe.ProbeTimeout):
                    await rec.until(lambda e: False)
            finally:
                rec.close()


if __name__ == "__main__":
    unittest.main()
