# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Render spoken earcons with macOS text-to-speech into the Worker's bundle.

    uv run --script make_earcons.py                 # Fred, the default
    uv run --script make_earcons.py --voice Zarvox  # any `say -v '?'` voice

Each label becomes cf_worker/earcons/<event>.pcm: raw mono PCM16LE at 24 kHz, leveled
a little below speech, ready for a GPT-Live output fixture. Keypad menu prompts render
the same way, a little slower and louder. Rendering happens here
because a Worker cannot run `say`; the files are committed and bundled as Data modules.
Event names are neutral; the spoken labels are presentation and may use the walk's fiction.
"""

from __future__ import annotations

import argparse
import array
import math
import subprocess
import tempfile
import wave
from pathlib import Path

OUT = Path(__file__).resolve().parent / "cf_worker" / "earcons"

LABELS = {
    "call_started": "Line open.",
    "session_created": "New dog.",
    "turn_started": "Dog sent.",
    "turn_stopped": "Dog back.",
    "turn_failed": "Errand failed.",
    "dogs_ready": "Dogs awake.",
    "turn_cancelled": "Dog recalled.",
    "turn_truncated": "Dog ran out of room.",
    "turn_refused": "Dog refused.",
    "permission_requested": "Permission needed.",
    "session_closed": "Dog home.",
    "recording_started": "Recording.",
    "recording_saved": "Recording saved.",
    "recording_discarded": "Recording discarded.",
}

# Keypad system menu prompts: always bundled regardless of EARCON_STYLE, because they are
# instructions the caller must understand rather than event markers. Slower and louder.
PROMPTS = {
    "menu": "Menu. One, status. Two, restart the sandbox. Three, recovery options. Nine, hang up. Star, back.",
    "menu_restart_confirm": "Restarting stops running work. Press two again to confirm.",
    "menu_restarting": "Restarting the sandbox.",
    "menu_back": "Back.",
}

TARGET_RMS = 0.06  # about -24 dBFS: audible, but quieter than the walker's voice
PROMPT_RMS = 0.09  # about -21 dBFS: closer to speech, since the caller must follow it


def render(text: str, voice: str, rate: int, target_rms: float = TARGET_RMS) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        aiff, wav = Path(tmp) / "label.aiff", Path(tmp) / "label.wav"
        subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1", str(aiff), str(wav)], check=True)
        with wave.open(str(wav)) as w:
            samples = array.array("h", w.readframes(w.getnframes()))
    # Trim leading and trailing near-silence so the clip starts immediately.
    threshold = 300
    start = next((i for i, s in enumerate(samples) if abs(s) > threshold), 0)
    end = len(samples) - next((i for i, s in enumerate(reversed(samples)) if abs(s) > threshold), 0)
    samples = samples[max(0, start - 240): min(len(samples), end + 1200)]
    rms = math.sqrt(sum(s * s for s in samples) / max(1, len(samples))) / 32768
    gain = min(target_rms / rms if rms else 1, 0.6 * 32767 / max(1, max(abs(s) for s in samples)))
    return array.array("h", (max(-32768, min(32767, round(s * gain))) for s in samples)).tobytes()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice", default="Fred")
    parser.add_argument("--rate", type=int, default=220, help="words per minute")
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    for event, text in LABELS.items():
        pcm = render(text, args.voice, args.rate)
        (OUT / f"{event}.pcm").write_bytes(pcm)
        print(f"{event:22} {len(pcm) / 48000:4.2f}s  {text}")
    for name, text in PROMPTS.items():
        pcm = render(text, args.voice, args.rate - 40, PROMPT_RMS)
        (OUT / f"{name}.pcm").write_bytes(pcm)
        print(f"{name:22} {len(pcm) / 48000:4.2f}s  {text}")


if __name__ == "__main__":
    main()
