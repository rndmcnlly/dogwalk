# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Summarize an exported probe JSONL log without printing its transcripts.

uv run --script summarize.py /path/to/dogwalk-audio.jsonl
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import statistics


def summarize(lines):
    events = Counter()
    errors = Counter()
    starts = []
    partials = []
    reasons = []
    locked = None
    locked_finals = 0
    malformed = 0
    for line in lines:
        try:
            row = json.loads(line)
            event = row["event"]
            detail = row["detail"]
            if not isinstance(event, str) or not isinstance(detail, str):
                raise ValueError("Invalid event schema")
        except (ValueError, KeyError, TypeError):
            malformed += 1
            continue
        events[event] += 1
        if event == "start":
            locked = None
        if event == "screen":
            if detail.endswith("SCREEN_OFF"):
                locked = True
            elif detail.endswith("USER_PRESENT"):
                locked = False
        if event == "sample":
            # Screen-off is an approximation; sampled keyguard state is stronger evidence.
            match = re.search(r"locked=(true|false)", detail)
            if match:
                locked = match[1] == "true"
        if event == "final" and locked is True:
            locked_finals += 1
        if event == "recognition_error":
            errors[detail] += 1
        if event in ("tts_start", "first_partial"):
            key = "request_to_start_ms" if event == "tts_start" else "from_speech_begin_ms"
            match = re.search(rf"{key}=(-?\d+)", detail)
            if match and int(match[1]) >= 0:
                (starts if event == "tts_start" else partials).append(int(match[1]))
        if event == "fatal":
            reasons.append(detail)
    return {
        "events": dict(events), "recognition_errors": dict(errors),
        "final_transcripts_while_screen_off_or_locked": locked_finals,
        "median_tts_callback_start_ms": statistics.median(starts) if starts else None,
        "median_first_partial_callback_ms": statistics.median(partials) if partials else None,
        "fatal_reasons": reasons, "malformed_lines": malformed,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    with args.log.open() as source:
        print(json.dumps(summarize(source), indent=2))
