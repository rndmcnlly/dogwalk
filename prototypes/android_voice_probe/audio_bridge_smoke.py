# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Exercise installed probe: WAV microphone -> final transcript -> TTS WAV.

uv run --script audio_bridge_smoke.py .tools/probe-status.wav
Uses UI Start/Stop, then tests awake and screen-off with swipe keyguard showing.
Temporarily enables swipe keyguard and restores its original disabled setting;
never changes a PIN/password. Requires the on-device
English ASR model and offline English TTS already installed. No host audio.
The fixture must say 'probe status'. Artifacts: .tools/audio-bridge-smoke/.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / ".tools/audio-bridge-smoke"
PACKAGE = "as.adamsmith.dogwalk.audioprobe"


def adb(*args):
    return subprocess.check_output(
        [str(ROOT / ".tools/sdk/platform-tools/adb"), "-s", "emulator-5580", *map(str, args)],
        env={**os.environ, "HOME": str(ROOT / ".tools/emulator-home")},
        text=True, timeout=25)


def events():
    raw = adb("shell", "run-as", PACKAGE, "cat", "no_backup/audio-probe.jsonl")
    # A previous emulator native crash can leave a zero-filled filesystem tail.
    return [json.loads(line) for line in raw.replace("\x00", "").splitlines() if line.strip()]


def tap(label):
    adb("shell", "uiautomator", "dump", "/sdcard/audio-bridge-ui.xml")
    tree = ET.fromstring(adb("shell", "cat", "/sdcard/audio-bridge-ui.xml"))
    node = next(node for node in tree.iter("node") if node.get("text", "").upper() == label.upper())
    x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.attrib["bounds"]))
    adb("shell", "input", "tap", (x1 + x2) // 2, (y1 + y2) // 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    adb("wait-for-device")
    deadline = time.monotonic() + 120
    while adb("shell", "getprop", "sys.boot_completed").strip() != "1":
        if time.monotonic() >= deadline:
            raise RuntimeError("Guest did not boot")
        time.sleep(1)
    original_disabled = adb("shell", "locksettings", "get-disabled").strip()
    if original_disabled not in {"true", "false"}:
        raise RuntimeError("Could not determine original emulator keyguard setting")
    before = None
    report = {}
    try:
        adb("shell", "locksettings", "set-disabled", "false")
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        adb("shell", "wm", "dismiss-keyguard")
        adb("shell", "am", "start", "-n", PACKAGE + "/.MainActivity")
        tap("Stop")
        before = len(events())
        tap("Start local audio")
        for name in ("awake", "screen-off"):
            if name != "awake":
                # Each fixture needs a fresh recognition window, not a turn
                # already about to time out after the previous 18s capture.
                tap("Stop")
                offset = len(events())
                tap("Start local audio")
            else:
                offset = before
            deadline = time.monotonic() + 35
            while True:
                startup = events()[offset:]
                fatal = next((e for e in startup if e["event"] == "fatal"), None)
                if fatal:
                    raise RuntimeError("Probe startup failed: " + fatal["detail"])
                if any(e["event"] == "listening" for e in startup):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("No recognizer ready within 35 seconds")
                time.sleep(0.1)
            if name == "screen-off":
                adb("shell", "input", "keyevent", "KEYCODE_SLEEP")
                deadline = time.monotonic() + 3
                while True:
                    policy = adb("shell", "dumpsys", "window", "policy")
                    power = adb("shell", "dumpsys", "power")
                    keyguard = bool(re.search(r"^\s+showing=true$", policy, re.M))
                    asleep = bool(re.search(r"^\s+mWakefulness=Asleep$", power, re.M))
                    screen_off = "screenState=SCREEN_STATE_OFF" in policy
                    (OUTPUT / "screen-off-keyguard.txt").write_text(policy)
                    (OUTPUT / "screen-off-power.txt").write_text(power)
                    if keyguard and asleep and screen_off:
                        break
                    if time.monotonic() >= deadline:
                        raise AssertionError("Refusing injection: keyguard must be showing and display asleep/off")
                    time.sleep(0.1)
            offset = len(events())
            completed = subprocess.run(
                ["uv", "run", "--script", str(ROOT / "audio_bridge.py"), "exchange",
                 str(args.fixture.resolve()), str(OUTPUT / (name + ".wav")),
                 "--seconds", "18", "--require-signal"],
                capture_output=True, text=True, timeout=35)
            (OUTPUT / (name + "-bridge.txt")).write_text(completed.stdout + completed.stderr)
            observed = events()[offset:]
            (OUTPUT / (name + "-events.jsonl")).write_text(
                "".join(json.dumps(e) + "\n" for e in observed))
            completed.check_returncode()
            final = [e for e in observed if e["event"] == "final"]
            if not any("probe status" in e["detail"].lower() for e in final):
                raise AssertionError(f"{name}: expected final 'probe status', got {final}")
            if not any(e["event"] == "tts_done" for e in observed):
                raise AssertionError(f"{name}: no completed TTS reply")
            report[name] = {"bridge": json.loads(completed.stdout), "final": final,
                            "tts_done": True, "screen_off": name == "screen-off",
                            "keyguard_showing": keyguard if name == "screen-off" else False,
                            "asleep": asleep if name == "screen-off" else False}
    finally:
        try:
            adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
            adb("shell", "wm", "dismiss-keyguard")
            tap("Stop")
            if before is not None:
                (OUTPUT / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events()[before:]))
        finally:
            # Restore even when startup, capture, or UI cleanup fails.
            adb("shell", "locksettings", "set-disabled", original_disabled)
            restored = adb("shell", "locksettings", "get-disabled").strip()
            report["keyguard_setting"] = {"original_disabled": original_disabled,
                                          "restored_disabled": restored}
            (OUTPUT / "result.json").write_text(json.dumps(report, indent=2) + "\n")
            if restored != original_disabled:
                raise RuntimeError("Failed to restore original emulator keyguard setting")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
