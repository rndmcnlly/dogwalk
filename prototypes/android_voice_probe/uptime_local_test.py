# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.11,<4", "grpcio==1.76.0", "grpcio-tools==1.76.0", "protobuf==6.33.0", "numpy>=2.0"]
# ///
"""Bounded real Android ASR/TTS test with explicitly simulated uptime downstream.

uv run --script uptime_local_test.py --manifest .tools/uptime-fixtures/manifest.json
No deployment, ACP action, text injection, host microphone, or duplex changes.
Optional --live-model uses OPENROUTER_API_KEY, at most two model decisions.
"""
import argparse
import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from aiohttp import ClientSession, WSMsgType, web

import audio_bridge as bridge
from frontend_app_smoke import PACKAGE, adb, configure, controls, events, tap
from frontend_server import DEFAULT_MODEL, FixtureModel, Frontend, OpenRouterModel, SafeLog, Usage
from interaction_test import capture, load_audio, normalized, packet_layout, place, save_audio

ROOT = Path(__file__).resolve().parent
QUESTION = "How long has the sandbox been running?"
ANSWER = "Fixture result: the simulated sandbox has been running for two hours and seventeen minutes."


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def fixture_path(manifest_path):
    manifest = json.loads(manifest_path.read_text())
    rows = manifest.get("fixtures", [])
    if isinstance(rows, dict):
        fixture = rows["uptime"]
    else:
        fixture = next(row for row in rows if row["id"] == "uptime")
    assert normalized(fixture["text"]) == normalized(QUESTION), "Unexpected fixture question"
    path = Path(fixture["wav"])
    if not path.is_absolute() and not path.exists():
        path = manifest_path.parent / path
    pcm, channels, width, rate = bridge.read_wav(path)
    assert (channels, width, rate) == (1, 2, 16000), "Fixture must be 16kHz mono PCM16"
    return path.resolve(), hashlib.sha256(pcm).hexdigest()


class BoundedModel:
    def __init__(self, model, report):
        self.model, self.report = model, report
        self.calls = 0

    async def decide(self, messages):
        self.calls += 1
        assert self.calls <= 2, "Only two model decisions authorized"
        decision = await self.model.decide(messages)
        self.report.setdefault("model_decisions", []).append(decision)
        return decision


def review(out, source, captured, injection, intervals):
    """Actual audio only in the conversation; separately labelled review narration."""
    intro_text = ("Local fixture review. Breeze supplies the user question. Android supplies the captured reply. "
                  "Sandbox uptime is simulated. This is not a real session or production test. Begin recording.")
    subprocess.run(["/usr/bin/say", "-o", str(out / "review-intro.aiff"), intro_text], check=True,
                   capture_output=True, timeout=20)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out / "review-intro.aiff"),
                    "-ar", "48000", "-ac", "1", str(out / "review-intro.wav")],
                   check=True, capture_output=True, timeout=20)
    intro = load_audio(out / "review-intro.wav")
    raw, _, _, _ = bridge.read_wav(out / "android-raw.wav")
    stereo = np.frombuffer(raw, dtype="<i2").reshape(-1, 2).astype(np.float32) / 32768
    user = load_audio(source)
    origin = captured["origin_monotonic"]
    input_start = max(0, round((injection - origin) * 48000)) if injection else 0
    layout = packet_layout(captured["packets"])
    length = max(input_start + len(user) if injection else 0,
                 max((start + size for start, size in layout), default=0))
    lead = len(intro) + 48000
    timeline = np.zeros((lead + length + 48000, 2), dtype=np.float32)
    place(timeline, intro, 0)
    offset = 0
    for start, size in layout:
        place(timeline, stereo[offset:offset + size], lead + start)
        offset += size
    if injection:
        place(timeline, user, lead + input_start)
    save_audio(out / "review.wav", timeline)
    labels = {"intro": {"role": "review narrator, not conversation", "text": intro_text,
                        "start_seconds": 0, "end_seconds": len(intro) / 48000},
              "fixture_boundary_seconds": lead / 48000,
              "user": {"role": "Breeze input, original source PCM explicitly resampled for review only",
                       "text": QUESTION, "injected": injection is not None,
                       "start_seconds": (lead + input_start) / 48000 if injection else None},
              "android": {"role": "actual captured emulator output, including earcons", "speeches": [
                  {**item, "review_start_seconds": lead / 48000 + item["start"] - origin,
                   "review_end_seconds": lead / 48000 + item["end"] - origin if item.get("end") else None}
                  for item in intervals]},
              "downstream": "explicit simulated uptime fixture; no ACP or actual sandbox query",
              "timing": "input injection host monotonic; output SDK packet completion timestamps"}
    write_json(out / "review-labels.json", labels)
    (out / "review.html").write_text('<!doctype html><meta charset="utf-8"><h1>Local simulated uptime fixture</h1>'
        '<p>Review narrator, then Breeze user input and actual Android output. No real sandbox query.</p>'
        '<audio controls src="review.wav"></audio><p>See review-labels.json and result.json for roles, timing and outcome.</p>')


async def main(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"passed": False, "mode": "fixture", "downstream": "simulated, no ACP or real sandbox",
              "live_model": args.live_model, "checks": {"actual_asr": False, "handoff": False,
              "order": False, "tts_completion": False, "reply_signal": False}, "voice_input": "not attempted"}
    source, sha = fixture_path(args.manifest)
    report.update(fixture_wav=str(source), fixture_pcm_sha256=sha, expected_question=QUESTION)
    token = secrets.token_urlsafe(32)
    wire, upstream, outbound, intervals = [], [], [], []
    connected, completed = asyncio.Event(), asyncio.Event()
    http = ClientSession()
    log, offset, ui, injected, captured = SafeLog(), None, None, None, None
    ready, finish = threading.Event(), threading.Event()
    capture_future = None
    core = None
    tasks = []
    usage = Usage(log)

    async def fixture_response():
        # Defer response until the neutral acknowledgement really completed. This
        # avoids manufacturing callback evidence or racing gateway epoch guards.
        while not any(e["type"] == "speech_finished" and e.get("outcome") == "completed" for e in wire):
            await asyncio.sleep(.05)
        await core.worker({"type": "session.commentary.append", "content": ANSWER})

    class Upstream:
        async def send(self, event):
            upstream.append({"host_monotonic": time.monotonic(), **event})
            if event["type"] == "session.delegation.created":
                transcript = next(e["delta"].strip() for e in upstream if e["type"] == "session.input_transcript.delta")
                assert normalized(transcript) == normalized(QUESTION), "Unrequested fixture uptime"
                tasks.append(asyncio.create_task(fixture_response()))

    async def websocket(request):
        if request.headers.get("Authorization") != "Bearer " + token:
            return web.Response(status=401)
        peer = web.WebSocketResponse()
        await peer.prepare(request)

        async def emit(event):
            outbound.append({"host_monotonic": time.monotonic(), **event})
            await peer.send_json(event)

        nonlocal core
        prompt = args.instructions_file.read_text()
        report["instructions_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
        prompt += ("\nLOCAL FIXTURE: Handoff the uptime question. When asked to phrase commentary, "
                   "preserve the explicit words Fixture result and simulated sandbox, and the exact duration. "
                   "No real session was contacted. Do not invent any facts.")
        model = OpenRouterModel(http, os.environ["OPENROUTER_API_KEY"], args.model, usage) if args.live_model else FixtureModel()
        core = Frontend(emit, Upstream(), BoundedModel(model, report), prompt, log, mode="fixture")
        await peer.send_json({"type": "ready", "protocol": 1, "mode": "fixture", "session_id": "local-uptime-fixture",
                              "frontend_model": args.model if args.live_model else "fixture/deterministic"})
        connected.set()
        async for message in peer:
            if message.type != WSMsgType.TEXT:
                continue
            event = json.loads(message.data)
            wire.append({"host_monotonic": time.monotonic(), **event})
            if event["type"] == "speech_started":
                speech = core.speeches[event["speech_id"]]
                intervals.append({"speech_id": event["speech_id"], "text": speech.text, "start": time.monotonic()})
            await core.android(event)
            if event["type"] == "speech_finished":
                for interval in intervals:
                    if interval["speech_id"] == event["speech_id"]:
                        interval.update(end=time.monotonic(), outcome=event["outcome"])
                if any(e["type"] == "session.output_transcript.delta" and "seventeen" in e["delta"] for e in upstream):
                    completed.set()
        return peer

    app = web.Application()
    app.router.add_get("/ws", websocket)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    channel = None
    try:
        await asyncio.to_thread(adb, "shell", "am", "force-stop", PACKAGE)
        await asyncio.to_thread(adb, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
        await asyncio.to_thread(adb, "shell", "wm", "dismiss-keyguard")
        await asyncio.to_thread(configure, f"ws://10.0.2.2:{port}/ws", token)
        ui = await asyncio.to_thread(controls)
        offset = len(await asyncio.to_thread(events))
        pb, pb_grpc = bridge.protocol()
        channel, stub, metadata = bridge.connection(pb_grpc)
        capture_future = asyncio.create_task(asyncio.to_thread(capture, stub, pb, metadata, out, ready, finish))
        assert await asyncio.to_thread(ready.wait, 5), "Capture not ready"
        await asyncio.to_thread(tap, ui["START LOCAL AUDIO"])
        await asyncio.wait_for(connected.wait(), 12)
        # Only active AudioFlinger is required, not a recognizer-ready callback.
        # Keep this immediate preflight: injectAudio without an open input can
        # crash this emulator natively. Never bypass this safety check.
        await asyncio.to_thread(bridge.wait_for_microphone, 5)
        report["active_microphone_before_injection"] = True
        report["ready_before_injection"] = any(e["event"] == "listening" for e in (await asyncio.to_thread(events))[offset:])
        injected = time.monotonic()
        report["injection_monotonic"] = injected
        report["voice_input"] = "Breeze WAV injected into active emulator microphone"
        report["injected"] = await asyncio.to_thread(bridge.inject, stub, pb, metadata, source, 15)
        try:
            await asyncio.wait_for(completed.wait(), 48 if args.live_model else 22)
        except TimeoutError:
            raise AssertionError("Actual voice input failed: no completed fixture answer within bounded deadline") from None
        observed = (await asyncio.to_thread(events))[offset:]
        finals = [e["detail"] for e in observed if e["event"] == "final"]
        transcripts = [e["text"] for e in wire if e["type"] == "user_transcript"]
        report.update(app_transcripts=finals, gateway_transcripts=transcripts)
        types = [e["type"] for e in upstream]
        responses = [e for e in outbound if e["type"] == "speak"]
        report["checks"].update(actual_asr=transcripts == finals and len(finals) == 1 and normalized(finals[0]) == normalized(QUESTION),
            handoff=types.count("session.delegation.created") == 1,
            order=types[:2] == ["session.input_transcript.delta", "session.delegation.created"] and
                  len(responses) == 2 and responses[0]["text"] == "Let me think." and "simulated" in responses[1]["text"].lower(),
            tts_completion=len(intervals) == 2 and all(e.get("outcome") == "completed" for e in intervals))
    except Exception as error:
        report["failure"] = str(error) if isinstance(error, AssertionError) else type(error).__name__
    finally:
        if ui:
            await asyncio.to_thread(tap, ui["STOP"])
        finish.set()
        if capture_future:
            captured = await capture_future
        try:
            await asyncio.to_thread(configure, "", "")
            for _ in range(30):
                prefs = await asyncio.to_thread(adb, "shell", "run-as", PACKAGE, "cat", "shared_prefs/frontend.xml")
                settings = {node.get("name"): node.text or "" for node in ET.fromstring(prefs)}
                if settings.get("endpoint") == settings.get("token") == "":
                    break
                await asyncio.sleep(.1)
            assert settings.get("endpoint") == settings.get("token") == "", "Settings not blank"
            report["cleanup"] = "stopped, blank endpoint and token, history retained"
        except Exception:
            report["cleanup"] = "FAILED"
        await asyncio.to_thread(adb, "shell", "am", "force-stop", PACKAGE)
        observed = (await asyncio.to_thread(events))[offset:] if offset is not None else []
        report["app_transcripts"] = [e["detail"] for e in observed if e["event"] == "final"]
        report["gateway_transcripts"] = [e["text"] for e in wire if e["type"] == "user_transcript"]
        report["asr_ready_observed"] = any(e["event"] == "listening" for e in observed)
        report["recognition_watchdogs"] = [e for e in observed if e["event"] == "recognition_watchdog"]
        report["checks"]["no_watchdog_or_fatal"] = not any(e["event"] in {"recognition_watchdog", "fatal"} for e in observed)
        forwarded = [e["delta"].strip() for e in upstream if e["type"] == "session.input_transcript.delta"]
        report["upstream_input_transcripts"] = forwarded
        report["checks"]["original_transcript_forwarded"] = bool(forwarded) and forwarded == report["gateway_transcripts"] == report["app_transcripts"]
        requests = [e for e in observed if e["event"] == "tts_request"]
        native_ids = [e["detail"].split("id=", 1)[-1].split()[0] for e in requests]
        report["native_utterance_ids"] = native_ids
        report["checks"]["native_tts_callbacks"] = len(native_ids) == 2 and all(
            sum(e["event"] == "tts_done" and e["detail"] == uid for e in observed) == 1
            for uid in native_ids)
        delegations = [e for e in upstream if e["type"] == "session.delegation.created"]
        report["checks"]["nothing_spoken_before_handoff"] = bool(delegations) and bool(outbound) and all(
            e["host_monotonic"] > delegations[0]["host_monotonic"] for e in outbound if e["type"] == "speak")
        if captured:
            report["capture"] = {k: v for k, v in captured.items() if k != "packets"}
            raw, _, _, _ = bridge.read_wav(out / "android-raw.wav")
            selected = b""
            for packet in captured["packets"]:
                end = captured["origin_monotonic"] + packet["start"]
                if any(e.get("end") and "seventeen" in e["text"] and end - packet["duration"] >= e["start"] + .35
                       and end <= e["end"] - .15 for e in intervals):
                    begin = packet["byte_offset"]
                    selected += raw[begin:begin + round(packet["duration"] * 192000)]
            stats = bridge.signal_stats(selected)
            stats["seconds"] = len(selected) / 192000
            report["reply_signal"] = stats
            report["checks"]["reply_signal"] = stats["seconds"] >= 1 and stats["rms"] > .001
            with wave.open(str(out / "reply-interior.wav"), "wb") as wav:
                wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                wav.writeframes(selected)
            try:
                await asyncio.to_thread(review, out, source, captured, injected, intervals)
            except Exception as error:
                report["review_failure"] = type(error).__name__
        for task in tasks:
            task.cancel()
        if core:
            await core.close()
        if channel:
            channel.close()
        await http.close()
        await runner.cleanup()
        report["usage"] = usage.summary()
        report["provider_calls"] = usage.requests
        report["acoustic_content_verification"] = "Capture signal and native completion verified; independent ASR of captured reply not performed"
        for name, rows in [("app-events", observed), ("wire-events", wire), ("upstream-events", upstream),
                           ("outbound-events", outbound), ("gateway-events", log.rows)]:
            (out / (name + ".jsonl")).write_text("".join(json.dumps(e) + "\n" for e in rows))
        report["passed"] = all(report["checks"].values()) and not report.get("failure") and report.get("cleanup") != "FAILED"
        write_json(out / "result.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / ".tools/uptime-fixtures/manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".tools/uptime-local/attempt-001")
    parser.add_argument("--instructions-file", type=Path, default=ROOT / ".tools/canonical-frontend-instructions.txt")
    parser.add_argument("--live-model", action="store_true")
    parser.add_argument("--model", default=os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()
    if args.live_model and not os.environ.get("OPENROUTER_API_KEY"):
        parser.error("--live-model requires OPENROUTER_API_KEY in environment")
    raise SystemExit(asyncio.run(main(args)))
