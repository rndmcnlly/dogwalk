# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=14,<17"]
# ///
"""Isolated native cancellation/overlapping-input probe, without tool history.

Reports transport/model clocks and generated tails, never actual audible stop.
"""
import argparse
import asyncio
import base64
import json
from pathlib import Path
import time

from live_probe import Recorder, wav_to_pcm16


async def run(args):
    import websockets
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "report.json").exists():
        raise ValueError("Use a fresh artifact directory")
    report, rec = {"actual_playback": False, "native_duplex_proven": False}, None
    try:
      async with asyncio.timeout(30):
       async with websockets.connect("ws://127.0.0.1:18391/v1/realtime", close_timeout=2,
                                     max_size=32*1024*1024) as ws:
        rec = Recorder(ws, args.output, 10)
        await rec.until(lambda e: e.get("type") == "session.created")
        await rec.exchange({"type": "session.update", "session": {
            "instructions": "Respond with interleaved text and audio.", "max_output_tokens": 512}}, "session.updated")
        rec.phase = "long_speech"
        await rec.user("Count from one to fifty, saying every number. Do not summarize.")
        requested = time.perf_counter()
        await rec.send({"type": "response.create"})
        await rec.until(lambda e: e.get("type") == "response.output_audio.delta")
        report["request_to_first_audio_s"] = time.perf_counter() - requested
        report["first_audio_observed_while_response_active"] = rec.responding
        if args.overlap_input:
            pcm, _ = wav_to_pcm16(args.input)
            await rec.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm[:6400]).decode()})
            report["caller_audio_sent_before_cancel"] = True
        rec.phase = "cancel"
        cancelled = time.perf_counter()
        await rec.send({"type": "response.cancel"})
        before = len(rec.events)
        done = await rec.until(lambda e: e.get("type") == "response.done")
        report.update(cancel_to_done_s=time.perf_counter()-cancelled,
                      response_status=done.get("response", {}).get("status"),
                      audio_delta_events_received_after_cancel=sum(r["event"].get("type") == "response.output_audio.delta"
                                                                  for r in rec.events[before:]))
        rec.phase = "new_caller"
        await rec.user("Stop counting. Say only: stopped.")
        await rec.response()
        report["new_turn_completed"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if rec:
            report["audio_artifacts"] = rec.close()
        (args.output / "report.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--input", type=Path)
    p.add_argument("--overlap-input", action="store_true")
    args = p.parse_args()
    if args.overlap_input and args.input is None:
        p.error("--overlap-input requires --input")
    asyncio.run(run(args))
