# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Isolated Android build for the on-device Voice Frontend prototype.

Reuses an existing JDK 17, Android SDK (platform 35, build-tools 35) and Gradle 8.11.1,
found in DOGWALK_ANDROID_TOOLS (default: this directory's .tools). It never accepts SDK
licenses or installs SDK packages; bootstrap those with the archived audio probe's build.py.

uv run --script build.py              # unit tests, assembleDebug, lintDebug
uv run --script build.py --install    # also `adb install -r` to exactly one attached device
"""

import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
GRADLE_VERSION = "8.11.1"


def run(command, env):
    print("Running:", " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=ROOT, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    tools = Path(os.environ.get("DOGWALK_ANDROID_TOOLS", ROOT / ".tools"))
    own = ROOT / ".tools"  # caches stay in this prototype, not the shared toolchain
    java_home, sdk = tools / "jdk", tools / "sdk"
    gradle = tools / f"gradle-{GRADLE_VERSION}" / "bin/gradle"
    for path in (java_home / "bin/java", sdk / "platforms/android-35/android.jar", gradle):
        if not path.exists():
            raise SystemExit(f"Missing {path}. Set DOGWALK_ANDROID_TOOLS to a bootstrapped toolchain.")
    env = os.environ.copy()
    env.update(JAVA_HOME=str(java_home), ANDROID_HOME=str(sdk),
               ANDROID_USER_HOME=str(own / "android-user"), GRADLE_USER_HOME=str(own / "gradle-home"))
    env["PATH"] = str(java_home / "bin") + os.pathsep + env["PATH"]
    run([gradle, "--no-daemon", "--console=plain", ":app:testDebugUnitTest", ":app:assembleDebug", ":app:lintDebug"], env)
    apk = ROOT / "app/build/outputs/apk/debug/app-debug.apk"
    print(f"\nAPK: {apk}")
    if args.install:
        run([sdk / "platform-tools/adb", "install", "-r", apk], env)


if __name__ == "__main__":
    main()
