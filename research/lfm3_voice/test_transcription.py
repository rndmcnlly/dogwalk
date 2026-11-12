"""Stdlib-only tests. No MLX import, model loading, or audio decoding.

    python3 -B -m unittest discover -s research/lfm3_voice -p test_transcription.py
"""

from dataclasses import dataclass
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import transcription as asr


@dataclass
class FakeResult:
    text: str
    sentences: list


class FakeRecognizer:
    def __init__(self):
        self.calls = []

    def transcribe(self, path, *, chunk_duration):
        self.calls.append((Path(path), chunk_duration))
        if Path(path).name == "bad.wav":
            raise ValueError("invalid audio")
        return FakeResult("Independently heard.", [])


class WordTests(unittest.TestCase):
    def test_subwords_contraction_and_punctuation(self):
        tokens = [dict(text=t, start=s, end=e) for t, s, e in [
            (" At", 0, .1), ("ten", .1, .2), ("tion", .2, .3), (",", .3, .6),
            (" can", .7, .8), ("’", .8, .81), ("t", .81, .9), (".", .9, 1.2),
        ]]
        words = asr.word_timestamps([{"tokens": tokens}])
        self.assertEqual([w["text"] for w in words], ["Attention", "can’t"])
        self.assertEqual([w["end"] for w in words], [.3, .9])
        self.assertEqual(words[0]["start"], 0)

    def test_shared_token_and_sentence_indices(self):
        words = asr.word_timestamps([
            {"tokens": [{"text": " two words!", "start": 1, "end": 2}]},
            {"tokens": [{"text": " Next", "start": 3, "end": 4}]},
        ])
        self.assertEqual([w["text"] for w in words], ["two", "words", "Next"])
        self.assertEqual([w["sentence_index"] for w in words], [0, 0, 1])
        self.assertEqual(words[0]["start"], words[1]["start"])
        self.assertEqual(asr.word_timestamps([]), [])
        self.assertEqual(asr.word_timestamps([{"tokens": [
            {"text": "...", "start": 0, "end": 1}]}]), [])


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / "asr"
        self.recognizer = FakeRecognizer()
        quiet = redirect_stderr(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self.loader = patch.object(asr, "load_recognizer", return_value=self.recognizer).start()
        self.addCleanup(patch.stopall)
        patch.object(asr, "version", return_value="test-version").start()

    def audio(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake waveform for stdlib testing")
        return path

    def test_one_load_errors_and_incremental_outputs(self):
        first, bad, last = [self.audio(n) for n in ("first.wav", "bad.wav", "last.wav")]
        original = self.recognizer.transcribe

        def check_saved(path, **kwargs):
            if Path(path) == last:
                self.assertTrue(asr.output_path(first, self.output).exists())
            return original(path, **kwargs)

        self.recognizer.transcribe = check_saved
        summary = asr.transcribe_batch([first, bad, last, last, self.root / "missing.wav"], self.output)
        self.loader.assert_called_once()
        self.assertEqual((summary["completed"], summary["errors"], summary["duplicates"]), (2, 2, 1))
        self.assertEqual(len(self.recognizer.calls), 3)
        self.assertEqual(json.loads((self.output / "summary.json").read_text()), summary)

    def test_resume_and_invalidate_content_settings_version_corruption(self):
        audio = self.audio("voice.wav")
        destination = asr.output_path(audio, self.output)
        asr.transcribe_batch([audio], self.output)
        self.loader.reset_mock()
        summary = asr.transcribe_batch([audio], self.output)
        self.assertEqual(summary["skipped"], 1)
        self.loader.assert_not_called()
        audio.write_bytes(b"changed audio")
        self.assertEqual(asr.transcribe_batch([audio], self.output)["completed"], 1)
        self.assertEqual(asr.transcribe_batch([audio], self.output, model="different")["completed"], 1)
        self.assertEqual(asr.transcribe_batch([audio], self.output, model="different",
                                            chunk_duration=60)["completed"], 1)
        with patch.object(asr, "version", return_value="new-version"):
            self.assertEqual(asr.transcribe_batch([audio], self.output, model="different",
                                                chunk_duration=60)["completed"], 1)
        destination.write_text("{broken")
        self.assertEqual(asr.transcribe_batch([audio], self.output)["completed"], 1)

    def test_paths_and_requested_text_not_used(self):
        a, b = self.audio("a/voice.wav"), self.audio("b/voice.wav")
        self.assertNotEqual(asr.output_path(a, self.output), asr.output_path(b, self.output))
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"files": [
            {"audio_path": "a/voice.wav", "requested_text": "DO NOT RECOGNIZE THIS"},
            "b/voice.wav",
        ]}))
        paths = asr.manifest_paths(manifest)
        self.assertEqual(paths, [a, b])
        asr.transcribe_batch(paths, self.output)
        result = json.loads(asr.output_path(a, self.output).read_text())
        self.assertEqual(result["text"], "Independently heard.")
        self.assertNotIn("DO NOT RECOGNIZE THIS", json.dumps(result))
        manifest.write_text('[{"audio": "a.wav", "path": "b.wav"}]')
        with self.assertRaises(ValueError):
            asr.manifest_paths(manifest)

    def test_load_failure_once_and_single_file_api(self):
        a, b = self.audio("a.wav"), self.audio("b.wav")
        self.loader.side_effect = RuntimeError("model unavailable")
        summary = asr.transcribe_batch([a, b], self.output)
        self.assertEqual(summary["errors"], 2)
        self.loader.assert_called_once()
        self.loader.side_effect = None
        self.assertEqual(asr.transcribe(a)["text"], "Independently heard.")

    def test_empty_batch_invalid_chunks_and_no_real_model_import(self):
        self.assertEqual(asr.transcribe_batch([], self.output)["completed"], 0)
        self.loader.assert_not_called()
        for duration in (0, 15, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                asr.transcribe_batch([], self.output, chunk_duration=duration)
        self.assertNotIn("parakeet_mlx", sys.modules)
        self.assertNotIn("mlx.core", sys.modules)

    def test_glob_cli_and_empty_selection(self):
        self.audio("clips/one.wav")
        self.audio("clips/two.wav")
        args = ["transcription.py", "--glob", str(self.root / "clips/*.wav"),
                "--output-dir", str(self.output)]
        with patch.object(sys, "argv", args), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                asr.main()
        self.assertEqual(stopped.exception.code, 0)
        self.loader.assert_called_once()
        self.assertEqual(json.loads((self.output / "summary.json").read_text())["completed"], 2)
        args[2] = str(self.root / "none/*.wav")
        with patch.object(sys, "argv", args):
            with self.assertRaises(SystemExit) as stopped:
                asr.main()
        self.assertEqual(stopped.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
