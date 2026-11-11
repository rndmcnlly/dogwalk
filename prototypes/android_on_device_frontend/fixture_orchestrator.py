# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.9"]
# ///
"""A scripted FIXTURE Session Orchestrator for the on-device Voice Frontend app.

It is not the real Orchestrator and has no model, Sandbox, or ACP Agent. It speaks the same
sideband shapes main's VoiceSession uses with a Voice Frontend: it reads transcript deltas and
`session.delegation.created` Handoffs, cuts the transcript the way `takeTranscript` does, and
answers with `session.thinking.append` (brief) and `session.commentary.append` (say).
It announces itself with `dev.fixture.ready` so the app tells the caller it is a fixture.

uv run --script fixture_orchestrator.py            # listen on 127.0.0.1:8770 (use `adb reverse`)
uv run --script fixture_orchestrator.py test       # self-test with a scripted client
"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

from aiohttp import WSMsgType, web

HOST, PORT = "127.0.0.1", int(os.environ.get("DOGWALK_FIXTURE_PORT", "8770"))
LOG_DIR = Path(__file__).resolve().parent / ".tools"


class Call:
    def __init__(self, ws: web.WebSocketResponse, log):
        self.ws, self.log = ws, log
        self.transcript: list[dict] = []
        self.cut_ms = 0.0

    async def send(self, kind: str, content: str, delegation_id: str | None = None):
        lane = {"say": "session.commentary.append", "brief": "session.thinking.append"}[kind]
        await self.ws.send_json({"type": lane, "delegation_id": delegation_id, "content": content})
        self.log({"out": kind, "delegation_id": delegation_id, "content": content})

    def take_transcript(self, offset_ms: float) -> list[str]:
        # Mirrors VoiceSession.takeTranscript: since the last cut, up to the Handoff offset.
        lines = [f"{'Caller' if e['role'] == 'caller' else 'Voice'}: {e['text'].strip()}"
                 for e in self.transcript if e["end"] > self.cut_ms and e["start"] <= offset_ms + 1500]
        if self.transcript:
            self.cut_ms = max(self.cut_ms, min(offset_ms, self.transcript[-1]["end"]))
        return lines

    async def handoff(self, delegation_id: str, offset_ms: float):
        await asyncio.sleep(0.3)  # the Worker's grace period for late transcript deltas
        lines = self.take_transcript(offset_ms)
        self.log({"handoff": delegation_id, "transcript": lines})
        said = " ".join(line.split(": ", 1)[1] for line in lines if line.startswith("Caller:")).lower()
        d = delegation_id
        if re.search(r"\b(goodbye|bye|hang up)\b", said):
            await self.send("say", "Goodbye for now. Your sessions keep their work.", d)
            await asyncio.sleep(4)
            await self.ws.close()
        elif re.search(r"\b(cancel|abort)\b", said):
            await self.send("brief", "Fixture: Fir's current turn was cancelled; Fir stays open.", d)
            await self.send("say", "I cancelled Fir's current turn. Fir is still open for more work.", d)
        elif re.search(r"\b(allow|yes|approve)\b", said):
            await self.send("say", "Fir is allowed to delete the build folder this once, and it carried on.", d)
        elif re.search(r"\b(deny|no)\b", said):
            await self.send("say", "Fir will not delete the build folder.", d)
        elif re.search(r"\b(start|build|fix|run)\b", said):
            await self.send("brief", "Fixture: began a Prompt Turn in Fir to clean and rebuild.", d)
            await self.send("say", "Fir is starting on the rebuild now.", d)
            await asyncio.sleep(2)
            await self.send("say", "Fir wants permission to delete the build folder before rebuilding. "
                                   "Should I allow it once, or deny it?", d)
        elif re.search(r"\b(two|both)\b", said):
            # Two distinct requests in one burst: each must get its own phrasing and receipt.
            await self.send("say", "Oak finished the test suite, and all forty tests passed.", d)
            await self.send("say", "Fir has a question: should the rebuild target staging or production?", d)
        elif re.search(r"\b(status|doing|sessions?|progress)\b", said):
            await self.send("brief", "Fixture state: Fir is idle. Oak is running the test suite.", d)
            await self.send("say", "Oak is running the test suite, and Fir is idle.", d)
        else:
            await self.send("say", "This test fixture only knows status, start, allow, deny, cancel, two things, and goodbye.", d)


async def websocket(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"fixture-{int(time.time())}.jsonl"
    out = path.open("a")

    def log(event: dict):
        event = {"t": round(time.time(), 3), **event}
        out.write(json.dumps(event) + "\n"); out.flush()
        print(json.dumps(event), flush=True)

    call = Call(ws, log)
    log({"connected": str(request.remote), "log": str(path)})
    await ws.send_json({"type": "dev.fixture.ready", "description": "scripted fixture, no Sandbox or ACP Agent"})
    await call.send("brief", "Fixture sessions: Fir (idle) and Oak (running tests). No real work happens.")
    await call.send("say", "Hello from the test fixture. Fir and Oak are here.")
    tasks = set()
    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        m = json.loads(msg.data)
        if m.get("type") in ("session.input_transcript.delta", "session.output_transcript.delta"):
            role = "caller" if m["type"].startswith("session.input") else "voice"
            call.transcript.append({"role": role, "text": m["delta"], "start": m["start_ms"], "end": m["end_ms"]})
            log({"in": role, "text": m["delta"], "start_ms": m["start_ms"], "end_ms": m["end_ms"],
                 "delivery": m.get("delivery", "speech")})
        elif m.get("type") == "session.delegation.created" and m.get("delegation", {}).get("target") == "client":
            log({"in": "handoff", "delegation_id": m["delegation"]["id"], "offset_ms": m.get("offset_ms")})
            task = asyncio.create_task(call.handoff(m["delegation"]["id"], float(m.get("offset_ms", 1e12))))
            tasks.add(task); task.add_done_callback(tasks.discard)
        else:
            log({"in": "unexpected", "type": m.get("type")})
    log({"closed": True})
    out.close()
    return ws


def app() -> web.Application:
    a = web.Application()
    a.router.add_get("/ws", websocket)
    return a


async def self_test():
    import aiohttp
    runner = web.AppRunner(app()); await runner.setup()
    site = web.TCPSite(runner, HOST, 0); await site.start()
    port = site._server.sockets[0].getsockname()[1]
    async with aiohttp.ClientSession() as s, s.ws_connect(f"http://{HOST}:{port}/ws") as ws:
        first = [await ws.receive_json() for _ in range(3)]
        assert [m["type"] for m in first] == ["dev.fixture.ready", "session.thinking.append", "session.commentary.append"], first
        await ws.send_json({"type": "session.input_transcript.delta", "delta": "start the rebuild", "start_ms": 100, "end_ms": 900})
        await ws.send_json({"type": "session.delegation.created", "offset_ms": 900, "delegation": {"id": "android_1", "target": "client"}})
        replies = [await asyncio.wait_for(ws.receive_json(), 5) for _ in range(3)]
        assert all(r["delegation_id"] == "android_1" for r in replies), replies
        assert "permission" in replies[2]["content"], replies
    await runner.cleanup()
    print("fixture self-test: ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["test"]:
        asyncio.run(self_test())
    else:
        print(f"FIXTURE Orchestrator on ws://{HOST}:{PORT}/ws (not the real Session Orchestrator)")
        web.run_app(app(), host=HOST, port=PORT, print=None)
