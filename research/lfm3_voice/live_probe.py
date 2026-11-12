# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=15,<17"]
# ///
"""Bounded native realtime probes. Running this script DOES invoke the model.

No TTS fallback, special delegate token, or simulated private channel is used.
Sources (installed server revision):
https://github.com/ggml-org/llama.cpp/blob/cd959bae9/tools/server/server-realtime.cpp
https://github.com/ggml-org/llama.cpp/blob/cd959bae9/models/templates/LFM3.jinja
Liquid conversational prefix: Respond with interleaved text and audio.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import math
from pathlib import Path
import struct
import time
import wave

PREFIX = "Respond with function calls, and interleaved text and audio.\n\n"
TOOLS = [{"type": "function", "name": "delegate", "description":
          "Ask the host to handle the caller's task asynchronously. Takes no arguments.",
          "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}]


def wav_to_pcm16(path, target_rate=16000):
    """Read RIFF PCM/IEEE float WAV, downmix and linearly resample (no inference)."""
    raw = Path(path).read_bytes()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError("Expected a RIFF WAVE file")
    fmt, chunks = None, []
    offset = 12
    while offset + 8 <= len(raw):
        tag, size = struct.unpack_from("<4sI", raw, offset)
        start = offset + 8
        if start + size > len(raw):
            raise ValueError("Truncated WAV chunk")
        chunk = raw[start:start + size]
        if tag == b"fmt ":
            fmt = chunk
        elif tag == b"data":
            chunks.append(chunk)
        offset = start + size + size % 2
    if fmt is None or len(fmt) < 16 or not chunks:
        raise ValueError("Missing WAV format or data")
    kind, channels, rate, _, align, bits = struct.unpack_from("<HHIIHH", fmt)
    if kind == 0xFFFE and len(fmt) >= 40:
        kind = struct.unpack_from("<H", fmt, 24)[0]
    width = bits // 8
    if not channels or not rate or align != channels * width:
        raise ValueError("Invalid WAV frame layout")
    if (kind == 1 and bits not in (8, 16, 24, 32)) or (kind == 3 and bits != 32) or kind not in (1, 3):
        raise ValueError(f"Unsupported WAV encoding {kind}, {bits} bits")
    data = b"".join(chunks)
    if len(data) % align:
        raise ValueError("Partial WAV frame")
    samples = []
    for i in range(0, len(data), align):
        values = []
        for channel in range(channels):
            b = data[i + channel * width:i + (channel + 1) * width]
            value = (struct.unpack("<f", b)[0] if kind == 3 else
                     (b[0] - 128) / 128 if bits == 8 else
                     int.from_bytes(b, "little", signed=True) / (2 ** (bits - 1)))
            if not math.isfinite(value):
                raise ValueError("Non-finite WAV sample")
            values.append(value)
        samples.append(sum(values) / channels)
    if not samples:
        raise ValueError("Empty input WAV")
    out = bytearray()
    for i in range(round(len(samples) * target_rate / rate)):
        pos = min(i * rate / target_rate, len(samples) - 1)
        lo = int(pos)
        value = samples[lo] + (samples[min(lo + 1, len(samples) - 1)] - samples[lo]) * (pos - lo)
        out.extend(struct.pack("<h", max(-32768, min(32767, round(value * 32768)))))
    return bytes(out), {"source_rate": rate, "source_channels": channels,
                        "source_bits": bits, "source_encoding": kind,
                        "rate": target_rate, "samples": len(out) // 2}


def save_wav(path, pcm, rate=24000):
    if len(pcm) % 2:
        raise ValueError("Partial PCM16 sample")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm)


def event_metadata(event):
    """Preserve event structure/text but replace audio payloads with byte metadata."""
    def visit(value, key="", parent_type=""):
        if isinstance(value, dict):
            return {k: visit(v, k, value.get("type", parent_type)) for k, v in value.items()}
        if isinstance(value, list):
            return [visit(v, key, parent_type) for v in value]
        if isinstance(value, str) and (key == "audio" or (key == "delta" and "audio" in parent_type and "transcript" not in parent_type)):
            data = base64.b64decode(value, validate=True)
            return {"base64_omitted": True, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        return value
    return visit(event)


class ProbeTimeout(Exception):
    pass


class Rejected(Exception):
    pass


class Recorder:
    def __init__(self, ws, directory, timeout):
        self.ws, self.directory, self.timeout = ws, directory, timeout
        self.started = time.monotonic()
        self.log = (directory / "events.jsonl").open("w")
        self.events, self.audio = [], {}
        self.phase = "connect"
        self.sequence = 0
        self.responding = False

    def record(self, direction, event):
        row = {"sequence": self.sequence, "elapsed_s": time.monotonic() - self.started,
               "wall_time_ns": time.time_ns(), "phase": self.phase,
               "direction": direction, "event": event_metadata(event)}
        self.sequence += 1
        self.log.write(json.dumps(row) + "\n")
        self.log.flush()
        if direction == "receive":
            self.events.append(row)
            if event.get("type") == "response.created":
                self.responding = True
            elif event.get("type") == "response.done":
                self.responding = False
            if event.get("type") in ("response.output_audio.delta", "response.audio.delta"):
                rid = event.get("response_id", "unknown")
                self.audio.setdefault(rid, bytearray()).extend(base64.b64decode(event["delta"], validate=True))

    async def send(self, event):
        self.record("send", event)
        await asyncio.wait_for(self.ws.send(json.dumps(event)), self.timeout)

    async def until(self, predicate, timeout=None):
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeTimeout("No matching server event before deadline")
            try:
                event = json.loads(await asyncio.wait_for(self.ws.recv(), remaining))
            except asyncio.TimeoutError as exc:
                raise ProbeTimeout("No matching server event before deadline") from exc
            self.record("receive", event)
            if event.get("type") == "error":
                raise Rejected(json.dumps(event.get("error", event)))
            if predicate(event):
                return event

    async def exchange(self, event, event_type):
        await self.send(event)
        return await self.until(lambda e: e.get("type") == event_type)

    async def user(self, text, role="user"):
        return await self.exchange({"type": "conversation.item.create", "item": {
            "type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}},
            "conversation.item.done")

    async def response(self):
        await self.send({"type": "response.create"})
        return await self.until(lambda e: e.get("type") == "response.done")

    def close(self):
        self.log.close()
        for index, (rid, pcm) in enumerate(self.audio.items()):
            save_wav(self.directory / f"response-{index:02d}.wav", pcm)
        return [{"response_id": rid, "path": f"response-{i:02d}.wav", "bytes": len(pcm),
                 "duration_s": len(pcm) / 48000} for i, (rid, pcm) in enumerate(self.audio.items())]


async def run(args):
    from websockets.asyncio.client import connect
    pcm, input_info = wav_to_pcm16(args.input)
    if input_info["samples"] > args.max_input_seconds * 16000:
        raise ValueError("Input exceeds --max-input-seconds; trim explicitly, not silently")
    directory = args.output
    directory.mkdir(parents=True, exist_ok=True)
    # Never overwrite another run's evidence.
    if (directory / "events.jsonl").exists() or (directory / "report.json").exists():
        raise ValueError("Artifact directory already contains a probe run")
    report = {"url": args.url, "input": str(args.input), "input_audio": input_info,
              "max_output_tokens": args.max_tokens, "timeout_s": args.timeout,
              "instructions_prefix": PREFIX, "output_mode": "auto (not forced)",
              "interpretation": "Protocol support only, not proof of semantic privacy or native delegate training. "
              "delegate is a generic no-argument function parsed by llama.cpp from model XML.",
              "capabilities": {}}
    rec = None

    async def capability(name, operation):
        rec.phase = name
        start = rec.sequence
        try:
            detail = await asyncio.wait_for(operation(), args.timeout)
            result = {"status": "supported", "detail": detail}
        except Rejected as exc:
            result = {"status": "explicitly_rejected", "detail": str(exc)}
        except (ProbeTimeout, asyncio.TimeoutError) as exc:
            result = {"status": "timed_out", "detail": str(exc) or "Probe deadline exceeded"}
        result["event_sequence_range_exclusive_end"] = [start, rec.sequence]
        report["capabilities"][name] = result
        # A deadline must not leave generation running and contaminate later tests.
        if result["status"] != "supported" and rec.responding:
            rec.phase = name + ":timeout_cleanup"
            await rec.send({"type": "response.cancel"})
            try:
                await rec.until(lambda e: e.get("type") == "response.done", timeout=min(5, args.timeout))
            except (Rejected, ProbeTimeout) as exc:
                raise RuntimeError("Could not drain active response after failed probe") from exc
        return result

    try:
        async with connect(args.url, open_timeout=args.timeout, max_size=16 * 1024 * 1024,
                           close_timeout=3) as ws:
            rec = Recorder(ws, directory, args.timeout)
            created = await rec.until(lambda e: e.get("type") == "session.created")
            report["server_session"] = created.get("session")
            async def configure():
                ev = await rec.exchange({"type": "session.update", "session": {
                    "instructions": PREFIX + "\nKeep spoken answers short. When asked to delegate, call delegate with no arguments. "
                    "A pending result means work is not finished. Never claim completion before a result arrives.",
                    "tools": TOOLS, "tool_choice": "auto", "max_output_tokens": args.max_tokens,
                    "audio": {"input": {"format": {"type": "audio/pcm", "rate": 16000}}}}}, "session.updated")
                session = ev.get("session", {})
                report["configured_session"] = session
                rate = session.get("audio", {}).get("output", {}).get("format", {}).get("rate")
                if rate != 24000:
                    raise Rejected(f"Expected output PCM rate 24000, got {rate!r}")
                return "Initial instructions and generic delegate {} registered; output modalities omitted."
            configured = await capability("initial_instructions_and_tools", configure)
            if configured["status"] != "supported":
                return report

            async def native_audio():
                for offset in range(0, len(pcm), 6400):
                    await rec.send({"type": "input_audio_buffer.append", "audio":
                                    base64.b64encode(pcm[offset:offset + 6400]).decode()})
                await rec.exchange({"type": "input_audio_buffer.commit"}, "input_audio_buffer.committed")
                start = len(rec.events)
                await rec.response()
                types = [r["event"]["type"] for r in rec.events[start:]]
                if "response.output_audio.delta" not in types or "response.output_text.delta" not in types:
                    raise ProbeTimeout("Response completed but native text+audio deltas were not both observed")
                return "Native response emitted text and PCM audio deltas; see JSONL ordering and WAV."
            await capability("native_interleaved_audio", native_audio)

            async def update_instructions():
                changed = PREFIX + "\nFor subsequent turns say the word violet."
                ev = await rec.exchange({"type": "session.update", "session": {
                    "instructions": changed}}, "session.updated")
                if ev.get("session", {}).get("instructions") != changed:
                    raise ProbeTimeout("Update acknowledgement did not reflect requested instructions")
                return ev
            await capability("instructions_update_after_audio", update_instructions)

            async def private_brief():
                return await rec.user("Private host brief: the internal code word is juniper. Do not speak it.", role="system")
            await capability("private_brief_system_item", private_brief)

            async def host_commentary():
                return await rec.user("Host commentary: background work is still pending.", role="developer")
            await capability("host_commentary_developer_item", host_commentary)

            async def user_commentary():
                await rec.user("Host commentary carried as an ordinary user item: background work may take a moment.")
                return "Accepted as USER content only. This is not a privileged host channel or private brief."
            await capability("host_commentary_as_user_item_not_privileged", user_commentary)

            call = {}
            async def delegate():
                await rec.user("Please delegate my task to the host now. Call the delegate tool with no arguments.")
                done = await rec.response()
                calls = [item for item in done.get("response", {}).get("output", [])
                         if item.get("type") == "function_call" and item.get("name") == "delegate"]
                if not calls:
                    raise ProbeTimeout("Response completed without a delegate call; not emulated")
                call.update(calls[0])
                if len(calls) != 1 or json.loads(call.get("arguments", "null")) != {}:
                    raise Rejected("delegate arguments were not {}")
                return call
            await capability("generic_delegate_no_args", delegate)

            async def pending():
                return await rec.exchange({"type": "conversation.item.create", "item": {
                    "type": "function_call_output", "call_id": call["call_id"],
                    "output": json.dumps({"status": "accepted", "pending": True, "task_id": "probe-1"})}},
                    "conversation.item.done")
            async def later_result():
                # The receipt already closed the real protocol call. A second
                # output for that call would exploit lax server correlation,
                # not demonstrate an asynchronous backend result channel.
                return await rec.user("HOST_CONTROL " + json.dumps({
                    "kind": "commentary", "handoff_id": "probe-1",
                    "text": "The host finished the synthetic background task. Say that briefly."}))
            if call:
                accepted = await capability("delegate_return_accepted_pending", pending)
                if accepted["status"] == "supported":
                    await capability("response_to_pending_acknowledgement", rec.response)
                await capability("new_caller_text_while_pending", lambda: rec.user(
                    "While that task is pending, remember that I prefer the short answer."))
                await capability("caller_response_while_delegate_pending", rec.response)
                await capability("later_host_user_commentary_same_connection", later_result)
                await capability("response_after_later_host_user_commentary", rec.response)
            else:
                for name in ("delegate_return_accepted_pending", "response_to_pending_acknowledgement",
                             "new_caller_text_while_pending", "caller_response_while_delegate_pending",
                             "later_host_user_commentary_same_connection", "response_after_later_host_user_commentary"):
                    report["capabilities"][name] = {"status": "timed_out", "detail":
                        "Not exercised: no actual delegate call observed. No synthetic call inserted."}

            async def barge_in():
                await rec.user("Count slowly from one to twenty, speaking each number.")
                await rec.send({"type": "response.create"})
                await rec.until(lambda e: e.get("type") == "response.output_audio.delta")
                await rec.send({"type": "response.cancel"})
                done = await rec.until(lambda e: e.get("type") == "response.done")
                if done.get("response", {}).get("status") != "cancelled":
                    raise ProbeTimeout("Cancellation was sent after first audio but response did not report cancelled")
                await rec.user("Stop counting. Say only stopped.")
                await rec.response()
                return "Cancel after first audio reported cancelled; subsequent caller turn completed. Playback interruption remains client-side."
            await capability("cancel_and_barge_in", barge_in)
    except Exception as exc:
        report["fatal_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if rec:
            report["audio_artifacts"] = rec.close()
            timings = {}
            for row in rec.events:
                event = row["event"]
                kind = event.get("type", "")
                rid = event.get("response_id") or event.get("response", {}).get("id")
                if not rid:
                    continue
                timing = timings.setdefault(rid, {})
                key = {"response.created": "created_s", "response.output_text.delta": "first_text_s",
                       "response.output_audio.delta": "first_audio_s", "response.done": "done_s"}.get(kind)
                if key:
                    timing.setdefault(key, row["elapsed_s"])
                if kind == "response.done":
                    timing["status"] = event.get("response", {}).get("status")
            for timing in timings.values():
                if "created_s" in timing:
                    for kind in ("text", "audio"):
                        if f"first_{kind}_s" in timing:
                            timing[f"created_to_first_{kind}_s"] = timing[f"first_{kind}_s"] - timing["created_s"]
            report["response_timings"] = timings
        (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", type=Path, required=True, help="Existing caller WAV (PCM or float); downmixed/resampled to 16 kHz")
    p.add_argument("--output", type=Path, required=True, help="Fresh artifact directory: report.json, events.jsonl, response-NN.wav")
    p.add_argument("--url", default="ws://127.0.0.1:18391/v1/realtime")
    p.add_argument("--timeout", type=float, default=45, help="Maximum seconds per capability, including generation (default: 45)")
    p.add_argument("--max-tokens", type=int, default=192, help="Maximum output tokens per response (default: 192)")
    p.add_argument("--max-input-seconds", type=float, default=20)
    return p


def main():
    p = parser()
    args = p.parse_args()
    if args.timeout <= 0 or args.max_tokens <= 0 or args.max_input_seconds <= 0:
        p.error("Timeout, max tokens, and max input duration must be positive")
    report = asyncio.run(run(args))
    print(json.dumps({"report": str(args.output / "report.json"), "capabilities": {
        k: v["status"] for k, v in report["capabilities"].items()}, "fatal_error": report.get("fatal_error")}, indent=2))
    return 1 if report.get("fatal_error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
