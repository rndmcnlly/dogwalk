# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.10,<4", "grpcio==1.76.0", "grpcio-tools==1.76.0", "protobuf==6.33.0"]
# ///
"""Native Android network/playback fixture, not ASR or Orchestrator integration.

Run with emulator-5580 already running: uv run --script frontend_app_smoke.py
Add --gateway-core for the real gateway state machine with free fixture inference.
Add --live-model for one bounded inference call. Default OpenRouter uses
OPENROUTER_API_KEY; --provider omlx --model ID_FROM_MODELS uses OMLX_API_KEY.
Optional --instructions-file supplies canonical instructions without logging them.
Installs the current debug APK. Uses explicit UI Start/Stop; no microphone input.
Only new artifacts under .tools/native-network-smoke are written. Credentials
are ephemeral, never printed or included in saved diagnostics. Settings are
reset to blank and the application stopped even on failure.
"""
import argparse
import asyncio
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
from urllib.parse import urlencode

from aiohttp import ClientSession, WSMsgType, web

import audio_bridge as bridge
from frontend_server import (
    FIXTURE_PROMPT,
    FixtureModel,
    Frontend,
    SafeLog,
    Usage,
    add_provider_args,
    configure_provider,
    configured_model,
)

ROOT = Path(__file__).resolve().parent
OUT = ROOT / ".tools/native-network-smoke"
PACKAGE = "as.adamsmith.dogwalk.audioprobe"
SPEECH_ID = "native-fixture-speech-1"
TEXT = "This is a local network playback fixture. The native Android voice is speaking."
COMMENTARY = "This is a local network test. No real session was contacted."


class OneDecision:
    """Enforce one decision, even if the actual state machine wakes again."""
    def __init__(self, model, usage, live):
        self.model, self.usage, self.live = model, usage, live
        self.calls = 0

    async def decide(self, messages):
        self.calls += 1
        if self.calls != 1:
            raise RuntimeError("Smoke permits only one model decision, no retries")
        started = time.monotonic()
        result = await self.model.decide(messages)
        if not self.live:
            self.usage.record("fixture/no-provider", time.monotonic() - started, provider="fixture")
        return result


def adb(*args):
    # In particular, never expose am's echoed private configuration URI.
    result = subprocess.run(
        [str(ROOT / ".tools/sdk/platform-tools/adb"), "-s", "emulator-5580", *map(str, args)],
        check=False,
        env={**os.environ, "HOME": str(ROOT / ".tools/emulator-home")},
        capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise RuntimeError("ADB operation failed (output suppressed for credential safety)")
    return result.stdout


def events():
    raw = adb("shell", "run-as", PACKAGE, "cat", "no_backup/audio-probe.jsonl")
    return [json.loads(line) for line in raw.replace("\x00", "").splitlines() if line.strip()]


def controls():
    # This emulator does not forward /dev/tty XML. Never retain the temporary
    # credential-bearing UI dump in host artifacts or on the guest.
    temporary = "/data/local/tmp/native-network-smoke-ui.xml"
    try:
        adb("shell", "uiautomator", "dump", temporary)
        tree = ET.fromstring(adb("shell", "cat", temporary))
    finally:
        adb("shell", "rm", "-f", temporary)
    found = {}
    for node in tree.iter("node"):
        key = node.get("content-desc") or node.get("text", "")
        if key.upper() in {"START LOCAL AUDIO", "STOP", "GATEWAY ENDPOINT", "GATEWAY BEARER TOKEN", "SAVE GATEWAY SETTINGS"}:
            x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.attrib["bounds"]))
            found[key.upper()] = ((x1 + x2) // 2, (y1 + y2) // 2)
    return found


def tap(point):
    adb("shell", "input", "tap", *point)


def configure(endpoint, token):
    uri = "dogwalk-audio://connect?" + urlencode({"endpoint": endpoint, "token": token})
    # Android's shell must not split the query at '&'.
    import shlex
    adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", shlex.quote(uri), "-p", PACKAGE)


class SpeakerCapture:
    """Capture packet receipt times so evidence excludes recognizer beeps.

    Conservative interior window: 350ms after speech_started until 150ms before
    speech_finished. Packets straddling its bounds are excluded completely.
    """
    def __init__(self):
        self.packets = []
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.error = None

    def run(self):
        channel = None
        try:
            import grpc
            pb, pb_grpc = bridge.protocol()
            channel, stub, metadata = bridge.connection(pb_grpc)
            fmt = pb.AudioFormat(samplingRate=48000, channels=pb.AudioFormat.Stereo,
                                 format=pb.AudioFormat.AUD_FMT_S16)
            stream = stub.streamAudio(fmt, metadata=metadata, timeout=18)
            self.ready.set()
            try:
                for packet in stream:
                    assert packet.format == fmt
                    self.packets.append((time.monotonic(), bytes(packet.audio)))
                    if self.stop.is_set():
                        break
            except grpc.RpcError as error:
                if error.code() != grpc.StatusCode.DEADLINE_EXCEEDED:
                    raise
            finally:
                stream.cancel()
        except Exception as error:  # noqa: BLE001  # Preserve capture failure without leaking transport metadata.
            self.error = type(error).__name__
            self.ready.set()
        finally:
            if channel is not None:
                channel.close()

    def save(self, start, end):
        if self.error:
            raise RuntimeError("Speaker capture failed: " + self.error)
        whole = b"".join(pcm for _, pcm in self.packets)
        interior = b"".join(pcm for at, pcm in self.packets
                            if at - len(pcm) / 192000 >= start + .35 and at <= end - .15)
        for name, pcm in [("speaker.wav", whole), ("tts-interior.wav", interior)]:
            with wave.open(str(OUT / name), "wb") as wav:
                wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                wav.writeframes(pcm)
        stats = bridge.signal_stats(interior)
        stats["seconds"] = len(interior) / 192000
        assert stats["seconds"] >= 1, "Insufficient interior TTS audio"
        assert stats["rms"] > .001 and stats["nonzero_samples"] > 4800, "No sustained TTS-interval signal"
        return stats


async def main(args):
    OUT.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    received = []
    connected, finished, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    peer = None
    start_at = end_at = None
    authenticated = False
    connections = 0
    core = None
    http = None
    log = SafeLog()
    usage = Usage(log)
    decision_model = None
    upstream_events = []
    outgoing = asyncio.Queue()
    expected = {"speech_id": SPEECH_ID, "text": TEXT}
    report = {"mode": "fixture", "scope": "native network playback only",
              "asr": "unverified: existing on-device recognizer stalls; no ASR debugging performed",
              "orchestrator": "not connected", "passed": False}
    report["gateway_core"] = args.gateway_core
    report["model"] = args.model if args.live_model else "fixture/no-provider"
    report["live_model"] = args.live_model
    report["provider"] = args.provider if args.live_model else "fixture"
    capture = SpeakerCapture()
    capture_thread = None
    offset = None
    ui = None

    class FixtureUpstream:
        async def send(self, event):
            # This is the actual Frontend output boundary, not a manufactured
            # transcript. No input transcript or delegation is injected.
            assert event["type"] == "session.output_transcript.delta", "Unexpected upstream event"
            assert finished.is_set(), "Gateway reported speech before native completion"
            assert core.speeches[expected["speech_id"]].state == "completed"
            upstream_events.append({"host_monotonic": time.monotonic(), **event})

    async def emit(event):
        await outgoing.put(event)

    async def websocket(request):
        nonlocal peer, authenticated, connections, start_at, end_at
        if request.headers.get("Authorization") != "Bearer " + token:
            return web.Response(status=401)
        authenticated = True
        connections += 1
        peer = web.WebSocketResponse()
        await peer.prepare(request)
        await peer.send_json({"type": "ready", "protocol": 1, "mode": "fixture", "session_id": "native-smoke"})
        connected.set()
        try:
            async for message in peer:
                if message.type == WSMsgType.TEXT:
                    event = json.loads(message.data)
                    received.append({"host_monotonic": time.monotonic(), **event})
                    if event.get("type") == "speech_started":
                        start_at = time.monotonic()
                        assert not upstream_events, "Output transcript arrived before playback"
                    if event.get("type") == "speech_finished":
                        end_at = time.monotonic()
                        finished.set()
                    if core is not None:
                        await core.android(event)
        finally:
            closed.set()
        return peer

    app = web.Application()
    app.router.add_get("/ws", websocket)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        if args.gateway_core:
            prompt = args.instructions_file.read_text() if args.instructions_file else FIXTURE_PROMPT
            report["instructions_source"] = "file" if args.instructions_file else "fixture"
            report["instructions_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
            prompt += ("\nLOCAL PLAYBACK SMOKE: Phrase the pending commentary in one brief sentence, "
                       "at most 18 words. Preserve that this is a local test and no real session "
                       "was contacted. Return action=reply. Do not add a greeting or other facts.")
            if args.live_model:
                http = ClientSession()
                model = configured_model(http, args, usage)
            else:
                model = FixtureModel()
            decision_model = OneDecision(model, usage, args.live_model)
            core = Frontend(emit, FixtureUpstream(), decision_model, prompt, log, mode="fixture")
            # Generate before explicit Start. There is no synthetic user turn:
            # this is an unsolicited response lane (turn_id remains null).
            await core.worker({"type": "session.commentary.append", "content": COMMENTARY})
            speech = await asyncio.wait_for(outgoing.get(), 38)
            assert speech["type"] == "speak", "Gateway failed to phrase fixture commentary"
            assert speech["turn_id"] is None
            assert len(speech["text"]) <= 200, "Model reply exceeds bounded playback smoke"
            expected = speech
            assert not upstream_events, "Gateway reported queued speech as heard"
        apk = ROOT / "app/build/outputs/apk/debug/app-debug.apk"
        report["apk_sha256"] = hashlib.sha256(apk.read_bytes()).hexdigest()
        await asyncio.to_thread(adb, "install", "-r", apk)
        await asyncio.to_thread(adb, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
        await asyncio.to_thread(adb, "shell", "wm", "dismiss-keyguard")
        await asyncio.to_thread(adb, "shell", "am", "force-stop", PACKAGE)
        await asyncio.to_thread(configure, f"ws://10.0.2.2:{port}/ws", token)
        ui = await asyncio.to_thread(controls)
        assert {"START LOCAL AUDIO", "STOP", "GATEWAY ENDPOINT", "GATEWAY BEARER TOKEN", "SAVE GATEWAY SETTINGS"} <= ui.keys()
        offset = len(await asyncio.to_thread(events))
        await asyncio.sleep(.4)
        assert not connected.is_set(), "Configuration link started network without explicit Start"
        capture_thread = threading.Thread(target=capture.run, daemon=True)
        capture_thread.start()
        assert await asyncio.to_thread(capture.ready.wait, 7), "Speaker capture did not initialize"
        assert not capture.error, "Speaker capture unavailable"
        await asyncio.to_thread(tap, ui["START LOCAL AUDIO"])
        await asyncio.wait_for(connected.wait(), 10)
        deadline = time.monotonic() + 8
        while True:
            observed = (await asyncio.to_thread(events))[offset:]
            assert not any(e["event"] == "fatal" for e in observed), "App failed during startup"
            if any(e["event"] == "offline_voice" for e in observed):
                break
            assert time.monotonic() < deadline, "Offline TTS not ready"
            await asyncio.sleep(.05)
        assert not any(e["event"] == "listening" for e in observed), "Fixture missed initial pre-recognition window"
        await peer.send_json({"type": "speak", "speech_id": expected["speech_id"], "turn_id": None, "text": expected["text"]})
        await asyncio.wait_for(finished.wait(), 12)
        await asyncio.to_thread(tap, ui["STOP"])
        await asyncio.wait_for(closed.wait(), 4)
        assert peer.close_code == 1000, "Socket did not close normally after Stop"
        capture.stop.set()
        await asyncio.to_thread(capture_thread.join, 19)
        assert not capture_thread.is_alive(), "Capture failed to terminate"
        await asyncio.sleep(1)
        observed = (await asyncio.to_thread(events))[offset:]
        assert not any(e["event"] in {"fatal", "recognition_watchdog"} for e in observed), "App/watchdog failed during playback test"
        starts = [e for e in received if e["type"] == "speech_started"]
        ends = [e for e in received if e["type"] == "speech_finished"]
        assert len(starts) == len(ends) == 1, "Expected exactly one correlated start/completion"
        assert start_at < end_at
        assert starts[0]["speech_id"] == ends[0]["speech_id"] == expected["speech_id"]
        assert ends[0]["outcome"] == "completed"
        assert any(e["type"] == "session_stop" for e in received)
        assert connections == 1 and not any(e["type"] == "user_transcript" for e in received)
        requests = [e for e in observed if e["event"] == "tts_request" and expected["text"] in e["detail"]]
        assert len(requests) == 1
        local_id = re.search(r"id=(\S+)", requests[0]["detail"])[1]
        assert len([e for e in observed if e["event"] == "tts_start" and "id=" + local_id + " " in e["detail"]]) == 1
        assert len([e for e in observed if e["event"] == "tts_done" and e["detail"] == local_id]) == 1
        stopped = next(i for i, e in enumerate(observed) if e["event"] == "stop")
        assert not any(e["event"] in {"tts_request", "tts_start", "listening"} for e in observed[stopped + 1:]), "Late playback/listening after Stop"
        stats = capture.save(start_at, end_at)
        report.update(passed=True, authenticated_bearer=authenticated, configuration_requires_start=True,
                      speech_id=expected["speech_id"], local_utterance_id=local_id,
                      exactly_one_completed_callback=True, socket_closed_after_stop=True,
                      socket_close_code=peer.close_code,
                      no_late_replay=True, audio=stats, delayed_speak_after_stop="not attempted: socket already closed")
        if core is not None:
            assert len(upstream_events) == 1, "Expected one actual gateway output transcript"
            assert upstream_events[0]["delta"] == expected["text"] + "\n"
            assert upstream_events[0]["host_monotonic"] >= end_at
            assert core.speeches[expected["speech_id"]].state == "completed"
            assert not core.pending and all(item["role"] != "user" for item in core.history)
            assert decision_model.calls == usage.requests == 1
            assert outgoing.empty(), "Unexpected additional gateway response"
            assert not any(row["event"] == "frontend.error" for row in log.rows)
            report.update(upstream_output_only_after_completion=True,
                          upstream_output_matches_spoken_text=True,
                          gateway_completed_and_pending_cleared=True,
                          model_decisions=decision_model.calls,
                          provider_calls=1 if args.live_model else 0)
    except Exception as error:  # noqa: BLE001  # Fixture/ADB errors may contain capability links.
        report["passed"] = False
        # Exceptions from fixture/ADB can contain capabilities; report only our
        # assertion messages or the exception class, never third-party text.
        report["failure"] = str(error) if isinstance(error, AssertionError) else type(error).__name__
    finally:
        try:
            if ui:
                await asyncio.to_thread(tap, ui["STOP"])
            await asyncio.to_thread(configure, "", "")
            # am start returns before onNewIntent and SharedPreferences.apply.
            deadline = time.monotonic() + 5
            while True:
                prefs = await asyncio.to_thread(adb, "shell", "run-as", PACKAGE, "cat", "shared_prefs/frontend.xml")
                settings = {node.get("name"): node.text or "" for node in ET.fromstring(prefs)}
                if settings.get("endpoint") == settings.get("token") == "":
                    break
                assert time.monotonic() < deadline, "Settings did not reset"
                await asyncio.sleep(.1)
            await asyncio.to_thread(adb, "shell", "am", "force-stop", PACKAGE)
            report["cleanup"] = "app force-stopped; endpoint and token blank; history retained"
        except Exception:  # noqa: BLE001  # Report cleanup failure without exposing private configuration.
            report["cleanup"] = "FAILED"
            report["passed"] = False
        finally:
            # Even a UI or settings-reset failure must not leave audio running.
            await asyncio.to_thread(adb, "shell", "am", "force-stop", PACKAGE)
        if offset is not None:
            observed = (await asyncio.to_thread(events))[offset:]
            (OUT / "app-events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in observed))
        (OUT / "wire-events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in received))
        capture.stop.set()
        if core is not None:
            await core.close()
            report["usage"] = usage.summary()
            (OUT / "gateway-events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in log.rows))
            (OUT / "upstream-events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in upstream_events))
        log.close()
        if http is not None:
            await http.close()
        await runner.cleanup()
        (OUT / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-core", action="store_true", help="Use actual Frontend state machine with explicit fixture upstream")
    parser.add_argument("--live-model", action="store_true", help="One inference call, max_tokens=220; environment credentials only")
    add_provider_args(parser, model_env="OPENROUTER_MODEL")
    parser.add_argument("--instructions-file", type=Path, help="Canonical instructions, kept out of artifacts")
    args = parser.parse_args()
    if (args.live_model or args.instructions_file) and not args.gateway_core:
        parser.error("--live-model and --instructions-file require --gateway-core")
    configure_provider(parser, args, required=args.live_model)
    asyncio.run(main(args))
