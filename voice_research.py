# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""List, pull, and delete Voice Donations from the private dogwalk-voice-research R2 bucket.

    uv run --script voice_research.py list
    uv run --script voice_research.py pull [SESSION ...]    # default: every session not yet pulled
    uv run --script voice_research.py delete SESSION --yes  # a donor asked; also removes the local copy

Sessions are named by their random id (a prefix is enough) and dated by when they started:
a donor who asks for deletion names roughly when they called, which `list` shows. Pulled files
go to ~/Library/Application Support/Dogwalk/voice_research/<date>/<session>/, mode 0700 and
outside Git. Uses the personal Cloudflare profile only, as deploy.py does.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ACCOUNT_ID = "890753ccd6f0bbbfbe2c030cc44f6a87"  # Adam's personal Cloudflare account
BUCKET = "dogwalk-voice-research"
CF_PROFILE = Path.home() / "Library/Preferences/cloudflare/config/personal.json"
LOCAL = Path.home() / "Library/Application Support/Dogwalk/voice_research"
API = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/r2/buckets/{BUCKET}/objects"


def token() -> str:
    # A real CLI call refreshes the personal profile's OAuth bearer before we read it.
    env = {**os.environ, "CLOUDFLARE_ACCOUNT_ID": ACCOUNT_ID}
    subprocess.run(["cf", "workers", "list", "--profile", "personal"], env=env, capture_output=True, check=True)
    return json.loads(CF_PROFILE.read_text())["oauth_token"]


def request(bearer: str, url: str, method: str = "GET") -> bytes:
    req = urllib.request.Request(url, method=method, headers={"authorization": f"Bearer {bearer}"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def objects(bearer: str) -> list[dict]:
    found, cursor = [], ""
    while True:
        query = {"prefix": "donations/", "per_page": "1000", **({"cursor": cursor} if cursor else {})}
        page = json.loads(request(bearer, f"{API}?{urllib.parse.urlencode(query)}"))
        found += page["result"]
        cursor = (page.get("result_info") or {}).get("cursor") or ""
        if not (page.get("result_info") or {}).get("is_truncated") or not cursor:
            return found


def sessions(bearer: str) -> dict[str, dict]:
    """donations/<date>/<session>/<file> keys grouped by session id."""
    grouped: dict[str, dict] = {}
    for item in objects(bearer):
        _, date, session, name = item["key"].split("/", 3)
        entry = grouped.setdefault(session, {"date": date, "files": {}})
        entry["files"][name] = item["size"]
    return grouped


def resolve(grouped: dict[str, dict], prefixes: list[str]) -> list[str]:
    chosen = []
    for prefix in prefixes:
        matches = [session for session in grouped if session.startswith(prefix)]
        if len(matches) != 1:
            sys.exit(f"{prefix!r} matches {len(matches)} sessions")
        chosen.append(matches[0])
    return chosen


def object_url(date: str, session: str, name: str) -> str:
    return f"{API}/{urllib.parse.quote(f'donations/{date}/{session}/{name}')}"


def local_dir(date: str, session: str) -> Path:
    old = os.umask(0o077)
    try:
        LOCAL.parent.mkdir(parents=True, exist_ok=True)
        for path in (LOCAL, LOCAL / date, LOCAL / date / session):
            path.mkdir(mode=0o700, exist_ok=True)
            path.chmod(0o700)
    finally:
        os.umask(old)
    return LOCAL / date / session


def cmd_list(bearer: str, _args) -> None:
    grouped = sessions(bearer)
    if not grouped:
        print("no donations")
    for session, entry in sorted(grouped.items(), key=lambda item: item[1]["date"]):
        meta = json.loads(request(bearer, object_url(entry["date"], session, "session.json")))
        heard = sum(1 for r in meta["rounds"] if r["transcript"])
        audio = sum(1 for name in entry["files"] if name.endswith(".wav"))
        pulled = "pulled" if (LOCAL / entry["date"] / session / "session.json").exists() else ""
        print(f"{meta['started_at'][:19]}Z  {session}  {meta['ended_by'] or 'hung up'}  "
              f"{audio}/{len(meta['rounds'])} rounds of audio, {heard} heard  "
              f"{sum(entry['files'].values()) // 1024} KiB  {meta['consent_version']}  {pulled}")


def cmd_pull(bearer: str, args) -> None:
    grouped = sessions(bearer)
    chosen = resolve(grouped, args.sessions) if args.sessions else [
        session for session, entry in grouped.items() if not (LOCAL / entry["date"] / session / "session.json").exists()]
    for session in chosen:
        entry = grouped[session]
        target = local_dir(entry["date"], session)
        for name, size in sorted(entry["files"].items()):
            path = target / name
            if path.exists() and path.stat().st_size == size and name != "session.json":
                continue
            data = request(bearer, object_url(entry["date"], session, name))
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(data)
        print(f"{session} -> {target}")
    if not chosen:
        print("nothing new to pull")


def cmd_delete(bearer: str, args) -> None:
    grouped = sessions(bearer)
    [session] = resolve(grouped, [args.session])
    entry = grouped[session]
    if not args.yes:
        sys.exit(f"would delete {len(entry['files'])} objects of {session} ({entry['date']}); rerun with --yes")
    for name in entry["files"]:
        request(bearer, object_url(entry["date"], session, name), method="DELETE")
    shutil.rmtree(LOCAL / entry["date"] / session, ignore_errors=True)
    print(f"deleted {session}: {len(entry['files'])} objects and any local copy")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    pull = commands.add_parser("pull")
    pull.add_argument("sessions", nargs="*")
    delete = commands.add_parser("delete")
    delete.add_argument("session")
    delete.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    {"list": cmd_list, "pull": cmd_pull, "delete": cmd_delete}[args.command](token(), args)


if __name__ == "__main__":
    main()
