#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "sounddevice>=0.5.2,<0.6",
#   "websockets>=15,<16",
# ]
# ///
"""Minimal gpt-realtime Walker with duplex audio and ACP-shaped stub tools."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import queue
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sounddevice as sd
from websockets.asyncio.client import connect


SAMPLE_RATE = 24_000
CHANNELS = 1
CHUNK_MS = 20
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_MS // 1000
MODEL = "gpt-realtime-2.1"

INSTRUCTIONS = """
You are Walker, a warm, concise voice interface between the User and coding agents
called Dogs. You are socially fluent but engineering-weak, and honest about
that limitation. Never speak code or technical identifiers aloud. Describe
engineering work in plain language as shape and consequence.

Use the supplied tools whenever the User asks you to inspect, change, test, or
otherwise act on software. Do not pretend you performed work yourself. A Dog
is ephemeral and task-scoped. Give each new Dog a short pronounceable name
inspired by its task. Ask the User before consequential choices. Tool results are
authoritative, but they are deliberately stubbed in this prototype. Keep
spoken replies short enough for a hands-free conversation.
""".strip()

TOOLS = [
    {
        "type": "function",
        "name": "sic_dog",
        "description": "Start a fresh coding agent for one scoped task. The Dog receives its role and safety briefing automatically, so task only needs to describe the work.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short pronounceable Dog name.",
                },
                "task": {
                    "type": "string",
                    "description": "The engineering task to relay.",
                },
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
        "description": "Check a working Dog for progress or its final report.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "relay_to_dog",
        "description": "Relay a follow-up instruction or the User's decision to a working Dog.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["name", "message"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "call_off_dog",
        "description": "Cancel and release a working Dog.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
]


def load_dotenv(path: Path) -> None:
    """Load a tiny KEY=VALUE .env without adding a dependency."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class SessionLog:
    def __init__(self, directory: Path, mode: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = directory / f"{stamp}-{mode}.jsonl"
        self._file = self.path.open("a", encoding="utf-8", buffering=1)

    def write(self, kind: str, **data: Any) -> None:
        record = {
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "kind": kind,
            **data,
        }
        self._file.write(json.dumps(record, ensure_ascii=True) + "\n")

    def close(self) -> None:
        self._file.close()


@dataclass
class StubDog:
    task: str
    read_only: bool
    checks: int = 0
    messages: list[str] = field(default_factory=list)


class StubPack:
    """Deterministic fake backend. Replace dispatch() with an ACP adapter."""

    def __init__(self) -> None:
        self.dogs: dict[str, StubDog] = {}

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.35)  # Make the tool seam perceptible in conversation.
        dog_name = arguments.get("name", "")

        if name == "sic_dog":
            if dog_name in self.dogs:
                return {
                    "ok": False,
                    "error": f"A Dog named {dog_name} is already working.",
                }
            self.dogs[dog_name] = StubDog(arguments["task"], arguments["read_only"])
            return {
                "ok": True,
                "name": dog_name,
                "status": "working",
                "message": "The Dog accepted the task. Check it for progress shortly.",
            }

        dog = self.dogs.get(dog_name)
        if dog is None:
            return {"ok": False, "error": f"No active Dog named {dog_name}."}

        if name == "check_dog":
            dog.checks += 1
            if dog.checks == 1:
                return {
                    "ok": True,
                    "name": dog_name,
                    "status": "working",
                    "update": "The Dog is inspecting the project and has not changed anything yet.",
                }
            del self.dogs[dog_name]
            return {
                "ok": True,
                "name": dog_name,
                "status": "done",
                "report": (
                    "Stub report: the project has a small amount of loose work. "
                    "The Dog found one failing check related to an edge case. "
                    "No files were changed."
                ),
            }

        if name == "relay_to_dog":
            dog.messages.append(arguments["message"])
            return {
                "ok": True,
                "name": dog_name,
                "status": "working",
                "message": "Instruction relayed.",
            }

        if name == "call_off_dog":
            del self.dogs[dog_name]
            return {"ok": True, "name": dog_name, "status": "cancelled"}

        return {"ok": False, "error": f"Unknown tool {name}."}


class DuplexAudio:
    """PortAudio callbacks bridge device threads to asyncio and a playback FIFO."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.input: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        self.output: queue.SimpleQueue[bytes] = queue.SimpleQueue()
        self._playback = bytearray()
        self._playback_lock = threading.Lock()
        self._loop = loop
        self._input_stream: sd.RawInputStream | None = None
        self._output_stream: sd.RawOutputStream | None = None

    def start(self) -> None:
        def capture(indata: memoryview, frames: int, time: Any, status: Any) -> None:
            chunk = bytes(indata)

            def enqueue() -> None:
                if not self.input.full():
                    self.input.put_nowait(chunk)

            self._loop.call_soon_threadsafe(enqueue)

        def playback(outdata: memoryview, frames: int, time: Any, status: Any) -> None:
            with self._playback_lock:
                needed = len(outdata)
                while len(self._playback) < needed:
                    try:
                        self._playback.extend(self.output.get_nowait())
                    except queue.Empty:
                        break
                available = min(needed, len(self._playback))
                outdata[:available] = self._playback[:available]
                outdata[available:] = b"\x00" * (needed - available)
                del self._playback[:available]

        self._input_stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK_FRAMES,
            channels=CHANNELS,
            dtype="int16",
            callback=capture,
        )
        self._output_stream = sd.RawOutputStream(
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK_FRAMES,
            channels=CHANNELS,
            dtype="int16",
            callback=playback,
        )
        self._input_stream.start()
        self._output_stream.start()

    def clear_output(self) -> None:
        with self._playback_lock:
            self._playback.clear()
            while True:
                try:
                    self.output.get_nowait()
                except queue.Empty:
                    break

    def close(self) -> None:
        for stream in (self._input_stream, self._output_stream):
            if stream is not None:
                stream.stop()
                stream.close()


async def send_microphone(ws: Any, audio: DuplexAudio) -> None:
    while True:
        chunk = await audio.input.get()
        await ws.send(
            json.dumps(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode("ascii"),
                }
            )
        )


async def send_text_input(ws: Any, log: SessionLog, initial_prompt: str | None) -> None:
    prompt = initial_prompt
    while True:
        if prompt is None:
            prompt = await asyncio.to_thread(input, "you> ")
        text = prompt.strip()
        prompt = None
        if text.lower() in {"/quit", "/q"}:
            return
        if not text:
            continue
        log.write("transcript", speaker="user", text=text, source="typed")
        await ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    },
                }
            )
        )
        await ws.send(json.dumps({"type": "response.create"}))
        if initial_prompt is not None:
            return


async def handle_function_calls(
    ws: Any, event: dict[str, Any], pack: StubPack, log: SessionLog
) -> None:
    calls = [
        item
        for item in event["response"].get("output", [])
        if item.get("type") == "function_call"
    ]
    if not calls:
        return
    for call in calls:
        try:
            arguments = json.loads(call.get("arguments") or "{}")
            log.write(
                "tool_call",
                tool=call["name"],
                call_id=call["call_id"],
                arguments=arguments,
            )
            result = await pack.dispatch(call["name"], arguments)
        except Exception as exc:
            result = {"ok": False, "error": f"Tool failed: {type(exc).__name__}: {exc}"}
        log.write(
            "tool_result", tool=call["name"], call_id=call["call_id"], result=result
        )
        print(f"\n[tool] {call['name']}({arguments}) -> {result}")
        await ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(result),
                    },
                }
            )
        )
    await ws.send(json.dumps({"type": "response.create"}))


async def receive_events(
    ws: Any,
    audio: DuplexAudio | None,
    pack: StubPack,
    log: SessionLog,
    one_shot_done: asyncio.Event,
) -> None:
    async for raw in ws:
        event = json.loads(raw)
        event_type = event.get("type", "unknown")

        if event_type == "response.output_audio.delta" and audio is not None:
            audio.output.put(base64.b64decode(event["delta"]))
        elif event_type == "input_audio_buffer.speech_started":
            if audio is not None:
                audio.clear_output()
            log.write("speech_started", audio_start_ms=event.get("audio_start_ms"))
            print("\n[user speaking]")
        elif event_type == "input_audio_buffer.speech_stopped":
            log.write("speech_stopped", audio_end_ms=event.get("audio_end_ms"))
        elif event_type == "conversation.item.input_audio_transcription.completed":
            text = event.get("transcript", "")
            log.write("transcript", speaker="user", text=text, source="microphone")
            print(f"\nuser> {text}")
        elif event_type == "response.output_audio_transcript.done":
            text = event.get("transcript", "")
            log.write("transcript", speaker="walker", text=text, source="realtime")
            print(f"\nwalker> {text}")
        elif event_type == "response.output_text.done":
            text = event.get("text", "")
            log.write("transcript", speaker="walker", text=text, source="realtime")
            print(f"\nwalker> {text}")
        elif event_type == "response.done":
            response = event["response"]
            log.write(
                "response_done",
                response_id=response.get("id"),
                status=response.get("status"),
                status_details=response.get("status_details"),
                usage=response.get("usage"),
            )
            calls = [
                item
                for item in response.get("output", [])
                if item.get("type") == "function_call"
            ]
            await handle_function_calls(ws, event, pack, log)
            if not calls:
                one_shot_done.set()
        elif event_type == "error":
            log.write("api_error", error=event.get("error"))
            print(f"\n[API error] {event.get('error')}", file=sys.stderr)
        elif event_type in {"session.created", "session.updated"}:
            session = event.get("session", {})
            log.write(
                event_type.replace(".", "_"),
                session_id=session.get("id"),
                model=session.get("model"),
            )


async def run(args: argparse.Namespace) -> None:
    load_dotenv(Path(__file__).with_name(".env"))
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit(
            "OPENAI_API_KEY is not set (the spike also reads .env beside this script)."
        )

    mode = "text" if args.text or args.prompt else "audio"
    log = SessionLog(args.log_dir, mode)
    log.write("session_start", mode=mode, model=args.model, voice=args.voice)
    print(f"Log: {log.path}")

    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"wss://api.openai.com/v1/realtime?model={args.model}"
    pack = StubPack()
    audio: DuplexAudio | None = None
    worker: asyncio.Task[None] | None = None
    one_shot_done = asyncio.Event()

    try:
        async with connect(
            url, additional_headers=headers, max_size=16 * 1024 * 1024
        ) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "realtime",
                            "model": args.model,
                            "output_modalities": ["text"]
                            if mode == "text"
                            else ["audio"],
                            "instructions": INSTRUCTIONS,
                            "audio": {
                                "input": {
                                    "format": {
                                        "type": "audio/pcm",
                                        "rate": SAMPLE_RATE,
                                    },
                                    "transcription": {
                                        "model": "gpt-4o-mini-transcribe"
                                    },
                                    "turn_detection": {
                                        "type": "semantic_vad",
                                        "interrupt_response": True,
                                    },
                                },
                                "output": {
                                    "format": {
                                        "type": "audio/pcm",
                                        "rate": SAMPLE_RATE,
                                    },
                                    "voice": args.voice,
                                },
                            },
                            "tools": TOOLS,
                            "tool_choice": "auto",
                        },
                    }
                )
            )

            if mode == "audio":
                audio = DuplexAudio(asyncio.get_running_loop())
                audio.start()
                worker = asyncio.create_task(send_microphone(ws, audio))
                print("Listening. Speak naturally; Ctrl-C quits.")
            else:
                worker = asyncio.create_task(send_text_input(ws, log, args.prompt))

            receiver = asyncio.create_task(
                receive_events(ws, audio, pack, log, one_shot_done)
            )
            if args.prompt:
                await worker
                await asyncio.wait_for(one_shot_done.wait(), timeout=60)
                receiver.cancel()
            else:
                done, pending = await asyncio.wait(
                    {worker, receiver}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                for task in done:
                    task.result()
    finally:
        if worker is not None:
            worker.cancel()
        if audio is not None:
            audio.close()
        log.write("session_end")
        log.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--text",
        action="store_true",
        help="Use typed text instead of microphone and speaker.",
    )
    parser.add_argument(
        "--prompt", help="Send one text prompt (useful for protocol tests)."
    )
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--voice", default="marin")
    parser.add_argument(
        "--log-dir", type=Path, default=Path(__file__).with_name("logs")
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        print("\nStopped.")
