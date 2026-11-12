import json
from pathlib import Path
import tempfile
import unittest

import analyze


class AnalysisTests(unittest.TestCase):
    def test_empty_quantiles_not_fabricated(self):
        self.assertIsNone(analyze.quantiles([]))
        self.assertEqual(analyze.quantiles([1, 3, 2])["p95"], 3)
        self.assertEqual(analyze.word_distance("Do not deploy.", "Do deploy.")["edits"], 1)
        self.assertIsNone(analyze.word_distance("test", None))

    def test_judge_cannot_drop_records_or_claim_to_have_listened(self):
        packet = {"scenarios": [{"steps": [{"record_id": "a"}]}]}
        record = {"record_id": "a", "routing": "pass", "semantics": "unresolved",
                  "critical_failure": False, "audio_evidence": "not_checked"}
        self.assertEqual(analyze.validate_judgment(packet, {"records": [record]})["records"], [record])
        with self.assertRaisesRegex(ValueError, "coverage"):
            analyze.validate_judgment(packet, {"records": []})
        with self.assertRaisesRegex(ValueError, "cannot claim listening"):
            analyze.validate_judgment(packet, {"records": [dict(record, audio_evidence="listened")]})

    def test_raw_proposals_not_host_acceptance_and_blind_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "run"
            run.mkdir()
            calls = [{"id": "observed", "name": "delegate", "arguments": {}, "valid": True}] * 2
            step = {"step_id": "quiet", "events": [{"kind": "brief", "text": "background"}],
                    "expected": {"delegate": False, "silence": True}, "status": "ok",
                    "response": {"calls": calls, "message": {"tool_calls": [{"id": "observed"}]}}, "raw_predicted_text": "I finished it.",
                    "host_suppression": "silence_gate", "synthesis_script": "", "decision_s": .3}
            (run / "secret_strategy-secret_mode-r1.json").write_text(json.dumps({"routing_pass": 0,
                "reports": [{"scenario_id": "synthetic", "repeat": 0, "steps": [step]}]}))
            output = root / "analysis"
            result = analyze.prepare([run], output)
            self.assertEqual(result["points"], 1)
            metrics = json.loads((output / "metrics.json").read_text())[0]
            self.assertEqual(metrics["false_handoff_points"], 1)
            self.assertEqual(metrics["duplicate_calls"], 1)
            self.assertEqual(metrics["raw_silence_violations"], 1)
            packet = (output / (metrics["arm_id"] + ".json")).read_text()
            self.assertNotIn("secret_strategy", packet)
            self.assertIn("I finished it.", packet)
            self.assertIn("silence_gate", packet)


if __name__ == "__main__":
    unittest.main()
