# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Render the keypad System Menu prompts with macOS text-to-speech into the Worker's bundle.

    uv run --script make_menu_prompts.py                 # Fred, the default
    uv run --script make_menu_prompts.py --voice Samantha  # any `say -v '?'` voice

Each prompt becomes cf_worker/menu_prompts/<name>.pcm: raw mono PCM16LE at 24 kHz, leveled
close to speech, ready for a GPT-Live output fixture. The menu must work when the Voice Agent
cannot hear or think straight, so no model speaks it. Rendering happens here because a Worker
cannot run `say`; the files are committed and bundled as Data modules.
"""

from __future__ import annotations

import argparse
import array
import math
import subprocess
import tempfile
import wave
from pathlib import Path

OUT = Path(__file__).resolve().parent / "cf_worker" / "menu_prompts"

PROMPTS = {
    "menu": "Menu. One, restart the sandbox. Two, recovery options. Star, back.",
    "menu_restart_confirm": "Restarting stops running work. Press one again to confirm.",
    "menu_restarting": "Restarting the sandbox.",
    "menu_back": "Back.",
}

PROMPT_RMS = 0.09  # about -21 dBFS: close to speech, since the caller must follow it


def render(text: str, voice: str, rate: int) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        aiff, wav = Path(tmp) / "prompt.aiff", Path(tmp) / "prompt.wav"
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
    gain = min(PROMPT_RMS / rms if rms else 1, 0.6 * 32767 / max(1, max(abs(s) for s in samples)))
    return array.array("h", (max(-32768, min(32767, round(s * gain))) for s in samples)).tobytes()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice", default="Fred")
    parser.add_argument("--rate", type=int, default=180, help="words per minute")
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    for name, text in PROMPTS.items():
        pcm = render(text, args.voice, args.rate)
        (OUT / f"{name}.pcm").write_bytes(pcm)
        print(f"{name:22} {len(pcm) / 48000:4.2f}s  {text}")


if __name__ == "__main__":
    main()
