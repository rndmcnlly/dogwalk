#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp"]
# ///
"""Voice bridge spike: manifest-driven Realtime agent with async SSE notifications.

Three subcommands:
  mock-backend  : serves a dogwalk-themed manifest + tools + SSE events
  simulate     : text-in, audio-out bridge that talks to any backend
  serve        : full Twilio bridge (future, currently a stub)

Protocol (see voice-bridge-protocol.md):
  GET  /manifest           -> {instructions, tools[], voice, greeting}
  POST /tool/:name         -> {call_id, args} -> {result} or {error}
  GET  /events?call_id=... -> SSE stream of {type, message, speak}

Quick start:
  # Terminal 1: start the mock backend
  uv run --script voice_bridge.py mock-backend --port 8799

  # Terminal 2: run the bridge in text simulate mode
  uv run --script voice_bridge.py simulate --backend http://127.0.0.1:8799

  # Type a message, press enter. Try: "sic Rex on fixing the tests"
  # Then wait ~5 seconds for the async completion notification.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import BasicAuth, ClientSession, WSMsgType, web

ROOT = Path(__file__).parent
REALTIME_MODEL = os.environ.get("OPENAI_REALTIME_MODEL", "gpt-realtime")
REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"

BRIDGE_INSTRUCTIONS = (
    "You are on a phone call. Keep spoken replies short (1-2 sentences). "
    "Confirm names and numbers before acting on them. If a tool call takes time, "
    "say a brief filler like 'one moment' and then continue. "
    "When you receive a system notification, briefly and naturally relay it to the caller."
)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def log(kind: str, **data: object) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    payload = json.dumps(data, default=str) if data else ""
    print(f"{stamp} {kind} {payload}", file=sys.stderr)


# ═══ Mock Backend ═════════════════════════════════════════════════════

MOCK_INSTRUCTIONS = """\
You are Walker, a voice agent for managing coding sessions called Dogs.
Dogs are named personas for retained ACP coding sessions. You can list them,
sic (dispatch) them on tasks, and check their status. Keep replies brief
and conversational. Never speak technical identifiers aloud. When you sic a
Dog, tell the caller you'll let them know when it's done, then wait for the
completion notification before reporting results.
"""

MOCK_GREETING = "Walker here. What are we working on?"

MOCK_TOOLS = [
    {
        "type": "function",
        "name": "list_sessions",
        "description": "List all available managed sessions (Dogs) and their current states.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "sic",
        "description": "Dispatch a prompt to a Dog (managed session). Returns immediately with an acknowledgment; the completion arrives as a notification later.",
        "parameters": {
            "type": "object",
            "properties": {
                "dog_name": {"type": "string", "description": "The spoken name of the Dog"},
                "prompt": {"type": "string", "description": "What to tell the Dog to do"},
            },
            "required": ["dog_name", "prompt"],
        },
    },
    {
        "type": "function",
        "name": "check_dog",
        "description": "Check the current status of a Dog.",
        "parameters": {
            "type": "object",
            "properties": {
                "dog_name": {"type": "string", "description": "The spoken name of the Dog"},
            },
            "required": ["dog_name"],
        },
    },
]


class MockState:
    def __init__(self) -> None:
        self.dogs: dict[str, dict[str, Any]] = {
            "rex": {"name": "Rex", "status": "resting", "task": "fixed the auth bug"},
            "pixel": {"name": "Pixel", "status": "resting", "task": "wrote API docs"},
        }
        self.sse_queues: dict[str, asyncio.Queue] = {}

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {"name": d["name"], "status": d["status"], "task": d["task"]}
            for d in self.dogs.values()
        ]

    def sic(self, call_id: str, args: dict[str, Any]) -> dict[str, Any]:
        dog_name = args.get("dog_name", "Unknown")
        prompt = args.get("prompt", "")
        key = dog_name.lower()
        self.dogs[key] = {"name": dog_name, "status": "working", "task": prompt}
        return {
            "ok": True,
            "acknowledged": True,
            "message": f"Told {dog_name} to {prompt}. I'll let you know when it's done.",
        }

    def check_dog(self, args: dict[str, Any]) -> dict[str, Any]:
        dog_name = args.get("dog_name", "")
        key = dog_name.lower()
        dog = self.dogs.get(key)
        if not dog:
            available = ", ".join(d["name"] for d in self.dogs.values())
            return {"error": f"No Dog named {dog_name}. Available: {available}."}
        return {"name": dog["name"], "status": dog["status"], "task": dog["task"]}

    async def schedule_completion(self, call_id: str, dog_name: str, delay: float = 5.0) -> None:
        await asyncio.sleep(delay)
        key = dog_name.lower()
        if key in self.dogs:
            self.dogs[key]["status"] = "resting"
        queue = self.sse_queues.get(call_id)
        if queue:
            await queue.put({
                "type": "notification",
                "message": f"{dog_name} finished. Tests pass: 2 passed, 0 failed.",
                "speak": True,
            })


async def handle_manifest(request: web.Request) -> web.Response:
    caller = request.query.get("from", "")
    instructions = MOCK_INSTRUCTIONS
    greeting = MOCK_GREETING
    if caller:
        greeting = f"Walker here. Got a call from {caller}. What are we working on?"
    return web.json_response({
        "instructions": instructions,
        "tools": MOCK_TOOLS,
        "voice": "cedar",
        "greeting": greeting,
    })


async def handle_tool(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    body = await request.json()
    call_id = body.get("call_id", "unknown")
    args = body.get("args", {})
    state: MockState = request.app["mock_state"]

    if name == "list_sessions":
        return web.json_response({"result": {"sessions": state.list_sessions()}})

    if name == "sic":
        result = state.sic(call_id, args)
        asyncio.create_task(state.schedule_completion(call_id, args.get("dog_name", "")))
        return web.json_response({"result": result})

    if name == "check_dog":
        return web.json_response({"result": state.check_dog(args)})

    return web.json_response({"error": f"Unknown tool: {name}"}, status=404)


async def handle_events(request: web.Request) -> web.StreamResponse:
    call_id = request.query.get("call_id", "default")
    state: MockState = request.app["mock_state"]

    response = web.StreamResponse(
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive"}
    )
    await response.prepare(request)

    queue: asyncio.Queue = asyncio.Queue()
    state.sse_queues[call_id] = queue
    log("sse_opened", call_id=call_id)

    try:
        while True:
            event = await queue.get()
            await response.write(f"data: {json.dumps(event)}\n\n".encode())
            log("sse_sent", call_id=call_id, event=event)
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        state.sse_queues.pop(call_id, None)
        log("sse_closed", call_id=call_id)

    return response


def mock_backend_main(args: argparse.Namespace) -> None:
    app = web.Application()
    app["mock_state"] = MockState()
    app.router.add_get("/manifest", handle_manifest)
    app.router.add_post("/tool/{name}", handle_tool)
    app.router.add_get("/events", handle_events)
    log("mock_backend_start", port=args.port)
    web.run_app(app, host=args.host, port=args.port, print=None)


# ═══ Bridge (simulate mode) ════════════════════════════════════════════


def write_wav(pcm16_bytes: bytes, path: Path, rate: int = 24000) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm16_bytes)


async def play_wav(path: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        "afplay", str(path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def fetch_manifest(
    http: ClientSession, backend_url: str, params: dict[str, str] | None = None
) -> dict[str, Any]:
    async with http.get(f"{backend_url}/manifest", params=params) as resp:
        resp.raise_for_status()
        return await resp.json()


async def execute_tool_call(
    http: ClientSession, backend_url: str, name: str, session_call_id: str, args: dict[str, Any]
) -> dict[str, Any]:
    log("tool_call", name=name, session_call_id=session_call_id, args=args)
    try:
        async with http.post(
            f"{backend_url}/tool/{name}",
            json={"call_id": session_call_id, "args": args},
        ) as resp:
            result = await resp.json()
            log("tool_result", name=name, session_call_id=session_call_id, result=result)
            return result
    except Exception as exc:
        log("tool_error", name=name, error=str(exc))
        return {"error": str(exc)}


async def listen_sse(
    http: ClientSession, backend_url: str, call_id: str, openai_ws: object
) -> None:
    try:
        async with http.get(
            f"{backend_url}/events",
            params={"call_id": call_id},
            headers={"Accept": "text/event-stream"},
            timeout=None,
        ) as resp:
            log("sse_connected", call_id=call_id)
            async for raw_line in resp.content:
                line = raw_line.decode().strip()
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                log("sse_event", call_id=call_id, event=event)

                message = event.get("message", "")
                speak = event.get("speak", True)

                item_text = f"[System notification] {message}"
                await openai_ws.send_json({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": item_text}],
                    },
                })

                if speak:
                    await openai_ws.send_json({"type": "response.create"})
                    log("notification_injected", speak=True, message=message)
                else:
                    log("notification_injected", speak=False, message=message)

    except asyncio.CancelledError:
        pass
    except Exception as exc:
        log("sse_error", error=str(exc))


async def run_simulate(args: argparse.Namespace) -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set.")

    backend_url = args.backend.rstrip("/")

    async with ClientSession() as http:
        manifest = await fetch_manifest(http, backend_url)
        log("manifest_fetched", tools=[t["name"] for t in manifest.get("tools", [])])

        openai_ws = await http.ws_connect(
            REALTIME_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            heartbeat=30,
        )
        log("realtime_connected", model=REALTIME_MODEL)

        combined_instructions = BRIDGE_INSTRUCTIONS + "\n\n" + manifest.get("instructions", "")
        await openai_ws.send_json({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": combined_instructions,
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "turn_detection": {"type": "server_vad", "create_response": False, "interrupt_response": True},
                        "transcription": {"model": "gpt-4o-mini-transcribe"},
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "voice": manifest.get("voice", "alloy"),
                    },
                },
                "tools": manifest.get("tools", []),
                "tool_choice": "auto",
            },
        })

        call_id = f"sim_{int(time.time())}"
        sse_task = asyncio.create_task(listen_sse(http, backend_url, call_id, openai_ws))

        audio_chunks: list[bytes] = []
        session_ready = False
        response_in_flight = False

        loop = asyncio.get_event_loop()
        stdin_reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(stdin_reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        print("voice_bridge simulate: type messages and press enter (Ctrl+C to quit)")

        async def handle_response_done(event: dict[str, Any]) -> None:
            nonlocal response_in_flight
            response = event.get("response", {})
            outputs = response.get("output", [])

            has_tool_output = False
            for item in outputs:
                if item.get("type") != "function_call":
                    continue
                name = item.get("name", "")
                call_id_tool = item.get("call_id", "")
                args_text = item.get("arguments", "{}")
                try:
                    tool_args = json.loads(args_text)
                except json.JSONDecodeError:
                    tool_args = {"raw": args_text}

                result = await execute_tool_call(http, backend_url, name, call_id, tool_args)

                await openai_ws.send_json({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id_tool,
                        "output": json.dumps(result),
                    },
                })
                has_tool_output = True

            if has_tool_output:
                await openai_ws.send_json({"type": "response.create"})
                response_in_flight = True
            else:
                response_in_flight = False

                if audio_chunks and not args.no_play:
                    pcm = b"".join(audio_chunks)
                    wav_path = Path(f"voice_bridge_{int(time.time() * 1000)}.wav")
                    write_wav(pcm, wav_path)
                    await play_wav(wav_path)
                    wav_path.unlink(missing_ok=True)
                audio_chunks.clear()

        async def read_realtime() -> None:
            nonlocal session_ready, response_in_flight
            async for msg in openai_ws:
                if msg.type != WSMsgType.TEXT:
                    if msg.type == WSMsgType.ERROR:
                        log("realtime_error")
                        break
                    continue
                event = json.loads(msg.data)
                etype = event.get("type", "")

                if etype == "session.updated":
                    session_ready = True
                    greeting = manifest.get("greeting")
                    if greeting:
                        await openai_ws.send_json({
                            "type": "response.create",
                            "response": {"instructions": f"Say exactly: '{greeting}'"},
                        })
                        response_in_flight = True
                    log("session_ready")

                elif etype in ("response.output_audio_transcript.delta",):
                    delta = event.get("delta", "")
                    if delta:
                        print(f"assistant> {delta}", end="", flush=True)

                elif etype in ("response.output_audio_transcript.done",):
                    print()

                elif etype in ("response.output_audio.delta",):
                    delta = event.get("delta", "")
                    if delta:
                        pcm = base64.b64decode(delta)
                        audio_chunks.append(pcm)

                elif etype == "response.done":
                    await handle_response_done(event)

                elif etype == "error":
                    log("realtime_error", error=event.get("error"))

        async def read_stdin() -> None:
            nonlocal response_in_flight
            while True:
                line = (await stdin_reader.readline()).decode().strip()
                if not line:
                    continue
                if not session_ready:
                    print("session not ready yet", file=sys.stderr)
                    continue
                if response_in_flight:
                    print("response in flight; wait for completion", file=sys.stderr)
                    continue
                await openai_ws.send_json({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": line}],
                    },
                })
                await openai_ws.send_json({"type": "response.create"})
                response_in_flight = True

        tasks = [
            asyncio.create_task(read_realtime()),
            asyncio.create_task(read_stdin()),
            sse_task,
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    if not openai_ws.closed:
        await openai_ws.close()
    log("bridge_closed")


# ═══ Bridge (serve mode: Twilio PSTN) ══════════════════════════════════

# With g711_ulaw passthrough, Twilio's native 8kHz mulaw audio goes
# directly to OpenAI and back with no resampling. The base64 payloads
# pass through unchanged in both directions.


class AudioBridge:
    """Pass-through audio bridge: Twilio mulaw <-> OpenAI g711_ulaw."""

    def __init__(self, twilio_ws: web.WebSocketResponse, openai_ws: object, stream_sid: str) -> None:
        self.twilio_ws = twilio_ws
        self.openai_ws = openai_ws
        self.stream_sid = stream_sid
        self._open = True

    async def twilio_to_openai(self, payload_b64: str) -> None:
        if not self._open:
            return
        await self.openai_ws.send_json({
            "type": "input_audio_buffer.append",
            "audio": payload_b64,
        })

    async def openai_to_twilio(self, delta_b64: str) -> None:
        if not self._open:
            return
        await self.twilio_ws.send_json({
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {"track": "outbound", "payload": delta_b64},
        })

    async def interrupt(self) -> None:
        if not self._open:
            return
        await self.twilio_ws.send_json({"event": "clear", "streamSid": self.stream_sid})

    def close(self) -> None:
        self._open = False


def validate_twilio_signature(
    secret: str,
    public_url: str,
    path: str,
    post_params: dict[str, str] | None,
    signature: str | None,
) -> bool:
    """Validate X-Twilio-Signature header.

    Twilio computes HMAC-SHA1 over the full URL + sorted POST params
    (key+value concatenated), using the auth token as secret.
    """
    if not signature or not secret:
        return False

    url = f"{public_url.rstrip('/')}{path}"
    if post_params:
        for key in sorted(post_params):
            url += f"{key}{post_params[key]}"

    computed = base64.b64encode(
        hmac.new(secret.encode(), url.encode(), hashlib.sha1).digest()
    ).decode()

    return hmac.compare_digest(computed, signature)


def twiml(public_url: str, stream_params: dict[str, str] | None = None) -> str:
    host = public_url.replace("https://", "").replace("http://", "").rstrip("/")
    params_xml = ""
    if stream_params:
        params_xml = "".join(
            f'<Parameter name="{k}" value="{v}" />'
            for k, v in stream_params.items()
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response><Connect>"
        f'<Stream url="wss://{host}/stream">{params_xml}</Stream>'
        "</Connect></Response>"
    )


async def handle_voice_webhook(request: web.Request) -> web.Response:
    post_params: dict[str, str] = {}
    if request.method == "POST":
        form = await request.post()
        post_params = {k: str(v) for k, v in form.items()}

    secret: str = request.app.get("twilio_secret", "")
    signature = request.headers.get("X-Twilio-Signature", "")

    if secret:
        if validate_twilio_signature(secret, request.app["public_url"], "/voice",
                                      post_params if request.method == "POST" else None,
                                      signature):
            log("signature_valid")
        else:
            log("signature_invalid", signature=signature[:20] + "..." if signature else "none")
            return web.Response(status=403, text="Invalid Twilio signature")
    else:
        log("signature_skipped", reason="no twilio secret configured")

    caller = post_params.get("From", "")
    call_sid = post_params.get("CallSid", "")
    log("voice_webhook", method=request.method, caller=caller, call_sid=call_sid)

    stream_params: dict[str, str] = {}
    if caller:
        stream_params["from"] = caller
    if call_sid:
        stream_params["callSid"] = call_sid

    return web.Response(
        text=twiml(request.app["public_url"], stream_params),
        content_type="text/xml",
    )


async def handle_twilio_stream(request: web.Request) -> web.WebSocketResponse:
    twilio_ws = web.WebSocketResponse()
    await twilio_ws.prepare(request)
    log("twilio_ws_connected")

    http: ClientSession = request.app["http"]
    backend_url: str = request.app["backend_url"]
    api_key: str = request.app["openai_key"]

    openai_ws = await http.ws_connect(
        REALTIME_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        heartbeat=30,
    )
    log("realtime_connected", model=REALTIME_MODEL)

    bridge: AudioBridge | None = None
    call_id = f"call_{int(time.time())}"
    session_ready = False
    manifest: dict[str, Any] = {}
    sse_task: asyncio.Task | None = None

    async def from_twilio() -> None:
        nonlocal bridge, session_ready, manifest, call_id, sse_task
        async for msg in twilio_ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                event = data.get("event", "")
                if event == "connected":
                    log("twilio_connected")
                elif event == "start":
                    stream_sid = data["start"]["streamSid"]
                    call_sid = data["start"].get("callSid")
                    call_id = f"twilio_{call_sid}" if call_sid else call_id
                    custom = data["start"].get("customParameters", {})
                    caller = custom.get("from", "")
                    log("stream_started", stream_sid=stream_sid, call_sid=call_sid, caller=caller)

                    manifest_params: dict[str, str] = {}
                    if caller:
                        manifest_params["from"] = caller
                    if call_sid:
                        manifest_params["call_sid"] = call_sid

                    manifest = await fetch_manifest(http, backend_url, manifest_params)
                    log("manifest_fetched", tools=[t["name"] for t in manifest.get("tools", [])])

                    combined_instructions = BRIDGE_INSTRUCTIONS + "\n\n" + manifest.get("instructions", "")
                    await openai_ws.send_json({
                        "type": "session.update",
                        "session": {
                            "type": "realtime",
                            "instructions": combined_instructions,
                            "audio": {
                                "input": {
                                    "format": {"type": "audio/pcmu"},
                                    "turn_detection": {"type": "server_vad", "create_response": True, "interrupt_response": True},
                                    "transcription": {"model": "gpt-4o-mini-transcribe"},
                                },
                                "output": {
                                    "format": {"type": "audio/pcmu"},
                                    "voice": manifest.get("voice", "alloy"),
                                },
                            },
                            "tools": manifest.get("tools", []),
                            "tool_choice": "auto",
                        },
                    })

                    sse_task = asyncio.create_task(listen_sse(http, backend_url, call_id, openai_ws))
                    bridge = AudioBridge(twilio_ws, openai_ws, stream_sid)

                elif event == "media" and bridge:
                    track = data["media"].get("track", "inbound")
                    if track == "inbound":
                        await bridge.twilio_to_openai(data["media"]["payload"])
                elif event == "stop":
                    log("stream_stopped")
                    break
            elif msg.type == WSMsgType.ERROR:
                log("twilio_ws_error")
                break

    async def from_openai() -> None:
        nonlocal bridge, session_ready
        async for msg in openai_ws:
            if msg.type != WSMsgType.TEXT:
                if msg.type == WSMsgType.ERROR:
                    log("realtime_ws_error")
                    break
                continue
            event = json.loads(msg.data)
            etype = event.get("type", "")

            if etype == "session.updated":
                session_ready = True
                log("session_ready")
                if manifest.get("greeting"):
                    await openai_ws.send_json({
                        "type": "response.create",
                        "response": {"instructions": f"Say exactly: '{manifest['greeting']}'"},
                    })

            elif etype == "response.audio.delta":
                if bridge:
                    await bridge.openai_to_twilio(event["delta"])
                else:
                    log("audio_delta_no_bridge")

            elif etype == "response.output_audio.delta":
                if bridge:
                    await bridge.openai_to_twilio(event["delta"])
                else:
                    log("audio_delta_no_bridge")

            elif etype == "input_audio_buffer.speech_started" and bridge:
                await bridge.interrupt()
                log("barge_in")

            elif etype == "conversation.item.input_audio_transcription.completed":
                log("transcript", speaker="user", text=event.get("transcript"))

            elif etype == "response.output_audio_transcript.done":
                log("transcript", speaker="walker", text=event.get("transcript"))

            elif etype == "response.audio_transcript.done":
                log("transcript", speaker="walker", text=event.get("transcript"))

            elif etype == "response.done":
                response = event.get("response", {})
                outputs = response.get("output", [])
                has_tool_output = False

                for item in outputs:
                    if item.get("type") != "function_call":
                        continue
                    name = item.get("name", "")
                    func_call_id = item.get("call_id", "")
                    args_text = item.get("arguments", "{}")
                    try:
                        tool_args = json.loads(args_text)
                    except json.JSONDecodeError:
                        tool_args = {"raw": args_text}

                    result = await execute_tool_call(http, backend_url, name, call_id, tool_args)
                    await openai_ws.send_json({
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": func_call_id,
                            "output": json.dumps(result),
                        },
                    })
                    has_tool_output = True

                if has_tool_output:
                    await openai_ws.send_json({"type": "response.create"})

            elif etype == "error":
                log("realtime_error", error=event.get("error"))

            elif etype.startswith("response.") or etype.startswith("audio."):
                log("realtime_event", type=etype)

    tasks = [
        asyncio.create_task(from_twilio()),
        asyncio.create_task(from_openai()),
    ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    if sse_task and not sse_task.done():
        sse_task.cancel()
        try:
            await sse_task
        except asyncio.CancelledError:
            pass

    if bridge:
        bridge.close()
    if not openai_ws.closed:
        await openai_ws.close()
    log("bridge_closed")
    return twilio_ws


async def trigger_outbound_call(request: web.Request) -> web.Response:
    to = request.query.get("to", "")
    if not to:
        return web.json_response({"ok": False, "error": "Provide ?to=+15551234567"}, status=400)
    sid = request.app["twilio_sid"]
    token = request.app["twilio_token"]
    number = request.app["twilio_number"]
    public_url = request.app["public_url"]
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"
    async with request.app["http"].post(
        url,
        data={"To": to, "From": number, "Url": f"{public_url}/voice"},
        auth=BasicAuth(sid, token),
    ) as resp:
        result = await resp.json()
    log("outbound_call", to=to, status=resp.status, sid=result.get("sid"))
    return web.json_response({"ok": resp.status == 201, "result": result})


async def serve_healthz(request: web.Request) -> web.Response:
    return web.json_response({
        "ok": True,
        "backend": request.app["backend_url"],
        "public_url": request.app.get("public_url", ""),
        "twilio_number": request.app.get("twilio_number", ""),
    })


async def serve_on_startup(app: web.Application) -> None:
    app["http"] = ClientSession()
    app["started_at"] = time.monotonic()
    log("serve_start", backend=app["backend_url"], public_url=app.get("public_url", ""))


async def serve_on_cleanup(app: web.Application) -> None:
    await app["http"].close()
    log("serve_stop")


def serve_main(args: argparse.Namespace) -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set.")

    public_url = args.public_url.rstrip("/")
    if not public_url:
        raise SystemExit("Set --public-url or PUBLIC_URL (e.g. https://darknut.tailnet-4e85.ts.net)")

    twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    twilio_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    twilio_number = os.environ.get("TWILIO_PHONE_NUMBER", "")
    twilio_secret = os.environ.get("TWILIO_AUTH_TOKEN", "") or os.environ.get("TWILIO_CLIENT_SECRET", "")
    if not twilio_sid or not twilio_token:
        log("serve_warning", msg="TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN not set; outbound /call disabled, inbound still works")
    if not twilio_secret:
        log("serve_warning", msg="No Twilio secret for signature validation; webhook will accept unverified requests")

    app = web.Application()
    app["openai_key"] = api_key
    app["backend_url"] = args.backend.rstrip("/")
    app["public_url"] = public_url
    app["twilio_sid"] = twilio_sid
    app["twilio_token"] = twilio_token
    app["twilio_number"] = twilio_number
    app["twilio_secret"] = twilio_secret
    app.router.add_get("/healthz", serve_healthz)
    app.router.add_route("GET", "/voice", handle_voice_webhook)
    app.router.add_route("POST", "/voice", handle_voice_webhook)
    app.router.add_get("/call", trigger_outbound_call)
    app.router.add_get("/stream", handle_twilio_stream)
    app.on_startup.append(serve_on_startup)
    app.on_cleanup.append(serve_on_cleanup)

    print(f"Voice bridge serve: http://{args.host}:{args.port}")
    print(f"Public URL: {public_url}")
    print(f"Backend: {app['backend_url']}")
    print(f"Twilio number: {twilio_number}")
    print(f"Voice webhook: {public_url}/voice")
    print(f"Call trigger:  {public_url}/call?to=+1...")
    web.run_app(app, host=args.host, port=args.port, print=None)


# ═══ CLI ═══════════════════════════════════════════════════════════════


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    mb = sub.add_parser("mock-backend", help="Serve the mock dogwalk backend")
    mb.add_argument("--host", default="127.0.0.1")
    mb.add_argument("--port", type=int, default=8799)

    sim = sub.add_parser("simulate", help="Text-in, audio-out bridge (no Twilio)")
    sim.add_argument("--backend", default="http://127.0.0.1:8799")
    sim.add_argument("--no-play", action="store_true", help="Disable audio playback")

    serve = sub.add_parser("serve", help="Full Twilio PSTN bridge")
    serve.add_argument("--backend", default="http://127.0.0.1:8799")
    serve.add_argument("--host", default=os.environ.get("DOGWALK_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("DOGWALK_PORT", "8766")))
    serve.add_argument("--public-url", default=os.environ.get("PUBLIC_URL", ""))

    args = parser.parse_args()

    if args.command == "mock-backend":
        mock_backend_main(args)
    elif args.command == "simulate":
        asyncio.run(run_simulate(args))
    elif args.command == "serve":
        serve_main(args)


if __name__ == "__main__":
    main()
