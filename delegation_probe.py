#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["openai[realtime]>=2", "python-dotenv"]
# ///
"""Probe GPT-Live client delegation with synthetic callers whose speech timing is known.

Each scenario runs in its own Live session on the production Voice Frontend prompt
(bundled from cf_worker/src/voice_session.ts) with the production delegation config.
Caller lines are synthesized with macOS `say`, trimmed to their speech, encoded as
G.711 mu-law at 8 kHz, and streamed in real time as 20 ms frames, silence included,
the way a phone leg would. Every server event is logged with its receipt time on the
same clock as the audio we sent (0 = first frame), so for each delegation we can ask:

- where offset_ms falls against the true end of speech (ground truth, not a transcript);
- how late the covering input transcript arrives after the delegation (the 300 ms grace);
- what the +1500 ms allowance in takeTranscript would pull in from later speech.

Usage: uv run --script delegation_probe.py [--reps N] [--only name,...] [--out DIR]
Reads OPENAI_API_KEY from .env. Costs real GPT-Live minutes: about 20 s per run.
"""

import argparse, asyncio, base64, json, os, subprocess, sys, tempfile, time
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parent
FRAME_MS = 20
FRAME_BYTES = 8000 * FRAME_MS // 1000  # mu-law: one byte per sample
SILENCE = b"\xff"  # mu-law zero

# A scenario is caller lines and gaps: str = speak it, int = that many ms of silence.
SCENARIOS = {
    "plain": ["Can you start a new session in the dogwalk project and have it fix the failing tests?", 12000],
    "trailing": ["Check on Juniper for me.", 250, "And tell me whether the tests passed this time.", 12000],
    "correction": ["Start a session in the bayleaf project.", 700, "No, sorry, the dogwalk project, not bayleaf.", 12000],
    "long": ["Okay so, I want a new session in dogwalk,", 450, "and it should look at the transcript code in the voice session,", 450,
             "the part that cuts the transcript for a handoff,", 450, "and tell me whether the grace period makes sense.", 12000],
    "chitchat": ["Hi there, how is it going today?", 8000],
}


def frontend() -> dict:
    """The production Voice Frontend instructions and voice, via esbuild (no Worker runtime needed)."""
    with tempfile.TemporaryDirectory() as tmp:
        entry, bundle = Path(tmp, "emit.ts"), Path(tmp, "emit.mjs")
        src = ROOT / "cf_worker" / "src"
        entry.write_text(
            f'import {{ frontendInstructions }} from "{src}/voice_session";\n'
            f'import {{ DEFAULT_THEME }} from "{src}/themes";\n'
            'process.stdout.write(JSON.stringify({ instructions: frontendInstructions(DEFAULT_THEME), voice: DEFAULT_THEME.voice }));\n')
        subprocess.run(["npx", "--yes", "esbuild", str(entry), "--bundle", "--platform=node", "--format=esm",
                        "--loader:.md=text", "--loader:.pcm=binary", "--loader:.wasm=binary", "--loader:.bin=binary",
                        "--external:cloudflare:*", f"--outfile={bundle}", "--log-level=error"],
                       check=True, cwd=ROOT / "cf_worker")
        return json.loads(subprocess.run(["node", str(bundle)], check=True, capture_output=True).stdout)


def speak(text: str, voice: str, cache: Path) -> bytes:
    """mu-law 8 kHz speech for text, with leading and trailing silence trimmed so its edges are the truth."""
    out = cache / f"{voice}-{abs(hash(text)):x}.ulaw"
    if not out.exists():
        aiff = out.with_suffix(".aiff")
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
        trim = "silenceremove=start_periods=1:start_threshold=-45dB"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff),
                        "-af", f"{trim},areverse,{trim},areverse,highpass=f=300,lowpass=f=3400",
                        "-ar", "8000", "-ac", "1", "-f", "mulaw", str(out)], check=True)
    return out.read_bytes()


def build(script: list, voice: str, cache: Path) -> tuple[bytes, list[dict]]:
    """The whole caller track (with 1 s lead-in) and the ground-truth span of each line."""
    track, lines = bytearray(SILENCE * 8000), []
    for part in script:
        if isinstance(part, int):
            track += SILENCE * (8 * part)
        else:
            audio = speak(part, voice, cache)
            start = len(track) // 8
            track += audio
            lines.append({"text": part, "start_ms": start, "end_ms": len(track) // 8})
    track += SILENCE * (FRAME_BYTES - len(track) % FRAME_BYTES)
    return bytes(track), lines


async def run(client: AsyncOpenAI, name: str, rep: int, fe: dict, args, cache: Path) -> dict:
    track, lines = build(SCENARIOS[name], args.caller_voice, cache)
    events, t0 = [], None
    now = lambda: round((time.monotonic() - t0) * 1000) if t0 else None
    session = {
        "model": args.model, "instructions": fe["instructions"],
        "audio": {"format": {"type": "audio/pcmu", "rate": 8000}, "output": {"voice": fe["voice"]}},
        "delegation": {"type": "client", "acknowledgement_delay_ms": args.ack_ms},
    }
    async with client.live.connect() as conn:
        await conn.session.start(session=session, event_id="start")
        started = asyncio.Event()
        replies: list[asyncio.Task] = []

        async def reply(delegation_id: str):
            # Stand-in for the Orchestrator: a brief at once, a say after --reply-ms.
            await conn.session.thinking.append(delegation_id=delegation_id, content="Working on it; nothing has started yet.")
            await asyncio.sleep(args.reply_ms / 1000)
            await conn.session.commentary.append(delegation_id=delegation_id, content="Tell the caller that's handled and nothing else needs their attention.")

        async def stream():
            nonlocal t0
            await started.wait()
            t0 = time.monotonic()
            for i in range(0, len(track), FRAME_BYTES):
                await asyncio.sleep(max(0, t0 + (i // FRAME_BYTES) * FRAME_MS / 1000 - time.monotonic()))
                await conn.session.input_audio.append(audio=base64.b64encode(track[i:i + FRAME_BYTES]).decode())
            events.append({"recv_ms": now(), "type": "probe.audio.done"})
            await asyncio.sleep(0.5)
            await conn.session.close()

        sender = asyncio.create_task(stream())
        async for event in conn:
            data = event.model_dump(exclude_none=True)
            kind = data.get("type")
            if kind == "session.started":
                started.set()
            if kind == "session.output_audio.delta":
                data = {"type": kind, "bytes": len(base64.b64decode(data.get("delta", "")))}
            data["recv_ms"] = now()
            events.append(data)
            if kind == "session.delegation.created" and (data.get("delegation") or {}).get("target") == "client":
                replies.append(asyncio.create_task(reply(data["delegation"]["id"])))
            if kind == "error":
                print(f"  error: {json.dumps(data)[:300]}", file=sys.stderr)
            if kind == "session.closed":
                break
        sender.cancel()
        for task in replies:
            task.cancel()
    result = {"scenario": name, "rep": rep, "lines": lines, "events": events}
    path = args.out / f"{name}-{rep}.json"
    path.write_text(json.dumps(result, indent=1))
    return result


def analyze(result: dict) -> list[dict]:
    """One row per delegation, joining it to ground truth and to transcript arrival."""
    ev, lines = result["events"], result["lines"]
    inputs = [e for e in ev if e["type"] == "session.input_transcript.delta"]
    rows = []
    for d in (e for e in ev if e["type"] == "session.delegation.created"):
        off, recv = d.get("offset_ms"), d["recv_ms"]
        said = [l for l in lines if l["start_ms"] <= (off if off is not None else recv)]
        line = said[-1] if said else None
        covering = [e for e in inputs if off is not None and e.get("start_ms", 0) <= off + 1500]
        before = [e for e in covering if e.get("start_ms", 0) <= off]
        beyond = [e for e in covering if e.get("start_ms", 0) > off]
        speech_end = line["end_ms"] if line else None
        later = [l for l in lines if l["start_ms"] > off] if off is not None else []
        rows.append({
            "scenario": result["scenario"], "rep": result["rep"], "id": d["delegation"]["id"][-6:],
            "line": line["text"][:40] if line else None,
            "offset_ms": off, "recv_ms": recv,
            # Positive: the delegation's offset falls after the true end of the line; negative: mid-line.
            "offset_minus_speech_end": off - speech_end if off is not None and speech_end is not None else None,
            "recv_minus_speech_end": recv - speech_end if speech_end is not None else None,
            "recv_minus_offset": recv - off if off is not None else None,
            # Positive: transcript up to the offset was still arriving after the delegation.
            "transcript_late_ms": max(e["recv_ms"] for e in before) - recv if before else None,
            "slack_late_ms": max(e["recv_ms"] for e in covering) - recv if covering else None,
            "slack_pulls_later_line": bool(beyond and later and any(e.get("start_ms", 0) >= later[0]["start_ms"] - 200 for e in beyond)),
            "first_voice_after_ms": next((e["recv_ms"] - recv for e in ev if e["type"] == "session.output_transcript.delta" and e["recv_ms"] >= recv), None),
        })
    if not rows:
        rows.append({"scenario": result["scenario"], "rep": result["rep"], "id": None, "line": "(no delegation)"})
    return rows


def calibration(result: dict) -> list[dict]:
    """Transcript timestamps against ground truth: do start_ms and end_ms share our audio clock?"""
    inputs = [e for e in result["events"] if e["type"] == "session.input_transcript.delta"]
    out = []
    for line in result["lines"]:
        mine = [e for e in inputs if line["start_ms"] - 1000 <= e.get("start_ms", -9e9) <= line["end_ms"] + 500]
        if mine:
            out.append({"line": line["text"][:30], "true_start": line["start_ms"], "true_end": line["end_ms"],
                        "tx_start": min(e["start_ms"] for e in mine), "tx_end": max(e.get("end_ms", 0) for e in mine),
                        "tx_last_recv": max(e["recv_ms"] for e in mine), "deltas": len(mine)})
    return out


async def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--only", default=",".join(SCENARIOS))
    p.add_argument("--model", default=os.environ.get("OPENAI_LIVE_MODEL", "gpt-live-1"))
    p.add_argument("--ack-ms", type=int, default=400)
    p.add_argument("--reply-ms", type=int, default=2500)
    p.add_argument("--caller-voice", default="Samantha")
    p.add_argument("--out", type=Path, default=Path("/tmp/dwprobe/runs") / time.strftime("%Y%m%d-%H%M%S"))
    p.add_argument("--analyze", type=Path, help="re-analyze a previous --out directory without calling the API")
    args = p.parse_args()

    if args.analyze:
        results = [json.loads(f.read_text()) for f in sorted(args.analyze.glob("*.json"))]
    else:
        load_dotenv(ROOT / ".env")
        args.out.mkdir(parents=True, exist_ok=True)
        cache = Path("/tmp/dwprobe/tts"); cache.mkdir(parents=True, exist_ok=True)
        fe = frontend()
        client = AsyncOpenAI()
        results = []
        for rep in range(args.reps):
            for name in args.only.split(","):
                print(f"running {name} #{rep}", file=sys.stderr)
                results.append(await run(client, name, rep, fe, args, cache))
        print(f"raw events: {args.out}", file=sys.stderr)
    print(json.dumps({"delegations": [r for res in results for r in analyze(res)],
                      "calibration": [dict(c, scenario=res["scenario"], rep=res["rep"]) for res in results for c in calibration(res)]}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
