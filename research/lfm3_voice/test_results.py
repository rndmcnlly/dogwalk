"""Public aggregation clocks and denominators, no inference."""
import json
from pathlib import Path
import tempfile
import unittest

from summarize_results import arm, cancelled_audio_tail_seconds


class ResultTests(unittest.TestCase):
    def test_cancel_tail_excludes_audio_from_the_following_response(self):
        rows = [
            {"sequence": 0, "direction": "send", "event": {"type": "response.cancel"}},
            {"sequence": 1, "direction": "receive", "event": {"type": "response.output_audio.delta", "delta": {"bytes": 4800}}},
            {"sequence": 2, "direction": "receive", "event": {"type": "response.done"}},
            {"sequence": 3, "direction": "receive", "event": {"type": "response.output_audio.delta", "delta": {"bytes": 48000}}},
        ]
        self.assertEqual(cancelled_audio_tail_seconds(rows), .1)

    def test_repetition_denominators_and_first_pcm_clock(self):
        step = {"expected": {"delegate": True, "silence": False}, "routing_pass": True, "status": "ok",
                "response": {"calls": [{"valid": True}], "first_audio_s": .3, "input_ready_s": .1},
                "decision_s": .8}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text(json.dumps({"reports": [{"repeat": i, "steps": [step]} for i in range(3)]}))
            self.assertEqual(arm(path)["points"], 3)
            self.assertEqual(arm(path, 1)["points"], 1)
            self.assertAlmostEqual(arm(path)["response_request_to_first_pcm_s"]["p95"], .2)
