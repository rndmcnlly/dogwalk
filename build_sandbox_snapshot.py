#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["daytona==0.199.0"]
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
DEFAULT_NAME = "dogwalk-acp-20260721-v3"
OPENCODE_VERSION = "1.18.4"
BASE_IMAGE = (
    "python:3.13.5-slim-bookworm@"
    "sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419"
)


def snapshot_image() -> Image:
    return (
        Image.base(BASE_IMAGE)
        .run_commands(
            "apt-get update && apt-get install -y --no-install-recommends "
            "bash ca-certificates curl dnsutils git iputils-ping jq netcat-openbsd "
            "procps ripgrep sudo tar && "
            "rm -rf /var/lib/apt/lists/*",
            "pip install --no-cache-dir aiohttp==3.14.2",
            "curl -fsSL "
            f"https://github.com/anomalyco/opencode/releases/download/v{OPENCODE_VERSION}/"
            "opencode-linux-x64.tar.gz | tar -xz -C /usr/local/bin opencode && "
            "chmod 755 /usr/local/bin/opencode",
            "useradd --create-home --shell /bin/bash daytona && "
            "mkdir -p /opt/dogwalk /home/daytona/.config/opencode/tools "
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
        .add_local_file(
            ROOT / "sandbox_tools/publish_review_bundle.ts",
            "/home/daytona/.config/opencode/tools/publish_review_bundle.ts",
        )
        .add_local_file(
            ROOT / "sandbox_tools/register_ephemeral_service.ts",
            "/home/daytona/.config/opencode/tools/register_ephemeral_service.ts",
        )
        .run_commands(
            "chmod 755 /opt/dogwalk/sandbox_acp_gateway.py && "
            "chmod 0700 /home/daytona/.config/dogwalk && "
            "chmod 0644 /home/daytona/.config/opencode/tools/*.ts && "
            "chown -R daytona:daytona /home/daytona /opt/dogwalk",
            f'test "$(opencode --version)" = "{OPENCODE_VERSION}"',
            "python -c 'import aiohttp'",
            "sudo -u daytona sudo -n true",
            "ping -c 1 127.0.0.1",
        )
        .env(
            {
                "HOME": "/home/daytona",
                "OPENCODE_CONFIG": "/home/daytona/.config/opencode/opencode.json",
                "OPENCODE_DISABLE_AUTOUPDATE": "true",
                "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true",
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
                "/usr/local/bin/opencode acp --pure --cwd {cwd}",
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
            resources=Resources(cpu=1, memory=1, disk=3),
        ),
        on_logs=lambda chunk: print(chunk, end=""),
        timeout=0,
    )
    print(f"\ncreated snapshot {snapshot.name} ({snapshot.id}) state={snapshot.state}")


if __name__ == "__main__":
    main()
