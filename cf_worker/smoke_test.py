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
import re
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
# Every phone number the local tests use (fictional 555-01xx numbers), as a LIKE pattern.
TEST_NUMBERS = "+1555555%"
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
    # Optional handlers for other GETs (toolbox file reads): path -> (status, body, content type).
    get_hooks: list = []

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
    print("33 core checks plus operator/provider timing checks passed" if mock_daytona else "21 core checks plus operator timing checks passed")
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
            "DEPLOYMENT_ENVIRONMENT:local-test",
            "--var",
            f"TWILIO_AUTH_TOKEN:{LOCAL_TOKEN}",
            "--var",
            f"TWILIO_API_BASE:http://127.0.0.1:{DAYTONA_MOCK_PORT}/twilio",
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
