# /// script
# requires-python = ">=3.12"
# dependencies = ["daytona==2.2.0"]
# ///
"""Deploy the Worker, then make the VoiceSession Durable Object load the new code.

    uv run --script deploy.py            # deploy, wait for no live call, refresh
    uv run --script deploy.py --no-deploy  # only the safe refresh
    uv run --script deploy.py --no-deploy --patch-sandboxes          # dry run: which image files differ
    uv run --script deploy.py --no-deploy --patch-sandboxes --apply  # back-patch them, then refresh

A Durable Object keeps running the code it started with while it holds the outbound Agent
event WebSocket, so a deploy alone leaves calls on the old version. Pausing the Sandbox closes
that socket and the object is evicted; the next call resumes the Sandbox (a few seconds) on
new code, with the Agent's processes as they were. This waits until no call is in progress,
because pausing the Sandbox mid-call hangs up the caller.

--patch-sandboxes brings existing Sandboxes' image-owned files (IMAGE_FILES in
build_sandbox_image.py: the Agent Gateway and OpenCode config, rules, and plugin) and OpenCode
itself up to date with this checkout, so changing one rarely needs a new image or a replaced
Sandbox. A persistent Sandbox keeps its disk across stop and start, and the gateway is the
image entrypoint, so a patched Sandbox is stopped rather than paused, and the next call starts
it on the patched files. Each differing file is backed up beside itself first; a gateway change
must pass its own self-test inside the Sandbox or every file is restored. A dry run starts a
sleeping Sandbox to read its files, then puts it back to sleep. Other packages need a new image.

A Worker deploy is refused unless wrangler.toml's DAYTONA_IMAGE is a ready Daytona image and
its DAYTONA_AGENT_SECRET exists: a missing one would fail every new caller's Sandbox.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACCOUNT_ID = "890753ccd6f0bbbfbe2c030cc44f6a87"  # Adam's personal Cloudflare account
CF_PROFILE = Path.home() / "Library/Preferences/cloudflare/config/personal.json"
def daytona_client():
    token = dict(line.split("=", 1) for line in (Path.home() / ".tokens/daytona-api").read_text().split()
                 if "=" in line)
    os.environ["DAYTONA_API_KEY"] = token["DAYTONA_API_KEY"].strip("'\"")
    from daytona import Daytona
    return Daytona().v2


def check_provider() -> None:
    """Refuse a Worker deploy whose new Sandboxes could not be created."""
    from daytona.v2 import NotFoundError
    with (ROOT / "cf_worker/wrangler.toml").open("rb") as file:
        config = tomllib.load(file)["vars"]
    daytona = daytona_client()
    try:
        image = daytona.images.get(config["DAYTONA_IMAGE"])
        daytona.secrets.get(config["DAYTONA_AGENT_SECRET"])
    except NotFoundError:
        sys.exit(f"refusing to deploy: image {config['DAYTONA_IMAGE']!r} or secret "
                 f"{config['DAYTONA_AGENT_SECRET']!r} does not exist. See build_sandbox_image.py and "
                 "cf_worker/README.md.")
    if image.status != "ready":
        sys.exit(f"refusing to deploy: image {image.name!r} is {image.status}")


def deploy() -> None:
    env = {**os.environ, "CLOUDFLARE_ACCOUNT_ID": ACCOUNT_ID}
    # A real CLI call refreshes the personal profile's OAuth bearer before we read it.
    subprocess.run(["cf", "workers", "list", "--profile", "personal"], env=env, capture_output=True, check=True)
    env["CLOUDFLARE_API_TOKEN"] = json.loads(CF_PROFILE.read_text())["oauth_token"]
    out = subprocess.run(["npx", "wrangler", "deploy"], cwd=ROOT / "cf_worker", env=env, capture_output=True, text=True)
    version = [line for line in out.stdout.splitlines() if "Current Version ID" in line]
    if out.returncode or not version:
        sys.exit(f"deploy failed:\n{out.stdout[-1500:]}\n{out.stderr[-1500:]}")
    print(version[0].strip())


def calls_in_progress() -> list[str]:
    password = subprocess.run(["security", "find-generic-password", "-a", "adam", "-s", "dogwalk-admin", "-w"],
                              capture_output=True, text=True, check=True).stdout.strip()
    request = urllib.request.Request("https://dogwalk.tools/admin/api/calls?verbose=1&limit=5", headers={
        "authorization": "Basic " + base64.b64encode(f"adam:{password}".encode()).decode(),
        "user-agent": "dogwalk-deploy/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return [c["call_sid"][:10] for c in json.load(response) if c["status"] in ("in-progress", "ringing", "queued")]


def wait_for_no_call(wait_minutes: float) -> None:
    deadline = time.time() + wait_minutes * 60
    while live := calls_in_progress():
        if time.time() > deadline:
            sys.exit(f"still in progress after {wait_minutes} min: {live}; rerun with --no-deploy later")
        print(f"call in progress ({', '.join(live)}); waiting")
        time.sleep(15)


def managed_sandboxes():
    daytona = daytona_client()
    page = daytona.search(labels={"dogwalk.dev/managed-by": "dogwalk"}, limit=200)
    # The index lags a few seconds, so act on each Sandbox as it is now.
    current = [daytona.get(found.id) for found in page.items]
    return [s for s in current if s.state in ("started", "paused", "stopped")]


def patch_sandbox(sandbox, apply: bool) -> bool:
    """Bring one Sandbox's image-owned files and OpenCode up to date; leaves it started. True when
    anything changed."""
    from build_sandbox_image import IMAGE_FILES, OPENCODE_URL, OPENCODE_VERSION, SERVICE_CHECK
    if sandbox.state != "started":
        sandbox.start(timeout=120)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    from daytona.v2 import FileNotFoundError

    def download(path: str) -> bytes | None:
        try:
            return sandbox.fs.read_bytes(path)
        except FileNotFoundError:
            return None

    def run(command: str) -> str:
        result = sandbox.process.run(command, timeout=190, execution_timeout=180)
        if result.exit_code:
            raise RuntimeError(f"{command!r} exited {result.exit_code}: {(result.stdout + result.stderr)[-800:]}")
        return result.stdout

    changed = []
    for source, destination, mode in IMAGE_FILES:
        want = (ROOT / source).read_bytes()
        if (have := download(destination)) != want:
            changed.append((destination, want, have, mode))
    installed = run("opencode --version 2>&1 || true").strip()
    upgrade = not installed.endswith(OPENCODE_VERSION)
    name = sandbox.id[:12]
    if not changed and not upgrade:
        print(f"sandbox {name}: image files and OpenCode {OPENCODE_VERSION} already current")
        return False
    if upgrade:
        print(f"sandbox {name}: {'installing' if apply else 'would install'} OpenCode "
              f"{OPENCODE_VERSION} (has {installed or 'none'})")
    for destination, _, have, _ in changed:
        print(f"sandbox {name}: {'patching' if apply else 'would patch'} {destination}"
              f"{'' if have is not None else ' (missing)'}")
    if not apply:
        return False

    restored = []
    binary_backup = f"/usr/local/bin/opencode.dogwalk-backup-{stamp}"
    try:
        if upgrade:
            # Fetched here and uploaded, so the Sandbox needs no egress to GitHub. The rename
            # leaves a running OpenCode its old inode until the refresh stops it.
            with urllib.request.urlopen(OPENCODE_URL, timeout=300) as response:
                sandbox.fs.write_bytes("/tmp/opencode-dogwalk.tar.gz", response.read())
            run("rm -rf /tmp/opencode-dogwalk && mkdir /tmp/opencode-dogwalk && "
                "tar -xzf /tmp/opencode-dogwalk.tar.gz -C /tmp/opencode-dogwalk opencode && "
                f"sudo cp -p /usr/local/bin/opencode {binary_backup} && "
                "sudo install -m 755 /tmp/opencode-dogwalk/opencode /usr/local/bin/opencode.new && "
                "sudo mv /usr/local/bin/opencode.new /usr/local/bin/opencode")
            restored.append(("/usr/local/bin/opencode", binary_backup))
            if not run("opencode --version").strip().endswith(OPENCODE_VERSION):
                raise RuntimeError(f"OpenCode {OPENCODE_VERSION} did not install")
        for destination, want, have, mode in changed:
            if have is not None:
                backup = f"{destination}.dogwalk-backup-{stamp}"
                sandbox.fs.write_bytes(backup, have)
                restored.append((destination, backup))
            else:
                run(f"mkdir -p {shlex.quote(str(Path(destination).parent))}")
                restored.append((destination, None))
            sandbox.fs.write_bytes(destination, want)
            run(f"chmod {mode} {shlex.quote(destination)}")
            digest = run(f"sha256sum {shlex.quote(destination)}").split()[0]
            if digest != hashlib.sha256(want).hexdigest():
                raise RuntimeError(f"{destination} does not match after upload")
        if any(d.endswith("sandbox_agent_gateway.py") for d, *_ in changed):
            run("cd /tmp && python /opt/dogwalk/sandbox_agent_gateway.py test")
            print(f"sandbox {name}: patched gateway passed its self-test")
        if upgrade:
            # Under a scratch HOME: the running OpenCode may still hold the real
            # database, which the new version migrates only once the Sandbox restarts.
            check = SERVICE_CHECK.replace("/home/daytona/.local/state", "$HOME/.local/state")
            run('export HOME="$(mktemp -d)" && cd "$HOME" && sh -c ' + shlex.quote(check)
                + '; status=$?; rm -rf "$HOME"; exit $status')
            print(f"sandbox {name}: OpenCode {OPENCODE_VERSION} served /api/info")
    except Exception:
        for destination, backup in restored:
            run(f"sudo cp -p {shlex.quote(backup)} {shlex.quote(destination)}" if backup
                else f"rm -f {shlex.quote(destination)}")
        print(f"sandbox {name}: patch failed; restored previous files", file=sys.stderr)
        raise
    print(f"sandbox {name}: {len(changed)} file(s) patched{', OpenCode upgraded' if upgrade else ''}; "
          f"backups end in .dogwalk-backup-{stamp}")
    return True


def refresh(wait_minutes: float, patch: bool = False, apply: bool = False) -> None:
    wait_for_no_call(wait_minutes)
    for sandbox in managed_sandboxes():
        patched = False
        try:
            if patch:
                patched = patch_sandbox(sandbox, apply)
        finally:
            sandbox.refresh_data()
            if sandbox.state != "started":
                print(f"sandbox {sandbox.id[:12]} is {sandbox.state}; the Durable Object is already free to reload")
            elif patched:
                # Pausing would keep the old gateway in memory; only a start runs the patched one.
                sandbox.stop()
                print(f"stopped sandbox {sandbox.id[:12]}; the next call starts it on the patched files and new code")
            else:
                sandbox.pause()
                print(f"paused sandbox {sandbox.id[:12]}; the Durable Object will load the new code on the next call")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--wait-minutes", type=float, default=20)
    parser.add_argument("--patch-sandboxes", action="store_true", help="sync image-owned files (dry run unless --apply)")
    parser.add_argument("--apply", action="store_true", help="with --patch-sandboxes: write the files")
    args = parser.parse_args()
    if args.apply and not args.patch_sandboxes:
        parser.error("--apply only applies to --patch-sandboxes")
    if not args.no_deploy:
        check_provider()
        deploy()
    refresh(args.wait_minutes, args.patch_sandboxes, args.apply)
