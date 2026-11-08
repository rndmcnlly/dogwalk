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
    openai_requests: list[tuple[str, dict]] = []
    # A provider state to report instead of the normal creating-then-started sequence.
    force_state: str | None = None
    start_count = 0
    preview_reads = 0
    # Toolbox commands seen, and optional handlers that answer some of them.
    executed: list[str] = []
    execute_hooks: list = []

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
            == f"/api/sandbox/mock-sandbox-{self.generation}/ports/8765/preview-url"
        ):
            DaytonaMock.preview_reads += 1
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
            if self.force_state:
                self.reply(self.sandbox(self.force_state))
                return
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
        if self.path.startswith("/live/sessions/"):
            DaytonaMock.openai_requests.append((self.path, payload))
            self.reply({})
            return
        if self.path == "/api/sandbox":
            DaytonaMock.exists = True
            DaytonaMock.detail_reads = 0
            DaytonaMock.generation += 1
            DaytonaMock.create_count += 1
            DaytonaMock.last_create_payload = payload
            self.reply(self.sandbox("creating"))
            return
        if self.path == f"/api/sandbox/mock-sandbox-{self.generation}/start":
            DaytonaMock.start_count += 1
            self.reply(self.sandbox("starting"))
            return
        if self.path == f"/api/sandbox/mock-sandbox-{self.generation}/stop":
            self.reply(self.sandbox("stopping"))
            return
        if self.path == f"/toolbox/mock-sandbox-{self.generation}/process/execute":
            command = str(payload.get("command", ""))
            DaytonaMock.executed.append(command)
            for hook in DaytonaMock.execute_hooks:
                answer = hook(command)
                if answer is not None:
                    self.reply(answer)
                    return
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
    require(
        "a voice line for supervising coding agents" in body and "three invite words" in body,
        f"unregistered greeting: {body}",
    )

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

    # Talk first: a registered caller is dialed straight to the Voice Agent while the new
    # Sandbox is still being created; there is no wake-up loop.
    status, body = post(
        base_url, "/voice", {"From": TEST_PHONE, "CallSid": "CA_TEST2"}, token
    )
    require(
        status == 200
        and '<Dial action="/voice/after-live"' in body
        and "sip:proj_test@sip.api.openai.com;transport=tls;secure=true" in body
        and "x-dogwalk-context=" in body
        and "wake" not in body
        and "<Say" not in body,
        f"immediate dial while provisioning: {status} {body}",
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
            DaytonaMock.last_create_payload.get("secrets") == [{"OPENAI_API_KEY": "dogwalk-test-agent-secret"}],
            "Agent inference secret was not mapped by name",
        )
        # The prewarm reaches the VoiceSession, which asks Daytona for the ACP preview URL.
        deadline = time.time() + 10
        while DaytonaMock.preview_reads == 0 and time.time() < deadline:
            time.sleep(0.2)
        require(DaytonaMock.preview_reads > 0, "prewarm did not reach the VoiceSession")

        # Re-entering /voice in the same call (as the in-call menu does) keeps the first
        # caller-ID attestation rather than recording one without StirVerstat.
        status, body = post(
            base_url, "/voice", {"From": TEST_PHONE, "CallSid": "CA_TEST2"}, token
        )
        require(status == 200 and "<Dial" in body, f"voice re-entry: {status} {body}")

        # A stopped Sandbox is started without waiting, and the call is dialed at once.
        time.sleep(3.1)
        DaytonaMock.force_state = "stopped"
        status, body = post(
            base_url, "/voice", {"From": TEST_PHONE, "CallSid": "CA_STOPPED"}, token
        )
        DaytonaMock.force_state = None
        require(
            status == 200 and "<Dial" in body and DaytonaMock.start_count == 1,
            f"stopped sandbox not started and dialed: {status} {body}",
        )

        # A broken Sandbox still goes to deterministic recovery.
        time.sleep(3.1)
        DaytonaMock.force_state = "error"
        status, body = post(
            base_url, "/voice", {"From": TEST_PHONE, "CallSid": "CA_BROKEN"}, token
        )
        DaytonaMock.force_state = None
        require(
            status == 200 and "Workspace unavailable" in body and "<Dial" not in body,
            f"broken sandbox did not offer recovery: {status} {body}",
        )

    status, body = post(
        base_url,
        "/voice/after-live",
        {"From": TEST_PHONE, "CallSid": "CA_TEST2"},
        token,
    )
    require(
        status == 200 and "Voice dropped" in body and "Press 1 to reconnect" in body and "/voice/recovery" in body,
        f"default stream handoff: {status} {body}",
    )
    status, body = post(
        base_url,
        "/voice/recovery",
        {"From": TEST_PHONE, "CallSid": "CA_TEST2", "Digits": "3"},
        token,
    )
    require(
        status == 200
        and "destroys all workspace files" in body
        and "/voice/recovery/confirm" in body,
        f"recovery replacement warning: {status} {body}",
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
            status == 200 and "<Dial" in body and "sip:proj_test@sip.api.openai.com" in body,
            f"deleted sandbox recovery: {status} {body}",
        )
        require(
            DaytonaMock.create_count == 2 and DaytonaMock.generation == 2,
            "deleted sandbox was not replaced exactly once",
        )

    status, _ = get(base_url, "/admin")
    require(status == 401, f"admin did not require authentication: {status}")

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
    expected_live_calls = 5 if replacement_call else 2
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
    attestations = [a for a in test2["activity"] if a["event"] == "call.attestation"]
    require(
        len(attestations) == 1,
        f"caller-ID attestation not recorded exactly once per call: {len(attestations)}",
    )

    call_sids = ["CA_TEST1", "CA_TEST2"]
    if replacement_call:
        call_sids += ["CA_STOPPED", "CA_BROKEN", replacement_call]
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

    print("31 checks passed" if mock_daytona else "21 checks passed")
    return 0


def project_wrangler() -> str | None:
    local = WORKER_DIR / "node_modules" / ".bin" / "wrangler"
    return str(local) if local.exists() else shutil.which("wrangler")


def prepare_local_fixture(wrangler: str) -> None:
    sql = f"""
DELETE FROM call_activity WHERE call_sid IN (SELECT call_sid FROM voice_calls WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}'));
DELETE FROM call_handoffs WHERE phone_number IN ('{TEST_PHONE}', '{OTHER_PHONE}');
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
    DaytonaMock.force_state = None
    DaytonaMock.start_count = 0
    DaytonaMock.preview_reads = 0
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
            "OPENAI_PROJECT_ID:proj_test",
            "--var",
            f"DAYTONA_API_BASE:http://127.0.0.1:{DAYTONA_MOCK_PORT}/api",
            "--var",
            "DAYTONA_SNAPSHOT:dogwalk-test-snapshot",
            "--var",
            "DAYTONA_AGENT_SECRET:dogwalk-test-agent-secret",
            "--var",
            f"ADMIN_PASSWORD:{LOCAL_ADMIN_PASSWORD}",
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
