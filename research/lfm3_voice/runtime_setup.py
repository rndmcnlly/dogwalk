# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Download pinned research weights, or launch the dedicated loopback server."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"
REVISION = "39670cb5c42e9c4bfe046519ba7ffbe47f9552ad"
REPO = "LiquidAI/LFM3-Audio-2B-GGUF"
FILES = ("LFM3-Audio-2B-Q8_0.gguf", "mmproj-LFM3-Audio-2B-Q8_0.gguf")
BINARY = "/opt/homebrew/Cellar/llama.cpp/0.9.0/bin/llama-server"


def download():
    url = f"https://huggingface.co/api/models/{REPO}/tree/{REVISION}?lfs=true"
    with urllib.request.urlopen(url) as response:
        entries = {entry["path"]: entry for entry in json.load(response)}
    manifest = {"repo": REPO, "revision": REVISION, "files": {}}
    for name in FILES:
        entry = entries[name]
        expected = entry["lfs"]["oid"]
        target = ARTIFACTS / name
        if not target.exists():
            partial = target.with_suffix(".gguf.partial")
            print(f"Downloading {name}: {entry['size']} bytes", flush=True)
            with urllib.request.urlopen(f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}") as response, partial.open("wb") as output:
                while chunk := response.read(8 * 1024 * 1024):
                    output.write(chunk)
            partial.rename(target)
        with target.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise RuntimeError(f"SHA256 mismatch: {target}")
        manifest["files"][name] = {"sha256": actual, "size": target.stat().st_size}
        print(f"Verified {name}: {actual}", flush=True)
    (ARTIFACTS / "model-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def start():
    for name in FILES:
        if not (ARTIFACTS / name).is_file():
            raise RuntimeError(f"Run download first: missing {name}")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 18391))
    command = [BINARY, "-m", str(ARTIFACTS / FILES[0]), "--mmproj", str(ARTIFACTS / FILES[1]),
               "--host", "127.0.0.1", "--port", "18391", "-c", "32768", "-np", "1", "-ngl", "99", "--alias", "lfm3-audio-2b-q8"]
    with (ARTIFACTS / "server.log").open("ab") as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, cwd=ROOT)
    (ARTIFACTS / "server.pid").write_text(str(process.pid) + "\n")
    (ARTIFACTS / "server-command.json").write_text(json.dumps(command, indent=2) + "\n")
    print(f"Started PID {process.pid}; log: {ARTIFACTS / 'server.log'}", flush=True)
    print("Readiness: GET http://127.0.0.1:18391/health")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("download", "start"))
    arguments = parser.parse_args()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / ".gitignore").write_text("*\n")
    {"download": download, "start": start}[arguments.action]()
