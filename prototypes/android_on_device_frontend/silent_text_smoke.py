# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Silent UI smoke test. Requires an already-running, audio-disabled emulator and fixture.

uv run --script silent_text_smoke.py --adb /path/to/adb --serial emulator-5558 --port 18771
Never installs, wipes, starts voice, injects audio, or addresses a physical phone.
The selected emulator must already have the updated app and model installed.
"""

import argparse
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--adb", required=True)
    p.add_argument("--serial", required=True)
    p.add_argument("--port", type=int, default=18771)
    p.add_argument("--artifacts", type=Path, required=True, help="Local screenshot/XML directory, outside Git")
    args = p.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r"emulator-\d+", args.serial):
        p.error("Only an explicit emulator serial is accepted")
    if args.port == 8770:
        p.error("Use an isolated fixture port, not the ongoing playtest")

    def adb(*command):
        return subprocess.check_output([args.adb, "-s", args.serial, *command], text=True, timeout=30)

    def nodes():
        # A fixed device-local test artifact, not a transcript stored in Git.
        adb("shell", "uiautomator", "dump", "/sdcard/dogwalk-silent-test.xml")
        return list(ET.fromstring(adb("shell", "cat", "/sdcard/dogwalk-silent-test.xml")).iter("node"))

    def wait(predicate, description, timeout=45):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            ns = nodes()
            if predicate(ns):
                print("PASS", description, flush=True)
                return ns
        raise AssertionError(f"Timed out: {description}")

    def tap(node):
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.attrib["bounds"]))
        adb("shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))

    def capture(name):
        with (args.artifacts / f"{name}.png").open("wb") as f:
            subprocess.run([args.adb, "-s", args.serial, "exec-out", "screencap", "-p"], stdout=f, check=True)
        ns = nodes()
        (args.artifacts / f"{name}.xml").write_text(ET.tostring(ET.Element("capture"), encoding="unicode") if not ns else adb("shell", "cat", "/sdcard/dogwalk-silent-test.xml"))
        return ns

    def send(text):
        print("SEND", text, flush=True)
        ns = nodes()
        tap(next(n for n in ns if n.attrib.get("content-desc") == "Message Dogwalk"))
        # Only simple test phrases; no shell metacharacters or private conversation.
        assert re.fullmatch(r"[a-z ]+", text)
        adb("shell", "input", "text", text.replace(" ", "%s"))
        if text == "hi there how are you today":
            ns = capture("keyboard-open")
            bounds = lambda n: list(map(int, re.findall(r"\d+", n.attrib["bounds"])))
            composer = next(n for n in ns if n.attrib.get("content-desc") == "Message Dogwalk")
            send_button = next(n for n in ns if n.attrib.get("text") == "Send")
            timeline = next(n for n in ns if n.attrib.get("content-desc") == "Conversation and useful diagnostics")
            assert bounds(timeline)[3] - bounds(timeline)[1] >= 160, "Timeline collapsed under IME"
            assert bounds(composer)[3] <= bounds(send_button)[3] + 50
            print("PASS real keyboard screenshot and remaining timeline bounds", bounds(timeline), bounds(composer), flush=True)
        tap(next(n for n in nodes() if n.attrib.get("text", "").lower() == "send"))

    def contains(ns, text):
        return any(text.lower() in n.attrib.get("text", "").lower() for n in ns)

    adb("reverse", f"tcp:{args.port}", f"tcp:{args.port}")
    adb("shell", "am", "start", "-n", "as.adamsmith.dogwalk.ondevice.dev/dogwalk.ondevice.MainActivity",
        "--ez", "start", "true", "--es", "endpoint", f"ws://127.0.0.1:{args.port}/ws")
    capture("startup-observed")  # Actual observed state only; may already be ready on a warm return.
    wait(lambda ns: contains(ns, "Connected") and contains(ns, "Model ready"), "independent model and link ready")
    pid = adb("shell", "pidof", "as.adamsmith.dogwalk.ondevice.dev").strip()
    def model_log():
        return adb("logcat", "-d", f"--pid={pid}", "-s", "DogwalkFrontend:I")

    wait(lambda ns: contains(ns, "Hello from the test fixture"), "fixture greeting visibly displayed")
    capture("connected")
    tap(next(n for n in nodes() if n.attrib.get("text") == "Menu"))
    wait(lambda ns: contains(ns, "Setup and tools"), "native settings modal")
    capture("menu")
    adb("shell", "input", "keyevent", "4")
    prior_replies = model_log().count("DECIDE reply")
    send("hi there how are you today")
    wait(lambda ns: contains(ns, "hi there how are you today"), "typed user turn visible")
    # A local reply is checked in the tagged model log, independent of its exact wording.
    end = time.monotonic() + 30
    while time.monotonic() < end:
        log = model_log()
        if log.count("DECIDE reply") > prior_replies:
            print("PASS real local-model conversational reply", flush=True)
            break
    else:
        raise AssertionError("No local conversational reply")
    send("what are the sessions doing")
    wait(lambda ns: contains(ns, "Fir is idle"), "work Handoff reply visible")
    send("how are both sessions doing")
    wait(lambda ns: contains(ns, "staging or production"), "second independent asynchronous say visible")
    capture("separate-replies")
    send("please start the rebuild")
    wait(lambda ns: contains(ns, "allow it once") and contains(ns, "deny it"), "work request and permission question visible")
    send("yes allow it once")
    wait(lambda ns: contains(ns, "this once") and contains(ns, "carried on"), "typed permission answer handed off and reply visible")
    send("goodbye")
    wait(lambda ns: contains(ns, "Disconnected"), "connection error visibly reported without speech")
    capture("disconnected-retry")
    tap(next(n for n in nodes() if n.attrib.get("text") == "Retry"))
    wait(lambda ns: contains(ns, "Connected") and contains(ns, "Model ready"), "Retry reconnects without automatically resending")
    capture("reconnected")
    log = model_log()
    for forbidden in ("TTS:", "ASR:", "PLAYBACK ", "BARGE-IN", "INJECTED"):
        assert forbidden not in log, f"Unexpected voice/debug path: {forbidden}"
    assert log.count("TEXT_DISPLAYED") >= 7
    print("PASS no voice-path or debug-injection logs; explicit text receipts", flush=True)


if __name__ == "__main__":
    main()
