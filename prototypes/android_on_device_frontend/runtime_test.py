# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Run test-APK-only Keystore and loopback WSS checks on an explicit silent emulator.

Never targets physical phones, downloads models, changes audio settings, or contacts
production. Start an existing emulator with -no-audio; pass --serial emulator-NNNN.
The test APK contains its own ephemeral loopback TLS trust, not the shipping app.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"emulator-\d+", args.serial):
        parser.error("Only an explicitly selected emulator is allowed")
    tools = Path(os.environ["DOGWALK_ANDROID_TOOLS"])
    adb = [str(tools / "sdk/platform-tools/adb"), "-s", args.serial]
    model = subprocess.check_output(adb + ["shell", "getprop", "ro.product.model"], text=True)
    if "sdk_gphone" not in model:
        parser.error("Selected target is not the supported emulator")
    env = os.environ.copy()
    env.update(JAVA_HOME=str(tools / "jdk"), ANDROID_HOME=str(tools / "sdk"),
               ANDROID_USER_HOME=str(ROOT / ".tools/android-user"), GRADLE_USER_HOME=str(ROOT / ".tools/gradle-home"))
    if not args.no_build:
        subprocess.run([sys.executable, str(ROOT / "build.py")], cwd=ROOT, env=env, check=True)
        subprocess.run([str(tools / "gradle-8.11.1/bin/gradle"), "--no-daemon", "--console=plain", ":app:assembleDebugAndroidTest"], cwd=ROOT, env=env, check=True)
    for apk in ("debug/app-debug.apk", "androidTest/debug/app-debug-androidTest.apk"):
        subprocess.run(adb + ["install", "-r", str(ROOT / "app/build/outputs/apk" / apk)], check=True)
    result = subprocess.check_output(adb + ["shell", "am", "instrument", "-w", "-r", "-e", "class",
        "dogwalk.ondevice.TransportRuntimeTest", "as.adamsmith.dogwalk.ondevice.dev.test/androidx.test.runner.AndroidJUnitRunner"], text=True)
    print(result)
    if "OK (3 tests)" not in result or "FAILURES!!!" in result:
        raise SystemExit("Runtime instrumentation failed")


if __name__ == "__main__":
    main()
