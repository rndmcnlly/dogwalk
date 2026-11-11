#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets==15.0.1"]
# ///
"""End-to-end local test of GPT-Live client delegation, the Session Orchestrator, and ACP."""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from http.server import BaseHTTPRequestHandler

from smoke_test import (
    DAYTONA_MOCK_PORT,
    INSPECTOR_PORT,
    PORT_BASE,
    WORKER_PORT,
    DEFAULT_LOCAL,
    DaytonaMock,
    LOCAL_ADMIN_PASSWORD,
    LOCAL_TOKEN,
    TEST_PHONE,
    ThreadingHTTPServer,
    prepare_local_fixture,
    spoken_handle,
    post,
    project_wrangler,
    wait_port,
)

WORKER_DIR = Path(__file__).resolve().parent
SOCKET_PORT = PORT_BASE + 4
ORCHESTRATOR_MOCK_PORT = PORT_BASE + 5
IDENTITY_SECRET = f"local-identity-secret-{secrets.token_hex(8)}"
OPENAI_WEBHOOK_KEY = secrets.token_bytes(32)
OPENAI_WEBHOOK_SECRET = "whsec_" + base64.b64encode(OPENAI_WEBHOOK_KEY).decode()


def without_meta(message: dict) -> dict:
    """A request's params apart from `_meta`, whose trace context is checked on its own."""
    return {key: value for key, value in message["params"].items() if key != "_meta"}


class SocketMocks:
    def __init__(self) -> None:
        self.live = None
        self.acp_messages: asyncio.Queue[dict] = asyncio.Queue()
        self.live_messages: asyncio.Queue[dict] = asyncio.Queue()
        self.acp_connected = asyncio.Event()
        self.live_connected = asyncio.Event()
        self.seen: list[dict] = []
        self.acp_seen: list[dict] = []
        self.acp = None
        self.acp_connections = 0
        # The Agent answers initialize late, so the call starts before the dogs are ready.
        self.init_delay = 1.5
        self.advertise_resume = False
        # Like OpenCode, the Agent reloads a session only from the directory it belongs to.
        self.session_cwds = {
            PERSISTED_SESSION_ID: "/home/daytona/parser",
            "ses_elsewhere0outside": "/tmp/elsewhere",
        }

    def session_entries(self) -> list[dict]:
        return [
            {
                "sessionId": session_id,
                "cwd": cwd,
                "title": PERSISTED_TITLE if session_id == PERSISTED_SESSION_ID else "Other work",
                "updatedAt": "2026-11-06T18:30:00Z",
            }
            for session_id, cwd in self.session_cwds.items()
        ]

    async def handler(self, socket) -> None:
        if socket.request.path == "/acp":
            self.acp = socket
            self.acp_connections += 1
            self.acp_connected.set()
            async for raw in socket:
                message = json.loads(raw)
                self.acp_seen.append(message)
                await self.acp_messages.put(message)
                request_id = message.get("id")
                method = message.get("method")
                if request_id is None:
                    continue
                params = message.get("params") or {}
                error = None
                if method == "initialize":
                    await asyncio.sleep(self.init_delay)
                    capabilities = {"sessionCapabilities": {"resume": {}}} if self.advertise_resume else {}
                    result = {"protocolVersion": 1, "agentCapabilities": capabilities}
                elif method == "session/new":
                    cwd = params["cwd"]
                    session_id = "acp-session-test" if cwd == "/home/daytona" else f"acp-session-{Path(cwd).name}"
                    self.session_cwds[session_id] = cwd
                    result = {"sessionId": session_id}
                elif method in ("session/load", "session/resume"):
                    result = {}
                    expected = self.session_cwds.get(params["sessionId"], params["cwd"])
                    if params["cwd"] != expected:
                        error = {"code": -32603, "message": "persisted session belongs to another directory"}
                elif method == "session/list":
                    # Two entries per page, so discovery must follow nextCursor.
                    start = int(params.get("cursor") or 0)
                    entries = self.session_entries()
                    result = {"sessions": entries[start:start + 2]}
                    if start + 2 < len(entries):
                        result["nextCursor"] = str(start + 2)
                elif method == "session/prompt" and params["prompt"][0]["text"] == FAIL_PROMPT:
                    for update in FAIL_UPDATES:
                        if "pause" in update:
                            await asyncio.sleep(update["pause"])
                            continue
                        await socket.send(json.dumps({
                            "jsonrpc": "2.0",
                            "method": "session/update",
                            "params": {"sessionId": "acp-session-test", "update": update},
                        }))
                    error = {"code": -32603, "message": "SseError: the event stream ended"}
                elif method == "session/prompt" and params["prompt"][0]["text"] == OOM_PROMPT:
                    # As in a real call: OpenCode's server child is OOM-killed, the turn gets a
                    # bare error, then the gateway closes with the reason.
                    await socket.send(json.dumps({
                        "jsonrpc": "2.0", "id": request_id,
                        "error": {"code": -32603, "message": "ServerUnavailable"},
                    }))
                    await asyncio.sleep(0.25)
                    await socket.close(1011, json.dumps(OOM_CLOSE, separators=(",", ":")))
                    return
                elif method == "session/prompt":
                    await asyncio.sleep(0.2)
                    for update in PROMPT_UPDATES:
                        if "pause" in update:
                            await asyncio.sleep(update["pause"])
                            continue
                        await socket.send(json.dumps({
                            "jsonrpc": "2.0",
                            "method": "session/update",
                            "params": {"sessionId": "acp-session-test", "update": update},
                        }))
                    result = {"stopReason": "end_turn"}
                else:
                    result = {}
                outcome = {"error": error} if error else {"result": result}
                await socket.send(
                    json.dumps({"jsonrpc": "2.0", "id": request_id, **outcome})
                )
            return

        path = socket.request.path
        if path.startswith("/live/sessions/") and path.endswith("/attach"):
            self.live = socket
            self.live_connected.set()
            async for raw in socket:
                message = json.loads(raw)
                self.seen.append(message)
                await self.live_messages.put(message)
                # GPT-Live acknowledges each output fixture once its mixer drains.
                if message.get("type") == "session.output_audio.fixture.play":
                    asyncio.create_task(self.ack_fixture(socket))
            return

        await socket.close(1008, "unknown mock path")

    async def ack_fixture(self, socket) -> None:
        await asyncio.sleep(0.05)
        await socket.send(json.dumps({"type": "session.output_audio.fixture.played"}))

    async def send_live(self, message: dict) -> None:
        assert self.live is not None
        await self.live.send(json.dumps(message))


def message_chunk(text: str) -> dict:
    return {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}


# Beats closer together than BEAT_COALESCE_MS (1.5 s) coalesce; the margin absorbs the
# Durable Object's own processing time.
SETTLE = {"pause": 2.2}

# One Prompt Turn's traffic, shaped like OpenCode's: a plan, a read whose locations arrive
# with in_progress, the Agent narrating between tool calls, then the first edit, each its own
# beat. Then a burst: a plan as a remark, a tool of no known kind, and a command at once.
PROMPT_UPDATES = [
    SETTLE,
    {"sessionUpdate": "plan", "entries": [
        {"content": "Find the parser", "priority": "high", "status": "in_progress"},
        {"content": "Write the artifact", "priority": "medium", "status": "pending"},
    ]},
    SETTLE,
    {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "read", "kind": "read", "status": "pending"},
    {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "in_progress",
     "locations": [{"path": "/home/daytona/src/parser.py"}]},
    {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"},
    SETTLE,
    message_chunk("The parser lives in **src**. "),
    {"sessionUpdate": "tool_call", "toolCallId": "t2", "title": "write", "kind": "edit", "status": "pending"},
    SETTLE,
    {"sessionUpdate": "tool_call_update", "toolCallId": "t2", "status": "in_progress",
     "locations": [{"path": "/home/daytona/artifact.txt"}]},
    {"sessionUpdate": "tool_call_update", "toolCallId": "t2", "status": "completed"},
    SETTLE,
    message_chunk("I'll check the tests next, which should take about a minute. "),
    {"sessionUpdate": "tool_call", "toolCallId": "t3", "title": "websearch", "kind": "other", "status": "in_progress"},
    {"sessionUpdate": "tool_call_update", "toolCallId": "t3", "status": "completed"},
    {"sessionUpdate": "tool_call", "toolCallId": "t4", "title": "bash", "kind": "execute", "status": "in_progress"},
    {"sessionUpdate": "tool_call_update", "toolCallId": "t4", "status": "completed"},
    message_chunk("Bridge report complete."),
]

FAIL_PROMPT = "Check the repository's state and summarize it."
SHELL_COMMAND = "git status --short --branch && git log --oneline -3"
# A command starts and never finishes; three steps fail in a burst; then the turn itself fails.
FAIL_UPDATES = [
    SETTLE,
    {"sessionUpdate": "tool_call", "toolCallId": "g1", "title": SHELL_COMMAND, "kind": "execute", "status": "in_progress"},
    SETTLE,
    *({"sessionUpdate": "tool_call", "toolCallId": f"gf{index}", "title": "websearch", "kind": "fetch", "status": "failed"} for index in range(3)),
    {"pause": 3.0},
]

OOM_PROMPT = "Install the dependencies with pnpm."
OOM_CLOSE = {"reason": "out_of_memory", "signal": "SIGKILL", "exit_status": 1, "limit_bytes": 1073741824}
OOM_SENTENCE = "The Agent was killed because the sandbox ran out of memory (1 GiB limit)."

CALL_BRIEF = "Call started with no Managed Sessions."
READY_BRIEF = "The sandbox is ready."
# Every theme's lexicon in src/themes.ts: words that would show a theme leaking into ACP.
THEME_LEXICON = [
    word
    for words in re.findall(r"lexicon: \[(.*?)\]", (Path(__file__).parent / "src" / "themes.ts").read_text())
    for word in re.findall(r'"([^"]+)"', words)
]
assert "dog" in THEME_LEXICON and "callsign" in THEME_LEXICON
KEYPAD_SAY = "Seven noted."
NEXT_GREETING = "Last time Juniper built a tiny verified artifact."
FIRST_OPENING = (
    "Caller ID unverified. Dogwalk. Welcome! I'm your walker; your sandbox is "
    "being set up, which takes a minute or two."
)
RETURN_OPENING = (
    f"Verified. Dogwalk. {NEXT_GREETING} Since then, Juniper asked for permission. Where to?"
)
REAPED_CLAUSE = "Your sandbox was cleared after a long time without calls, so this one starts fresh."
WORK_BRIEF = "Starting Juniper on a tiny verified artifact."
AGENT_PROMPT = (
    "Goal: create a tiny artifact and verify it. Finish with a short summary "
    "of what changed and how it was verified."
)
STARTED_SAY = "Juniper is on it."
FINISHED_SAY = "Juniper finished; it reports the artifact is verified."
REPORTED_SAY = "Juniper says the artifact is verified."
FAREWELL_SAY = "Talk soon."
SWITCH_SAY = "Switching themes; a new voice will pick up in a moment."
PERSISTED_SESSION_ID = "ses_earlier0parser"
PERSISTED_TITLE = "Earlier parser work"
LEASH_SAY = "Rowan has gone home; Juniper is still with us."
REPLAY_SAY = "The page heard them but could not read them; sending them again."


def call(call_id: str, name: str, **arguments) -> dict:
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments),
    }


def tool_result(context: list[dict], call_id: str) -> dict:
    return json.loads(next(
        item for item in context
        if item.get("type") == "function_call_output" and item["call_id"] == call_id
    )["output"])


def invite_words(context: list[dict], call_id: str) -> str:
    output = next(
        item for item in context
        if item.get("type") == "function_call_output" and item["call_id"] == call_id
    )
    return json.loads(output["output"])["invitation"]["words"]


def created_alias(context: list[dict]) -> str:
    output = next(
        item for item in context
        if item.get("type") == "function_call_output" and item["call_id"] == "h1-create"
    )
    return json.loads(output["output"])["session"]["alias"]


# Each step fires once its trigger appears in a user message, in order, after the
# previous step's outputs are back. Steps that answer a handoff use its delegation id.
ORCHESTRATOR_SCRIPT = [
    # Session tools fail fast while the Sandbox is still starting.
    ("call_started", lambda ctx: [
        call("cs-brief", "brief", text=CALL_BRIEF, delegation_id=None),
        call("cs-create", "create_managed_session", alias="Early", directory="."),
        call("cs-list", "list_managed_sessions"),
    ]),
    ("[harness event] agent_connected", lambda ctx: [call("dr-brief", "brief", text=READY_BRIEF, delegation_id=None)]),
    ("[handoff del-1]", lambda ctx: [
        call("h1-brief", "brief", text=WORK_BRIEF, delegation_id="del-1"),
        call("h1-create", "create_managed_session", alias="Juniper", directory="."),
    ]),
    ("[handoff del-1]", lambda ctx: [
        call("h1-begin", "begin_prompt_turn", alias=created_alias(ctx), prompt=AGENT_PROMPT),
    ]),
    ("[handoff del-1]", lambda ctx: [
        call("h1-say-url", "say", text="Watch it at https://example.com", delegation_id="del-1"),
    ]),
    ("[handoff del-1]", lambda ctx: [call("h1-say", "say", text=STARTED_SAY, delegation_id="del-1")]),
    ("turn_stopped", lambda ctx: [call("ts-say", "say", text=FINISHED_SAY, delegation_id=None)]),
    ("[handoff del-2]", lambda ctx: [
        call("h2-inspect", "inspect_managed_session", alias=created_alias(ctx)),
    ]),
    ("[handoff del-2]", lambda ctx: [call("h2-unfollow", "stop_following")]),
    ("[handoff del-2]", lambda ctx: [call("h2-say", "say", text=REPORTED_SAY, delegation_id="del-2")]),
    # Handoff 3: persisted-session lifecycle (discover, load, retag, close, list).
    ("[handoff del-3]", lambda ctx: [call("h3-discover", "discover_persisted_sessions")]),
    ("[handoff del-3]", lambda ctx: [
        call("h3-load", "load_persisted_session", ref="r1", alias="Birch"),
    ]),
    ("[handoff del-3]", lambda ctx: [
        call("h3-retag", "set_alias", alias="Birch", new_alias="Rowan"),
    ]),
    ("[handoff del-3]", lambda ctx: [call("h3-close", "close_managed_session", alias="Rowan")]),
    ("[handoff del-3]", lambda ctx: [call("h3-list", "list_managed_sessions")]),
    # Sessions are rooted in project directories, which must exist inside the Workspace.
    ("[handoff del-3]", lambda ctx: [call("h3-projects", "list_workspace_projects")]),
    ("[handoff del-3]", lambda ctx: [
        call("h3-dozer", "create_managed_session", alias="Dozer", directory="honeydozer"),
    ]),
    ("[handoff del-3]", lambda ctx: [
        call("h3-ghost", "create_managed_session", alias="Ghost", directory="missing"),
        call("h3-escape", "create_managed_session", alias="Escape", directory="../etc"),
    ]),
    ("[handoff del-3]", lambda ctx: [call("h3-say", "say", text=LEASH_SAY, delegation_id="del-3")]),
    ("[handoff del-4]", lambda ctx: [
        call("h4-say", "say", text=FAREWELL_SAY, delegation_id="del-4"),
        call("h4-end", "end_call"),
    ]),
    # Second call: a turn that fails mid-command: its running command ends with it.
    ("[handoff del-f]", lambda ctx: [call("f-begin", "begin_prompt_turn", alias="Juniper", prompt=FAIL_PROMPT)]),
    ("turn_failed", lambda ctx: [call("f-inspect", "inspect_managed_session", alias="Juniper")]),
    # The Agent is OOM-killed mid-turn: the reason follows the bare error.
    ("[handoff del-o]", lambda ctx: [call("o-begin", "begin_prompt_turn", alias="Juniper", prompt=OOM_PROMPT)]),
    ('"agent_termination":"out_of_memory"', lambda ctx: [call("o-inspect", "inspect_managed_session", alias="Juniper")]),
    # Second call: digits without a system meaning still reach the Orchestrator.
    ('"key":"7"', lambda ctx: [call("k7-say", "say", text=KEYPAD_SAY, delegation_id=None)]),
    # Invitations: the caller (invited by SEED_INVITER) invites friends, by voice alone.
    ("[handoff del-i1]", lambda ctx: [
        call("i1-create", "create_invitation", recipient_phone=None, caller_confirmed_readback=False),
        call("i1-who", "inspect_registration"),
        call("i1-foreign", "revoke_invitation", invitation=FOREIGN_WORDS.upper()),
    ]),
    # A number is read back before it binds anything.
    ("[handoff del-i1]", lambda ctx: [call("i2-readback", "create_invitation", recipient_phone="555 555 0102", caller_confirmed_readback=False)]),
    ("[handoff del-i1]", lambda ctx: [call("i2-create", "create_invitation", recipient_phone="(555) 555-0102", caller_confirmed_readback=True)]),
    ("[handoff del-i1]", lambda ctx: [call("i3-create", "create_invitation", recipient_phone=None, caller_confirmed_readback=False)]),
    ("[handoff del-i1]", lambda ctx: [call("i4-limit", "create_invitation", recipient_phone=None, caller_confirmed_readback=False)]),
    ("[handoff del-i1]", lambda ctx: [
        call("i3-abroad", "set_invitation_recipient", invitation=invite_words(ctx, "i3-create"), recipient_phone="+44 20 7946 0958", caller_confirmed_readback=False),
        call("i3-bare", "set_invitation_recipient", invitation=invite_words(ctx, "i3-create"), recipient_phone="020 7946 0958", caller_confirmed_readback=True),
    ]),
    ("[handoff del-i1]", lambda ctx: [
        call("i-say", "say", text=f"The words are {invite_words(ctx, 'i1-create')}. Their number is 555 555 0102: plus one, five five five, five five five, zero one zero two.", delegation_id="del-i1"),
        call("i-leak", "begin_prompt_turn", alias="Juniper", prompt="Text my friend at +1 (555) 555-0102 about the parser."),
    ]),
    # Bound after a claim began: the claim's confirmation must now fail.
    ("[handoff del-i2]", lambda ctx: [
        call("i3-bind", "set_invitation_recipient", invitation=invite_words(ctx, "i3-create"), recipient_phone="+1 555 555 0104", caller_confirmed_readback=True),
    ]),
    ("[handoff del-i3]", lambda ctx: [call("i-list", "list_invitations")]),
    ("[handoff del-i3]", lambda ctx: [
        call("i3-revoke", "revoke_invitation", invitation=invite_words(ctx, "i3-create")),
        call("i1-hide", "revoke_invitation", invitation=invite_words(ctx, "i1-create")),
    ]),
    ("[handoff del-i3]", lambda ctx: [call("i-relist", "list_invitations")]),
    # Renaming: Dogwalk offers Handles; only an offered one can be chosen, from any spin.
    ("[handoff del-r1]", lambda ctx: [
        call("r1-offer", "offer_handles"),
        call("r1-bogus", "set_handle", handle="zebra zeppelin"),
    ]),
    ("[handoff del-r1]", lambda ctx: [call("r1-offer2", "offer_handles"), call("r1-offer3", "offer_handles")]),
    ("[handoff del-r1]", lambda ctx: [call("r1-offer4", "offer_handles")]),
    ("[handoff del-r1]", lambda ctx: [call("r1-set", "set_handle", handle=tool_result(ctx, "r1-offer")["options"][1].upper())]),
    ("[handoff del-r1]", lambda ctx: [call("r1-who", "inspect_registration")]),
    # Preferences and pairing are voice operations; keypad digits stand in for asking here.
    ('"key":"6"', lambda ctx: [call("k6-style", "set_earcon_style", style="tonal")]),
    ('"key":"8"', lambda ctx: [call("k8-pair", "pair_browser")]),
    # No browser claimed the first tones: the Orchestrator answers and plays them again.
    ("[harness event] pairing_unclaimed", lambda ctx: [
        call("pu-say", "say", text=REPLAY_SAY, delegation_id=None),
        call("pu-pair", "pair_browser"),
    ]),
    ('"key":"9"', lambda ctx: [call("k9-show", "show_in_browser", uri="dogwalk:file/REPORT.md", note="the report")]),
    ('"key":"4"', lambda ctx: [call("k4-state", "inspect_call_state")]),
    ('"key":"5"', lambda ctx: [call("k5-board", "draw_on_whiteboard", program=BOARD_PROGRAM, note="pick one")]),
    # A chapter closes: the Orchestrator compacts. An over-long Memory is refused first.
    ("[harness event] whiteboard_answer", lambda ctx: [call("cc-long", "compact_context", memory="x" * 12_001)]),
    ("[harness event] whiteboard_answer", lambda ctx: [call("cc-memory", "compact_context", memory=MEMORY_TEXT)]),
    # Five more boards push the first out of retention, and its captured image with it.
    ('"key":"A"', lambda ctx: [call(f"kA-evict-{n}", "draw_on_whiteboard", program=ICON_PROGRAM, note=f"icon {n}") for n in range(1, 6)]),
    ('"key":"B"', lambda ctx: [call("kB-html", "show_in_browser", uri="dogwalk:file/report.html", note="the chart report")]),
    ('"key":"3"', lambda ctx: [
        call("k3-say", "say", text=SWITCH_SAY, delegation_id=None),
        call("k3-theme", "set_theme", theme="survey", register="high"),
    ]),
    # After the bounce, Runtime Dramaturgy renders the active theme's authoring notes.
    ('"key":"2"', lambda ctx: [{
        **call("k2-dramaturgy", "load_voice_agent_skill"),
        "arguments": json.dumps({"name": "runtime-dramaturgy"}),
    }]),
    # Asking to donate voice samples hands the call to the deterministic Voice Donation flow.
    ('"key":"C"', lambda ctx: [call("kC-donate", "start_voice_donation")]),
    # Asking to test their voice hands the call to deterministic Challenge Practice.
    ('"key":"D"', lambda ctx: [call("kD-practice", "start_challenge_practice")]),
]
# Invitation fixtures (test numbers only). SEED_INVITER invited TEST_PHONE; FOREIGN is
# SEED_INVITER's own, unbound and active; EXPIRED is TEST_PHONE's and lapsed.
SEED_INVITER = "+15555550109"
SEED_HANDLE, CALLER_HANDLE = "gecko lantern", "walrus kayak"
WELCOME_WORDS = "acme bravado cobra"
FOREIGN_WORDS = "absurd caravan tiger"
EXPIRED_WORDS = "adult escapade tonic"
BOUND_PHONE = "+15555550102"
INVITEE_PHONE = "+15555550103"
REBOUND_PHONE = "+15555550104"
RACE_PHONE = "+15555550105"
LATE_PHONE = "+15555550106"
RIVAL_PHONE = "+15555550107"
TEST_NUMBERS = [TEST_PHONE, SEED_INVITER, BOUND_PHONE, INVITEE_PHONE, REBOUND_PHONE, RACE_PHONE, LATE_PHONE, RIVAL_PHONE]
MEMORY_TEXT = "Caller prefers terse updates. Juniper fixes the parser; Dozer works in honeydozer."
BOARD_PROGRAM = (
    'root = Stack([fig, big, pick])\nfig = Image("diagram.svg", "The parser\'s two stages", "Drawn by an agent", "small")\n'
    'big = Image(\'big.png\', "A generated chart")\n'
    'pick = Choice("approach", ["Quick fix", "Refactor"], "Which approach?")'
)
ICON_PROGRAM = 'root = Stack([Image (asset, "", null, "icon"), Text("Example: Image(\'missing.png\', \'x\')")])\nasset = "/home/daytona/chart.png"'
STEP_IDS = [
    "cs-brief", "cs-create", "cs-list", "dr-brief", "h1-brief", "h1-create", "h1-begin", "h1-say-url", "h1-say",
    "ts-say", "h2-inspect", "h2-unfollow", "h2-say", "f-begin", "f-inspect", "o-begin", "o-inspect",
    "h3-discover", "h3-load", "h3-retag", "h3-close", "h3-list",
    "h3-projects", "h3-dozer", "h3-ghost", "h3-escape", "h3-say",
    "h4-say", "h4-end", "k7-say", "i1-create", "i1-who", "i1-foreign", "i2-readback", "i2-create", "i3-create", "i4-limit",
    "i3-abroad", "i3-bare", "i-say", "i-leak", "i3-bind", "i-list", "i3-revoke", "i1-hide", "i-relist",
    "r1-offer", "r1-bogus", "r1-offer2", "r1-offer3", "r1-offer4", "r1-set", "r1-who", "k6-style", "k8-pair", "pu-say", "pu-pair", "k9-show", "k4-state", "k5-board", "cc-long", "cc-memory", "kA-evict-1", "kA-evict-2", "kA-evict-3", "kA-evict-4", "kA-evict-5", "kB-html", "k3-say", "k3-theme",
    "k2-dramaturgy", "kC-donate", "kD-practice",
]


class OrchestratorMock(BaseHTTPRequestHandler):
    """A scripted /v1/responses endpoint standing in for the Session Orchestrator model."""

    requests: list[tuple[dict[str, str], dict]] = []
    greeting_requests: list[dict] = []
    issued: list[tuple[str, int]] = []
    lock = threading.Lock()

    def log_message(self, *args) -> None:
        pass

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("content-length", "0")))
        payload = json.loads(raw)
        headers = {key.lower(): value for key, value in self.headers.items()}
        if self.path != "/v1/responses":
            self.send_error(404)
            return
        if payload.get("tool_choice") == "none":
            # The end-of-call request that writes the next call's welcome line.
            OrchestratorMock.greeting_requests.append(payload)
            self.respond({"output": [{
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": NEXT_GREETING}],
            }]})
            return
        with OrchestratorMock.lock:
            OrchestratorMock.requests.append((headers, payload))
            context = payload["input"]
            user_text = "\n".join(
                str(item["content"]) for item in context if item.get("role") == "user"
            )
            output = []
            step = len({index for _, index in OrchestratorMock.issued})
            if step < len(ORCHESTRATOR_SCRIPT) and ORCHESTRATOR_SCRIPT[step][0] in user_text:
                output = ORCHESTRATOR_SCRIPT[step][1](context)
                OrchestratorMock.issued.extend((item["call_id"], step) for item in output)
        # Provider correlation metadata, derived from the client's ID so the test can match rows.
        client = headers.get("x-client-request-id", "").replace("-", "")
        self.respond({"id": f"resp_{client}", "model": "mock-model-2026-11-10", "output": output,
                      "usage": {"input_tokens": 1, "output_tokens": 1}}, {"x-request-id": f"req_{client}"})

    def respond(self, value: dict, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(value).encode()
        self.send_response(200)
        for key, header in (extra_headers or {}).items():
            self.send_header(key, header)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @classmethod
    def user_message(cls, marker: str) -> str:
        for _, payload in cls.requests:
            for item in payload["input"]:
                if item.get("role") == "user" and marker in str(item["content"]):
                    return str(item["content"])
        raise AssertionError(f"orchestrator never received {marker}")

    @classmethod
    def tool_output(cls, call_id: str) -> dict:
        for _, payload in reversed(cls.requests):
            for item in payload["input"]:
                if item.get("type") == "function_call_output" and item["call_id"] == call_id:
                    return json.loads(item["output"])
        raise AssertionError(f"orchestrator never received output for {call_id}")


def transcript(direction: str, delta: str, start_ms: int, end_ms: int) -> dict:
    return {
        "type": f"session.{direction}_transcript.delta",
        "delta": delta,
        "start_ms": start_ms,
        "end_ms": end_ms,
    }


def delegation(delegation_id: str, offset_ms: int) -> dict:
    return {
        "type": "session.delegation.created",
        "offset_ms": offset_ms,
        "delegation": {"id": delegation_id, "type": "delegation", "target": "client"},
    }


def appended(lane: str, content: str):
    return lambda item: (
        item.get("type") == f"session.{lane}.append" and item.get("content") == content
    )


async def next_matching(
    queue: asyncio.Queue[dict], predicate, timeout: float = 8
) -> dict:
    deferred = []
    async with asyncio.timeout(timeout):
        while True:
            message = await queue.get()
            if predicate(message):
                for item in deferred:
                    queue.put_nowait(item)
                return message
            deferred.append(message)


def configure_assignment(wrangler: str, identity: str) -> None:
    sql = f"""
INSERT INTO invite_codes (code_words, expires_at, max_uses)
VALUES ('{WELCOME_WORDS}', unixepoch() + 86400, 1), ('{FOREIGN_WORDS}', unixepoch() + 86400, 1),
       ('{EXPIRED_WORDS}', unixepoch() - 60, 1)
ON CONFLICT(code_words) DO UPDATE SET expires_at = excluded.expires_at, max_uses = 1;
INSERT INTO users (handle, identity_hash, invite_code, registered_at, last_seen_at, home_phone, home_used_at)
VALUES ('{SEED_HANDLE}', 'seed-inviter-{identity}', 'ahead almighty apple', unixepoch() - 86400, NULL, '{SEED_INVITER}', unixepoch() - 86400),
       ('{CALLER_HANDLE}', '{identity}', '{WELCOME_WORDS}', unixepoch(), unixepoch(), '{TEST_PHONE}', unixepoch());
INSERT INTO invitations (code_words, inviter_user_id, redeemed_by_user_id, redeemed_from, redeemed_at)
VALUES ('{WELCOME_WORDS}', (SELECT id FROM users WHERE handle = '{SEED_HANDLE}'),
        (SELECT id FROM users WHERE handle = '{CALLER_HANDLE}'), '{TEST_PHONE}', unixepoch()),
       ('{FOREIGN_WORDS}', (SELECT id FROM users WHERE handle = '{SEED_HANDLE}'), NULL, NULL, NULL),
       ('{EXPIRED_WORDS}', (SELECT id FROM users WHERE handle = '{CALLER_HANDLE}'), NULL, NULL, NULL);
INSERT INTO sandbox_assignments (user_id, provider_id, state, provisioning_started_at, last_checked_at)
VALUES ((SELECT id FROM users WHERE identity_hash = '{identity}'), 'mock-sandbox-1', 'started', unixepoch(), unixepoch());
-- CA_VOICE_TEST carries front-door trace context; the others are calls from before it existed.
INSERT INTO voice_calls (call_sid, phone_number, status, started_at, last_activity_at, trace_id, span_id, user_id)
SELECT call_sid, '{TEST_PHONE}', 'in-progress', unixepoch(), unixepoch(), trace_id, span_id,
       (SELECT id FROM users WHERE identity_hash = '{identity}')
  FROM (SELECT 'CA_VOICE_TEST' AS call_sid, '{CALL_TRACE_ID}' AS trace_id, '{CALL_SPAN_ID}' AS span_id
        UNION ALL SELECT 'CA_VOICE_TEST2', NULL, NULL UNION ALL SELECT 'CA_VOICE_TEST3', NULL, NULL);
INSERT INTO call_activity (call_sid, ts, source, direction, event, detail)
VALUES ('CA_VOICE_TEST2', unixepoch(), 'voice', 'inbound', 'call.attestation',
        '{{"stir_verstat":"TN-Validation-Passed-A","caller_name_present":false,"from_country":"US","parameters":[]}}');
"""
    subprocess.run(
        [wrangler, "d1", "execute", "dogwalk", "--local", "--command", sql],
        cwd=WORKER_DIR,
        check=True,
        stdout=subprocess.DEVNULL,
    )


def signed_webhook(call_sid: str, live_session_id: str) -> tuple[str, str, str]:
    expires_at = int(time.time()) + 300
    content = f"{call_sid}.{expires_at}"
    signature = base64.urlsafe_b64encode(
        hmac.new(
            IDENTITY_SECRET.encode(),
            f"sip-context-v1:{content}".encode(),
            hashlib.sha256,
        ).digest()
    ).decode().rstrip("=")
    event = {
        "object": "event",
        "id": f"evt_{live_session_id}",
        "type": "live.transport.incoming",
        "data": {
            "type": "sip",
            "session_id": live_session_id,
            "sip_headers": [{"name": "X-Dogwalk-Context", "value": f"{content}.{signature}"}],
        },
    }
    body = json.dumps(event, separators=(",", ":"))
    webhook_id = f"wh_{live_session_id}"
    timestamp = str(int(time.time()))
    webhook_signature = base64.b64encode(
        hmac.new(
            OPENAI_WEBHOOK_KEY,
            f"{webhook_id}.{timestamp}.{body}".encode(),
            hashlib.sha256,
        ).digest()
    ).decode()
    return body, f"{webhook_id}|{timestamp}", f"v1,{webhook_signature}"


def post_webhook(body: str, identity: str, signature_header: str) -> int:
    webhook_id, timestamp = identity.split("|")
    request = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}/openai/live",
        data=body.encode(),
        headers={
            "content-type": "application/json",
            "webhook-id": webhook_id,
            "webhook-timestamp": timestamp,
            "webhook-signature": signature_header,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


CALL_TRACE_ID, CALL_SPAN_ID = "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7"  # W3C spec example IDs


def call_activity_rows(call_sid: str, timing: bool = False) -> list[dict]:
    credentials = base64.b64encode(f"adam:{LOCAL_ADMIN_PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}/admin/api/calls?verbose=1&limit=10" + ("&timing=1" if timing else ""),
        headers={"authorization": f"Basic {credentials}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        calls = json.load(response)
    return [item for call in calls if call["call_sid"] == call_sid for item in call["activity"]]


def call_activity(call_sid: str) -> list[str]:
    return [row["event"] for row in call_activity_rows(call_sid)]


def operator_timing_page(call_sid: str, after: int = 0) -> dict:
    credentials = base64.b64encode(f"adam:{LOCAL_ADMIN_PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}/admin/api/timing?call_sid={call_sid}&after={after}",
        headers={"authorization": f"Basic {credentials}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def operator_content(call_sid: str) -> dict:
    credentials = base64.b64encode(f"adam:{LOCAL_ADMIN_PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}/admin/api/timing/content?call_sid={call_sid}",
        headers={"authorization": f"Basic {credentials}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def conversations() -> list[dict]:
    credentials = base64.b64encode(f"adam:{LOCAL_ADMIN_PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}/admin/api/conversations?verbose=1",
        headers={"authorization": f"Basic {credentials}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def operator_access_state(verbose: bool = False) -> dict:
    credentials = base64.b64encode(f"adam:{LOCAL_ADMIN_PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}/admin/api/state" + ("?verbose=1" if verbose else ""),
        headers={"authorization": f"Basic {credentials}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def latest_user_text() -> str:
    if not OrchestratorMock.requests:
        return ""
    return "\n".join(
        str(item["content"]) for item in OrchestratorMock.requests[-1][1]["input"] if item.get("role") == "user"
    )


# --- Paired Browser helpers ----------------------------------------------------

CHROME_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
DTMF_ROWS = [697, 770, 852, 941]
DTMF_COLS = [1209, 1336, 1477, 1633]
DTMF_KEYPAD = ["123A", "456B", "789C", "*0#D"]
PAIRING_ALPHABET = "0123456789ABCD*#"
REPORT_TEXT = "# Parser report\n\nAll green.\n"


def decode_pairing_tones(audio_b64: str) -> str | None:
    """Read the Pairing Code from a played fixture, as a browser would; None if it is not tones.

    The fixture is exactly clocked (24 kHz, 0.3 s lead, 8 baud: a 125 ms slot opening with a
    70 ms tone), so one Goertzel window early in each slot suffices; real browsers need the
    symbol clock and ECC.
    """
    raw = base64.b64decode(audio_b64)
    samples = [int.from_bytes(raw[i:i + 2], "little", signed=True) / 32768 for i in range(0, len(raw) - 1, 2)]
    lead, slot, window = 7200, 3000, 1000
    if len(samples) < lead + 32 * slot:
        return None
    symbols = []
    for index in range(32):
        x = samples[lead + index * slot + 250: lead + index * slot + 250 + window]
        energy = sum(v * v for v in x) / window
        powers = []
        for frequency in DTMF_ROWS + DTMF_COLS:
            c = 2 * math.cos(2 * math.pi * frequency / 24000)
            s1 = s2 = 0.0
            for v in x:
                s1, s2 = v + c * s1 - s2, s1
            powers.append(2 * (s1 * s1 + s2 * s2 - c * s1 * s2) / (window * window))
        row = max(range(4), key=lambda i: powers[i])
        col = max(range(4), key=lambda i: powers[4 + i])
        if not energy or powers[row] + powers[4 + col] < 0.8 * energy:
            return None
        symbols.append(DTMF_KEYPAD[row][col])
    nibbles = [PAIRING_ALPHABET.index(symbol) for symbol in symbols[:20]]
    return bytes(nibbles[i] << 4 | nibbles[i + 1] for i in range(0, 20, 2)).hex()


def pair_http(method: str, path: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{WORKER_PORT}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def pair_raw(path: str, headers: dict) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(f"http://127.0.0.1:{WORKER_PORT}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


async def recv_type(socket, kind: str, timeout: float = 8) -> dict:
    """The next message of a kind, skipping the status pushes that may arrive in between."""
    async with asyncio.timeout(timeout):
        while True:
            message = json.loads(await socket.recv())
            if message.get("type") == kind:
                return message


PNG_BYTES = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII=")
SVG_BYTES = b'<?xml version="1.0"?>\n<!-- drawn by an agent -->\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><script>alert(1)</script><rect width="10" height="10"/></svg>'
# Workspace images: name -> (bytes, size the file info reports). huge.png understates its size,
# so only the cap on downloaded bytes can catch it.
IMAGE_FILES = {
    "chart.png": (PNG_BYTES, len(PNG_BYTES)),
    "diagram.svg": (SVG_BYTES, len(SVG_BYTES)),
    "notes.png": (b"just some text\n", 15),
    "huge.png": (PNG_BYTES + bytes(8 * 1024 * 1024), 1000),
    "escape.png": (PNG_BYTES, len(PNG_BYTES)),
    "big.png": (PNG_BYTES + bytes(1_600_000), len(PNG_BYTES) + 1_600_000),
    "wide.png": (PNG_BYTES[:16] + (9000).to_bytes(4, "big") + PNG_BYTES[20:], len(PNG_BYTES)),
    "pixels.png": (PNG_BYTES[:16] + (5000).to_bytes(4, "big") * 2 + PNG_BYTES[24:], len(PNG_BYTES)),
}
# An HTML report: a Workspace image (relative) is inlined, a web image and one outside the Workspace are not.
REPORT_HTML = (
    '<!doctype html><h1>Chart</h1><img src="chart.png" srcset="chart.png 2x" alt="The chart">'
    '<img src="https://example.com/a.png" alt="web"><img src="/etc/x.png" alt="Outside"><img src="data:image/gif;base64,R0lG" alt="inline">'
)


def sandbox_files(path: str):
    """Toolbox file reads: home holds a report and some images; anything else does not exist."""
    prefix = "/toolbox/mock-sandbox-1/files"
    for name, (body, size) in IMAGE_FILES.items():
        if path == f"{prefix}/info?path=%2Fhome%2Fdaytona%2F{name}":
            return 200, json.dumps({"name": name, "isDir": False, "size": size}).encode(), "application/json"
        if path == f"{prefix}/download?path=%2Fhome%2Fdaytona%2F{name}":
            return 200, body, "application/octet-stream"
    if path == f"{prefix}?path=%2Fhome%2Fdaytona":
        listing = [{"name": "REPORT.md", "isDir": False, "size": len(REPORT_TEXT), "modTime": "2026-11-08T10:00:00Z"},
                   {"name": "parser", "isDir": True, "size": 4096, "modTime": "2026-11-08T09:00:00Z"}]
        return 200, json.dumps(listing).encode(), "application/json"
    if path == f"{prefix}/info?path=%2Fhome%2Fdaytona%2Freport.html":
        return 200, json.dumps({"name": "report.html", "isDir": False, "size": len(REPORT_HTML)}).encode(), "application/json"
    if path == f"{prefix}/download?path=%2Fhome%2Fdaytona%2Freport.html":
        return 200, REPORT_HTML.encode(), "application/octet-stream"
    if path == f"{prefix}/info?path=%2Fhome%2Fdaytona%2FREPORT.md":
        return 200, json.dumps({"name": "REPORT.md", "isDir": False, "size": len(REPORT_TEXT)}).encode(), "application/json"
    if path == f"{prefix}/download?path=%2Fhome%2Fdaytona%2FREPORT.md":
        return 200, REPORT_TEXT.encode(), "application/octet-stream"
    return None


def dtmf(key: str) -> dict:
    return {"type": "transport.dtmf.received", "event": key}


def of_type(kind: str):
    return lambda item: item.get("type") == kind


async def wait_for(condition, timeout: float = 8) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.05)


async def run_protocol(identity: str) -> None:
    mocks = SocketMocks()
    async with serve(mocks.handler, "127.0.0.1", SOCKET_PORT):
        body, webhook_identity, signature = signed_webhook("CA_VOICE_TEST", "live-test")
        assert await asyncio.to_thread(post_webhook, body, webhook_identity, "v1,invalid") == 400
        assert not DaytonaMock.openai_requests
        assert await asyncio.to_thread(post_webhook, body, webhook_identity, signature) == 204
        await asyncio.wait_for(mocks.acp_connected.wait(), 8)
        await asyncio.wait_for(mocks.live_connected.wait(), 8)
        accept_path, accept_payload = next(
            item for item in DaytonaMock.openai_requests if item[0].endswith("/accept")
        )
        assert accept_path == "/live/sessions/live-test/accept"
        session = accept_payload["session"]
        assert session["type"] == "live" and session["model"] == "gpt-live-1"
        assert "audio" in session and "format" not in session["audio"]
        assert "Generate <|SpawnThinking|> when" in session["instructions"]
        assert session["delegation"] == {"type": "client", "acknowledgement_delay_ms": 400}
        assert "tools" not in session
        frontend = session["instructions"]
        assert "Match the caller's sophistication" in frontend
        assert "press star for a menu" in frontend and "pound to make you stop talking" in frontend
        # A neutral core holds the rules; the Park theme only presents them. The voice
        # defers to its head and names no backend except in GPT-Live's delegation-policy
        # label and the one sentence forbidding it.
        assert "The session model:" in frontend and "Whatever the theme and intensity" in frontend
        assert "The walk (Park theme)" in frontend and "your head (your mind)" in frontend
        # Examples narrate a placeholder, never a plausible alias the voice might repeat.
        assert "<alias>" in frontend and not any(name in frontend for name in ("Cedar", "Biscuit", "Kappa", "Dozer")), frontend
        assert "name only aliases the Session Orchestrator" in frontend
        assert "An intention or estimate is not an action or a limit" in frontend
        assert "A failed step is not a failed turn" in frontend
        assert "Never mention a backend" in frontend
        # Delegated work is never claimed before the Orchestrator says a turn began.
        assert "unless the Session Orchestrator told you a turn began for it" in frontend
        # Pairing is thinking work: the Voice Frontend hands off rather than standing by.
        delegated = frontend.split("Backend tools:", 1)[1].split("\n", 1)[0]
        assert "pairing a browser" in delegated and "paired screen" in delegated and "whiteboard" in delegated, delegated
        assert "inviting someone" in delegated and "who invited the caller" in delegated, delegated
        assert "backend" not in (
            frontend.replace("Backend tools:", "").replace("Never mention a backend", "").lower()
        )

        # Talk first: the opening is spoken before the Agent has even initialized.
        greeting = await next_matching(mocks.live_messages, of_type("session.commentary.append"))
        assert greeting["content"] == FIRST_OPENING and greeting["delegation_id"] is None
        await next_matching(mocks.acp_messages, lambda item: item.get("method") == "initialize")
        call_brief = await next_matching(mocks.live_messages, appended("thinking", CALL_BRIEF))
        assert call_brief["delegation_id"] is None
        started_event = OrchestratorMock.user_message("call_started")
        assert '"first_call":true' in started_event and FIRST_OPENING in started_event
        assert '"caller_id":null' in started_event
        assert "agent_connecting" in started_event
        await next_matching(mocks.live_messages, appended("thinking", READY_BRIEF))
        early = OrchestratorMock.tool_output("cs-create")
        assert early["ok"] is False and "still starting" in early["error"], early
        assert OrchestratorMock.tool_output("cs-list") == []
        assert '"managed_session_count":0' in OrchestratorMock.user_message("[harness event] agent_connected")

        # Handoff 1: the Voice Frontend delegates; the task is the transcript.
        await mocks.send_live(transcript("input", "Start a session called Juniper ", 0, 900))
        await mocks.send_live(transcript("input", "to build a tiny verified artifact.", 900, 2100))
        await mocks.send_live(transcript("output", "On it.", 2200, 2600))
        await mocks.send_live(delegation("del-1", 2150))
        work_brief = await next_matching(mocks.live_messages, appended("thinking", WORK_BRIEF))
        assert work_brief["delegation_id"] == "del-1"
        created = await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/new"
        )
        assert without_meta(created) == {"cwd": "/home/daytona", "mcpServers": []}
        prompt_request = await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/prompt"
        )
        assert prompt_request["params"]["sessionId"] == "acp-session-test"
        assert prompt_request["params"]["prompt"] == [{"type": "text", "text": AGENT_PROMPT}]
        started = await next_matching(mocks.live_messages, appended("commentary", STARTED_SAY))
        assert started["delegation_id"] == "del-1"
        finished = await next_matching(mocks.live_messages, appended("commentary", FINISHED_SAY), 20)
        assert finished["delegation_id"] is None
        handoff = OrchestratorMock.user_message("[handoff del-1]")
        assert "Caller: Start a session called Juniper to build a tiny verified artifact." in handoff
        assert "Voice: On it." in handoff
        stopped = OrchestratorMock.user_message("turn_stopped")
        assert '"stop_reason":"end_turn"' in stopped and "Bridge report complete." in stopped
        rejected = OrchestratorMock.tool_output("h1-say-url")
        assert rejected["ok"] is False and "URL" in rejected["error"]
        juniper = OrchestratorMock.tool_output("h1-create")["session"]
        assert juniper["alias"] == "Juniper" and juniper["directory"] == "."
        begun = OrchestratorMock.tool_output("h1-begin")
        assert begun["turn_state"] == "in_progress" and begun["following"] == "Juniper", begun
        assert '"followed":true' in stopped

        # Sending Juniper off followed it: progress beats reach the Voice Frontend as
        # thinking, built from ACP updates without a model call.
        # Beats a few seconds apart are joined into one append; beats in one burst make one beat.
        def beats() -> str:
            return "\n".join(
                m["content"] for m in mocks.seen
                if m.get("type") == "session.thinking.append" and m["content"].startswith("Following Juniper:")
            )

        expected_beats = [
            "Juniper is working", "step 1 of 2: Find the parser.", "Juniper is reading parser.py.",
            'Juniper says: "The parser lives in src."', "started changing files: editing artifact.txt.",
            # A remark keeps the Agent's words and tense: a plan and an estimate, not an action.
            "Juniper says: \"I'll check the tests next, which should take about a minute.\"",
        ]
        await wait_for(lambda: all(fragment in beats() for fragment in expected_beats))
        # The burst made one beat: the remark outweighed the command started with it, and the
        # tool of no known kind made none, so its name never reached the Voice Frontend.
        assert "running a command" not in beats() and "websearch" not in beats(), beats()
        assert "using" not in beats(), beats()
        # Tool calls failing after the turn ended make no beat: the turn event already spoke.
        for index in range(3):
            await mocks.acp.send(json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": "acp-session-test",
                "update": {"sessionUpdate": "tool_call", "toolCallId": f"f{index}", "title": "websearch", "kind": "fetch", "status": "failed"},
            }}))
        await asyncio.sleep(4)
        assert "failed for Juniper" not in beats(), beats()

        # Handoff 2: a follow-up question answered from session inspection.
        await mocks.send_live(transcript("input", "What did it report?", 5000, 6200))
        await mocks.send_live(delegation("del-2", 6250))
        reported = await next_matching(mocks.live_messages, appended("commentary", REPORTED_SAY))
        assert reported["delegation_id"] == "del-2"
        handoff = OrchestratorMock.user_message("[handoff del-2]")
        assert "Caller: What did it report?" in handoff and "Juniper" not in handoff
        inspected = OrchestratorMock.tool_output("h2-inspect")
        assert inspected["alias"] == "Juniper" and inspected["stop_reason"] == "end_turn"
        # The failed searches after the turn made its closing words the latest remark.
        assert inspected["followed"] is True and inspected["remark"] == "Bridge report complete.", inspected
        assert [(c["kind"], c["status"], c["paths"]) for c in inspected["recent_tool_calls"]] == [
            ("edit", "completed", ["artifact.txt"]), ("other", "completed", []), ("execute", "completed", []),
            ("fetch", "failed", []), ("fetch", "failed", []), ("fetch", "failed", []),
        ], inspected
        assert OrchestratorMock.tool_output("h2-unfollow") == {"ok": True, "stopped_following": "Juniper"}
        events = await asyncio.to_thread(call_activity, "CA_VOICE_TEST")
        for expected in ("session.followed", "session.beat", "session.unfollowed"):
            assert expected in events, (expected, events)

        # The trace is opt-in, millisecond-stamped, and never retains raw audio.
        await mocks.send_live({"type": "session.output_audio.delta", "delta": "PRIVATE_AUDIO_BASE64_SENTINEL", "start_ms": 0, "end_ms": 200})
        await asyncio.sleep(1.2)  # one off-path telemetry flush
        rows = await asyncio.to_thread(call_activity_rows, "CA_VOICE_TEST", True)
        timed = [json.loads(row["detail"]) for row in rows if row["event"] == "voice.timing"]
        assert "voice.timing" not in events
        assert timed and all(isinstance(row["ts_ms"], int) and row["live_session_id"] == "live-test" for row in timed)
        assert all(not ({"audio", "content", "delta"} & row.keys()) for row in timed)
        assert "PRIVATE_AUDIO_BASE64_SENTINEL" not in json.dumps(timed), "audio delta was mistaken for transcript content"
        kinds = {row["event"] for row in timed}
        assert {"live.received", "live.sent", "handoff.started", "handoff.queued", "model.started",
                "model.headers", "model.completed", "tool.started", "tool.completed", "acp.sent", "acp.received"} <= kinds, kinds
        # Producer identity and tool outcomes are recorded as rows are written.
        assert all(row.get("deployment_environment") == "local-test" for row in timed), "rows not stamped with their environment"
        acp_rows = [row for row in timed if row["event"] in {"acp.sent", "acp.received"}]
        assert acp_rows and all(isinstance(row.get("agent_connection_id"), str) for row in acp_rows), "ACP rows lack Agent Connection identity"
        assert all(row.get("outcome") in {"ok", "error_result", "returned"} for row in timed if row["event"] == "tool.completed")
        # Live trace context (issue #8, B1): the front door mints the Voice Call's context, and
        # Orchestrator work, Prompt Turns, and ACP requests carry what they minted at start.
        call_trace = {"trace_id": CALL_TRACE_ID, "span_id": CALL_SPAN_ID}
        orchestrated = [row for row in timed if row["event"].startswith(("model.", "tool."))]
        assert orchestrated and all(row.get("trace_id") == call_trace["trace_id"]
                                    and row.get("parent_span_id") == call_trace["span_id"] for row in orchestrated)
        spans_by_request = {}
        for row in orchestrated:
            spans_by_request.setdefault(row.get("request_id") or row.get("tool_call_id"), set()).add(row["span_id"])
        assert all(len(spans) == 1 for spans in spans_by_request.values()), "one span per model request or tool call"
        turns = [json.loads(row["detail"]) for row in rows if row["event"] == "acp.turn.started"]
        assert turns and all(t.get("trace_id") != call_trace["trace_id"] and t.get("link_trace_id") == call_trace["trace_id"]
                             and t.get("link_span_id") == call_trace["span_id"] for t in turns), "turns are linked work traces"
        prompts = [row for row in timed if row["event"] == "acp.sent" and row.get("method") == "session/prompt"]
        assert prompts and all(any(p.get("trace_id") == t["trace_id"] and p.get("parent_span_id") == t["span_id"] for t in turns)
                               for p in prompts), "session/prompt is a child of its Prompt Turn"
        requests = {row["rpc_id"]: row for row in timed if row["event"] == "acp.sent" and row.get("method")}
        answers = [row for row in timed if row["event"] == "acp.received" and not row.get("method") and row.get("rpc_id") in requests]
        assert answers and all(a.get("span_id") == requests[a["rpc_id"]].get("span_id") for a in answers), "responses name their request"
        # ACP propagation (issue #8, B3): each request carries its own journaled span as
        # `_meta.traceparent` and nothing else in `_meta` (so session/prompt's, matching its row
        # above, is a child of the Prompt Turn); responses and notifications carry none.
        wire = {m["id"]: m for m in mocks.acp_seen if m.get("method") and "id" in m}
        for rpc_id, row in requests.items():
            meta = wire[rpc_id]["params"].get("_meta")
            assert meta == {"traceparent": f"00-{row['trace_id']}-{row['span_id']}-01"}, (rpc_id, meta, row)
        assert {"initialize", "session/new", "session/prompt"} <= {wire[i]["method"] for i in requests}
        assert all("_meta" not in (m.get("params") or {}) and "_meta" not in (m.get("result") or {})
                   for m in mocks.acp_seen if not m.get("method") or "id" not in m), "only our requests carry trace context"
        # Responses propagation (issue #8, B2): each model request carries its own span as a
        # version-00 traceparent and its request_id as X-Client-Request-Id; the provider's
        # correlation metadata, never content, is journaled on the headers and completed rows.
        sent = {headers.get("x-client-request-id"): headers for headers, _ in OrchestratorMock.requests}
        model_done = [row for row in timed if row["event"] == "model.completed"]
        assert model_done
        for done in model_done:  # Completed rows only: a request may still be in flight.
            start = next(row for row in timed if row["event"] == "model.started" and row["request_id"] == done["request_id"])
            headers = sent.get(start["request_id"])
            assert headers, f"no request carried X-Client-Request-Id {start['request_id']}"
            assert headers.get("traceparent") == f"00-{start['trace_id']}-{start['span_id']}-01", headers.get("traceparent")
            assert "tracestate" not in headers and "baggage" not in headers
            assert (start["request_model"], start["server_address"], start["server_port"], start["reasoning_level"]) == (
                "gpt-6.1-sol", "127.0.0.1", ORCHESTRATOR_MOCK_PORT, "low"), start
            client = start["request_id"].replace("-", "")
            heard = next(row for row in timed if row["event"] == "model.headers" and row["request_id"] == start["request_id"])
            assert heard["provider_request_id"] == f"req_{client}", heard
            assert (done["response_id"], done["response_model"], done["usage"]) == (
                f"resp_{client}", "mock-model-2026-11-10", {"input_tokens": 1, "output_tokens": 1}), done
        assert not any({"input", "output", "instructions"} & row.keys() for row in timed if row["event"].startswith("model."))
        received = next(row for row in timed if row["event"] == "live.received" and row.get("type") == "session.input_transcript.delta")
        assert received["end_ms"] >= received["start_ms"] and received["queue_ms"] >= 0
        assert isinstance(received.get("content_text"), str) and received.get("content_kind") == "caller"
        for row in (row for row in timed if row["event"] == "model.completed"):
            start = next(start for start in timed if start["event"] == "model.started" and start["request_id"] == row["request_id"])
            assert row["ts_ms"] >= start["ts_ms"] and row["ms"] >= 0
        queued = next(row for row in timed if row["event"] == "handoff.queued" and row["delegation_id"] == "del-2")
        started = next(row for row in timed if row["event"] == "handoff.started" and row["delegation_id"] == "del-2")
        assert queued["ts_ms"] - started["ts_ms"] >= 300
        content = await asyncio.to_thread(operator_content, "CA_VOICE_TEST")
        kinds = {entry["kind"] for entry in content["entries"]}
        assert {"caller", "voice", "handoff", "say", "acp_prompt", "agent_report"} <= kinds, kinds
        assert all(entry["source"] in ("saved_transcript", "orchestrator_history") for entry in content["entries"])
        assert any(entry["observed_at_ms"] is not None for entry in content["entries"]), "known tool/handoff IDs lost timing correlation"
        tools = [row for row in timed if row["event"] == "acp.received" and row.get("tool_call_id")]
        assert tools and any(row.get("tool_status") == "completed" for row in tools), tools

        # Handoff 3: leash earlier work, retag it, let it go home, and list what remains.
        await mocks.send_live(transcript("input", "Pick up that parser work from yesterday ", 7000, 8000))
        await mocks.send_live(transcript("input", "as Birch, call it Rowan, then let it go.", 8000, 8800))
        await mocks.send_live(delegation("del-3", 8850))
        # Discovery lists every directory (following nextCursor) and resumes from the
        # session's own directory.
        listed = await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/list"
        )
        assert without_meta(listed) == {}
        listed = await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/list"
        )
        assert without_meta(listed) == {"cursor": "2"}
        resumed = await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/resume"
        )
        assert without_meta(resumed) == {
            "sessionId": PERSISTED_SESSION_ID, "cwd": "/home/daytona/parser", "mcpServers": [],
        }
        closed = await next_matching(
            mocks.acp_messages, lambda item: item.get("method") == "session/close"
        )
        assert without_meta(closed) == {"sessionId": PERSISTED_SESSION_ID}
        leashed = await next_matching(mocks.live_messages, appended("commentary", LEASH_SAY))
        assert leashed["delegation_id"] == "del-3"
        discovered = OrchestratorMock.tool_output("h3-discover")
        assert discovered["ok"] is True
        assert discovered["sessions"] == [
            {"ref": "r1", "title": PERSISTED_TITLE, "updated_at": "2026-11-06T18:30:00Z", "directory": "parser"}
        ]
        discovered_raw = json.dumps(discovered)
        assert "sessionId" not in discovered_raw and "ses_" not in discovered_raw
        assert "acp-session-test" not in discovered_raw
        loaded = OrchestratorMock.tool_output("h3-load")
        assert loaded["ok"] is True and loaded["session"]["alias"] == "Birch"
        assert PERSISTED_SESSION_ID not in json.dumps(loaded)
        retagged = OrchestratorMock.tool_output("h3-retag")
        assert retagged == {"ok": True, "previous": "Birch", "alias": "Rowan"}
        assert OrchestratorMock.tool_output("h3-close")["alias"] == "Rowan"
        remaining = OrchestratorMock.tool_output("h3-list")
        assert [item["alias"] for item in remaining] == ["Juniper"]
        assert "Rowan" not in json.dumps(remaining)
        projects = OrchestratorMock.tool_output("h3-projects")
        assert projects["ok"] is True and projects["projects"] == [
            {"directory": ".", "has_agents_md": True, "has_git": False, "origin": None},
            {
                "directory": "honeydozer", "has_agents_md": True, "has_git": True,
                "origin": "https://github.com/rndmcnlly/honeydozer.git",
            },
        ], projects
        assert "ghp_" not in json.dumps(projects)
        dozer = OrchestratorMock.tool_output("h3-dozer")
        assert dozer["ok"] is True and dozer["session"]["directory"] == "honeydozer", dozer
        news = [m["params"]["cwd"] for m in mocks.acp_seen if m.get("method") == "session/new"]
        assert news == ["/home/daytona", "/home/daytona/honeydozer"], news
        ghost = OrchestratorMock.tool_output("h3-ghost")
        assert ghost["ok"] is False and "does not exist" in ghost["error"], ghost
        escape = OrchestratorMock.tool_output("h3-escape")
        assert escape["ok"] is False and "inside the Workspace" in escape["error"], escape
        assert any("test -d" in command and "honeydozer" in command for command in DaytonaMock.executed)

        # Handoff 4: farewell and end_call in one response; hangup follows the farewell.
        await mocks.send_live(transcript("input", "Thanks, hang up now.", 9000, 10000))
        await mocks.send_live(delegation("del-4", 10050))
        farewell = await next_matching(mocks.live_messages, appended("commentary", FAREWELL_SAY))
        assert farewell["delegation_id"] == "del-4"
        await wait_for(lambda: any(path == "/live/sessions/live-test/hangup" for path, _ in DaytonaMock.openai_requests), 10)
        assert OrchestratorMock.tool_output("h4-end")["status"] == "ending call"

        # Between calls: the provider closes the sideband, which ends the voice session and
        # writes the next welcome; the Agent asks for permission while nobody is on the line;
        # then the Agent Connection drops.
        await mocks.live.close()
        await wait_for(lambda: OrchestratorMock.greeting_requests, 10)
        greeting_request = OrchestratorMock.greeting_requests[-1]
        assert greeting_request["tools"] and greeting_request["model"] == "gpt-6.1-sol"
        assert "call_ended" in json.dumps(greeting_request["input"])
        await mocks.acp.send(json.dumps({
            "jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission",
            "params": {
                "sessionId": "acp-session-test",
                "toolCall": {"title": "Run the test suite"},
                "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"}],
            },
        }))
        await asyncio.sleep(0.5)
        # As in production, the Agent has since associated Juniper with a project directory,
        # so resuming from the recorded home directory fails until Dogwalk repairs it.
        mocks.session_cwds["acp-session-test"] = "/home/daytona/honeydozer"
        await mocks.acp.close()
        await asyncio.sleep(0.3)

        # Second call: a returning caller with verified caller ID. The Agent now advertises
        # resume, so the reconnect restores Juniper without replaying its history.
        mocks.advertise_resume = True
        mocks.live_connected.clear()
        body, webhook_identity, signature = signed_webhook("CA_VOICE_TEST2", "live-test-2")
        assert await asyncio.to_thread(post_webhook, body, webhook_identity, signature) == 204
        await asyncio.wait_for(mocks.live_connected.wait(), 8)
        opening = await next_matching(mocks.live_messages, appended("commentary", RETURN_OPENING))
        assert opening["delegation_id"] is None
        def resume_of(session_id: str):
            return lambda item: item.get("method") == "session/resume" and item["params"]["sessionId"] == session_id

        restored = await next_matching(mocks.acp_messages, resume_of("acp-session-test"))
        assert without_meta(restored) == {"sessionId": "acp-session-test", "cwd": "/home/daytona", "mcpServers": []}
        await next_matching(mocks.acp_messages, lambda item: item.get("method") == "session/list")
        repaired = await next_matching(mocks.acp_messages, resume_of("acp-session-test"))
        assert repaired["params"]["cwd"] == "/home/daytona/honeydozer", repaired
        dozer_restored = await next_matching(mocks.acp_messages, resume_of("acp-session-honeydozer"))
        assert without_meta(dozer_restored) == {
            "sessionId": "acp-session-honeydozer", "cwd": "/home/daytona/honeydozer", "mcpServers": [],
        }
        assert not any(m.get("method") == "session/load" for m in mocks.acp_seen)
        assert mocks.acp_connections == 2 and DaytonaMock.preview_reads == 1, (
            "the ACP preview URL was not reused", mocks.acp_connections, DaytonaMock.preview_reads,
        )
        await wait_for(lambda: latest_user_text().count("[harness event] agent_connected") >= 2, 10)
        second_start = next(
            str(item["content"]) for item in OrchestratorMock.requests[-1][1]["input"]
            if item.get("role") == "user" and RETURN_OPENING in str(item["content"])
        )
        assert "first_call" not in second_start and "[harness event] agent_connecting" in second_start
        assert '"stir_verstat":"TN-Validation-Passed-A"' in second_start

        # Keypad conventions, all handled without the Orchestrator.
        await mocks.send_live(dtmf("1"))
        status = await next_matching(mocks.live_messages, lambda item: item.get("type") == "session.commentary.append" and "Juniper" in item.get("content", ""))
        assert status["content"].startswith("Juniper is resting") and status["delegation_id"] is None, status
        await mocks.send_live(dtmf("#"))
        await next_matching(mocks.live_messages, of_type("session.output_audio.fixture.play"))
        stop = await next_matching(mocks.live_messages, of_type("session.instructions.append"))
        assert "pressed pound" in stop["content"]
        await mocks.send_live(transcript("output", "The parser handles nested quotes now.", 20000, 22000))
        await mocks.send_live(dtmf("0"))
        repeat = await next_matching(mocks.live_messages, lambda item: item.get("type") == "session.commentary.append" and "word for word" in item.get("content", ""))
        assert "The parser handles nested quotes now." in repeat["content"]

        # A turn that fails mid-command. Its command is named by phase, never by its text; steps
        # failing in a burst make one beat that says the turn goes on; and once the turn has
        # failed its command is closed, so nothing about it is still running.
        await mocks.send_live(transcript("input", "Have Juniper check the repository.", 22500, 23100))
        await mocks.send_live(delegation("del-f", 23150))
        await wait_for(lambda: "Juniper is working with Git." in beats(), 10)
        await wait_for(lambda: "3 steps in a row failed for Juniper, the latest while looking something up on the web; its turn is still going." in beats(), 10)
        await wait_for(lambda: any(item.get("type") == "function_call_output" and item.get("call_id") == "f-inspect" for _, payload in OrchestratorMock.requests for item in payload["input"]), 15)
        failed_at = len(beats())
        assert "git status" not in beats() and "--short" not in beats() and "(" not in beats(), beats()
        assert '"followed":true' in OrchestratorMock.user_message("turn_failed")
        failed = OrchestratorMock.tool_output("f-inspect")
        assert failed["turn_state"] == "failed" and "SseError" in failed["failure"], failed
        assert [(c["kind"], c["status"]) for c in failed["recent_tool_calls"]] == [("execute", "failed")] + [("fetch", "failed")] * 3, failed
        await asyncio.sleep(4)
        assert not re.search(r"running|working with|still|failed for", beats()[failed_at:]), beats()[failed_at:]

        # An out-of-memory kill reaches the Orchestrator as a sayable reason, and the
        # Agent Connection comes back.
        await mocks.send_live(transcript("input", "Have Juniper install the dependencies.", 23500, 24100))
        await mocks.send_live(delegation("del-o", 24150))
        await wait_for(lambda: any(item.get("type") == "function_call_output" and item.get("call_id") == "o-inspect" for _, payload in OrchestratorMock.requests for item in payload["input"]), 15)
        unavailable = OrchestratorMock.user_message('"agent_termination":"out_of_memory"')
        assert OOM_SENTENCE in unavailable and '"failed_turns":["Juniper"]' in unavailable, unavailable
        killed = OrchestratorMock.tool_output("o-inspect")
        assert "ServerUnavailable" in killed["failure"] and OOM_SENTENCE in killed["failure"], killed
        await wait_for(lambda: latest_user_text().count("[harness event] agent_connected") >= 3, 15)

        # Star opens the System Menu: muted input, a prompt clip, star goes back. Status and
        # sound style are no longer in it.
        await mocks.send_live(dtmf("*"))
        await next_matching(mocks.live_messages, of_type("session.input_audio.mute"))
        await next_matching(mocks.live_messages, of_type("session.output_audio.fixture.play"))
        await mocks.send_live(dtmf("*"))
        await next_matching(mocks.live_messages, of_type("session.input_audio.unmute"))
        # Menu one asks for a second press before restarting; anything else backs out.
        await mocks.send_live(dtmf("*"))
        await next_matching(mocks.live_messages, of_type("session.input_audio.mute"))
        await mocks.send_live(dtmf("1"))
        await mocks.send_live(dtmf("2"))
        await next_matching(mocks.live_messages, of_type("session.input_audio.unmute"))

        # Other digits still reach the Orchestrator with their conversational context.
        await mocks.send_live(dtmf("7"))
        await next_matching(mocks.live_messages, appended("commentary", KEYPAD_SAY))
        await run_invitations(mocks)

        # Sound style is a voice tool: it stores the preference and previews the opening sound.
        await mocks.send_live(dtmf("6"))
        await next_matching(mocks.live_messages, of_type("session.output_audio.fixture.play"), 15)
        await wait_for(lambda: "k6-style" in {item.get("call_id") for _, payload in OrchestratorMock.requests for item in payload["input"] if item.get("type") == "function_call_output"}, 10)

        # Pairing a browser (pair_browser, asked for by voice): the tones carry a Pairing Code, a
        # browser claims it, the Voice Frontend names the browser, and a press of one accepts.
        # Then the browser reads status and files.
        async def next_pairing_code() -> str:
            async with asyncio.timeout(20):
                while True:
                    played = await next_matching(mocks.live_messages, of_type("session.output_audio.fixture.play"))
                    if code := decode_pairing_tones(played["audio"]):
                        return code

        # The first tones go unclaimed: a page reports that it heard them but could not decode
        # them, and about ten seconds later the Orchestrator hears pairing_unclaimed with that
        # count, answers, and plays them again.
        await mocks.send_live(dtmf("8"))
        unclaimed_code = await next_pairing_code()
        assert (await asyncio.to_thread(pair_http, "POST", "/pair/decode-failed", {"stage": "ecc_failed", "padding": "x" * 2000}))[0] == 413
        assert (await asyncio.to_thread(pair_http, "POST", "/pair/decode-failed", {"stage": "guess"}))[0] == 400
        failure = {"stage": "ecc_failed", "baud": 8, "seconds": 4.1, "symbols_heard": 25, "erasures": 4}
        assert (await asyncio.to_thread(pair_http, "POST", "/pair/decode-failed", failure))[0] == 204
        await wait_for(lambda: "[harness event] pairing_unclaimed" in latest_user_text(), 15)
        unclaimed = OrchestratorMock.user_message("[harness event] pairing_unclaimed")
        assert '"baud":8' in unclaimed and '"decode_failures":1' in unclaimed, unclaimed
        assert OrchestratorMock.tool_output("k8-pair")["ok"] is True
        await next_matching(mocks.live_messages, appended("commentary", REPLAY_SAY))
        code = await next_pairing_code()
        assert code != unclaimed_code
        status, _ = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": unclaimed_code})
        assert status == 404, "a new attempt retires the previous Pairing Code"
        status, claim = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": code}, {"user-agent": CHROME_MAC})
        assert status == 200 and claim["ticket"].startswith(identity + "."), (status, claim)
        named = await next_matching(mocks.live_messages, lambda m: m.get("type") == "session.commentary.append" and "pairing tones" in m.get("content", ""))
        assert "Chrome on a Mac" in named["content"] and "press one" in named["content"]
        status, _ = await asyncio.to_thread(pair_http, "POST", "/pair/claim", {"code": code})
        assert status == 404, "a Pairing Code works once"
        ticket = {"authorization": f"Bearer {claim['ticket']}"}
        assert (await asyncio.to_thread(pair_http, "GET", "/pair/claim/status", None, ticket))[1] == {"state": "waiting"}
        # Keys count again only once echoes of the tones have cleared (half a second).
        await asyncio.sleep(0.6)
        await mocks.send_live(dtmf("1"))
        await next_matching(mocks.live_messages, lambda m: m.get("type") == "session.commentary.append" and "browser is paired" in m.get("content", ""))
        accepted = (await asyncio.to_thread(pair_http, "GET", "/pair/claim/status", None, ticket))[1]
        assert accepted["state"] == "accepted" and accepted["token"].startswith(identity + ".")
        assert (await asyncio.to_thread(pair_http, "GET", "/pair/claim/status", None, ticket))[1] == {"state": "accepted"}, "the token is handed out once"
        token = accepted["token"]
        # The page carries its build identity and sends it when it connects.
        page = await asyncio.to_thread(lambda: urllib.request.urlopen(f"http://127.0.0.1:{WORKER_PORT}/pair", timeout=10).read().decode())
        build = re.search(r'const PAIR_BUILD = "([0-9a-f]{8})";', page).group(1)
        assert "__PAIR_BUILD__" not in page
        async with connect(f"ws://127.0.0.1:{WORKER_PORT}/pair/ws?for={identity}") as browser:
            await browser.send(json.dumps({"type": "auth", "token": token, "build": build}))
            hello = await recv_type(browser, "hello")
            assert hello["device"] == "Chrome on a Mac" and hello["idle_expires_at"] > time.time() * 1000
            assert hello["build"] == build and hello["stale"] is False, hello
            snapshot = await recv_type(browser, "status")
            assert any(item["alias"] == "Juniper" for item in snapshot["sessions"]) and snapshot["sandbox"]["call_attached"]
            # The live feed starts with a snapshot of what the call has said and done so far.
            feed = await recv_type(browser, "feed")
            assert feed.get("snapshot") and any(e["role"] == "system" and e.get("lane") == "keypad" and e["text"] == "keypad 8" for e in feed["entries"]), feed["entries"][-5:]
            assert any(e["role"] == "orchestrator" and e.get("lane") == "say" and "pairing tones" in e["text"] for e in feed["entries"])
            await browser.send(json.dumps({"type": "list", "id": 1, "path": ""}))
            listing = await recv_type(browser, "list")
            assert listing["id"] == 1 and listing["path"] == "/home/daytona"
            assert [entry["name"] for entry in listing["entries"]] == ["parser", "REPORT.md"], "directories first"
            await browser.send(json.dumps({"type": "read", "id": 2, "path": "REPORT.md"}))
            report = await recv_type(browser, "file")
            assert report["text"] == REPORT_TEXT and not report["truncated"]
            await browser.send(json.dumps({"type": "read", "id": 3, "path": "../../etc/passwd"}))
            escaped = await recv_type(browser, "error")
            assert escaped["id"] == 3 and "/home/daytona" in escaped["error"]
            await browser.send(json.dumps({"type": "ping"}))
            await recv_type(browser, "pong")
            # The Orchestrator learns the page is open, and can put a file on it.
            await wait_for(lambda: any("browser_connected" in str(item.get("content")) for _, payload in OrchestratorMock.requests for item in payload["input"]), 10)
            await mocks.send_live(dtmf("9"))
            shown = await recv_type(browser, "show", 15)
            assert shown["uri"] == "dogwalk:file//home/daytona/REPORT.md" and shown["note"] == "the report", shown
            # What the page shows is sensed without changing anything.
            await browser.send(json.dumps({"type": "viewing", "view": "files", "path": "/home/daytona/REPORT.md", "kind": "file"}))
            await asyncio.sleep(0.3)
            # A tab loaded before a deploy: one with another build is told to reload itself; one
            # from before builds existed cannot, so the Orchestrator hears it must ask the caller.
            async with connect(f"ws://127.0.0.1:{WORKER_PORT}/pair/ws?for={identity}") as newer:
                await newer.send(json.dumps({"type": "auth", "token": token, "build": "00000000"}))
                told = await recv_type(newer, "hello")
                assert told["stale"] is True and told["build"] == build, told
            await wait_for(lambda: any('browser_page_stale: {"reloads_itself":true}' in str(item.get("content")) for _, payload in OrchestratorMock.requests for item in payload["input"]), 10)
            stale = await connect(f"ws://127.0.0.1:{WORKER_PORT}/pair/ws?for={identity}")
            await stale.send(json.dumps({"type": "auth", "token": token}))
            assert (await recv_type(stale, "hello"))["stale"] is True
            await wait_for(lambda: any('browser_page_stale: {"reloads_itself":false}' in str(item.get("content")) for _, payload in OrchestratorMock.requests for item in payload["input"]), 10)
            await mocks.send_live(dtmf("4"))
            await wait_for(lambda: any(item.get("type") == "function_call_output" and item.get("call_id") == "k4-state" for _, payload in OrchestratorMock.requests for item in payload["input"]), 15)
            state = OrchestratorMock.tool_output("k4-state")
            browser_state = state["paired_browser"]
            assert browser_state["paired"] and browser_state["page_open"] and browser_state["device"] == "Chrome on a Mac", browser_state
            assert browser_state["showing_now"] == {"view": "files", "path": "/home/daytona/REPORT.md"}, browser_state
            assert browser_state["page_stale"] is True, browser_state
            assert state["earcon_style"] == "tonal", state
            assert state["context_tokens"] == 1 and state["memory"] is None, state
            # The Orchestrator draws a board; the caller answers it on the page; it hears the answer.
            await mocks.send_live(dtmf("5"))
            board = (await recv_type(browser, "board", 15))["board"]
            assert board["program"] == BOARD_PROGRAM and board["status"] == "open" and board["note"] == "pick one", board
            # Images are captured when drawn: a manifest of content hashes, never bytes in the program.
            svg_hash = hashlib.sha256(SVG_BYTES).hexdigest()
            assert board["images"]["/home/daytona/diagram.svg"] == {"hash": svg_hash, "type": "image/svg+xml", "bytes": len(SVG_BYTES)}, board["images"]
            assert "too large" in board["images"]["/home/daytona/big.png"]["error"], board["images"]
            await wait_for(lambda: any(item.get("type") == "function_call_output" and item.get("call_id") == "k5-board" for _, payload in OrchestratorMock.requests for item in payload["input"]), 15)
            drawn = OrchestratorMock.tool_output("k5-board")
            assert "page_stale" in drawn, "draw_on_whiteboard warns of the stale page"
            assert [item["path"] for item in drawn["images_not_captured"]] == ["/home/daytona/big.png"] and "WebP" in drawn["images_not_captured"][0]["error"], drawn
            assert svg_hash not in json.dumps(drawn), "no hash reaches the Orchestrator"
            # The stale page cannot render Image; its error says the page is out of date.
            await stale.send(json.dumps({"type": "board_error", "id": board["id"], "error": "line 2: unknown component Image (left out)"}))
            await wait_for(lambda: any("whiteboard_error" in str(item.get("content")) and "out of date" in str(item.get("content")) for _, payload in OrchestratorMock.requests for item in payload["input"]), 10)
            await stale.close()
            await browser.send(json.dumps({"type": "board_answer", "id": board["id"], "answer": {"button": "Send", "values": {"approach": "Refactor"}, "note": ""}}))
            answered = (await recv_type(browser, "board"))["board"]
            assert answered["status"] == "answered" and answered["answer"]["values"] == {"approach": "Refactor"}, answered
            await wait_for(lambda: any("whiteboard_answer" in str(item.get("content")) and "Refactor" in str(item.get("content")) for _, payload in OrchestratorMock.requests for item in payload["input"]), 15)
            # The Orchestrator compacts after the answer: an over-long Memory is refused, then the
            # next request leads with the pinned Memory and has shed the first call, with every
            # function call still paired with its output.
            await wait_for(lambda: any(item.get("call_id") == "cc-memory" and item.get("type") == "function_call_output" for _, payload in OrchestratorMock.requests for item in payload["input"]), 15)
            refused_memory = OrchestratorMock.tool_output("cc-long")
            assert refused_memory["ok"] is False and "condense" in refused_memory["error"], refused_memory
            compacted = next(payload["input"] for _, payload in OrchestratorMock.requests if any(item.get("call_id") == "cc-memory" and item.get("type") == "function_call_output" for item in payload["input"]))
            assert compacted[0] == {"role": "developer", "content": f"[memory] {MEMORY_TEXT}"}, compacted[0]
            assert not any(item.get("call_id") == "h1-create" for item in compacted), "the first call survived compaction"
            assert compacted[1].get("role") == "user", compacted[1]
            calls_in = {item["call_id"] for item in compacted if item.get("type") == "function_call"}
            outputs_in = {item["call_id"] for item in compacted if item.get("type") == "function_call_output"}
            assert calls_in == outputs_in and "cc-memory" in calls_in, (calls_in ^ outputs_in)
            assert (await recv_type(browser, "memory"))["memory"]["text"] == MEMORY_TEXT
            diagnostics = next(item for item in await asyncio.to_thread(conversations) if item.get("memory"))
            assert diagnostics["memory"]["text"] == MEMORY_TEXT and diagnostics["memory_versions"][-1]["chars"] == len(MEMORY_TEXT), diagnostics["memory_versions"]
            assert diagnostics["context_archives"][-1]["reason"] == "compaction", diagnostics["context_archives"]
            assert not any(item.get("call_id") == "h1-create" for item in diagnostics["orchestrator_context"])
            # A second answer to the same board is ignored.
            await browser.send(json.dumps({"type": "board_answer", "id": board["id"], "answer": {"values": {"approach": "Quick fix"}}}))
            # Media viewers fetch raw bytes with the credential in a header, never the URL.
            bearer = {"authorization": f"Bearer {token}"}
            status, headers, body = await asyncio.to_thread(pair_raw, "/pair/file?path=%2Fhome%2Fdaytona%2FREPORT.md", bearer)
            assert status == 200 and body == REPORT_TEXT.encode(), (status, body[:80])
            assert headers.get("Content-Type", headers.get("content-type")) == "application/octet-stream"
            assert "sandbox" in headers.get("Content-Security-Policy", headers.get("content-security-policy", ""))
            assert (await asyncio.to_thread(pair_raw, "/pair/file?path=%2Fetc%2Fpasswd", bearer))[0] == 400
            assert (await asyncio.to_thread(pair_raw, "/pair/file?path=%2Fhome%2Fdaytona%2FREPORT.md", {"authorization": f"Bearer {identity}.{'0' * 48}"}))[0] == 401
            # Whiteboard images: the type comes from the bytes, an SVG opened alone runs and loads
            # nothing, and mismatches, oversized downloads, links out of the Workspace, and URLs are refused.
            def image(path: str, headers: dict = bearer) -> tuple[int, dict, bytes]:
                status, raw, body = pair_raw(f"/pair/file?as=image&path={urllib.parse.quote(path, safe='')}", headers)
                return status, {key.lower(): value for key, value in raw.items()}, body
            status, headers, body = await asyncio.to_thread(image, "chart.png")
            assert status == 200 and body == PNG_BYTES and headers.get("content-type") == "image/png", (status, headers)
            assert headers.get("x-content-type-options") == "nosniff" and "no-store" in headers.get("cache-control", "")
            status, headers, body = await asyncio.to_thread(image, "/home/daytona/diagram.svg")
            assert status == 200 and body == SVG_BYTES and headers.get("content-type") == "image/svg+xml", (status, headers)
            csp = headers.get("content-security-policy", "")
            assert "default-src 'none'" in csp and "style-src 'unsafe-inline'" in csp and "img-src data:" in csp, csp
            status, _, body = await asyncio.to_thread(image, "notes.png")
            assert status == 415 and b"not a PNG" in body, (status, body)
            assert (await asyncio.to_thread(image, "huge.png"))[0] == 413, "the cap counts downloaded bytes"
            for oversized in ["wide.png", "pixels.png"]:
                status, _, body = await asyncio.to_thread(image, oversized)
                assert status == 415 and b"megapixels" in body, (oversized, status, body)
            status, _, body = await asyncio.to_thread(image, "escape.png")
            assert status == 403 and b"outside" in body, (status, body)
            assert (await asyncio.to_thread(image, "missing.png"))[0] == 404
            for url in ["https://example.com/a.png", "data:image/png;base64,AAAA", "//example.com/a.png"]:
                status, _, body = await asyncio.to_thread(image, url)
                assert status == 400 and b"never a URL" in body, (url, status, body)
            # A file named as an image is sniffed in the file viewer too; images still need a pairing.
            assert (await asyncio.to_thread(pair_raw, "/pair/file?path=notes.png", bearer))[0] == 415
            assert (await asyncio.to_thread(image, "chart.png", {"authorization": f"Bearer {identity}.{'0' * 48}"}))[0] == 401
            assert not any("example.com" in command for command in DaytonaMock.executed)
            # A board's images come from its captured assets, never the Sandbox: with the file
            # gone, the asset still serves, typed and confined like an image read.
            def asset(digest: str, headers: dict = bearer) -> tuple[int, dict, bytes]:
                status, raw, body = pair_raw(f"/pair/asset/{digest}", headers)
                return status, {key.lower(): value for key, value in raw.items()}, body
            removed = IMAGE_FILES.pop("diagram.svg")
            try:
                assert (await asyncio.to_thread(image, "diagram.svg"))[0] == 404
                status, headers, body = await asyncio.to_thread(asset, svg_hash)
                assert status == 200 and body == SVG_BYTES and headers.get("content-type") == "image/svg+xml", (status, headers)
                assert headers.get("x-content-type-options") == "nosniff" and "no-store" in headers.get("cache-control", "")
                assert "default-src 'none'" in headers.get("content-security-policy", "") and "sandbox" in headers.get("content-security-policy", "")
            finally:
                IMAGE_FILES["diagram.svg"] = removed
            assert (await asyncio.to_thread(asset, hashlib.sha256(PNG_BYTES).hexdigest()))[0] == 404, "only hashes a board refers to"
            assert (await asyncio.to_thread(asset, svg_hash, {"authorization": f"Bearer {identity}.{'0' * 48}"}))[0] == 401
            assert (await asyncio.to_thread(asset, "../file"))[0] == 404
            # Five newer boards retire the first: its asset is collected and no longer served.
            await mocks.send_live(dtmf("A"))
            for _ in range(5):
                await recv_type(browser, "board", 15)
            await wait_for(lambda: any(item.get("call_id") == "kA-evict-5" and item.get("type") == "function_call_output" for _, payload in OrchestratorMock.requests for item in payload["input"]), 15)
            icon = OrchestratorMock.tool_output("kA-evict-5")
            assert icon["ok"] and "images_not_captured" not in icon, icon
            assert (await asyncio.to_thread(asset, svg_hash))[0] == 404, "an evicted board's asset is gone"
            status, headers, body = await asyncio.to_thread(asset, hashlib.sha256(PNG_BYTES).hexdigest())
            assert status == 200 and body == PNG_BYTES and headers.get("content-type") == "image/png", "one asset shared by five boards"
            assert "whiteboard.assets_collected" in await asyncio.to_thread(call_activity, "CA_VOICE_TEST2")
            # An HTML report's Workspace images are inlined when it is read for the page.
            await mocks.send_live(dtmf("B"))
            assert (await recv_type(browser, "show", 15))["uri"] == "dogwalk:file//home/daytona/report.html"
            await browser.send(json.dumps({"type": "read", "id": 9, "path": "/home/daytona/report.html"}))
            html = (await recv_type(browser, "file"))["text"]
            assert f'src="data:image/png;base64,{base64.b64encode(PNG_BYTES).decode()}"' in html and "srcset" not in html, html[:400]
            assert 'src="https://example.com/a.png"' in html and 'src="data:image/gif;base64,R0lG"' in html, html
            assert 'src="/etc/x.png" alt="Outside (image not shown: only files under /home/daytona can be read)"' in html, html
            # Human Takeover: a terminal launch yields a one-time ticket in a URL fragment.
            await browser.send(json.dumps({"type": "launch", "kind": "terminal", "id": 7}))
            launch = await recv_type(browser, "launch")
            assert launch["id"] == 7 and f"/pair/terminal#t={identity}." in launch["url"], launch
            ticket_credential = launch["url"].split("#t=", 1)[1]
            terminal_url = f"ws://127.0.0.1:{WORKER_PORT}/pair/terminal/ws?for={identity}"
            async with connect(terminal_url) as terminal:
                await terminal.send(json.dumps({"type": "auth", "ticket": ticket_credential, "cols": 100, "rows": 30}))
                ready = await recv_type(terminal, "ready")
                terminal_token = ready["token"]
                assert terminal_token.startswith(identity + ".") and terminal_token != ticket_credential
                # The mock toolbox has no PTY, so the relay reports why and closes.
                notice = await recv_type(terminal, "notice")
                assert "terminal" in notice["text"], notice
            async with connect(terminal_url) as again:
                await again.send(json.dumps({"type": "auth", "ticket": ticket_credential}))
                assert "expired" in (await recv_type(again, "error"))["error"], "a launch ticket works once"
            async with connect(terminal_url) as reload:
                await reload.send(json.dumps({"type": "auth", "token": terminal_token}))
                assert (await recv_type(reload, "ready"))["token"] == terminal_token, "a reload reconnects with its token"
            # A desktop launch reports why it could not start (the mock has no computer use).
            await browser.send(json.dumps({"type": "launch", "kind": "desktop", "id": 8}))
            refused = await recv_type(browser, "error")
            assert refused["id"] == 8 and "desktop" in refused["error"], refused
        # A wrong secret for the right identity is refused and closed with 4001.
        async with connect(f"ws://127.0.0.1:{WORKER_PORT}/pair/ws?for={identity}") as stranger:
            await stranger.send(json.dumps({"type": "auth", "token": identity + "." + "0" * 48}))
            await recv_type(stranger, "error")
            try:
                await stranger.recv()
            except ConnectionClosed as closed:
                assert closed.rcvd and closed.rcvd.code == 4001
        # Unpairing ends the credential: the next connection is refused.
        async with connect(f"ws://127.0.0.1:{WORKER_PORT}/pair/ws?for={identity}") as browser:
            await browser.send(json.dumps({"type": "auth", "token": token}))
            await recv_type(browser, "hello")
            await browser.send(json.dumps({"type": "unpair"}))
            await recv_type(browser, "unpaired")
        # Unpairing ends what the browser launched: its terminal token no longer works.
        async with connect(f"ws://127.0.0.1:{WORKER_PORT}/pair/terminal/ws?for={identity}") as revoked:
            await revoked.send(json.dumps({"type": "auth", "token": terminal_token}))
            await recv_type(revoked, "error")
        async with connect(f"ws://127.0.0.1:{WORKER_PORT}/pair/ws?for={identity}") as browser:
            await browser.send(json.dumps({"type": "auth", "token": token}))
            await recv_type(browser, "error")

        # A theme switch bounces the call: the new theme's greeting is written before the
        # GPT-Live leg hangs up, then the same CallSid returns with the new voice and conceit.
        greetings = len(OrchestratorMock.greeting_requests)
        await mocks.send_live(dtmf("3"))
        await next_matching(mocks.live_messages, appended("commentary", SWITCH_SAY))
        await wait_for(lambda: len(OrchestratorMock.greeting_requests) > greetings, 10)
        switch_instructions = OrchestratorMock.greeting_requests[-1]["instructions"]
        assert "The survey (Survey theme)" in switch_instructions and "switched to this theme mid-call" in switch_instructions
        assert "at the high rung" in switch_instructions, switch_instructions
        assert OrchestratorMock.greeting_requests[-1]["input"][0]["content"] == f"[memory] {MEMORY_TEXT}"
        await wait_for(lambda: any(path == "/live/sessions/live-test-2/hangup" for path, _ in DaytonaMock.openai_requests), 10)
        assert OrchestratorMock.tool_output("k3-theme")["bouncing"] is True
        await mocks.live.close()
        mocks.live_connected.clear()
        body, webhook_identity, signature = signed_webhook("CA_VOICE_TEST2", "live-test-3")
        assert await asyncio.to_thread(post_webhook, body, webhook_identity, signature) == 204
        await asyncio.wait_for(mocks.live_connected.wait(), 8)
        rejoined = next(payload for path, payload in DaytonaMock.openai_requests if path == "/live/sessions/live-test-3/accept")
        assert rejoined["session"]["audio"]["output"]["voice"] == "sage", rejoined["session"]["audio"]
        assert "The survey (Survey theme)" in rejoined["session"]["instructions"]
        await next_matching(mocks.live_messages, appended("commentary", f"Dogwalk. {NEXT_GREETING}"))
        # The register carries across the bounce as unspoken context for the new voice.
        await next_matching(mocks.live_messages, appended("thinking", "The caller had the register at high; start there."))
        await mocks.send_live(dtmf("2"))
        await wait_for(lambda: '"call_id": "k2-dramaturgy"' in json.dumps([p["input"] for _, p in OrchestratorMock.requests]), 10)
        dramaturgy = OrchestratorMock.tool_output("k2-dramaturgy")["guidance"]
        assert "Planetary Romance" in dramaturgy and "Survey theme" in dramaturgy, dramaturgy

        # start_voice_donation leaves a donation handoff and ends the GPT-Live leg, so the Dial
        # action sends the call to the Voice Donation flow.
        hangups = lambda: sum(path == "/live/sessions/live-test-3/hangup" for path, _ in DaytonaMock.openai_requests)
        await mocks.send_live(dtmf("C"))
        await wait_for(lambda: hangups() == 1, 10)
        assert OrchestratorMock.tool_output("kC-donate")["status"] == "handing the call to voice donation"
        assert await asyncio.to_thread(d1, "SELECT action FROM call_handoffs WHERE call_sid = 'CA_VOICE_TEST2'") == [{"action": "donation"}]
        status, body = await asyncio.to_thread(post, DEFAULT_LOCAL, "/voice/after-live", {
            "From": TEST_PHONE, "CallSid": "CA_VOICE_TEST2"}, LOCAL_TOKEN)
        assert status == 200 and '<Redirect method="POST">/voice/donation</Redirect>' in body, body

        # start_challenge_practice leaves a practice handoff the same way; practice itself
        # returns the call to /voice, which reconnects the Voice Agent.
        await mocks.send_live(dtmf("D"))
        await wait_for(lambda: hangups() == 2, 10)
        assert OrchestratorMock.tool_output("kD-practice")["status"] == "handing the call to challenge practice"
        assert await asyncio.to_thread(d1, "SELECT action FROM call_handoffs WHERE call_sid = 'CA_VOICE_TEST2'") == [{"action": "practice"}]
        status, body = await asyncio.to_thread(post, DEFAULT_LOCAL, "/voice/after-live", {
            "From": TEST_PHONE, "CallSid": "CA_VOICE_TEST2"}, LOCAL_TOKEN)
        assert status == 200 and '<Redirect method="POST">/voice/practice</Redirect>' in body, body

        # Star then two hands the call to the recovery menu, which ends the GPT-Live leg.
        await mocks.send_live(dtmf("*"))
        await next_matching(mocks.live_messages, of_type("session.input_audio.mute"))
        await mocks.send_live(dtmf("2"))
        await wait_for(lambda: hangups() == 3, 10)
        events = await asyncio.to_thread(call_activity, "CA_VOICE_TEST2")
        # Drain the operator API by insertion cursor, independently of event-time
        # sorting and without pairing to either caller's browser.
        pages, observed, cursor = 0, [], 0
        while True:
            page = await asyncio.to_thread(operator_timing_page, "CA_VOICE_TEST", cursor)
            pages += 1
            assert len(page["events"]) <= 500
            observed.extend(page["events"])
            assert page["cursor"] >= cursor
            cursor = page["cursor"]
            if not page["has_more"]:
                break
            assert pages < 30, "operator timing pagination did not terminate"
        assert observed and len({row["id"] for row in observed}) == len(observed)
        assert [row["id"] for row in observed] == sorted(row["id"] for row in observed)
        turn = next(row for row in observed if row["event"] == "acp.turn.stopped")
        assert turn["detail"]["acp_session_id"] and turn["detail"]["ms"] >= 0
        for expected in ("menu.opened", "menu.closed", "menu.restart.requested", "menu.recovery", "voice.keypad.stop", "voice.keypad.status",
                         "voice.earcons", "voice.theme", "pairing.started", "pairing.unclaimed", "pairing.claimed", "pairing.accepted", "pairing.page_stale"):
            assert expected in events, (expected, events)
        handoff_rows = OrchestratorMock.user_message('"key":"7"')
        assert '"key":"1"' not in handoff_rows and '"key":"*"' not in handoff_rows

        # Themes and phone numbers stay above ACP: no Agent prompt uses the theme's words or
        # any number Access Control knows.
        prompts = [m for m in mocks.acp_seen if m.get("method") == "session/prompt"]
        assert prompts
        for message in prompts:
            for block in message["params"]["prompt"]:
                text = block.get("text", "").lower()
                assert not any(word in text for word in THEME_LEXICON), text
                digits = re.sub(r"\D", "", text)
                assert not any(number[-10:] in digits for number in TEST_NUMBERS), text

        # Nothing with a URL reached the caller-facing lane.
        spoken = [m for m in mocks.seen if m.get("type") == "session.commentary.append"]
        assert spoken and not any("http" in m["content"] for m in spoken)

        requests = OrchestratorMock.requests
        assert {step for step, _ in OrchestratorMock.issued} == set(STEP_IDS)
        for headers, payload in requests:
            assert headers.get("authorization") == "Bearer local-openai-token"
            assert payload["model"] == "gpt-6.1-sol"
            assert payload["store"] is False and payload["parallel_tool_calls"] is True
            assert "Session Orchestrator" in payload["instructions"]
            assert "The session model:" in payload["instructions"]
            assert "first-call-tour" in payload["instructions"]
            assert {tool["name"] for tool in payload["tools"]} >= {
                "brief", "say", "create_managed_session", "begin_prompt_turn", "end_call",
                "discover_persisted_sessions", "load_persisted_session", "set_alias",
                "close_managed_session", "list_workspace_projects", "create_invitation", "list_invitations",
                "set_invitation_recipient", "revoke_invitation", "inspect_registration", "start_voice_donation",
                "start_challenge_practice",
            }
            assert "start_voice_donation together in the same response" in payload["instructions"]
            assert "start_challenge_practice together in the same response" in payload["instructions"]
            assert "invite-someone" in payload["instructions"]
            assert "Never create a session in home and tell it to work in another directory" in payload["instructions"]
            assert "from what I know, unverified" in payload["instructions"]
            assert "unless its Prompt Turn is already running or you begin it in the same response" in payload["instructions"]
            assert "browser_page_stale" in payload["instructions"]
            assert not any(name in payload["instructions"] for name in ("Cedar", "Biscuit", "Kappa", "Dozer"))
            assert "rename an existing session only when the caller asks" in payload["instructions"]
            assert "where something is written down, update the Memory" in payload["instructions"]
        # The Orchestrator presents the caller's theme: Park until the switch, Survey after it.
        assert "The walk (Park theme)" in requests[0][1]["instructions"]
        assert "The survey (Survey theme)" in requests[-1][1]["instructions"]
        fed_back = {
            item["call_id"]
            for _, payload in requests
            for item in payload["input"]
            if item.get("type") == "function_call_output"
        }
        assert fed_back == set(STEP_IDS)


def ritual(phone: str, call_sid: str, words: str, confirm: bool = True) -> str:
    """An unregistered caller says invite words and, if they matched, confirms the readback."""
    status, body = post(DEFAULT_LOCAL, "/voice/claim?attempts=1", {
        "From": phone, "CallSid": call_sid, "SpeechResult": words, "Confidence": "0.9",
    }, LOCAL_TOKEN)
    assert status == 200, (status, body)
    if "don't match" in body or not confirm:
        return body
    return confirm_claim(phone, call_sid)


def confirm_claim(phone: str, call_sid: str) -> str:
    status, body = post(DEFAULT_LOCAL, "/voice/confirm?attempts=1", {
        "From": phone, "CallSid": call_sid, "SpeechResult": "yes", "Confidence": "0.9",
    }, LOCAL_TOKEN)
    assert status == 200, (status, body)
    return body


def d1(sql: str) -> list[dict]:
    result = subprocess.run(
        [project_wrangler(), "d1", "execute", "dogwalk", "--local", "--json", "--command", sql],
        cwd=WORKER_DIR, check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)[0]["results"]


def outputs_back(*call_ids: str):
    return lambda: set(call_ids) <= {
        item.get("call_id") for _, payload in OrchestratorMock.requests for item in payload["input"]
        if item.get("type") == "function_call_output"
    }


async def run_invitations(mocks: SocketMocks) -> None:
    """Invitations end to end: tools on the inviter's call, redemption through the ritual."""
    await mocks.send_live(transcript("input", "Invite my friend; he's listening.", 30000, 31500))
    await mocks.send_live(delegation("del-i1", 31600))
    await wait_for(outputs_back("i-say", "i-leak"), 15)
    out = OrchestratorMock.tool_output
    first = out("i1-create")["invitation"]
    assert first["status"] == "active" and first["recipient"] is None and first["expires_in_minutes"] == 24 * 60, first
    assert len(first["words"].split()) == 3 and first["words"].replace(" ", "").isalpha(), first
    # The caller learns who invited them; another User's invitation is not theirs to touch.
    assert out("i1-who") == {
        "ok": True, "handle": CALLER_HANDLE, "registered_days_ago": 0, "via": "invitation", "invited_by": SEED_HANDLE,
    }, out("i1-who")
    assert out("i1-foreign")["ok"] is False and "no such invitation" in out("i1-foreign")["error"]
    # Without a confirmed readback nothing is created; the call is answered with what to read back.
    readback = out("i2-readback")
    assert readback["ok"] is False and "invitation" not in readback, readback
    assert readback["readback"] == {
        "phone": BOUND_PHONE, "spoken": "plus one, five five five, five five five, zero one zero two", "assumed_country_code": True,
    }, readback
    bound = out("i2-create")["invitation"]
    assert bound["recipient"]["phone"] == BOUND_PHONE, bound
    assert out("i3-create")["ok"] is True
    limited = out("i4-limit")
    assert limited["ok"] is False and "3 active invitations" in limited["error"], limited
    abroad = out("i3-abroad")["readback"]
    assert abroad["phone"] == "+442079460958" and abroad["spoken"] == "plus four four two, zero seven nine, four six zero, nine five eight", abroad
    assert out("i3-bare")["ok"] is False and "country code" in out("i3-bare")["error"], out("i3-bare")
    # Invite words and a number in digit groups may be spoken; a number never reaches an Agent.
    assert out("i-say")["ok"] is True, out("i-say")
    assert out("i-leak")["ok"] is False and "phone number" in out("i-leak")["error"], out("i-leak")
    third = out("i3-create")["invitation"]["words"]

    # A claim begins on an unbound invitation, then the inviter binds it elsewhere: the
    # confirmation fails and consumes nothing.
    assert "I heard" in await asyncio.to_thread(ritual, RACE_PHONE, "CA_INV_RACE", third, False)
    await mocks.send_live(transcript("input", "Tie that last one to my other friend's number.", 32000, 33500))
    await mocks.send_live(delegation("del-i2", 33600))
    await wait_for(outputs_back("i3-bind"), 15)
    assert out("i3-bind")["invitation"]["recipient"]["phone"] == REBOUND_PHONE
    assert "no longer available" in await asyncio.to_thread(confirm_claim, RACE_PHONE, "CA_INV_RACE")

    # Unbound: any unregistered phone redeems it, once.
    invitee_handle = spoken_handle(await asyncio.to_thread(ritual, INVITEE_PHONE, "CA_INV_1", first["words"]))
    assert "don't match" in await asyncio.to_thread(ritual, LATE_PHONE, "CA_INV_2", first["words"])
    # Bound: the wrong number cannot even claim it; the right one registers.
    assert "don't match" in await asyncio.to_thread(ritual, REBOUND_PHONE, "CA_INV_3", bound["words"])
    bound_handle = spoken_handle(await asyncio.to_thread(ritual, BOUND_PHONE, "CA_INV_4", bound["words"]))
    assert "don't match" in await asyncio.to_thread(ritual, LATE_PHONE, "CA_INV_5", EXPIRED_WORDS)
    # Two claims on one single-use invitation, confirmed at once: exactly one registers.
    assert "I heard" in await asyncio.to_thread(ritual, LATE_PHONE, "CA_INV_6", FOREIGN_WORDS, False)
    assert "I heard" in await asyncio.to_thread(ritual, RIVAL_PHONE, "CA_INV_7", FOREIGN_WORDS, False)
    raced = await asyncio.gather(
        asyncio.to_thread(confirm_claim, LATE_PHONE, "CA_INV_6"),
        asyncio.to_thread(confirm_claim, RIVAL_PHONE, "CA_INV_7"),
    )
    assert sorted("Registered" in body for body in raced) == [False, True], raced
    winner = LATE_PHONE if "Registered" in raced[0] else RIVAL_PHONE
    winner_handle = spoken_handle(raced[0] if winner == LATE_PHONE else raced[1])

    await mocks.send_live(transcript("input", "Did my friends make it in?", 34000, 35500))
    await mocks.send_live(delegation("del-i3", 35600))
    await wait_for(outputs_back("i-relist"), 15)
    listed = {item["words"]: item for item in out("i-list")["invitations"]}
    assert FOREIGN_WORDS not in listed and set(listed) == {first["words"], bound["words"], third, EXPIRED_WORDS}, listed
    # redeemed_by names the invitee by Handle; redeemed_from is the number that redeemed it.
    assert listed[first["words"]]["status"] == "redeemed" and listed[first["words"]]["redeemed_by"] == invitee_handle
    assert listed[first["words"]]["redeemed_from"]["phone"] == INVITEE_PHONE, listed[first["words"]]
    assert listed[first["words"]]["matched_recipient"] is None and listed[first["words"]]["redeemed_minutes_ago"] == 0
    assert listed[bound["words"]]["redeemed_by"] == bound_handle and listed[bound["words"]]["matched_recipient"] is True
    assert listed[bound["words"]]["redeemed_from"]["phone"] == BOUND_PHONE
    assert listed[third]["status"] == "active" and listed[third]["recipient"]["phone"] == REBOUND_PHONE
    assert listed[EXPIRED_WORDS]["status"] == "expired"
    assert out("i-list")["active"] == 1, out("i-list")
    assert out("i3-revoke")["revoked"] is True and out("i1-hide") | {"note": None} == {
        "ok": True, "words": first["words"], "was": "redeemed", "revoked": False, "note": None,
    }, out("i1-hide")
    assert {item["words"] for item in out("i-relist")["invitations"]} == {bound["words"], EXPIRED_WORDS}
    assert "don't match" in await asyncio.to_thread(ritual, REBOUND_PHONE, "CA_INV_8", third)

    # Hiding and revoking keep the graph: every edge, across generations.
    edges = await asyncio.to_thread(d1, """SELECT inviter.handle AS inviter, invitee.handle AS invitee,
               invitee.home_phone AS home, v.redeemed_from
        FROM invitations v JOIN users inviter ON inviter.id = v.inviter_user_id
        JOIN users invitee ON invitee.id = v.redeemed_by_user_id""")
    edges = [e for e in edges if e["redeemed_from"].startswith("+1555555")]
    assert all(e["home"] == e["redeemed_from"] for e in edges), edges
    assert sorted((e["inviter"], e["invitee"]) for e in edges) == sorted([
        (CALLER_HANDLE, bound_handle), (CALLER_HANDLE, invitee_handle), (SEED_HANDLE, CALLER_HANDLE), (SEED_HANDLE, winner_handle),
    ]), edges
    assert next(e for e in edges if e["invitee"] == winner_handle)["redeemed_from"] == winner
    unredeemed = await asyncio.to_thread(d1, f"SELECT redeemed_by_user_id, redeemed_from, revoked_at, hidden_at FROM invitations WHERE code_words = '{third}'")
    assert unredeemed[0]["redeemed_by_user_id"] is None and unredeemed[0]["redeemed_from"] is None, unredeemed
    assert unredeemed[0]["revoked_at"] and unredeemed[0]["hidden_at"], unredeemed

    # Operator-only graph projection includes hidden history across generations. The
    # intended recipient of an unused Invitation is not presented as a registered invitee.
    state = await asyncio.to_thread(operator_access_state, True)
    invitations = {v["code_words"]: v for v in state["invitations"]}
    assert invitations[first["words"]]["hidden_at"] and invitations[first["words"]]["status"] == "redeemed"
    assert invitations[third]["status"] == "revoked" and invitations[third]["hidden_at"]
    assert invitations[third]["recipient_phone"] == REBOUND_PHONE and invitations[third]["invitee_phone"] is None
    assert invitations[EXPIRED_WORDS]["status"] == "expired"
    registrations = {r["diagnostic_id"]: r for r in state["registrations"]}
    projected_edges = []
    for v in invitations.values():
        assert v["inviter_id"].startswith("user-") and registrations[v["inviter_id"]]["handle"] == v["inviter_handle"]
        if v["invitee_id"]:
            assert registrations[v["invitee_id"]]["handle"] == v["invitee_handle"]
            assert registrations[v["invitee_id"]]["phone_number"] == v["invitee_phone"]
            projected_edges.append((v["inviter_handle"], v["invitee_handle"]))
    assert sorted(e for e in projected_edges if None not in e) == sorted((e["inviter"], e["invitee"]) for e in edges)
    safe = await asyncio.to_thread(operator_access_state)
    assert not safe["verbose"]
    assert {v["diagnostic_id"] for v in safe["invitations"]} == {v["diagnostic_id"] for v in invitations.values()}
    for v in safe["invitations"]:
        assert "code_words" not in v
        assert "inviter_phone" not in v
        assert all(v[field] is None or "*" in v[field] for field in ("recipient_phone", "invitee_phone"))
    safe_json = json.dumps(safe)
    assert all(words not in safe_json for words in invitations)
    assert all(r["phone_number"] not in safe_json for r in registrations.values() if r["phone_number"])
    # Handles are not masked: the safe lens names Users by them.
    assert {CALLER_HANDLE, SEED_HANDLE, invitee_handle} <= {r["handle"] for r in safe["registrations"]}

    # Renaming: three spins of three Dogwalk-chosen Handles; the caller picks one from any spin.
    await mocks.send_live(transcript("input", "Can I get a new name?", 36000, 37000))
    await mocks.send_live(delegation("del-r1", 37100))
    await wait_for(outputs_back("r1-who"), 15)
    offer = out("r1-offer")
    assert offer["current"] == CALLER_HANDLE and offer["spins_left"] == 2 and offer["earlier_options"] == [], offer
    options = offer["options"] + out("r1-offer2")["options"] + out("r1-offer3")["options"]
    assert len(set(options)) == 9 and all(len(h.split()) == 2 and CALLER_HANDLE != h for h in options), options
    assert out("r1-offer3")["earlier_options"] == offer["options"] + out("r1-offer2")["options"]
    assert out("r1-bogus")["ok"] is False and "not offered" in out("r1-bogus")["error"], out("r1-bogus")
    assert out("r1-offer4")["ok"] is False and "no spins left" in out("r1-offer4")["error"], out("r1-offer4")
    renamed = offer["options"][1]
    assert out("r1-set") | {"note": None} == {"ok": True, "handle": renamed, "previous": CALLER_HANDLE, "note": None}, out("r1-set")
    assert out("r1-who")["handle"] == renamed, out("r1-who")
    # Identity is unchanged: same User, its old Handle quarantined, its invitations still its own.
    rows = await asyncio.to_thread(d1, f"""SELECT u.id, u.handle, r.user_id AS retired_by FROM users u
        JOIN retired_handles r ON r.handle = '{CALLER_HANDLE}' WHERE u.handle = '{renamed}'""")
    assert len(rows) == 1 and rows[0]["retired_by"] == rows[0]["id"], rows
    renamed_state = await asyncio.to_thread(operator_access_state, True)
    assert all(v["inviter_handle"] != CALLER_HANDLE for v in renamed_state["invitations"])
    assert any(v["inviter_handle"] == renamed for v in renamed_state["invitations"])


async def run_unobserved_end(restart) -> None:
    """The Durable Object resets mid-call: the saved transcript survives and is filed at call end."""
    mocks = SocketMocks()
    mocks.advertise_resume = True
    spoken = ["Is the brake noise worth worrying about?", "Let me think that over."]
    # Since the last call, a reap took an old Sandbox out of service (and its deletion is done):
    # this Opening says so, once, in place of the welcome written about the old Sandbox.
    await asyncio.to_thread(d1, f"""INSERT INTO sandbox_deletions (provider_id, user_id, reaped_at, next_attempt_at, deleted_at)
        SELECT 'mock-reaped-old', id, unixepoch() - 60, unixepoch(), unixepoch() FROM users WHERE home_phone = '{TEST_PHONE}'""")
    async with serve(mocks.handler, "127.0.0.1", SOCKET_PORT):
        body, webhook_identity, signature = signed_webhook("CA_VOICE_TEST3", "live-test-4")
        assert await asyncio.to_thread(post_webhook, body, webhook_identity, signature) == 204
        await asyncio.wait_for(mocks.live_connected.wait(), 8)
        cleared = await next_matching(mocks.live_messages, lambda item: item.get("type") == "session.commentary.append"
                                      and REAPED_CLAUSE in item.get("content", ""))
        assert cleared["content"].endswith(f"{REAPED_CLAUSE} Welcome back. Where to?"), cleared["content"]
        notice = await asyncio.to_thread(d1, "SELECT noticed_at FROM sandbox_deletions WHERE provider_id = 'mock-reaped-old'")
        assert notice[0]["noticed_at"] is not None, "the reap notice was not consumed, so a later Opening would repeat it"
        await mocks.send_live(transcript("input", spoken[0], 0, 1500))
        await mocks.send_live(transcript("output", spoken[1], 1600, 2400))
        async with asyncio.timeout(10):
            while not (await asyncio.to_thread(conversations))[0]["current_transcript"]:
                await asyncio.sleep(0.2)
        # Restarting wrangler resets the object with the sideband still open: no cleanup runs.
        # The mock sockets then drop without a close frame, which is expected here.
        logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
        await asyncio.to_thread(restart)
    state = (await asyncio.to_thread(conversations))[0]
    assert state["current_transcript"]["call_sid"] == "CA_VOICE_TEST3", state["current_transcript"]
    assert not any(item["call_sid"] == "CA_VOICE_TEST3" for item in state["call_transcripts"]), "cleanup ran"
    status, _ = await asyncio.to_thread(post, DEFAULT_LOCAL, "/voice/status", {
        "From": TEST_PHONE, "CallSid": "CA_VOICE_TEST3", "CallStatus": "completed", "CallDuration": "30",
    }, LOCAL_TOKEN)
    assert status == 204, status
    state = (await asyncio.to_thread(conversations))[0]
    assert state["current_transcript"] is None
    filed = state["call_transcripts"][-1]
    assert filed["call_sid"] == "CA_VOICE_TEST3" and [entry["text"] for entry in filed["entries"]] == spoken, filed
    # last_call carries the recovered last words into the next call's since_last_call.
    ended = state["orchestrator_queue"] + [json.dumps(item) for item in state["orchestrator_context"]]
    assert any("voice end not observed" in item for item in ended), ended[-3:]


def sandbox_toolbox(command: str) -> dict | None:
    """Sandbox commands: honeydozer is the only project directory besides home; escape.png links outside it."""
    if "realpath -e --" in command:
        path = re.search(r"/home/daytona[^'\\]*", command).group(0)
        if path.endswith("/escape.png"):
            return {"result": "/etc/shadow\n", "exitCode": 0}
        known = path.endswith("/REPORT.md") or path.endswith("/report.html") or path.rsplit("/", 1)[-1] in IMAGE_FILES
        return {"result": f"{path}\n", "exitCode": 0} if known else {"result": "", "exitCode": 1}
    if "test -d" in command:
        return {"result": "", "exitCode": 0 if "/home/daytona/honeydozer" in command else 1}
    if "remote.origin.url" in command:
        rows = [
            ".\t0\t1\t",
            "./honeydozer\t1\t1\thttps://x-access-token:ghp_mocksecret@github.com/rndmcnlly/honeydozer.git",
        ]
        return {"result": "\n".join(rows) + "\n", "exitCode": 0}
    return None


def whiteboard_parser_checks() -> None:
    """The page's openui-lang parser, extracted from pair_page.ts and run in Node: Image takes a
    Workspace path and nothing that could reach another origin."""
    page = (WORKER_DIR / "src" / "pair_page.ts").read_text()
    resolve = re.search(r"function resolvePath\(.*?\n}\n", page, re.S).group(0)
    parser = page[page.index("// ---- Tokens:"):page.index("// ---- Rendering.")]
    cases = [
        'root = Stack([fig, t])\nfig = Image("bayleaf/diagram.svg", "Chain path", "Generated, unverified", "large")\nt = Text("hi")',
        'root = Stack([Image("https://example.com/a.png", "x")])',
        'root = Stack([Image("data:image/png;base64,AAAA", "x")])',
        'root = Stack([Image("../../etc/a.png", "x")])',
        'root = Stack([Image("notes.txt", "x")])',
        'root = Stack([Image("/home/daytona/icon.png", "", null, "huge")])',
        'root = Stack([Image (path, "Plot")])\npath = alias\nalias = "chart.png"',
    ]
    script = (
        'const HOME = "/home/daytona";\n' + resolve + parser
        + f"\nconsole.log(JSON.stringify({json.dumps(cases)}.map((source) => {{ const r = wbProgram(source);"
        + " return { errors: r.errors, children: r.root ? r.root.props.children.map((c) => c.props) : null }; })));"
    )
    results = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    drawn, url, data, escape, text, size, reference = results
    assert not drawn["errors"] and drawn["children"][0] == {
        "path": "/home/daytona/bayleaf/diagram.svg", "alt": "Chain path", "caption": "Generated, unverified", "size": "large",
    }, drawn
    for refused, why in [(url, "never a URL"), (data, "never a URL"), (escape, "under /home/daytona"), (text, ".png")]:
        assert refused["children"] == [] and any(why in error for error in refused["errors"]), refused
    assert size["children"] == [{"path": "/home/daytona/icon.png", "alt": ""}] and "must be one of icon" in size["errors"][0], size
    assert not reference["errors"] and reference["children"] == [{"path": "/home/daytona/chart.png", "alt": "Plot"}], reference


def main() -> int:
    whiteboard_parser_checks()
    wrangler = project_wrangler()
    if not wrangler:
        print("wrangler is not installed", file=sys.stderr)
        return 2
    prepare_local_fixture(wrangler)
    identity = hmac.new(
        IDENTITY_SECRET.encode(), TEST_PHONE.encode(), hashlib.sha256
    ).hexdigest()
    configure_assignment(wrangler, identity)
    DaytonaMock.exists = True
    DaytonaMock.generation = 1
    DaytonaMock.openai_requests = []
    DaytonaMock.preview_reads = 0
    DaytonaMock.executed = []
    DaytonaMock.execute_hooks = [sandbox_toolbox]
    DaytonaMock.get_hooks = [sandbox_files]
    daytona = ThreadingHTTPServer(("127.0.0.1", DAYTONA_MOCK_PORT), DaytonaMock)
    thread = threading.Thread(target=daytona.serve_forever, daemon=True)
    thread.start()
    orchestrator = ThreadingHTTPServer(("127.0.0.1", ORCHESTRATOR_MOCK_PORT), OrchestratorMock)
    threading.Thread(target=orchestrator.serve_forever, daemon=True).start()
    processes = [start_wrangler(wrangler)]

    def restart() -> None:
        stop_wrangler(processes[-1])
        processes.append(start_wrangler(wrangler))

    try:
        asyncio.run(run_protocol(identity))
        asyncio.run(run_unobserved_end(restart))
        print("voice session delegation checks passed")
        return 0
    finally:
        stop_wrangler(processes[-1])
        daytona.shutdown()
        daytona.server_close()
        orchestrator.shutdown()
        orchestrator.server_close()


def stop_wrangler(process: subprocess.Popen) -> None:
    process.send_signal(signal.SIGTERM)
    process.wait(timeout=10)
    # The port frees once workerd exits, which may lag wrangler itself.
    deadline = time.time() + 10
    while time.time() < deadline and wait_port("127.0.0.1", WORKER_PORT, timeout=0.2):
        time.sleep(0.2)


def start_wrangler(wrangler: str) -> subprocess.Popen:
    process = subprocess.Popen(
        [
            wrangler,
            "dev",
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
            "DAYTONA_API_KEY:local-daytona-token",
            "--var",
            f"DOGWALK_IDENTITY_SECRET:{IDENTITY_SECRET}",
            "--var",
            f"DAYTONA_API_BASE:http://127.0.0.1:{DAYTONA_MOCK_PORT}/api",
            "--var",
            "OPENAI_API_KEY:local-openai-token",
            "--var",
            "OPENAI_PROJECT_ID:proj_test",
            "--var",
            f"OPENAI_WEBHOOK_SECRET:{OPENAI_WEBHOOK_SECRET}",
            "--var",
            f"OPENAI_LIVE_API_BASE:http://127.0.0.1:{DAYTONA_MOCK_PORT}/live",
            "--var",
            f"OPENAI_LIVE_ATTACH_BASE:http://127.0.0.1:{SOCKET_PORT}/live",
            "--var",
            f"ORCHESTRATOR_API_BASE:http://127.0.0.1:{ORCHESTRATOR_MOCK_PORT}/v1",
            "--var",
            f"ADMIN_PASSWORD:{LOCAL_ADMIN_PASSWORD}",
        ],
        cwd=WORKER_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not wait_port("127.0.0.1", WORKER_PORT, timeout=20):
        process.kill()
        raise RuntimeError("wrangler dev did not start")
    return process


if __name__ == "__main__":
    sys.exit(main())
