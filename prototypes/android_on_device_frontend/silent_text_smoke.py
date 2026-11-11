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
import re
import subprocess
import time
import xml.etree.ElementTree as ET


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--adb", required=True)
    p.add_argument("--serial", required=True)
    p.add_argument("--port", type=int, default=18771)
    args = p.parse_args()
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

    def send(text):
        print("SEND", text, flush=True)
        ns = nodes()
        tap(next(n for n in ns if n.attrib.get("class") == "android.widget.EditText" and n.attrib.get("text") == "Message Dogwalk"))
        # Only simple test phrases; no shell metacharacters or private conversation.
        assert re.fullmatch(r"[a-z ]+", text)
        adb("shell", "input", "text", text.replace(" ", "%s"))
        adb("shell", "input", "keyevent", "111")  # dismiss IME, not a mode action
        tap(next(n for n in nodes() if n.attrib.get("text", "").lower() == "send"))

    def contains(ns, text):
        return any(text.lower() in n.attrib.get("text", "").lower() for n in ns)

    adb("reverse", f"tcp:{args.port}", f"tcp:{args.port}")
    adb("shell", "am", "start", "-n", "as.adamsmith.dogwalk.ondevice.dev/dogwalk.ondevice.MainActivity",
        "--ez", "start", "true", "--es", "endpoint", f"ws://127.0.0.1:{args.port}/ws")
    wait(lambda ns: contains(ns, "Text ready"), "local model and text connection ready")
    pid = adb("shell", "pidof", "as.adamsmith.dogwalk.ondevice.dev").strip()
    def model_log():
        return adb("logcat", "-d", f"--pid={pid}", "-s", "DogwalkFrontend:I")

    wait(lambda ns: contains(ns, "Dogwalk\nHello from the test fixture"), "fixture greeting visibly displayed")
    prior_replies = model_log().count("DECIDE reply")
    send("hi there how are you today")
    wait(lambda ns: contains(ns, "You\nhi there how are you today"), "typed user turn visible")
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
    wait(lambda ns: contains(ns, "Oak is running the test suite") and contains(ns, "Fir is idle"), "work Handoff reply visible")
    send("how are both sessions doing")
    wait(lambda ns: contains(ns, "forty tests passed") and contains(ns, "staging or production"), "two separate asynchronous say turns visible")
    send("please start the rebuild")
    wait(lambda ns: contains(ns, "allow it once") and contains(ns, "deny it"), "work request and permission question visible")
    send("yes allow it once")
    wait(lambda ns: contains(ns, "this once") and contains(ns, "carried on"), "typed permission answer handed off and reply visible")
    send("goodbye")
    wait(lambda ns: contains(ns, "Disconnected"), "connection error visibly reported without speech")
    log = model_log()
    for forbidden in ("TTS:", "ASR:", "PLAYBACK ", "BARGE-IN", "INJECTED"):
        assert forbidden not in log, f"Unexpected voice/debug path: {forbidden}"
    assert log.count("TEXT_DISPLAYED") >= 7
    print("PASS no voice-path or debug-injection logs; explicit text receipts", flush=True)


if __name__ == "__main__":
    main()
