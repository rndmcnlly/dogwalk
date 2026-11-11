"""Offline per-request delivery tests, no inference/Android/production calls."""
import json
import unittest
from pathlib import Path

from frontend_requests import RequestSession, run_case, validate_plan


class FakeModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.hook = None
        self.calls = []

    async def infer(self, messages, stage, schema=None):
        result = next(self.replies)
        self.calls.append((messages, stage, schema))
        if stage == "phrase_plain" and self.hook:
            self.hook()
        return result, {"ms": 5, "error": None if result else "ValueError"}


class LedgerTests(unittest.TestCase):
    def session(self, design="atomic_fifo"):
        session = RequestSession(design)
        session.ingest({"say": ["First question?", "Second question?"]})
        return session

    def test_one_receipt_cannot_acknowledge_two_atomic_requests(self):
        session = self.session()
        with self.assertRaises(ValueError):
            session.queue_speech("Only the first question?", ["r01", "r02"])

    def test_completed_first_job_keeps_second_pending(self):
        session = self.session()
        key = session.queue_speech("First question?", ["r01"])
        session.finish(key)
        self.assertEqual(session.requests["r01"]["status"], "playback_complete")
        self.assertEqual(session.requests["r02"]["status"], "pending")
        self.assertEqual([r["id"] for r in session.pending], ["r02"])

    def test_interrupt_keeps_both_pending(self):
        session = self.session()
        key = session.queue_speech("Would you allow once, always allow, or deny?", ["r01"])
        session.finish(key, "interrupted", .4)
        self.assertEqual(len(session.pending), 2)
        self.assertEqual(session.requests["r01"]["heard_prefixes"], ["Would you allow"])

    def test_supersession_is_not_playback(self):
        session = self.session()
        session.apply_plan({"r01": "r02", "r02": None})
        self.assertEqual(session.requests["r01"]["status"], "superseded")
        self.assertEqual(session.requests["r02"]["status"], "pending")
        self.assertEqual(session.history, [])

    def test_old_callback_cannot_acknowledge_new_arrival(self):
        session = self.session()
        old = session.queue_speech("First question?", ["r01"])
        session.ingest({"say": ["A new question?"]})
        self.assertFalse(session.finish(old))
        self.assertEqual(len(session.pending), 3)
        self.assertEqual(session.requests["r03"]["status"], "pending")

    def test_duplicate_ack_is_idempotent(self):
        session = self.session()
        key = session.queue_speech("First question?", ["r01"])
        session.finish(key)
        self.assertFalse(session.finish(key))
        self.assertEqual(len(session.history), 1)

    def test_caller_reply_acknowledges_no_requests(self):
        session = self.session()
        key = session.queue_speech("Let me think.", [], "caller")
        session.finish(key)
        self.assertEqual(len(session.pending), 2)

    def test_zero_heard_audio_does_not_invent_a_prefix(self):
        session = self.session()
        key = session.queue_speech("These words were not heard.", ["r01"])
        session.finish(key, "interrupted", 0)
        self.assertEqual(session.history, [])
        self.assertEqual(session.requests["r01"]["heard_prefixes"], [])
        self.assertEqual(len(session.pending), 2)

    def test_supersession_invalidates_queued_receipt(self):
        session = self.session()
        old = session.queue_speech("Old status.", ["r01"])
        session.apply_plan({"r01": "r02", "r02": None})
        self.assertFalse(session.finish(old))
        self.assertEqual(session.history, [])
        self.assertEqual(session.requests["r01"]["status"], "superseded")
        self.assertEqual(session.requests["r02"]["status"], "pending")

    def test_stop_rejects_new_speech_and_late_ack(self):
        session = self.session()
        old = session.queue_speech("First question?", ["r01"])
        session.ingest({"stop": True})
        self.assertFalse(session.finish(old))
        with self.assertRaises(ValueError):
            session.queue_speech("Too late.", ["r02"])

    def test_plan_rejects_missing_unknown_duplicate_backward_self_ids(self):
        pending = self.session().pending
        invalid = [[], [{"id": "r01", "superseded_by": None}],
                   [{"id": "r01", "superseded_by": "r01"}, {"id": "r02", "superseded_by": None}],
                   [{"id": "r01", "superseded_by": None}, {"id": "r02", "superseded_by": "r01"}],
                   [{"id": "r01", "superseded_by": "r99"}, {"id": "r02", "superseded_by": None}],
                   [{"id": "r01", "superseded_by": None}, {"id": "r01", "superseded_by": None}]]
        for rows in invalid:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_plan({"decisions": rows}, pending)
        self.assertEqual(validate_plan({"decisions": [
            {"id": "r01", "superseded_by": "r02"}, {"id": "r02", "superseded_by": None}]}, pending),
                         {"r01": "r02", "r02": None})


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def cases(self):
        return json.loads(Path(__file__).with_name("frontend_request_cases.json").read_text())["cases"]

    async def test_atomic_two_questions_and_input_does_not_include_grades(self):
        model = FakeModel([{"text": "Would Maple allow once or deny?"},
                           {"text": "Should Birch target Python 3.11 or 3.12?"}])
        row = await run_case(model, "atomic_fifo", self.cases()[0], "test")
        self.assertTrue(all(c["covered"] for c in row["coverage"]))
        self.assertEqual(row["false_playback_completions"], [])
        self.assertEqual([r["request_ids"] for r in row["receipts"]], [["r01"], ["r02"]])
        self.assertNotIn("expected_superseded", str(model.calls))
        self.assertNotIn('"must"', str(model.calls))

    async def test_batch_reproduces_false_completion(self):
        model = FakeModel([{"text": "Would Maple allow once or deny?"}])
        row = await run_case(model, "batch_control", self.cases()[0], "test")
        self.assertEqual(row["false_playback_completions"], ["r02"])
        self.assertEqual([r["status"] for r in row["requests"]], ["playback_complete", "playback_complete"])

    async def test_planned_supersession_then_one_phrase(self):
        model = FakeModel([{"decisions": [{"id": "r01", "superseded_by": "r02"},
                                          {"id": "r02", "superseded_by": None}]},
                           {"text": "Maple's tests passed. The release was not deployed."}])
        row = await run_case(model, "atomic_planned", self.cases()[1], "test")
        self.assertEqual(row["unsafe_supersessions"], {})
        self.assertEqual(row["missed_supersessions"], {})
        self.assertEqual(row["requests"][0]["status"], "superseded")
        self.assertEqual(len(row["receipts"]), 1)

    async def test_invalid_plan_keeps_requests_no_fallback(self):
        model = FakeModel([{"decisions": []}])
        row = await run_case(model, "atomic_planned", self.cases()[0], "test")
        self.assertEqual(row["errors"], ["invalid_supersession_plan"])
        self.assertTrue(all(r["status"] == "pending" for r in row["requests"]))
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(row["receipts"], [])

    async def test_cancellation_during_phrase_does_not_clear_pending(self):
        case = next(c for c in self.cases() if c["name"] == "caller_cancels_during_phrase")
        model = FakeModel([{"text": "A stale plan."}, {"action": "handoff"}])
        row = await run_case(model, "atomic_fifo", case, "test")
        self.assertEqual(row["stale_discards"], 1)
        self.assertTrue(row["handoff_ok"])
        self.assertEqual(row["requests"][0]["status"], "pending")
        self.assertEqual([r["text"] for r in row["receipts"]], ["Let me think."])

    async def test_stop_during_phrase_no_output(self):
        case = next(c for c in self.cases() if c["name"] == "local_stop_during_phrase")
        model = FakeModel([{"text": "Must not be spoken."}])
        row = await run_case(model, "atomic_fifo", case, "test")
        self.assertTrue(row["race_ok"])
        self.assertTrue(row["stop_ok"])
        self.assertEqual(row["receipts"], [])

    async def test_small_selector_still_produces_explicit_supersession(self):
        model = FakeModel([{"replacement": "r02"},
                           {"text": "Maple's tests passed. No release was deployed."}])
        row = await run_case(model, "atomic_selected", self.cases()[1], "test")
        self.assertEqual(row["supersession"], {"r01": "r02"})
        self.assertEqual(row["unsafe_supersessions"], {})
        self.assertTrue(row["coverage"][0]["covered"])

    async def test_small_selector_unknown_reference_never_drops_requests(self):
        model = FakeModel([{"replacement": "r99"}])
        row = await run_case(model, "atomic_selected", self.cases()[0], "test")
        self.assertEqual(row["errors"], ["invalid_supersession_selection"])
        self.assertTrue(all(r["status"] == "pending" for r in row["requests"]))
        self.assertEqual(row["receipts"], [])


if __name__ == "__main__":
    unittest.main()
