#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets==15.0.1"]
# ///
"""Assertion-based smoke test for the Dogwalk registration Worker."""

import argparse
import base64
import hashlib
import hmac
import json
import re
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect as ws_connect

LOCAL_TOKEN = "local-smoke-token"
LOCAL_ADMIN_PASSWORD = "local-admin-password"
# Ports derive from one base so worktrees can run the local suites at the same time:
# DOGWALK_TEST_PORT_BASE=8800 uses 8800 (Worker), 8803-8805 (mocks), and 10242 (inspector).
PORT_BASE = int(os.environ.get("DOGWALK_TEST_PORT_BASE", "8787"))
WORKER_PORT = PORT_BASE
INSPECTOR_PORT = PORT_BASE + 1442
DEFAULT_LOCAL = f"http://127.0.0.1:{WORKER_PORT}"
DEFAULT_REMOTE = "https://dogwalk.tools"
LOCAL_INVITE = "ahead almighty apple"
TEST_PHONE = "+15555550100"
OTHER_PHONE = "+15555550101"
# Every phone number the local tests use (fictional 555-01xx numbers), as a LIKE pattern.
TEST_NUMBERS = "+1555555%"
DONOR_PHONE = "+15555550109"
LOCAL_IDENTITY_SECRET = "local-identity-secret"
TEST_ACCOUNT = "AC" + "0" * 32
WORKER_DIR = Path(__file__).resolve().parent
DAYTONA_MOCK_PORT = PORT_BASE + 3


class DaytonaMock(BaseHTTPRequestHandler):
    exists = False
    detail_reads = 0
    generation = 0
    create_count = 0
    last_create_payload: dict = {}
    openai_requests: list[tuple[str, dict]] = []
    # Seconds GPT-Live takes to accept a call, so a test can finish work before the attach.
    live_accept_delay = 0.0
    # A provider state to report instead of the normal creating-then-started sequence.
    force_state: str | None = None
    start_count = 0
    preview_reads = 0
    # Toolbox commands seen, and optional handlers that answer some of them.
    executed: list[str] = []
    execute_hooks: list = []
    # Optional handlers for other GETs (toolbox file reads): path -> (status, body, content type).
    get_hooks: list = []
    # Paths of every DELETE, answered or not, and optional handlers: path -> status or None.
    deleted: list[str] = []
    delete_hooks: list = []
    # Optional handlers for other POSTs: (path, payload) -> a JSON reply, or None to fall through.
    post_hooks: list = []

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
                    "url": f"http://127.0.0.1:{PORT_BASE + 4}",
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
        for hook in DaytonaMock.get_hooks:
            answer = hook(self.path)
            if answer is not None:
                status, body, kind = answer
                self.send_response(status)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
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
            if self.path.endswith("/accept"):
                time.sleep(DaytonaMock.live_accept_delay)
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
        for hook in DaytonaMock.post_hooks:
            answer = hook(self.path, payload)
            if answer is not None:
                self.reply(answer)
                return
        self.send_error(404)

    def do_DELETE(self) -> None:
        DaytonaMock.deleted.append(self.path)
        for hook in DaytonaMock.delete_hooks:
            status = hook(self.path)
            if status == 200:
                self.reply({"id": self.path.rsplit("/", 1)[-1], "state": "destroying"})
                return
            if status is not None:
                self.send_error(status)
                return
        if self.exists and self.path == f"/api/sandbox/mock-sandbox-{self.generation}":
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


def get(base_url: str, path: str, headers: dict | None = None) -> tuple[int, str]:
    try:
        req = urllib.request.Request(
            base_url + path, headers={"user-agent": "twilio-smoke-test/1.0", **(headers or {})}
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


def spoken_handle(body: str) -> str:
    """The Handle a Registration's TwiML speaks: two words, said twice."""
    match = re.search(r"Your name on Dogwalk is ([a-z]+ [a-z]+)\. Again: ([a-z]+ [a-z]+)\.", body)
    require(match is not None and match[1] == match[2], f"registration did not speak a Handle twice: {body}")
    require("This phone is now your home" in body, f"registration did not name the Home: {body}")
    return match[1]


def d1_local(sql: str) -> list[dict]:
    result = subprocess.run(
        [project_wrangler(), "d1", "execute", "dogwalk", "--local", "--json", "--command", sql],
        cwd=WORKER_DIR, check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)[0]["results"]


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
    require("say donate" in body and ",donate" in body, f"greeting does not offer Voice Donation: {body}")

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
            # Speech recognition splits compounds ("bread line" for breadline) and callers add
            # words around the code: a first word split in two, inside filler, still matches.
            "SpeechResult": f"Um, my words are {invite[:2]} {invite[2:]}, thanks.",
            "Confidence": "0.92",
        },
        token,
    )
    require(
        status == 200 and 'action="/voice/confirm?attempts=1"' in body
        and f"I heard: {invite.replace(' ', ', ')}." in body,
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
    # Registration assigns a two-word Handle and speaks it twice; the number becomes its Home.
    handle = spoken_handle(body)
    if mock_daytona:
        users = d1_local(f"SELECT handle, home_phone FROM users WHERE invite_code = '{invite}'")
        require(users == [{"handle": handle, "home_phone": TEST_PHONE}], f"registered User: {users}")

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
        # The prewarm reaches the VoiceSession, which asks Daytona for the Agent Gateway preview URL.
        deadline = time.time() + 10
        while DaytonaMock.preview_reads == 0 and time.time() < deadline:
            time.sleep(0.2)
        require(DaytonaMock.preview_reads > 0, "prewarm did not reach the VoiceSession")

        # Re-entering /voice in the same call (as the in-call menu does) keeps the first
        # caller-ID attestation rather than recording one without StirVerstat. The User now
        # has a Voice Reference, so the call also warms the speaker verifier in the
        # background: speaker_mock.js takes three seconds over its first warm, and the call
        # must dial without waiting for it (health below shows the warm happened).
        d1_local(
            "INSERT INTO voice_references (user_id, embedding_space, embedding, enrolled_at) "
            f"SELECT id, 'local-test', '[]', unixepoch() FROM users WHERE home_phone = '{TEST_PHONE}'"
        )
        started = time.monotonic()
        status, body = post(
            base_url, "/voice", {"From": TEST_PHONE, "CallSid": "CA_TEST2"}, token
        )
        elapsed = time.monotonic() - started
        require(status == 200 and "<Dial" in body, f"voice re-entry: {status} {body}")
        require(elapsed < 2.5, f"the verifier warm delayed TwiML: {elapsed:.2f} s")
        d1_local(f"DELETE FROM voice_references WHERE user_id IN (SELECT id FROM users WHERE home_phone = '{TEST_PHONE}')")

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

    # Operator diagnostics are independent of pairing and require operator auth
    # for the page, call discovery, and every incremental read.
    operator_headers = {"authorization": "Basic " + base64.b64encode(f"adam:{LOCAL_ADMIN_PASSWORD}".encode()).decode()}
    for path in ("/admin/api/state", "/admin/api/state?verbose=1", "/admin/api/events?verbose=1", "/admin/timing", "/admin/api/timing/calls", "/admin/api/timing?call_sid=CA_TEST2", "/admin/api/timing/content?call_sid=CA_TEST2"):
        require(get(base_url, path)[0] == 401, f"operator route is public: {path}")
        require(get(base_url, path, {"authorization": "Bearer paired-browser-credential"})[0] == 401,
                f"operator route accepts pairing bearer: {path}")
    status, body = get(base_url, "/admin/timing", operator_headers)
    require(status == 200 and "sequence" in body.lower(), "operator timing page unavailable")

    # Voice verification calibration is operator-only. Local dev runs speaker_mock.js as the
    # speaker verification Worker; deployed, health runs a real inference.
    def voice_verify(body: bytes, headers: dict, user: str = "1") -> tuple[int, str]:
        req = urllib.request.Request(base_url + "/admin/api/voice-verify?user=" + user, data=body,
                                     headers={"content-type": "audio/wav", **headers}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()
    require(get(base_url, "/admin/api/voice-verify/health")[0] == 401, "voice verify health is public")
    require(voice_verify(b"RIFF", {})[0] == 401, "voice verify is public")
    status, body = get(base_url, "/admin/api/voice-verify/health", operator_headers)
    require(status == 200 and json.loads(body)["ok"], f"voice verify health: {status} {body}")
    if mock_daytona:
        # The enrolled Home call above already warmed the verifier in the background.
        require(json.loads(body)["cold"] is False, f"the Home call did not warm the verifier: {body}")
    require(get(base_url, "/admin/api/voice-verify?user=1", operator_headers)[0] == 405, "voice verify accepted GET")
    status, _ = voice_verify(b"not a wav file", operator_headers)
    require(status in (400, 503), f"voice verify accepted a non-WAV body: {status}")
    require(voice_verify(b"x" * ((1 << 20) + 1), operator_headers)[0] in (413, 503), "voice verify accepted an oversized body")
    status, body = get(base_url, "/admin/api/timing/calls", operator_headers)
    listed = json.loads(body)
    require(status == 200 and listed["calls"] and all("*" in call["caller"] for call in listed["calls"]),
            "operator call discovery missing or caller labels unmasked")
    require(all("phone_number" not in call for call in listed["calls"]), "operator discovery exposed unmasked phone field")
    status, body = get(base_url, "/admin/api/timing/content?call_sid=CA_TEST2", operator_headers)
    require(status == 200 and isinstance(json.loads(body).get("entries"), list), "operator content unavailable for known call")
    require(get(base_url, "/admin/api/timing/content?call_sid=CA_UNKNOWN", operator_headers)[0] == 404,
            "operator content accepted unknown call")
    status, body = get(base_url, "/admin/api/timing?call_sid=CA_TEST2&after=0", operator_headers)
    page = json.loads(body)
    require(status == 200 and page["events"] and all(isinstance(row["detail"], dict) for row in page["events"]),
            "incremental timing API did not return parsed event details")
    require(page["cursor"] == page["events"][-1]["id"] and not page["has_more"], "bad timing cursor")
    status, body = get(base_url, f"/admin/api/timing?call_sid=CA_TEST2&after={page['cursor']}", operator_headers)
    require(status == 200 and json.loads(body)["events"] == [], "timing cursor repeated already-seen rows")
    for suffix, expected in (("call_sid=CA_TEST2&after=-1", 400), ("call_sid=CA_TEST2&after=1.5", 400),
                             ("call_sid=CA_TEST2&after=9007199254740992", 400),
                             ("call_sid=CA_UNKNOWN", 404), ("after=0", 400)):
        require(get(base_url, "/admin/api/timing?" + suffix, operator_headers)[0] == expected,
                f"invalid operator query accepted: {suffix}")

    # OTLP export: same operator gate, pure OTLP bodies, row-local log pages.
    for path in ("/admin/api/otlp/v1/logs?call_sid=CA_TEST2", "/admin/api/otlp/v1/traces?call_sid=CA_TEST2"):
        require(get(base_url, path)[0] == 401, f"OTLP export is public: {path}")
        require(get(base_url, path, {"authorization": "Bearer paired-browser-credential"})[0] == 401,
                f"OTLP export accepts pairing bearer: {path}")
    status, body = get(base_url, "/admin/api/otlp/v1/logs?call_sid=CA_TEST2&after=0", operator_headers)
    records = [r for rl in json.loads(body)["resourceLogs"] for s in rl["scopeLogs"] for r in s["logRecords"]]
    require(status == 200 and records and all(r["observedTimeUnixNano"].isdigit() and r["eventName"] for r in records),
            "OTLP logs export did not return LogRecords")
    require(all("rowId" not in r and "body" not in r for r in records), "OTLP logs carry non-OTLP fields or content")
    # Every writer (front door, Durable Object, timing) records its producer, exported as Resource.
    resources = [{a["key"]: a["value"]["stringValue"] for a in rl["resource"]["attributes"]} for rl in json.loads(body)["resourceLogs"]]
    require(resources and all(r.get("deployment.environment.name") == "local-test" and r.get("service.version") for r in resources),
            f"OTLP log rows without recorded producer: {resources}")
    last = max(int(a["value"]["intValue"]) for r in records for a in r["attributes"] if a["key"] == "dogwalk.journal.row_id")
    status, body = get(base_url, f"/admin/api/otlp/v1/logs?call_sid=CA_TEST2&after={last}", operator_headers)
    require(status == 200 and json.loads(body)["resourceLogs"] == [],
            "OTLP log cursor repeated already-seen rows")
    status, body = get(base_url, "/admin/api/otlp/v1/traces?call_sid=CA_TEST2", operator_headers)
    require(status == 200 and isinstance(json.loads(body)["resourceSpans"], list),
            "OTLP traces export unavailable")
    # The front door mints each Voice Call's W3C trace context once, when the call begins.
    traced = json.loads(subprocess.run(
        [project_wrangler(), "d1", "execute", "dogwalk", "--local", "--json", "--command",
         "SELECT trace_id, span_id FROM voice_calls WHERE call_sid = 'CA_TEST2'"],
        cwd=WORKER_DIR, check=True, capture_output=True, text=True).stdout)[0]["results"]
    require(traced and re.fullmatch(r"(?!0{32})[0-9a-f]{32}", traced[0]["trace_id"] or "")
            and re.fullmatch(r"(?!0{16})[0-9a-f]{16}", traced[0]["span_id"] or ""), f"call trace context not minted: {traced}")
    # A Prompt Turn started in one call and ended in another: the start's export completes the
    # span by looking the end up across calls (indexed by span_id in real D1).
    # 96 turns, so the lookup needs more than one 90-ID batch.
    turn_trace, turn_span = "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7"
    turn_spans = [turn_span] + [f"{n + 1:016x}" for n in range(95)]
    def turn_row(call_sid: str, event: str, span: str, extra: str) -> str:
        return (f"INSERT INTO call_activity (call_sid, ts, source, direction, event, detail) VALUES ('{call_sid}', 1, 'agent', "
                f"'internal', '{event}', json_object('agent_session_id', 'ses_{span}', 'started_ms', 1000, 'ts_ms', 1000, "
                f"'trace_id', '{turn_trace}', 'span_id', '{span}'{extra}));")
    subprocess.run([project_wrangler(), "d1", "execute", "dogwalk", "--local", "--command",
        f"INSERT OR REPLACE INTO voice_calls (call_sid, phone_number, status, started_at, last_activity_at, ended_at) VALUES "
        f"('CA_XCALL_A', '{TEST_PHONE}', 'completed', 1, 2, 2), ('CA_XCALL_B', '{TEST_PHONE}', 'completed', 3, 4, 4);"
        + "".join(turn_row("CA_XCALL_A", "agent.turn.started", span, "") for span in turn_spans)
        + "".join(turn_row("CA_XCALL_B", "agent.turn.stopped", span, ", 'ended_ms', 3500, 'outcome', 'completed'")
                  for span in turn_spans)],
        cwd=WORKER_DIR, check=True, stdout=subprocess.DEVNULL)
    status, body = get(base_url, "/admin/api/otlp/v1/traces?call_sid=CA_XCALL_A", operator_headers)
    spans = [s for rs in json.loads(body)["resourceSpans"] for ss in rs["scopeSpans"] for s in ss["spans"]]
    completed = {s["spanId"] for s in spans if s["name"] == "agent.prompt_turn" and s["traceId"] == turn_trace}
    require(status == 200 and completed == set(turn_spans), f"cross-call Prompt Turns not all completed: {len(completed)}/96")
    for path, expected in (("logs?call_sid=CA_UNKNOWN", 404), ("logs?call_sid=CA_TEST2&after=-1", 400), ("traces?after=0", 400)):
        require(get(base_url, "/admin/api/otlp/v1/" + path, operator_headers)[0] == expected,
                f"invalid OTLP query accepted: {path}")

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
        [row["handle"] for row in safe_state["registrations"]] == [handle]
        and all(row["diagnostic_id"].startswith("user-") and row["voice_enrolled"] == 0
                for row in safe_state["registrations"]),
        f"safe SSE Users lack Handle or diagnostic id: {safe_state['registrations']}",
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

    if mock_daytona:
        rehome_checks(base_url, token, invite, handle)
        provider_route_checks(base_url, token, operator_headers)
        # Donation writes to the research bucket, so it runs only against local R2.
        donation_checks(base_url, token)
        # Practice scores through the mock speaker Worker that runs beside local dev.
        practice_checks(base_url, token)
        identity_checks(base_url, token)
        reenrollment_checks(base_url, token)
    print("33 core checks plus operator/provider timing, Voice Donation, Challenge Practice, Identity Check, and Re-enrollment checks passed" if mock_daytona else "21 core checks plus operator timing checks passed")
    return 0


def rehome_checks(base_url: str, token: str, invite: str, first_handle: str) -> None:
    """Registering from a number that is already a Home displaces that Home: the old User
    keeps its row (and Sandbox Assignment) without a Home, and the new User takes the number."""
    status, body = post(base_url, "/voice/claim?attempts=1",
                        {"From": TEST_PHONE, "CallSid": "CA_REHOME", "SpeechResult": invite, "Confidence": "0.9"}, token)
    require(status == 200 and "I heard" in body, f"Home number could not claim again: {status} {body}")
    status, body = post(base_url, "/voice/confirm?attempts=1",
                        {"From": TEST_PHONE, "CallSid": "CA_REHOME", "SpeechResult": "yes", "Confidence": "0.9"}, token)
    require(status == 200 and "Registered" in body, f"re-registration: {status} {body}")
    handle = spoken_handle(body)
    require(handle != first_handle, "re-registration reused the displaced User's Handle")
    users = d1_local(f"""SELECT u.handle, u.home_phone, u.home_used_at, s.provider_id FROM users u
        LEFT JOIN sandbox_assignments s ON s.user_id = u.id WHERE u.invite_code = '{invite}' ORDER BY u.id""")
    require([(u["handle"], u["home_phone"]) for u in users] == [(first_handle, None), (handle, TEST_PHONE)],
            f"Home not displaced: {users}")
    require(users[0]["home_used_at"] is None and users[0]["provider_id"], f"displaced User lost its row: {users}")
    _, _, state = first_sse_event(base_url, "adam", LOCAL_ADMIN_PASSWORD, verbose=True)
    homes = {row["handle"]: row["phone_number"] for row in state["registrations"]}
    require(homes == {first_handle: None, handle: TEST_PHONE}, f"snapshot Homes: {homes}")


def provider_route_checks(base_url: str, token: str, headers: dict) -> None:
    """Signed child callbacks enrich an ended root without touching call control."""
    root, child, account = "CA" + "a" * 32, "CA" + "b" * 32, "AC" + "c" * 32
    wrangler = project_wrangler()
    subprocess.run([wrangler, "d1", "execute", "dogwalk", "--local", "--command",
        f"INSERT OR REPLACE INTO voice_calls (call_sid,phone_number,status,started_at,last_activity_at,ended_at,duration_seconds) "
        f"VALUES ('{root}','{TEST_PHONE}','completed',100,200,200,100)"],
        cwd=WORKER_DIR, check=True, stdout=subprocess.DEVNULL)
    date = "Tue, 10 Nov 2026 17:00:00 +0000"
    requested = []

    def resource(path):
        requested.append(path)
        if path == f"/twilio/Accounts/{account}/Calls/{child}.json":
            body = {"sid": child, "account_sid": account, "parent_call_sid": root, "status": "ringing", "date_created": date,
                    "from": "do-not-retain-phone", "to": "sip:private?cap=do-not-retain-capability"}
            return 200, json.dumps(body).encode(), "application/json"
        return None

    DaytonaMock.get_hooks.append(resource)
    form = {"AccountSid": account, "CallSid": child, "ParentCallSid": root, "CallStatus": "ringing",
            "Timestamp": date, "SequenceNumber": "1", "To": "do-not-retain-phone"}
    require(post(base_url, "/voice/provider-status", form, "bogus")[0] == 403, "provider callback accepted a bad signature")
    require(post(base_url, "/voice/provider-status", form, token)[0] == 204, "signed SIP callback rejected without From")
    deadline = time.time() + 10
    while True:
        _, body = get(base_url, f"/admin/api/timing?call_sid={root}&after=0", headers)
        page = json.loads(body)
        if any(row["event"] == "provider.twilio.call.created" for row in page["events"]):
            break
        require(time.time() < deadline, f"provider metadata did not arrive off-path; local requests: {requested}")
        time.sleep(0.05)
    require(page["call"]["status"] == "completed" and page["call"]["ended_at"] == 200, "child callback revived ended root")
    callback = next(row["detail"] for row in page["events"] if row["event"] == "provider.twilio.callback.ringing")
    require(callback["parent_call_sid"] == root and callback["leg"] == "sip" and callback["sequence_number"] == 1,
            "provider callback lost explicit relationship")
    require(callback["timestamp_precision_ms"] == 1000 and callback["occurred_at_ms"] < callback["received_at_ms"],
            "provider event and receipt clocks conflated")
    require("do-not-retain" not in json.dumps(page), "provider metadata persisted sensitive fields")
    require(post(base_url, "/voice/provider-status", form, token)[0] == 204, "provider retry failed")
    time.sleep(0.2)
    _, body = get(base_url, f"/admin/api/timing?call_sid={root}&after=0", headers)
    require(sum(row["event"] == "provider.twilio.callback.ringing" for row in json.loads(body)["events"]) == 1,
            "provider callback retry was not deduplicated")


REAP_CRON = "17 10 * * *"
DAY = 86400
# Sandbox reaping fixtures, each a User whose assignment was made 60 days ago:
# name -> (Home, provider ID or None for a fresh mock Sandbox, calls). A call is (started days
# ago, ended days ago or None while in progress, linked to the User). "linking" has a call from
# its Home that /voice has not yet linked to the User; "diag" is the assignment the diagnostics
# race rewrites.
REAP_USERS = {
    "idle": ("+15555550105", None, [(40, 40, True)]),
    "gone": ("+15555550106", "mock-reap-gone", []),
    "recent": ("+15555550107", "mock-reap-recent", [(45, 45, True), (2, 2, True)]),
    "live": ("+15555550108", "mock-reap-live", [(45, 45, True), (0.05, None, True)]),
    "linking": ("+15555550104", "mock-reap-linking", [(45, 45, True), (0.001, None, False)]),
    "diag": ("+15555550103", "mock-diag-old", [(1, 1, True)]),
}
# Users already reaped, whose old Sandboxes are only rows in sandbox_deletions: "pending" was left
# by an interrupted pass (its Sandbox is already gone), "flaky" is refused by Daytona until told.
REAPED_USERS = {"pending": ("+15555550102", "mock-reap-pending"), "flaky": ("+15555550110", "mock-reap-flaky")}


def user_sql(name: str) -> str:
    return f"(SELECT id FROM users WHERE identity_hash = 'reap-{name}')"


def reap_user(name: str, phone: str) -> str:
    return (f"INSERT INTO users (identity_hash, invite_code, registered_at, home_phone, home_used_at) "
            f"VALUES ('reap-{name}', '{LOCAL_INVITE}', unixepoch() - {60 * DAY}, '{phone}', unixepoch() - {40 * DAY})")


def reaped_assignment(name: str, provider: str, due: str = "unixepoch() - 60") -> list[str]:
    """An assignment a pass already cleared, and the sandbox_deletions row it left."""
    return [
        f"INSERT INTO sandbox_assignments (user_id, provider_id, state, provisioning_started_at, created_at) "
        f"VALUES ({user_sql(name)}, NULL, 'reaped', 0, unixepoch() - {60 * DAY})",
        f"INSERT INTO sandbox_deletions (provider_id, user_id, reaped_at, next_attempt_at) "
        f"VALUES ('{provider}', {user_sql(name)}, unixepoch() - {DAY}, {due})",
    ]


def seed_reap_fixture() -> dict[str, int]:
    DaytonaMock.generation += 1
    DaytonaMock.exists = True
    DaytonaMock.detail_reads = 0
    statements = []
    for name, (phone, provider, calls) in REAP_USERS.items():
        provider = provider or f"mock-sandbox-{DaytonaMock.generation}"
        statements.append(reap_user(name, phone))
        statements.append(
            f"INSERT INTO sandbox_assignments (user_id, provider_id, state, provisioning_started_at, created_at) "
            f"VALUES ({user_sql(name)}, '{provider}', 'stopped', unixepoch() - {60 * DAY}, unixepoch() - {60 * DAY})")
        for index, (started, ended, linked) in enumerate(calls):
            end = "NULL" if ended is None else f"unixepoch() - {int(ended * DAY)}"
            statements.append(
                f"INSERT INTO voice_calls (call_sid, phone_number, status, started_at, last_activity_at, ended_at, user_id) "
                f"VALUES ('CA_REAP_{name.upper()}_{index}', '{phone}', '{'in-progress' if ended is None else 'completed'}', "
                f"unixepoch() - {int(started * DAY)}, unixepoch() - {int(started * DAY)}, {end}, "
                f"{user_sql(name) if linked else 'NULL'})")
    for name, (phone, provider) in REAPED_USERS.items():
        statements += [reap_user(name, phone), *reaped_assignment(name, provider)]
    d1_local(";\n".join(statements))
    rows = d1_local("SELECT id, identity_hash FROM users WHERE identity_hash LIKE 'reap-%'")
    return {row["identity_hash"].removeprefix("reap-"): row["id"] for row in rows}


def reap_assignments() -> dict[int, dict]:
    rows = d1_local("""SELECT s.user_id, s.provider_id, s.state FROM sandbox_assignments s
        JOIN users u ON u.id = s.user_id WHERE u.identity_hash LIKE 'reap-%'""")
    return {row["user_id"]: row for row in rows}


def reap_deletions() -> dict[str, dict]:
    rows = d1_local("SELECT provider_id, user_id, attempts, next_attempt_at, deleted_at, noticed_at FROM sandbox_deletions")
    return {row["provider_id"]: row for row in rows}


def run_reap_pass(base_url: str, users: dict[str, int], done) -> list[tuple[str, dict]]:
    """Trigger the daily pass; return the fixture Users' new sandbox.reap* events once `done`."""
    mark = d1_local("SELECT COALESCE(MAX(id), 0) AS id FROM audit_log")[0]["id"]
    status, _ = get(base_url, "/__scheduled?cron=" + urllib.parse.quote_plus(REAP_CRON))
    require(status == 200, f"scheduled trigger failed: {status}")
    ids = set(users.values())

    def events() -> list[tuple[str, dict]]:
        rows = d1_local(f"SELECT event, detail FROM audit_log WHERE id > {mark} AND event LIKE 'sandbox.reap%' ORDER BY id")
        pairs = [(row["event"], json.loads(row["detail"])) for row in rows]
        return [(event, detail) for event, detail in pairs if detail.get("user_id") in ids]

    deadline = time.time() + 15
    while not done(events()) and time.time() < deadline:
        time.sleep(0.2)
    time.sleep(0.5)
    return events()


def by_event(events: list[tuple[str, dict]], name: str) -> dict[int, dict]:
    return {detail["user_id"]: detail for event, detail in events if event == name}


def reap_dry_run_checks(base_url: str) -> dict[str, int]:
    """A dry run only audits candidates: no provider call, no assignment or deletion change."""
    users = seed_reap_fixture()
    before, deletions = reap_assignments(), reap_deletions()
    DaytonaMock.deleted.clear()
    events = run_reap_pass(base_url, users, lambda found: len(found) >= 2)
    require({event for event, _ in events} == {"sandbox.reap.candidate"}, f"dry run events: {events}")
    candidates = by_event(events, "sandbox.reap.candidate")
    require(set(candidates) == {users["idle"], users["gone"]}, f"dry-run candidates: {events}")
    require(all(detail["mode"] == "dry-run" and detail["idle_days"] >= 39 for detail in candidates.values()),
            f"dry-run candidate detail: {events}")
    require(DaytonaMock.deleted == [], f"dry run deleted a Sandbox: {DaytonaMock.deleted}")
    require(reap_assignments() == before, "dry run changed a Sandbox Assignment")
    require(reap_deletions() == deletions, "dry run changed a pending deletion")
    return users


def reap_delete_checks(base_url: str, token: str, users: dict[str, int]) -> None:
    """Delete mode: idle Sandboxes leave service at once and the same pass deletes them (a 404
    counts as gone); it also finishes a deletion an earlier pass left; refused deletions back off;
    recent, in-progress, and not-yet-linked callers are spared; the next call provisions fresh."""
    before = reap_assignments()
    idle_provider = before[users["idle"]]["provider_id"]
    refuse = {"mock-reap-flaky"}
    DaytonaMock.delete_hooks.append(lambda path: 503 if path.rsplit("/", 1)[-1] in refuse else None)
    DaytonaMock.deleted.clear()
    events = run_reap_pass(base_url, users, lambda found: len(by_event(found, "sandbox.reaped")) >= 3
                           and by_event(found, "sandbox.reap.failed"))
    started, reaped, failed = (by_event(events, name) for name in ("sandbox.reap.started", "sandbox.reaped", "sandbox.reap.failed"))
    require(set(started) == {users["idle"], users["gone"]}, f"reaps started: {events}")
    require(set(reaped) == {users["idle"], users["gone"], users["pending"]}, f"reaped: {events}")
    require(reaped[users["idle"]]["already_gone"] is False and reaped[users["gone"]]["already_gone"] is True
            and reaped[users["pending"]]["already_gone"] is True, f"404 not tolerated as already gone: {events}")
    require(set(failed) == {users["flaky"]} and failed[users["flaky"]]["attempts"] == 1, f"refused deletion: {events}")
    require(sorted(DaytonaMock.deleted) == sorted(f"/api/sandbox/{p}" for p in (idle_provider, "mock-reap-gone", "mock-reap-pending", "mock-reap-flaky")),
            f"provider deletions: {DaytonaMock.deleted}")
    after, deletions = reap_assignments(), reap_deletions()
    for name in ("idle", "gone"):
        require(after[users[name]]["provider_id"] is None and after[users[name]]["state"] == "reaped",
                f"{name} assignment not cleared: {after[users[name]]}")
    for name in ("recent", "live", "linking", "diag"):
        require(after[users[name]] == before[users[name]], f"{name} Sandbox was touched: {after[users[name]]}")
    for provider in (idle_provider, "mock-reap-gone", "mock-reap-pending"):
        require(deletions[provider]["deleted_at"] is not None, f"deletion not recorded as done: {deletions[provider]}")
    flaky = deletions["mock-reap-flaky"]
    now = d1_local("SELECT unixepoch() AS now")[0]["now"]
    require(flaky["deleted_at"] is None and flaky["attempts"] == 1 and flaky["next_attempt_at"] > now + DAY / 2,
            f"refused deletion not backed off: {flaky}")

    created = DaytonaMock.create_count
    phone = REAP_USERS["idle"][0]
    status, body = post(base_url, "/voice", {"From": phone, "CallSid": "CA_REAP_NEXT"}, token)
    require(status == 200 and "<Dial" in body, f"reaped User's next call: {status} {body}")
    fresh = reap_assignments()[users["idle"]]
    require(DaytonaMock.create_count == created + 1 and fresh["provider_id"] == f"mock-sandbox-{DaytonaMock.generation}"
            and fresh["provider_id"] != idle_provider, f"reaped User did not get a fresh Sandbox: {fresh}")
    post(base_url, "/voice/status", {"From": phone, "CallSid": "CA_REAP_NEXT", "CallStatus": "completed"}, token)
    reap_pending_call_checks(base_url, token, users, refuse)


def reap_pending_call_checks(base_url: str, token: str, users: dict[str, int], refuse: set[str]) -> None:
    """A call while the old Sandbox awaits deletion (and Daytona still lists it as running) gets a
    fresh Sandbox, and the pass later deletes the old one without touching the new. A deletion
    refused REAP_STUCK_ATTEMPTS times is flagged stuck, keeps retrying, and never returns to service."""
    DaytonaMock.generation += 1
    DaytonaMock.exists = True
    DaytonaMock.detail_reads = 0
    doomed, phone = f"mock-sandbox-{DaytonaMock.generation}", "+15555550111"
    d1_local(";\n".join([reap_user("waiting", phone), *reaped_assignment("waiting", doomed, due="unixepoch() + 86400")]))
    users["waiting"] = d1_local(f"SELECT id FROM users WHERE identity_hash = 'reap-waiting'")[0]["id"]
    created = DaytonaMock.create_count
    status, body = post(base_url, "/voice", {"From": phone, "CallSid": "CA_REAP_WAITING"}, token)
    require(status == 200 and "<Dial" in body, f"call while deletion pending: {status} {body}")
    fresh = reap_assignments()[users["waiting"]]
    require(DaytonaMock.create_count == created + 1 and fresh["provider_id"] == f"mock-sandbox-{DaytonaMock.generation}",
            f"call adopted the Sandbox awaiting deletion: {fresh}")
    post(base_url, "/voice/status", {"From": phone, "CallSid": "CA_REAP_WAITING", "CallStatus": "completed"}, token)

    # Both come due: the waiting Sandbox is deleted; flaky fails a third time and is flagged.
    DaytonaMock.delete_hooks.insert(0, lambda path: 200 if path == f"/api/sandbox/{doomed}" else None)
    d1_local(f"""UPDATE sandbox_deletions SET next_attempt_at = unixepoch() - 60 WHERE provider_id = '{doomed}';
        UPDATE sandbox_deletions SET attempts = 2, next_attempt_at = unixepoch() - 60 WHERE provider_id = 'mock-reap-flaky'""")
    DaytonaMock.deleted.clear()
    events = run_reap_pass(base_url, users, lambda found: by_event(found, "sandbox.reaped") and by_event(found, "sandbox.reap.stuck"))
    require(set(by_event(events, "sandbox.reaped")) == {users["waiting"]}, f"pending deletion after a call: {events}")
    require(set(by_event(events, "sandbox.reap.stuck")) == {users["flaky"]}
            and by_event(events, "sandbox.reap.failed")[users["flaky"]]["attempts"] == 3, f"stuck deletion: {events}")
    require(f"/api/sandbox/{doomed}" in DaytonaMock.deleted, f"old Sandbox not deleted: {DaytonaMock.deleted}")
    require(reap_assignments()[users["waiting"]]["provider_id"] == fresh["provider_id"], "deleting the old Sandbox touched the new one")
    require(reap_assignments()[users["flaky"]] == {"user_id": users["flaky"], "provider_id": None, "state": "reaped"},
            "a stuck Sandbox returned to service")

    # Daytona relents: the next due attempt deletes it, with no second stuck flag.
    refuse.clear()
    d1_local("UPDATE sandbox_deletions SET next_attempt_at = unixepoch() - 60 WHERE provider_id = 'mock-reap-flaky'")
    events = run_reap_pass(base_url, users, lambda found: by_event(found, "sandbox.reaped"))
    require(set(by_event(events, "sandbox.reaped")) == {users["flaky"]} and not by_event(events, "sandbox.reap.stuck"),
            f"retried deletion: {events}")
    require(reap_deletions()["mock-reap-flaky"]["deleted_at"] is not None, "retried deletion not recorded")
    diagnostics_race_checks(base_url, users)


def diagnostics_race_checks(base_url: str, users: dict[str, int]) -> None:
    """A diagnostics refresh that read Sandbox A cannot write A back over an assignment that a
    reap or replacement moved to B while the provider read was in flight."""
    reached, release = threading.Event(), threading.Event()

    def slow_detail(path):
        if path != "/api/sandbox/mock-diag-old":
            return None
        reached.set()
        release.wait(8)
        return 200, json.dumps({"id": "mock-diag-old", "state": "started", "desiredState": "started"}).encode(), "application/json"

    DaytonaMock.get_hooks.append(slow_detail)
    credentials = base64.b64encode(f"adam:{LOCAL_ADMIN_PASSWORD}".encode()).decode()
    result: dict = {}

    def snapshot():
        request = urllib.request.Request(base_url + "/admin/api/state",
                                         headers={"authorization": f"Basic {credentials}", "user-agent": "dogwalk-smoke-test/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            result["state"] = json.loads(response.read())

    reader = threading.Thread(target=snapshot)
    reader.start()
    try:
        require(reached.wait(15), "diagnostics never read the fixture Sandbox")
        d1_local(f"UPDATE sandbox_assignments SET provider_id = 'mock-diag-new', state = 'creating' WHERE user_id = {users['diag']}")
    finally:
        release.set()
        reader.join(30)
        DaytonaMock.get_hooks.remove(slow_detail)
    require("state" in result, "diagnostics snapshot failed")
    row = reap_assignments()[users["diag"]]
    require(row["provider_id"] == "mock-diag-new" and row["state"] == "creating",
            f"diagnostics refresh clobbered a replaced assignment: {row}")


def research_object(key: str) -> bytes | None:
    """An object from the local research bucket, or None when it does not exist."""
    result = subprocess.run(
        [project_wrangler(), "r2", "object", "get", f"dogwalk-voice-research/{key}", "--local", "--pipe"],
        cwd=WORKER_DIR, capture_output=True,
    )
    return result.stdout if result.returncode == 0 and result.stdout else None


def donation_session(prefix: str) -> dict:
    data = research_object(prefix + "session.json")
    require(data is not None, f"no session.json under {prefix}")
    return json.loads(data)


def stream_capability(date: str, session: str, round_number: int, expires: int, account: str = TEST_ACCOUNT) -> str:
    content = f"{date}.{session}.{round_number}.{expires}.{account}"
    mac = hmac.new(LOCAL_IDENTITY_SECRET.encode(), f"donation-stream-v1:{content}".encode(), hashlib.sha256).digest()
    return content + "." + base64.urlsafe_b64encode(mac).decode().rstrip("=")


def open_stream(base_url: str, path: str, token: str | None, scheme: str = "http"):
    """Connect as Twilio's Media Streams client would, signing the upgrade over its URL."""
    headers = {"x-twilio-signature": sign(f"{scheme}://dogwalk.tools{path}", {}, token)} if token else {}
    return ws_connect(base_url.replace("http://", "ws://") + path, additional_headers=headers, open_timeout=5)


def stream_rejection(base_url: str, path: str, token: str | None) -> int:
    try:
        with open_stream(base_url, path, token):
            return 101
    except InvalidStatus as error:
        return error.response.status_code


def donation_checks(base_url: str, token: str) -> None:
    """Voice Donation: reached by saying donate, a spoken consent, then signed per-round streams
    whose audio lands in the research bucket with no link to the caller."""
    call = {"From": DONOR_PHONE, "CallSid": "CA_DONATE", "AccountSid": TEST_ACCOUNT}
    status, body = post(base_url, "/voice", call, token)
    require(status == 200 and "say donate" in body, f"donor greeting: {body}")
    status, body = post(base_url, "/voice/claim?attempts=1", {**call, "SpeechResult": "I want to donate.", "Confidence": "0.9"}, token)
    require(status == 200 and '<Redirect method="POST">/voice/donation</Redirect>' in body, f"donate not routed: {body}")
    status, body = post(base_url, "/voice/donation", call, token)
    require(status == 200 and 'action="/voice/donation/consent?attempts=1"' in body
            and "not linked to your phone number" in body and "say stop at any time" in body
            and "<Stream" not in body, f"consent prompt: {body}")
    status, body = post(base_url, "/voice/donation/consent?attempts=1", {**call, "SpeechResult": "No thanks."}, token)
    require("Nothing was recorded" in body and "<Hangup/>" in body and "<Stream" not in body, f"declined consent: {body}")
    status, body = post(base_url, "/voice/donation/consent?attempts=1", {**call, "SpeechResult": "Hmm."}, token)
    require("Please say yes" in body and "consent?attempts=2" in body, f"unclear consent: {body}")
    status, body = post(base_url, "/voice/donation/consent?attempts=3", {**call, "SpeechResult": "what"}, token)
    require("nothing was recorded" in body and "<Hangup/>" in body, f"unclear consent cap: {body}")
    status, body = post(base_url, "/voice/donation/consent?attempts=1", {**call, "SpeechResult": "Yes, I agree."}, token)
    stream = re.search(r'<Start><Stream name="donation-round-01" url="wss://dogwalk\.tools(/voice/donation/stream/[^"]+)" track="inbound_track">', body)
    action = re.search(r'action="/voice/donation/round\?date=([\d-]+)&amp;session=([0-9a-f-]{36})&amp;round=1"', body)
    require(status == 200 and stream and action and "<Play>https://dogwalk.tools/voice/donation/tone.wav</Play>" in body
            and body.index("<Start>") < body.index("<Play>") < body.index("<Gather"), f"first round: {body}")
    date, session = action[1], action[2]
    prefix = f"donations/{date}/{session}/"
    record = donation_session(prefix)
    require(record["consent_version"] and record["ended_at"] is None and len(record["rounds"]) == 10
            and [r["kind"] for r in record["rounds"]].count("free_speech") == 2
            and len({r["prompt"] for r in record["rounds"]}) == 10, f"session.json: {record}")
    stored = json.dumps(record)
    require(DONOR_PHONE[2:] not in stored and "CA_" not in stored and "AccountSid" not in stored, f"session links the caller: {stored}")

    # Streams: the upgrade needs Twilio's signature and an unexpired capability for this round.
    require(stream_rejection(base_url, stream[1], None) == 403, "unsigned stream accepted")
    require(stream_rejection(base_url, stream[1][:-2] + "xx", token) == 403, "tampered capability accepted")
    expired = f"/voice/donation/stream/{stream_capability(date, session, 1, int(time.time()) - 1)}"
    require(stream_rejection(base_url, expired, token) == 403, "expired capability accepted")
    future = f"/voice/donation/stream/{stream_capability(date, session, 11, int(time.time()) + 60)}"
    require(stream_rejection(base_url, future, token) == 404, "capability for a round the session lacks accepted")
    start = {"event": "start", "start": {
        "accountSid": TEST_ACCOUNT, "streamSid": "MZ" + "1" * 32, "callSid": "CA_DONATE", "tracks": ["inbound"],
        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        "customParameters": {"session": session, "round": "1"}}}
    with open_stream(base_url, stream[1], token) as ws:
        ws.send(json.dumps({**start, "start": {**start["start"], "accountSid": "AC" + "f" * 32}}))
        try:
            ws.recv(timeout=5)
        except Exception:
            pass
    require(research_object(prefix + "round-01.wav") is None, "a stream from another account was kept")
    with open_stream(base_url, stream[1], token, scheme="wss") as ws:
        ws.send(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
        ws.send(json.dumps(start))
        payload = base64.b64encode(bytes([0x7F, 0xFF, 0x00, 0x80] * 40)).decode()
        for frame in range(50):
            # One lost 20 ms frame at frame 25: the next timestamp jumps by 40 ms.
            timestamp = frame * 20 + (20 if frame >= 25 else 0)
            ws.send(json.dumps({"event": "media", "media": {"track": "inbound", "chunk": str(frame + 1), "timestamp": str(timestamp), "payload": payload}}))
        ws.send(json.dumps({"event": "stop", "stop": {"accountSid": TEST_ACCOUNT, "callSid": "CA_DONATE"}}))
        try:
            ws.recv(timeout=5)
        except Exception:
            pass
    deadline = time.time() + 10
    while time.time() < deadline and not donation_session(prefix)["rounds"][0]["stream"]:
        time.sleep(0.3)
    first = donation_session(prefix)["rounds"][0]["stream"]
    require(first and first["complete"] and first["frames"] == 50 and first["inserted_gap_ms"] == 20
            and not first["truncated"], f"stream record: {first}")
    audio = research_object(prefix + "round-01.wav")
    require(audio and audio[:4] == b"RIFF" and audio[8:16] == b"WAVEfmt " and len(audio) == 44 + 2 * (50 * 160 + 160)
            and int.from_bytes(audio[24:28], "little") == 8000, f"round WAV: {audio[:44] if audio else None}")
    # mu-law 0x7F and 0xFF are both silence; 0x00 and 0x80 are the two extremes.
    require(audio[44:52] == bytes([0, 0, 0, 0, 0x84, 0x82, 0x7C, 0x7D]), f"mu-law decode: {audio[44:52]!r}")
    require(stream_rejection(base_url, stream[1], token) == 409, "a recorded round accepted a second stream")

    # Rounds: the transcript is kept, the stream stopped, and the next round started.
    round_path = f"/voice/donation/round?date={date}&session={session}&round="
    phrase = record["rounds"][0]["prompt"]
    status, body = post(base_url, round_path + "1", {**call, "SpeechResult": phrase, "Confidence": "0.87"}, token)
    require(status == 200 and body.startswith('<Response><Stop><Stream name="donation-round-01"/></Stop><Start><Stream name="donation-round-02"')
            and "round=2" in body, f"next round: {body}")
    heard = donation_session(prefix)["rounds"][0]
    require(heard["transcript"] == phrase and heard["confidence"] == 0.87, f"transcript: {heard}")
    status, body = post(base_url, round_path + "2", {**call, "SpeechResult": "Stop.", "Confidence": "0.9"}, token)
    require("Stopped. What you already recorded is kept" in body and "<Hangup/>" in body and "<Start>" not in body, f"stop: {body}")
    ended = donation_session(prefix)
    require(ended["ended_by"] == "stop" and ended["ended_at"] and ended["rounds"][0]["stream"], f"stopped session: {ended}")
    require(stream_rejection(base_url, f"/voice/donation/stream/{stream_capability(date, session, 3, int(time.time()) + 60)}", token) == 404,
            "an ended session accepted a stream")

    # A second session runs to the end; the last round's action finishes it.
    status, body = post(base_url, "/voice/donation/consent?attempts=1", {**call, "SpeechResult": "okay"}, token)
    second = re.search(r"session=([0-9a-f-]{36})", body)[1]
    require(second != session, "Donation Sessions share an id")
    status, body = post(base_url, f"/voice/donation/round?date={date}&session={second}&round=10",
                        {**call, "SpeechResult": "I had to stop at the store on the way home this morning.", "Confidence": "0.8"}, token)
    require("That is everything" in body and "<Hangup/>" in body, f"completion: {body}")
    require(donation_session(f"donations/{date}/{second}/")["ended_by"] == "completed", "free speech mentioning stop ended the session")

    with urllib.request.urlopen(base_url + "/voice/donation/tone.wav", timeout=5) as response:
        require(response.headers["content-type"] == "audio/wav" and response.read(4) == b"RIFF", "tone unavailable")
    events = [row["event"] for row in d1_local("SELECT event, detail FROM call_activity WHERE call_sid = 'CA_DONATE' ORDER BY id")]
    require(events.count("donation.started") == 2 and "donation.declined" in events and "donation.requested" in events, f"donation activity: {events}")
    details = json.dumps(d1_local("SELECT detail FROM call_activity WHERE call_sid = 'CA_DONATE'"))
    require(session not in details and second not in details, "Call Activity names a Donation Session")

    # A Voice Agent handoff sends the call from the GPT-Live leg to the donation flow.
    subprocess.run([project_wrangler(), "d1", "execute", "dogwalk", "--local", "--command",
                    f"INSERT INTO call_handoffs (call_sid, phone_number, action, created_at) VALUES ('CA_DONATE', '{DONOR_PHONE}', 'donation', unixepoch())"],
                   cwd=WORKER_DIR, check=True, stdout=subprocess.DEVNULL)
    status, body = post(base_url, "/voice/after-live", call, token)
    require(status == 200 and '<Redirect method="POST">/voice/donation</Redirect>' in body, f"donation handoff: {body}")


def ulaw_byte(target: int) -> int:
    """The mu-law byte whose decoded sample is nearest the target, as the Worker decodes it."""
    def decode(encoded: int) -> int:
        value = ~encoded & 0xFF
        magnitude = ((((value & 0x0F) << 3) + 0x84) << ((value & 0x70) >> 4)) - 0x84
        return -magnitude if value & 0x80 else magnitude
    return min(range(256), key=lambda encoded: abs(decode(encoded) - target))


def steady_audio(seconds: float, first_sample: int) -> bytes:
    """Constant audio from the first frame: the endpointer never hears an answer end in it, so
    the round is scored at the stream's stop. Its sample tells speaker_mock.js what to answer."""
    return bytes([ulaw_byte(first_sample)]) * int(seconds * 8000)


def answer_audio(first_sample: int, *parts: tuple[float, bool]) -> bytes:
    """A quiet line whose first sample tells speaker_mock.js what to answer, then (seconds,
    speech) parts: speech is a loud buzz, anything else silence. The endpointer hears an answer
    end 0.7 s into a silence after at least a second of speech."""
    loud, quiet = ulaw_byte(6000), ulaw_byte(0)
    audio = bytearray([ulaw_byte(first_sample)]) + bytes([quiet]) * 7999
    for seconds, speech in parts:
        audio += bytes([loud if speech else quiet]) * int(seconds * 8000)
    return bytes(audio)


def practice_stream(base_url: str, path: str, token: str, round_number: int, audio: bytes, heard, take: int = 0) -> None:
    """One round's caller-only stream, then the Gather's action (heard), then the stop event its
    <Stop> produces."""
    start = {"event": "start", "start": {"accountSid": TEST_ACCOUNT, "callSid": "CA_PRACTICE",
             "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
             "customParameters": {"round": str(round_number), "take": str(take)}}}
    with open_stream(base_url, path, token, scheme="wss") as ws:
        ws.send(json.dumps(start))
        for frame in range(len(audio) // 160):
            payload = base64.b64encode(audio[frame * 160:(frame + 1) * 160]).decode()
            ws.send(json.dumps({"event": "media", "media": {"track": "inbound", "timestamp": str(frame * 20), "payload": payload}}))
        heard()
        ws.send(json.dumps({"event": "stop", "stop": {"accountSid": TEST_ACCOUNT, "callSid": "CA_PRACTICE"}}))
        try:
            ws.recv(timeout=5)
        except Exception:
            pass


def practice_checks(base_url: str, token: str) -> None:
    """Challenge Practice: a deterministic loop of phrases, transcript first, then a spoken level
    from the verifier, never the number; every exit returns the call to /voice."""
    received_cue = re.escape("<Play>https://dogwalk.tools/voice/practice/received.wav</Play>")
    user = d1_local(f"SELECT id, home_phone FROM users WHERE home_phone LIKE '{TEST_NUMBERS}' AND reaped_at IS NULL LIMIT 1")[0]
    phone = user["home_phone"]
    call = {"From": phone, "CallSid": "CA_PRACTICE", "AccountSid": TEST_ACCOUNT}
    subprocess.run([project_wrangler(), "d1", "execute", "dogwalk", "--local", "--command",
                    f"INSERT INTO voice_calls (call_sid, phone_number, status, started_at, last_activity_at, user_id) "
                    f"VALUES ('CA_PRACTICE', '{phone}', 'in-progress', unixepoch(), unixepoch(), {user['id']}); "
                    f"DELETE FROM voice_references WHERE user_id = {user['id']}"],
                   cwd=WORKER_DIR, check=True, stdout=subprocess.DEVNULL)

    # The Voice Agent's handoff, and a User without a Voice Reference.
    subprocess.run([project_wrangler(), "d1", "execute", "dogwalk", "--local", "--command",
                    f"INSERT INTO call_handoffs (call_sid, phone_number, action, created_at) VALUES ('CA_PRACTICE', '{phone}', 'practice', unixepoch())"],
                   cwd=WORKER_DIR, check=True, stdout=subprocess.DEVNULL)
    status, body = post(base_url, "/voice/after-live", call, token)
    require(status == 200 and '<Redirect method="POST">/voice/practice</Redirect>' in body, f"practice handoff: {body}")
    status, body = post(base_url, "/voice/practice", call, token)
    require(status == 200 and "needs an enrolled voice" in body and body.endswith('<Redirect method="POST">/voice</Redirect></Response>')
            and "<Stream" not in body, f"practice without a Voice Reference: {body}")
    returned = lambda sid: json.loads(d1_local(f"SELECT returned_from FROM voice_calls WHERE call_sid = '{sid}'")[0]["returned_from"] or "null")
    require(returned("CA_PRACTICE") == {"flow": "practice", "outcome": "unavailable", "reason": "no_voice_reference"},
            f"practice return note: {returned('CA_PRACTICE')}")

    subprocess.run([project_wrangler(), "d1", "execute", "dogwalk", "--local", "--command",
                    f"INSERT INTO voice_references (user_id, embedding_space, embedding, enrolled_at) "
                    f"VALUES ({user['id']}, 'mock-speaker-space', '[0.5, 0.5]', unixepoch())"],
                   cwd=WORKER_DIR, check=True, stdout=subprocess.DEVNULL)
    status, body = post(base_url, "/voice/practice", call, token)
    require(status == 200 and "Nothing is recorded" in body, f"practice intro: {body}")

    def round_of(body: str, number: int, take: int = 0) -> tuple[str, str]:
        name = f"practice-round-{number:02d}" + (f"-{take}" if take else "")
        stream = re.search(rf'<Start><Stream name="{name}" url="wss://dogwalk\.tools(/voice/practice/stream/[^"]+)" track="inbound_track">', body)
        action = re.search(rf'action="(/voice/practice/heard\?round={number}&amp;take={take}&amp;silent=\d&amp;judged=\d+)"', body)
        require(stream and action and "<Play>https://dogwalk.tools/voice/practice/tone.wav</Play>" in body, f"round {number}: {body}")
        # A one-second pause ends the answer: "auto" waited up to four seconds after a car caller's last word.
        require('<Gather input="speech" speechTimeout="1" timeout="5" ' in body, f"round {number} Gather timing: {body}")
        phrase = d1_local(f"SELECT phrase FROM challenge_rounds WHERE call_sid = 'CA_PRACTICE' AND round = {number}")[0]["phrase"]
        require(f">{phrase}</Say></Gather>" in body, f"round {number} does not say its phrase: {body}")
        hints = re.search(r'hints="([^"]*)"', body)[1].split(",")
        require(hints[0] == "stop" and all(word.rstrip(".").lower() in hints for word in phrase.split()
                                           if word.lower() not in ("a", "the", "my", "your", "our", "his", "her", "me", "we", "was", "this")),
                f"round {number} hints: {hints} for {phrase}")
        return stream[1], action[1].replace("&amp;", "&")

    def speculative_stored(number: int, seconds: float) -> None:
        """Wait until the stream has stored the speculative score of its first `seconds` of audio."""
        for _ in range(40):
            rows = d1_local(f"SELECT spec_speech_seconds FROM challenge_rounds WHERE call_sid = 'CA_PRACTICE' AND round = {number}")
            if rows and rows[0]["spec_speech_seconds"] == seconds:
                return
            time.sleep(0.25)
        raise AssertionError(f"round {number}: no speculative score of {seconds} s before /heard")

    def scored(number: int, body: str, audio: bytes, stored: float | None = None) -> str:
        stream, action = round_of(body, number)
        phrase = d1_local(f"SELECT phrase FROM challenge_rounds WHERE call_sid = 'CA_PRACTICE' AND round = {number}")[0]["phrase"]
        redirect = []
        def heard() -> None:
            if stored is not None:
                speculative_stored(number, stored)
            _, twiml = post(base_url, action, {**call, "SpeechResult": phrase.lower().rstrip("."), "Confidence": "0.9"}, token)
            # The received cue plays before the wait for the level.
            match = re.fullmatch(rf'<Response><Stop><Stream name="practice-round-{number:02d}"/></Stop>{received_cue}'
                                 rf'<Redirect method="POST">(/voice/practice/result\?round={number}&amp;judged=\d+)</Redirect></Response>', twiml)
            require(match is not None, f"matched phrase: {twiml}")
            redirect.append(match[1].replace("&amp;", "&"))
        practice_stream(base_url, stream, token, number, audio, heard)
        status, result = post(base_url, redirect[0], call, token)
        require(status == 200, f"result {number}: {status} {result}")
        return result

    # Round 1: one wrong content word (a misheard prompt) is a wrong phrase, not scored, and the
    # stream is stopped.
    stream, action = round_of(body, 1)
    phrase = d1_local("SELECT phrase FROM challenge_rounds WHERE call_sid = 'CA_PRACTICE' AND round = 1")[0]["phrase"]
    misheard = phrase.rstrip(".").rsplit(" ", 1)[0] + " cow"
    status, body = post(base_url, action, {**call, "SpeechResult": misheard, "Confidence": "0.9"}, token)
    require(re.match(rf'<Response><Stop><Stream name="practice-round-01"/></Stop>{received_cue}<Say[^>]*>That wasn\'t the sentence\. Here\'s another\.', body)
            and "/voice/practice/result" not in body, f"wrong phrase: {body}")
    require(stream_rejection(base_url, stream, token) == 409, "a skipped round accepted a stream")
    # Each level, said as words and never as a number.
    body = scored(2, body, steady_audio(3.0, 8000))
    require("Strong voice match. Here's another." in body and not any(re.search(r"\d", said) for said in re.findall(r"<Say[^>]*>([^<]*)</Say>", body)), f"strong: {body}")
    # MIN_SPEECH_SECONDS itself is enough to judge.
    body = scored(3, body, steady_audio(1.5, 6000))
    require("Voice matched. Here's another." in body, f"match: {body}")
    body = scored(4, body, steady_audio(3.0, 3000))
    require("Voice not matched. Here's another." in body, f"no match: {body}")
    # Too little speech, by the verifier's measure and by its insufficient-audio error.
    body = scored(5, body, steady_audio(1.2, 8000))
    require("Not enough to judge. Here's another." in body, f"short speech: {body}")
    body = scored(6, body, steady_audio(0.3, 8000))
    require("Not enough to judge. Here's another." in body, f"insufficient audio: {body}")
    # A 35 s answer is judged on its last 30 s, the verifier's maximum (the mock refuses more).
    body = scored(7, body, steady_audio(35.0, 6000))
    require("Voice matched. Here's another." in body, f"long answer: {body}")
    # "Say that again" replays the same phrase as a new take with its own stream, counting nothing;
    # the stopped take refuses a late stream.
    old_stream, action = round_of(body, 8)
    phrase = d1_local("SELECT phrase FROM challenge_rounds WHERE call_sid = 'CA_PRACTICE' AND round = 8")[0]["phrase"]
    status, body = post(base_url, action, {**call, "SpeechResult": "Say that again?", "Confidence": "0.9"}, token)
    require(body.startswith('<Response><Stop><Stream name="practice-round-08"/></Stop><Say voice="Polly.Joanna">Again.</Say>')
            and f">{phrase}</Say></Gather>" in body and "Here's another" not in body and "received.wav" not in body, f"repeat: {body}")
    _, action = round_of(body, 8, take=1)
    require(stream_rejection(base_url, old_stream, token) == 409, "a replaced take accepted a stream")
    # Past three replays a repeat request moves to a fresh phrase, still without the received cue.
    for take in (2, 3):
        status, body = post(base_url, action, {**call, "SpeechResult": "Again.", "Confidence": "0.9"}, token)
        _, action = round_of(body, 8, take=take)
    status, body = post(base_url, action, {**call, "SpeechResult": "Pardon?", "Confidence": "0.9"}, token)
    require(body.startswith('<Response><Stop><Stream name="practice-round-08-3"/></Stop><Say voice="Polly.Joanna">No more repeats of that one. '
                            "Here's another.</Say>") and "received.wav" not in body, f"repeat limit: {body}")
    _, action = round_of(body, 9)
    # A short stop returns the call to the Voice Agent and clears the rounds.
    status, body = post(base_url, action, {**call, "SpeechResult": "Stop.", "Confidence": "0.9"}, token)
    require(body.startswith('<Response><Stop><Stream name="practice-round-09"/></Stop>') and "Practice over. Back to Dogwalk." in body
            and "received.wav" not in body and body.endswith('<Redirect method="POST">/voice</Redirect></Response>'), f"stop: {body}")
    require(d1_local("SELECT COUNT(*) AS n FROM challenge_rounds WHERE call_sid = 'CA_PRACTICE'")[0]["n"] == 0, "rounds kept after stop")

    # An empty transcript moves on; a second in a row ends practice. Both are logged as silent.
    status, body = post(base_url, "/voice/practice", call, token)
    _, action = round_of(body, 1)
    status, body = post(base_url, action, {**call, "SpeechResult": ""}, token)
    require("I didn't hear the sentence. Here's another." in body and "received.wav" not in body, f"first silence: {body}")
    _, action = round_of(body, 2)
    status, body = post(base_url, action, {**call, "SpeechResult": ""}, token)
    require("I didn't hear anything. Back to Dogwalk." in body and "received.wav" not in body and body.endswith('<Redirect method="POST">/voice</Redirect></Response>'),
            f"second silence: {body}")

    # A verifier error ends practice as unavailable and returns.
    status, body = post(base_url, "/voice/practice", call, token)
    body = scored(1, body, steady_audio(3.0, -8000))
    require("not available right now. Back to Dogwalk." in body and body.endswith('<Redirect method="POST">/voice</Redirect></Response>'),
            f"verifier error: {body}")

    # Speculative scoring: the stream starts the verifier when the answer ends (0.7 s of silence
    # after a second of speech), before /heard. The mock's speech seconds are the audio length it
    # was given, so they show which audio was scored.
    status, body = post(base_url, "/voice/practice", call, token)
    # The answer ends at 3.7 s; the stream runs to 4.0 s. The score stored at the end, before
    # /heard, is used.
    body = scored(1, body, answer_audio(8000, (2.0, True), (1.0, False)), stored=3.7)
    require("Strong voice match. Here's another." in body, f"speculative score: {body}")
    # The caller keeps talking after a pause long enough to end the answer: a fresh score starts
    # at the second end (5.6 s) and only it is used.
    body = scored(2, body, answer_audio(6000, (2.0, True), (0.9, False), (1.0, True), (1.0, False)), stored=5.6)
    require("Voice matched. Here's another." in body, f"restarted speculative score: {body}")
    # Talking again just before the stream stops leaves the first score stale and no second end:
    # the whole stream (5.2 s) is scored at the stop.
    body = scored(3, body, answer_audio(3000, (2.0, True), (0.9, False), (1.0, True), (0.3, False)))
    require("Voice not matched. Here's another." in body, f"stale speculative score: {body}")
    # A fifth end of the answer in one take starts no verifier call: the whole stream (13 s) is
    # scored at the stop.
    body = scored(4, body, answer_audio(8000, *[(1.2, True), (0.8, False)] * 6))
    require("Strong voice match. Here's another." in body, f"speculative score limit: {body}")

    def discarded(number: int, body: str, transcript: str, take: int = 0) -> str:
        """Stream an answer, wait for its stored speculative score, then send a transcript that
        is not a matched phrase: the score must be gone, never used or logged."""
        stream, action = round_of(body, number, take=take)
        answer = []
        def heard() -> None:
            speculative_stored(number, 3.7)
            answer.append(post(base_url, action, {**call, "SpeechResult": transcript, "Confidence": "0.9"}, token)[1])
        practice_stream(base_url, stream, token, number, answer_audio(8000, (2.0, True), (1.0, False)), heard, take=take)
        time.sleep(1)
        row = d1_local("SELECT state, spec_seq, spec_started_ms, spec_cosine, spec_speech_seconds, spec_error, cosine, speculative "
                       f"FROM challenge_rounds WHERE call_sid = 'CA_PRACTICE' AND round = {number}")[0]
        require(row == {"state": "skipped", "spec_seq": None, "spec_started_ms": None, "spec_cosine": None, "spec_speech_seconds": None,
                        "spec_error": None, "cosine": None, "speculative": 0}, f"round {number} kept a speculative score: {row}")
        return answer[0]

    # A wrong phrase discards the score its answer started.
    phrase = d1_local("SELECT phrase FROM challenge_rounds WHERE call_sid = 'CA_PRACTICE' AND round = 5")[0]["phrase"]
    body = discarded(5, body, phrase.rstrip(".").rsplit(" ", 1)[0] + " cow")
    require("That wasn't the sentence." in body, f"wrong phrase after an answer end: {body}")
    # So does a repeat request past the replay limit.
    for take in (1, 2, 3):
        _, action = round_of(body, 6, take=take - 1)
        status, body = post(base_url, action, {**call, "SpeechResult": "Again.", "Confidence": "0.9"}, token)
    body = discarded(6, body, "Pardon?", take=3)
    require("No more repeats of that one." in body, f"repeat limit after an answer end: {body}")
    _, action = round_of(body, 7)
    status, body = post(base_url, action, {**call, "SpeechResult": "Stop.", "Confidence": "0.9"}, token)
    require("Practice over." in body, f"stop after speculative rounds: {body}")

    # Both cues are public 8 kHz WAVs Twilio can cache; the received cue is 210 ms, unlike the 150 ms tone.
    for path, ms in (("/voice/practice/tone.wav", 150), ("/voice/practice/received.wav", 210)):
        with urllib.request.urlopen(base_url + path, timeout=5) as response:
            audio = response.read()
            require(response.headers["content-type"] == "audio/wav" and response.headers["cache-control"] == "public, max-age=86400"
                    and audio[:4] == b"RIFF" and int.from_bytes(audio[24:28], "little") == 8000 and len(audio) == 44 + ms * 16,
                    f"{path}: {response.headers} {len(audio)} bytes")

    rows = d1_local("SELECT event, detail FROM call_activity WHERE call_sid = 'CA_PRACTICE' AND event LIKE 'practice.%' ORDER BY id")
    rounds = [json.loads(row["detail"]) for row in rows if row["event"] == "practice.round"]
    require([r["outcome"] for r in rounds] == ["wrong_phrase", "strong_match", "match", "no_match", "not_enough_speech",
                                               "not_enough_speech", "match", "repeat_limit", "silent", "silent", "unscored",
                                               "strong_match", "match", "no_match", "strong_match", "wrong_phrase", "repeat_limit"],
            f"practice rounds: {rounds}")
    # Steady audio never ends an answer, so it is scored at the stop, without a head start.
    require(rounds[1]["speech_seconds"] == 3.0 and 0.79 < rounds[1]["cosine"] < 0.82 and isinstance(rounds[1].get("wait_ms"), int)
            and rounds[1]["speculative"] is False and "head_start_ms" not in rounds[1], f"scored round detail: {rounds[1]}")
    speculative = rounds[11:17]
    require([(r.get("speculative"), r.get("speech_seconds")) for r in speculative[:4]] == [(True, 3.7), (True, 5.6), (False, 5.2), (False, 13.0)]
            and all(r.get("head_start_ms", 0) > 0 for r in speculative[:2]) and not any("head_start_ms" in r for r in speculative[2:4]),
            f"speculative rounds: {speculative}")
    require(not any({"cosine", "speech_seconds", "error", "speculative", "head_start_ms"} & set(r) for r in speculative[4:]),
            f"a discarded round logged a score: {speculative[4:]}")
    # Too little speech keeps the verifier's score for calibration; insufficient audio has none.
    require(rounds[4]["speech_seconds"] == 1.2 and 0.79 < rounds[4]["cosine"] < 0.82, f"short speech detail: {rounds[4]}")
    require(rounds[5]["cosine"] is None and rounds[5]["error"] == "insufficient-audio", f"insufficient audio detail: {rounds[5]}")
    require(rounds[6]["speech_seconds"] == 30.0, f"long answer was not trimmed to 30 s: {rounds[6]}")
    require(all("transcript" not in r and "phrase" not in r for r in rounds), "Call Activity kept practice speech")
    # Only scored rounds count toward the round limit: four of the first practice's nine judged.
    ended = [json.loads(row["detail"]) for row in rows if row["event"] == "practice.ended"]
    require(ended[0]["reason"] == "stop" and ended[0]["judged"] == 4 and ended[0]["attempts"] == 9, f"practice.ended: {ended[0]}")
    events = [row["event"] for row in rows]
    require(events.count("practice.started") == 4 and events.count("practice.ended") == 4 and "practice.unavailable" in events
            and events.count("practice.repeat") == 6,
            f"practice activity: {events}")
    # The last practice's end is what the reconnected Voice Agent hears (call_started.returned_from).
    require(returned("CA_PRACTICE") == {"flow": "practice", "outcome": ended[-1]["reason"]}, f"practice return note: {returned('CA_PRACTICE')}")


ROAMING_HANDLE = "anchor antelope"
ENROLL_HANDLE = "acrobat albatross"
ROAMING_PHONE = "+15555550122"
ENROLL_PHONE = "+15555550120"
REGISTER_PHONE = "+15555550121"
REGISTER_FAIL_PHONE = "+15555550123"
INVITEE_PHONE = "+15555550127"
INVITEE_WORDS = "aimless bodyguard banjo"


def identity_checks(base_url: str, token: str) -> None:
    """Shadow Identity Check: a Home call hears its Handle and one challenge, then connects
    whatever the level; a caller who names another Handle takes three and is never admitted;
    a User without a Voice Reference enrolls instead; Registration ends with enrollment."""
    received_cue = re.escape("<Play>https://dogwalk.tools/voice/identity/received.wav</Play>")
    home = d1_local(f"SELECT id, handle, home_phone FROM users WHERE home_phone LIKE '{TEST_NUMBERS}' AND reaped_at IS NULL "
                    f"AND home_phone NOT IN ('{ROAMING_PHONE}', '{ENROLL_PHONE}', '{REGISTER_PHONE}', '{REGISTER_FAIL_PHONE}', '{INVITEE_PHONE}') LIMIT 1")[0]
    uid, handle, phone = home["id"], home["handle"], home["home_phone"]
    d1_local(f"INSERT OR REPLACE INTO voice_references (user_id, embedding_space, embedding, enrolled_at) "
             f"VALUES ({uid}, 'mock-speaker-space', '[0.5, 0.5]', unixepoch()); UPDATE users SET challenges_completed = 0 WHERE id = {uid}")
    d1_local(f"INSERT INTO users (handle, identity_hash, invite_code, registered_at, home_phone, home_used_at) VALUES "
             f"('{ROAMING_HANDLE}', 'mock-identity-roaming', '{LOCAL_INVITE}', unixepoch(), '{ROAMING_PHONE}', unixepoch()), "
             f"('{ENROLL_HANDLE}', 'mock-identity-enroll', '{LOCAL_INVITE}', unixepoch(), '{ENROLL_PHONE}', unixepoch())")
    vid = d1_local(f"SELECT id FROM users WHERE handle = '{ROAMING_HANDLE}'")[0]["id"]
    eid = d1_local(f"SELECT id FROM users WHERE handle = '{ENROLL_HANDLE}'")[0]["id"]
    d1_local(f"INSERT INTO voice_references (user_id, embedding_space, embedding, enrolled_at) VALUES ({vid}, 'mock-speaker-space', '[0.5, 0.5]', unixepoch())")

    def call(sid: str, caller: str = phone) -> dict:
        return {"From": caller, "CallSid": sid, "AccountSid": TEST_ACCOUNT}

    def identity(sid: str) -> dict | None:
        row = d1_local(f"SELECT identity FROM voice_calls WHERE call_sid = '{sid}'")
        return json.loads(row[0]["identity"]) if row and row[0]["identity"] else None

    def round_of(body: str, sid: str, number: int, path: str, user: int, take: int = 0) -> tuple[str, str, str]:
        name = f"identity-round-{number:02d}" + (f"-{take}" if take else "")
        stream = re.search(rf'<Start><Stream name="{name}" url="wss://dogwalk\.tools(/voice/identity/stream/[^"]+)" track="inbound_track">', body)
        action = re.search(rf'action="(/voice/identity/heard\?path={path}&amp;user={user}&amp;silent=\d&amp;judged=\d&amp;passed=\d'
                           rf'&amp;start=\d+&amp;round={number}&amp;take={take})"', body)
        require(stream and action and "<Play>https://dogwalk.tools/voice/identity/tone.wav</Play>" in body, f"identity round {number}: {body}")
        phrase = d1_local(f"SELECT phrase FROM challenge_rounds WHERE call_sid = '{sid}' AND round = {number}")[0]["phrase"]
        require(f">{phrase}</Say></Gather>" in body, f"identity round {number} does not say its phrase: {body}")
        return stream[1], action[1].replace("&amp;", "&"), phrase

    def answer(body: str, form: dict, number: int, path: str, user: int, seconds: float, first_sample: int, take: int = 0,
               audio: bytes | None = None, stored: float | None = None) -> str:
        """A matched answer: its stream is scored (steady audio at the stop, or `audio` whose end
        starts a speculative score of `stored` seconds before the transcript), and the result
        step speaks what follows."""
        stream, action, phrase = round_of(body, form["CallSid"], number, path, user, take)
        name = f"identity-round-{number:02d}" + (f"-{take}" if take else "")
        redirect = []
        def heard() -> None:
            for _ in range(40 if stored is not None else 0):
                rows = d1_local(f"SELECT spec_speech_seconds FROM challenge_rounds WHERE call_sid = '{form['CallSid']}' AND round = {number}")
                if rows and rows[0]["spec_speech_seconds"] == stored:
                    break
                time.sleep(0.25)
            _, twiml = post(base_url, action, {**form, "SpeechResult": phrase.lower().rstrip("."), "Confidence": "0.9"}, token)
            match = re.fullmatch(rf'<Response><Stop><Stream name="{name}"/></Stop>{received_cue}'
                                 rf'<Redirect method="POST">(/voice/identity/result\?[^<]+)</Redirect></Response>', twiml)
            require(match is not None, f"identity matched phrase: {twiml}")
            redirect.append(match[1].replace("&amp;", "&"))
        practice_stream(base_url, stream, token, number, audio or steady_audio(seconds, first_sample), heard, take)
        status, result = post(base_url, redirect[0], form, token)
        require(status == 200, f"identity result {number}: {status} {result}")
        return result

    def say(sid: str, number: int, path: str, user: int, body: str, words: str, caller: str = phone) -> str:
        _, action, _ = round_of(body, sid, number, path, user)
        return post(base_url, action, {**call(sid, caller), "SpeechResult": words, "Confidence": "0.9"}, token)[1]

    connect = '<Redirect method="POST">/voice</Redirect></Response>'
    # A Home call: the Handle, full instructions, one challenge, the level, then the Voice Agent.
    status, body = post(base_url, "/voice", call("CA_ID_1"), token)
    require(status == 200 and f"Dogwalk, for {handle}. To check that it's you" in body and "If you're someone else, say so." in body
            and "<Dial" not in body, f"Home greeting: {body}")
    body = answer(body, call("CA_ID_1"), 1, "home", uid, 3.0, 8000)
    require(body == f'<Response><Say voice="Polly.Joanna">Strong voice match.</Say>{connect}', f"strong Home level: {body}")
    require(identity("CA_ID_1") == {"path": "home", "mode": "sentence", "user_id": uid, "outcome": "strong_match", "level": "Strong voice match.",
                                    "enforcement": "admit", "shadow": True}, f"identity: {identity('CA_ID_1')}")
    status, body = post(base_url, "/voice", call("CA_ID_1"), token)
    # Re-entry goes straight to the connection step (this fixture's User shares the mock's one Sandbox, so it may not dial).
    require(status == 200 and "identity-round" not in body and "Dogwalk, for" not in body, f"the check ran again on re-entry: {body}")
    # Shadow mode: a voice that does not match still connects, and the record says what enforcement would do.
    for sid, sample, said, outcome, enforcement in (("CA_ID_2", 6000, "Voice matched.", "match", "admit"),
                                                    ("CA_ID_3", 3000, "Voice not matched.", "no_match", "require_roaming")):
        _, body = post(base_url, "/voice", call(sid), token)
        # The match round's score starts when the answer ends, before its transcript (#18).
        speculative = sid == "CA_ID_2"
        body = answer(body, call(sid), 1, "home", uid, 3.0, sample, audio=answer_audio(sample, (2.0, True), (1.0, False)) if speculative else None,
                      stored=3.7 if speculative else None)
        require(body == f'<Response><Say voice="Polly.Joanna">{said}</Say>{connect}', f"{outcome} Home level: {body}")
        require(identity(sid)["outcome"] == outcome and identity(sid)["enforcement"] == enforcement, f"identity: {identity(sid)}")
    require(d1_local(f"SELECT challenges_completed AS n FROM users WHERE id = {uid}")[0]["n"] == 3, "completed challenges not counted")

    # Teach once, then go terse. A wrong phrase, a repeat, too little speech, then stop.
    _, body = post(base_url, "/voice", call("CA_ID_4"), token)
    require(f"Dogwalk, for {handle}. Your sentence.</Say>" in body and "someone else" not in body, f"terse greeting: {body}")
    _, _, phrase = round_of(body, "CA_ID_4", 1, "home", uid)
    body = say("CA_ID_4", 1, "home", uid, body, phrase.rstrip(".").rsplit(" ", 1)[0] + " cow")
    require(re.match(rf'<Response><Stop><Stream name="identity-round-01"/></Stop>{received_cue}<Say[^>]*>That wasn\'t the sentence\. Here\'s another\.', body)
            and "/voice/identity/result" not in body, f"identity wrong phrase: {body}")
    body = say("CA_ID_4", 2, "home", uid, body, "say that again")
    require(body.startswith('<Response><Stop><Stream name="identity-round-02"/></Stop><Say voice="Polly.Joanna">Again.</Say>'), f"identity repeat: {body}")
    body = answer(body, call("CA_ID_4"), 2, "home", uid, 0.4, 6000, take=1)
    require("Not enough to judge. Here's another." in body and "matched" not in body, f"identity not enough speech: {body}")
    body = say("CA_ID_4", 3, "home", uid, body, "stop")
    require(body == f'<Response><Stop><Stream name="identity-round-03"/></Stop><Say voice="Polly.Joanna">Skipping the voice check.</Say>{connect}',
            f"identity stop: {body}")
    require(identity("CA_ID_4")["outcome"] == "stopped", f"identity: {identity('CA_ID_4')}")
    require(d1_local(f"SELECT challenges_completed AS n FROM users WHERE id = {uid}")[0]["n"] == 3, "an unscored check counted")

    # Someone else, who names another User's Handle: three challenges against that User, never admitted.
    _, body = post(base_url, "/voice", call("CA_ID_5"), token)
    body = say("CA_ID_5", 1, "home", uid, body, "I'm someone else")
    require(body.startswith('<Response><Stop><Stream name="identity-round-01"/></Stop>') and 'action="/voice/identity/who?attempt=1"' in body
            and "Who's calling?" in body, f"someone else: {body}")
    _, body = post(base_url, "/voice/identity/who?attempt=1", {**call("CA_ID_5"), "SpeechResult": f"it's {ROAMING_HANDLE}"}, token)
    require(f"Checking {ROAMING_HANDLE}. 3 sentences." in body, f"named Handle: {body}")
    body = answer(body, call("CA_ID_5"), 1, "roaming", vid, 3.0, 8000)
    require("Strong voice match.</Say>" in body and "<Hangup/>" not in body, f"roaming round 1: {body}")
    body = answer(body, call("CA_ID_5"), 2, "roaming", vid, 3.0, 6000)
    require("Voice matched.</Say>" in body, f"roaming round 2: {body}")
    body = answer(body, call("CA_ID_5"), 3, "roaming", vid, 3.0, 3000)
    require(body == '<Response><Say voice="Polly.Joanna">Voice not matched. Calling as yourself from another phone is coming soon. '
            'Until then, call from your home phone. Goodbye.</Say><Hangup/></Response>', f"roaming end: {body}")
    require(identity("CA_ID_5") == {"path": "roaming", "user_id": vid, "outcome": "failed", "enforcement": "deny", "judged": 3, "passed": 2,
                                    "shadow": True}, f"roaming identity: {identity('CA_ID_5')}")
    require(d1_local("SELECT user_id FROM voice_calls WHERE call_sid = 'CA_ID_5'")[0]["user_id"] == uid, "a roaming claim was admitted")
    # A Handle said at the greeting starts its challenges at once; stop ends the call.
    _, body = post(base_url, "/voice", call("CA_ID_6"), token)
    body = say("CA_ID_6", 1, "home", uid, body, f"this is {ROAMING_HANDLE}")
    require(f"Checking {ROAMING_HANDLE}." in body, f"Handle at the greeting: {body}")
    body = say("CA_ID_6", 2, "roaming", vid, body, "stop")
    require(body.endswith('<Say voice="Polly.Joanna">Goodbye.</Say><Hangup/></Response>'), f"roaming stop: {body}")
    # Someone else who names no known Handle hears the question once more, then a polite end.
    post(base_url, "/voice", call("CA_ID_7"), token)
    _, body = post(base_url, "/voice/identity/who?attempt=1", {**call("CA_ID_7"), "SpeechResult": "purple monkey"}, token)
    require('action="/voice/identity/who?attempt=2"' in body, f"unknown name, first: {body}")
    _, body = post(base_url, "/voice/identity/who?attempt=2", {**call("CA_ID_7"), "SpeechResult": "purple monkey"}, token)
    require("I don't know that name." in body and body.endswith("<Hangup/></Response>"), f"unknown name, second: {body}")
    # An invite or donating from the greeting.
    _, body = post(base_url, "/voice", call("CA_ID_8"), token)
    body = say("CA_ID_8", 1, "home", uid, body, "I have an invite")
    require('action="/voice/claim?attempts=1"' in body and "Say your three invite words." in body, f"invite from the greeting: {body}")
    _, body = post(base_url, "/voice", call("CA_ID_9"), token)
    body = say("CA_ID_9", 1, "home", uid, body, LOCAL_INVITE)
    require('action="/voice/confirm?attempts=1"' in body and "I heard: ahead, almighty, apple." in body, f"Invite Code from the greeting: {body}")
    _, body = post(base_url, "/voice", call("CA_ID_10"), token)
    body = say("CA_ID_10", 1, "home", uid, body, "I want to donate")
    require(body.endswith('<Redirect method="POST">/voice/donation</Redirect></Response>'), f"donate from the greeting: {body}")

    def failing_rounds(*sids: str) -> None:
        """Make inserting a challenge round fail for these calls (none: stop failing)."""
        sql = "DROP TRIGGER IF EXISTS test_fail_rounds;"
        if sids:
            names = ", ".join(f"'{sid}'" for sid in sids)
            sql += (f"\nCREATE TRIGGER test_fail_rounds BEFORE INSERT ON challenge_rounds WHEN NEW.call_sid IN ({names})\n"
                    "BEGIN SELECT RAISE(ABORT, 'test failure'); END;")
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as handle:
            handle.write(sql)
        subprocess.run([project_wrangler(), "d1", "execute", "dogwalk", "--local", "--file", handle.name],
                       cwd=WORKER_DIR, check=True, stdout=subprocess.DEVNULL)
        os.unlink(handle.name)

    # Saying the Handle being checked replays the sentence, but only as often as a repeat request.
    _, body = post(base_url, "/voice", call("CA_ID_11"), token)
    for take in range(4):
        _, action, _ = round_of(body, "CA_ID_11", 1, "home", uid, take)
        _, body = post(base_url, action, {**call("CA_ID_11"), "SpeechResult": f"it's {handle}", "Confidence": "0.9"}, token)
        if take < 3:
            require(f"<Say voice=\"Polly.Joanna\">Your sentence.</Say>" in body, f"own Handle replay {take}: {body}")
    require("No more repeats of that one. Here's another." in body, f"own Handle past the replay limit: {body}")
    round_of(body, "CA_ID_11", 2, "home", uid)

    # A check that cannot start (here, its first round cannot be stored) connects the Home caller.
    failing_rounds("CA_ID_BROKEN", "CA_ID_REGFAIL")
    status, body = post(base_url, "/voice", call("CA_ID_BROKEN"), token)
    require(status == 200 and "identity-round" not in body and body.startswith("<Response>"), f"check start failure: {status} {body}")
    require(identity("CA_ID_BROKEN")["outcome"] == "error", f"start failure identity: {identity('CA_ID_BROKEN')}")
    # Registration is committed even when its enrollment cannot start: the Handle is still spoken.
    form = call("CA_ID_REGFAIL", REGISTER_FAIL_PHONE)
    post(base_url, "/voice", form, token)
    post(base_url, "/voice/claim?attempts=1", {**form, "SpeechResult": LOCAL_INVITE, "Confidence": "0.9"}, token)
    _, body = post(base_url, "/voice/confirm?attempts=1", {**form, "SpeechResult": "yes", "Confidence": "0.9"}, token)
    require(re.fullmatch(r'<Response><Say voice="Polly.Joanna">Registered\. Your name on Dogwalk is [a-z]+ [a-z]+\. Again: [a-z]+ [a-z]+\. '
                         r'This phone is now your home\. Goodbye\.</Say><Hangup/></Response>', body) is not None, f"registration enrollment failure: {body}")
    failing_rounds()

    # An unenrolled Home User who says someone else, then their own Handle, enrolls; never Roaming.
    _, body = post(base_url, "/voice", call("CA_ID_SELF", ENROLL_PHONE), token)
    body = say("CA_ID_SELF", 1, "enroll", eid, body, "I'm someone else", ENROLL_PHONE)
    require('action="/voice/identity/who?attempt=1"' in body, f"unenrolled someone else: {body}")
    _, body = post(base_url, "/voice/identity/who?attempt=1", {**call("CA_ID_SELF", ENROLL_PHONE), "SpeechResult": f"it's {ENROLL_HANDLE}"}, token)
    require("One step first, about a minute: 5 short sentences" in body and "<Hangup/>" not in body, f"unenrolled own Handle: {body}")
    body = say("CA_ID_SELF", 1, "enroll", eid, body, "stop", ENROLL_PHONE)
    require(body.endswith(f'Skipping enrollment. I\'ll ask again next call.</Say>{connect}'), f"unenrolled own Handle, stop: {body}")

    # A Handle named late in enrollment still gets Roaming's own round budget.
    _, body = post(base_url, "/voice", call("CA_ID_LATE", ENROLL_PHONE), token)
    for number in range(1, 8):
        body = say("CA_ID_LATE", number, "enroll", eid, body, "the purple cow jumped", ENROLL_PHONE)
        require("That wasn't the sentence." in body, f"late Handle, wrong phrase {number}: {body}")
    body = say("CA_ID_LATE", 8, "enroll", eid, body, f"this is {ROAMING_HANDLE}", ENROLL_PHONE)
    require(f"Checking {ROAMING_HANDLE}." in body and "&amp;start=8&amp;round=9&amp;" in body, f"late Handle: {body}")
    body = say("CA_ID_LATE", 9, "roaming", vid, body, "stop", ENROLL_PHONE)
    require(body.endswith('<Say voice="Polly.Joanna">Goodbye.</Say><Hangup/></Response>'), f"late Roaming round accepted: {body}")

    # A hangup mid-enrollment leaves no embedding behind once the call's terminal status arrives.
    form = call("CA_ID_HANGUP", ENROLL_PHONE)
    _, body = post(base_url, "/voice", form, token)
    body = answer(body, form, 1, "enroll", eid, 3.0, 6000)
    require(d1_local("SELECT COUNT(*) AS n FROM challenge_rounds WHERE call_sid = 'CA_ID_HANGUP' AND embedding IS NOT NULL")[0]["n"] == 1,
            "no collected enrollment answer before the hangup")
    status, _ = post(base_url, "/voice/status", {"From": ENROLL_PHONE, "CallSid": "CA_ID_HANGUP", "CallStatus": "completed", "CallDuration": "40"}, token)
    require(status == 204 and d1_local("SELECT COUNT(*) AS n FROM challenge_rounds WHERE call_sid = 'CA_ID_HANGUP'")[0]["n"] == 0,
            f"enrollment rows kept after a hangup: {status}")
    require(d1_local(f"SELECT COUNT(*) AS n FROM voice_references WHERE user_id = {eid}")[0]["n"] == 0, "an abandoned enrollment stored a reference")

    # First-call enrollment: a User without a Voice Reference repeats five phrases, hears no levels,
    # and leaves with one computed from the answers' embeddings.
    form = call("CA_ID_ENROLL", ENROLL_PHONE)
    _, body = post(base_url, "/voice", form, token)
    # The shape comes first: how many sentences, and how long.
    require(f"Dogwalk, for {ENROLL_HANDLE}. One step first, about a minute: 5 short sentences so Dogwalk can recognize your voice "
            "from any phone. I keep a voice profile, never a recording" in body, f"enrollment intro: {body}")
    for number in range(1, 7):
        # The first answer is embedded speculatively, from the end of the answer.
        body = answer(body, form, number, "enroll", eid, 0.4 if number == 2 else 3.0, 6000,
                      audio=answer_audio(6000, (2.0, True), (1.0, False)) if number == 1 else None, stored=3.7 if number == 1 else None)
        # Progress marks count collected answers, so the short second answer moves nothing: the
        # third collected answer leaves two, the fourth leaves the last one.
        mark = {4: "Two more.", 5: "Last one."}.get(number)
        if number == 2:
            require("I didn't get enough of that one. Here's another." in body, f"enrollment short answer: {body}")
        elif mark:
            require(body.startswith(f'<Response><Say voice="Polly.Joanna">{mark}</Say><Start><Stream'), f"enrollment mark {number}: {body}")
        elif number < 6:
            require(body.startswith("<Response><Start><Stream") and "match" not in body, f"enrollment round {number}: {body}")
    require(body == f'<Response><Say voice="Polly.Joanna">Thanks. Your voice is enrolled.</Say>{connect}', f"enrollment end: {body}")
    reference = d1_local(f"SELECT embedding_space, embedding FROM voice_references WHERE user_id = {eid}")
    vector = json.loads(reference[0]["embedding"]) if reference else []
    require(reference and reference[0]["embedding_space"] == "mock-speaker-space" and len(vector) == 192
            and abs(sum(v * v for v in vector) - 1) < 1e-9, f"enrolled reference: {reference}")
    require(identity("CA_ID_ENROLL")["outcome"] == "enrolled" and identity("CA_ID_ENROLL")["enrolled_now"] is True
            and identity("CA_ID_ENROLL")["answers"] == 5, f"enrollment identity: {identity('CA_ID_ENROLL')}")
    require(d1_local("SELECT COUNT(*) AS n FROM challenge_rounds WHERE call_sid = 'CA_ID_ENROLL'")[0]["n"] == 0, "enrollment rows kept")

    # Registration ends with the same enrollment; stop skips it and ends the call.
    form = call("CA_ID_REGISTER", REGISTER_PHONE)
    post(base_url, "/voice", form, token)
    post(base_url, "/voice/claim?attempts=1", {**form, "SpeechResult": LOCAL_INVITE, "Confidence": "0.9"}, token)
    _, body = post(base_url, "/voice/confirm?attempts=1", {**form, "SpeechResult": "yes", "Confidence": "0.9"}, token)
    rid = d1_local(f"SELECT id FROM users WHERE home_phone = '{REGISTER_PHONE}'")[0]["id"]
    # Before the work, the steps left and how long; then the Handle, twice.
    require(re.search(r'<Say voice="Polly\.Joanna">Registered\. Two steps left, about two minutes: your Dogwalk name, then 5 short sentences '
                      r'so Dogwalk can recognize your voice from any phone\. Your name on Dogwalk is ([a-z]+ [a-z]+)\. Again: \1\. '
                      r'This phone is now your home\. Now your voice\. I keep a voice profile', body) is not None, f"registration enrollment: {body}")
    body = say("CA_ID_REGISTER", 1, "register", rid, body, "stop", REGISTER_PHONE)
    require(body.endswith("Skipping enrollment. I'll ask again next call. Goodbye.</Say><Hangup/></Response>"), f"registration skip: {body}")

    # Registration by Invitation: how far the invitee gets is kept on the Invitation for the
    # inviter's next call (invitations.ts, inviteeNews), counting collected answers, never rounds.
    d1_local(f"DELETE FROM invitations WHERE code_words = '{INVITEE_WORDS}'; "
             f"INSERT INTO invite_codes (code_words, minted_at, expires_at, max_uses) VALUES ('{INVITEE_WORDS}', unixepoch(), unixepoch() + 86400, 1) "
             f"ON CONFLICT(code_words) DO UPDATE SET expires_at = excluded.expires_at, max_uses = 1; "
             f"INSERT INTO invitations (code_words, inviter_user_id) VALUES ('{INVITEE_WORDS}', {uid})")
    onboarding = lambda: d1_local("SELECT onboarding_call_sid, enrollment_answers, enrollment_outcome FROM invitations "
                                  f"WHERE code_words = '{INVITEE_WORDS}'")[0]
    hang_up = lambda sid: post(base_url, "/voice/status", {"From": INVITEE_PHONE, "CallSid": sid, "CallStatus": "completed", "CallDuration": "30"}, token)
    # The words read back, then a hangup before confirming: the Invitation knows which call got that far.
    form = call("CA_ID_INVITEE1", INVITEE_PHONE)
    post(base_url, "/voice", form, token)
    _, body = post(base_url, "/voice/claim?attempts=1", {**form, "SpeechResult": INVITEE_WORDS, "Confidence": "0.9"}, token)
    require("I heard" in body and onboarding() == {"onboarding_call_sid": "CA_ID_INVITEE1", "enrollment_answers": 0, "enrollment_outcome": None},
            f"invitee readback: {onboarding()}")
    hang_up("CA_ID_INVITEE1")
    # Called back and registered: two answers collected around a wrong phrase, then stop.
    form = call("CA_ID_INVITEE2", INVITEE_PHONE)
    post(base_url, "/voice", form, token)
    post(base_url, "/voice/claim?attempts=1", {**form, "SpeechResult": INVITEE_WORDS, "Confidence": "0.9"}, token)
    _, body = post(base_url, "/voice/confirm?attempts=1", {**form, "SpeechResult": "yes", "Confidence": "0.9"}, token)
    nid = d1_local(f"SELECT id FROM users WHERE home_phone = '{INVITEE_PHONE}'")[0]["id"]
    require("Registered. Two steps left" in body, f"invitee registration: {body}")
    body = answer(body, form, 1, "register", nid, 3.0, 6000)
    require(onboarding() == {"onboarding_call_sid": "CA_ID_INVITEE2", "enrollment_answers": 1, "enrollment_outcome": None},
            f"invitee first answer: {onboarding()}")
    body = say("CA_ID_INVITEE2", 2, "register", nid, body, "the purple cow jumped", INVITEE_PHONE)
    require("That wasn't the sentence." in body and onboarding()["enrollment_answers"] == 1, f"invitee wrong phrase: {onboarding()}")
    body = answer(body, form, 3, "register", nid, 3.0, 6000)
    body = say("CA_ID_INVITEE2", 4, "register", nid, body, "stop", INVITEE_PHONE)
    require(body.endswith("Skipping enrollment. I'll ask again next call. Goodbye.</Say><Hangup/></Response>")
            and onboarding() == {"onboarding_call_sid": "CA_ID_INVITEE2", "enrollment_answers": 2, "enrollment_outcome": "stopped"},
            f"invitee stop: {onboarding()} {body}")
    hang_up("CA_ID_INVITEE2")

    # Call Activity: identity.* under access, every scored round with its score, wait, and cold flag,
    # and never a transcript or phrase.
    rows = d1_local("SELECT source, event, detail FROM call_activity WHERE call_sid LIKE 'CA_ID_%' AND event LIKE 'identity.%' ORDER BY id")
    require(rows and all(row["source"] == "access" for row in rows), f"identity activity source: {rows[:2]}")
    rounds = [json.loads(row["detail"]) for row in rows if row["event"] == "identity.round"]
    scored = [r for r in rounds if r["outcome"] in ("strong_match", "match", "no_match")]
    require(len(scored) == 6 and all("cosine" in r and "speech_seconds" in r and "wait_ms" in r and isinstance(r.get("cold"), bool) for r in scored),
            f"scored identity rounds: {scored}")
    require(all("transcript" not in r and "phrase" not in r and "embedding" not in r for r in rounds), "Call Activity kept identity speech")
    spec = [r for r in rounds if r.get("speculative")]
    require(len(spec) == 2 and all(r["head_start_ms"] > 0 and r["speech_seconds"] == 3.7 for r in spec)
            and [r["outcome"] for r in spec] == ["match", "collected"], f"speculative identity rounds: {spec}")
    require({"identity.started", "identity.escape", "identity.who", "identity.result", "identity.repeat"} <= {row["event"] for row in rows},
            f"identity events: {sorted({row['event'] for row in rows})}")


def reenrollment_checks(base_url: str, token: str) -> None:
    """Re-enrollment: from an admitted call, three matched challenges against the current Voice
    Reference (enforced, though the Identity Check is in shadow mode), then five enrollment
    answers whose mean replaces it outright, only if it still matches the old one."""
    received_cue = re.escape("<Play>https://dogwalk.tools/voice/identity/received.wav</Play>")
    connect = '<Redirect method="POST">/voice</Redirect></Response>'
    user = d1_local(f"SELECT id, home_phone FROM users WHERE home_phone LIKE '{TEST_NUMBERS}' AND reaped_at IS NULL "
                    f"AND home_phone NOT IN ('{ROAMING_PHONE}', '{ENROLL_PHONE}', '{REGISTER_PHONE}', '{REGISTER_FAIL_PHONE}', '{INVITEE_PHONE}') LIMIT 1")[0]
    uid, phone = user["id"], user["home_phone"]
    # The mock embeds every answer as the uniform unit vector: NEAR still matches it (cosine 0.95),
    # FAR does not (cosine 0).
    near = json.dumps([1.0] * 96 + [0.5] * 96)
    far = json.dumps([1.0] * 96 + [-1.0] * 96)

    def call(sid: str) -> dict:
        d1_local(f"INSERT OR IGNORE INTO voice_calls (call_sid, phone_number, status, started_at, last_activity_at, user_id) "
                 f"VALUES ('{sid}', '{phone}', 'in-progress', unixepoch(), unixepoch(), {uid})")
        return {"From": phone, "CallSid": sid, "AccountSid": TEST_ACCOUNT}

    def reference() -> str | None:
        rows = d1_local(f"SELECT embedding FROM voice_references WHERE user_id = {uid}")
        return rows[0]["embedding"] if rows else None

    def set_reference(embedding: str) -> None:
        d1_local(f"INSERT OR REPLACE INTO voice_references (user_id, embedding_space, embedding, enrolled_at) "
                 f"VALUES ({uid}, 'mock-speaker-space', '{embedding}', unixepoch())")

    def round_of(body: str, sid: str, number: int, phase: str, ritual: str = "identity") -> tuple[str, str, str]:
        name = f"identity-round-{number:02d}"
        stream = re.search(rf'<Start><Stream name="{name}" url="wss://dogwalk\.tools(/voice/identity/stream/[^"]+)" track="inbound_track">', body)
        action = re.search(rf'action="(/voice/reenrollment/heard\?phase={phase}&amp;silent=\d&amp;judged=\d&amp;start=\d+&amp;ref=[0-9a-f]{{16}}'
                           rf'&amp;round={number}&amp;take=0)"', body)
        require(stream and action and "<Play>https://dogwalk.tools/voice/identity/tone.wav</Play>" in body, f"re-enrollment round {number}: {body}")
        row = d1_local(f"SELECT phrase, ritual FROM challenge_rounds WHERE call_sid = '{sid}' AND round = {number}")[0]
        require(row["ritual"] == ritual and f">{row['phrase']}</Say></Gather>" in body, f"re-enrollment round {number} ritual: {row}")
        return stream[1], action[1].replace("&amp;", "&"), row["phrase"]

    def answer(body: str, form: dict, number: int, phase: str, seconds: float, sample: int, retried: bool = False) -> str:
        """A matched answer; `retried` posts its transcript twice, as a Twilio retry would."""
        stream, action, phrase = round_of(body, form["CallSid"], number, phase, "identity" if phase == "check" else "enrollment")
        redirect = []
        def heard() -> None:
            responses = [post(base_url, action, {**form, "SpeechResult": phrase.lower().rstrip("."), "Confidence": "0.9"}, token)[1]
                         for _ in range(2 if retried else 1)]
            require(len(set(responses)) == 1, f"a retried matched phrase answered differently: {responses}")
            match = re.fullmatch(rf'<Response><Stop><Stream name="identity-round-{number:02d}"/></Stop>{received_cue}'
                                 rf'<Redirect method="POST">(/voice/reenrollment/result\?[^<]+)</Redirect></Response>', responses[0])
            require(match is not None, f"re-enrollment matched phrase: {responses[0]}")
            redirect.append(match[1].replace("&amp;", "&"))
        practice_stream(base_url, stream, token, number, steady_audio(seconds, sample), heard)
        status, result = post(base_url, redirect[0], form, token)
        require(status == 200, f"re-enrollment result {number}: {status} {result}")
        return result

    def say(body: str, form: dict, number: int, phase: str, words: str) -> str:
        _, action, _ = round_of(body, form["CallSid"], number, phase, "identity" if phase == "check" else "enrollment")
        return post(base_url, action, {**form, "SpeechResult": words, "Confidence": "0.9"}, token)[1]

    # The Voice Agent's handoff, and a User with nothing to replace.
    form = call("CA_REENROLL_NONE")
    d1_local(f"DELETE FROM voice_references WHERE user_id = {uid}; INSERT INTO call_handoffs (call_sid, phone_number, action, created_at) "
             f"VALUES ('CA_REENROLL_NONE', '{phone}', 'reenrollment', unixepoch())")
    status, body = post(base_url, "/voice/after-live", form, token)
    require(status == 200 and '<Redirect method="POST">/voice/reenrollment</Redirect>' in body, f"re-enrollment handoff: {body}")
    status, body = post(base_url, "/voice/reenrollment", form, token)
    require(status == 200 and "isn't enrolled yet" in body and body.endswith(connect) and "<Stream" not in body,
            f"re-enrollment without a Voice Reference: {body}")
    require(reference() is None, "re-enrollment created a first Voice Reference")

    # The whole ritual: three matches (a wrong phrase between them does not count), then five
    # answers, one too short; the mean replaces the old reference outright.
    set_reference(near)
    form = call("CA_REENROLL")
    _, body = post(base_url, "/voice/reenrollment", form, token)
    require("replaces your voice profile" in body and "3 sentences, and each must match" in body and "Say stop to cancel." in body,
            f"re-enrollment intro: {body}")
    body = answer(body, form, 1, "check", 3.0, 8000)
    require(body.startswith('<Response><Say voice="Polly.Joanna">Strong voice match.</Say><Start><Stream'), f"check round 1: {body}")
    body = say(body, form, 2, "check", "the purple cow jumped")
    require(re.match(rf'<Response><Stop><Stream name="identity-round-02"/></Stop>{received_cue}<Say[^>]*>That wasn\'t the sentence\.', body),
            f"check wrong phrase: {body}")
    body = answer(body, form, 3, "check", 3.0, 6000, retried=True)
    require("Voice matched.</Say>" in body and "That's you." not in body, f"check round 3: {body}")
    body = answer(body, form, 4, "check", 3.0, 6000)
    require("Voice matched. That's you. Now the new profile: 5 sentences" in body and re.search(r"&amp;start=4&amp;ref=[0-9a-f]{16}&amp;round=5&amp;", body),
            f"check passed: {body}")
    for number in range(5, 11):
        body = answer(body, form, number, "enroll", 0.4 if number == 6 else 3.0, 6000)
        if number == 6:
            require("I didn't get enough of that one. Here's another." in body, f"re-enrollment short answer: {body}")
        elif number in (8, 9):
            # The same progress marks as first-call enrollment, counting collected answers only.
            mark = "Two more." if number == 8 else "Last one."
            require(body.startswith(f'<Response><Say voice="Polly.Joanna">{mark}</Say><Start><Stream'), f"re-enrollment mark {number}: {body}")
        elif number < 10:
            require(body.startswith("<Response><Start><Stream") and "match" not in body, f"re-enrollment answer {number}: {body}")
    require(body == f'<Response><Say voice="Polly.Joanna">Thanks. Your new voice profile replaces the old one. Back to Dogwalk.</Say>{connect}',
            f"re-enrollment end: {body}")
    vector = json.loads(reference() or "[]")
    require(len(vector) == 192 and all(abs(v - 192 ** -0.5) < 1e-9 for v in vector), f"replaced reference: {vector[:3]}")
    require(d1_local("SELECT COUNT(*) AS n FROM challenge_rounds WHERE call_sid = 'CA_REENROLL'")[0]["n"] == 0, "re-enrollment rows kept")

    # One miss ends the check, whatever came before: the reference is unchanged.
    set_reference(near)
    form = call("CA_REENROLL_MISS")
    _, body = post(base_url, "/voice/reenrollment", form, token)
    body = answer(body, form, 1, "check", 3.0, 8000)
    body = answer(body, form, 2, "check", 3.0, 3000)
    require(body == f'<Response><Say voice="Polly.Joanna">Voice not matched. Your voice profile is unchanged. Back to Dogwalk.</Say>{connect}',
            f"failed check: {body}")
    require(reference() == near, "a failed check changed the reference")

    # A different voice after a passed check: the new answers do not match the old reference.
    set_reference(far)
    form = call("CA_REENROLL_SWAP")
    _, body = post(base_url, "/voice/reenrollment", form, token)
    for number in range(1, 4):
        body = answer(body, form, number, "check", 3.0, 8000)
    for number in range(4, 9):
        body = answer(body, form, number, "enroll", 3.0, 6000)
    require("Those answers didn't sound like your current profile. Your voice profile is unchanged." in body and body.endswith(connect),
            f"discontinuous re-enrollment: {body}")
    require(reference() == far, "a discontinuous re-enrollment changed the reference")

    # Stop during enrollment cancels it, keeping no collected embedding.
    set_reference(near)
    form = call("CA_REENROLL_STOP")
    _, body = post(base_url, "/voice/reenrollment", form, token)
    for number in range(1, 4):
        body = answer(body, form, number, "check", 3.0, 8000)
    body = answer(body, form, 4, "enroll", 3.0, 6000)
    body = say(body, form, 5, "enroll", "stop")
    require(body == f'<Response><Stop><Stream name="identity-round-05"/></Stop><Say voice="Polly.Joanna">Re-enrollment cancelled. '
            f'Your voice profile is unchanged. Back to Dogwalk.</Say>{connect}', f"re-enrollment stop: {body}")
    require(reference() == near and d1_local("SELECT COUNT(*) AS n FROM challenge_rounds WHERE call_sid = 'CA_REENROLL_STOP'")[0]["n"] == 0,
            "stopped re-enrollment changed the reference or kept rows")

    # Another call replaces the reference after this one's check began: this one changes nothing.
    set_reference(near)
    form = call("CA_REENROLL_RACE")
    _, body = post(base_url, "/voice/reenrollment", form, token)
    body = answer(body, form, 1, "check", 3.0, 8000)
    set_reference(json.dumps([1.0] * 192))
    _, action, phrase = round_of(body, "CA_REENROLL_RACE", 2, "check")
    _, body = post(base_url, action, {**form, "SpeechResult": phrase.lower().rstrip("."), "Confidence": "0.9"}, token)
    require(body == '<Response><Stop><Stream name="identity-round-02"/></Stop><Say voice="Polly.Joanna">Your voice profile changed during this '
            f'call, so I\'ve left it as it is now. Back to Dogwalk.</Say>{connect}', f"reference changed mid-ritual: {body}")
    require(reference() == json.dumps([1.0] * 192), "a stale ritual changed the newer reference")

    # Call Activity: under access, with every outcome and never speech or an embedding.
    rows = d1_local("SELECT source, event, detail FROM call_activity WHERE call_sid LIKE 'CA_REENROLL%' AND event LIKE 'reenrollment.%' ORDER BY id")
    require(rows and all(row["source"] == "access" for row in rows), f"re-enrollment activity source: {rows[:2]}")
    results = [json.loads(row["detail"])["outcome"] for row in rows if row["event"] == "reenrollment.result"]
    require(results == ["reenrolled", "failed", "discontinuous", "stopped", "lost_reference"], f"re-enrollment results: {results}")
    # Each call's end, as the reconnected Voice Agent's call_started reports it: outcome names only.
    notes = {row["call_sid"]: json.loads(row["returned_from"]) for row in
             d1_local("SELECT call_sid, returned_from FROM voice_calls WHERE call_sid LIKE 'CA_REENROLL%'")}
    require(notes == {
        "CA_REENROLL_NONE": {"flow": "reenrollment", "outcome": "unavailable", "reason": "no_voice_reference"},
        "CA_REENROLL": {"flow": "reenrollment", "outcome": "reenrolled"},
        "CA_REENROLL_MISS": {"flow": "reenrollment", "outcome": "failed"},
        "CA_REENROLL_SWAP": {"flow": "reenrollment", "outcome": "discontinuous"},
        "CA_REENROLL_STOP": {"flow": "reenrollment", "outcome": "stopped"},
        "CA_REENROLL_RACE": {"flow": "reenrollment", "outcome": "lost_reference"},
    }, f"re-enrollment return notes: {notes}")
    require(not any({"phrase", "transcript", "embedding"} & json.loads(row["detail"]).keys() for row in rows),
            "Call Activity kept re-enrollment speech")
    require({"reenrollment.unavailable", "reenrollment.started", "reenrollment.round", "reenrollment.checked"} <= {row["event"] for row in rows},
            f"re-enrollment events: {sorted({row['event'] for row in rows})}")
    set_reference("[0.5, 0.5]")
STRANGER_PHONE = "+15555550124"
STRANGER_PHONE_2 = "+15555550125"
STRANGER_PHONE_3 = "+15555550126"


def roaming_admission_checks(base_url: str, token: str) -> None:
    """Roaming admission (ROAMING_ADMISSION on; identity_checks ran with it off): a caller from a
    phone that is no Home names a Handle at the greeting, passes three challenges, and returns to
    /voice as that User with the Home unchanged; an unmatched voice ends the attempt; recent
    failures pause the Handle, and the calling number. The next Home Opening's notice is in
    voice_session_test."""
    received_cue = re.escape("<Play>https://dogwalk.tools/voice/identity/received.wav</Play>")
    connect = '<Redirect method="POST">/voice</Redirect></Response>'
    # Shadow mode wrote no attempts, though identity_checks ran Roaming claims.
    require(d1_local("SELECT COUNT(*) AS n FROM roaming_attempts")[0]["n"] == 0, "shadow Roaming recorded attempts")
    named = d1_local(f"SELECT id, home_phone, home_used_at FROM users WHERE handle = '{ROAMING_HANDLE}'")[0]
    vid = named["id"]
    others = [row["id"] for row in d1_local(
        f"SELECT u.id FROM users u JOIN voice_references vr ON vr.user_id = u.id WHERE u.home_phone LIKE '{TEST_NUMBERS}' "
        f"AND u.id != {vid} AND u.reaped_at IS NULL ORDER BY u.id LIMIT 3")]
    require(len(others) >= 2, f"Roaming admission needs two other enrolled Users: {others}")

    def call(sid: str, caller: str) -> dict:
        return {"From": caller, "CallSid": sid, "AccountSid": TEST_ACCOUNT}

    def identity(sid: str) -> dict | None:
        row = d1_local(f"SELECT identity FROM voice_calls WHERE call_sid = '{sid}'")
        return json.loads(row[0]["identity"]) if row and row[0]["identity"] else None

    def answer(body: str, form: dict, number: int, first_sample: int, user: int = vid) -> str:
        """A matched Roaming answer of steady audio, scored at the stream's stop."""
        name = f"identity-round-{number:02d}"
        stream = re.search(rf'<Start><Stream name="{name}" url="wss://dogwalk\.tools(/voice/identity/stream/[^"]+)"', body)
        action = re.search(rf'action="(/voice/identity/heard\?path=roaming&amp;user={user}&amp;[^"]*&amp;round={number}&amp;take=0)"', body)
        require(stream and action, f"Roaming round {number}: {body}")
        phrase = d1_local(f"SELECT phrase FROM challenge_rounds WHERE call_sid = '{form['CallSid']}' AND round = {number}")[0]["phrase"]
        redirect = []
        def heard() -> None:
            _, twiml = post(base_url, action[1].replace("&amp;", "&"), {**form, "SpeechResult": phrase.lower().rstrip("."), "Confidence": "0.9"}, token)
            match = re.fullmatch(rf'<Response><Stop><Stream name="{name}"/></Stop>{received_cue}'
                                 rf'<Redirect method="POST">(/voice/identity/result\?[^<]+)</Redirect></Response>', twiml)
            require(match is not None, f"Roaming matched phrase: {twiml}")
            redirect.append(match[1].replace("&amp;", "&"))
        practice_stream(base_url, stream[1], token, number, steady_audio(3.0, first_sample), heard)
        status, result = post(base_url, redirect[0], form, token)
        require(status == 200, f"Roaming result {number}: {status} {result}")
        return result

    def claim(form: dict, words: str) -> str:
        post(base_url, "/voice", form, token)
        return post(base_url, "/voice/claim?attempts=1", {**form, "SpeechResult": words, "Confidence": "0.9"}, token)[1]

    # The unregistered greeting asks for a Handle too.
    form = call("CA_RA_PASS", STRANGER_PHONE)
    _, body = post(base_url, "/voice", form, token)
    require("Say your two-word Dogwalk name, or your three invite words." in body, f"greeting with Roaming admission: {body}")
    # Three passed challenges admit the caller as the named User, with each level spoken.
    body = post(base_url, "/voice/claim?attempts=1", {**form, "SpeechResult": f"It's {ROAMING_HANDLE}.", "Confidence": "0.9"}, token)[1]
    require(f"Checking {ROAMING_HANDLE}. 3 sentences." in body, f"Handle at the unregistered greeting: {body}")
    body = answer(body, form, 1, 8000)
    require("Strong voice match.</Say>" in body, f"admission round 1: {body}")
    body = answer(body, form, 2, 6000)
    require("Voice matched.</Say>" in body, f"admission round 2: {body}")
    body = answer(body, form, 3, 8000)
    require(body == f'<Response><Say voice="Polly.Joanna">Strong voice match. Welcome, {ROAMING_HANDLE}.</Say>{connect}', f"admitted: {body}")
    require(identity("CA_RA_PASS") == {"path": "roaming", "user_id": vid, "outcome": "passed", "enforcement": "admit_roaming", "judged": 3,
                                       "passed": 3, "admitted": True, "shadow": False}, f"admission identity: {identity('CA_RA_PASS')}")
    attempt = d1_local("SELECT phone_number, failures, admitted_at FROM roaming_attempts WHERE call_sid = 'CA_RA_PASS'")
    require(len(attempt) == 1 and attempt[0]["phone_number"] == STRANGER_PHONE and attempt[0]["failures"] == 0 and attempt[0]["admitted_at"],
            f"admission attempt: {attempt}")
    # Back at /voice the call is the named User's, without another check; the Home is unchanged.
    status, body = post(base_url, "/voice", form, token)
    require(status == 200 and "identity-round" not in body and "Dogwalk, for" not in body and "invite words" not in body,
            f"admitted call at /voice: {body}")
    require(d1_local("SELECT user_id FROM voice_calls WHERE call_sid = 'CA_RA_PASS'")[0]["user_id"] == vid, "admitted call lost its User")
    after = d1_local(f"SELECT home_phone, home_used_at FROM users WHERE id = {vid}")[0]
    require(after == {"home_phone": named["home_phone"], "home_used_at": named["home_used_at"]}, f"Roaming changed the Home: {after}")
    via = d1_local("SELECT detail FROM call_activity WHERE call_sid = 'CA_RA_PASS' AND event = 'caller.registered' ORDER BY id DESC LIMIT 1")
    require(via and json.loads(via[0]["detail"]).get("via") == "roaming" and json.loads(via[0]["detail"]).get("user_id") == vid,
            f"admitted call's registration: {via}")

    # The first unmatched voice ends the attempt and counts one failure; the call has no User.
    form = call("CA_RA_FAIL", STRANGER_PHONE)
    body = claim(form, ROAMING_HANDLE)
    body = answer(body, form, 1, 3000)
    require(body == f'<Response><Say voice="Polly.Joanna">Voice not matched. I can\'t let you in as {ROAMING_HANDLE} this time. Goodbye.</Say>'
            "<Hangup/></Response>", f"failed attempt: {body}")
    require(identity("CA_RA_FAIL") == {"path": "roaming", "user_id": vid, "outcome": "failed", "enforcement": "deny", "judged": 1, "passed": 0,
                                       "shadow": False}, f"failed identity: {identity('CA_RA_FAIL')}")
    require(d1_local("SELECT failures FROM roaming_attempts WHERE call_sid = 'CA_RA_FAIL'")[0]["failures"] == 1, "failure not counted")
    require(d1_local("SELECT user_id FROM voice_calls WHERE call_sid = 'CA_RA_FAIL'")[0]["user_id"] is None, "a failed attempt got a User")
    # Stop is no failure.
    form = call("CA_RA_STOP", STRANGER_PHONE)
    body = claim(form, ROAMING_HANDLE)
    action = re.search(r'action="(/voice/identity/heard\?[^"]+)"', body)[1]
    _, body = post(base_url, action.replace("&amp;", "&"), {**form, "SpeechResult": "stop", "Confidence": "0.9"}, token)
    require(body.endswith("Goodbye.</Say><Hangup/></Response>"), f"Roaming stop: {body}")
    require(d1_local("SELECT failures FROM roaming_attempts WHERE call_sid = 'CA_RA_STOP'")[0]["failures"] == 0, "stop counted as a failure")

    # One attempt per Handle at a time: a second call waits until the first concludes or ends.
    hold = call("CA_RA_HOLD", STRANGER_PHONE)
    require(f"Checking {ROAMING_HANDLE}." in claim(hold, ROAMING_HANDLE), "first concurrent attempt")
    form = call("CA_RA_BUSY", STRANGER_PHONE_2)
    require(claim(form, ROAMING_HANDLE) == f'<Response><Say voice="Polly.Joanna">Someone is already checking as {ROAMING_HANDLE}. '
            "Try again in a few minutes. Goodbye.</Say><Hangup/></Response>", "concurrent attempt")
    require(identity("CA_RA_BUSY")["outcome"] == "busy", f"busy identity: {identity('CA_RA_BUSY')}")
    post(base_url, "/voice/status", {"From": STRANGER_PHONE, "CallSid": "CA_RA_HOLD", "CallStatus": "completed", "CallDuration": "20"}, token)
    form = call("CA_RA_AFTER", STRANGER_PHONE_2)
    require(f"Checking {ROAMING_HANDLE}." in claim(form, ROAMING_HANDLE), "Handle held after the first call ended")
    post(base_url, "/voice/status", {"From": STRANGER_PHONE_2, "CallSid": "CA_RA_AFTER", "CallStatus": "completed", "CallDuration": "20"}, token)

    # An attempt admission did not record (the flag turned on mid-call) stays shadow, whatever its levels.
    shadow_handle = d1_local(f"SELECT handle FROM users WHERE id = {others[1]}")[0]["handle"]
    form = call("CA_RA_SHADOW", STRANGER_PHONE)
    body = claim(form, shadow_handle)
    d1_local("DELETE FROM roaming_attempts WHERE call_sid = 'CA_RA_SHADOW'")
    for number in (1, 2, 3):
        body = answer(body, form, number, 8000, user=others[1])
    require("Calling as yourself from another phone is coming soon." in body and body.endswith("<Hangup/></Response>"), f"unrecorded attempt: {body}")
    require(identity("CA_RA_SHADOW")["shadow"] is True and "admitted" not in identity("CA_RA_SHADOW"), f"{identity('CA_RA_SHADOW')}")
    require(d1_local("SELECT user_id FROM voice_calls WHERE call_sid = 'CA_RA_SHADOW'")[0]["user_id"] is None, "an unrecorded attempt was admitted")

    # An attempt that outlives its hold on the Handle ends without a verdict, adding no failure.
    form = call("CA_RA_SLOW", STRANGER_PHONE)
    body = claim(form, shadow_handle)
    d1_local("UPDATE roaming_attempts SET started_at = unixepoch() - 700 WHERE call_sid = 'CA_RA_SLOW'")
    body = answer(body, form, 1, 3000, user=others[1])
    require(body == '<Response><Say voice="Polly.Joanna">That took too long. Call again to start over. Goodbye.</Say><Hangup/></Response>',
            f"expired attempt: {body}")
    require(identity("CA_RA_SLOW")["outcome"] == "expired"
            and d1_local("SELECT failures FROM roaming_attempts WHERE call_sid = 'CA_RA_SLOW'")[0]["failures"] == 0, "expired attempt counted")

    # Failures by other calls that land after an attempt began pause its admission (one statement decides).
    form = call("CA_RA_LATE", STRANGER_PHONE)
    body = claim(form, shadow_handle)
    body = answer(body, form, 1, 8000, user=others[1])
    body = answer(body, form, 2, 8000, user=others[1])
    d1_local(f"INSERT INTO roaming_attempts (call_sid, user_id, phone_number, started_at, failures) VALUES "
             + ", ".join(f"('CA_RA_L{i}', {others[1]}, '{STRANGER_PHONE_2}', unixepoch() - 30, 1)" for i in range(3)))
    body = answer(body, form, 3, 8000, user=others[1])
    require(body == f'<Response><Say voice="Polly.Joanna">Strong voice match. Calls as {shadow_handle} from other phones are paused right now. '
            "Goodbye.</Say><Hangup/></Response>", f"pause during an attempt: {body}")
    require(identity("CA_RA_LATE")["outcome"] == "paused" and d1_local("SELECT user_id FROM voice_calls WHERE call_sid = 'CA_RA_LATE'")[0]["user_id"] is None
            and d1_local("SELECT admitted_at FROM roaming_attempts WHERE call_sid = 'CA_RA_LATE'")[0]["admitted_at"] is None, "admitted while paused")

    # A name that is no Handle and no Invite Code: the retry still offers both.
    form = call("CA_RA_UNKNOWN", STRANGER_PHONE)
    body = claim(form, "purple monkey")
    require("I didn't recognize a Dogwalk name or invite words. Say your two-word Dogwalk name, or your invite words." in body,
            f"retry with Roaming admission: {body}")

    # Three recent failed attempts as a Handle pause it from every number; older ones have aged out.
    d1_local(f"INSERT INTO roaming_attempts (call_sid, user_id, phone_number, started_at, failures) VALUES "
             f"('CA_RA_OLD', {vid}, '{STRANGER_PHONE_2}', unixepoch() - 2 * 86400, 1), "
             f"('CA_RA_F2', {vid}, '{STRANGER_PHONE_2}', unixepoch() - 60, 1)")
    form = call("CA_RA_OPEN", STRANGER_PHONE_2)
    require(f"Checking {ROAMING_HANDLE}." in claim(form, ROAMING_HANDLE), "Roaming paused by failures outside the window")
    d1_local(f"INSERT INTO roaming_attempts (call_sid, user_id, phone_number, started_at, failures) VALUES "
             f"('CA_RA_F3', {vid}, '{STRANGER_PHONE_2}', unixepoch() - 30, 1)")
    form = call("CA_RA_PAUSED", STRANGER_PHONE_3)
    body = claim(form, ROAMING_HANDLE)
    require(body == f'<Response><Say voice="Polly.Joanna">Too many failed tries as {ROAMING_HANDLE} from other phones today. Call from its home phone, '
            "or try again tomorrow. Goodbye.</Say><Hangup/></Response>", f"paused Handle: {body}")
    require(identity("CA_RA_PAUSED")["outcome"] == "paused" and identity("CA_RA_PAUSED")["paused"] == "handle", f"{identity('CA_RA_PAUSED')}")
    require(not d1_local("SELECT 1 FROM roaming_attempts WHERE call_sid = 'CA_RA_PAUSED'"), "a paused claim started an attempt")
    # Five recent failures from one number, across Handles, pause it for every Handle.
    rows = ", ".join(f"('CA_RA_N{i}', {others[i % 2]}, '{STRANGER_PHONE_3}', unixepoch() - 60, 1)" for i in range(4))
    d1_local(f"INSERT INTO roaming_attempts (call_sid, user_id, phone_number, started_at, failures) VALUES {rows}")
    target = d1_local(f"SELECT handle FROM users WHERE id = {others[0]}")[0]["handle"]
    form = call("CA_RA_NUMBER", STRANGER_PHONE_3)
    require(f"Checking {target}." in claim(form, target), "four failures paused the number")
    d1_local(f"INSERT INTO roaming_attempts (call_sid, user_id, phone_number, started_at, failures) VALUES "
             f"('CA_RA_N4', {others[1]}, '{STRANGER_PHONE_3}', unixepoch() - 60, 1)")
    form = call("CA_RA_NUMBER2", STRANGER_PHONE_3)
    require(claim(form, target) == '<Response><Say voice="Polly.Joanna">Too many failed voice checks from this phone today. Try again tomorrow. '
            "Goodbye.</Say><Hangup/></Response>", "paused number")


def home_name_checks(base_url: str, token: str) -> None:
    """The Home check's name path (wrangler.toml's HOME_NAME_WINDOW_HOURS; the earlier runs had it
    off): within the window of the User's last Voice Call, from any phone, a Home call asks for
    "this is <Handle>", scores the short answer without speaking a level, and connects; thin speech never
    blocks; anything but the Handle falls back to one sentence; past the window, a sentence."""
    received_cue = re.escape("<Play>https://dogwalk.tools/voice/identity/received.wav</Play>")
    connect = '<Redirect method="POST">/voice</Redirect></Response>'
    home = d1_local("SELECT u.id, u.handle, u.home_phone FROM voice_calls c JOIN users u ON u.id = c.user_id WHERE c.call_sid = 'CA_ID_1'")[0]
    uid, handle, phone = home["id"], home["handle"], home["home_phone"]
    vid = d1_local(f"SELECT id FROM users WHERE handle = '{ROAMING_HANDLE}'")[0]["id"]
    completed = d1_local(f"SELECT challenges_completed AS n FROM users WHERE id = {uid}")[0]["n"]
    require(completed >= 3, f"the name checks expect a taught User: {completed}")

    def call(sid: str, caller: str = phone) -> dict:
        return {"From": caller, "CallSid": sid, "AccountSid": TEST_ACCOUNT}

    def identity(sid: str) -> dict | None:
        row = d1_local(f"SELECT identity FROM voice_calls WHERE call_sid = '{sid}'")
        return json.loads(row[0]["identity"]) if row and row[0]["identity"] else None

    def round_of(body: str, number: int, user: int, name: bool) -> tuple[str, str]:
        stream = re.search(rf'<Start><Stream name="identity-round-{number:02d}" url="wss://dogwalk\.tools(/voice/identity/stream/[^"]+)"', body)
        action = re.search(rf'action="(/voice/identity/heard\?path=home&amp;user={user}&amp;[^"]*&amp;start=0{"&amp;mode=name" if name else ""}'
                           rf'&amp;round={number}&amp;take=0)"', body)
        require(stream and action, f"name check round {number} (name {name}): {body}")
        return stream[1], action[1].replace("&amp;", "&")

    def name_greeting(body: str, who: str) -> None:
        words = ",".join(who.split())
        require(f'<Say voice="Polly.Joanna">Dogwalk, for {who}.</Say><Start>' in body and f'hints="stop,{words}"' in body
                and f'<Say voice="Polly.Joanna">Say: this is {who}.</Say></Gather>' in body and "sentence" not in body, f"name greeting: {body}")

    def answer(body: str, form: dict, number: int, user: int, name: bool, words: str, seconds: float, sample: int,
               audio: bytes | None = None, spec_error: str | None = None) -> str:
        """A matched answer (the Handle or the round's phrase) of steady audio, scored at the stop, or
        `audio` whose end first stores a speculative score with `spec_error`."""
        stream, action = round_of(body, number, user, name)
        redirect = []
        def heard() -> None:
            for _ in range(40 if spec_error else 0):
                rows = d1_local(f"SELECT spec_error FROM challenge_rounds WHERE call_sid = '{form['CallSid']}' AND round = {number}")
                if rows and rows[0]["spec_error"] == spec_error:
                    break
                time.sleep(0.25)
            else:
                require(not spec_error, f"no speculative {spec_error}")
            _, twiml = post(base_url, action, {**form, "SpeechResult": words, "Confidence": "0.9"}, token)
            match = re.fullmatch(rf'<Response><Stop><Stream name="identity-round-{number:02d}"/></Stop>{received_cue}'
                                 rf'<Redirect method="POST">(/voice/identity/result\?[^<]+)</Redirect></Response>', twiml)
            require(match is not None, f"name check matched answer: {twiml}")
            redirect.append(match[1].replace("&amp;", "&"))
        practice_stream(base_url, stream, token, number, audio or steady_audio(seconds, sample), heard)
        return post(base_url, redirect[0], form, token)[1]

    def say(sid: str, body: str, number: int, user: int, name: bool, words: str, caller: str = phone) -> str:
        _, action = round_of(body, number, user, name)
        return post(base_url, action, {**call(sid, caller), "SpeechResult": words, "Confidence": "0.9"}, token)[1]

    # Within the window: the Handle, a prompt to say "this is" it, a received cue, and the connection, with no level.
    _, body = post(base_url, "/voice", call("CA_HN_1"), token)
    name_greeting(body, handle)
    body = answer(body, call("CA_HN_1"), 1, uid, True, f"this is {handle}", 3.0, 8000)
    require(body == f"<Response>{connect}", f"name path end: {body}")
    require(identity("CA_HN_1") == {"path": "home", "mode": "name", "user_id": uid, "outcome": "strong_match", "enforcement": "admit",
                                    "shadow": True}, f"name identity: {identity('CA_HN_1')}")
    require(d1_local(f"SELECT challenges_completed AS n FROM users WHERE id = {uid}")[0]["n"] == completed, "the name path counted as a challenge")
    # Thin speech never blocks: under the level's floor, or under the verifier's own, the call connects with the score recorded.
    # Variants match too ("it's", the bare Handle a caller will sometimes give).
    for sid, words, seconds in (("CA_HN_THIN", f"it's {handle}", 1.0), ("CA_HN_TINY", handle, 0.4)):
        _, body = post(base_url, "/voice", call(sid), token)
        body = answer(body, call(sid), 1, uid, True, words, seconds, 6000)
        require(body == f"<Response>{connect}", f"thin name {seconds}: {body}")
        require(identity(sid) == {"path": "home", "mode": "name", "user_id": uid, "outcome": "not_enough_speech", "enforcement": "undecided",
                                  "shadow": True}, f"thin name identity: {identity(sid)}")
    rounds = {row["call_sid"]: json.loads(row["detail"]) for row in d1_local(
        "SELECT call_sid, detail FROM call_activity WHERE call_sid IN ('CA_HN_1', 'CA_HN_THIN', 'CA_HN_TINY') AND event = 'identity.round'")}
    # The mock's cosine is its first sample through mu-law, so only its level is exact.
    require(rounds["CA_HN_1"]["mode"] == "name" and rounds["CA_HN_1"]["cosine"] >= 0.7 and rounds["CA_HN_1"]["speech_seconds"] == 3.0
            and 0.5 <= rounds["CA_HN_THIN"]["cosine"] < 0.7 and rounds["CA_HN_THIN"]["speech_seconds"] == 1.0
            and rounds["CA_HN_TINY"]["cosine"] is None and rounds["CA_HN_TINY"]["speech_seconds"] == 0.4
            and rounds["CA_HN_TINY"]["error"] == "insufficient-audio", f"name rounds: {rounds}")
    # A speculative refusal for too little audio (the mock's 3.5 s floor, at the answer's end) is
    # never used: the whole stream, which is longer, is scored instead.
    _, body = post(base_url, "/voice", call("CA_HN_SPEC"), token)
    body = answer(body, call("CA_HN_SPEC"), 1, uid, True, f"this's {handle}", 0, 0, audio=answer_audio(7200, (1.2, True), (2.0, False)),
                  spec_error="insufficient-audio")
    require(body == f"<Response>{connect}" and identity("CA_HN_SPEC")["outcome"] == "strong_match", f"speculative refusal: {identity('CA_HN_SPEC')}")
    spec = json.loads(d1_local("SELECT detail FROM call_activity WHERE call_sid = 'CA_HN_SPEC' AND event = 'identity.round'")[0]["detail"])
    require(spec["speculative"] is False and spec["speech_seconds"] == 4.2 and "error" not in spec, f"speculative refusal round: {spec}")

    # A wrong name falls back to one sentence, whose level is spoken as past the window.
    _, body = post(base_url, "/voice", call("CA_HN_WRONG"), token)
    body = say("CA_HN_WRONG", body, 1, uid, True, "purple monkey")
    require(re.match(rf'<Response><Stop><Stream name="identity-round-01"/></Stop>{received_cue}'
                     r'<Say voice="Polly\.Joanna">I didn\'t catch your name\. Your sentence\.</Say><Start>', body), f"wrong name: {body}")
    phrase = d1_local("SELECT phrase FROM challenge_rounds WHERE call_sid = 'CA_HN_WRONG' AND round = 2")[0]["phrase"]
    require(f">{phrase}</Say></Gather>" in body and phrase != handle, f"fallback sentence: {body}")
    body = answer(body, call("CA_HN_WRONG"), 2, uid, False, phrase.lower().rstrip("."), 3.0, 8000)
    require(body == f'<Response><Say voice="Polly.Joanna">Strong voice match.</Say>{connect}', f"fallback level: {body}")
    require(identity("CA_HN_WRONG")["mode"] == "sentence" and identity("CA_HN_WRONG")["outcome"] == "strong_match",
            f"fallback identity: {identity('CA_HN_WRONG')}")
    # Silence falls back too, and a second silence connects as before; a request to repeat asks again.
    _, body = post(base_url, "/voice", call("CA_HN_SILENT"), token)
    _, action = round_of(body, 1, uid, True)
    body = post(base_url, action, {**call("CA_HN_SILENT"), "SpeechResult": "say that again"}, token)[1]
    require('<Say voice="Polly.Joanna">Again.</Say>' in body and f'<Say voice="Polly.Joanna">Say: this is {handle}.</Say></Gather>' in body
            and "&amp;mode=name&amp;round=1&amp;take=1" in body, f"name repeat: {body}")
    action = re.search(r'action="(/voice/identity/heard\?[^"]+take=1)"', body)[1].replace("&amp;", "&")
    body = post(base_url, action, {**call("CA_HN_SILENT"), "SpeechResult": ""}, token)[1]
    require("I didn't hear your name. Your sentence.</Say>" in body, f"silent name: {body}")
    body = say("CA_HN_SILENT", body, 2, uid, False, "")
    require(body.endswith(f'<Say voice="Polly.Joanna">I didn\'t hear anything.</Say>{connect}') and identity("CA_HN_SILENT")["outcome"] == "silent",
            f"silent twice: {body}")
    # The escapes still apply to the name round: someone else, and another User's Handle.
    _, body = post(base_url, "/voice", call("CA_HN_ELSE"), token)
    body = say("CA_HN_ELSE", body, 1, uid, True, "no, this is someone else")
    require('action="/voice/identity/who?attempt=1"' in body, f"someone else at the name: {body}")
    _, body = post(base_url, "/voice", call("CA_HN_OTHER"), token)
    body = say("CA_HN_OTHER", body, 1, uid, True, f"this is {ROAMING_HANDLE}")
    # Roaming as that Handle starts (or, after roaming_admission_checks' failures, says it is paused).
    require(ROAMING_HANDLE in body and "sentence" not in body and "mode=name" not in body, f"another Handle at the name: {body}")

    # Any phone counts: the Roaming-admitted call (roaming_admission_checks) is this User's only earlier one.
    require(d1_local(f"SELECT call_sid FROM voice_calls WHERE user_id = {vid}") == [{"call_sid": "CA_RA_PASS"}],
            "the Roaming User has other calls")
    _, body = post(base_url, "/voice", call("CA_HN_ROAMED", ROAMING_PHONE), token)
    name_greeting(body, ROAMING_HANDLE)
    body = say("CA_HN_ROAMED", body, 1, vid, True, "stop", ROAMING_PHONE)
    require(body.endswith(f"Skipping the voice check.</Say>{connect}") and identity("CA_HN_ROAMED")["mode"] == "name", f"name stop: {body}")

    # Past the window: the sentence path, unchanged.
    d1_local(f"UPDATE voice_calls SET started_at = started_at - 172800, last_activity_at = last_activity_at - 172800, "
             f"ended_at = ended_at - 172800 WHERE user_id = {uid}")
    _, body = post(base_url, "/voice", call("CA_HN_OLD"), token)
    require(f'<Say voice="Polly.Joanna">Dogwalk, for {handle}. Your sentence.</Say>' in body and "mode=name" not in body, f"past the window: {body}")
    round_of(body, 1, uid, False)
    started = [json.loads(row["detail"]) for row in d1_local(
        "SELECT detail FROM call_activity WHERE call_sid IN ('CA_HN_1', 'CA_HN_OLD') AND event = 'identity.started' ORDER BY id")]
    require([row["mode"] for row in started] == ["name", "sentence"], f"identity.started modes: {started}")


def project_wrangler() -> str | None:
    local = WORKER_DIR / "node_modules" / ".bin" / "wrangler"
    return str(local) if local.exists() else shutil.which("wrangler")


def prepare_local_fixture(wrangler: str) -> None:
    # Test Users are those whose Home, calls, or redemption used a test number, plus any the
    # local invite admitted (a displaced Home leaves no number on the User). Deleting them
    # first, with foreign keys deferred, lets everything that referred to them go as orphans.
    sql = f"""
PRAGMA defer_foreign_keys = true;
DELETE FROM users WHERE home_phone LIKE '{TEST_NUMBERS}' OR invite_code = '{LOCAL_INVITE}'
  OR id IN (SELECT user_id FROM voice_calls WHERE phone_number LIKE '{TEST_NUMBERS}')
  OR id IN (SELECT redeemed_by_user_id FROM invitations WHERE redeemed_from LIKE '{TEST_NUMBERS}');
DELETE FROM invitations WHERE inviter_user_id NOT IN (SELECT id FROM users)
  OR redeemed_by_user_id NOT IN (SELECT id FROM users);
DELETE FROM sandbox_assignments WHERE user_id NOT IN (SELECT id FROM users);
DELETE FROM sandbox_deletions WHERE user_id NOT IN (SELECT id FROM users);
DELETE FROM voice_references WHERE user_id NOT IN (SELECT id FROM users);
DELETE FROM retired_handles WHERE user_id NOT IN (SELECT id FROM users);
DELETE FROM roaming_attempts WHERE user_id NOT IN (SELECT id FROM users) OR phone_number LIKE '{TEST_NUMBERS}';
DELETE FROM provider_call_jobs WHERE root_call_sid IN (SELECT call_sid FROM voice_calls WHERE phone_number LIKE '{TEST_NUMBERS}');
DELETE FROM call_activity WHERE call_sid IN (SELECT call_sid FROM voice_calls WHERE phone_number LIKE '{TEST_NUMBERS}');
DELETE FROM spend WHERE call_sid IN (SELECT call_sid FROM voice_calls WHERE phone_number LIKE '{TEST_NUMBERS}');
DELETE FROM call_handoffs WHERE phone_number LIKE '{TEST_NUMBERS}';
DELETE FROM voice_calls WHERE phone_number LIKE '{TEST_NUMBERS}' OR user_id NOT IN (SELECT id FROM users);
DELETE FROM claim_attempt WHERE phone_number LIKE '{TEST_NUMBERS}';
DELETE FROM invite_codes WHERE max_uses = 1
  AND code_words NOT IN (SELECT invite_code FROM users)
  AND code_words NOT IN (SELECT code_words FROM invitations);
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
    # schema.sql only creates missing tables, so a local D1 from before OpenCode's native API
    # still has call_activity's 'acp' CHECK; the migration rebuilds it, once.
    table = json.loads(subprocess.run(
        [wrangler, "d1", "execute", "dogwalk", "--local", "--json", "--command",
         "SELECT sql FROM sqlite_master WHERE name = 'call_activity'"],
        cwd=WORKER_DIR, check=True, capture_output=True, text=True,
    ).stdout)[0]["results"]
    if "'acp'" in table[0]["sql"]:
        subprocess.run(
            [wrangler, "d1", "execute", "dogwalk", "--local", "--file", "migrations/2026-11-17-agent-activity.sql"],
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

    # A private dev registry, shared by both Workers this run starts, so no other worktree's
    # wrangler dev binds to this speaker mock.
    registry = tempfile.mkdtemp(prefix="dogwalk-wrangler-registry-")
    # Pinned to a dry run and shadow Roaming whatever wrangler.toml deploys, so the reaping checks
    # start without deleting and identity_checks see Roaming that admits nobody. The name path
    # stays off until its own checks, so earlier Home calls all hear a sentence.
    process = start_worker(wrangler, registry, "SANDBOX_REAP_MODE:dry-run", "ROAMING_ADMISSION:off", "HOME_NAME_WINDOW_HOURS:0")
    try:
        if not wait_port("127.0.0.1", WORKER_PORT, timeout=20):
            print("wrangler dev did not start", file=sys.stderr)
            return 1
        result = run_tests(
            DEFAULT_LOCAL, "local Worker", LOCAL_TOKEN, LOCAL_INVITE, mock_daytona=True
        )
        if result:
            return result
        # Sandbox reaping: the dry run first, then delete mode, which needs a restart.
        reap_users = reap_dry_run_checks(DEFAULT_LOCAL)
        stop_worker(process)
        process = start_worker(wrangler, registry, "SANDBOX_REAP_MODE:delete", "ROAMING_ADMISSION:off", "HOME_NAME_WINDOW_HOURS:0")
        if not wait_port("127.0.0.1", WORKER_PORT, timeout=20):
            print("wrangler dev did not restart", file=sys.stderr)
            return 1
        reap_delete_checks(DEFAULT_LOCAL, LOCAL_TOKEN, reap_users)
        print("Sandbox reaping checks passed (dry run, delete, 404, leftover deletion, recent, live, and unlinked callers, call while deletion pending, backoff and stuck, diagnostics race)")
        # Roaming admission is configuration: turning it on needs a restart too. This run keeps
        # wrangler.toml's name window, which the Roaming checks never reach (no Home calls).
        stop_worker(process)
        process = start_worker(wrangler, registry, "ROAMING_ADMISSION:on")
        if not wait_port("127.0.0.1", WORKER_PORT, timeout=20):
            print("wrangler dev did not restart", file=sys.stderr)
            return 1
        roaming_admission_checks(DEFAULT_LOCAL, LOCAL_TOKEN)
        print("Roaming admission checks passed (greeting Handle, admission with the Home unchanged, failure, stop, Handle and number pauses)")
        home_name_checks(DEFAULT_LOCAL, LOCAL_TOKEN)
        print("Home name path checks passed (within the window, thin speech, wrong name, silence, escapes, a Roaming-admitted call, past the window)")
        return 0
    finally:
        stop_worker(process)
        daytona.shutdown()
        daytona.server_close()
        shutil.rmtree(registry, ignore_errors=True)


def start_worker(wrangler: str, registry: str, *overrides: str) -> subprocess.Popen:
    print("starting wrangler dev")
    extra = [arg for override in overrides for arg in ("--var", override)]
    return subprocess.Popen(
        [
            wrangler,
            "dev",
            # The speaker mock answers the SPEAKER Service Binding inside this one process.
            "-c",
            "wrangler.toml",
            "-c",
            "speaker_mock.toml",
            "--port",
            str(WORKER_PORT),
            "--inspector-port",
            str(INSPECTOR_PORT),
            "--ip",
            "127.0.0.1",
            "--var",
            "DEPLOYMENT_ENVIRONMENT:local-test",
            "--var",
            f"TWILIO_AUTH_TOKEN:{LOCAL_TOKEN}",
            "--var",
            f"TWILIO_API_BASE:http://127.0.0.1:{DAYTONA_MOCK_PORT}/twilio",
            "--var",
            "DAYTONA_API_KEY:local-daytona-token",
            "--var",
            f"DOGWALK_IDENTITY_SECRET:{LOCAL_IDENTITY_SECRET}",
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
            "--test-scheduled",
            *extra,
        ],
        cwd=WORKER_DIR,
        env={**os.environ, "WRANGLER_REGISTRY_PATH": registry},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_worker(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=10)
    # Wait for the port to be released before a restart binds it again.
    end = time.time() + 10
    while time.time() < end and wait_port("127.0.0.1", WORKER_PORT, timeout=0.3):
        time.sleep(0.2)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
