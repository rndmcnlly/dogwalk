# /// script
# requires-python = ">=3.12"
# dependencies = ["daytona==0.231.0"]
# ///
"""Deploy the Worker, then make the VoiceSession Durable Object load the new code.

    uv run --script deploy.py            # deploy, wait for no live call, refresh
    uv run --script deploy.py --no-deploy  # only the safe refresh
    uv run --script deploy.py --no-deploy --patch-sandboxes          # dry run: which snapshot files differ
    uv run --script deploy.py --no-deploy --patch-sandboxes --apply  # back-patch them, then refresh

A Durable Object keeps running the code it started with while it holds the outbound Agent
event WebSocket, so a deploy alone leaves calls on the old version. Stopping the Sandbox closes
that socket and the object is evicted; the next call restarts the Sandbox (about 15 s) on
new code. This waits until no call is in progress, because stopping the Sandbox mid-call
hangs up the caller.

--patch-sandboxes brings existing Sandboxes' snapshot-owned files (SNAPSHOT_FILES in
build_sandbox_snapshot.py: the Agent Gateway and OpenCode config, rules, and plugin) up to
date with this checkout, so changing one rarely needs a new snapshot or a replaced Sandbox.
A Sandbox keeps its disk across stop and start, and the gateway is the image entrypoint,
so the patched files take effect when the refresh stops the Sandbox and the next call
starts it. Each differing file is backed up beside itself first; a gateway change must
pass its own self-test inside the Sandbox or every file is restored. A dry run starts a
stopped Sandbox to read its files, then stops it again.

It also migrates a Sandbox from an ACP-era snapshot (dogwalk-acp-*) in place, keeping its
Workspace and OpenCode history: it installs OPENCODE_VERSION when the Sandbox has another,
and, since the image entrypoint cannot change, replaces the retired
/opt/dogwalk/sandbox_acp_gateway.py with a shim that execs the Agent Gateway with
`opencode serve --service`. A migrated Sandbox is labelled MIGRATED_LABEL. Other packages
still need a new snapshot.

A Worker deploy is refused unless wrangler.toml's DAYTONA_SNAPSHOT is an Agent Gateway
snapshot (dogwalk-agent-*) and every managed Sandbox runs one or has been migrated: the
Worker speaks OpenCode's native API, which an ACP gateway does not serve.
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
AGENT_SNAPSHOT_PREFIX = "dogwalk-agent-"
MIGRATED_LABEL = "dogwalk.dev/agent-api"
LEGACY_ENTRYPOINT = "/opt/dogwalk/sandbox_acp_gateway.py"
# What an ACP-era image still starts: hand its arguments to the Agent Gateway instead.
LEGACY_ENTRYPOINT_SHIM = b'''#!/usr/bin/env python
# Written by deploy.py --patch-sandboxes. This Sandbox's image entrypoint still names the
# retired ACP gateway, so start the Agent Gateway with the shared OpenCode service instead.
import os, sys
arguments = ["/usr/local/bin/opencode serve --service" if a == "/usr/local/bin/opencode acp" else a for a in sys.argv[1:]]
os.execv(sys.executable, [sys.executable, "/opt/dogwalk/sandbox_agent_gateway.py", *arguments])
'''


def configured_snapshot() -> str:
    with (ROOT / "cf_worker/wrangler.toml").open("rb") as file:
        return tomllib.load(file)["vars"]["DAYTONA_SNAPSHOT"]


def runs_agent_gateway(sandbox) -> bool:
    return str(sandbox.snapshot or "").startswith(AGENT_SNAPSHOT_PREFIX) or \
        (sandbox.labels or {}).get(MIGRATED_LABEL) == "native"


def check_cutover(allow_legacy: bool) -> None:
    """Refuse a Worker deploy that would leave Sandboxes on the ACP gateway."""
    snapshot = configured_snapshot()
    if not snapshot.startswith(AGENT_SNAPSHOT_PREFIX):
        sys.exit(f"refusing to deploy: wrangler.toml DAYTONA_SNAPSHOT is {snapshot!r}, not an "
                 f"{AGENT_SNAPSHOT_PREFIX}* snapshot. New Sandboxes would run the ACP gateway, which "
                 "this Worker cannot talk to. See cf_worker/README.md, Cutting Over to the Native API.")
    _, sandboxes = managed_sandboxes()
    legacy = [f"{s.id[:8]} ({s.snapshot}, {s.state})" for s in sandboxes if not runs_agent_gateway(s)]
    if legacy and not allow_legacy:
        sys.exit("refusing to deploy: these Sandboxes still run the ACP gateway, so their calls would "
                 f"fail: {', '.join(legacy)}. Migrate them first with `deploy.py --no-deploy "
                 "--patch-sandboxes --apply`, or delete them, or pass --allow-legacy-sandboxes.")
    for line in legacy:
        print(f"warning: sandbox {line} still runs the ACP gateway", file=sys.stderr)


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
    """Bring one Sandbox's snapshot-owned files and OpenCode up to date, migrating an ACP-era
    one in place; leaves it started."""
    from build_sandbox_snapshot import OPENCODE_URL, OPENCODE_VERSION, SERVICE_CHECK, SNAPSHOT_FILES
    if not str(sandbox.state).endswith("STARTED"):
        daytona.start(sandbox, timeout=120)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    def download(path: str) -> bytes | None:
        try:
            return sandbox.fs.download_file(path)
        except Exception:
            return None

    def run(command: str) -> str:
        result = sandbox.process.exec(command, timeout=180)
        if result.exit_code:
            raise RuntimeError(f"{command!r} exited {result.exit_code}: {str(result.result)[-800:]}")
        return str(result.result)

    changed = []
    for source, destination, mode in SNAPSHOT_FILES:
        want = (ROOT / source).read_bytes()
        if (have := download(destination)) != want:
            changed.append((destination, want, have, mode))
    # An ACP-era image starts this path; it becomes the shim, which no snapshot file covers.
    if (legacy := download(LEGACY_ENTRYPOINT)) is not None and legacy != LEGACY_ENTRYPOINT_SHIM:
        changed.append((LEGACY_ENTRYPOINT, LEGACY_ENTRYPOINT_SHIM, legacy, "755"))
    installed = run("opencode --version 2>&1 || true").strip()
    upgrade = not installed.endswith(OPENCODE_VERSION)
    name = sandbox.id[:8]
    if not changed and not upgrade:
        print(f"sandbox {name}: snapshot files and OpenCode {OPENCODE_VERSION} already current")
        if legacy is not None and apply:
            sandbox.set_labels({**(sandbox.labels or {}), MIGRATED_LABEL: "native"})
        return
    if upgrade:
        print(f"sandbox {name}: {'installing' if apply else 'would install'} OpenCode "
              f"{OPENCODE_VERSION} (has {installed or 'none'})")
    for destination, _, have, _ in changed:
        print(f"sandbox {name}: {'patching' if apply else 'would patch'} {destination}"
              f"{'' if have is not None else ' (missing)'}")
    if not apply:
        return

    restored = []
    binary_backup = f"/usr/local/bin/opencode.dogwalk-backup-{stamp}"
    try:
        if upgrade:
            # Fetched here and uploaded, so the Sandbox needs no egress to GitHub. The rename
            # leaves a running `opencode acp` its old inode until the refresh stops it.
            with urllib.request.urlopen(OPENCODE_URL, timeout=300) as response:
                sandbox.fs.upload_file(response.read(), "/tmp/opencode-dogwalk.tar.gz")
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
        if any(d.endswith("sandbox_agent_gateway.py") for d, *_ in changed):
            run("cd /tmp && python /opt/dogwalk/sandbox_agent_gateway.py test")
            print(f"sandbox {name}: patched gateway passed its self-test")
        if upgrade:
            # Under a scratch HOME: the old gateway's `opencode acp` may still hold the real
            # database, which the new version migrates only once the Sandbox restarts.
            check = SERVICE_CHECK.replace("/home/daytona/.local/state", "$HOME/.local/state")
            run('export HOME="$(mktemp -d)" && cd "$HOME" && sh -c ' + shlex.quote(check)
                + '; status=$?; rm -rf "$HOME"; exit $status')
            print(f"sandbox {name}: OpenCode {OPENCODE_VERSION} served /api/info")
        if legacy is not None:
            # The shim must reach the Agent Gateway's own argument parser.
            if "usage: sandbox_agent_gateway.py" not in run(f"python {LEGACY_ENTRYPOINT} --help"):
                raise RuntimeError("the legacy entrypoint shim did not start the Agent Gateway")
    except Exception:
        for destination, backup in restored:
            run(f"sudo cp -p {shlex.quote(backup)} {shlex.quote(destination)}" if backup
                else f"rm -f {shlex.quote(destination)}")
        print(f"sandbox {name}: patch failed; restored previous files", file=sys.stderr)
        raise
    if legacy is not None:
        sandbox.set_labels({**(sandbox.labels or {}), MIGRATED_LABEL: "native"})
        print(f"sandbox {name}: migrated in place to the Agent Gateway; labelled {MIGRATED_LABEL}=native")
    print(f"sandbox {name}: {len(changed)} file(s) patched{', OpenCode upgraded' if upgrade else ''}; "
          f"backups end in .dogwalk-backup-{stamp}")


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
    parser.add_argument("--allow-legacy-sandboxes", action="store_true",
                        help="deploy even though some Sandboxes still run the ACP gateway")
    args = parser.parse_args()
    if args.apply and not args.patch_sandboxes:
        parser.error("--apply only applies to --patch-sandboxes")
    if not args.no_deploy:
        check_cutover(args.allow_legacy_sandboxes)
        deploy()
    refresh(args.wait_minutes, args.patch_sandboxes, args.apply)
