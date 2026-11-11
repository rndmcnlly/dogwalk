"""Offline context-layout and evaluation tests: no provider or Android needed."""
import unittest

from frontend_context import (
    COMPACT_PROMPT,
    build_messages,
    check_decision,
    scenario_state,
)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "recent_transcript": [
                {"role": "assistant", "text": "Juniper found", "playback": "interrupted"},
                {"role": "user", "text": "Stop that and inspect the error."}],
            "latest_unspoken_context": "Build is running.",
            "speech_requests": [{"id": "a", "text": "Tell the caller the build is running."}],
            "new_user_utterance": True, "trigger": "user_utterance"}

    def test_canonical_layout_unchanged(self):
        messages = build_messages("original", "mapping", self.state, additions="extra")
        self.assertEqual(messages[0], {"role": "system", "content": "original\nextramapping"})
        self.assertEqual(len(messages), 2)
        self.assertIn('"recent_transcript"', messages[1]["content"])

    def test_roles_preserve_user_text_and_authority_separation(self):
        messages = build_messages("original", "mapping", self.state, "roles")
        self.assertEqual([m["role"] for m in messages], ["system", "assistant", "user"])
        self.assertEqual(messages[-1]["content"], self.state["recent_transcript"][-1]["text"])
        self.assertIn('"playback": ["interrupted"]', messages[0]["content"])
        self.assertNotIn("Build is running", messages[-1]["content"])

    def test_compact_is_explicit_plain_prompt_experiment(self):
        messages = build_messages("Park original", "mapping", self.state, "compact", "extra")
        self.assertTrue(messages[0]["content"].startswith(COMPACT_PROMPT))
        self.assertNotIn("Park original", messages[0]["content"])
        self.assertIn("extra", messages[0]["content"])

    def test_background_event_does_not_replay_old_user_as_new(self):
        self.state["new_user_utterance"] = False
        messages = build_messages("original", "mapping", self.state, "compact")
        self.assertIn("No new caller utterance", messages[-1]["content"])
        self.assertEqual(messages[-2]["content"], "Stop that and inspect the error.")

    def test_expected_answers_not_model_input(self):
        import json
        from pathlib import Path
        cases = json.loads(Path(__file__).with_name("frontend_context_checks.json").read_text())
        self.assertEqual(len(cases), 12)
        self.assertEqual(len({case["name"] for case in cases}), 12)
        for case in cases:
            messages = build_messages("original", "mapping", scenario_state(case), "compact")
            self.assertNotIn("expected_action", messages[0]["content"])
            self.assertNotIn("speech_required", messages[0]["content"])
            self.assertNotIn("speech_forbidden", messages[0]["content"])

    def test_grading_catches_action_only_false_positive(self):
        case = {"expected_action": "handoff"}
        self.assertEqual(check_decision({"action": "handoff", "text": "I'll start."}, case),
                         ["nonreply_text"])
        case = {"expected_action": "reply", "speech_required": ["deny"],
                "speech_forbidden": ["Prompt Turn"]}
        failures = check_decision({"action": "reply", "text": "The Prompt Turn is running."}, case)
        self.assertEqual(len(failures), 3)

    def test_unknown_profile_fails_closed(self):
        with self.assertRaises(ValueError):
            build_messages("original", "mapping", self.state, "typo")


if __name__ == "__main__":
    unittest.main()
