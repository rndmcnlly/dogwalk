"""CPU-only checks for round-two scoring logic. No inference."""

import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import composite  # noqa: E402
import router_organ as router  # noqa: E402
import speech_organ as speech  # noqa: E402


def lp(token, p):
    return {"token": token, "logprob": math.log(p)}


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.points = router.caller_points(json.loads((ROOT / "scenarios.json").read_text()))

    def test_caller_points_match_frozen_denominator(self):
        self.assertEqual(len(self.points), 25)
        self.assertEqual(sum(p["handoff"] for p in self.points), 16)
        classes = {p["point_id"]: p["class"] for p in self.points}
        self.assertEqual(classes["stop_silence_resume/stop"], "silence")
        self.assertEqual(classes["stop_silence_resume/resume"], "resume")
        self.assertEqual(sum(p["critical"] for p in self.points), 5)

    def test_snapshot_holds_muted_commentary_as_fact_not_ledger(self):
        resume = next(p for p in self.points if p["point_id"] == "stop_silence_resume/resume")
        text = router.render_snapshot(resume["snapshot"])
        self.assertIn("Held update, not yet spoken: Cedar's tests passed", text)
        self.assertNotIn("assistant: Cedar's tests passed", text)
        self.assertIn("speech is muted", text)

    def test_pending_handoff_cleared_by_result(self):
        recall = next(p for p in self.points if p["point_id"] == "pending_correction/recall")
        self.assertEqual(recall["snapshot"]["pending_handoffs"], ["Caller turn handed off, no result yet: \"Ask the agent to change the checkout button to blue.\""])

    def test_class_probs_prefix_and_normalization(self):
        labels = router.FRAMINGS["multiway"]["labels"]
        out = router.class_probs([lp("work", 0.6), lp("sil", 0.2), lp("res", 0.1), lp("<think>", 0.1)], labels)
        self.assertAlmostEqual(out["probs"]["work"], 0.6 / 0.9)
        self.assertAlmostEqual(out["covered_mass"], 0.9)
        yes = router.class_probs([lp("yes", 0.3), lp("No", 0.6), lp(" no", 0.1)], router.FRAMINGS["yesno_rules"]["labels"])
        self.assertAlmostEqual(yes["probs"]["work"], 0.3)

    def test_single_letter_tokens_do_not_count(self):
        out = router.class_probs([lp("s", 0.5), lp("work", 0.5)], router.FRAMINGS["multiway"]["labels"])
        self.assertAlmostEqual(out["probs"]["work"], 1.0)

    def test_operating_point_keeps_every_positive(self):
        scored = [(0.9, True), (0.4, True), (0.5, False), (0.3, False), (0.95, False)]
        op = router.operating_point(scored)
        self.assertEqual(op["threshold"], 0.4)
        self.assertEqual(op["false_handoffs"], 2)
        self.assertAlmostEqual(op["margin"], 0.4 - 0.95)
        top = router.curve(scored)[0]
        self.assertEqual((top["threshold"], top["tp"], top["fp"]), (0.95, 0, 1))

    def test_selection_prefers_fewer_false_then_margin(self):
        cell = lambda fp, m: {"operating_point": {"false_handoffs": fp, "margin": m}}
        cells = {"a|x": cell(1, -0.1), "a|y": cell(1, -0.1), "b|x": cell(1, 0.0), "b|y": cell(1, 0.0), "c|x": cell(0, -0.9), "c|y": cell(3, -0.9)}
        self.assertEqual(router.select(cells), "b")


class SpeechTests(unittest.TestCase):
    def test_spans_and_plain(self):
        script = "[[Permission was denied]]. Thanks."
        self.assertEqual(speech.spans(script), ["Permission was denied"])
        self.assertEqual(speech.plain(script), "Permission was denied. Thanks.")

    def test_span_wer_numbers_compounds_and_negation(self):
        self.assertEqual(speech.span_wer("Two of seven tests still fail", "2 of 7 tests still fail."), 0)
        self.assertEqual(speech.span_wer("Cedar is working on the timezone test", "Cedar is working on the time zone test."), 0)
        self.assertGreater(speech.span_wer("No deployment occurred", "Mode deployment occurred."), 0)
        self.assertEqual(speech.span_wer("reject", "allow once, or reject"), 0)

    def test_text_check_catches_dropped_choice_and_added_negation(self):
        script = "Choices: [[allow this once]] or [[reject]]."
        self.assertFalse(speech.text_check(script, "Choices: reject.")["pass"])
        bad = speech.text_check("[[Cedar is working]].", "Cedar is working, not done.")
        self.assertIn("not", bad["glue_forbidden"])
        ok = speech.text_check("Cedar asks to [[stop]].", "Okay, Cedar asks to stop.")
        self.assertTrue(ok["pass"], ok)

    def test_score_falls_back_to_verbatim(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            records = [{"case_id": "x", "arm": "verbatim_tts_male", "script": "[[No tests ran]].", "audio_path": None},
                       {"case_id": "x", "arm": "native_glue", "script": "[[No tests ran]].", "status": "completed",
                        "calls": [], "predicted_text": "No tests ran.", "audio_path": None}]
            records[0]["audio_check"] = None
            summary = speech.score(records, Path(tmp))
            self.assertEqual(summary["glue_after_host"]["fallbacks"], 1)  # no recognition: never trusted


class CompositeTests(unittest.TestCase):
    def test_score_separates_host_and_router_points(self):
        records = [
            {"point_id": "a", "gold_handoff": False, "gold_silence": True, "outputs": [], "decided_by": ["host:brief_unspoken"]},
            {"point_id": "b", "gold_handoff": True, "gold_silence": False, "handoff": True,
             "outputs": [{"first_pcm_after_caller_s": 0.2}], "decided_by": ["router:work", "host:ack"], "router": {"elapsed_s": 0.1}},
            {"point_id": "c", "gold_handoff": False, "gold_silence": True, "handoff": False,
             "outputs": [{"first_pcm_after_caller_s": 0.3}], "decided_by": ["router:social"], "router": {"elapsed_s": 0.1}},
        ]
        s = composite.score(records, None)
        self.assertEqual(s["routing_correct"], 3)
        self.assertEqual(s["silence_violations"], ["c"])
        self.assertEqual((s["host_only_points"], s["router_points"]), (1, 2))


if __name__ == "__main__":
    unittest.main()
