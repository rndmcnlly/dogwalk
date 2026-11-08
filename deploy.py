# /// script
# requires-python = ">=3.12"
# dependencies = ["daytona==0.231.0"]
# ///
"""Deploy the Worker, then make the VoiceSession Durable Object load the new code.

    uv run --script deploy.py            # deploy, wait for no live call, refresh
    uv run --script deploy.py --no-deploy  # only the safe refresh

A Durable Object keeps running the code it started with while it holds the outbound ACP
WebSocket, so a deploy alone leaves calls on the old version. Stopping the Sandbox closes
that socket and the object is evicted; the next call restarts the Sandbox (about 15 s) on
new code. This waits until no call is in progress, because stopping the Sandbox mid-call
hangs up the caller.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
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


def refresh(wait_minutes: float) -> None:
    deadline = time.time() + wait_minutes * 60
    while live := calls_in_progress():
        if time.time() > deadline:
            sys.exit(f"still in progress after {wait_minutes} min: {live}; rerun with --no-deploy later")
        print(f"call in progress ({', '.join(live)}); waiting")
        time.sleep(15)
    token = dict(line.split("=", 1) for line in (Path.home() / ".tokens/daytona-api").read_text().split()
                 if "=" in line)
    os.environ["DAYTONA_API_KEY"] = token["DAYTONA_API_KEY"].strip("'\"")
    from daytona import Daytona
    daytona = Daytona()
    for sandbox in daytona.list():
        if (sandbox.labels or {}).get("dogwalk.dev/managed-by") != "dogwalk":
            continue
        if str(sandbox.state).endswith("STARTED"):
            daytona.stop(sandbox)
            print(f"stopped sandbox {sandbox.id[:8]}; the Durable Object will load the new code on the next call")
        else:
            print(f"sandbox {sandbox.id[:8]} is {sandbox.state}; the Durable Object is already free to reload")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--wait-minutes", type=float, default=20)
    args = parser.parse_args()
    if not args.no_deploy:
        deploy()
    refresh(args.wait_minutes)
