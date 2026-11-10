#!/usr/bin/env python3
"""Offline TwiML contract proof. No network, credentials, or live changes."""

import random
import unittest
from xml.etree import ElementTree as ET

ROUNDS = 6
SUBJECTS = ("The small fox", "A blue bird", "The young rabbit", "A quiet dog")
VERBS = ("waited beside", "walked around", "looked beyond", "rested near")
OBJECTS = ("the wooden bridge", "a yellow gate", "the garden wall", "a silver bicycle")


def phrases(rng=None):
    """Labels, not passwords or authentication challenges."""
    rng = rng or random.SystemRandom()
    candidates = [f"{s} {v} {o}." for s in SUBJECTS for v in VERBS for o in OBJECTS]
    return rng.sample(candidates, ROUNDS)


def document(labels, round_index, origin="https://collector.example.invalid"):
    """Endpoint adapter persists labels BEFORE returning each document.

    POST /voice starts round 0. POST /next/<i> returns round i.
    POST /recording/<i> records async readiness, never advances the call.
    Both callbacks use signed CallSid plus round index to find private state.
    """
    if len(labels) != ROUNDS or not 0 <= round_index <= ROUNDS:
        raise ValueError("Expected six labels and round index 0 through 6")
    response = ET.Element("Response")
    if round_index == ROUNDS:
        ET.SubElement(response, "Say", voice="man").text = "Collection complete. Goodbye."
        ET.SubElement(response, "Hangup")
    else:
        if round_index == 0:
            ET.SubElement(response, "Say", voice="man").text = (
                "This call collects six samples of your voice for an experiment. "
                "Hang up now if you do not want to be recorded. "
                "After each beep, repeat the sentence, then pause. "
                "These sentences are not passwords."
            )
            ET.SubElement(response, "Pause", length="3")
        ET.SubElement(response, "Say", voice="man").text = (
            f"Sentence {round_index + 1}. {labels[round_index]}"
        )
        ET.SubElement(
            response, "Record",
            action=f"{origin}/next/{round_index + 1}", method="POST",
            recordingStatusCallback=f"{origin}/recording/{round_index}",
            recordingStatusCallbackMethod="POST",
            recordingStatusCallbackEvent="completed absent",
            timeout="3", maxLength="12", playBeep="true",
            trim="do-not-trim", finishOnKey="", transcribe="false",
        )
    return ET.tostring(response, encoding="unicode")


class FlowTests(unittest.TestCase):
    def test_six_unique_nonsecret_labels(self):
        self.assertEqual(len(set(phrases(random.Random(1)))), ROUNDS)

    def test_six_separate_recordings_then_hangup(self):
        labels = phrases(random.Random(1))
        for i in range(ROUNDS):
            root = ET.fromstring(document(labels, i))
            record = root.find("Record")
            self.assertEqual(record.attrib["action"], f"https://collector.example.invalid/next/{i + 1}")
            self.assertEqual(record.attrib["trim"], "do-not-trim")
            self.assertEqual(record.attrib["finishOnKey"], "")
            self.assertIs(root[-1], record)  # Verbs after Record are unreachable.
        final = ET.fromstring(document(labels, ROUNDS))
        self.assertIsNone(final.find("Record"))
        self.assertIsNotNone(final.find("Hangup"))

    def test_retry_renders_identical_document(self):
        labels = phrases(random.Random(2))
        self.assertEqual(document(labels, 2), document(labels, 2))


if __name__ == "__main__":
    unittest.main()
