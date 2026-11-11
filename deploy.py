# /// script
# requires-python = ">=3.12"
# dependencies = ["daytona==0.231.0"]
# ///
"""Deploy the Worker, then make the VoiceSession Durable Object load the new code.

    uv run --script deploy.py            # deploy, wait for no live call, refresh
    uv run --script deploy.py --no-deploy  # only the safe refresh
    uv run --script deploy.py --no-deploy --patch-sandboxes          # dry run: which snapshot files differ
    uv run --script deploy.py --no-deploy --patch-sandboxes --apply  # back-patch them, then refresh

A Durable Object keeps running the code it started with while it holds the outbound ACP
WebSocket, so a deploy alone leaves calls on the old version. Stopping the Sandbox closes
that socket and the object is evicted; the next call restarts the Sandbox (about 15 s) on
new code. This waits until no call is in progress, because stopping the Sandbox mid-call
hangs up the caller.

--patch-sandboxes brings existing Sandboxes' snapshot-owned files (SNAPSHOT_FILES in
build_sandbox_snapshot.py: the ACP Gateway and OpenCode config, rules, and plugin) up to
date with this checkout, so changing one rarely needs a new snapshot or a replaced Sandbox.
A Sandbox keeps its disk across stop and start, and the gateway is the image entrypoint,
so the patched files take effect when the refresh stops the Sandbox and the next call
starts it. Each differing file is backed up beside itself first; a gateway change must
pass its own self-test inside the Sandbox or every file is restored. Packages and
binaries (OpenCode itself) still need a new snapshot. A dry run starts a stopped Sandbox
to read its files, then stops it again.
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
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACCOUNT_ID = "890753ccd6f0bbbfbe2c030cc44f6a87"  # Adam's personal Cloudflare account
CF_PROFILE = Path.home() / "Library/Preferences/cloudflare/config/personal.json"


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
    token = dict(line.split("=", 1) for line in (Path.home() / ".tokens/daytona-api").read_text().split()
                 if "=" in line)
    os.environ["DAYTONA_API_KEY"] = token["DAYTONA_API_KEY"].strip("'\"")
    from daytona import Daytona
    daytona = Daytona()
    return daytona, [s for s in daytona.list() if (s.labels or {}).get("dogwalk.dev/managed-by") == "dogwalk"]


def patch_sandbox(daytona, sandbox, apply: bool) -> None:
    """Bring one Sandbox's snapshot-owned files up to date; leaves it started."""
    from build_sandbox_snapshot import SNAPSHOT_FILES
    if not str(sandbox.state).endswith("STARTED"):
        daytona.start(sandbox, timeout=120)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    changed = []
    for source, destination, mode in SNAPSHOT_FILES:
        want = (ROOT / source).read_bytes()
        try:
            have = sandbox.fs.download_file(destination)
        except Exception:
            have = None
        if have != want:
            changed.append((destination, want, have, mode))
    name = sandbox.id[:8]
    if not changed:
        print(f"sandbox {name}: snapshot files already current")
        return
    for destination, _, have, _ in changed:
        print(f"sandbox {name}: {'patching' if apply else 'would patch'} {destination}"
              f"{'' if have is not None else ' (missing)'}")
    if not apply:
        return

    def run(command: str) -> str:
        result = sandbox.process.exec(command, timeout=180)
        if result.exit_code:
            raise RuntimeError(f"{command!r} exited {result.exit_code}: {str(result.result)[-800:]}")
        return str(result.result)

    restored = []
    try:
        for destination, want, have, mode in changed:
            if have is not None:
                backup = f"{destination}.dogwalk-backup-{stamp}"
                sandbox.fs.upload_file(have, backup)
                restored.append((destination, backup))
            else:
                run(f"mkdir -p {shlex.quote(str(Path(destination).parent))}")
                restored.append((destination, None))
            sandbox.fs.upload_file(want, destination)
            run(f"chmod {mode} {shlex.quote(destination)}")
            digest = run(f"sha256sum {shlex.quote(destination)}").split()[0]
            if digest != hashlib.sha256(want).hexdigest():
                raise RuntimeError(f"{destination} does not match after upload")
        if any(d.endswith("sandbox_acp_gateway.py") for d, *_ in changed):
            run("cd /tmp && python /opt/dogwalk/sandbox_acp_gateway.py test")
            print(f"sandbox {name}: patched gateway passed its self-test")
    except Exception:
        for destination, backup in restored:
            run(f"cp -p {shlex.quote(backup)} {shlex.quote(destination)}" if backup
                else f"rm -f {shlex.quote(destination)}")
        print(f"sandbox {name}: patch failed; restored previous files", file=sys.stderr)
        raise
    print(f"sandbox {name}: {len(changed)} file(s) patched; backups end in .dogwalk-backup-{stamp}")


def refresh(wait_minutes: float, patch: bool = False, apply: bool = False) -> None:
    wait_for_no_call(wait_minutes)
    daytona, sandboxes = managed_sandboxes()
    for sandbox in sandboxes:
        try:
            if patch:
                patch_sandbox(daytona, sandbox, apply)
        finally:
            # Always stop: a patched gateway loads only when the entrypoint restarts.
            if patch:
                sandbox.refresh_data()
            if str(sandbox.state).endswith("STARTED"):
                daytona.stop(sandbox)
                print(f"stopped sandbox {sandbox.id[:8]}; the Durable Object will load the new code on the next call")
            else:
                print(f"sandbox {sandbox.id[:8]} is {sandbox.state}; the Durable Object is already free to reload")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--wait-minutes", type=float, default=20)
    parser.add_argument("--patch-sandboxes", action="store_true", help="sync snapshot-owned files (dry run unless --apply)")
    parser.add_argument("--apply", action="store_true", help="with --patch-sandboxes: write the files")
    args = parser.parse_args()
    if args.apply and not args.patch_sandboxes:
        parser.error("--apply only applies to --patch-sandboxes")
    if not args.no_deploy:
        deploy()
    refresh(args.wait_minutes, args.patch_sandboxes, args.apply)
