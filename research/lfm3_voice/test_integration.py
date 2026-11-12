"""Fixture integrity and grader-leakage integration checks. CPU only."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

import strategies
import heldout_cases
from harness import Backend
from validate_suite import validate_suite


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.suite = json.loads(Path(__file__).with_name("scenarios.json").read_text())

    def test_frozen_suite_counts(self):
        counts = validate_suite(self.suite)
        self.assertEqual(counts["scenarios"], 13)
        self.assertEqual(counts["points"], 40)
        self.assertEqual(counts["positive"] + counts["negative"], 40)
        self.assertEqual(counts["telephone_points"], 2)

    def test_heldout_negatives_have_separate_denominator(self):
        counts = validate_suite(heldout_cases.suite(), expected_points=12)
        self.assertEqual(counts["negative"], 12)
        self.assertEqual(counts["positive"], 0)

    def test_handoff_cut_is_not_mutated_by_later_transcript_edits(self):
        backend = Backend()
        transcript = [{"role": "caller", "text": "Do not deploy."}]
        receipt = backend.accept({"name": "delegate", "arguments": {}}, transcript, 0)
        transcript[0]["text"] = "Changed later."
        transcript.append({"role": "caller", "text": "A later correction."})
        cut = backend.pending[receipt["receipt_id"]]["transcript"]
        self.assertEqual(cut, [{"role": "caller", "text": "Do not deploy."}])
        self.assertEqual(receipt["transcript_cut"], 1)

    def test_unresolved_or_same_step_handoff_rejected(self):
        for ref in ("nonexistent", "answer"):
            with self.subTest(ref=ref):
                broken = deepcopy(self.suite)
                broken["scenarios"][1]["steps"][1]["events"][0]["handoff"] = ref
                with self.assertRaisesRegex(ValueError, "earlier Handoff"):
                    validate_suite(broken)

    def test_silence_cannot_require_speech(self):
        broken = deepcopy(self.suite)
        broken["scenarios"][0]["steps"][0]["expect"]["silence"] = True
        with self.assertRaisesRegex(ValueError, "silence requires speech"):
            validate_suite(broken)

    def test_every_caller_fixture_excludes_reference_in_native_arm(self):
        for scenario in self.suite["scenarios"]:
            for step in scenario["steps"]:
                for event in step["events"]:
                    if event["kind"] != "caller":
                        continue
                    # A fake payload is sufficient: builders must never decode it.
                    caller = {**event, "audio": {"data": "UklGRg==", "format": "wav"},
                              "oracle_text": event["text"], "lfm_stt": "ASR_ONLY_SENTINEL",
                              "expect": "GRADER_ONLY_SENTINEL"}
                    before = deepcopy(caller)
                    for strategy in strategies.STRATEGIES:
                        with self.subTest(scenario=scenario["id"], step=step["id"], strategy=strategy):
                            serialized = json.dumps(strategies.build(strategy, [], caller))
                            self.assertNotIn(event["text"], serialized)
                            self.assertNotIn("ASR_ONLY_SENTINEL", serialized)
                            self.assertNotIn("GRADER_ONLY_SENTINEL", serialized)
                            self.assertIn("UklGRg==", serialized)
                    self.assertEqual(caller, before)


if __name__ == "__main__":
    unittest.main()
