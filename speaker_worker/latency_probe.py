# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Measure the deployed speaker verifier's cold and warm latency through dogwalk's operator endpoints.

    uv run --script speaker_worker/latency_probe.py sequential [-n 20] [--keepalive]
    uv run --script speaker_worker/latency_probe.py concurrent [-k 2 4] [--rounds 3]
    uv run --script speaker_worker/latency_probe.py idle [--gaps 30 60 120 300 600 1200] [--repeats 1]
    uv run --script speaker_worker/latency_probe.py verify WAV [-n 5] [--user 1]
    uv run --script speaker_worker/latency_probe.py analytics [--hours 24]

Each probe is `GET /admin/api/voice-verify/health`, which runs one real inference on a synthetic
signal; its `ms` is the SPEAKER RPC alone, measured inside dogwalk, so client network time is
excluded. A probe over COLD_MS is counted as a model load in a fresh instance. Requests from this
laptop reach a nearby colo, which is not necessarily the one serving Twilio's webhooks. `verify`
scores a local WAV (8 or 16 kHz mono 16-bit) against a User's Voice Reference; use synthetic audio
(macOS `say`), never commit it. `analytics` reads dogwalk-speaker's invocation metrics (status,
CPU and wall time quantiles) from the GraphQL Analytics API, read-only, with the personal profile.
Prints one JSON line per probe and a summary; nothing is written to disk.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import subprocess
import sys
import http.client
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ORIGIN = "https://dogwalk.tools"
COLD_MS = 2000
ACCOUNT_ID = "890753ccd6f0bbbfbe2c030cc44f6a87"  # Adam's personal Cloudflare account
CF_PROFILE = Path.home() / "Library/Preferences/cloudflare/config/personal.json"


def auth() -> str:
    password = subprocess.run(["security", "find-generic-password", "-a", "adam", "-s", "dogwalk-admin", "-w"],
                              capture_output=True, text=True, check=True).stdout.strip()
    return "Basic " + base64.b64encode(f"adam:{password}".encode()).decode()


def call(authorization: str, path: str, body: bytes | None = None,
         connection: http.client.HTTPSConnection | None = None) -> dict:
    """One request on a fresh connection, or on `connection` (kept alive across calls)."""
    headers = {"authorization": authorization, "user-agent": "dogwalk-research/1"}
    if body is not None:
        headers["content-type"] = "audio/wav"
    started = time.monotonic()
    if connection is not None:
        connection.request("POST" if body else "GET", path, body=body, headers=headers)
        resp = connection.getresponse()
        status, raw, ray = resp.status, resp.read(), resp.headers.get("cf-ray", "")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
    else:
        req = urllib.request.Request(ORIGIN + path, data=body, headers=headers, method="POST" if body else "GET")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status, payload, ray = resp.status, json.loads(resp.read()), resp.headers.get("cf-ray", "")
        except urllib.error.HTTPError as error:
            status, ray = error.code, error.headers.get("cf-ray", "")
            try:
                payload = json.loads(error.read())
            except ValueError:
                payload = {}
    total = round((time.monotonic() - started) * 1000)
    ms = payload.get("ms")
    return {"status": status, "rpc_ms": ms, "total_ms": total, "colo": ray.rpartition("-")[2],
            "cold": ms is not None and ms > COLD_MS, "error": payload.get("error")}


def probe(authorization: str, connection: http.client.HTTPSConnection | None = None, **extra) -> dict:
    result = {**extra, **call(authorization, "/admin/api/voice-verify/health", connection=connection)}
    print(json.dumps(result), flush=True)
    return result


def summarize(label: str, results: list[dict]) -> None:
    ok = [r for r in results if r["rpc_ms"] is not None]
    cold = [r["rpc_ms"] for r in ok if r["cold"]]
    warm = [r["rpc_ms"] for r in ok if not r["cold"]]

    def spread(values: list[int]) -> str:
        if not values:
            return "none"
        values = sorted(values)
        return f"n={len(values)} min={values[0]} median={statistics.median(values):.0f} max={values[-1]}"

    errors = {}
    for r in results:
        if r["rpc_ms"] is None:
            errors[f"{r['status']} {r['error']}"] = errors.get(f"{r['status']} {r['error']}", 0) + 1
    print(json.dumps({"summary": label, "requests": len(results), "cold": len(cold),
                      "cold_ms": spread(cold), "warm_ms": spread(warm), "errors": errors,
                      "colos": sorted({r["colo"] for r in results})}), flush=True)


def sequential(args, authorization: str) -> None:
    # --keepalive sends every probe on one TCP connection, so they reach one Cloudflare machine.
    connection = http.client.HTTPSConnection(ORIGIN.removeprefix("https://"), timeout=60) if args.keepalive else None
    mode = "keepalive" if args.keepalive else "sequential"
    summarize(mode, [probe(authorization, connection, mode=mode, i=i) for i in range(args.n)])


def concurrent(args, authorization: str) -> None:
    for k in args.k:
        results = []
        for round_ in range(args.rounds):
            with ThreadPoolExecutor(k) as pool:
                results += list(pool.map(lambda i: probe(authorization, mode="concurrent", k=k, round=round_, i=i), range(k)))
        summarize(f"concurrent k={k}", results)


def idle(args, authorization: str) -> None:
    # One connection pins every probe to one machine, so a cold probe after a gap means the
    # instance was evicted, not that routing chose another machine. /healthz every 20 s keeps
    # the connection open without touching the verifier; a replaced socket is reported.
    connection = http.client.HTTPSConnection(ORIGIN.removeprefix("https://"), timeout=60)
    results = []
    for repeat in range(args.repeats):
        for gap in args.gaps:
            probe(authorization, connection, mode="idle-warmup", gap=gap, repeat=repeat)
            sock = connection.sock
            deadline = time.monotonic() + gap
            while (left := deadline - time.monotonic()) > 0:
                time.sleep(min(20, left))
                connection.request("GET", "/healthz", headers={"user-agent": "dogwalk-research/1"})
                connection.getresponse().read()
            results.append(probe(authorization, connection, mode="idle", gap=gap, repeat=repeat,
                                 same_connection=connection.sock is sock))
    for gap in args.gaps:
        summarize(f"idle gap={gap}s", [r for r in results if r["gap"] == gap])


def verify(args, authorization: str) -> None:
    wav = Path(args.wav).read_bytes()
    results = []
    for i in range(args.n):
        result = {"mode": "verify", "i": i, **call(authorization, f"/admin/api/voice-verify?user={args.user}", wav)}
        print(json.dumps(result), flush=True)
        results.append(result)
    summarize("verify", results)


def analytics(args, _authorization: str) -> None:
    # A real CLI call refreshes the personal profile's OAuth bearer before we read it.
    env = {**os.environ, "CLOUDFLARE_ACCOUNT_ID": ACCOUNT_ID}
    subprocess.run(["cf", "workers", "list", "--profile", "personal"], env=env, capture_output=True, check=True)
    token = json.loads(CF_PROFILE.read_text())["oauth_token"]
    until = datetime.now(timezone.utc)
    since = until - timedelta(hours=args.hours)
    query = """query($account: string!, $since: Time!, $until: Time!) { viewer { accounts(filter: {accountTag: $account}) {
      workersInvocationsAdaptive(limit: 100, filter: {scriptName: "dogwalk-speaker", datetime_geq: $since, datetime_leq: $until},
                                 orderBy: [datetimeHour_ASC]) {
        sum { requests errors subrequests }
        quantiles { cpuTimeP50 cpuTimeP99 wallTimeP50 wallTimeP99 }
        dimensions { datetimeHour status }
      } } } }"""
    body = json.dumps({"query": query, "variables": {
        "account": ACCOUNT_ID, "since": since.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "until": until.isoformat(timespec="seconds").replace("+00:00", "Z")}}).encode()
    req = urllib.request.Request("https://api.cloudflare.com/client/v4/graphql", data=body, headers={
        "authorization": f"Bearer {token}", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read())
    if payload.get("errors"):
        sys.exit(json.dumps(payload["errors"]))
    for row in payload["data"]["viewer"]["accounts"][0]["workersInvocationsAdaptive"]:
        print(json.dumps({**row["dimensions"], **row["sum"], **row["quantiles"]}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sequential")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--keepalive", action="store_true")
    p = sub.add_parser("concurrent")
    p.add_argument("-k", type=int, nargs="+", default=[2, 4])
    p.add_argument("--rounds", type=int, default=3)
    p = sub.add_parser("idle")
    p.add_argument("--gaps", type=int, nargs="+", default=[30, 60, 120, 300, 600, 1200])
    p.add_argument("--repeats", type=int, default=1)
    p = sub.add_parser("verify")
    p.add_argument("wav")
    p.add_argument("-n", type=int, default=5)
    p.add_argument("--user", type=int, default=1)
    sub.add_parser("analytics").add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    globals()[args.command](args, None if args.command == "analytics" else auth())


if __name__ == "__main__":
    main()
