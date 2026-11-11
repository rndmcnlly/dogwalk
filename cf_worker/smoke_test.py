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
    # A provider state to report instead of the normal creating-then-started sequence.
    force_state: str | None = None
    start_count = 0
    preview_reads = 0
    # Toolbox commands seen, and optional handlers that answer some of them.
    executed: list[str] = []
    execute_hooks: list = []
    # Optional handlers for other GETs (toolbox file reads): path -> (status, body, content type).
    get_hooks: list = []
    # Paths of every DELETE, answered or not.
    deleted: list[str] = []

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
        DaytonaMock.deleted.append(self.path)
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
        return (f"INSERT INTO call_activity (call_sid, ts, source, direction, event, detail) VALUES ('{call_sid}', 1, 'acp', "
                f"'internal', '{event}', json_object('acp_session_id', 'ses_{span}', 'started_ms', 1000, 'ts_ms', 1000, "
                f"'trace_id', '{turn_trace}', 'span_id', '{span}'{extra}));")
    subprocess.run([project_wrangler(), "d1", "execute", "dogwalk", "--local", "--command",
        f"INSERT OR REPLACE INTO voice_calls (call_sid, phone_number, status, started_at, last_activity_at, ended_at) VALUES "
        f"('CA_XCALL_A', '{TEST_PHONE}', 'completed', 1, 2, 2), ('CA_XCALL_B', '{TEST_PHONE}', 'completed', 3, 4, 4);"
        + "".join(turn_row("CA_XCALL_A", "acp.turn.started", span, "") for span in turn_spans)
        + "".join(turn_row("CA_XCALL_B", "acp.turn.stopped", span, ", 'ended_ms', 3500, 'stop_reason', 'end_turn'")
                  for span in turn_spans)],
        cwd=WORKER_DIR, check=True, stdout=subprocess.DEVNULL)
    status, body = get(base_url, "/admin/api/otlp/v1/traces?call_sid=CA_XCALL_A", operator_headers)
    spans = [s for rs in json.loads(body)["resourceSpans"] for ss in rs["scopeSpans"] for s in ss["spans"]]
    completed = {s["spanId"] for s in spans if s["name"] == "acp.prompt_turn" and s["traceId"] == turn_trace}
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
    print("33 core checks plus operator/provider timing, Voice Donation, and Challenge Practice checks passed" if mock_daytona else "21 core checks plus operator timing checks passed")
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
# Sandbox reaping fixtures: name -> (Home, provider ID or None for a fresh mock Sandbox, calls).
# A call is (started days ago, ended days ago or None while in progress).
REAP_USERS = {
    "idle": ("+15555550105", None, [(40, 40)]),
    "gone": ("+15555550106", "mock-reap-gone", []),
    "recent": ("+15555550107", "mock-reap-recent", [(45, 45), (2, 2)]),
    "live": ("+15555550108", "mock-reap-live", [(45, 45), (0.05, None)]),
}


def seed_reap_fixture() -> dict[str, int]:
    """Users whose Sandboxes were assigned 60 days ago, with the calls REAP_USERS lists."""
    DaytonaMock.generation += 1
    DaytonaMock.exists = True
    DaytonaMock.detail_reads = 0
    statements = []
    for name, (phone, provider, calls) in REAP_USERS.items():
        provider = provider or f"mock-sandbox-{DaytonaMock.generation}"
        user = f"(SELECT id FROM users WHERE identity_hash = 'reap-{name}')"
        statements.append(
            f"INSERT INTO users (identity_hash, invite_code, registered_at, home_phone, home_used_at) "
            f"VALUES ('reap-{name}', '{LOCAL_INVITE}', unixepoch() - {60 * DAY}, '{phone}', unixepoch() - {40 * DAY})")
        statements.append(
            f"INSERT INTO sandbox_assignments (user_id, provider_id, state, provisioning_started_at, created_at) "
            f"VALUES ({user}, '{provider}', 'stopped', unixepoch() - {60 * DAY}, unixepoch() - {60 * DAY})")
        for index, (started, ended) in enumerate(calls):
            end = "NULL" if ended is None else f"unixepoch() - {int(ended * DAY)}"
            statements.append(
                f"INSERT INTO voice_calls (call_sid, phone_number, status, started_at, last_activity_at, ended_at, user_id) "
                f"VALUES ('CA_REAP_{name.upper()}_{index}', '{phone}', '{'in-progress' if ended is None else 'completed'}', "
                f"unixepoch() - {int(started * DAY)}, unixepoch() - {int(started * DAY)}, {end}, {user})")
    d1_local(";\n".join(statements))
    rows = d1_local("SELECT id, identity_hash FROM users WHERE identity_hash LIKE 'reap-%'")
    return {row["identity_hash"].removeprefix("reap-"): row["id"] for row in rows}


def reap_assignments() -> dict[int, dict]:
    rows = d1_local("""SELECT s.user_id, s.provider_id, s.state FROM sandbox_assignments s
        JOIN users u ON u.id = s.user_id WHERE u.identity_hash LIKE 'reap-%'""")
    return {row["user_id"]: row for row in rows}


def run_reap_pass(base_url: str, users: dict[str, int], event: str, expected: int) -> list[dict]:
    """Trigger the daily pass and return the fixture Users' new `event` audit rows."""
    mark = d1_local("SELECT COALESCE(MAX(id), 0) AS id FROM audit_log")[0]["id"]
    status, _ = get(base_url, "/__scheduled?cron=" + urllib.parse.quote_plus(REAP_CRON))
    require(status == 200, f"scheduled trigger failed: {status}")
    ids = set(users.values())
    deadline = time.time() + 10
    while True:
        rows = d1_local(f"SELECT event, detail FROM audit_log WHERE id > {mark} AND event LIKE 'sandbox.reap%'")
        mine = [dict(row, detail=json.loads(row["detail"])) for row in rows]
        mine = [row for row in mine if row["detail"].get("user_id") in ids]
        if len(mine) >= expected or time.time() > deadline:
            break
        time.sleep(0.2)
    time.sleep(0.5)
    require(all(row["event"] == event for row in mine), f"unexpected reaping events: {mine}")
    return mine


def reap_dry_run_checks(base_url: str) -> dict[str, int]:
    """The default mode only audits candidates: no provider call, no assignment change."""
    users = seed_reap_fixture()
    before = reap_assignments()
    DaytonaMock.deleted.clear()
    rows = run_reap_pass(base_url, users, "sandbox.reap.candidate", 2)
    candidates = {row["detail"]["user_id"] for row in rows}
    require(candidates == {users["idle"], users["gone"]}, f"dry-run candidates: {rows}")
    require(all(row["detail"]["mode"] == "dry-run" and row["detail"]["idle_days"] >= 39 for row in rows),
            f"dry-run candidate detail: {rows}")
    require(DaytonaMock.deleted == [], f"dry run deleted a Sandbox: {DaytonaMock.deleted}")
    require(reap_assignments() == before, "dry run changed a Sandbox Assignment")
    return users


def reap_delete_checks(base_url: str, token: str, users: dict[str, int]) -> None:
    """Delete mode reaps idle Sandboxes (a provider 404 counts as gone), spares recent and
    in-progress callers, and the reaped User's next call provisions a fresh Sandbox."""
    before = reap_assignments()
    idle_provider = before[users["idle"]]["provider_id"]
    DaytonaMock.deleted.clear()
    rows = run_reap_pass(base_url, users, "sandbox.reaped", 2)
    reaped = {row["detail"]["user_id"]: row["detail"] for row in rows}
    require(set(reaped) == {users["idle"], users["gone"]}, f"reaped: {rows}")
    require(reaped[users["idle"]]["already_gone"] is False and reaped[users["gone"]]["already_gone"] is True,
            f"404 not tolerated as already gone: {rows}")
    require(sorted(DaytonaMock.deleted) == sorted([f"/api/sandbox/{idle_provider}", "/api/sandbox/mock-reap-gone"]),
            f"provider deletions: {DaytonaMock.deleted}")
    after = reap_assignments()
    for name in ("idle", "gone"):
        require(after[users[name]]["provider_id"] is None and after[users[name]]["state"] == "reaped",
                f"{name} assignment not cleared: {after[users[name]]}")
    for name in ("recent", "live"):
        require(after[users[name]] == before[users[name]], f"{name} Sandbox was touched: {after[users[name]]}")

    created = DaytonaMock.create_count
    phone = REAP_USERS["idle"][0]
    status, body = post(base_url, "/voice", {"From": phone, "CallSid": "CA_REAP_NEXT"}, token)
    require(status == 200 and "<Dial" in body, f"reaped User's next call: {status} {body}")
    fresh = reap_assignments()[users["idle"]]
    require(DaytonaMock.create_count == created + 1 and fresh["provider_id"] == f"mock-sandbox-{DaytonaMock.generation}"
            and fresh["provider_id"] != idle_provider, f"reaped User did not get a fresh Sandbox: {fresh}")
    post(base_url, "/voice/status", {"From": phone, "CallSid": "CA_REAP_NEXT", "CallStatus": "completed"}, token)


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


def practice_stream(base_url: str, path: str, token: str, round_number: int, seconds: float, first_sample: int, heard, take: int = 0) -> None:
    """One round's caller-only stream: constant audio whose first sample tells speaker_mock.js
    what to answer, then the Gather's action (heard), then the stop event its <Stop> produces."""
    start = {"event": "start", "start": {"accountSid": TEST_ACCOUNT, "callSid": "CA_PRACTICE",
             "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
             "customParameters": {"round": str(round_number), "take": str(take)}}}
    payload = base64.b64encode(bytes([ulaw_byte(first_sample)] * 160)).decode()
    with open_stream(base_url, path, token, scheme="wss") as ws:
        ws.send(json.dumps(start))
        for frame in range(int(seconds * 50)):
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
        phrase = d1_local(f"SELECT phrase FROM practice_rounds WHERE call_sid = 'CA_PRACTICE' AND round = {number}")[0]["phrase"]
        require(f">{phrase}</Say></Gather>" in body, f"round {number} does not say its phrase: {body}")
        hints = re.search(r'hints="([^"]*)"', body)[1].split(",")
        require(hints[0] == "stop" and all(word.rstrip(".").lower() in hints for word in phrase.split()
                                           if word.lower() not in ("a", "the", "my", "your", "our", "his", "her", "me", "we", "was", "this")),
                f"round {number} hints: {hints} for {phrase}")
        return stream[1], action[1].replace("&amp;", "&")

    def scored(number: int, body: str, seconds: float, first_sample: int) -> str:
        stream, action = round_of(body, number)
        phrase = d1_local(f"SELECT phrase FROM practice_rounds WHERE call_sid = 'CA_PRACTICE' AND round = {number}")[0]["phrase"]
        redirect = []
        def heard() -> None:
            _, twiml = post(base_url, action, {**call, "SpeechResult": phrase.lower().rstrip("."), "Confidence": "0.9"}, token)
            match = re.fullmatch(rf'<Response><Stop><Stream name="practice-round-{number:02d}"/></Stop>'
                                 rf'<Redirect method="POST">(/voice/practice/result\?round={number}&amp;judged=\d+)</Redirect></Response>', twiml)
            require(match is not None, f"matched phrase: {twiml}")
            redirect.append(match[1].replace("&amp;", "&"))
        practice_stream(base_url, stream, token, number, seconds, first_sample, heard)
        status, result = post(base_url, redirect[0], call, token)
        require(status == 200, f"result {number}: {status} {result}")
        return result

    # Round 1: one wrong content word (a misheard prompt) is a wrong phrase, not scored, and the
    # stream is stopped.
    stream, action = round_of(body, 1)
    phrase = d1_local("SELECT phrase FROM practice_rounds WHERE call_sid = 'CA_PRACTICE' AND round = 1")[0]["phrase"]
    misheard = phrase.rstrip(".").rsplit(" ", 1)[0] + " cow"
    status, body = post(base_url, action, {**call, "SpeechResult": misheard, "Confidence": "0.9"}, token)
    require(body.startswith('<Response><Stop><Stream name="practice-round-01"/></Stop>') and "That wasn't the sentence. Here's another." in body
            and "/voice/practice/result" not in body, f"wrong phrase: {body}")
    require(stream_rejection(base_url, stream, token) == 409, "a skipped round accepted a stream")
    # Each level, said as words and never as a number.
    body = scored(2, body, 3.0, 8000)
    require("Strong voice match. Here's another." in body and not any(re.search(r"\d", said) for said in re.findall(r"<Say[^>]*>([^<]*)</Say>", body)), f"strong: {body}")
    # MIN_SPEECH_SECONDS itself is enough to judge.
    body = scored(3, body, 1.5, 6000)
    require("Voice matched. Here's another." in body, f"match: {body}")
    body = scored(4, body, 3.0, 3000)
    require("Voice not matched. Here's another." in body, f"no match: {body}")
    # Too little speech, by the verifier's measure and by its insufficient-audio error.
    body = scored(5, body, 1.2, 8000)
    require("Not enough to judge. Here's another." in body, f"short speech: {body}")
    body = scored(6, body, 0.3, 8000)
    require("Not enough to judge. Here's another." in body, f"insufficient audio: {body}")
    # A 35 s answer is judged on its last 30 s, the verifier's maximum (the mock refuses more).
    body = scored(7, body, 35.0, 6000)
    require("Voice matched. Here's another." in body, f"long answer: {body}")
    # "Say that again" replays the same phrase as a new take with its own stream, counting nothing;
    # the stopped take refuses a late stream.
    old_stream, action = round_of(body, 8)
    phrase = d1_local("SELECT phrase FROM practice_rounds WHERE call_sid = 'CA_PRACTICE' AND round = 8")[0]["phrase"]
    status, body = post(base_url, action, {**call, "SpeechResult": "Say that again?", "Confidence": "0.9"}, token)
    require(body.startswith('<Response><Stop><Stream name="practice-round-08"/></Stop><Say voice="Polly.Joanna">Again.</Say>')
            and f">{phrase}</Say></Gather>" in body and "Here's another" not in body, f"repeat: {body}")
    _, action = round_of(body, 8, take=1)
    require(stream_rejection(base_url, old_stream, token) == 409, "a replaced take accepted a stream")
    # A short stop returns the call to the Voice Agent and clears the rounds.
    status, body = post(base_url, action, {**call, "SpeechResult": "Stop.", "Confidence": "0.9"}, token)
    require(body.startswith('<Response><Stop><Stream name="practice-round-08-1"/></Stop>') and "Practice over. Back to Dogwalk." in body
            and body.endswith('<Redirect method="POST">/voice</Redirect></Response>'), f"stop: {body}")
    require(d1_local("SELECT COUNT(*) AS n FROM practice_rounds WHERE call_sid = 'CA_PRACTICE'")[0]["n"] == 0, "rounds kept after stop")

    # An empty transcript moves on; a second in a row ends practice. Both are logged as silent.
    status, body = post(base_url, "/voice/practice", call, token)
    _, action = round_of(body, 1)
    status, body = post(base_url, action, {**call, "SpeechResult": ""}, token)
    require("I didn't hear the sentence. Here's another." in body, f"first silence: {body}")
    _, action = round_of(body, 2)
    status, body = post(base_url, action, {**call, "SpeechResult": ""}, token)
    require("I didn't hear anything. Back to Dogwalk." in body and body.endswith('<Redirect method="POST">/voice</Redirect></Response>'),
            f"second silence: {body}")

    # A verifier error ends practice as unavailable and returns.
    status, body = post(base_url, "/voice/practice", call, token)
    body = scored(1, body, 3.0, -8000)
    require("not available right now. Back to Dogwalk." in body and body.endswith('<Redirect method="POST">/voice</Redirect></Response>'),
            f"verifier error: {body}")

    rows = d1_local("SELECT event, detail FROM call_activity WHERE call_sid = 'CA_PRACTICE' AND event LIKE 'practice.%' ORDER BY id")
    rounds = [json.loads(row["detail"]) for row in rows if row["event"] == "practice.round"]
    require([r["outcome"] for r in rounds] == ["wrong_phrase", "strong_match", "match", "no_match", "not_enough_speech",
                                               "not_enough_speech", "match", "silent", "silent", "unscored"], f"practice rounds: {rounds}")
    require(rounds[1]["speech_seconds"] == 3.0 and 0.79 < rounds[1]["cosine"] < 0.82 and isinstance(rounds[1].get("wait_ms"), int),
            f"scored round detail: {rounds[1]}")
    # Too little speech keeps the verifier's score for calibration; insufficient audio has none.
    require(rounds[4]["speech_seconds"] == 1.2 and 0.79 < rounds[4]["cosine"] < 0.82, f"short speech detail: {rounds[4]}")
    require(rounds[5]["cosine"] is None and rounds[5]["error"] == "insufficient-audio", f"insufficient audio detail: {rounds[5]}")
    require(rounds[6]["speech_seconds"] == 30.0, f"long answer was not trimmed to 30 s: {rounds[6]}")
    require(all("transcript" not in r and "phrase" not in r for r in rounds), "Call Activity kept practice speech")
    # Only scored rounds count toward the round limit: four of the first practice's eight judged.
    ended = [json.loads(row["detail"]) for row in rows if row["event"] == "practice.ended"]
    require(ended[0]["reason"] == "stop" and ended[0]["judged"] == 4 and ended[0]["attempts"] == 8, f"practice.ended: {ended[0]}")
    events = [row["event"] for row in rows]
    require(events.count("practice.started") == 3 and events.count("practice.ended") == 3 and "practice.unavailable" in events
            and events.count("practice.repeat") == 1,
            f"practice activity: {events}")


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
DELETE FROM voice_references WHERE user_id NOT IN (SELECT id FROM users);
DELETE FROM retired_handles WHERE user_id NOT IN (SELECT id FROM users);
DELETE FROM provider_call_jobs WHERE root_call_sid IN (SELECT call_sid FROM voice_calls WHERE phone_number LIKE '{TEST_NUMBERS}');
DELETE FROM call_activity WHERE call_sid IN (SELECT call_sid FROM voice_calls WHERE phone_number LIKE '{TEST_NUMBERS}');
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
    process = start_worker(wrangler, registry)
    try:
        if not wait_port("127.0.0.1", WORKER_PORT, timeout=20):
            print("wrangler dev did not start", file=sys.stderr)
            return 1
        result = run_tests(
            DEFAULT_LOCAL, "local Worker", LOCAL_TOKEN, LOCAL_INVITE, mock_daytona=True
        )
        if result:
            return result
        # Sandbox reaping: the configured default is a dry run; delete mode needs a restart.
        reap_users = reap_dry_run_checks(DEFAULT_LOCAL)
        stop_worker(process)
        process = start_worker(wrangler, registry, "SANDBOX_REAP_MODE:delete")
        if not wait_port("127.0.0.1", WORKER_PORT, timeout=20):
            print("wrangler dev did not restart", file=sys.stderr)
            return 1
        reap_delete_checks(DEFAULT_LOCAL, LOCAL_TOKEN, reap_users)
        print("Sandbox reaping checks passed (dry run, delete, 404, recent and live callers, fresh Sandbox)")
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
