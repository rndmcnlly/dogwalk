# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Isolated API 35 ARM64 emulator. Run build.py --bootstrap first.

setup downloads emulator/image, start runs it in the foreground (optionally
--headless), install waits for boot then installs and opens the existing debug
APK. smoke grants permissions and taps Start, checking startup/clean shutdown.
Missing speech services/models are reported as unsupported audio, not passed.
Artifacts stay under .tools/smoke. No cloud speech fallback.
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
TOOLS = ROOT / ".tools"
SDK = TOOLS / "sdk"
AVDS = TOOLS / "avd"
NAME = "dogwalk_api35_arm64"
IMAGE = "system-images;android-35;google_apis;arm64-v8a"
SERIAL = "emulator-5580"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "start", "install", "smoke", "stop"])
    parser.add_argument("--headless", action="store_true", help="Start without a window; direct audio via audio_bridge.py")
    parser.add_argument("--accept-sdk-licenses", action="store_true",
                        help="Explicitly accept licenses for these two SDK packages only")
    args = parser.parse_args()
    if args.accept_sdk_licenses and args.action != "setup":
        parser.error("--accept-sdk-licenses requires setup")
    env = os.environ.copy()
    env.update(JAVA_HOME=str(TOOLS / "jdk"), ANDROID_HOME=str(SDK),
               ANDROID_SDK_ROOT=str(SDK), ANDROID_USER_HOME=str(TOOLS / "android-user"),
               ANDROID_AVD_HOME=str(AVDS))
    env["PATH"] = str(TOOLS / "jdk/bin") + os.pathsep + env.get("PATH", "")
    AVDS.mkdir(parents=True, exist_ok=True)

    def run(command, **kwargs):
        return subprocess.run([str(x) for x in command], cwd=ROOT, env=env,
                              check=True, **kwargs)

    def adb(*command, **kwargs):
        # adb emu reads the console token from the launching HOME as well.
        return subprocess.run([str(SDK / "platform-tools/adb"), "-s", SERIAL,
                               *map(str, command)], cwd=ROOT,
                              env={**env, "HOME": str(TOOLS / "emulator-home")},
                              check=True, **kwargs)

    if args.action == "setup":
        run([SDK / "cmdline-tools/latest/bin/sdkmanager", f"--sdk_root={SDK}",
             "emulator", IMAGE], input=("y\n" if args.accept_sdk_licenses else "n\n") * 20,
            text=True)
        if not (AVDS / f"{NAME}.ini").exists():
            run([SDK / "cmdline-tools/latest/bin/avdmanager", "create", "avd",
                 "--name", NAME, "--package", IMAGE, "--device", "pixel_7",
                 "--path", AVDS / f"{NAME}.avd"], input="no\n", text=True)
        config = AVDS / f"{NAME}.avd/config.ini"
        values = dict(line.split("=", 1) for line in config.read_text().splitlines()
                      if "=" in line)
        values.update({"hw.audioInput": "yes", "hw.audioOutput": "yes",
                       "hw.keyboard": "yes", "disk.dataPartition.size": "6G"})
        config.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    elif args.action == "start":
        # Keep console/gRPC bearer credentials inside ignored local tooling.
        home = TOOLS / "emulator-home"
        home.mkdir(mode=0o700, exist_ok=True)
        env["HOME"] = str(home)
        env["ANDROID_EMULATOR_HOME"] = str(TOOLS / "android-user")
        run([SDK / "emulator/emulator", "-avd", NAME, "-port", "5580",
             "-grpc", "8554", "-grpc-use-token",
             "-audio", "none", "-no-metrics",
             "-no-snapshot", "-no-boot-anim", *(["-no-window"] if args.headless else [])])
    elif args.action in {"install", "smoke"}:
        adb("wait-for-device", timeout=180)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            boot = adb("shell", "getprop", "sys.boot_completed", capture_output=True,
                       text=True, timeout=15).stdout.strip()
            if boot == "1":
                break
            time.sleep(2)
        else:
            raise SystemExit("Emulator did not finish booting in 180 seconds")
        adb("emu", "avd", "hostmicon")
        adb("install", "-r", ROOT / "app/build/outputs/apk/debug/app-debug.apk")
        adb("shell", "am", "start", "-n", "as.adamsmith.dogwalk.audioprobe/.MainActivity")
        if args.action == "smoke":
            smoke(adb)
    else:
        adb("emu", "kill")


def smoke(adb):
    """UI-driven smoke test, never starts the microphone service from adb."""
    package = "as.adamsmith.dogwalk.audioprobe"
    output = TOOLS / "smoke"
    output.mkdir(exist_ok=True)

    def text(*command, check=True):
        # run() is checked; optional missing private log is handled separately.
        try:
            return adb(*command, capture_output=True, text=True, timeout=20).stdout
        except subprocess.CalledProcessError:
            if check:
                raise
            return ""

    def records():
        raw = text("shell", "run-as", package, "cat", "no_backup/audio-probe.jsonl", check=False)
        # Earlier native emulator crashes left a zero-filled file tail. Apply the
        # same repair as audio_bridge_smoke.events; other malformed JSON still fails.
        return [json.loads(line) for line in raw.replace("\x00", "").splitlines() if line.strip()]

    def ui(name):
        adb("shell", "uiautomator", "dump", "/sdcard/dogwalk-smoke.xml", timeout=20,
            capture_output=True)
        raw = text("shell", "cat", "/sdcard/dogwalk-smoke.xml")
        (output / name).write_text(raw)
        return ET.fromstring(raw)

    def tap(tree, label):
        node = next(n for n in tree.iter("node") if n.get("text", "").upper() == label)
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.attrib["bounds"]))
        adb("shell", "input", "tap", (x1 + x2) // 2, (y1 + y2) // 2)

    for permission in ["RECORD_AUDIO", "BLUETOOTH_CONNECT", "POST_NOTIFICATIONS"]:
        adb("shell", "pm", "grant", package, "android.permission." + permission)
    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    adb("shell", "wm", "dismiss-keyguard")
    adb("shell", "am", "start", "-n", package + "/.MainActivity", capture_output=True)
    before = len(records())
    tree = ui("before.xml")
    assert any(n.get("text") == "Dogwalk: local audio probe" for n in tree.iter("node")), "App UI missing"
    pid = text("shell", "pidof", package).strip()
    tap(tree, "START LOCAL AUDIO")
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        events = records()[before:]
        if any(e["event"] in {"fatal", "listening"} for e in events):
            break
        time.sleep(1)
    else:
        raise AssertionError("No listening or explicit fatal result within 40 seconds")
    unsupported_reason = next((e["detail"] for e in events if e["event"] == "fatal" and
                               ("No on-device recognition service" in e["detail"] or
                                "Recognition unavailable: LANGUAGE_UNAVAILABLE" in e["detail"] or
                                "No installed offline English voice" in e["detail"])), None)
    unsupported = unsupported_reason is not None
    if not unsupported:
        assert not any(e["event"] == "fatal" for e in events), events
        assert any(e["event"] == "offline_voice" for e in events), "No offline TTS voice"
        adb("shell", "input", "keyevent", "KEYCODE_SLEEP")
        time.sleep(3)
        locked_events = records()[before:]
        assert any(e["event"] == "screen" and "SCREEN_OFF" in e["detail"] for e in locked_events), "No screen-off event"
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        adb("shell", "wm", "dismiss-keyguard")
    tree = ui("after.xml")
    # Scroll only the diagnostic pane so its newest failure/cleanup is visible.
    scroll = next((n for n in tree.iter("node") if n.get("class") == "android.widget.ScrollView"), None)
    if scroll is not None:
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", scroll.attrib["bounds"]))
        adb("shell", "input", "swipe", (x1 + x2) // 2, y2 - 40,
            (x1 + x2) // 2, y1 + 20, 300)
        tree = ui("after.xml")
    with (output / "screen.png").open("wb") as image:
        adb("exec-out", "screencap", "-p", stdout=image, timeout=20)
    if not unsupported:
        tap(tree, "STOP")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        events = records()[before:]
        if any(e["event"] == "stop" for e in events):
            break
        time.sleep(0.5)
    assert any(e["event"] == "stop" for e in events), "Resources not released"
    services = text("shell", "dumpsys", "activity", "services", package)
    (output / "services.txt").write_text(services)
    assert "ServiceRecord{" not in services, "Service still running"
    logcat = text("logcat", "-d", "--pid=" + pid, "-v", "threadtime")
    (output / "logcat.txt").write_text(logcat)
    assert "FATAL EXCEPTION" not in logcat, "App crash in logcat"
    (output / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    result = {"ui_install_launch": "passed", "ui_start_and_clean_stop": "passed",
              "audio": "unsupported: " + unsupported_reason if unsupported else "startup only; acoustic round trip untested",
              "lock_unlock": "skipped: speech startup unsupported" if unsupported else "screen-off event observed",
              "hardware_checks": "skipped: real microphone routing, headset, barge-in, offline acoustics, battery",
              "screenshot": str(output / "screen.png")}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
