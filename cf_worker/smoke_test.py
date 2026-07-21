#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Assertion-based smoke test for the Dogwalk registration Worker."""

import argparse
import base64
import hashlib
import hmac
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCAL_TOKEN = "local-smoke-token"
LOCAL_ADMIN_PASSWORD = "local-admin-password"
DEFAULT_LOCAL = "http://127.0.0.1:8787"
DEFAULT_REMOTE = "https://dogwalk.lathe.tools"
LOCAL_INVITE = "ahead almighty apple"
TEST_PHONE = "+15555550100"
OTHER_PHONE = "+15555550101"
WORKER_DIR = Path(__file__).resolve().parent
DAYTONA_MOCK_PORT = 8790


class DaytonaMock(BaseHTTPRequestHandler):
    exists = False
    detail_reads = 0

    def sandbox(self, state: str = "started") -> dict:
        return {
            "id": "mock-sandbox",
            "name": "dogwalk-mock",
            "state": state,
            "desiredState": "started",
            "errorReason": None,
            "toolboxProxyUrl": f"http://127.0.0.1:{DAYTONA_MOCK_PORT}/toolbox",
        }

    def reply(self, value: dict) -> None:
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.startswith("/api/sandbox?"):
            self.reply({"items": [self.sandbox()] if self.exists else [], "nextCursor": None})
            return
        if self.path == "/api/sandbox/mock-sandbox":
            DaytonaMock.detail_reads += 1
            self.reply(self.sandbox("creating" if self.detail_reads == 1 else "started"))
            return
        self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/sandbox":
            DaytonaMock.exists = True
            DaytonaMock.detail_reads = 0
            self.reply(self.sandbox("creating"))
            return
        if self.path == "/api/sandbox/mock-sandbox/start":
            self.reply(self.sandbox("starting"))
            return
        if self.path == "/toolbox/mock-sandbox/process/execute":
            result = "ready" if payload.get("command") == "printf ready" else (
                "uptime_seconds=9000\nmemory_percent=25\ndisk_percent=12\n"
            )
            self.reply({"result": result, "exitCode": 0})
            return
        self.send_error(404)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


def wait_port(host: str, port: int, timeout: float = 15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def sign(url: str, params: dict[str, str], token: str) -> str:
    payload = url + "".join(k + (params[k] or "") for k in sorted(params))
    mac = hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(mac).decode()


def post(base_url: str, path: str, params: dict[str, str], token: str) -> tuple[int, str]:
    url = base_url + path
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "x-twilio-signature": sign(url, params, token),
            "content-type": "application/x-www-form-urlencoded",
            "user-agent": "twilio-smoke-test/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def get(base_url: str, path: str) -> tuple[int, str]:
    try:
        req = urllib.request.Request(base_url + path, headers={"user-agent": "twilio-smoke-test/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def first_sse_event(base_url: str, username: str, password: str, verbose: bool = False) -> tuple[int, str, dict]:
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    request = urllib.request.Request(
        base_url + "/admin/api/events" + ("?verbose=1" if verbose else ""),
        headers={
            "authorization": f"Basic {credentials}",
            "accept": "text/event-stream",
            "user-agent": "dogwalk-smoke-test/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        event = ""
        data = ""
        for _ in range(10):
            line = response.readline().decode().rstrip("\r\n")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
            elif not line and event and data:
                break
        return response.status, event, json.loads(data)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_tests(base_url: str, label: str, token: str, invite: str) -> int:
    print(f"testing {label}: {base_url}")

    status, body = get(base_url, "/healthz")
    require((status, body) == (200, "ok\n"), f"healthz: {status} {body!r}")

    status, _ = post(base_url, "/voice", {"From": TEST_PHONE, "CallSid": "CA_BOGUS"}, "bogus")
    require(status == 403, f"bogus signature returned {status}")

    status, body = post(base_url, "/voice", {"From": "anonymous", "CallSid": "CA_INVALID"}, token)
    require(status == 200 and "could not verify" in body, f"invalid phone accepted: {status} {body}")

    status, body = post(base_url, "/voice", {"From": TEST_PHONE, "CallSid": "CA_TEST1"}, token)
    require(status == 200 and body.startswith("<Response><Gather"), f"registration gather: {status} {body}")
    require('actionOnEmptyResult="true"' in body, "gather does not callback on silence")

    status, body = post(
        base_url,
        "/voice/claim?attempts=3",
        {"From": TEST_PHONE, "CallSid": "CA_SILENCE", "SpeechResult": "", "Confidence": "0"},
        token,
    )
    require(status == 200 and "Too many attempts" in body and "<Hangup/>" in body, f"attempt cap: {status} {body}")

    status, body = post(
        base_url,
        "/voice/claim?attempts=1",
        {"From": TEST_PHONE, "CallSid": "CA_TEST1", "SpeechResult": invite, "Confidence": "0.92"},
        token,
    )
    require(status == 200 and 'action="/voice/confirm?attempts=1"' in body, f"invite match: {status} {body}")
    require('hints="yes,again"' in body, "confirmation gather has incorrect hints")

    status, body = post(
        base_url,
        "/voice/confirm?attempts=1",
        {"From": OTHER_PHONE, "CallSid": "CA_TEST1", "SpeechResult": "yes", "Confidence": "0.95"},
        token,
    )
    require(status == 200 and "Let's start over" in body, f"pending claim crossed phone identities: {status} {body}")

    status, body = post(
        base_url,
        "/voice/confirm?attempts=1",
        {"From": TEST_PHONE, "CallSid": "CA_TEST1", "SpeechResult": "yes.", "Confidence": "0.95"},
        token,
    )
    require(status == 200 and "Registered" in body, f"confirmation: {status} {body}")

    status, body = post(base_url, "/voice", {"From": TEST_PHONE, "CallSid": "CA_TEST2"}, token)
    require(status == 200 and "Waking your workspace" in body and "/voice?wake=1" in body, f"sandbox create: {status} {body}")

    for wake in (1, 2):
        time.sleep(3.1)
        status, body = post(base_url, f"/voice?wake={wake}", {"From": TEST_PHONE, "CallSid": "CA_TEST2"}, token)
        if "Workspace awake" in body:
            break
    require(status == 200 and "Workspace awake" in body and "/voice/menu?attempts=1" in body, f"sandbox ready: {status} {body}")

    status, body = post(
        base_url,
        "/voice/menu?attempts=1",
        {"From": TEST_PHONE, "CallSid": "CA_TEST2", "SpeechResult": "status", "Confidence": "0.97"},
        token,
    )
    require(status == 200 and "Memory is 25 percent used" in body and "Disk is 12 percent used" in body, f"warm menu status: {status} {body}")

    status, _ = get(base_url, "/admin")
    require(status == 401, f"admin did not require authentication: {status}")

    status, event, safe_state = first_sse_event(base_url, "adam", LOCAL_ADMIN_PASSWORD)
    require(status == 200 and event == "state", f"SSE did not emit state: {status} {event}")
    require(safe_state.get("verbose") is False, f"safe SSE marked verbose: {safe_state}")
    require(all("*" in row["phone_number"] for row in safe_state["registrations"]), "safe SSE exposed phone number")
    require(all("identity_hash" not in row for row in safe_state["registrations"]), "safe SSE exposed identity hash")
    require(all("detail" not in item for call in safe_state["live_calls"] for item in call["activity"]), "safe SSE exposed activity details")

    status, event, state = first_sse_event(base_url, "adam", LOCAL_ADMIN_PASSWORD, verbose=True)
    require(state.get("verbose") is True, f"verbose SSE not enabled: {state}")
    require(len(state.get("registrations", [])) == 1, f"SSE state missing registration: {state}")
    live_calls = state.get("live_calls", [])
    require(len(live_calls) == 2, f"SSE state missing live calls: {live_calls}")
    test2 = next(call for call in live_calls if call["call_sid"] == "CA_TEST2")
    sources = {activity["source"] for activity in test2["activity"]}
    require({"voice", "hosting", "menu"}.issubset(sources), f"call activity sources incomplete: {sources}")

    for call_sid in ("CA_TEST1", "CA_TEST2"):
        status, body = post(
            base_url,
            "/voice/status",
            {"From": TEST_PHONE, "CallSid": call_sid, "CallStatus": "completed", "CallDuration": "42"},
            token,
        )
        require(status == 204 and body == "", f"call status callback failed: {status} {body}")

    _, _, closed_state = first_sse_event(base_url, "adam", LOCAL_ADMIN_PASSWORD, verbose=True)
    require(closed_state.get("live_calls") == [], f"completed calls remain live: {closed_state.get('live_calls')}")

    print("17 checks passed")
    return 0


def project_wrangler() -> str | None:
    local = WORKER_DIR / "node_modules" / ".bin" / "wrangler"
    return str(local) if local.exists() else shutil.which("wrangler")


def prepare_local_fixture(wrangler: str) -> None:
    sql = f"""
DELETE FROM call_activity WHERE call_sid IN (SELECT call_sid FROM voice_calls WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}'));
DELETE FROM voice_calls WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}');
DELETE FROM sandbox_assignments WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}');
DELETE FROM registrations WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}');
DELETE FROM claim_attempt WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}');
INSERT INTO invite_codes (code_words, expires_at, max_uses)
VALUES ('{LOCAL_INVITE}', unixepoch('now', '+1 hour'), NULL)
ON CONFLICT(code_words) DO UPDATE SET expires_at = excluded.expires_at, max_uses = NULL;
"""
    subprocess.run(
        [wrangler, "d1", "execute", "dogwalk", "--local", "--file", "schema.sql"],
        cwd=WORKER_DIR,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [wrangler, "d1", "execute", "dogwalk", "--local", "--command", sql],
        cwd=WORKER_DIR,
        check=True,
        stdout=subprocess.DEVNULL,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote", action="store_true", help="test the deployed worker")
    parser.add_argument("--url", help="custom deployed base URL")
    args = parser.parse_args()

    if args.remote or args.url:
        token = os.environ.get("TWILIO_AUTH_TOKEN")
        invite = os.environ.get("DOGWALK_TEST_INVITE_CODE")
        if not token or not invite:
            print("remote tests require TWILIO_AUTH_TOKEN and DOGWALK_TEST_INVITE_CODE", file=sys.stderr)
            return 2
        return run_tests(args.url or DEFAULT_REMOTE, "deployed Worker", token, invite)

    wrangler = project_wrangler()
    if not wrangler:
        print("wrangler is not installed", file=sys.stderr)
        return 2
    prepare_local_fixture(wrangler)
    DaytonaMock.exists = False
    DaytonaMock.detail_reads = 0
    daytona = ThreadingHTTPServer(("127.0.0.1", DAYTONA_MOCK_PORT), DaytonaMock)
    thread = threading.Thread(target=daytona.serve_forever, daemon=True)
    thread.start()

    print("starting wrangler dev")
    process = subprocess.Popen(
        [
            wrangler, "dev", "--port", "8787", "--ip", "127.0.0.1",
            "--var", f"TWILIO_AUTH_TOKEN:{LOCAL_TOKEN}",
            "--var", "DAYTONA_API_KEY:local-daytona-token",
            "--var", "DOGWALK_IDENTITY_SECRET:local-identity-secret",
            "--var", f"DAYTONA_API_BASE:http://127.0.0.1:{DAYTONA_MOCK_PORT}/api",
            "--var", f"ADMIN_PASSWORD:{LOCAL_ADMIN_PASSWORD}",
        ],
        cwd=WORKER_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_port("127.0.0.1", 8787, timeout=20):
            print("wrangler dev did not start", file=sys.stderr)
            return 1
        return run_tests(DEFAULT_LOCAL, "local Worker", LOCAL_TOKEN, LOCAL_INVITE)
    finally:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=10)
        daytona.shutdown()
        daytona.server_close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
