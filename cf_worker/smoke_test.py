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
DEFAULT_REMOTE = "https://dogwalk.tools"
LOCAL_INVITE = "ahead almighty apple"
TEST_PHONE = "+15555550100"
OTHER_PHONE = "+15555550101"
WORKER_DIR = Path(__file__).resolve().parent
DAYTONA_MOCK_PORT = 8790


class DaytonaMock(BaseHTTPRequestHandler):
    exists = False
    detail_reads = 0
    generation = 0
    create_count = 0
    last_create_payload: dict = {}
    capability_uploads = 0
    sms_messages: list[dict[str, str]] = []

    def sandbox(self, state: str = "started") -> dict:
        return {
            "id": f"mock-sandbox-{self.generation}",
            "name": f"dogwalk-mock-{self.generation}",
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
        if (
            self.path
            == f"/api/sandbox/mock-sandbox-{self.generation}/ports/8000/signed-preview-url?expiresInSeconds=3600"
        ):
            self.reply(
                {
                    "sandboxId": f"mock-sandbox-{self.generation}",
                    "port": 8000,
                    "token": "signed-service-token",
                    "url": "https://8000-signed-service-token.proxy.daytona.test",
                }
            )
            return
        if (
            self.path
            == f"/api/sandbox/mock-sandbox-{self.generation}/ports/8765/preview-url"
        ):
            self.reply(
                {
                    "url": "http://127.0.0.1:8791",
                    "token": "local-preview-token",
                }
            )
            return
        if self.path.startswith("/api/sandbox?"):
            self.reply(
                {"items": [self.sandbox()] if self.exists else [], "nextCursor": None}
            )
            return
        if self.path == f"/api/sandbox/mock-sandbox-{self.generation}":
            if not self.exists:
                self.send_error(404)
                return
            DaytonaMock.detail_reads += 1
            self.reply(
                self.sandbox("creating" if self.detail_reads == 1 else "started")
            )
            return
        self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length)
        payload = (
            json.loads(raw or b"{}")
            if "application/json" in self.headers.get("content-type", "")
            else {}
        )
        if self.path == "/api/sandbox":
            DaytonaMock.exists = True
            DaytonaMock.detail_reads = 0
            DaytonaMock.generation += 1
            DaytonaMock.create_count += 1
            DaytonaMock.last_create_payload = payload
            self.reply(self.sandbox("creating"))
            return
        if self.path == f"/api/sandbox/mock-sandbox-{self.generation}/start":
            self.reply(self.sandbox("starting"))
            return
        if self.path == f"/api/sandbox/mock-sandbox-{self.generation}/stop":
            self.reply(self.sandbox("stopping"))
            return
        if self.path.startswith(
            f"/toolbox/mock-sandbox-{self.generation}/files/folder?"
        ):
            self.reply({})
            return
        if self.path.startswith(
            f"/toolbox/mock-sandbox-{self.generation}/files/upload?"
        ):
            DaytonaMock.capability_uploads += 1
            self.reply({})
            return
        if self.path.startswith(
            f"/toolbox/mock-sandbox-{self.generation}/files/permissions?"
        ):
            self.reply({})
            return
        if self.path.startswith("/twilio/Accounts/") and self.path.endswith(
            "/Messages.json"
        ):
            message = dict(urllib.parse.parse_qsl(raw.decode()))
            DaytonaMock.sms_messages.append(message)
            self.send_response(201)
            body = json.dumps({"sid": "SM_TEST", "status": "queued"}).encode()
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == f"/toolbox/mock-sandbox-{self.generation}/process/execute":
            result = (
                "ready"
                if payload.get("command") == "printf ready"
                else ("uptime_seconds=9000\nmemory_percent=25\ndisk_percent=12\n")
            )
            self.reply({"result": result, "exitCode": 0})
            return
        self.send_error(404)

    def do_DELETE(self) -> None:
        if self.path == f"/api/sandbox/mock-sandbox-{self.generation}":
            DaytonaMock.exists = False
            self.reply(self.sandbox("destroying"))
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


def post(
    base_url: str, path: str, params: dict[str, str], token: str
) -> tuple[int, str]:
    url = base_url + path
    signed_url = ("http://dogwalk.tools" + path) if base_url == DEFAULT_LOCAL else url
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "x-twilio-signature": sign(signed_url, params, token),
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
        req = urllib.request.Request(
            base_url + path, headers={"user-agent": "twilio-smoke-test/1.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def post_json(
    base_url: str, path: str, value: dict, authorization: str
) -> tuple[int, str]:
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(value).encode(),
        headers={
            "authorization": authorization,
            "content-type": "application/json",
            "user-agent": "dogwalk-smoke-test/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def first_sse_event(
    base_url: str, username: str, password: str, verbose: bool = False
) -> tuple[int, str, dict]:
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


def run_tests(
    base_url: str, label: str, token: str, invite: str, mock_daytona: bool = False
) -> int:
    print(f"testing {label}: {base_url}")

    status, body = get(base_url, "/healthz")
    require((status, body) == (200, "ok\n"), f"healthz: {status} {body!r}")

    status, body = get(base_url, "/")
    require(
        status == 200
        and "Take your coding agents" in body
        and "github.com/rndmcnlly/dogwalk" in body,
        f"landing page: {status} {body[:100]!r}",
    )

    status, _ = post(
        base_url, "/voice", {"From": TEST_PHONE, "CallSid": "CA_BOGUS"}, "bogus"
    )
    require(status == 403, f"bogus signature returned {status}")

    status, body = post(
        base_url, "/voice", {"From": "anonymous", "CallSid": "CA_INVALID"}, token
    )
    require(
        status == 200 and "could not verify" in body,
        f"invalid phone accepted: {status} {body}",
    )

    status, body = post(
        base_url, "/voice", {"From": TEST_PHONE, "CallSid": "CA_TEST1"}, token
    )
    require(
        status == 200 and body.startswith("<Response><Gather"),
        f"registration gather: {status} {body}",
    )
    require('actionOnEmptyResult="true"' in body, "gather does not callback on silence")

    status, body = post(
        base_url,
        "/voice/claim?attempts=3",
        {
            "From": TEST_PHONE,
            "CallSid": "CA_SILENCE",
            "SpeechResult": "",
            "Confidence": "0",
        },
        token,
    )
    require(
        status == 200 and "Too many attempts" in body and "<Hangup/>" in body,
        f"attempt cap: {status} {body}",
    )

    status, body = post(
        base_url,
        "/voice/claim?attempts=1",
        {
            "From": TEST_PHONE,
            "CallSid": "CA_TEST1",
            "SpeechResult": invite,
            "Confidence": "0.92",
        },
        token,
    )
    require(
        status == 200 and 'action="/voice/confirm?attempts=1"' in body,
        f"invite match: {status} {body}",
    )
    require('hints="yes,again"' in body, "confirmation gather has incorrect hints")

    status, body = post(
        base_url,
        "/voice/confirm?attempts=1",
        {
            "From": OTHER_PHONE,
            "CallSid": "CA_TEST1",
            "SpeechResult": "yes",
            "Confidence": "0.95",
        },
        token,
    )
    require(
        status == 200 and "Let's start over" in body,
        f"pending claim crossed phone identities: {status} {body}",
    )

    status, body = post(
        base_url,
        "/voice/confirm?attempts=1",
        {
            "From": TEST_PHONE,
            "CallSid": "CA_TEST1",
            "SpeechResult": "yes.",
            "Confidence": "0.95",
        },
        token,
    )
    require(status == 200 and "Registered" in body, f"confirmation: {status} {body}")

    status, body = post(
        base_url, "/voice", {"From": TEST_PHONE, "CallSid": "CA_TEST2"}, token
    )
    require(
        status == 200 and "Waking your workspace" in body and "/voice?wake=1" in body,
        f"sandbox create: {status} {body}",
    )
    if mock_daytona:
        require(
            DaytonaMock.create_count == 1,
            "initial sandbox was not created exactly once",
        )
        require(
            DaytonaMock.last_create_payload.get("snapshot") == "dogwalk-test-snapshot",
            "sandbox snapshot was not configured",
        )
        require(
            "secrets" not in DaytonaMock.last_create_payload,
            "credential mapping was added to the free-model sandbox",
        )

    for wake in (1, 2):
        time.sleep(3.1)
        status, body = post(
            base_url,
            f"/voice?wake={wake}",
            {"From": TEST_PHONE, "CallSid": "CA_TEST2"},
            token,
        )
        if "<Connect><Stream" in body:
            break
    require(
        status == 200
        and "<Connect><Stream" in body
        and "wss://dogwalk.tools/voice/stream/" in body
        and '<Parameter name="from"' in body
        and '<Redirect method="POST">/voice/after-stream</Redirect>' in body,
        f"sandbox ready: {status} {body}",
    )

    status, body = post(
        base_url,
        "/voice/after-stream",
        {"From": TEST_PHONE, "CallSid": "CA_TEST2"},
        token,
    )
    require(
        status == 200 and "Voice unavailable" in body and "/voice/recovery" in body,
        f"default stream handoff: {status} {body}",
    )
    status, body = post(
        base_url,
        "/voice/recovery",
        {"From": TEST_PHONE, "CallSid": "CA_TEST2", "Digits": "2"},
        token,
    )
    require(
        status == 200
        and "destroys all workspace files" in body
        and "/voice/recovery/confirm" in body,
        f"recovery replacement warning: {status} {body}",
    )

    if mock_daytona:
        require(
            DaytonaMock.capability_uploads == 1,
            "sandbox capability was not provisioned exactly once",
        )
        capability = hmac.new(
            b"local-identity-secret",
            f"sandbox-capability-v1:{TEST_PHONE}:mock-sandbox-1".encode(),
            hashlib.sha256,
        ).hexdigest()
        status, published_body = post_json(
            base_url,
            "/api/sandbox/review-bundles",
            {
                "version": 1,
                "title": "Smoke report",
                "context": {
                    "session_id": "session-smoke",
                    "message_id": "message-smoke",
                },
                "files": [
                    {
                        "path": "report.md",
                        "media_type": "text/markdown; charset=utf-8",
                        "content_base64": base64.b64encode(
                            b"# Verified\n\nBundle body.\n"
                        ).decode(),
                    }
                ],
            },
            f"Bearer {capability}",
        )
        require(
            status == 201,
            f"Review Bundle publication failed: {status} {published_body}",
        )
        bundle_id = json.loads(published_body)["bundle_id"]
        public_token = hmac.new(
            b"local-identity-secret",
            f"review-bundle-v1:{bundle_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        status, review_body = get(base_url, f"/b/{public_token}/report.md")
        require(
            status == 200 and "Bundle body" in review_body and "<pre>" in review_body,
            f"Review Bundle read failed: {status} {review_body}",
        )
        status, service_body = post_json(
            base_url,
            "/api/sandbox/ephemeral-services",
            {
                "version": 1,
                "name": "VS Code",
                "port": 8000,
                "context": {
                    "session_id": "session-smoke",
                    "message_id": "message-smoke",
                },
            },
            f"Bearer {capability}",
        )
        require(
            status == 201 and json.loads(service_body)["name"] == "VS Code",
            f"Ephemeral Service registration failed: {status} {service_body}",
        )

    status, body = post(
        base_url,
        "/voice/menu?attempts=1",
        {
            "From": TEST_PHONE,
            "CallSid": "CA_TEST2",
            "SpeechResult": "status",
            "Confidence": "0.97",
        },
        token,
    )
    require(
        status == 200
        and "Memory is 25 percent used" in body
        and "Disk is 12 percent used" in body,
        f"warm menu status: {status} {body}",
    )

    replacement_call = None
    if mock_daytona:
        DaytonaMock.exists = False
        replacement_call = "CA_REPLACED"
        status, body = post(
            base_url, "/voice", {"From": TEST_PHONE, "CallSid": replacement_call}, token
        )
        require(
            status == 200 and "Waking your workspace" in body,
            f"deleted sandbox recovery: {status} {body}",
        )
        require(
            DaytonaMock.create_count == 2 and DaytonaMock.generation == 2,
            "deleted sandbox was not replaced exactly once",
        )
        for wake in (1, 2):
            time.sleep(3.1)
            status, body = post(
                base_url,
                f"/voice?wake={wake}",
                {"From": TEST_PHONE, "CallSid": replacement_call},
                token,
            )
            if "<Connect><Stream" in body:
                break
        require(
            status == 200 and "<Connect><Stream" in body,
            f"replacement sandbox not ready: {status} {body}",
        )

    status, _ = get(base_url, "/admin")
    require(status == 401, f"admin did not require authentication: {status}")

    # Scoped Diagnostic View: an unminted per-call token must not resolve. The
    # capability is unenumerable, so a random 64-hex token is a 404.
    status, _ = get(base_url, "/v/" + ("0" * 64))
    require(status == 404, f"unknown scoped view token resolved: {status}")

    status, event, safe_state = first_sse_event(base_url, "adam", LOCAL_ADMIN_PASSWORD)
    require(
        status == 200 and event == "state", f"SSE did not emit state: {status} {event}"
    )
    require(
        safe_state.get("verbose") is False, f"safe SSE marked verbose: {safe_state}"
    )
    require(
        all("*" in row["phone_number"] for row in safe_state["registrations"]),
        "safe SSE exposed phone number",
    )
    require(
        all("identity_hash" not in row for row in safe_state["registrations"]),
        "safe SSE exposed identity hash",
    )
    require(
        all(
            "detail" not in item
            for call in safe_state["live_calls"]
            for item in call["activity"]
        ),
        "safe SSE exposed activity details",
    )

    status, event, state = first_sse_event(
        base_url, "adam", LOCAL_ADMIN_PASSWORD, verbose=True
    )
    require(state.get("verbose") is True, f"verbose SSE not enabled: {state}")
    require(
        len(state.get("registrations", [])) == 1,
        f"SSE state missing registration: {state}",
    )
    live_calls = state.get("live_calls", [])
    expected_live_calls = 3 if replacement_call else 2
    require(
        len(live_calls) == expected_live_calls,
        f"SSE state missing live calls: {live_calls}",
    )
    test2 = next(call for call in live_calls if call["call_sid"] == "CA_TEST2")
    sources = {activity["source"] for activity in test2["activity"]}
    require(
        {"voice", "hosting", "menu"}.issubset(sources),
        f"call activity sources incomplete: {sources}",
    )

    call_sids = ["CA_TEST1", "CA_TEST2"]
    if replacement_call:
        call_sids.append(replacement_call)
    for call_sid in call_sids:
        status, body = post(
            base_url,
            "/voice/status",
            {
                "From": TEST_PHONE,
                "CallSid": call_sid,
                "CallStatus": "completed",
                "CallDuration": "42",
            },
            token,
        )
        require(
            status == 204 and body == "",
            f"call status callback failed: {status} {body}",
        )

    _, _, closed_state = first_sse_event(
        base_url, "adam", LOCAL_ADMIN_PASSWORD, verbose=True
    )
    require(
        closed_state.get("live_calls") == [],
        f"completed calls remain live: {closed_state.get('live_calls')}",
    )

    print("29 checks passed" if mock_daytona else "21 checks passed")
    return 0


def project_wrangler() -> str | None:
    local = WORKER_DIR / "node_modules" / ".bin" / "wrangler"
    return str(local) if local.exists() else shutil.which("wrangler")


def prepare_local_fixture(wrangler: str) -> None:
    sql = f"""
DELETE FROM call_activity WHERE call_sid IN (SELECT call_sid FROM voice_calls WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}'));
DELETE FROM call_handoffs WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}');
DELETE FROM sms_log WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}');
DELETE FROM voice_calls WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}');
DELETE FROM review_bundle_files WHERE bundle_id IN (SELECT id FROM review_bundles WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}'));
DELETE FROM review_bundles WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}');
DELETE FROM ephemeral_services WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}');
DELETE FROM sandbox_capabilities WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}');
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
    parser.add_argument(
        "--remote", action="store_true", help="test the deployed worker"
    )
    parser.add_argument("--url", help="custom deployed base URL")
    args = parser.parse_args()

    if args.remote or args.url:
        token = os.environ.get("TWILIO_AUTH_TOKEN")
        invite = os.environ.get("DOGWALK_TEST_INVITE_CODE")
        if not token or not invite:
            print(
                "remote tests require TWILIO_AUTH_TOKEN and DOGWALK_TEST_INVITE_CODE",
                file=sys.stderr,
            )
            return 2
        return run_tests(args.url or DEFAULT_REMOTE, "deployed Worker", token, invite)

    wrangler = project_wrangler()
    if not wrangler:
        print("wrangler is not installed", file=sys.stderr)
        return 2
    prepare_local_fixture(wrangler)
    DaytonaMock.exists = False
    DaytonaMock.detail_reads = 0
    DaytonaMock.generation = 0
    DaytonaMock.create_count = 0
    DaytonaMock.last_create_payload = {}
    DaytonaMock.capability_uploads = 0
    DaytonaMock.sms_messages = []
    daytona = ThreadingHTTPServer(("127.0.0.1", DAYTONA_MOCK_PORT), DaytonaMock)
    thread = threading.Thread(target=daytona.serve_forever, daemon=True)
    thread.start()

    print("starting wrangler dev")
    process = subprocess.Popen(
        [
            wrangler,
            "dev",
            "--port",
            "8787",
            "--ip",
            "127.0.0.1",
            "--var",
            f"TWILIO_AUTH_TOKEN:{LOCAL_TOKEN}",
            "--var",
            "DAYTONA_API_KEY:local-daytona-token",
            "--var",
            "DOGWALK_IDENTITY_SECRET:local-identity-secret",
            "--var",
            f"DAYTONA_API_BASE:http://127.0.0.1:{DAYTONA_MOCK_PORT}/api",
            "--var",
            "DAYTONA_SNAPSHOT:dogwalk-test-snapshot",
            "--var",
            f"ADMIN_PASSWORD:{LOCAL_ADMIN_PASSWORD}",
            "--var",
            "TWILIO_ACCOUNT_SID:AC_TEST",
            "--var",
            "TWILIO_FROM_NUMBER:+18317775707",
            "--var",
            f"TWILIO_API_BASE:http://127.0.0.1:{DAYTONA_MOCK_PORT}/twilio",
        ],
        cwd=WORKER_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_port("127.0.0.1", 8787, timeout=20):
            print("wrangler dev did not start", file=sys.stderr)
            return 1
        return run_tests(
            DEFAULT_LOCAL, "local Worker", LOCAL_TOKEN, LOCAL_INVITE, mock_daytona=True
        )
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
