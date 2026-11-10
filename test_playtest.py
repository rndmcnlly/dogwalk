"""Local report tests: python -m unittest test_playtest."""

import json
import unittest

from playtest import event_ms, stats_lines, timing_lines


def row(kind, ts_ms, **data):
    return {"event": kind, "ts": ts_ms // 1000,
            "detail": json.dumps({"ts_ms": ts_ms, **data})}


class TimingTests(unittest.TestCase):
    def test_handoff_latency_preserves_subsecond_precision(self):
        call = {"started_at": 1, "activity": [
            row("voice.delegation.received", 1105, delegation_id="d1"),
            row("orchestrator.say", 1120, delegation_id="d1", ok=False),
            row("orchestrator.say", 1268, delegation_id="d1", ok=True),
        ]}
        self.assertIn("163ms", "\n".join(stats_lines(call, [])))

    def test_trace_uses_observation_not_batch_insertion_order(self):
        call = {"started_at": 1, "activity": [
            row("orchestrator.say", 1268),
            row("voice.timing", 1105, event="live.received")
        ]}
        lines = timing_lines(call)
        self.assertIn("live.received", lines[2])
        self.assertIn("+0000000105ms", lines[2])
        self.assertIn("orchestrator.say", lines[3])

    def test_historical_precision_is_explicit(self):
        old = {"event": "old", "ts": 2, "detail": "{}"}
        self.assertEqual(event_ms(old), 2000)
        self.assertIn("[seconds only]", timing_lines({"started_at": 1, "activity": [old]})[-1])


if __name__ == "__main__":
    unittest.main()
