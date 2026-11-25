#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["daytona==2.2.0"]
# ///
"""Build the versioned Daytona image that Dogwalk sandboxes boot from."""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path

from daytona import Daytona
from daytona.v2 import ImageBuilder, NotFoundError


ROOT = Path(__file__).resolve().parent
DEFAULT_NAME = "dogwalk-agent-20261125-v1"
REGION = "us-east-ash-1"  # an image builds in one home region, so Sandboxes are created there too
OPENCODE_VERSION = "2.2.7"
# Also what `deploy.py --patch-sandboxes` installs into an existing Sandbox with another version.
OPENCODE_URL = (
    "https://github.com/anomalyco/opencode/releases/download/"
    f"v{OPENCODE_VERSION}/opencode-linux-x64.tar.gz"
)
BASE_IMAGE = (
    "python:3.13.5-slim-bookworm@"
    "sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419"
)
# Daytona's runtime brings its own display and VNC server; the window manager, browser, and
# noVNC (the page a Desktop grant opens) are ours.
DESKTOP_PACKAGES = (
    "xfce4 xfce4-terminal novnc dbus-x11 libx11-6 libxrandr2 "
    "libxext6 libxrender1 libxfixes3 libxss1 libxtst6 libxi6 "
    "chromium fonts-dejavu-core"
)
# The binary guard plugin detects types with file(1); its hints name these. ffmpeg and
# ImageMagick also let sessions transcode audio and downscale images for reading.
BINARY_TOOLS = "file poppler-utils unzip sqlite3 xxd binutils ffmpeg imagemagick"
# The Takeover Terminal. Daytona has no shareable web terminal, so a grant starts this.
TTYD_URL = "https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64"
TTYD_SHA256 = "8a217c968aba172e0dbf3f34447218dc015bc4d5e59bf51db2f2cd12b7be4f55"
# Start the shared OpenCode service as the daytona user, wait for it to register, and
# check its API with the password it registered; then stop it.
SERVICE_CHECK = (
    "state=/home/daytona/.local/state/opencode/service.json; "
    "opencode serve --service & pid=$!; "
    'for i in $(seq 60); do [ -f "$state" ] && break; sleep 0.5; done; '
    'curl -fsS -u "opencode:$(jq -r .password "$state")" "$(jq -r .url "$state")/api/info" '
    f"| jq -e '.version == \"{OPENCODE_VERSION}\"'; "
    'status=$?; kill "$pid"; wait "$pid"; exit "$status"'
)


# Repository files the image owns: (source, Sandbox path, mode). `deploy.py
# --patch-sandboxes` brings existing Sandboxes up to date from this same list, so
# changing one of these files rarely needs a new image or a replaced Sandbox.
# Packages, binaries (OpenCode itself), and image settings are not covered.
IMAGE_FILES = (
    ("sandbox_agent_gateway.py", "/opt/dogwalk/sandbox_agent_gateway.py", "755"),
    ("sandbox_opencode.json", "/home/daytona/.config/opencode/opencode.json", "644"),
    # Global rules OpenCode loads into every session: narrate between tool calls,
    # since Dogwalk relays the Agent's text, not its tool arguments.
    ("sandbox_opencode_agents.md", "/home/daytona/.config/opencode/AGENTS.md", "644"),
    # OpenCode discovers plugins in this directory without a config entry.
    ("sandbox_opencode_binary_guard.ts", "/home/daytona/.config/opencode/plugins/binary-guard.ts", "644"),
)


def sandbox_image() -> ImageBuilder:
    image = (
        ImageBuilder.from_registry(BASE_IMAGE)
        .run_commands(
            "apt-get update && DEBIAN_FRONTEND=noninteractive "
            "apt-get install -y --no-install-recommends "
            "bash ca-certificates curl dnsutils git iputils-ping jq netcat-openbsd "
            f"procps ripgrep sudo tar tmux {BINARY_TOOLS} {DESKTOP_PACKAGES} && "
            "rm -rf /var/lib/apt/lists/*",
            # Daytona names a secret's variable after the secret, and secrets are project-wide.
            # Login shells get the usual name; the gateway does the same for the Agent.
            "printf '%s\\n' 'export OPENAI_API_KEY=\"$DOGWALK_OPENAI_API_KEY\"' > /etc/profile.d/dogwalk.sh",
            "pip install --no-cache-dir aiohttp==3.14.2",
            f"curl -fsSL --retry 5 --retry-all-errors {OPENCODE_URL} "
            "| tar -xz -C /usr/local/bin opencode && "
            "chmod 755 /usr/local/bin/opencode",
            f"curl -fsSL --retry 5 --retry-all-errors {TTYD_URL} -o /usr/local/bin/ttyd && "
            f"echo '{TTYD_SHA256}  /usr/local/bin/ttyd' | sha256sum -c - && "
            "chmod 755 /usr/local/bin/ttyd",
            "useradd --create-home --shell /bin/bash daytona && "
            "mkdir -p /opt/dogwalk /home/daytona/.config/opencode "
            "/home/daytona/.config/dogwalk && "
            "printf 'daytona ALL=(ALL) NOPASSWD:ALL\\n' > /etc/sudoers.d/daytona && "
            "chmod 0440 /etc/sudoers.d/daytona",
        )
    )
    for source, destination, _mode in IMAGE_FILES:
        image = image.add_local_file(ROOT / source, destination)
    return (
        image.run_commands(
            "chmod 755 /opt/dogwalk/sandbox_agent_gateway.py && "
            "chmod 0700 /home/daytona/.config/dogwalk && "
            "chown -R daytona:daytona /home/daytona /opt/dogwalk",
            # OpenCode 2.x prints "opencode v2.2.7"; 1.x printed the bare version.
            f'v="$(opencode --version)" && echo "$v" && '
            f'case "$v" in *"{OPENCODE_VERSION}") ;; *) exit 1 ;; esac',
            "python -c 'import aiohttp'",
            "file --version && command -v pdftotext unzip sqlite3 xxd strings ffmpeg convert",
            "cd /tmp && python /opt/dogwalk/sandbox_agent_gateway.py test",
            "cd /home/daytona && sudo -u daytona -H sh -c " + shlex.quote(SERVICE_CHECK),
            # Leave no OpenCode state from these checks in the image. The root
            # `opencode --version` above creates $TMPDIR/opencode owned by root,
            # which the daytona user then cannot write tool output into. The service
            # check leaves two files named service.json: the saved password (and any
            # port) in ~/.config/opencode, which each Sandbox must generate for itself,
            # and the running service's url, pid, and password in
            # ~/.local/state/opencode, which goes with ~/.local.
            "rm -rf /home/daytona/.local /home/daytona/.cache /tmp/opencode "
            "/home/daytona/.config/opencode/service.json /tmp/.bun-*.so",
            "sudo -u daytona sudo -n true",
            "ping -c 1 127.0.0.1",
            "tmux -V",
            "chromium --version",
            "command -v websockify ttyd pgrep && test -f /usr/share/novnc/vnc.html",
        )
        .env(
            {"HOME": "/home/daytona"}
        )
        .workdir("/home/daytona")
        .user("daytona")
        .entrypoint(
            "/usr/local/bin/python",
            "/opt/dogwalk/sandbox_agent_gateway.py",
            "serve",
            "--workspace",
            "/home/daytona",
            "--host",
            "0.0.0.0",
            "--port",
            "8765",
            "--agent-command",
            "/usr/local/bin/opencode serve --service",
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=DEFAULT_NAME)
    args = parser.parse_args()

    images = Daytona().v2.images
    try:
        existing = images.get(args.name)
    except NotFoundError:
        pass
    else:
        raise SystemExit(f"Image {existing.name!r} already exists; use a new versioned name.")

    image = images.build(sandbox_image(), name=args.name, region=REGION)
    images.follow_build_logs(image.id, on_logs=lambda chunk: print(chunk, end=""))
    image = images.get(image.id, wait="ready", timeout=1800)
    if image.status != "ready":
        raise SystemExit(f"build {image.status}: {image.error}")
    print(f"\ncreated image {image.name} ({image.id}) status={image.status}")


if __name__ == "__main__":
    main()
