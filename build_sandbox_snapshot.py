#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["daytona==0.231.0"]
# ///
"""Build the versioned Daytona snapshot used by Dogwalk sandboxes."""

from __future__ import annotations

import argparse
from pathlib import Path

from daytona import (
    CreateSnapshotParams,
    Daytona,
    DaytonaNotFoundError,
    Image,
    Resources,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_NAME = "dogwalk-acp-20261108-v2"
OPENCODE_VERSION = "2.2.5"
BASE_IMAGE = (
    "python:3.13.5-slim-bookworm@"
    "sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419"
)
# Daytona's computer-use plugin reports 503 until these X11 and VNC packages
# exist; its /computeruse/start then launches Xvfb, xfce4, x11vnc, and noVNC.
DESKTOP_PACKAGES = (
    "xvfb xfce4 xfce4-terminal x11vnc novnc dbus-x11 libx11-6 libxrandr2 "
    "libxext6 libxrender1 libxfixes3 libxss1 libxtst6 libxi6 "
    "chromium fonts-dejavu-core"
)
# The binary guard plugin detects types with file(1); its hints name these. ffmpeg and
# ImageMagick also let sessions transcode audio and downscale images for reading.
BINARY_TOOLS = "file poppler-utils unzip sqlite3 xxd binutils ffmpeg imagemagick"
# Chromium's own sandbox and /dev/shm sizing do not fit inside the container.
CHROMIUM_FLAGS = (
    'export CHROMIUM_FLAGS="$CHROMIUM_FLAGS '
    '--no-sandbox --test-type --disable-dev-shm-usage"'
)
ACP_INITIALIZE = (
    '{"jsonrpc":"2.0","id":1,"method":"initialize",'
    '"params":{"protocolVersion":1,"clientCapabilities":{}}}'
)


def snapshot_image() -> Image:
    return (
        Image.base(BASE_IMAGE)
        .run_commands(
            "apt-get update && DEBIAN_FRONTEND=noninteractive "
            "apt-get install -y --no-install-recommends "
            "bash ca-certificates curl dnsutils git iputils-ping jq netcat-openbsd "
            f"procps ripgrep sudo tar tmux {BINARY_TOOLS} {DESKTOP_PACKAGES} && "
            "rm -rf /var/lib/apt/lists/*",
            f"printf '%s\\n' '{CHROMIUM_FLAGS}' > /etc/chromium.d/dogwalk",
            "pip install --no-cache-dir aiohttp==3.14.2",
            "curl -fsSL --retry 5 --retry-all-errors "
            f"https://github.com/anomalyco/opencode/releases/download/v{OPENCODE_VERSION}/"
            "opencode-linux-x64.tar.gz | tar -xz -C /usr/local/bin opencode && "
            "chmod 755 /usr/local/bin/opencode",
            "useradd --create-home --shell /bin/bash daytona && "
            "mkdir -p /opt/dogwalk /home/daytona/.config/opencode "
            "/home/daytona/.config/dogwalk && "
            "printf 'daytona ALL=(ALL) NOPASSWD:ALL\\n' > /etc/sudoers.d/daytona && "
            "chmod 0440 /etc/sudoers.d/daytona",
        )
        .add_local_file(
            ROOT / "sandbox_acp_gateway.py", "/opt/dogwalk/sandbox_acp_gateway.py"
        )
        .add_local_file(
            ROOT / "sandbox_opencode.json",
            "/home/daytona/.config/opencode/opencode.json",
        )
        # Global rules OpenCode loads into every session: narrate between tool calls,
        # since Dogwalk relays the Agent's text, not its tool arguments.
        .add_local_file(
            ROOT / "sandbox_opencode_agents.md",
            "/home/daytona/.config/opencode/AGENTS.md",
        )
        # OpenCode discovers plugins in this directory without a config entry.
        .add_local_file(
            ROOT / "sandbox_opencode_binary_guard.ts",
            "/home/daytona/.config/opencode/plugins/binary-guard.ts",
        )
        .run_commands(
            "chmod 755 /opt/dogwalk/sandbox_acp_gateway.py && "
            "chmod 0700 /home/daytona/.config/dogwalk && "
            "chown -R daytona:daytona /home/daytona /opt/dogwalk",
            # OpenCode 2.x prints "opencode v2.2.5"; 1.x printed the bare version.
            f'v="$(opencode --version)" && echo "$v" && '
            f'case "$v" in *"{OPENCODE_VERSION}") ;; *) exit 1 ;; esac',
            "python -c 'import aiohttp'",
            "file --version && command -v pdftotext unzip sqlite3 xxd strings ffmpeg convert",
            "cd /tmp && python /opt/dogwalk/sandbox_acp_gateway.py test",
            # The Agent command must still answer ACP initialize over stdio.
            f"cd /home/daytona && ( printf '%s\\n' '{ACP_INITIALIZE}'; sleep 15 ) | "
            "sudo -u daytona -H timeout 30 /usr/local/bin/opencode acp | "
            "grep -q '\"protocolVersion\":1'",
            # Leave no OpenCode state from these checks in the image. The root
            # `opencode --version` above creates $TMPDIR/opencode owned by root,
            # which the daytona user then cannot write tool output into.
            "rm -rf /home/daytona/.local /home/daytona/.cache /tmp/opencode "
            "/tmp/.bun-*.so",
            "sudo -u daytona sudo -n true",
            "ping -c 1 127.0.0.1",
            "tmux -V",
            "chromium --version",
        )
        .env(
            {
                "HOME": "/home/daytona",
                # Read by Daytona's computer-use plugin when it starts Xvfb.
                "VNC_RESOLUTION": "1280x800",
            }
        )
        .workdir("/home/daytona")
        .dockerfile_commands(["USER daytona"])
        .entrypoint(
            [
                "/usr/local/bin/python",
                "/opt/dogwalk/sandbox_acp_gateway.py",
                "serve",
                "--workspace",
                "/home/daytona",
                "--host",
                "0.0.0.0",
                "--port",
                "8765",
                "--agent-command",
                "/usr/local/bin/opencode acp",
            ]
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=DEFAULT_NAME)
    args = parser.parse_args()

    daytona = Daytona()
    try:
        existing = daytona.snapshot.get(args.name)
    except DaytonaNotFoundError:
        pass
    else:
        raise SystemExit(
            f"Snapshot {existing.name!r} already exists; use a new versioned name."
        )

    snapshot = daytona.snapshot.create(
        CreateSnapshotParams(
            name=args.name,
            image=snapshot_image(),
            resources=Resources(cpu=2, memory=4, disk=10),
        ),
        on_logs=lambda chunk: print(chunk, end=""),
        timeout=0,
    )
    print(f"\ncreated snapshot {snapshot.name} ({snapshot.id}) state={snapshot.state}")


if __name__ == "__main__":
    main()
