# /// script
# requires-python = ">=3.11"
# dependencies = ["flask", "flask-sock", "waitress", "twilio", "python-dotenv", "httpx"]
# ///
"""Direct inbound Media Streams: no Twilio-hosted recordings or media URLs."""
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import sys
import threading
import time
import wave
from xml.etree import ElementTree as ET

from flask import Flask, Response, abort, request
from flask_sock import Sock
from twilio.request_validator import RequestValidator

import collect as core
from flow import phrases

ROOT = core.ROOT
PORT = 8770
MAX_BYTES = 8000 * 14


def mulaw_table():
    table = []
    for encoded in range(256):
        value = (~encoded) & 255
        sample = ((value & 15) << 3) + 132
        sample <<= (value & 112) >> 4
        sample -= 132
        table.append(struct.pack("<h", -sample if value & 128 else sample))
    return table


ULAW = mulaw_table()


def beep():
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", int(7000 * math.sin(2 * math.pi * 880 * i / 8000))) for i in range(1200)))
    return out.getvalue()


def twiml(labels, origin, synthetic=False):
    root = ET.Element("Response")
    ET.SubElement(root, "Say", voice="man").text = (
        "Synthetic audio transport check." if synthetic else
        "This call collects six samples of your voice for an experiment. "
        "Hang up if you do not want to be recorded. "
        "After each beep, repeat the sentence, then wait for the next sentence. "
        "These sentences are not passwords."
    )
    if not synthetic:
        ET.SubElement(root, "Pause", length="2")
    for i, label in enumerate(labels):
        ET.SubElement(root, "Say", voice="man").text = f"Sentence {i + 1}. {label}"
        start = ET.SubElement(root, "Start")
        stream = ET.SubElement(start, "Stream", url=origin.replace("https://", "wss://") + "/media",
                               name=f"round-{i}", track="inbound_track",
                               statusCallback=origin + f"/stream-status/{i}", statusCallbackMethod="POST")
        ET.SubElement(stream, "Parameter", name="round", value=str(i))
        ET.SubElement(root, "Play").text = origin + "/beep.wav"
        ET.SubElement(root, "Pause", length="3" if synthetic else "10")
        stop = ET.SubElement(root, "Stop")
        ET.SubElement(stop, "Stream", name=f"round-{i}")
    ET.SubElement(root, "Say", voice="man").text = "Collection complete. Goodbye."
    ET.SubElement(root, "Hangup")
    return ET.tostring(root, encoding="unicode")


def valid_signature(path, websocket=False):
    origin = core.runtime()["origin"]
    signature = request.headers.get("X-Twilio-Signature", "")
    validator = RequestValidator(core.TOKEN)
    urls = [origin + path]
    if websocket:
        # Twilio websocket upgrades and HTTP proxy URL schemes differ.
        urls.append(origin.replace("https://", "wss://") + path)
    return any(validator.validate(url, {} if websocket else request.form, signature) for url in urls)


def make_app():
    app = Flask(__name__, static_folder=None)
    app.config.update(MAX_CONTENT_LENGTH=64 * 1024, SOCK_SERVER_OPTIONS={"ping_interval": 20})
    sock = Sock(app)

    @app.get("/health")
    def health():
        return "ok"

    @app.get("/beep.wav")
    def tone():
        return Response(beep(), content_type="audio/wav", headers={"Cache-Control": "public, max-age=3600"})

    @app.before_request
    def validate():
        if request.path in ("/health", "/beep.wav"):
            return
        if not valid_signature(request.full_path.removesuffix("?"), request.path == "/media"):
            abort(403)
        if request.path != "/media" and request.form.get("AccountSid") != core.ACCOUNT:
            abort(403)

    @app.post("/failure")
    def failure():
        return Response(core.GOODBYE, content_type="text/xml")

    @app.post("/voice")
    def voice():
        call = request.form.get("CallSid", "")
        if not re.fullmatch(r"CA[0-9a-fA-F]{32}", call):
            abort(400)
        with core.LOCK:
            cfg = core.runtime()
            path = ROOT / "calls" / f"{call}.json"
            if path.exists():
                state = core.read(path)
            else:
                synthetic = cfg["mode"] == "stream-test" and request.form.get("From") == cfg["number"] and not cfg.get("stream_test_call")
                if not synthetic and not (cfg["mode"] == "stream-armed" and time.time() < cfg["expires"] and cfg.get("calls", 0) < 3):
                    return Response(core.GOODBYE, content_type="text/xml")
                state = {"call_sid": call, "synthetic": synthetic, "speaker": "unconfirmed",
                         "phrases": ["The quiet bird waits."] if synthetic else phrases(),
                         "recordings": {}, "streams": {}, "created": time.time(),
                         "delete_local_after": time.time() + 7 * 86400}
                if synthetic:
                    cfg["stream_test_call"] = call
                else:
                    cfg["calls"] = cfg.get("calls", 0) + 1
                core.save(ROOT / "runtime.json", cfg)
                core.persist_call(call, state)
        return Response(twiml(state["phrases"], cfg["origin"], state["synthetic"]), content_type="text/xml")

    @app.post("/stream-status/<int:i>")
    def stream_status(i):
        call = request.form.get("CallSid", "")
        if not re.fullmatch(r"CA[0-9a-fA-F]{32}", call) or not 0 <= i < 6:
            abort(400)
        with core.LOCK:
            path = ROOT / "calls" / f"{call}.json"
            if not path.exists():
                abort(404)
            state = core.read(path)
            state.setdefault("stream_events", []).append({"round": i, "event": request.form.get("StreamEvent"),
                "error": request.form.get("StreamError"), "at": time.time()})
            core.save(path, state)
        return "", 204

    @sock.route("/media")
    def media(ws):
        call = sid = None
        raw = bytearray()
        completed = False
        round_index = None
        last_timestamp = 0
        gaps_ms = 0
        try:
            while True:
                message = ws.receive(timeout=20)
                if message is None:
                    break
                if len(message) > 128 * 1024:
                    raise ValueError("Oversized frame")
                event = json.loads(message)
                if event.get("event") == "start":
                    start = event["start"]
                    call, sid = start["callSid"], start["streamSid"]
                    if start.get("accountSid") != core.ACCOUNT or not re.fullmatch(r"CA[0-9a-fA-F]{32}", call) or not re.fullmatch(r"MZ[0-9a-fA-F]{32}", sid):
                        raise ValueError("Invalid stream identity")
                    round_index = int(start.get("customParameters", {})["round"])
                    state = core.read(ROOT / "calls" / f"{call}.json")
                    if not 0 <= round_index < len(state["phrases"]):
                        raise ValueError("Unknown round")
                    fmt = start.get("mediaFormat", {})
                    if fmt.get("encoding") != "audio/x-mulaw" or int(fmt.get("sampleRate", 0)) != 8000 or int(fmt.get("channels", 0)) != 1:
                        raise ValueError("Unexpected encoding")
                elif event.get("event") == "media":
                    if not call or not sid:
                        raise ValueError("Media before start")
                    if event.get("streamSid") != sid or event["media"].get("track", "inbound") != "inbound":
                        raise ValueError("Wrong stream track")
                    payload = base64.b64decode(event["media"]["payload"], validate=True)
                    timestamp = int(event["media"].get("timestamp", last_timestamp))
                    expected_timestamp = len(raw) // 8
                    if timestamp > expected_timestamp:
                        missing = min(timestamp - expected_timestamp, 2000)
                        raw.extend(b"\xff" * (missing * 8))
                        gaps_ms += missing
                    raw.extend(payload)
                    last_timestamp = timestamp
                    if len(raw) > MAX_BYTES:
                        raise ValueError("Audio exceeds round limit")
                elif event.get("event") == "stop":
                    completed = True
                    break
        except Exception as e:
            core.save(ROOT / "last_stream_error.json", {"kind": type(e).__name__, "at": time.time()})
        finally:
            if call and sid and round_index is not None and raw:
                pcm = b"".join(ULAW[v] for v in raw[:MAX_BYTES])
                output = io.BytesIO()
                with wave.open(output, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(8000)
                    w.writeframes(pcm)
                data = output.getvalue()
                dest = ROOT / "audio" / f"{sid}.wav"
                with dest.open("xb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                with core.LOCK:
                    path = ROOT / "calls" / f"{call}.json"
                    state = core.read(path)
                    state["streams"][sid] = {"round": round_index, "phrase": state["phrases"][round_index],
                        "file": dest.name, "duration": len(pcm) / 16000, "channels": 1,
                        "sample_rate": 8000, "sample_width": 2, "sha256": hashlib.sha256(data).hexdigest(),
                        "complete": completed, "inserted_silence_ms": gaps_ms, "saved": time.time()}
                    core.save(path, state)
                    if state["synthetic"]:
                        core.save(ROOT / "stream_test.json", {"signed_transport_verified": True,
                            "saved_wav": bool(len(data) > 44), "completed": completed, "at": time.time(),
                            "duration": len(pcm) / 16000})

    return app


def watchdog():
    while True:
        try:
            cfg = core.runtime()
            if time.time() > cfg["expires"] and (ROOT / "active.json").exists():
                core.restore()
                cfg["mode"] = "closed"
                core.save(ROOT / "runtime.json", cfg)
                print("Collection expired; normal incoming routing restored.", flush=True)
        except Exception as e:
            core.save(ROOT / "watchdog_error.json", {"kind": type(e).__name__, "at": time.time()})
        time.sleep(10)


def start():
    for name in ("calls", "audio"):
        (ROOT / name).mkdir(exist_ok=True, mode=0o700)
    threading.Thread(target=watchdog, daemon=True).start()
    # Werkzeug's websocket-compatible threaded server, isolated attended experiment only.
    make_app().run(host="127.0.0.1", port=PORT, threaded=True, debug=False, use_reloader=False)


def arm():
    test = core.read(ROOT / "stream_test.json")
    assert test["signed_transport_verified"] and test["saved_wav"] and test["completed"]
    assert time.time() - test["at"] < 900
    cfg = core.runtime()
    cfg["mode"] = "stream-armed"
    cfg["calls"] = 0
    cfg["expires"] = time.time() + 4 * 3600
    core.save(ROOT / "runtime.json", cfg)
    print("READY: " + cfg["number"])
    print("Six phrases, direct private inbound audio, normal routing automatically restored after four hours.")


def status():
    cfg = core.runtime()
    states = [core.read(p) for p in (ROOT / "calls").glob("*.json")]
    print(json.dumps({"mode": cfg["mode"], "minutes_remaining": int((cfg["expires"]-time.time())/60),
        "stream_test": core.read(ROOT / "stream_test.json") if (ROOT / "stream_test.json").exists() else None,
        "stream_calls": [{"synthetic": s["synthetic"], "clips": len(s["streams"]),
                          "complete": sum(v["complete"] for v in s["streams"].values())} for s in states if "streams" in s],
        "stream_error": (ROOT / "last_stream_error.json").exists()}))


def cleanup():
    count = 0
    for p in (ROOT / "calls").glob("*.json"):
        state = core.read(p)
        if "streams" not in state or time.time() < state["delete_local_after"]:
            continue
        for sid in state["streams"]:
            if re.fullmatch(r"MZ[0-9a-fA-F]{32}", sid):
                (ROOT / "audio" / f"{sid}.wav").unlink(missing_ok=True)
        p.unlink()
        count += 1
    print(f"Deleted {count} expired stream sample sets.")


if __name__ == "__main__":
    try:
        {"serve": start, "arm": arm, "status": status, "cleanup": cleanup}[sys.argv[1]]()
    except Exception as e:
        print(f"Operation failed: {type(e).__name__}; private details suppressed", file=sys.stderr)
        sys.exit(1)
