# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Isolated, pinned Android build. No global Java/Gradle/SDK installation required.

uv run --script build.py --bootstrap       # download tools; never accepts SDK licenses
uv run --script build.py --bootstrap --accept-sdk-licenses  # explicitly accept licenses
uv run --script build.py                  # assembleDebug + lintDebug
uv run --script build.py --install        # also adb install to one authorized phone
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / ".tools"
GRADLE_VERSION = "8.11.1"
COMMAND_TOOLS = "13114758"


def open_url(url: str):
    # Some download hosts reject urllib's default user agent with HTTP 403.
    request = urllib.request.Request(url, headers={"User-Agent": "DogwalkAudioProbe/0.1"})
    return urllib.request.urlopen(request, timeout=120)


def fetch(url: str, destination: Path, digest: str | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        print(f"Downloading {url}", flush=True)
        partial = destination.with_suffix(".part")
        with open_url(url) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out)
        partial.replace(destination)
    if digest and hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        raise SystemExit(f"SHA-256 mismatch: {destination}. Remove this archive and retry.")
    return destination


def unzip(archive: Path, destination: Path):
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            if not (destination / item.filename).resolve().is_relative_to(destination.resolve()):
                raise SystemExit("Unsafe archive path")
            z.extract(item, destination)
            mode = item.external_attr >> 16
            if mode:
                (destination / item.filename).chmod(mode & 0o777)


def run(command, env, **kwargs):
    print("Running:", " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=ROOT, env=env, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--accept-sdk-licenses", action="store_true",
                        help="Explicitly accept Google's SDK licenses noninteractively")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--test", action="store_true", help="Only run deterministic policy tests")
    args = parser.parse_args()
    if args.accept_sdk_licenses and not args.bootstrap:
        parser.error("--accept-sdk-licenses requires --bootstrap")
    system = {"Darwin": "mac", "Linux": "linux"}.get(platform.system())
    arch = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x64"}.get(platform.machine())
    if system is None or arch is None:
        raise SystemExit("Bootstrap supports macOS/Linux on arm64/x64. Otherwise use Android Studio.")
    env = os.environ.copy()
    java_home = TOOLS / "jdk"
    sdk = Path(env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT") or TOOLS / "sdk")
    gradle = TOOLS / f"gradle-{GRADLE_VERSION}" / "bin/gradle"
    manager = sdk / "cmdline-tools/latest/bin/sdkmanager"
    if args.bootstrap:
        if not (java_home / "bin/java").exists():
            url = f"https://api.adoptium.net/v3/assets/latest/17/hotspot?architecture={arch}&image_type=jdk&os={system}&vendor=eclipse"
            with open_url(url) as response:
                package = json.load(response)[0]["binary"]["package"]
            archive = fetch(package["link"], TOOLS / "jdk.tar.gz", package["checksum"])
            unpacked = TOOLS / "jdk-unpacked"
            with tarfile.open(archive) as t:
                t.extractall(unpacked, filter="data")
            home = next(unpacked.glob("*/Contents/Home"), None) if system == "mac" else next(unpacked.iterdir())
            if home is None:
                raise SystemExit("JDK home missing in downloaded archive")
            home.rename(java_home)
        if not gradle.exists():
            url = f"https://services.gradle.org/distributions/gradle-{GRADLE_VERSION}-bin.zip"
            with open_url(url + ".sha256") as response:
                checksum = response.read().decode().strip()
            unzip(fetch(url, TOOLS / "gradle.zip", checksum), TOOLS)
        if not manager.exists():
            os_name = "mac" if system == "mac" else "linux"
            archive = fetch(f"https://dl.google.com/android/repository/commandlinetools-{os_name}-{COMMAND_TOOLS}_latest.zip",
                            TOOLS / "commandlinetools.zip")
            staging = TOOLS / "command-tools-unpacked"
            unzip(archive, staging)
            manager.parent.parent.parent.mkdir(parents=True, exist_ok=True)
            (staging / "cmdline-tools").rename(manager.parent.parent)
    if not (java_home / "bin/java").exists():
        supplied = env.get("JAVA_HOME")
        if not supplied:
            raise SystemExit("Run with --bootstrap, or set JAVA_HOME to JDK 17–23.")
        java_home = Path(supplied)
    env["JAVA_HOME"] = str(java_home)
    env["ANDROID_HOME"] = str(sdk)
    env["ANDROID_USER_HOME"] = str(TOOLS / "android-user")
    env["GRADLE_USER_HOME"] = str(TOOLS / "gradle-home")
    env["PATH"] = str(java_home / "bin") + os.pathsep + env.get("PATH", "")
    if args.test:
        run([java_home / "bin/java", ROOT / "test_policy.java"], env)
        run([sys.executable, "-m", "unittest", "test_summarize.py"], env)
        return
    if args.bootstrap:
        # Only review licenses for the three requested packages, not every available
        # emulator, XR, or third-party SDK license in the repository.
        run([manager, f"--sdk_root={sdk}", "platform-tools", "platforms;android-35", "build-tools;35.0.0"],
            env, input=("y\n" if args.accept_sdk_licenses else "n\n") * 100, text=True)
        required = [sdk / "platforms/android-35/android.jar", sdk / "build-tools/35.0.0/source.properties",
                    sdk / "platform-tools/adb"]
        if not all(path.exists() for path in required):
            raise SystemExit("SDK packages missing. Review Google's SDK licenses, then explicitly run "
                             "--bootstrap --accept-sdk-licenses if you agree. Host tests: --test.")
    if not gradle.exists():
        raise SystemExit("Gradle missing. Run with --bootstrap.")
    run([gradle, "--no-daemon", "--console=plain", ":app:assembleDebug", ":app:lintDebug"], env)
    run([java_home / "bin/java", ROOT / "test_policy.java"], env)
    run([sys.executable, "-m", "unittest", "test_summarize.py"], env)
    apk = ROOT / "app/build/outputs/apk/debug/app-debug.apk"
    print(f"\nAPK: {apk}")
    if args.install:
        run([sdk / "platform-tools/adb", "install", "-r", apk], env)


if __name__ == "__main__":
    main()
