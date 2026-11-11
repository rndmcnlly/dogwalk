"""Offline checks: uv run --with aiohttp python -m unittest test_frontend_eval.py."""
import json
import unittest
from pathlib import Path

from frontend_eval import DESIGNS, State, decide, messages, replay_event, run_event


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def infer(self, messages_, stage):
        self.calls.append((messages_, stage))
        value = next(self.responses)
        return value, {"ms": 5, "error": None if value else "ValueError"}


class StateTests(unittest.TestCase):
    def test_separate_channels_and_monotonic_revisions(self):
        state = State()
        state.ingest({"brief": "Birch writes docs."})
        state.ingest({"following": "Maple tests."})
        snap = state.snapshot()
        self.assertEqual([r["revision"] for r in snap["context"]], [1, 2])
        self.assertIn("Birch", str(snap["context"]))

    def test_interrupted_output_is_actual_prefix_and_request_survives(self):
        state = State()
        state.ingest({"say": ["Ask permission."]})
        ids = [p["id"] for p in state.pending]
        state.acknowledge({"action": "reply", "text": "One two three four five six."}, ids, .5)
        self.assertEqual(state.last_heard(), "One two three")
        self.assertEqual(len(state.pending), 1)
        self.assertEqual(state.history[-1]["playback"], "interrupted")

    def test_only_acknowledged_requests_are_removed(self):
        state = State()
        state.ingest({"say": ["Old request"]})
        ids = [p["id"] for p in state.pending]
        state.ingest({"say": ["New request"]})
        state.acknowledge({"action": "reply", "text": "Old result."}, ids)
        self.assertEqual([r["text"] for r in state.pending], ["New request"])

    def test_no_transcript_never_invents_a_user(self):
        state = State()
        state.ingest({"no_transcript": True})
        self.assertIsNone(state.user)
        self.assertEqual(state.history, [])

    def test_handoff_is_verbatim_and_no_caller_cannot_handoff(self):
        state = State()
        self.assertIsNone(state.acknowledge({"action": "handoff", "text": ""}, []))
        state.ingest({"user": "Cancel that, but KEEP the history."})
        state.acknowledge({"action": "handoff", "text": "I will delete it."}, [])
        self.assertEqual(state.forwarded, ["Cancel that, but KEEP the history."])
        self.assertEqual(state.last_heard(), "Let me think.")

    def test_history_has_a_deterministic_bound(self):
        state = State()
        for _ in range(100):
            state.remember({"role": "assistant", "text": "x" * 500})
        self.assertLessEqual(sum(len(r["text"]) for r in state.history), 6000)
        self.assertGreater(state.compactions, 0)

    def test_grading_metadata_is_not_model_input(self):
        corpus = json.loads(Path(__file__).with_name("frontend_eval_cases.json").read_text())
        self.assertEqual(len(corpus["isolated"]), 28)
        for event in corpus["isolated"]:
            state = State()
            state.ingest(event)
            for design in DESIGNS:
                content = json.dumps(messages(design, state.snapshot(), "test", "route"))
                for grade_key in ("critical_handoff", "max_words", "repeat_last_heard"):
                    self.assertNotIn(grade_key, content)

    def test_review_annotations_reference_real_cases(self):
        root = Path(__file__).parent
        corpus = json.loads((root / "frontend_eval_cases.json").read_text())
        review = json.loads((root / "frontend_eval_review.json").read_text())
        names = {c["name"] for c in corpus["isolated"]}
        names.update(f"{trace['name']}/{index}" for trace in corpus["traces"]
                     for index, event in enumerate(trace["events"]) if "expect" in event)
        for annotation in review["annotations"]:
            self.assertIn(annotation["case"], names)
            self.assertTrue(set(annotation["designs"]).issubset(DESIGNS))


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_announcement_bypasses_router_in_refinements(self):
        state = State()
        state.ingest({"say": ["The test failed."]})
        for design in ("priority_split", "event_single"):
            model = FakeModel([{"text": "The test failed."}])
            result, calls = await decide(model, design, state.snapshot(), "test")
            self.assertEqual(result["action"], "reply")
            self.assertEqual(len(calls), 1)
            self.assertEqual(model.calls[0][1], "phrase_plain")

    async def test_background_is_zero_inference(self):
        model = FakeModel([])
        state = State()
        event = {"brief": "Quiet context", "expect": {"action": "wait", "zero_calls": True}}
        row = await run_event(state, model, "baseline_split", "test", event, "idle", "test")
        self.assertEqual(row["issues"], [])
        self.assertEqual(model.calls, [])

    async def test_real_generated_speech_enters_next_context(self):
        state = State()
        model = FakeModel([{"text": "An actual generated response."}])
        event = {"say": ["Say something."], "expect": {"action": "reply"}}
        await run_event(state, model, "priority_split", "test", event, "first", "test")
        state.ingest({"user": "Repeat that."})
        self.assertIn("An actual generated response.", str(state.snapshot()["recent_transcript"]))
        self.assertNotIn("scripted heard", str(state.snapshot()))

    async def test_new_caller_invalidates_inflight_announcement(self):
        state = State()
        model = FakeModel([{"text": "Stale progress."}, {"action": "handoff"}])
        event = {"say": ["Share progress."], "during_generation": {"user": "Cancel it."},
                 "axes": ["race", "authorization"],
                 "expect": {"action": "handoff", "critical_handoff": True, "stale_discards": 1}}
        row = await run_event(state, model, "priority_split", "test", event, "race", "test")
        self.assertEqual(row["stale_discards"], 1)
        self.assertEqual(row["forwarded_transcript"], "Cancel it.")
        self.assertNotIn("Stale progress.", str(state.history))
        self.assertEqual(row["issues"], [])

    async def test_stop_during_generation_emits_nothing(self):
        state = State()
        model = FakeModel([{"text": "Must not be played."}])
        event = {"say": ["Share progress."], "during_generation": {"stop": True},
                 "expect": {"action": "wait", "stale_discards": 1, "no_playback": True}}
        row = await run_event(state, model, "event_single", "test", event, "stop", "test")
        self.assertIsNone(row["playback"])
        self.assertEqual(row["result"]["action"], "wait")
        self.assertEqual(len(model.calls), 1)

    async def test_event_single_routes_new_caller_in_one_call(self):
        state = State()
        state.ingest({"user": "Please cancel it."})
        model = FakeModel([{"action": "handoff", "text": ""}])
        result, calls = await decide(model, "event_single", state.snapshot(), "test")
        self.assertEqual(result["action"], "handoff")
        self.assertEqual(len(calls), 1)
        self.assertEqual(model.calls[0][1], "decision")

    async def test_baseline_announcement_handoff_does_not_crash_or_fabricate_turn(self):
        state = State()
        model = FakeModel([{"action": "handoff"}])
        event = {"say": ["Announce this."], "expect": {"action": "reply"}}
        row = await run_event(state, model, "baseline_split", "test", event, "announcement", "test")
        self.assertTrue(row["handoff_without_caller"])
        self.assertTrue(row["announcement_dropped"])
        self.assertIsNone(row["playback"])
        self.assertIsNone(row["forwarded_transcript"])

    async def test_resume_replays_actual_heard_state_without_inference(self):
        original = State()
        model = FakeModel([{"text": "The actual generated reply."}])
        event = {"say": ["Say this."], "interrupt_fraction": .5, "expect": {"action": "reply"}}
        row = await run_event(original, model, "event_single", "test", event, "case", "test")
        restored = State()
        replay_event(restored, event, row)
        self.assertEqual(original.snapshot(), restored.snapshot())
        self.assertEqual(original.epoch, restored.epoch)


if __name__ == "__main__":
    unittest.main()
