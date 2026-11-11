# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Send Dogwalk's metadata-only OTLP/JSON export to a stock OTLP/HTTP receiver.

    uv run --script otlp_send.py CAxxxxxxxx                    # fetch one call from the Worker, POST it
    uv run --script otlp_send.py CAxxxxxxxx --save /tmp/otlp   # also keep the fetched bodies locally
    uv run --script otlp_send.py --file /tmp/dogwalk-otlp-fixture   # POST synthetic fixture bodies
    uv run --script otlp_send.py ... --endpoint http://localhost:4318

The Worker builds the bodies (cf_worker/src/otlp.ts); this script is only transport. It posts
them unchanged to /v1/logs and /v1/traces, honours OTLP partial success, and retries
retryable statuses with backoff. Fetching uses the operator password from macOS Keychain,
like playtest.py. Saved bodies are metadata-only but come from production: keep them out of Git.
"""
import argparse
import base64
import email.message
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

ORIGIN = "https://dogwalk.tools"
RETRYABLE = {429, 502, 503, 504}  # OTLP/HTTP spec: retry these, never other 4xx.


def admin_get(path: str) -> tuple[dict, "email.message.Message"]:
    password = subprocess.run(["security", "find-generic-password", "-a", "adam", "-s", "dogwalk-admin", "-w"],
                              capture_output=True, text=True, check=True).stdout.strip()
    request = urllib.request.Request(f"{ORIGIN}{path}", headers={
        "authorization": "Basic " + base64.b64encode(f"adam:{password}".encode()).decode(),
        "user-agent": "dogwalk-otlp-send/1",  # Cloudflare's bot filter rejects Python-urllib
    })
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response), response.headers  # case-insensitive lookups


def fetch_call(call_sid: str) -> tuple[list[dict], dict]:
    """Logs arrive in insertion-cursor pages; traces in one bounded body."""
    pages, after = [], 0
    while True:
        body, headers = admin_get(f"/admin/api/otlp/v1/logs?call_sid={call_sid}&after={after}")
        if count(body, "logs"):
            pages.append(body)
        cursor = int(headers["x-dogwalk-cursor"])
        if headers.get("x-dogwalk-has-more") != "true":
            break
        if cursor <= after:
            raise SystemExit("log cursor did not advance")
        after = cursor
    traces, headers = admin_get(f"/admin/api/otlp/v1/traces?call_sid={call_sid}")
    if headers.get("x-dogwalk-truncated") == "true":
        print("warning: call exceeds the export row cap; spans cover only its first rows", file=sys.stderr)
    return pages, traces


def post(endpoint: str, signal: str, body: dict, attempts: int = 5) -> int:
    """POST one export request. Returns the number of items the receiver rejected."""
    data = json.dumps(body).encode()
    for attempt in range(attempts):
        request = urllib.request.Request(f"{endpoint.rstrip('/')}/v1/{signal}", data=data, method="POST",
                                         headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                reply = json.loads(response.read() or b"{}")
            partial = reply.get("partialSuccess") or {}
            rejected = int(partial.get(f"rejected{'LogRecords' if signal == 'logs' else 'Spans'}", 0) or 0)
            if rejected or partial.get("errorMessage"):
                print(f"{signal}: receiver rejected {rejected}: {partial.get('errorMessage', '')}", file=sys.stderr)
            return rejected
        except urllib.error.HTTPError as error:
            if error.code not in RETRYABLE or attempt == attempts - 1:
                raise SystemExit(f"{signal}: HTTP {error.code}: {error.read()[:300]!r}")
            delay = float(error.headers.get("retry-after") or 2 ** attempt)
        except urllib.error.URLError as error:
            if attempt == attempts - 1:
                raise SystemExit(f"{signal}: receiver unreachable: {error.reason}")
            delay = 2 ** attempt
        time.sleep(min(delay, 30))
    return 0


def count(body: dict, signal: str) -> int:
    if signal == "logs":
        return sum(len(s["logRecords"]) for r in body["resourceLogs"] for s in r["scopeLogs"])
    return sum(len(s["spans"]) for r in body["resourceSpans"] for s in r["scopeSpans"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("call_sid", nargs="?")
    parser.add_argument("--file", type=pathlib.Path, help="directory holding logs.json and traces.json")
    parser.add_argument("--save", type=pathlib.Path, help="write fetched bodies here")
    parser.add_argument("--endpoint", default="http://localhost:4318")
    args = parser.parse_args()
    if bool(args.call_sid) == bool(args.file):
        parser.error("give exactly one of CALL_SID or --file")
    if args.file:
        log_pages = [json.loads((args.file / "logs.json").read_text())]
        traces = json.loads((args.file / "traces.json").read_text())
    else:
        log_pages, traces = fetch_call(args.call_sid)
        if args.save:
            args.save.mkdir(parents=True, exist_ok=True)
            for i, page in enumerate(log_pages):
                (args.save / f"logs-{i:03}.json").write_text(json.dumps(page))
            (args.save / "traces.json").write_text(json.dumps(traces))
    sent = rejected = 0
    for page in log_pages:
        sent += count(page, "logs")
        rejected += post(args.endpoint, "logs", page)
    spans = count(traces, "traces")
    rejected_spans = post(args.endpoint, "traces", traces) if spans else 0
    print(f"logs: {sent} sent, {rejected} rejected · spans: {spans} sent, {rejected_spans} rejected")


if __name__ == "__main__":
    main()
