"""Run with: uv run --with numpy python -m unittest interaction_helpers_test.py"""
import contextlib
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

import interaction_test as interaction
from interaction_test import (
    RATE,
    attach_analysis,
    digest,
    load_audio,
    normalized,
    packet_layout,
    place,
    save_audio,
    voice_stop_completed,
)


class TimelineTests(unittest.TestCase):
    def test_static_ui_controls_need_only_one_dump(self):
        xml = ('<hierarchy><node text="Stop" bounds="[0,0][20,20]"/>'
               '<node text="Start local audio" bounds="[20,0][40,20]"/></hierarchy>')
        def fake_adb(*args):
            return xml if args[1] == "cat" else ""
        with patch.dict(interaction.BUTTONS, {}, clear=True), patch.object(interaction, "adb", side_effect=fake_adb) as command:
            interaction.tap("Stop")
            interaction.tap("Start local audio")
            dumps = [c for c in command.call_args_list if c.args[1] == "uiautomator"]
            self.assertEqual(len(dumps), 1)
            self.assertEqual(command.call_args_list[-1].args, ("shell", "input", "tap", 30, 10))

    def test_placement_and_pan(self):
        target = np.zeros((8, 2), np.float32)
        place(target, np.ones(3), 2, (.8, .6))
        np.testing.assert_allclose(target[2:5], [[.8, .6]] * 3)
        self.assertFalse(target[:2].any())
        self.assertFalse(target[5:].any())

    def test_overlap_adds_without_time_edit(self):
        target = np.zeros((6, 2), np.float32)
        place(target, np.ones(3), 1)
        place(target, np.ones((2, 2)), 2)
        np.testing.assert_array_equal(target[:, 0], [0, 1, 2, 2, 0, 0])

    def test_reject_outside_timeline(self):
        for start in (-1, 3):
            with self.assertRaises(ValueError):
                place(np.zeros((4, 2)), np.ones(2), start)

    def test_pcm_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.wav"
            save_audio(path, np.full((480, 2), .25, np.float32))
            with wave.open(str(path)) as wav:
                self.assertEqual((wav.getnchannels(), wav.getframerate()), (2, RATE))
            np.testing.assert_allclose(load_audio(path), .25, atol=1 / 32768)

    def test_normalized_words(self):
        self.assertEqual(normalized("Please remember the blue umbrella!"), "please remember the blue umbrella")

    def test_review_resampling_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mono.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(np.full(160, 4096, dtype="<i2").tobytes())
            audio = load_audio(path)
            self.assertEqual(len(audio), 480)
            np.testing.assert_allclose(audio, .125)

    def test_packets_keep_all_frames_and_idle_gap(self):
        packets = [{"start": .01, "duration": .01},
                   {"start": .021, "duration": .01},
                   {"start": .5, "duration": .01}]
        self.assertEqual(packet_layout(packets), [(0, 480), (480, 480), (23520, 480)])

    def test_packet_jitter_never_duplicates_or_drops_pcm(self):
        packets = [{"start": .011, "duration": .01},
                   {"start": .019, "duration": .01},
                   {"start": .031, "duration": .01}]
        layout = packet_layout(packets)
        self.assertEqual(sum(size for _, size in layout), 1440)
        self.assertEqual(layout[1][0], layout[0][0] + layout[0][1])
        self.assertEqual(layout[2][0], layout[1][0] + layout[1][1])

    def test_old_ui_stop_is_not_spoken_stop(self):
        rows = [{"event": "stop"}, {"event": "start"}, {"event": "final"}]
        self.assertFalse(voice_stop_completed(rows))
        rows.append({"event": "voice_stop"})
        self.assertFalse(voice_stop_completed(rows))
        rows.append({"event": "stop"})
        self.assertTrue(voice_stop_completed(rows))

    def test_analysis_attaches_by_hash_without_assigning_speakers(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            asr = output / "asr"
            asr.mkdir()
            save_audio(output / "review.wav", np.zeros((480, 2)))
            (output / "timeline.json").write_text("{}")
            (output / "review.html").write_text("<h1>Review</h1>")
            (asr / "inputs.json").write_text(json.dumps({"model": "test", "package": "test",
                                                        "reference_text_supplied": False}))
            (asr / "review.json").write_text("{}")
            evidence = {"audio": {"sha256": digest(output / "review.wav")},
                        "words": [{"word": "Hello", "start": .01, "end": .1}]}
            (asr / "review.words.json").write_text(json.dumps(evidence))
            with contextlib.redirect_stdout(io.StringIO()):
                attach_analysis(output, asr)
            analysis = json.loads((output / "timeline.json").read_text())["independent_speech_analysis"]
            self.assertEqual(analysis["words"], evidence["words"])
            self.assertFalse(analysis["reference_text_supplied"])
            self.assertNotIn("role", analysis["words"][0])
            evidence["audio"]["sha256"] = "stale"
            (asr / "review.words.json").write_text(json.dumps(evidence))
            with self.assertRaises(ValueError):
                attach_analysis(output, asr)


if __name__ == "__main__":
    unittest.main()
