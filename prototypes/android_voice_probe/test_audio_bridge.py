"""Run: uv run --script test_audio_bridge.py (stdlib only, no running device)."""
import struct
import tempfile
from pathlib import Path
import unittest
import wave

from audio_bridge import microphone_active, read_wav, signal_stats


class WavTests(unittest.TestCase):
    def make(self, *, channels=1, width=2, rate=16000, pcm=b"\x00\x00\xff\x7f"):
        path = Path(self.directory.name) / "test.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setparams((channels, width, rate, 0, "NONE", "not compressed"))
            wav.writeframes(pcm)
        return path

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def test_pcm16_preserved(self):
        pcm = struct.pack("<4h", -32768, 0, 32767, -1)
        self.assertEqual(read_wav(self.make(pcm=pcm)), (pcm, 1, 2, 16000))
        self.assertEqual(signal_stats(pcm)["nonzero_samples"], 3)
        self.assertEqual(signal_stats(pcm)["peak"], 1)

    def test_unsigned8_silence(self):
        pcm = bytes([128, 128, 128, 128])
        self.assertEqual(read_wav(self.make(width=1, channels=2, pcm=pcm)), (pcm, 2, 1, 16000))
        self.assertEqual(signal_stats(pcm, 1)["nonzero_samples"], 0)

    def test_reject_unsupported(self):
        for kwargs in ({"width": 3}, {"channels": 3}, {"rate": 96000}, {"pcm": b""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                read_wav(self.make(**kwargs))

    def test_truncated(self):
        path = self.make()
        path.write_bytes(path.read_bytes()[:-2])
        with self.assertRaisesRegex(ValueError, "Truncated"):
            read_wav(path)

    def test_partial_sample(self):
        with self.assertRaises(ValueError):
            signal_stats(b"\x00")

    def test_empty_stats(self):
        self.assertEqual(signal_stats(b"")["rms"], 0)

    def test_microphone_guard(self):
        active = "Input thread abc:\n  Standby: no\n  1 Tracks of which 1 are active\n"
        self.assertTrue(microphone_active(active))
        self.assertFalse(microphone_active(active.replace("1 are active", "0 are active")))
        self.assertFalse(microphone_active("Historical Thread Log\n" + active))
        self.assertFalse(microphone_active(active.replace("Standby: no", "Standby: yes")))


if __name__ == "__main__":
    unittest.main()
