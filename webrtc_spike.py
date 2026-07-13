#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["agent-client-protocol==0.11.0"]
# ///
"""Local sideband server for the browser-based Walker WebRTC spike."""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import os
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
You are Walker, a warm, concise voice interface between the User and coding agents
called Dogs. You are socially fluent but engineering-weak, and honest about
that limitation. Never speak code or technical identifiers aloud. Describe
engineering work in plain language as shape and consequence.

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

DOG_BRIEFING = """You are {name}, a Dog in the Dogwalk system. Walker is attached
to the real human User through a live voice interface. Walker is a speech-to-speech
model with basic function calling: enough to start a task like the one below,
receive your report, relay it in plain language, and bring user questions back to
you. Walker is deliberately engineering-weak. You are the task-scoped engineering
agent: investigate the workspace and report useful findings to Walker. Do not
speak to the User directly or assume you have the voice conversation's history.

Safety mode: {safety}
End with a concise plain-language report for Walker to relay.

Task from Walker:
{task}"""

TOOLS = [
    {
        "type": "function",
        "name": "sic_dog",
        "description": "Start a fresh coding agent for one scoped task. This local spike accepts read-only Dogs only, so always set read_only true. The server automatically gives every Dog its role, safety mode, reporting expectations, and workspace context. In task, state only the work to do; do not repeat Dogwalk background or instructions.",
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
        "description": "Cancel and release a working or resting Dog and its retained ACP session.",
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
        "description": "Resolve a pending Dog permission request after the User chooses. Use the exact option_id supplied by the pending decision, or 'deny' to cancel it.",
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


class SessionLog:
    def __init__(self, mode: str = "webrtc") -> None:
        directory = ROOT / "logs"
        directory.mkdir(exist_ok=True)
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


class AcpPack:
    """Local stdio ACP adapter. A remote bridge can implement this same dispatch surface."""

    def __init__(self, log: SessionLog, cwd: Path, allow_writes: bool = False) -> None:
        self.log = log
        self.cwd = cwd
        self.runtime = AcpRuntime()
        self.allow_writes = allow_writes
        self.dogs: dict[str, dict[str, Any]] = {}
        self._completed: list[dict[str, Any]] = []
        self._pending_decisions: dict[str, dict[str, Any]] = {}
        self._decision_events: list[dict[str, Any]] = []
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
            return {"ok": True, "dogs": self.available_dogs()}
        if tool == "name_dog":
            return self.name_dog(arguments["current_name"], name)
        if tool == "sic_dog":
            if not arguments["read_only"] and not self.allow_writes:
                return {
                    "ok": False,
                    "error": "This local ACP spike accepts read-only Dogs only.",
                }
            with self._lock:
                if self._dog_for_name(name) is not None:
                    return {
                        "ok": False,
                        "error": f"A Dog named {name} already exists. Choose another name or continue it.",
                    }
                self.dogs[name] = {
                    "name": name,
                    "status": "working",
                    "task": arguments["task"],
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
                self.dogs[name]["future"] = self.runtime.submit(
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
            entry = self._dog_entry_for_name(name)
            if entry is None:
                return {"ok": False, "error": f"No Dog named {name}."}
            dog_key, dog = entry
            if tool == "check_dog":
                result = {"ok": True, "name": name, "status": dog["status"]}
                if dog["status"] == "resting":
                    result["report"] = dog["report"]
                elif dog["status"] == "failed":
                    result["error"] = dog["report"]
                else:
                    result["update"] = dog["activity"]
                return result
            if tool == "relay_to_dog":
                if dog["status"] in {"cancelled", "failed"}:
                    return {
                        "ok": False,
                        "error": f"{dog['name']} is {dog['status']} and cannot continue.",
                    }
                dog["status"] = "working"
                queued = self.runtime.submit(self._enqueue(dog_key, arguments["message"]))
                self._background.add(queued)
                queued.add_done_callback(self._background.discard)
                return {"ok": True, "name": dog["name"], "status": "working", "message": "Dog is continuing its retained session."}
            if tool == "call_off_dog":
                if dog["status"] in {"cancelled", "failed"}:
                    return {
                        "ok": False,
                        "error": f"{dog['name']} is already {dog['status']} and cannot be called off.",
                    }
                future = dog["future"]
                future.cancel()
                dog["status"] = "cancelled"
                self._cancel_decisions(dog_key)
                return {"ok": True, "name": dog["name"], "status": "cancelled"}
        return {"ok": False, "error": f"Unknown tool {tool}."}

    def _dog_for_name(self, name: str) -> dict[str, Any] | None:
        entry = self._dog_entry_for_name(name)
        return entry[1] if entry else None

    def _dog_entry_for_name(self, name: str) -> tuple[str, dict[str, Any]] | None:
        normalized = name.casefold().strip()
        return next(
            (
                (key, dog)
                for key, dog in self.dogs.items()
                if dog["name"].casefold() == normalized
            ),
            None,
        )

    def available_dogs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "name": dog["name"],
                    "status": dog["status"],
                    "task": dog["task"],
                    "activity": dog["activity"],
                    "title": dog["session_title"],
                    "last_updated": dog["updated_at"],
                }
                for dog in self.dogs.values()
                if dog["status"] not in {"cancelled", "failed"}
            ]

    def name_dog(self, current_name: str, name: str) -> dict[str, Any]:
        with self._lock:
            entry = self._dog_entry_for_name(current_name)
            if entry is None:
                return {"ok": False, "error": f"No Dog named {current_name}."}
            dog_key, dog = entry
            if self._dog_for_name(name) is not None:
                return {"ok": False, "error": f"A Dog named {name} already exists."}
            old_name = dog["name"]
            dog["name"] = name
            for decision in self._pending_decisions.values():
                if decision["dog_key"] == dog_key:
                    decision["dog"] = name
            for event in self._decision_events:
                if event["decision_id"] in self._pending_decisions:
                    event["dog"] = self._pending_decisions[event["decision_id"]]["dog"]
        self.log.write("dog_renamed", old_name=old_name, dog=name)
        return {"ok": True, "old_name": old_name, "name": name}

    async def _enqueue(self, name: str, message: str) -> None:
        while True:
            with self._lock:
                dog = self.dogs.get(name)
                if dog is None or dog["status"] in {"cancelled", "failed"}:
                    return
                queue = dog["queue"]
            if queue is not None:
                await queue.put(message)
                return
            await asyncio.sleep(0.01)

    async def _run(self, name: str, task: str) -> None:
        client = DogClient(self, name)
        read_only = self.dogs[name]["read_only"]
        safety = (
            "Read-only. Do not modify files, install dependencies, run commands that "
            "change state, or commit."
            if read_only
            else "Workspace changes are authorized for this test. Do not commit or make "
            "unrelated changes."
        )
        prompt = DOG_BRIEFING.format(name=name, task=task, safety=safety)
        try:
            async with spawn_agent_process(
                client,
                "opencode",
                "acp",
                "--pure",
                "--cwd",
                str(self.cwd),
                cwd=self.cwd,
            ) as (connection, _process):
                await connection.initialize(protocol_version=PROTOCOL_VERSION)
                session = await connection.new_session(
                    cwd=str(self.cwd), mcp_servers=[]
                )
                queue: asyncio.Queue[str] = asyncio.Queue()
                with self._lock:
                    dog = self.dogs[name]
                    dog["session_id"] = session.session_id
                    dog["queue"] = queue
                self.log.write(
                    "acp_session_started", dog=name, session_id=session.session_id
                )
                await connection.prompt(
                    session_id=session.session_id, prompt=[text_block(prompt)]
                )
                while True:
                    self._rest(name)
                    message = await queue.get()
                    with self._lock:
                        if self.dogs[name]["status"] == "cancelled":
                            return
                        self.dogs[name]["status"] = "working"
                        self.dogs[name]["report"] = ""
                    self.log.write("acp_session_continued", dog=name, session_id=session.session_id)
                    await connection.prompt(
                        session_id=session.session_id, prompt=[text_block(message)]
                    )
        except asyncio.CancelledError:
            self.log.write("dog_finished", dog=name, status="cancelled")
            raise
        except Exception as exc:
            with self._lock:
                self.dogs[name]["status"] = "failed"
                self.dogs[name]["report"] = f"ACP failure: {type(exc).__name__}: {exc}"
                self._completed.append(
                    {
                        "dog_key": name,
                        "status": "failed",
                        "report": self.dogs[name]["report"],
                    }
                )
            self.log.write(
                "dog_finished",
                dog=name,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _rest(self, name: str) -> None:
        with self._lock:
            dog = self.dogs[name]
            if dog["status"] != "working":
                return
            dog["status"] = "resting"
            dog["report"] = (
                dog["report"].strip()
                or f"The Dog finished without a textual report. Its last activity was: {dog['activity']}."
            )
            self._completed.append(
                {"dog_key": name, "status": "resting", "report": dog["report"]}
            )
        self.log.write("dog_resting", dog=name, status="resting")

    def update(self, name: str, update: Any) -> None:
        kind = type(update).__name__
        text = getattr(getattr(update, "content", None), "text", None)
        detail = str(update)[:2000]
        self.log.write(
            "acp_update",
            dog=name,
            update_type=kind,
            text=text,
            detail=detail,
        )
        with self._lock:
            dog = self.dogs[name]
            dog["updates"].append({"type": kind, "text": text, "detail": detail})
            dog["updates"] = dog["updates"][-50:]
            if kind in {"ToolCallStart", "ToolCallProgress"}:
                dog["activity"] = self.activity_gloss(update)
            if kind == "AgentMessageChunk" and text:
                self.dogs[name]["report"] += text
            if kind == "SessionInfoUpdate":
                dog["session_title"] = getattr(update, "title", None)
                dog["updated_at"] = getattr(update, "updated_at", None)
            if kind == "UsageUpdate":
                dog["usage"] = {
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
                    "name": dog["name"],
                    "status": dog["status"],
                    "task": dog["task"],
                    "activity": dog["activity"],
                    "report": dog["report"],
                    "updates": list(dog["updates"]),
                    "session": {
                        "id": dog["session_id"],
                        "title": dog["session_title"],
                        "updated_at": dog["updated_at"],
                        "usage": dog["usage"],
                    },
                }
                for name, dog in self.dogs.items()
            ]

    def take_completed(self) -> list[dict[str, Any]]:
        with self._lock:
            completed, self._completed = self._completed, []
            return [
                {
                    "name": self.dogs[event["dog_key"]]["name"],
                    "status": event["status"],
                    "report": event["report"],
                }
                for event in completed
            ]

    async def request_permission(
        self, name: str, session_id: str, tool_call: Any, options: list[Any]
    ) -> dict[str, Any]:
        decision_id = f"permission-{time.monotonic_ns()}"
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        with self._lock:
            dog = self.dogs[name]
            if dog["status"] == "cancelled":
                raise asyncio.CancelledError
            event = {
                "decision_id": decision_id,
                "dog": dog["name"],
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
            self._pending_decisions[decision_id] = {
                **event,
                "dog_key": name,
                "future": future,
            }
            self._decision_events.append(event)
        self.log.write("acp_permission_requested", **event)
        return await future

    async def create_elicitation(
        self, name: str, message: str, mode: Any
    ) -> dict[str, Any]:
        decision_id = f"elicitation-{time.monotonic_ns()}"
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        mode_data = mode.model_dump(mode="json", by_alias=True)
        with self._lock:
            dog = self.dogs[name]
            if dog["status"] == "cancelled":
                raise asyncio.CancelledError
            event = {
                "decision_id": decision_id,
                "dog": dog["name"],
                "kind": "question",
                "message": message,
                "schema": mode_data.get("requestedSchema"),
            }
            self._pending_decisions[decision_id] = {
                **event,
                "dog_key": name,
                "future": future,
            }
            self._decision_events.append(event)
        self.log.write("acp_elicitation_requested", **event)
        return await future

    def resolve_permission(self, decision_id: str, option_id: str) -> dict[str, Any]:
        with self._lock:
            decision = self._pending_decisions.pop(decision_id, None)
        if decision is None or decision["kind"] != "permission":
            return {"ok": False, "error": "No pending permission with that ID."}
        valid_ids = {option["option_id"] for option in decision["options"]}
        if option_id != "deny" and option_id not in valid_ids:
            return {"ok": False, "error": "That option is not offered by the Dog."}
        result = (
            {"outcome": {"outcome": "cancelled"}}
            if option_id == "deny"
            else {"outcome": {"outcome": "selected", "optionId": option_id}}
        )
        self._resolve_decision(decision, result)
        self.log.write(
            "acp_permission_resolved", decision_id=decision_id, option_id=option_id
        )
        return {"ok": True, "decision_id": decision_id, "option_id": option_id}

    def resolve_elicitation(
        self, decision_id: str, answer: dict[str, Any] | None, decline: bool
    ) -> dict[str, Any]:
        with self._lock:
            decision = self._pending_decisions.pop(decision_id, None)
        if decision is None or decision["kind"] != "question":
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

    def _cancel_decisions(self, dog_key: str) -> None:
        with self._lock:
            decisions = [
                self._pending_decisions.pop(decision_id)
                for decision_id, decision in list(self._pending_decisions.items())
                if decision["dog_key"] == dog_key
            ]
            cancelled_ids = {decision["decision_id"] for decision in decisions}
            self._decision_events = [
                event
                for event in self._decision_events
                if event["decision_id"] not in cancelled_ids
            ]
        for decision in decisions:
            future = decision["future"]
            future.get_loop().call_soon_threadsafe(future.cancel)

    def take_pending_decisions(self) -> list[dict[str, Any]]:
        with self._lock:
            decisions, self._decision_events = self._decision_events, []
        return decisions

    def pending_decisions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    key: value
                    for key, value in decision.items()
                    if key not in {"future", "dog_key"}
                }
                for decision in self._pending_decisions.values()
            ]

    def close(self) -> None:
        with self._lock:
            names = [
                dog["name"]
                for dog in self.dogs.values()
                if dog["status"] not in {"cancelled", "failed"}
            ]
        for name in names:
            self.dispatch("call_off_dog", {"name": name})
        for dog in self.dogs.values():
            future = dog["future"]
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


class DogClient(Client):
    def __init__(self, pack: AcpPack, name: str) -> None:
        self.pack = pack
        self.name = name

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.pack.update(self.name, update)

    async def request_permission(
        self, session_id: str, tool_call: Any, options: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return await self.pack.request_permission(
            self.name, session_id, tool_call, options
        )

    async def create_elicitation(
        self, message: str, mode: Any, **kwargs: Any
    ) -> dict[str, Any]:
        return await self.pack.create_elicitation(self.name, message, mode)


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


class Handler(SimpleHTTPRequestHandler):
    log: SessionLog
    pack: AcpPack
    timers: TimerQueue
    api_key: str

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/session":
            self.create_session()
        elif self.path == "/tool":
            self.run_tool()
        elif self.path == "/event":
            self.record_event()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/due":
            self.respond_json({"due": self.timers.take_due()})
        elif self.path == "/dog-events":
            self.respond_json({"completed": self.pack.take_completed()})
        elif self.path == "/decisions":
            self.respond_json({"decisions": self.pack.take_pending_decisions()})
        elif self.path == "/monitor":
            self.respond_json(
                {
                    "dogs": self.pack.monitor(),
                    "timers": self.timers.monitor(),
                    "decisions": self.pack.pending_decisions(),
                }
            )
        else:
            super().do_GET()

    def body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", "0")))

    def create_session(self) -> None:
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
            detail = exc.read().decode(errors="replace")
            self.log.write("session_error", status=exc.code, detail=detail)
            self.send_error(exc.code, detail)
            return
        self.log.write("session_created", transport="webrtc")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/sdp")
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)

    def run_tool(self) -> None:
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
            result = self.pack.dispatch(payload["name"], payload["arguments"])
        self.log.write(
            "tool_result",
            tool=payload["name"],
            call_id=payload["call_id"],
            result=result,
        )
        self.respond_json(result)

    def record_event(self) -> None:
        payload = json.loads(self.body())
        self.log.write(payload.pop("kind", "browser_event"), **payload)
        self.respond_json({"ok": True})

    def respond_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set.")
    Handler.log = SessionLog()
    Handler.pack = AcpPack(Handler.log, ROOT)
    Handler.api_key = api_key
    Handler.timers = TimerQueue(Handler.log)
    Handler.log.write("session_start", mode="webrtc", model=MODEL)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"WebRTC Walker: http://127.0.0.1:{args.port}/webrtc_spike.html")
    print(f"Log: {Handler.log.path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        Handler.pack.close()
        Handler.log.write("session_end")
        Handler.log.file.close()
        server.server_close()


if __name__ == "__main__":
    main()
