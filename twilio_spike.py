#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp", "audioop-lts"]
# ///
"""Twilio phone bridge spike: talk to Walker via a real phone call.

Exposes:
  POST /voice   - Twilio voice webhook (returns TwiML with <Connect><Stream>)
  WS   /stream  - Twilio media stream bridge (mulaw 8kHz <-> PCM16 24kHz)
  GET  /call    - trigger outbound call (?to=+15551234567)
  GET  /healthz - health check
  GET  /        - status page with call trigger

Env:
  OPENAI_API_KEY
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  TWILIO_PHONE_NUMBER

Setup:
  1. Buy a Twilio number ($1.15/mo)
  2. Expose publicly: tailscale funnel --bg <port>
  3. Point Twilio number voice webhook to https://<public>/voice
  4. Dial the number, or visit / to trigger an outbound call
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import BasicAuth, ClientSession, WSMsgType, web

import audioop

ROOT = Path(__file__).parent
REALTIME_URL = "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1"
TWILIO_RATE = 8000
REALTIME_RATE = 24000
SAMPLE_WIDTH = 2

INSTRUCTIONS = """
You are Walker, a warm, concise voice companion for a hands-free phone call.
Keep replies short and natural for spoken conversation. This is a test of a
new phone bridge, so be yourself and help the user see if it works.
""".strip()

GREETING = "I'll be your dog walker for today. What's on your mind?"


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
    print(f"{stamp} {kind} {payload}")


class CallBridge:
    """Bidirectional audio bridge: Twilio mulaw 8kHz <-> OpenAI PCM16 24kHz."""

    def __init__(self, twilio_ws: web.WebSocketResponse, openai_ws: object, stream_sid: str) -> None:
        self.twilio_ws = twilio_ws
        self.openai_ws = openai_ws
        self.stream_sid = stream_sid
        self._in_state: tuple[bytes, int] | None = None
        self._out_state: tuple[bytes, int] | None = None
        self._open = True

    async def twilio_to_openai(self, chunk_b64: str) -> None:
        if not self._open:
            return
        mulaw = base64.b64decode(chunk_b64)
        pcm = audioop.ulaw2lin(mulaw, SAMPLE_WIDTH)
        resampled, self._in_state = audioop.ratecv(
            pcm, SAMPLE_WIDTH, 1, TWILIO_RATE, REALTIME_RATE, self._in_state
        )
        await self.openai_ws.send_json({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(resampled).decode(),
        })

    async def openai_to_twilio(self, delta_b64: str) -> None:
        if not self._open:
            return
        pcm = base64.b64decode(delta_b64)
        resampled, self._out_state = audioop.ratecv(
            pcm, SAMPLE_WIDTH, 1, REALTIME_RATE, TWILIO_RATE, self._out_state
        )
        mulaw = audioop.lin2ulaw(resampled, SAMPLE_WIDTH)
        await self.twilio_ws.send_json({
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {"track": "outbound", "chunk": base64.b64encode(mulaw).decode()},
        })

    async def interrupt(self) -> None:
        if not self._open:
            return
        await self.twilio_ws.send_json({"event": "clear", "streamSid": self.stream_sid})

    def close(self) -> None:
        self._open = False


async def handle_stream(request: web.Request) -> web.WebSocketResponse:
    twilio_ws = web.WebSocketResponse()
    await twilio_ws.prepare(request)
    log("twilio_ws_connected")

    openai_ws = await request.app["http"].ws_connect(
        REALTIME_URL,
        headers={"Authorization": f"Bearer {request.app['openai_key']}"},
        heartbeat=30,
    )
    log("openai_ws_connected")

    await openai_ws.send_json({
        "type": "session.update",
        "session": {
            "instructions": INSTRUCTIONS,
            "voice": "cedar",
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {"model": "gpt-4o-mini-transcribe"},
            "turn_detection": {"type": "semantic_vad", "interrupt_response": True},
        },
    })

    bridge: CallBridge | None = None

    async def from_twilio() -> None:
        nonlocal bridge
        async for msg in twilio_ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                event = data.get("event", "")
                if event == "connected":
                    log("twilio_connected")
                elif event == "start":
                    stream_sid = data["start"]["streamSid"]
                    call_sid = data["start"].get("callSid")
                    bridge = CallBridge(twilio_ws, openai_ws, stream_sid)
                    log("stream_started", stream_sid=stream_sid, call_sid=call_sid)
                    await openai_ws.send_json({
                        "type": "response.create",
                        "response": {"instructions": f"Say: '{GREETING}'"},
                    })
                elif event == "media" and bridge:
                    track = data["media"].get("track", "inbound")
                    if track == "inbound":
                        await bridge.twilio_to_openai(data["media"]["chunk"])
                elif event == "stop":
                    log("stream_stopped")
                    break
            elif msg.type == WSMsgType.ERROR:
                log("twilio_ws_error")
                break

    async def from_openai() -> None:
        nonlocal bridge
        async for msg in openai_ws:
            if msg.type == WSMsgType.TEXT:
                event = json.loads(msg.data)
                etype = event.get("type", "")
                if etype == "response.output_audio.delta" and bridge:
                    await bridge.openai_to_twilio(event["delta"])
                elif etype == "input_audio_buffer.speech_started" and bridge:
                    await bridge.interrupt()
                    log("speech_started")
                elif etype == "input_audio_buffer.speech_stopped":
                    log("speech_stopped", audio_end_ms=event.get("audio_end_ms"))
                elif etype == "conversation.item.input_audio_transcription.completed":
                    log("transcript", speaker="user", text=event.get("transcript"))
                elif etype == "response.output_audio_transcript.done":
                    log("transcript", speaker="walker", text=event.get("transcript"))
                elif etype == "response.done":
                    log("response_done",
                        status=event.get("response", {}).get("status"),
                        usage=event.get("response", {}).get("usage"))
                elif etype == "error":
                    log("openai_error", error=event.get("error"))
            elif msg.type == WSMsgType.ERROR:
                log("openai_ws_error")
                break

    tasks = [asyncio.create_task(from_twilio()), asyncio.create_task(from_openai())]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    if bridge:
        bridge.close()
    if not openai_ws.closed:
        await openai_ws.close()
    log("bridge_closed")
    return twilio_ws


def twiml(public_url: str) -> str:
    host = public_url.replace("https://", "").replace("http://", "").rstrip("/")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response><Connect>"
        f'<Stream url="wss://{host}/stream" />'
        "</Connect></Response>"
    )


async def voice_webhook(request: web.Request) -> web.Response:
    log("voice_webhook_hit")
    return web.Response(text=twiml(request.app["public_url"]), content_type="text/xml")


async def trigger_call(request: web.Request) -> web.Response:
    to = request.query.get("to", "")
    if not to:
        return web.json_response(
            {"ok": False, "error": "Provide ?to=+15551234567"}, status=400
        )
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


async def healthz(request: web.Request) -> web.Response:
    return web.json_response({
        "ok": True,
        "uptime": round(time.monotonic() - request.app["started_at"], 1),
        "twilio_number": request.app["twilio_number"],
        "public_url": request.app["public_url"],
    })


async def index(request: web.Request) -> web.Response:
    number = request.app["twilio_number"]
    url = request.app["public_url"]
    return web.Response(
        text=f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font:14px monospace;max-width:28rem;margin:2rem auto;padding:1.5rem;
background:#07100f;color:#e1ebe6}}input,button{{font:inherit;padding:.4rem}}
button{{background:#101f1c;color:#b8ef62;border:1px solid #29413b}}</style></head>
<body><h1>Dogwalk Twilio Bridge</h1>
<p>Number: {number}<br>Public: {url}</p>
<form action="/call" method="get">
<input name="to" placeholder="+1..." style="width:12rem">
<button>Call me</button>
</form></body></html>""",
        content_type="text/html",
    )


async def on_startup(app: web.Application) -> None:
    app["http"] = ClientSession()
    app["started_at"] = time.monotonic()
    log("service_start", public_url=app["public_url"], twilio_number=app["twilio_number"])


async def on_cleanup(app: web.Application) -> None:
    await app["http"].close()
    log("service_stop")


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("DOGWALK_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DOGWALK_PORT", "8766")))
    parser.add_argument("--public-url", default=os.environ.get("PUBLIC_URL", ""))
    args = parser.parse_args()

    public_url = args.public_url.rstrip("/")
    if not public_url:
        raise SystemExit("Set --public-url or PUBLIC_URL (e.g. https://darknut.tailnet-4e85.ts.net)")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set.")
    twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    twilio_token = os.environ.get("TWILIO_AUTH_TOKEN")
    twilio_number = os.environ.get("TWILIO_PHONE_NUMBER")
    if not all([twilio_sid, twilio_token, twilio_number]):
        raise SystemExit("Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER")

    app = web.Application()
    app["openai_key"] = api_key
    app["twilio_sid"] = twilio_sid
    app["twilio_token"] = twilio_token
    app["twilio_number"] = twilio_number
    app["public_url"] = public_url
    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_post("/voice", voice_webhook)
    app.router.add_get("/call", trigger_call)
    app.router.add_get("/stream", handle_stream)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    print(f"Dogwalk Twilio Bridge: http://{args.host}:{args.port}")
    print(f"Public URL: {public_url}")
    print(f"Twilio number: {twilio_number}")
    print(f"Voice webhook: {public_url}/voice")
    print(f"Call trigger:  {public_url}/call?to=+1...")
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
