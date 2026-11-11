# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.11,<4", "grpcio==1.76.0", "grpcio-tools==1.76.0", "protobuf==6.33.0", "numpy>=2.0"]
# ///
"""One original Breeze question through the real operator-only Voice Frontend.

Preparation is offline: --help and --check-config never connect. Running requires
--go-live, an operator JSON config containing orchestrator_url (or --endpoint-key),
and OPENROUTER_API_KEY. All evidence stays beneath the approved private temp dir.
No deployment, wake, Diagnostics API, fabricated answer, synthetic user transcript,
or ACP cancellation. A captured answer is NOT proof of its uptime measurement:
playtest.py/Agent evidence must independently establish container versus host time.
"""
import argparse
import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
import time
import wave
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import numpy as np
from aiohttp import ClientSession, WSMsgType, web

import audio_bridge as bridge
from frontend_app_smoke import PACKAGE, adb, configure, controls, events, tap
from frontend_server import DEFAULT_MODEL, Frontend, OpenRouterModel, SafeLog, Usage
from interaction_test import load_audio, normalized, packet_layout, place, save_audio
from uptime_local_test import QUESTION, fixture_path

ROOT = Path(__file__).resolve().parent
PRIVATE = Path("/private/var/folders/qj/gyqbn5y57956rrqd_g6yfqwr0000gn/T/opencode/dogwalk_live_uptime")


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def config(args):
    assert args.operator_config.resolve().is_relative_to(PRIVATE), "Operator config must be in approved private temp directory"
    value = json.loads(args.operator_config.read_text())
    url = value[args.endpoint_key]
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.hostname, parsed.path) == ("wss", "dogwalk.tools", "/dev/voice-frontend"), "Unexpected operator endpoint"
    assert not parsed.username and not parsed.password and not parsed.fragment, "Invalid endpoint credentials/fragment"
    query = parse_qs(parsed.query, strict_parsing=True)
    assert set(query) == {"phone"} and len(query["phone"]) == 1 and query["phone"][0], "Operator endpoint requires one phone query"
    assert args.output.resolve().is_relative_to(PRIVATE), "Evidence must stay in approved private temp directory"
    assert 30 <= args.timeout <= 240, "Deadline must be 30–240 seconds"
    source, sha = fixture_path(args.manifest)
    return url, source, sha


def credential(args):
    key = os.environ.get("DOGWALK_ADMIN_PASSWORD")
    if not key:
        metadata = json.loads(args.operator_config.read_text())
        service = metadata.get("admin_keychain_service", "dogwalk-admin")
        assert service == "dogwalk-admin", "Unexpected admin credential service"
        command = ["security", "find-generic-password", "-s", service]
        if metadata.get("admin_keychain_account"):
            command += ["-a", metadata["admin_keychain_account"]]
        result = subprocess.run([*command, "-w"],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0 and result.stdout.strip(), "Admin credential unavailable"
        key = result.stdout.strip()
    return "Basic " + base64.b64encode(("adam:" + key).encode()).decode()


def answer_candidate(text):
    """Conservative finish gate, never a claim that measurements are verified."""
    if re.search(r"\b(?:will|checking|give me|let me|please wait|should take)\b", text, re.I):
        return False
    duration = re.search(r"\b(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                         r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|"
                         r"fifty|sixty|seventy|eighty|ninety|hundred)\s+(?:seconds?|minutes?|hours?|days?)\b", text, re.I)
    timestamp = re.search(r"\b(?:since|started at|started on)\b.{0,60}\d", text, re.I)
    assertion = re.search(r"\b(?:has been|is|it's|been|shows?|reports?|measured|reads?|running for|running since)\b", text, re.I)
    subject = re.search(r"\b(?:sandbox|container|kernel|uptime|running)\b", text, re.I)
    return bool(subject and assertion and (duration or timestamp))


def capture_audio(out, ready, finish, limit):
    """Cancellable, bounded guest-output capture with SDK completion timestamps."""
    import grpc
    pb, pb_grpc = bridge.protocol()
    channel, stub, metadata = bridge.connection(pb_grpc)
    fmt = pb.AudioFormat(samplingRate=48000, channels=pb.AudioFormat.Stereo, format=pb.AudioFormat.AUD_FMT_S16)
    stream = stub.streamAudio(fmt, metadata=metadata, timeout=limit)
    origin, wall = time.monotonic(), time.time()
    packets, chunks, offset = [], [], 0

    def cancel():
        finish.wait(limit)
        stream.cancel()

    threading.Thread(target=cancel, daemon=True).start()
    ready.set()
    try:
        for packet in stream:
            assert packet.format == fmt and len(packet.audio) % 4 == 0, "Invalid capture format"
            duration = len(packet.audio) / 192000
            received = time.monotonic() - origin
            stamp = packet.timestamp / 1e6 - wall
            valid = -.25 <= stamp <= received + 1
            packets.append({"start": max(0, stamp if valid else received), "duration": duration,
                            "byte_offset": offset, "timestamp_us": packet.timestamp,
                            "timing_source": "SDK capture completion" if valid else "receipt completion estimate"})
            chunks.append(bytes(packet.audio))
            offset += len(packet.audio)
    except grpc.RpcError as error:
        if error.code() not in (grpc.StatusCode.CANCELLED, grpc.StatusCode.DEADLINE_EXCEEDED):
            raise RuntimeError("Capture transport failed") from None
    finally:
        stream.cancel()
        channel.close()
    pcm = b"".join(chunks)
    with wave.open(str(out / "android-raw.wav"), "wb") as wav:
        wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    dump(out / "packets.json", packets)
    return {"origin_monotonic": origin, "origin_wall": wall, "packets": packets,
            "stats": bridge.signal_stats(pcm)}


def review(out, source, captured, injection, speeches):
    text = ("Live operator adapter review. This is an actual Worker and sandbox connection, not a simulated answer. "
            "Android supplies the opening and replies. Breeze supplies the user question. "
            "Uptime measurement provenance still requires independent diagnostic review. Begin live recording.")
    subprocess.run(["/usr/bin/say", "-o", str(out / "review-intro.aiff"), text],
                   check=True, capture_output=True, timeout=20)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out / "review-intro.aiff"),
                    "-ar", "48000", "-ac", "1", str(out / "review-intro.wav")],
                   check=True, capture_output=True, timeout=20)
    intro, user = load_audio(out / "review-intro.wav"), load_audio(source)
    with wave.open(str(out / "android-raw.wav"), "rb") as wav:
        raw = wav.readframes(wav.getnframes())
    stereo = np.frombuffer(raw, dtype="<i2").reshape(-1, 2).astype(np.float32) / 32768
    layout = packet_layout(captured["packets"])
    lead = len(intro) + 48000
    input_start = round((injection - captured["origin_monotonic"]) * 48000) if injection else None
    length = max(max((a + b for a, b in layout), default=0),
                 input_start + len(user) if input_start is not None else 0)
    timeline = np.zeros((lead + length + 48000, 2), dtype=np.float32)
    place(timeline, intro, 0)
    offset = 0
    for start, size in layout:
        place(timeline, stereo[offset:offset + size], lead + start)
        offset += size
    if input_start is not None:
        place(timeline, user, lead + input_start)
    save_audio(out / "review.wav", timeline)
    dump(out / "review-labels.json", {"mode": "live", "narrator": {"text": text, "role": "review-only narration"},
         "live_recording_start_seconds": lead / 48000,
         "user": {"role": "original Breeze input; review-only resampling", "text": QUESTION,
                  "start_seconds": (lead + input_start) / 48000 if input_start is not None else None},
         "android": [{**s, "role": "actual captured Android speech", "review_start_seconds":
             lead / 48000 + s["start"] - captured["origin_monotonic"]} for s in speeches],
         "provenance": "Actual Worker commentary, not a canned answer; Agent measurements require separate verification"})


async def run(args, url, source, sha):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"mode": "live", "passed": False, "transport_passed": False,
              "measurement_verification": "pending independent playtest/Agent evidence review",
              "uptime_caveat": "/proc/uptime may describe the host kernel, not container lifetime or time since wake",
              "fixture_pcm_sha256": sha, "question": QUESTION, "checks": {}, "pending_work": "unknown"}
    wire, upstream, worker, outbound, speeches = [], [], [], [], []
    log, usage = SafeLog(), None
    usage = Usage(log)
    core = socket = None
    reader = None
    ui = offset = injection = captured = None
    ready, finish = threading.Event(), threading.Event()
    capture_future = None
    connected, opening_done, answer_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    started, model_calls = time.monotonic(), 0
    token = secrets.token_urlsafe(32)
    opening = None
    opening_append_count = 0
    audio_lock = asyncio.Lock()
    primer_task = None
    primer_stop = asyncio.Event()
    primer_events = []
    silence = out / "microphone-primer.wav"
    with wave.open(str(silence), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(bytes(16000))  # 500ms of actual zero PCM, never a user turn.

    async def prime_idle_windows():
        attempts = {}
        pb, pb_grpc = bridge.protocol()
        channel, stub, auth = bridge.connection(pb_grpc)
        try:
            while not primer_stop.is_set():
                await asyncio.sleep(.25)
                async with audio_lock:
                    if any(s.state in ("queued", "started") for s in core.speeches.values()):
                        continue
                    rows = (await asyncio.to_thread(events))[offset:]
                    requests = [i for i, e in enumerate(rows) if e["event"] == "listen_request"]
                    if not requests:
                        continue
                    index = requests[-1]
                    generation = rows[index]["detail"]
                    # Each fresh recognizer needs initial PCM, not a continuous
                    # stream after ready. Never prime a terminal/closed window.
                    if any(e["event"] in {"listening", "final", "recognition_error", "stop", "fatal", "speech_end"}
                           for e in rows[index + 1:]) or attempts.get(generation, 0) >= 3:
                        continue
                    dump_text = await asyncio.to_thread(adb, "shell", "dumpsys", "media.audio_flinger")
                    if not bridge.microphone_active(dump_text):
                        continue
                    attempts[generation] = attempts.get(generation, 0) + 1
                    at = time.monotonic()
                    await asyncio.to_thread(bridge.inject, stub, pb, auth, silence, 3)
                    primer_events.append({"host_monotonic": at, "generation": generation,
                                          "active_microphone": True, "seconds": .5})
        finally:
            channel.close()

    async def read_worker():
        nonlocal opening_append_count
        async for message in socket:
            if message.type != WSMsgType.TEXT:
                continue
            event = json.loads(message.data)
            worker.append({"host_monotonic": time.monotonic(), **event})
            if event.get("type") == "session.commentary.append" and event.get("content") == opening:
                opening_append_count += 1
                assert opening_append_count == 1, "Duplicate opening request"
            await core.worker(event)

    class Model:
        def __init__(self, model):
            self.model = model

        async def decide(self, messages):
            nonlocal model_calls
            model_calls += 1
            assert model_calls <= args.max_model_calls, "Frontend inference budget exceeded"
            decision = await self.model.decide(messages)
            # The canonical opening must be spoken exactly once as supplied.
            if not opening_done.is_set():
                state = json.loads(messages[-1]["content"])
                if any(p["text"] == opening for p in state["speech_requests"]):
                    assert decision == {"action": "reply", "text": opening}, "Model did not preserve exact opening"
            return decision

    class Upstream:
        async def send(self, event):
            upstream.append({"host_monotonic": time.monotonic(), **event})
            if event["type"] == "session.input_transcript.delta":
                assert normalized(event["delta"]) == normalized(QUESTION), "Unexpected ASR turn, no downstream forwarding"
            await socket.send_json(event)

    async def websocket(request):
        if request.headers.get("Authorization") != "Bearer " + token:
            return web.Response(status=401)
        assert core is not None, "Upstream not ready"
        peer = web.WebSocketResponse()
        await peer.prepare(request)

        async def emit(event):
            speech = core.speeches.get(event.get("speech_id"))
            outbound.append({"host_monotonic": time.monotonic(), **event,
                             "worker_commentary_sources": [p["text"] for p in core.pending
                                 if speech and p["id"] in speech.requests]})
            async with audio_lock:
                await peer.send_json(event)

        core.emit = emit
        await peer.send_json({"type": "ready", "protocol": 1, "mode": "live", "session_id": report["call_id"],
                              "frontend_model": args.model})
        connected.set()
        # Start reading only once Android is ready. Buffered first commentary is
        # the one opening speech request, never ready.opening itself.
        nonlocal reader
        reader = asyncio.create_task(read_worker())
        async for message in peer:
            if message.type != WSMsgType.TEXT:
                continue
            event = json.loads(message.data)
            wire.append({"host_monotonic": time.monotonic(), **event})
            if event["type"] == "user_transcript" and injection is None:
                raise AssertionError("Unsolicited transcript before authorized audio injection")
            if event["type"] == "speech_started":
                s = core.speeches[event["speech_id"]]
                speeches.append({"speech_id": event["speech_id"], "text": s.text, "start": time.monotonic(),
                                 "request_ids": list(s.requests)})
            await core.android(event)
            if event["type"] == "speech_finished":
                for s in speeches:
                    if s["speech_id"] == event["speech_id"]:
                        s.update(end=time.monotonic(), outcome=event["outcome"])
                        if s["text"] == opening and s["outcome"] == "completed":
                            opening_done.set()
                        if injection and s["request_ids"] and s["text"] != opening and s["outcome"] == "completed":
                            # Candidate only: never equate acknowledgement/progress
                            # with a verified answer. Main must inspect Agent output.
                            if answer_candidate(s["text"]):
                                answer_done.set()
        return peer

    app = web.Application()
    app.router.add_get("/ws", websocket)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as http:
            # This is the first production operation. CLI requires explicit GO.
            socket = await asyncio.wait_for(http.ws_connect(url, headers={"Authorization": credential(args)},
                         heartbeat=25, max_msg_size=128000), 20)
            metadata = await asyncio.wait_for(socket.receive_json(), 20)
            assert metadata.get("type") == "dev.frontend.ready", "Missing actual Worker ready"
            assert isinstance(metadata.get("instructions"), str) and metadata.get("opening"), "Missing canonical metadata"
            dump(out / "worker-ready-private.json", metadata)
            report.update(call_id=metadata["call_id"], theme=metadata.get("theme"),
                          instructions_sha256=hashlib.sha256(metadata["instructions"].encode()).hexdigest())
            opening = metadata["opening"]
            async def unavailable(_):
                raise AssertionError("Android not ready for speech")
            core = Frontend(unavailable, Upstream(), Model(OpenRouterModel(http, os.environ["OPENROUTER_API_KEY"], args.model, usage)),
                            metadata["instructions"], log, mode="live")
            await asyncio.to_thread(adb, "shell", "am", "force-stop", PACKAGE)
            await asyncio.to_thread(adb, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
            await asyncio.to_thread(adb, "shell", "wm", "dismiss-keyguard")
            await asyncio.to_thread(configure, f"ws://10.0.2.2:{port}/ws", token)
            ui = await asyncio.to_thread(controls)
            offset = len(await asyncio.to_thread(events))
            capture_future = asyncio.create_task(asyncio.to_thread(capture_audio, out, ready, finish, args.timeout))
            assert await asyncio.to_thread(ready.wait, 5), "Capture did not initialize"
            await asyncio.to_thread(tap, ui["START LOCAL AUDIO"])
            await asyncio.wait_for(connected.wait(), 12)
            await asyncio.wait_for(opening_done.wait(), min(65, args.timeout - (time.monotonic() - started)))
            assert opening_append_count == 1, "Opening request count not one"
            # Do not wait for recognizer-ready. Actual active mic is the required
            # safety gate, and incoming PCM can unblock the cold recognizer.
            await asyncio.to_thread(bridge.wait_for_microphone, 5)
            report["active_microphone_before_injection"] = True
            report["ready_before_injection"] = any(e["event"] == "listening" for e in (await asyncio.to_thread(events))[offset:])
            pb, pb_grpc = bridge.protocol()
            channel, stub, auth = bridge.connection(pb_grpc)
            try:
                # Recheck immediately after channel initialization.
                await asyncio.to_thread(bridge.wait_for_microphone, 2)
                injection = time.monotonic()
                report["injection_monotonic"] = injection
                report["injected"] = await asyncio.to_thread(bridge.inject, stub, pb, auth, source, 15)
            finally:
                channel.close()
            primer_task = asyncio.create_task(prime_idle_windows())
            deadline = started + args.timeout
            while not answer_done.is_set():
                if reader.done():
                    await reader
                    raise AssertionError("Worker connection ended before answer")
                if primer_task.done():
                    await primer_task
                    raise AssertionError("Idle microphone primer stopped unexpectedly")
                assert time.monotonic() < deadline, "No completed uptime-answer candidate within deadline; downstream work may remain pending"
                await asyncio.sleep(.1)
            # Allow the immediate speech-completion transcript to reach the Worker.
            await asyncio.sleep(.3)
            report["answer_candidate_captured"] = True
    except Exception as error:
        report["failure"] = str(error) if isinstance(error, AssertionError) else type(error).__name__
    finally:
        if primer_task:
            primer_stop.set()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await primer_task
        try:
            if ui:
                await asyncio.to_thread(tap, ui["STOP"])
            await asyncio.to_thread(configure, "", "")
            for _ in range(30):
                raw = await asyncio.to_thread(adb, "shell", "run-as", PACKAGE, "cat", "shared_prefs/frontend.xml")
                settings = {n.get("name"): n.text or "" for n in ET.fromstring(raw)}
                if settings.get("endpoint") == settings.get("token") == "":
                    break
                await asyncio.sleep(.1)
            assert settings.get("endpoint") == settings.get("token") == "", "Settings not blank"
            report["cleanup"] = "app stopped, endpoint/token blank, history retained; no ACP cancellation sent"
        except Exception:
            report["cleanup"] = "FAILED"
        await asyncio.to_thread(adb, "shell", "am", "force-stop", PACKAGE)
        finish.set()
        if capture_future:
            try:
                captured = await capture_future
            except Exception as error:
                report["capture_failure"] = type(error).__name__
        if reader:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
        if core:
            await core.close()
        if socket:
            await socket.close()
        await runner.cleanup()
        rows = (await asyncio.to_thread(events))[offset:] if offset is not None else []
        finals = [e["detail"] for e in rows if e["event"] == "final"]
        inputs = [e["text"] for e in wire if e["type"] == "user_transcript"]
        forwarded = [e["delta"].strip() for e in upstream if e["type"] == "session.input_transcript.delta"]
        delegations = [e for e in upstream if e["type"] == "session.delegation.created"]
        completed = [s for s in speeches if s.get("outcome") == "completed"]
        answers = [s for s in completed if injection and s["start"] > injection and s["request_ids"] and s["text"] != opening]
        candidates = [s for s in answers if answer_candidate(s["text"])]
        report.update(app_transcripts=finals, gateway_transcripts=inputs, upstream_transcripts=forwarded,
                      speeches=speeches, frontend_calls=model_calls, usage=usage.summary(),
                      microphone_primers=primer_events,
                      pending_work="unknown until playtest/Agent diagnostics reviewed; nothing cancelled")
        report["checks"].update(actual_asr=len(finals) == 1 and normalized(finals[0]) == normalized(QUESTION),
            original_transcript_forwarded=bool(inputs) and finals == inputs == forwarded,
            handoff=len(delegations) == 1,
            opening_exactly_once=opening_append_count == 1 and sum(s["text"] == opening for s in completed) == 1,
            opening_before_input=bool(injection) and any(s["text"] == opening and s.get("end", float("inf")) < injection for s in completed),
            downstream_commentary_completed=bool(answers),
            substantive_uptime_candidate_completed=bool(candidates),
            no_watchdog_or_fatal=not any(e["event"] in {"fatal", "recognition_watchdog"} for e in rows),
            handoff_before_post_question_speech=bool(delegations) and bool(injection) and any(
                e["type"] == "speak" and e["host_monotonic"] > injection for e in outbound) and all(
                e["host_monotonic"] > delegations[0]["host_monotonic"] for e in outbound
                if e["type"] == "speak" and injection and e["host_monotonic"] > injection))
        native_requests = [e for e in rows if e["event"] == "tts_request"]
        report["checks"]["native_answer_completion"] = bool(candidates) and all(any(
            s["text"] in request["detail"] and any(e["event"] == "tts_done" and
                e["detail"] == request["detail"].split("id=", 1)[-1].split()[0] for e in rows)
            for request in native_requests) for s in candidates)
        if captured:
            with wave.open(str(out / "android-raw.wav"), "rb") as wav:
                raw = wav.readframes(wav.getnframes())
            pcm = b""
            for p in captured["packets"]:
                end = captured["origin_monotonic"] + p["start"]
                if any(end - p["duration"] >= s["start"] + .35 and end <= s["end"] - .15 for s in candidates):
                    pcm += raw[p["byte_offset"]:p["byte_offset"] + round(p["duration"] * 192000)]
            stats = bridge.signal_stats(pcm)
            stats["seconds"] = len(pcm) / 192000
            report["reply_signal"] = stats
            report["checks"]["reply_signal"] = stats["seconds"] >= 1 and stats["rms"] > .001
            try:
                await asyncio.to_thread(review, out, source, captured, injection, speeches)
            except Exception as error:
                report["review_failure"] = type(error).__name__
        for name, records in [("app-events", rows), ("android-events", wire), ("upstream-events", upstream),
                              ("worker-events", worker), ("outbound-events", outbound), ("frontend-events", log.rows)]:
            (out / (name + ".jsonl")).write_text("".join(json.dumps(e) + "\n" for e in records))
        report["transport_passed"] = bool(report.get("answer_candidate_captured")) and all(report["checks"].values()) and not report.get("failure") and report.get("cleanup") != "FAILED"
        report["status"] = "answer_captured_requires_measurement_review" if report["transport_passed"] else "failed_or_pending"
        dump(out / "result.json", report)
    # Only non-sensitive outcome/path goes to terminal. Real text and identifiers
    # remain in private artifacts, never ordinary stdout or repository files.
    print(json.dumps({"status": report["status"], "transport_passed": report["transport_passed"],
                      "measurement_verified": False, "artifacts": str(out)}))
    return 0 if report["transport_passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-config", type=Path, required=True)
    parser.add_argument("--endpoint-key", default="orchestrator_url")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / ".tools/uptime-fixtures/manifest.json")
    parser.add_argument("--model", default=os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL))
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--max-model-calls", type=int, default=12)
    parser.add_argument("--check-config", action="store_true", help="Offline validation only, no credentials fetched or connection")
    parser.add_argument("--go-live", action="store_true", help="Explicit main-session GO required: opens actual Worker adapter")
    parser.add_argument("--live-model", action="store_true", help="Accepted for clarity; this helper always uses real inference")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        url, source, sha = config(args)
        if args.check_config:
            print("Offline config and original Breeze WAV validated; no connection made")
            return 0
        assert args.go_live, "Main-session explicit GO required; use --check-config for offline preparation"
        assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY must be supplied in environment"
        assert 1 <= args.max_model_calls <= 12, "Frontend call budget must be 1–12"
        return asyncio.run(run(args, url, source, sha))
    except Exception as error:
        print(json.dumps({"status": "failed", "error_class": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
