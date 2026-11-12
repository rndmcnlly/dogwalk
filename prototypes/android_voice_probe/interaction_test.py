# /// script
# requires-python = ">=3.11"
# dependencies = ["grpcio==1.76.0", "grpcio-tools==1.76.0", "protobuf==6.33.0", "numpy>=2.0"]
# ///
"""Review real WAV -> Android ASR -> Android TTS interactions, without host audio.

uv run --script interaction_test.py .tools/voice-fixtures/manifest.json
Independent, bounded app restarts are an edited montage, NOT an uninterrupted
conversation. No duplex control is changed. Intro is review-only, never injected.
"""
import argparse
import hashlib
import html
import json
import re
import subprocess
import threading
import time
import wave
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

import audio_bridge as bridge
from audio_bridge_smoke import PACKAGE, adb, events

ROOT = Path(__file__).resolve().parent
RATE = 48000
STATUS = "The local audio probe is running. There is no model or remote backend connected."
LONG = ("This is a deliberately long local speech test. "
        "If you are wearing headphones and enabled listening during replies, you can interrupt me now. "
        "Otherwise I will finish speaking before listening again. "
        "The app requests on-device recognition and an offline speech voice from the system engines. "
        "No coding session is connected. Lock the screen, walk around, and check whether you can still hear me. "
        "This reply continues for a few more seconds so you have time to test interruption and audio routing.")
TEXT = {"status": "Probe status.", "echo": "Please remember the blue umbrella.",
        "long_reply": "Long reply.", "stop": "Stop listening."}
BUTTONS = {}


def tap(label):
    """Cache the static Activity controls; avoid a heavyweight UI dump per tap."""
    if not BUTTONS:
        for attempt in range(2):
            try:
                adb("shell", "uiautomator", "dump", "/sdcard/interaction-ui.xml")
                tree = ET.fromstring(adb("shell", "cat", "/sdcard/interaction-ui.xml"))
                for node in tree.iter("node"):
                    if node.get("text", "").lower() in {"stop", "start local audio"}:
                        x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.attrib["bounds"]))
                        BUTTONS[node.attrib["text"].lower()] = ((x1 + x2) // 2, (y1 + y2) // 2)
                if len(BUTTONS) != 2:
                    raise RuntimeError("Could not locate static probe controls")
                break
            except subprocess.CalledProcessError:
                if attempt:
                    raise
                time.sleep(.3)
    adb("shell", "input", "tap", *BUTTONS[label.lower()])


def normalized(text):
    return " ".join(re.sub(r"[^a-z ]", "", text.lower()).split())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def load_audio(path):
    pcm, channels, width, rate = bridge.read_wav(path)
    data = (np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768 if width == 2
            else (np.frombuffer(pcm, dtype=np.uint8).astype(np.float32) - 128) / 128)
    data = data.reshape(-1, channels).mean(axis=1)
    # Review copy only: explicit linear resampling. Injection uses original PCM.
    count = round(len(data) * RATE / rate)
    return np.interp(np.arange(count) * rate / RATE, np.arange(len(data)), data).astype(np.float32)


def place(target, source, start, gains=(1, 1)):
    """Add mono/stereo source at a nonnegative sample offset, no time stretching."""
    if start < 0 or start + len(source) > len(target):
        raise ValueError("Source lies outside timeline")
    if source.ndim == 1:
        source = source[:, None]
    target[start:start + len(source)] += source * np.asarray(gains)


def save_audio(path, data):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((2, 2, RATE, 0, "NONE", "not compressed"))
        wav.writeframes((np.clip(data, -1, 1) * 32767).astype("<i2").tobytes())


def packet_layout(packets):
    """Preserve every PCM frame, removing only sub-25ms packet delivery jitter.

    This emulator timestamps packet completion in practice (timestamp differences
    follow the current packet duration). Anchor burst starts at timestamp-duration,
    then keep samples contiguous. Preserve larger idle gaps without time compression.
    """
    result, end = [], 0
    for packet in packets:
        proposed = max(0, round((packet["start"] - packet["duration"]) * RATE))
        start = end if result and abs(proposed - end) < .025 * RATE else max(end, proposed)
        size = round(packet["duration"] * RATE)
        result.append((start, size))
        end = start + size
    return result


def capture(stub, pb, metadata, directory, ready, finish):
    """Keep original PCM and packet timing. Timestamp is SDK Unix epoch microseconds."""
    import grpc
    fmt = pb.AudioFormat(samplingRate=RATE, channels=pb.AudioFormat.Stereo,
                         format=pb.AudioFormat.AUD_FMT_S16)
    stream = stub.streamAudio(fmt, metadata=metadata, timeout=85)
    packets, chunks = [], []
    origin_wall, origin_mono = time.time(), time.monotonic()
    ready.set()
    def cancel_when_done():
        finish.wait(85)
        stream.cancel()
    timer = threading.Thread(target=cancel_when_done, daemon=True)
    timer.start()
    offset = 0
    try:
        for packet in stream:
            if packet.format != fmt or len(packet.audio) % 4:
                raise ValueError("Unexpected audio format or partial frame")
            duration = len(packet.audio) / (RATE * 4)
            received = time.monotonic() - origin_mono
            stamp = packet.timestamp / 1e6 - origin_wall
            valid = -0.25 <= stamp <= received + 1
            start = max(0, stamp if valid else received)
            packets.append({"start": start, "duration": duration, "byte_offset": offset,
                            "timestamp_us": packet.timestamp, "received": received,
                            "timing_source": "SDK capture completion timestamp" if valid else "receipt completion estimate"})
            chunks.append(packet.audio)
            offset += len(packet.audio)
    except grpc.RpcError as error:
        if error.code() not in (grpc.StatusCode.CANCELLED, grpc.StatusCode.DEADLINE_EXCEEDED):
            raise RuntimeError("Capture RPC failed: " + error.code().name) from None
    finally:
        stream.cancel()
        finish.set()
    pcm = b"".join(chunks)
    with wave.open(str(directory / "android-raw.wav"), "wb") as wav:
        wav.setparams((2, 2, RATE, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    json_write(directory / "packets.json", packets)
    return {"origin_monotonic": origin_mono, "origin_wall": origin_wall,
            "packets": packets, "stats": bridge.signal_stats(pcm)}


def wait_events(offset, predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = events()[offset:]
        fatal = next((e for e in rows if e["event"] == "fatal"), None)
        if fatal:
            raise RuntimeError("Probe fatal: " + fatal["detail"])
        if predicate(rows):
            return rows
        time.sleep(0.12)
    raise TimeoutError("Timed out waiting for probe event")


def voice_stop_completed(rows):
    """A UI Stop from the preceding turn is not evidence of spoken cleanup."""
    voice = next((i for i, e in enumerate(rows) if e["event"] == "voice_stop"), None)
    return voice is not None and any(e["event"] == "stop" for e in rows[voice + 1:])


def fresh_microphone(offset):
    """Ready callback alone is insufficient: inspect the currently active input."""
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        rows = events()[offset:]
        relevant = [r for r in rows if r["event"] in
                    {"listening", "speech_end", "final", "recognition_error", "stop", "fatal"}]
        if relevant and relevant[-1]["event"] == "listening":
            guest_uptime = float(adb("shell", "cat", "/proc/uptime").split()[0])
            age = guest_uptime - relevant[-1]["elapsed_ms"] / 1000
            if age > 2:
                time.sleep(.1)
                continue  # Wait for the next readiness window, not its stale tail.
            dump = adb("shell", "dumpsys", "media.audio_flinger")
            if bridge.microphone_active(dump):
                return {"checked_monotonic": time.monotonic(), "listening_event": relevant[-1],
                        "active_audio_flinger": True, "ready_event_age_seconds": age}
        time.sleep(.1)
    raise TimeoutError("No fresh ready event plus active guest microphone; injection refused")


def run_turn(name, fixture, directory, stub, pb, metadata, locked=False):
    directory.mkdir(parents=True, exist_ok=True)
    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    adb("shell", "wm", "dismiss-keyguard")
    adb("shell", "am", "start", "-n", PACKAGE + "/.MainActivity")
    tap("Stop")
    offset = len(events())
    ready, finish = threading.Event(), threading.Event()
    report = {"id": name, "fixture_id": fixture["id"], "app_restart": True,
              "uninterrupted_conversation": False, "locked": locked, "checks": {}}
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(capture, stub, pb, metadata, directory, ready, finish)
        try:
            if not ready.wait(5):
                raise TimeoutError("Capture not ready")
            tap("Start local audio")
            wait_events(offset, lambda rows: any(e["event"] == "listening" for e in rows), 35)
            if locked:
                adb("shell", "input", "keyevent", "KEYCODE_SLEEP")
                deadline = time.monotonic() + 4
                while True:
                    policy = adb("shell", "dumpsys", "window", "policy")
                    power = adb("shell", "dumpsys", "power")
                    locked_ready = bool(re.search(r"^\s+showing=true$", policy, re.MULTILINE) and
                        re.search(r"^\s+mWakefulness=Asleep$", power, re.MULTILINE) and
                        "screenState=SCREEN_STATE_OFF" in policy)
                    if locked_ready or time.monotonic() >= deadline:
                        break
                    time.sleep(.1)
                (directory / "policy.txt").write_text(policy)
                (directory / "power.txt").write_text(power)
                report["checks"]["locked_asleep_display_off"] = locked_ready
                if not report["checks"]["locked_asleep_display_off"]:
                    raise RuntimeError("Lock state not proven; injection refused")
            report["readiness"] = fresh_microphone(offset)
            report["injection_monotonic"] = time.monotonic()
            report["injected"] = bridge.inject(stub, pb, metadata, Path(fixture["wav"]), 15)
            is_stop = fixture["id"] == "stop"
            terminal = "stop" if is_stop else "tts_done"
            rows = wait_events(offset, voice_stop_completed if is_stop else
                               lambda rows: any(e["event"] == terminal for e in rows), 65)
            time.sleep(.5)
            rows = events()[offset:]
            finals = [e["detail"] for e in rows if e["event"] == "final"]
            requests = [e["detail"].split(" text=", 1)[-1] for e in rows if e["event"] == "tts_request"]
            expected = {"status": STATUS, "echo": "I heard: " + (finals[0] if finals else ""),
                        "long_reply": LONG, "stop": None}[fixture["id"]]
            report.update(observed_transcripts=finals, observed_reply_requests=requests,
                          expected_input=fixture["text"], expected_reply=expected)
            report["checks"].update(
                recognized_intended_words=any(normalized(s) == normalized(fixture["text"]) for s in finals),
                response_requested=(requests == [] if is_stop else requests == [expected]),
                tts_completion_or_stop=any(e["event"] == terminal for e in rows),
                stop_cleanup=(any(e["event"] == "voice_stop" for e in rows) if is_stop else True))
            if is_stop:
                report["checks"]["microphone_released"] = not bridge.microphone_active(
                    adb("shell", "dumpsys", "media.audio_flinger"))
        except Exception as error:  # noqa: BLE001 - preserve failure evidence and finish capture
            report["error"] = f"{type(error).__name__}: {error}"
        finally:
            finish.set()
            captured = future.result(timeout=10)
            rows = events()[offset:]
            (directory / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in rows))
    report["capture"] = captured
    report["android_voice"] = [e["detail"] for e in rows if e["event"] == "offline_voice"]
    report["events"] = rows
    if fixture["id"] != "stop":
        report["checks"]["nonzero_captured_signal"] = captured["stats"]["nonzero_samples"] > 0
    report["passed"] = not report.get("error") and all(report["checks"].values())
    json_write(directory / "result.json", report)
    return report


def build_review(output, manifest, fixtures, reports):
    segments, cursor = [], 0
    intro = fixtures.get("intro")
    if intro:
        audio = load_audio(intro["wav"])
        segments.append((cursor, audio, "intro", (.8, .6)))
        cursor += len(audio) + RATE
    timeline = {"format": "edited montage; live packet timing within each independently restarted turn",
                "review_rate": RATE, "roles": {"simulated_human": {"engine": manifest["engine"],
                "model": manifest["model"], "voice": manifest["voice"]},
                "android": {"voice_ids": sorted({v for r in reports for v in r["android_voice"]})}},
                "intro_review_only_never_injected": bool(intro), "turns": [],
                "independent_speech_analysis": {"status": "pending", "word_timing": "pending",
                    "integration": "Attach Parakeet analysis against android-only.wav or per-turn android-timed.wav; times must name their source track. Preserve these source-provenance roles. No speaker identity inferred from ASR.",
                    "audio_events": "Separate chime/event candidates from ASR words. No recognized words does not establish silence."},
                "beep_origin": "unknown: no earcon-specific evidence collected; captured Android output, not added by harness",
                "mix": "Breeze gain L=.8 R=.6; original Android stereo gain L=.6 R=.8; no pitch shift"}
    if intro:
        timeline["intro"] = {"role": "simulated_human", "review_only": True, "start": 0,
                             "end": len(load_audio(intro["wav"])) / RATE, "text": intro["text"],
                             "sha256": intro["sha256"], "voice_id": manifest["voice"]["name"]}
    for report in reports:
        cap = report["capture"]
        packets = cap["packets"]
        fixture = fixtures[report["fixture_id"]]
        actor = load_audio(fixture["wav"])
        injected = report.get("injection_monotonic")
        actor_start = max(0, round((injected - cap["origin_monotonic"]) * RATE)) if injected else None
        layout = packet_layout(packets)
        length = max([start + size for start, size in layout] +
                     [actor_start + len(actor) if actor_start is not None else 0, RATE])
        android = np.zeros((length, 2), dtype=np.float32)
        raw_path = output / report["id"] / "android-raw.wav"
        with wave.open(str(raw_path), "rb") as wav:
            pcm = wav.readframes(wav.getnframes())
        overlap = 0
        previous_end = 0
        for packet, (start, size) in zip(packets, layout):
            chunk = np.frombuffer(pcm[packet["byte_offset"]:packet["byte_offset"] + size * 4],
                                  dtype="<i2").astype(np.float32).reshape(-1, 2) / 32768
            overlap += max(0, previous_end - start)
            place(android, chunk, start)
            previous_end = start + len(chunk)
        save_audio(output / report["id"] / "android-timed.wav", android)
        segments.append((cursor, android, "android", (.6, .8)))
        # The guest capture must not already contain the actor fixture. Use a
        # normalized correlation over the known injection interval, not an ear test.
        duplication = {"method": "normalized cross-correlation during input, +/- 250ms", "max_correlation": 0.0}
        if actor_start is not None:
            mono = android.mean(axis=1)
            stride = 12
            x = actor[::stride].astype(np.float64)
            for shift in range(-12000, 12001, 240):
                start = actor_start + shift
                if start < 0 or start + len(actor) > len(mono):
                    continue
                y = mono[start:start + len(actor):stride].astype(np.float64)
                denominator = np.linalg.norm(x) * np.linalg.norm(y)
                correlation = abs(float(np.dot(x, y) / denominator)) if denominator else 0
                duplication["max_correlation"] = max(duplication["max_correlation"], correlation)
            duplication["suspected_duplicate"] = duplication["max_correlation"] > .65
            if duplication["suspected_duplicate"]:
                # Fail closed: keep original capture, do not double the actor.
                report["passed"] = False
                report["review_warning"] = "Possible fixture replay in system audio; actor overlay omitted"
            else:
                segments.append((cursor + actor_start, actor, "simulated_human", (.8, .6)))
        def event_time(event, segment_start=cursor / RATE, origin=cap["origin_wall"]):
            return segment_start + event["wall_ms"] / 1000 - origin
        speeches = []
        if actor_start is not None:
            speeches.append({"role": "simulated_human", "text": fixture["text"],
                             "start": (cursor + actor_start) / RATE,
                             "end": (cursor + actor_start + len(actor)) / RATE,
                             "sha256": fixture["sha256"], "voice_id": manifest["voice"]["name"]})
        starts = [e for e in report["events"] if e["event"] == "tts_start"]
        ends = [e for e in report["events"] if e["event"] == "tts_done"]
        for request in [e for e in report["events"] if e["event"] == "tts_request"]:
            speeches.append({"role": "android", "text": request["detail"].split(" text=", 1)[-1],
                             "start": event_time(starts[0] if starts else request),
                             "end": event_time(ends[0]) if ends else None,
                             "voice_id": report["android_voice"], "text_evidence": "TTS request, not output ASR",
                             "interval_timing_evidence": "Android TTS lifecycle callbacks, not acoustic word boundaries"})
        if report["fixture_id"] != "stop":
            # Earcons alone cannot satisfy output-signal evidence: inspect only
            # the callback-bounded TTS interval, allowing 150ms transport jitter.
            if starts and ends:
                begin = max(0, round((starts[0]["wall_ms"] / 1000 - cap["origin_wall"] - .15) * RATE))
                end = min(len(android), round((ends[0]["wall_ms"] / 1000 - cap["origin_wall"] + .15) * RATE))
                nonzero = int(np.count_nonzero(android[begin:end]))
            else:
                nonzero = 0
            report["checks"]["signal_during_tts_interval"] = nonzero > 0
            report["tts_interval_nonzero_samples"] = nonzero
            report["passed"] = report["passed"] and nonzero > 0
        timeline["turns"].append({"id": report["id"], "start": cursor / RATE,
                                  "end": (cursor + length) / RATE, "app_restart": True,
                                  "speeches": speeches, "duplication_check": duplication,
                                  "packet_overlap_samples": overlap,
                                  "timing_note": "Packet completion timestamps minus duration anchor bursts; contiguous PCM within bursts removes sub-25ms delivery jitter, larger idle gaps retained. Receipt-time fallback recorded per packet. Injection RPC start is estimated input onset.",
                                  "android_raw_sha256": digest(raw_path),
                                  "android_timed_sha256": digest(output / report["id"] / "android-timed.wav"),
                                  "expected": report.get("expected_input"),
                                  "observed": report.get("observed_transcripts", [])})
        cursor += length + RATE
        json_write(output / report["id"] / "result.json", report)
    mixed, android_only = np.zeros((cursor, 2), np.float32), np.zeros((cursor, 2), np.float32)
    for start, data, role, gains in segments:
        place(mixed, data, start, gains)
        if role == "android":
            place(android_only, data, start)
    peak = float(np.max(np.abs(mixed)))
    gain = min(1, .98 / peak) if peak else 1
    save_audio(output / "review.wav", mixed * gain)
    save_audio(output / "android-only.wav", android_only)
    timeline.update(review_gain=gain, review_sha256=digest(output / "review.wav"),
                    android_only_sha256=digest(output / "android-only.wav"))
    json_write(output / "timeline.json", timeline)
    lines = ["<!doctype html><meta charset='utf-8'><title>Android interaction review</title>",
             "<style>body{font:18px system-ui;max-width:850px;margin:2em auto;padding:1em;background:#161b22;color:#eee}audio{width:100%}li{margin:1em 0}a{color:#9cd}</style>",
             "<h1>Android interaction review</h1><p>Edited montage, independently restarted turns. Live timing within each turn. One-second edited gaps. Intro is never injected.</p>",
             f"<p><b>Breeze, {html.escape(manifest['voice']['name'])}: simulated human, slightly left.</b> Android: actual captured system voice, slightly right. No pitch shifts or dubbed replies. Beep origin unknown. Independent word timing pending.</p>",
             "<audio controls src='review.wav'></audio><p>Original Android-only timeline:</p><audio controls src='android-only.wav'></audio><ul>"]
    for turn in timeline["turns"]:
        lines.append(f"<li><b>{html.escape(turn['id'])}</b> ({turn['start']:.1f}–{turn['end']:.1f}s)<ul>")
        for speech in turn["speeches"]:
            lines.append(f"<li>{speech['start']:.1f}s <b>{html.escape(speech['role'])}</b>: {html.escape(speech['text'])}</li>")
        lines.append("</ul></li>")
    lines.append("</ul><a href='timeline.json'>Timing and provenance</a> · <a href='result.json'>Assertions</a>")
    (output / "review.html").write_text("\n".join(lines))


def attach_analysis(output, directory):
    """Attach independent lexical evidence without guessing word-level speakers."""
    words_path, raw_path = directory / "review.words.json", directory / "review.json"
    words = json.loads(words_path.read_text())
    if words["audio"]["sha256"] != digest(output / "review.wav"):
        raise ValueError("ASR audio hash does not match this review")
    inputs = json.loads((directory / "inputs.json").read_text())
    timeline_path = output / "timeline.json"
    timeline = json.loads(timeline_path.read_text())
    for turn in timeline.get("turns", []):
        for speech in turn.get("speeches", []):
            if speech["role"] == "android":
                speech["interval_timing_evidence"] = "Android TTS lifecycle callbacks, not acoustic word boundaries"
    timeline["independent_speech_analysis"] = {
        "status": "complete", "model": inputs["model"], "package": inputs["package"],
        "reference_text_supplied": inputs["reference_text_supplied"],
        "source_track": "review.wav", "source_sha256": words["audio"]["sha256"],
        "raw_json": str(raw_path.resolve()), "raw_sha256": digest(raw_path),
        "lexical_json": str(words_path.resolve()), "lexical_sha256": digest(words_path),
        "word_timing": "independent ASR estimates in review.wav seconds, no inferred speaker identity",
        "calibration": "Repeated speech boundaries varied up to 180ms, not a guaranteed error bound. Chimes produced no text or event labels; absent words are not silence.",
        "words": words["words"], "audio_events": "No chime detector applied; origins unknown"}
    android_words_path = directory / "android-only.words.json"
    android_transcript = None
    if android_words_path.exists():
        android_words = json.loads(android_words_path.read_text())
        if android_words["audio"]["sha256"] != digest(output / "android-only.wav"):
            raise ValueError("Android-only ASR audio hash mismatch")
        android_raw_path = directory / "android-only.json"
        android_transcript = json.loads(android_raw_path.read_text())["text"]
        timeline["independent_speech_analysis"]["android_only"] = {
            "source_track": "android-only.wav", "source_sha256": android_words["audio"]["sha256"],
            "lexical_json": str(android_words_path.resolve()), "lexical_sha256": digest(android_words_path),
            "raw_json": str(android_raw_path.resolve()), "raw_sha256": digest(android_raw_path),
            "words": android_words["words"], "text": android_transcript,
            "role_provenance": "Captured system-output track, not ASR speaker identification"}
    timeline["independent_speech_analysis"]["coverage_caution"] = (
        "ASR transcripts can omit audible stretches. Callback-requested text is an expectation, "
        "not an acoustic transcript. Compare mixed and Android-only analyses with the audio.")
    json_write(timeline_path, timeline)
    page_path = output / "review.html"
    page = page_path.read_text().split("<!-- independent-asr -->", 1)[0]
    transcript = " ".join(w["word"] for w in words["words"])
    page += ("\n<!-- independent-asr --><h2>Independent Parakeet transcript</h2>"
             "<p>Script-free ASR, no speaker identification. Word times are approximate; "
             "no words does not mean silence or absent chimes.</p><p>" + html.escape(transcript) +
             "</p><p><a href='asr/review.words.json'>Lexical timing</a></p>")
    if android_transcript is not None:
        page += ("<h3>Android-only independent transcript</h3><p>" + html.escape(android_transcript) +
                 "</p><p>These two ASR passes can omit different audible stretches. "
                 "Their omissions are not proof of missing audio.</p>")
    page_path.write_text(page)
    print(json.dumps({"analysis_attached": True, "words": len(words["words"])}))


def attach_chimes(output, match_path):
    """Attach exposed-template candidates, never silently label speech or all beeps."""
    design_path = ROOT / ".tools/chime-matching-take-002/design.json"
    design = json.loads(design_path.read_text())
    matched = json.loads(match_path.read_text())
    if matched["threshold"] != design["threshold"]:
        raise ValueError("Chime threshold differs from frozen calibration")
    source = output / "android-only.wav"
    analysis_input = output / "chime-input-16k.wav"
    extraction = output / "chime-extraction-16k.json"
    extraction_info = json.loads(extraction.read_text())
    if Path(extraction_info["inputs"][0]["path"]) != analysis_input.resolve():
        raise ValueError("Chime extraction names a different input")
    timeline_path = output / "timeline.json"
    timeline = json.loads(timeline_path.read_text())
    candidates = []
    for hit in matched["hits"]:
        turn = next((t for t in timeline["turns"] if t["start"] <= hit["onset_s"] < t["end"]), None)
        candidates.append({**hit, "kind": "known Android-capture earcon template candidate",
                           "source_track": "android-only.wav", "source_sha256": digest(source),
                           "review_onset_s": hit["onset_s"], "turn_id": turn["id"] if turn else None,
                           "turn_relative_onset_s": hit["onset_s"] - turn["start"] if turn else None,
                           "generating_component": "unknown; template similarity does not prove recognition-service origin"})
    timeline["audio_event_candidates"] = candidates
    timeline["chime_analysis"] = {
        "status": "complete", "tool_version": extraction_info["tool_version"],
        "source_track": "android-only.wav", "source_sha256": digest(source),
        "analysis_input_sha256": digest(analysis_input),
        "transformation": "48kHz stereo averaged to mono, scipy resample_poly 1:3, PCM16; zero timeline offset",
        "template": design["template"], "calibration_design_sha256": digest(design_path),
        "matches_sha256": digest(match_path), "extraction_sha256": digest(extraction),
        "settings": design["settings"], "threshold": matched["threshold"],
        "limitations": "Known exposed-template detector only. Calibration missed 6/6 speech-overlapped chimes. No match does not establish no chime; scores are not probabilities."}
    timeline["beep_origin"] = "Known-template candidates are recorded separately; exact Android component remains unknown. No harness-added beeps."
    if "independent_speech_analysis" in timeline:
        timeline["independent_speech_analysis"]["audio_events"] = "Separate audio_event_candidates, never inferred from ASR words or omissions"
    json_write(timeline_path, timeline)
    page_path = output / "review.html"
    page = page_path.read_text().split("<!-- chime-candidates -->", 1)[0]
    page += ("\n<!-- chime-candidates --><h2>Known-template audio-event candidates</h2>"
             "<p>Exposed Android-capture earcon template matches, not general chime detection "
             "or proof of the generating service. Speech-overlapped events can be missed.</p><ul>")
    for hit in candidates:
        page += f"<li>{hit['review_onset_s']:.3f}s: score {hit['score']:.3f}, {html.escape(hit['turn_id'] or 'gap')}</li>"
    page_path.write_text(page + "</ul>")
    print(json.dumps({"chime_candidates_attached": len(candidates)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, nargs="?")
    parser.add_argument("--output", type=Path, default=ROOT / ".tools/interaction-review")
    parser.add_argument("--attach-analysis", type=Path, help="Attach existing review ASR without operating emulator")
    parser.add_argument("--attach-chimes", type=Path, help="Attach calibrated known-template match JSON without operating emulator")
    parser.add_argument("--skip-locked", action="store_true")
    parser.add_argument("--include-stop", action="store_true", help="Attempt stop only with fresh active mic proof")
    args = parser.parse_args()
    if args.attach_analysis:
        attach_analysis(args.output.resolve(), args.attach_analysis.resolve())
        return
    if args.attach_chimes:
        attach_chimes(args.output.resolve(), args.attach_chimes.resolve())
        return
    if args.manifest is None:
        parser.error("manifest is required unless --attach-analysis is supplied")
    if not args.manifest.exists():
        print(json.dumps({"status": "ready-for-fixtures", "manifest": str(args.manifest)}))
        return
    manifest = json.loads(args.manifest.read_text())
    if manifest["schema_version"] != 1:
        raise ValueError("Unsupported manifest version")
    fixtures = {f["id"]: f for f in manifest["fixtures"]}
    for name, text in TEXT.items():
        f = fixtures[name]
        if f.get("review_only") or normalized(f["text"]) != normalized(text):
            raise ValueError("Invalid runtime fixture: " + name)
    if "intro" in fixtures and not fixtures["intro"].get("review_only"):
        raise ValueError("Intro must be review-only")
    for fixture in fixtures.values():
        if not Path(fixture["wav"]).is_absolute() or digest(fixture["wav"]) != fixture["sha256"]:
            raise ValueError("Fixture path/hash mismatch: " + fixture["id"])
        bridge.read_wav(fixture["wav"])
    output = args.output.resolve()
    if (output / "result.json").exists():
        raise ValueError("Output already contains a run; choose a new --output to preserve evidence")
    output.mkdir(parents=True, exist_ok=True)
    json_write(output / "fixture-manifest.json", manifest)
    original = adb("shell", "locksettings", "get-disabled").strip()
    if original not in {"true", "false"}:
        raise RuntimeError("Cannot establish original keyguard setting")
    report = {"schema_version": 1, "interruption": "unsupported on emulator: no actual headset; duplex guard unchanged",
              "conversation": "independent restarted turns, not uninterrupted", "turns": [],
              "stop": "attempted" if args.include_stop else "skipped: avoid unsafe post-microphone-close emulator race"}
    pb, pb_grpc = bridge.protocol()
    channel, stub, metadata = bridge.connection(pb_grpc)
    try:
        from google.protobuf.empty_pb2 import Empty
        state = stub.getMicrophoneState(Empty(), metadata=metadata, timeout=5)
        if state.realAudioEnabled:
            raise RuntimeError("Host microphone is enabled; refusing non-isolated interaction test")
        report["host_microphone_enabled"] = False
        adb("shell", "locksettings", "set-disabled", "false")
        plan = [(name, name, False) for name in ("status", "echo", "long_reply")]
        if args.include_stop:
            plan.append(("stop", "stop", False))
        if not args.skip_locked:
            plan.append(("locked-status", "status", True))
        for name, fixture_id, locked in plan:
            row = run_turn(name, fixtures[fixture_id], output / name, stub, pb, metadata, locked)
            report["turns"].append(row)
            print(json.dumps({"turn": name, "passed": row["passed"], "error": row.get("error")}))
            if row.get("error"):
                break  # bounded failure, no blind retries against a crashed emulator
        build_review(output, manifest, fixtures, report["turns"])
    except Exception as error:
        report["harness_error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        channel.close()
        try:
            adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
            adb("shell", "wm", "dismiss-keyguard")
            tap("Stop")
        finally:
            adb("shell", "locksettings", "set-disabled", original)
            restored = adb("shell", "locksettings", "get-disabled").strip()
            report["keyguard"] = {"original_disabled": original, "restored_disabled": restored}
            report["ui_button_coordinates"] = BUTTONS
            report["passed"] = (not report.get("harness_error") and bool(report["turns"])
                                and all(t["passed"] for t in report["turns"]) and restored == original
                                and (output / "review.wav").exists())
            json_write(output / "result.json", report)
    print(json.dumps({"passed": report["passed"], "review": str(output / "review.html")}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
