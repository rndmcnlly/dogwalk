"""Offline bake-off checks. uv run --with aiohttp python -m unittest this_file."""
import copy
import unittest

from frontend_bakeoff import (
    DESIGNS,
    all_cases,
    checkpoint_state,
    evaluate,
    layout,
    summarize,
    trace_cases,
)


class BakeoffTests(unittest.TestCase):
    def test_cases_never_include_grade_in_state(self):
        cases = all_cases()
        self.assertEqual(len(cases), 31)
        self.assertEqual(len({c["name"] for c in cases}), len(cases))
        for case in cases:
            self.assertNotIn("expected_action", str(case["state"]))
            for design in DESIGNS:
                messages = layout(design, case["state"], case["ledger"], "test")
                self.assertNotIn("expected_action", str(messages))
                self.assertNotIn("speech_required", str(messages))

    def test_ledger_retains_channels_snapshot_does_not(self):
        events = [{"brief": "Cedar writes docs."}, {"following": "Juniper builds."},
                  {"user": "What about Cedar?", "expected_action": "reply"}]
        case = next(trace_cases("test", events))
        self.assertEqual(case["state"]["latest_unspoken_context"], "Juniper builds.")
        self.assertIn("Cedar writes docs.", str(case["ledger"]))
        self.assertIn("Juniper builds.", str(case["ledger"]))

    def test_stable_system_prefix_does_not_include_changing_state(self):
        a, b = all_cases()[:2]
        for design in ("stable_tail", "split", "ledger"):
            self.assertEqual(layout(design, a["state"], a["ledger"], "same")[0],
                             layout(design, b["state"], b["ledger"], "same")[0])
        self.assertNotEqual(layout("compact", a["state"], a["ledger"], "same")[0],
                            layout("compact", b["state"], b["ledger"], "same")[0])

    def test_layout_does_not_modify_input(self):
        case = all_cases()[0]
        before = copy.deepcopy(case)
        for design in DESIGNS:
            layout(design, case["state"], case["ledger"], "same")
        self.assertEqual(case, before)

    def test_background_updates_skip_inference(self):
        self.assertEqual(list(trace_cases("test", [{"brief": "Quiet background."}])), [])

    def test_checkpoint_separates_current_caller_and_heard_history(self):
        case = next(c for c in all_cases() if c["name"] == "context_retention/2")
        frame = checkpoint_state(case["state"], case["ledger"])
        self.assertEqual(frame["current_user_utterance"], "What is Cedar working on?")
        self.assertEqual(frame["recent_transcript"], [])
        self.assertIn("Cedar", frame["latest_unspoken_context"]["brief"])
        self.assertIn("Juniper", frame["latest_unspoken_context"]["following"])

    def test_unknown_cache_counts_remain_unknown(self):
        row = {"design": "compact", "condition": "sequence", "failures": [],
               "delivered_failures": [], "safe_but_unhelpful_handoff": False, "ms": 10,
               "calls": [{"input_tokens": 12, "cached_tokens": None, "new_input_tokens": None,
                          "output_tokens": 3, "first_content_ms": 5}]}
        result = summarize([row])[0]
        self.assertIsNone(result["cache_fraction"])
        self.assertIsNone(result["new_input_tokens"])
        self.assertEqual(result["median_ms"], 10)


class EvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_split_does_not_phrase_handoff(self):
        class FakeModel:
            async def infer(self, messages, stage):
                self.stage = stage
                return {"action": "handoff"}, {"ms": 3}
        model = FakeModel()
        result = await evaluate(model, "split", all_cases()[0], "test")
        self.assertEqual(len(result["calls"]), 1)
        self.assertEqual(model.stage, "route")
        self.assertEqual(result["result"], {"action": "handoff", "text": ""})

    async def test_split_second_call_is_included(self):
        class FakeModel:
            async def infer(self, messages, stage):
                return ({"action": "reply"} if stage == "route" else
                        {"text": "The test failed. Should Juniper investigate?"}), {"ms": 3}
        result = await evaluate(FakeModel(), "split", all_cases()[1], "test")
        self.assertEqual(len(result["calls"]), 2)
        self.assertEqual(result["ms"], 6)
        self.assertEqual(result["delivered_failures"], [])

    async def test_invalid_output_has_no_fallback(self):
        class FakeModel:
            async def infer(self, messages, stage):
                return None, {"ms": 3}
        result = await evaluate(FakeModel(), "split", all_cases()[0], "test")
        self.assertEqual(len(result["calls"]), 1)
        self.assertEqual(result["delivered_failures"], ["invalid_output"])

    async def test_adapter_guard_is_not_hidden(self):
        class FakeModel:
            async def infer(self, messages, stage):
                return {"action": "handoff", "text": "I'll start that."}, {"ms": 3}
        result = await evaluate(FakeModel(), "compact", all_cases()[0], "test")
        self.assertEqual(result["failures"], ["nonreply_text"])
        self.assertEqual(result["delivered_failures"], [])


if __name__ == "__main__":
    unittest.main()
