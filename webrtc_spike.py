#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["agent-client-protocol==0.11.0"]
# ///
"""Portable Dogwalk service for browser voice calls and local ACP agents."""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import os
import secrets
import shlex
import shutil
import signal
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, Client, spawn_agent_process, text_block


ROOT = Path(__file__).parent
MODEL = "gpt-realtime-2.1"

INSTRUCTIONS = """
You are Walker, a warm, concise voice interface through which the User supervises
coding sessions called Dogs. You are socially fluent but engineering-weak, and
honest about that limitation. The Dogs, by contrast, are extraordinarily capable
engineers. Trust their technical judgment and never talk about them as simplistic
helpers or pets.

You know that Dog, Walker, Pack, sic, and call off form a humorously strained
metaphor. A Dog is really the friendly spoken name and persona for a retained
coding session. Dogs spring into existence when needed, keep their context across
follow-up turns, and blow away when called off. You may occasionally acknowledge
the metaphor's absurdity with dry, compact humor, especially when it stretches,
but do not explain the architecture unprompted, force dog jokes, use baby talk, or
let the bit obstruct the work. Never speak code or technical identifiers aloud.
Describe engineering work in plain language as shape and consequence.

Natural dogisms are welcome when they fit the actual activity. A Dog investigating
something may have "gotten its nose into it"; one making progress may be "still
digging"; one working through a stubborn problem may be "still chewing on that
bone"; and one that found the key issue may have "caught the scent." Use these as
brief seasoning on a concrete, truthful update, vary them, and never replace the
substance of the update with metaphor.

Use the supplied tools whenever the User asks you to inspect, change, test, or
otherwise act on software, or asks for information that a Dog can obtain. Treat
such a request as implicit authorization to sic a Dog: form a concrete task and
dispatch it rather than saying you cannot do it because you lack access or
engineering ability. A Dog keeps its context until called off or Walker-hands
stops. Give each new Dog a short pronounceable name inspired by its task. Before
starting related work, list the existing Dogs and continue a suitable resting
one when possible. The User may give an existing Dog a new spoken name. Ask the
User a question only when
the task's scope is materially unclear or a consequential safety choice needs
their decision. Tool results are authoritative, but they are deliberately stubbed
in this prototype. Keep spoken replies short enough for a hands-free conversation.

This local spike accepts read-only Dogs only: always set read_only to true when
you sic a Dog. A read-only Dog may still inspect, run non-mutating commands, and
ask the User for an ACP permission decision. Do not describe an ordinary
read-only task as impossible merely because it involves generating text.

Open every new session with this brief, warm welcome: "I'll be your dog walker
for today." Then invite the User to say what is on their mind. Do not ask
whether they can hear you. Never poll a Dog. Check a Dog only when the User
explicitly asks, or when a timer you set has fired. If the User asks you to wait and check later, call set_timer
after siccing the Dog, then continue the conversation normally. When the timer
notification arrives, tell the User it is time to check and ask whether they want
you to do so. To end this conversation hands-free, speak a brief farewell first
and then call end_call as your final action. Treat ordinary closings such as
"bye", "we're good", "that's it", or "stop here" as a request to end the call
unless the User clearly asks to keep talking.

When a system notification says the User muted their microphone, enter silent
mode: do not speak, create responses, or poll Dogs. Let running Dogs continue
and let the local monitor collect their progress. When a system notification
says the User unmuted, it is explicit authorization to give one concise catch-up.
You may check each known working Dog once to make that catch-up accurate, then
say only the essential update.

When a system notification says a Dog completed, immediately give the User a
brief plain-language result. Do not claim success when the report says the Dog
did not produce one: say that it finished without confirming the requested result.

When a system notification says a Dog needs a decision, briefly explain its
question or requested action and the available choices. Ask the User, then use
the matching decision tool to send their answer back to the Dog. Never select a
permission option or invent an answer yourself.
""".strip()

DOG_BRIEFING = """You are {name}, a Dog in the Dogwalk system: the friendly named
persona for this retained coding session. Walker is attached
to the real human User through a live voice interface. Walker is a speech-to-speech
model with basic function calling: enough to start a task like the one below,
receive your report, relay it in plain language, and bring user questions back to
you. Walker is deliberately engineering-weak. You are the highly capable engineering
Agent behind the Dog persona: investigate the workspace and report useful findings
to Walker. Retain context for follow-up Prompt Turns in this session. Do not
speak to the User directly or assume you have the voice conversation's history.

Safety mode: {safety}
End with a concise plain-language report for Walker to relay.

Task from Walker:
{task}"""

TOOLS = [
    {
        "type": "function",
        "name": "sic_dog",
        "description": "Create a fresh named Dog and retained coding session for an assignment. This local spike accepts read-only Dogs only, so always set read_only true. The server automatically supplies role, safety, reporting, and workspace context. In task, state only the work to do; do not repeat Dogwalk background or instructions.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short pronounceable Dog name.",
                },
                "task": {"type": "string", "description": "Engineering task to relay."},
                "read_only": {
                    "type": "boolean",
                    "description": "Whether the Dog must avoid changes.",
                },
            },
            "required": ["name", "task", "read_only"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "check_dog",
        "description": "Check a Dog only when the User asks or after a timer notification. A working Dog returns a concise activity gloss based on its ACP tool activity, not its private reasoning or full transcript.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "list_dogs",
        "description": "List Dogs whose ACP sessions remain available. Use this before related work to find a resting Dog to resume. Never speak opaque session identifiers aloud.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "name_dog",
        "description": "Give an existing Dog a new short spoken name for this call.",
        "parameters": {
            "type": "object",
            "properties": {
                "current_name": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["current_name", "name"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "relay_to_dog",
        "description": "Relay a follow-up instruction to a working or resting Dog. A resting Dog resumes its retained ACP session.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "message": {"type": "string"}},
            "required": ["name", "message"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "call_off_dog",
        "description": "Close a Dog's retained ACP session. If its Prompt Turn is active, that turn is cancelled first.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "respond_to_dog_permission",
        "description": "Resolve a pending Dog permission request after the User chooses. Use the exact option_id supplied by the pending request, including the Agent's rejection option when the User refuses.",
        "parameters": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string"},
                "option_id": {"type": "string"},
            },
            "required": ["decision_id", "option_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "answer_dog_question",
        "description": "Resolve a pending Dog question after the User answers. Map the User's answer into the requested answer object, or set decline true if they decline.",
        "parameters": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "string"},
                "answer": {"type": "object"},
                "decline": {"type": "boolean"},
            },
            "required": ["decision_id", "decline"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "set_timer",
        "description": "Schedule a future notification. This does not inspect or act on a Dog.",
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
                "purpose": {
                    "type": "string",
                    "description": "Plain-language reason for the notification.",
                },
            },
            "required": ["seconds", "purpose"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "end_call",
        "description": "End the hands-free audio session. Speak a short farewell before calling this as your final action.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def agent_executable_available(command: str, workspace: Path) -> tuple[bool, str]:
    executable = shlex.split(command)[0].replace("{cwd}", str(workspace))
    candidate = Path(executable).expanduser()
    if candidate.is_absolute() or "/" in executable:
        if not candidate.is_absolute():
            candidate = workspace / candidate
        return candidate.is_file() and os.access(candidate, os.X_OK), str(candidate)
    resolved = shutil.which(executable)
    return resolved is not None, resolved or executable


class SessionLog:
    def __init__(self, mode: str = "webrtc", directory: Path | None = None) -> None:
        directory = directory or ROOT / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = directory / f"{stamp}-{mode}.jsonl"
        self.file = self.path.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()

    def write(self, kind: str, **data: Any) -> None:
        with self._lock:
            self.file.write(
                json.dumps(
                    {
                        "at": datetime.now(timezone.utc).isoformat(
                            timespec="milliseconds"
                        ),
                        "kind": kind,
                        **data,
                    },
                    ensure_ascii=True,
                )
                + "\n"
            )


class AcpRuntime:
    """Dedicated asyncio loop lets synchronous HTTP handlers manage live ACP work."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def submit(self, coroutine: Any) -> concurrent.futures.Future[Any]:
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def close(self) -> None:
        async def drain() -> None:
            current = asyncio.current_task()
            tasks = [task for task in asyncio.all_tasks() if task is not current]
            if tasks:
                _, pending = await asyncio.wait(tasks, timeout=5)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(drain(), self.loop).result(timeout=6)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


class SessionManager:
    """Manage retained ACP sessions and project them onto Walker's Dog tool surface."""

    def __init__(
        self,
        log: SessionLog,
        cwd: Path,
        allow_writes: bool = False,
        agent_command: str = "opencode acp --pure --cwd {cwd}",
    ) -> None:
        self.log = log
        self.cwd = cwd
        self.runtime = AcpRuntime()
        self.allow_writes = allow_writes
        self.agent_command = agent_command
        self.sessions: dict[str, dict[str, Any]] = {}
        self._turn_results: list[dict[str, Any]] = []
        self._attention_requests: dict[str, dict[str, Any]] = {}
        self._attention_events: list[dict[str, Any]] = []
        self._background: set[concurrent.futures.Future[Any]] = set()
        self._lock = threading.RLock()

    def dispatch(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        name = arguments.get("name", "")
        if tool == "respond_to_dog_permission":
            return self.resolve_permission(
                arguments["decision_id"], arguments["option_id"]
            )
        if tool == "answer_dog_question":
            return self.resolve_elicitation(
                arguments["decision_id"],
                arguments.get("answer"),
                arguments["decline"],
            )
        if tool == "list_dogs":
            return {"ok": True, "dogs": self.list_dogs()}
        if tool == "name_dog":
            return self.set_alias(arguments["current_name"], name)
        if tool == "sic_dog":
            if not arguments["read_only"] and not self.allow_writes:
                return {
                    "ok": False,
                    "error": "This local ACP spike accepts read-only Dogs only.",
                }
            with self._lock:
                if self._session_for_alias(name) is not None:
                    return {
                        "ok": False,
                        "error": f"A Dog named {name} already exists. Choose another name or continue it.",
                    }
                self.sessions[name] = {
                    "alias": name,
                    "session_state": "creating",
                    "turn_state": "in_progress",
                    "stop_reason": None,
                    "assignment": arguments["task"],
                    "read_only": arguments["read_only"],
                    "report": "",
                    "activity": "starting up",
                    "updates": [],
                    "future": None,
                    "session_id": None,
                    "session_title": None,
                    "updated_at": None,
                    "usage": None,
                    "queue": None,
                }
                self.sessions[name]["future"] = self.runtime.submit(
                    self._run(name, arguments["task"])
                )
            return {
                "ok": True,
                "name": name,
                "status": "working",
                "message": (
                    "The read-only Dog is scouting the local workspace."
                    if arguments["read_only"]
                    else "The write-enabled Dog is working in the isolated test workspace."
                ),
            }

        with self._lock:
            entry = self._session_entry_for_alias(name)
            if entry is None:
                return {"ok": False, "error": f"No Dog named {name}."}
            session_key, session = entry
            if tool == "check_dog":
                status = self._dog_status(session)
                result = {"ok": True, "name": name, "status": status}
                if status == "resting":
                    result["report"] = session["report"]
                elif status == "failed":
                    result["error"] = session["report"]
                else:
                    result["update"] = session["activity"]
                return result
            if tool == "relay_to_dog":
                if session["session_state"] != "ready":
                    return {
                        "ok": False,
                        "error": f"{session['alias']} is {self._dog_status(session)} and cannot continue.",
                    }
                session["turn_state"] = "queued"
                queued = self.runtime.submit(self._enqueue(session_key, arguments["message"]))
                self._background.add(queued)
                queued.add_done_callback(self._background.discard)
                return {"ok": True, "name": session["alias"], "status": "working", "message": "Dog is continuing its retained session."}
            if tool == "call_off_dog":
                if session["session_state"] in {"closed", "unavailable"}:
                    return {
                        "ok": False,
                        "error": f"{session['alias']} is already {self._dog_status(session)} and cannot be called off.",
                    }
                future = session["future"]
                session["session_state"] = "closing"
                future.cancel()
                if session["turn_state"] in {"queued", "in_progress"}:
                    session["turn_state"] = "stopped"
                    session["stop_reason"] = "cancelled"
                session["session_state"] = "closed"
                self._cancel_attention_requests(session_key)
                return {"ok": True, "name": session["alias"], "status": "closed"}
        return {"ok": False, "error": f"Unknown tool {tool}."}

    def _session_for_alias(self, alias: str) -> dict[str, Any] | None:
        entry = self._session_entry_for_alias(alias)
        return entry[1] if entry else None

    def _session_entry_for_alias(
        self, alias: str
    ) -> tuple[str, dict[str, Any]] | None:
        normalized = alias.casefold().strip()
        return next(
            (
                (key, session)
                for key, session in self.sessions.items()
                if session["alias"].casefold() == normalized
            ),
            None,
        )

    @staticmethod
    def _dog_status(session: dict[str, Any]) -> str:
        if session["session_state"] == "closed":
            return "cancelled"
        if session["session_state"] == "unavailable" or session["turn_state"] == "failed":
            return "failed"
        if session["turn_state"] in {"queued", "in_progress"}:
            return "working"
        return "resting"

    def list_dogs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "name": session["alias"],
                    "status": self._dog_status(session),
                    "task": session["assignment"],
                    "activity": session["activity"],
                    "title": session["session_title"],
                    "last_updated": session["updated_at"],
                }
                for session in self.sessions.values()
                if session["session_state"] == "ready"
            ]

    def set_alias(self, current_alias: str, alias: str) -> dict[str, Any]:
        with self._lock:
            entry = self._session_entry_for_alias(current_alias)
            if entry is None:
                return {"ok": False, "error": f"No Dog named {current_alias}."}
            session_key, session = entry
            if self._session_for_alias(alias) is not None:
                return {"ok": False, "error": f"A Dog named {alias} already exists."}
            old_alias = session["alias"]
            session["alias"] = alias
            for request in self._attention_requests.values():
                if request["session_key"] == session_key:
                    request["dog"] = alias
            for event in self._attention_events:
                if event["decision_id"] in self._attention_requests:
                    event["dog"] = self._attention_requests[event["decision_id"]]["dog"]
        self.log.write("dog_renamed", old_name=old_alias, dog=alias)
        return {"ok": True, "old_name": old_alias, "name": alias}

    async def _enqueue(self, session_key: str, message: str) -> None:
        while True:
            with self._lock:
                session = self.sessions.get(session_key)
                if session is None or session["session_state"] != "ready":
                    return
                queue = session["queue"]
            if queue is not None:
                await queue.put(message)
                return
            await asyncio.sleep(0.01)

    async def _run(self, session_key: str, assignment: str) -> None:
        client = AcpClientAdapter(self, session_key)
        session = self.sessions[session_key]
        alias = session["alias"]
        read_only = session["read_only"]
        safety = (
            "Read-only. Do not modify files, install dependencies, run commands that "
            "change state, or commit."
            if read_only
            else "Workspace changes are authorized for this test. Do not commit or make "
            "unrelated changes."
        )
        prompt = DOG_BRIEFING.format(name=alias, task=assignment, safety=safety)
        command = [
            part.replace("{cwd}", str(self.cwd))
            for part in shlex.split(self.agent_command)
        ]
        try:
            async with spawn_agent_process(
                client,
                command[0],
                *command[1:],
                cwd=self.cwd,
            ) as (connection, _process):
                await connection.initialize(protocol_version=PROTOCOL_VERSION)
                session = await connection.new_session(
                    cwd=str(self.cwd), mcp_servers=[]
                )
                queue: asyncio.Queue[str] = asyncio.Queue()
                with self._lock:
                    managed_session = self.sessions[session_key]
                    managed_session["session_id"] = session.session_id
                    managed_session["session_state"] = "ready"
                    managed_session["queue"] = queue
                self.log.write(
                    "acp_session_started", dog=alias, session_id=session.session_id
                )
                prompt_result = await connection.prompt(
                    session_id=session.session_id, prompt=[text_block(prompt)]
                )
                while True:
                    self._stop_turn(session_key, str(prompt_result.stop_reason))
                    message = await queue.get()
                    with self._lock:
                        managed_session = self.sessions[session_key]
                        if managed_session["session_state"] != "ready":
                            return
                        managed_session["turn_state"] = "in_progress"
                        managed_session["stop_reason"] = None
                        managed_session["report"] = ""
                    self.log.write("acp_session_continued", dog=alias, session_id=session.session_id)
                    prompt_result = await connection.prompt(
                        session_id=session.session_id, prompt=[text_block(message)]
                    )
        except asyncio.CancelledError:
            self.log.write("managed_session_closed", alias=alias)
            raise
        except Exception as exc:
            with self._lock:
                managed_session = self.sessions[session_key]
                managed_session["session_state"] = "unavailable"
                managed_session["turn_state"] = "failed"
                managed_session["report"] = f"ACP failure: {type(exc).__name__}: {exc}"
                self._turn_results.append(
                    {
                        "session_key": session_key,
                        "status": "failed",
                        "report": managed_session["report"],
                    }
                )
            self.log.write(
                "dog_finished",
                dog=alias,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _stop_turn(self, session_key: str, stop_reason: str) -> None:
        with self._lock:
            session = self.sessions[session_key]
            if session["turn_state"] != "in_progress":
                return
            session["turn_state"] = "stopped"
            session["stop_reason"] = stop_reason
            session["report"] = (
                session["report"].strip()
                or f"The Dog finished without a textual report. Its last activity was: {session['activity']}."
            )
            self._turn_results.append(
                {"session_key": session_key, "status": "resting", "report": session["report"]}
            )
        self.log.write(
            "prompt_turn_stopped",
            alias=session["alias"],
            stop_reason=stop_reason,
        )

    def update(self, session_key: str, update: Any) -> None:
        kind = type(update).__name__
        text = getattr(getattr(update, "content", None), "text", None)
        detail = str(update)[:2000]
        self.log.write(
            "acp_update",
            dog=self.sessions[session_key]["alias"],
            update_type=kind,
            text=text,
            detail=detail,
        )
        with self._lock:
            session = self.sessions[session_key]
            session["updates"].append({"type": kind, "text": text, "detail": detail})
            session["updates"] = session["updates"][-50:]
            if kind in {"ToolCallStart", "ToolCallProgress"}:
                session["activity"] = self.activity_gloss(update)
            if kind == "AgentMessageChunk" and text:
                session["report"] += text
            if kind == "SessionInfoUpdate":
                session["session_title"] = getattr(update, "title", None)
                session["updated_at"] = getattr(update, "updated_at", None)
            if kind == "UsageUpdate":
                session["usage"] = {
                    "used": getattr(update, "used", None),
                    "size": getattr(update, "size", None),
                    "cost": str(getattr(update, "cost", None) or "") or None,
                }

    @staticmethod
    def activity_gloss(update: Any) -> str:
        kind = getattr(update, "kind", None)
        title = getattr(update, "title", None)
        raw_input = getattr(update, "raw_input", None) or {}
        path = raw_input.get("filePath") or raw_input.get("path")
        locations = getattr(update, "locations", None) or []
        if not path and locations:
            path = getattr(locations[0], "path", None)
        action = {
            "read": "reading",
            "search": "searching",
            "execute": "running",
            "edit": "editing",
            "write": "writing",
        }.get(kind, "working with")
        if path:
            return f"{action} {Path(path).name}"
        if kind == "execute":
            return "running a command"
        if title:
            return f"{action} {title}"
        return "working in the workspace"

    def monitor(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "name": session["alias"],
                    "status": self._dog_status(session),
                    "session_state": session["session_state"],
                    "turn_state": session["turn_state"],
                    "stop_reason": session["stop_reason"],
                    "task": session["assignment"],
                    "activity": session["activity"],
                    "report": session["report"],
                    "updates": list(session["updates"]),
                    "session": {
                        "id": session["session_id"],
                        "title": session["session_title"],
                        "updated_at": session["updated_at"],
                        "usage": session["usage"],
                    },
                }
                for session in self.sessions.values()
            ]

    def take_turn_results(self) -> list[dict[str, Any]]:
        with self._lock:
            results, self._turn_results = self._turn_results, []
            return [
                {
                    "name": self.sessions[event["session_key"]]["alias"],
                    "status": event["status"],
                    "report": event["report"],
                }
                for event in results
            ]

    async def request_permission(
        self, session_key: str, session_id: str, tool_call: Any, options: list[Any]
    ) -> dict[str, Any]:
        decision_id = f"permission-{time.monotonic_ns()}"
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        with self._lock:
            session = self.sessions[session_key]
            if session["session_state"] != "ready":
                raise asyncio.CancelledError
            event = {
                "decision_id": decision_id,
                "dog": session["alias"],
                "kind": "permission",
                "message": getattr(tool_call, "title", "The Dog requests permission."),
                "options": [
                    {
                        "option_id": option.option_id,
                        "name": option.name,
                        "kind": str(option.kind),
                    }
                    for option in options
                ],
            }
            self._attention_requests[decision_id] = {
                **event,
                "session_key": session_key,
                "future": future,
            }
            self._attention_events.append(event)
        self.log.write("acp_permission_requested", **event)
        return await future

    async def create_elicitation(
        self, session_key: str, message: str, mode: Any
    ) -> dict[str, Any]:
        decision_id = f"elicitation-{time.monotonic_ns()}"
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        mode_data = mode.model_dump(mode="json", by_alias=True)
        with self._lock:
            session = self.sessions[session_key]
            if session["session_state"] != "ready":
                raise asyncio.CancelledError
            event = {
                "decision_id": decision_id,
                "dog": session["alias"],
                "kind": "elicitation",
                "message": message,
                "schema": mode_data.get("requestedSchema"),
            }
            self._attention_requests[decision_id] = {
                **event,
                "session_key": session_key,
                "future": future,
            }
            self._attention_events.append(event)
        self.log.write("acp_elicitation_requested", **event)
        return await future

    def resolve_permission(self, decision_id: str, option_id: str) -> dict[str, Any]:
        with self._lock:
            decision = self._attention_requests.pop(decision_id, None)
        if decision is None or decision["kind"] != "permission":
            return {"ok": False, "error": "No pending permission with that ID."}
        valid_ids = {option["option_id"] for option in decision["options"]}
        if option_id not in valid_ids:
            return {"ok": False, "error": "That option is not offered by the Dog."}
        result = {"outcome": {"outcome": "selected", "optionId": option_id}}
        self._resolve_decision(decision, result)
        self.log.write(
            "acp_permission_resolved", decision_id=decision_id, option_id=option_id
        )
        return {"ok": True, "decision_id": decision_id, "option_id": option_id}

    def resolve_elicitation(
        self, decision_id: str, answer: dict[str, Any] | None, decline: bool
    ) -> dict[str, Any]:
        with self._lock:
            decision = self._attention_requests.pop(decision_id, None)
        if decision is None or decision["kind"] != "elicitation":
            return {"ok": False, "error": "No pending Dog question with that ID."}
        result = (
            {"action": "decline"}
            if decline
            else {"action": "accept", "content": answer or {}}
        )
        self._resolve_decision(decision, result)
        self.log.write(
            "acp_elicitation_resolved", decision_id=decision_id, declined=decline
        )
        return {"ok": True, "decision_id": decision_id, "declined": decline}

    @staticmethod
    def _resolve_decision(decision: dict[str, Any], result: dict[str, Any]) -> None:
        future = decision["future"]

        def resolve() -> None:
            if not future.done():
                future.set_result(result)

        future.get_loop().call_soon_threadsafe(resolve)

    def _cancel_attention_requests(self, session_key: str) -> None:
        with self._lock:
            requests = [
                self._attention_requests.pop(decision_id)
                for decision_id, request in list(self._attention_requests.items())
                if request["session_key"] == session_key
            ]
            cancelled_ids = {request["decision_id"] for request in requests}
            self._attention_events = [
                event
                for event in self._attention_events
                if event["decision_id"] not in cancelled_ids
            ]
        for request in requests:
            future = request["future"]
            future.get_loop().call_soon_threadsafe(future.cancel)

    def take_attention_requests(self) -> list[dict[str, Any]]:
        with self._lock:
            requests, self._attention_events = self._attention_events, []
        return requests

    def attention_requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    key: value
                    for key, value in request.items()
                    if key not in {"future", "session_key"}
                }
                for request in self._attention_requests.values()
            ]

    def close(self) -> None:
        with self._lock:
            aliases = [
                session["alias"]
                for session in self.sessions.values()
                if session["session_state"] not in {"closed", "unavailable"}
            ]
        for alias in aliases:
            self.dispatch("call_off_dog", {"name": alias})
        for session in self.sessions.values():
            future = session["future"]
            if future is None:
                continue
            try:
                future.result(timeout=5)
            except (concurrent.futures.CancelledError, TimeoutError):
                pass
        for future in list(self._background):
            try:
                future.result(timeout=5)
            except (concurrent.futures.CancelledError, TimeoutError):
                pass
        self.runtime.close()


class AcpClientAdapter(Client):
    def __init__(self, manager: SessionManager, session_key: str) -> None:
        self.manager = manager
        self.session_key = session_key

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.manager.update(self.session_key, update)

    async def request_permission(
        self, session_id: str, tool_call: Any, options: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return await self.manager.request_permission(
            self.session_key, session_id, tool_call, options
        )

    async def create_elicitation(
        self, message: str, mode: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return await self.manager.create_elicitation(self.session_key, message, mode)


class TimerQueue:
    """Own time in Walker-hands, then notify the browser's live data channel."""

    def __init__(self, log: SessionLog) -> None:
        self.log = log
        self._due: list[dict[str, Any]] = []
        self._timers: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def set(self, seconds: int, purpose: str) -> dict[str, Any]:
        timer_id = f"timer-{time.monotonic_ns()}"

        def fire() -> None:
            event = {"timer_id": timer_id, "purpose": purpose, "seconds": seconds}
            with self._lock:
                self._due.append(event)
                self._timers[timer_id]["status"] = "due"
            self.log.write("timer_fired", **event)

        created_at = time.monotonic()
        with self._lock:
            self._timers[timer_id] = {
                "timer_id": timer_id,
                "purpose": purpose,
                "seconds": seconds,
                "created_at": created_at,
                "deadline": created_at + seconds,
                "status": "waiting",
            }
        threading.Timer(seconds, fire).start()
        self.log.write("timer_set", timer_id=timer_id, purpose=purpose, seconds=seconds)
        return {
            "ok": True,
            "timer_id": timer_id,
            "seconds": seconds,
            "purpose": purpose,
        }

    def take_due(self) -> list[dict[str, Any]]:
        with self._lock:
            due, self._due = self._due, []
            for event in due:
                self._timers[event["timer_id"]]["status"] = "delivered"
        return due

    def monitor(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            return [
                {
                    "timer_id": timer["timer_id"],
                    "purpose": timer["purpose"],
                    "seconds": timer["seconds"],
                    "status": timer["status"],
                    "remaining_seconds": max(0, round(timer["deadline"] - now, 1)),
                    "progress": min(
                        1, max(0, (now - timer["created_at"]) / timer["seconds"])
                    ),
                }
                for timer in self._timers.values()
            ]


class CallLease:
    """Keep one Walker attached while letting Dogs outlive individual calls."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self._token: str | None = None
        self._last_seen = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> str | None:
        now = time.monotonic()
        with self._lock:
            if self._token and now - self._last_seen <= self.timeout_seconds:
                return None
            self._token = secrets.token_urlsafe(24)
            self._last_seen = now
            return self._token

    def touch(self, token: str | None) -> bool:
        now = time.monotonic()
        with self._lock:
            if not self._token or now - self._last_seen > self.timeout_seconds:
                self._token = None
                return False
            if not token or not secrets.compare_digest(token, self._token):
                return False
            self._last_seen = now
            return True

    def release(self, token: str | None) -> bool:
        with self._lock:
            if not self._token or not token or not secrets.compare_digest(token, self._token):
                return False
            self._token = None
            return True

    def active(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if self._token and now - self._last_seen <= self.timeout_seconds:
                return True
            self._token = None
            return False


class Handler(SimpleHTTPRequestHandler):
    log: SessionLog
    manager: SessionManager
    timers: TimerQueue
    calls: CallLease
    api_key: str
    started_at: float
    workspace: Path
    agent_command: str

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/call":
            self.begin_call()
        elif self.path == "/session":
            self.create_session()
        elif self.path == "/tool":
            self.run_tool()
        elif self.path == "/event":
            self.record_event()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self.respond_json(
                {
                    "ok": True,
                    "uptime_seconds": round(time.monotonic() - self.started_at, 1),
                    "active_call": self.calls.active(),
                    "dogs": len(self.manager.list_dogs()),
                }
            )
        elif self.path == "/readyz":
            executable_ready, executable = agent_executable_available(
                self.agent_command, self.workspace
            )
            ready = self.workspace.is_dir() and executable_ready
            self.respond_json(
                {
                    "ok": ready,
                    "workspace": str(self.workspace),
                    "agent_executable": executable,
                },
                status=HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
            )
        elif self.path == "/call-heartbeat":
            self.stream_call_heartbeat()
        elif self.path in {
            "/due",
            "/dog-events",
            "/decisions",
            "/monitor",
        } and not self.require_call():
            return
        elif self.path == "/due":
            self.respond_json({"due": self.timers.take_due()})
        elif self.path == "/dog-events":
            self.respond_json({"completed": self.manager.take_turn_results()})
        elif self.path == "/decisions":
            self.respond_json({"decisions": self.manager.take_attention_requests()})
        elif self.path == "/monitor":
            self.respond_json(
                {
                    "dogs": self.manager.monitor(),
                    "timers": self.timers.monitor(),
                    "decisions": self.manager.attention_requests(),
                }
            )
        elif self.path in {"/", "/webrtc_spike.html"}:
            self.serve_browser_client()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", "0")))

    def begin_call(self) -> None:
        call_token = self.calls.acquire()
        if call_token is None:
            self.respond_json(
                {"ok": False, "error": "Another Walker call is active."},
                status=HTTPStatus.CONFLICT,
            )
            return
        self.log.write("call_started")
        self.respond_json({"ok": True, "call_token": call_token})

    def stream_call_heartbeat(self) -> None:
        token = self.headers.get("X-Dogwalk-Call")
        if not self.calls.touch(token):
            self.respond_json(
                {"ok": False, "error": "Walker call lease is not active."},
                status=HTTPStatus.CONFLICT,
            )
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        interval = min(5.0, max(0.25, self.calls.timeout_seconds / 3))
        try:
            while self.calls.touch(token):
                self.wfile.write(b'{"ok":true}\n')
                self.wfile.flush()
                time.sleep(interval)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.calls.release(token)

    def serve_browser_client(self) -> None:
        body = (ROOT / "webrtc_spike.html").read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def create_session(self) -> None:
        call_token = self.headers.get("X-Dogwalk-Call")
        if not self.calls.touch(call_token):
            self.respond_json(
                {"ok": False, "error": "Walker call lease is not active."},
                status=HTTPStatus.CONFLICT,
            )
            return
        offer = self.body()
        session = json.dumps(
            {
                "type": "realtime",
                "model": MODEL,
                "output_modalities": ["audio"],
                "instructions": INSTRUCTIONS,
                "tools": TOOLS,
                "tool_choice": "auto",
                "audio": {
                    "output": {"voice": "cedar"},
                    "input": {
                        "turn_detection": {
                            "type": "semantic_vad",
                            "interrupt_response": True,
                        },
                        "transcription": {"model": "gpt-4o-mini-transcribe"},
                    },
                },
            }
        ).encode()
        boundary = "dogwalk-boundary"
        body = b"".join(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="sdp"\r\nContent-Type: application/sdp\r\n\r\n'.encode(),
                offer,
                b"\r\n",
                f'--{boundary}\r\nContent-Disposition: form-data; name="session"\r\n\r\n'.encode(),
                session,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            )
        )
        request = urllib.request.Request(
            "https://api.openai.com/v1/realtime/calls",
            body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "OpenAI-Safety-Identifier": "dogwalk-local-user",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                answer = response.read()
        except urllib.error.HTTPError as exc:
            self.calls.release(call_token)
            detail = exc.read().decode(errors="replace")
            self.log.write("session_error", status=exc.code, detail=detail)
            self.send_error(exc.code, detail)
            return
        except urllib.error.URLError as exc:
            self.calls.release(call_token)
            detail = str(exc.reason)
            self.log.write("session_error", status=502, detail=detail)
            self.send_error(HTTPStatus.BAD_GATEWAY, detail)
            return
        self.log.write("session_created", transport="webrtc")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/sdp")
        self.send_header("X-Dogwalk-Call", call_token)
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)

    def run_tool(self) -> None:
        if not self.require_call():
            return
        payload = json.loads(self.body())
        self.log.write(
            "tool_call",
            tool=payload["name"],
            call_id=payload["call_id"],
            arguments=payload["arguments"],
        )
        if payload["name"] == "set_timer":
            result = self.timers.set(**payload["arguments"])
        elif payload["name"] == "end_call":
            result = {"ok": True, "end_call": True, "delay_ms": 2000}
        else:
            result = self.manager.dispatch(payload["name"], payload["arguments"])
        self.log.write(
            "tool_result",
            tool=payload["name"],
            call_id=payload["call_id"],
            result=result,
        )
        self.respond_json(result)

    def record_event(self) -> None:
        payload = json.loads(self.body())
        kind = payload.pop("kind", "browser_event")
        token = self.headers.get("X-Dogwalk-Call")
        if token and not self.calls.touch(token):
            self.respond_json(
                {"ok": False, "error": "Walker call lease is not active."},
                status=HTTPStatus.CONFLICT,
            )
            return
        if not token and self.calls.active():
            self.respond_json(
                {"ok": False, "error": "Walker call token is required."},
                status=HTTPStatus.CONFLICT,
            )
            return
        self.log.write(kind, **payload)
        if kind == "browser_session_stopped":
            self.calls.release(token)
        self.respond_json({"ok": True})

    def require_call(self) -> bool:
        if self.calls.touch(self.headers.get("X-Dogwalk-Call")):
            return True
        self.respond_json(
            {"ok": False, "error": "Walker call lease is not active."},
            status=HTTPStatus.CONFLICT,
        )
        return False

    def respond_json(
        self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("DOGWALK_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("DOGWALK_PORT", "8765"))
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(os.environ.get("DOGWALK_WORKSPACE", ROOT)),
    )
    parser.add_argument(
        "--agent-command",
        default=os.environ.get(
            "DOGWALK_AGENT_COMMAND", "opencode acp --pure --cwd {cwd}"
        ),
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(os.environ.get("DOGWALK_LOG_DIR", ROOT / "logs")),
    )
    parser.add_argument(
        "--call-lease-seconds",
        type=float,
        default=float(os.environ.get("DOGWALK_CALL_LEASE_SECONDS", "15")),
    )
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise SystemExit(f"Workspace is not a directory: {workspace}")
    if not shlex.split(args.agent_command):
        raise SystemExit("DOGWALK_AGENT_COMMAND is empty.")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set.")
    Handler.log = SessionLog(directory=args.log_dir.expanduser().resolve())
    Handler.manager = SessionManager(
        Handler.log, workspace, agent_command=args.agent_command
    )
    Handler.api_key = api_key
    Handler.timers = TimerQueue(Handler.log)
    Handler.calls = CallLease(args.call_lease_seconds)
    Handler.started_at = time.monotonic()
    Handler.workspace = workspace
    Handler.agent_command = args.agent_command
    Handler.log.write(
        "service_start",
        mode="webrtc",
        model=MODEL,
        host=args.host,
        port=args.port,
        workspace=str(workspace),
        agent_command=args.agent_command,
    )
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True

    def stop_server(signum: int, frame: Any) -> None:
        Handler.log.write("service_signal", signal=signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    print(f"Dogwalk: http://{args.host}:{args.port}/webrtc_spike.html")
    print(f"Workspace: {workspace}")
    print(f"Log: {Handler.log.path}")
    try:
        server.serve_forever()
    finally:
        Handler.manager.close()
        Handler.log.write("service_stop")
        Handler.log.file.close()
        server.server_close()


if __name__ == "__main__":
    main()
