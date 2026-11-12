import json
import unittest

from summarize import summarize


class SummaryTests(unittest.TestCase):
    def report(self, rows):
        return summarize(json.dumps({"event": e, "detail": d}) for e, d in rows)

    def test_locked_transcripts_without_content(self):
        result = self.report([
            ("screen", "android.intent.action.SCREEN_OFF"), ("final", "private words"),
            ("sample", "interactive=true locked=false"), ("final", "other private words"),
        ])
        self.assertEqual(result["final_transcripts_while_screen_off_or_locked"], 1)
        self.assertNotIn("private words", json.dumps(result))

    def test_latencies_and_missing_callback(self):
        result = self.report([
            ("tts_start", "id=reply-1 request_to_start_ms=200"),
            ("tts_start", "id=reply-2 request_to_start_ms=400"),
            ("first_partial", "from_speech_begin_ms=-1 text=private"),
        ])
        self.assertEqual(result["median_tts_callback_start_ms"], 300)
        self.assertIsNone(result["median_first_partial_callback_ms"])

    def test_bad_log_and_errors(self):
        result = summarize(["not json", "[]", '{"event":1,"detail":2}',
                            '{"event":"recognition_error","detail":"NO_MATCH"}'])
        self.assertEqual(result["malformed_lines"], 3)
        self.assertEqual(result["recognition_errors"], {"NO_MATCH": 1})

    def test_new_run_resets_lock_assumption(self):
        result = self.report([("screen", "SCREEN_OFF"), ("start", "new run"), ("final", "text")])
        self.assertEqual(result["final_transcripts_while_screen_off_or_locked"], 0)


if __name__ == "__main__":
    unittest.main()
