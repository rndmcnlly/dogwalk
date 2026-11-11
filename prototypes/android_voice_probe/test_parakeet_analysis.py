# /// script
# dependencies = ["numpy", "soundfile", "scipy"]
# ///
"""Run: uv run --script test_parakeet_analysis.py"""
import unittest

from parakeet_analysis import word_spans


class WordSpanTests(unittest.TestCase):
    def test_subwords_and_late_punctuation(self):
        tokens = [dict(text=t, start=s, end=e) for t, s, e in [
            (' At', .1, .2), ('ten', .2, .3), ('tion', .3, .4), (',', .4, 1.0),
            (' can', 1.1, 1.2), ("'", 1.2, 1.3), ('t', 1.3, 1.4), ('.', 1.4, 2.0)]]
        actual = word_spans(dict(sentences=[dict(tokens=tokens)]))
        self.assertEqual([w['word'] for w in actual], ['Attention', "can't"])
        self.assertEqual([(w['start'], w['end']) for w in actual], [(.1, .4), (1.1, 1.4)])

    def test_empty(self):
        self.assertEqual(word_spans(dict(text='', sentences=[])), [])

    def test_unicode_and_punctuation_only(self):
        tokens = [dict(text=' café', start=.1, end=.3), dict(text='!', start=.3, end=1)]
        self.assertEqual(word_spans(dict(sentences=[dict(tokens=tokens)])),
                         [dict(word='café', start=.1, end=.3, sentence=0)])


if __name__ == '__main__':
    unittest.main()
