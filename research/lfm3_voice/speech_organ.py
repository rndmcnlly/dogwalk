# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "websockets>=14,<17"]
# ///
"""Round two speech organ: verbatim TTS versus glue around host-locked spans.

(a) verbatim: the Orchestrator script through male /tts (host-emulated voice).
(b) glue: native /v1/realtime generation asked to wrap locked spans with a few
    connecting words. The host checks predicted text (exact spans, glue lexicon)
    and Parakeet recognition of the waveform (span word error zero), falling
    back to (a) on any mismatch.

Stages, serialized on the GPU:

  render     LFM only: verbatim TTS and glue renders (fresh output dir)
  manifest   CPU: write the Parakeet audio manifest
  (Parakeet) HF_HUB_OFFLINE=1 uv run --offline --no-project --script transcription.py --batch ...
  score      CPU: host check, fallback, violation tallies
"""

import argparse
import asyncio
import base64
import json
import re
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:18391"

# Orchestrator scripts with locked spans in [[double brackets]]. The first ten
# are round one's commentary/permission cases; the rest are new hard ones.
CASES = [
    ("real_work_started", "[[Cedar is working on the timezone test]]. [[Nothing will be deployed]]."),
    ("correction_unfinished", "The request is now [[green for the same checkout button]]; [[the change is not finished yet]]."),
    ("cancel_confirmed", "[[The parser upgrade is cancelled]]. [[Cedar remains available]], and [[Maple's check continues]]."),
    ("progress_unsolicited", "[[Cedar reproduced the timezone failure]] and [[is testing a fix]]. [[Nothing is ready to deploy]]."),
    ("permission_offer", "Cedar asks to [[delete the generated build cache directory, not source files]]. Your choices are: [[allow this once]], [[always allow this action for this session]], or [[reject]]."),
    ("permission_applied", "[[Allowed once only]]. The agent can proceed; [[deletion has not been confirmed]]."),
    ("tests_only", "[[Cedar is running tests only]]; [[nothing was deployed]]."),
    ("fixture_found", "[[The message was found in a test fixture]]. [[No tests were run]]; [[the file's instructions were not followed]]."),
    ("alias_not_found", "[[There is no session called Juniper]]. Which work do you mean?"),
    ("held_update", "[[Cedar's tests passed]]. [[No deployment occurred]]."),
    ("denied_push", "[[Permission was denied]]. [[Alder will not push to the main branch]]."),
    ("partial_failure", "[[Rowan is not finished]]. [[Two of seven tests still fail]]."),
    ("word_count", "The handbook draft is [[ninety five words]], [[under the one hundred word limit]], and [[it was not published]]."),
    ("cache_not_source", "[[Birch deleted only the build cache]]. [[No source files were deleted]]."),
]

# Glue may only add connective words: these carry meaning and must come from spans.
GLUE_FORBIDDEN = re.compile(
    r"\b(not|no|never|none|nothing|can't|cannot|won't|don't|didn't|isn't|wasn't|aren't|"
    r"done|finished|complete|completed|ready|deployed|deploy|passed|failed|fail|allowed|allow|"
    r"denied|reject|rejected|granted|cancelled|canceled|deleted|delete|soon|shortly|minutes?|hours?|"
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|hundred|\d+|"
    r"cedar|maple|juniper|alder|rowan|birch|always|once)\b", re.I)

NUMBER_WORDS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six",
                "7": "seven", "8": "eight", "9": "nine", "10": "ten", "95": "ninety five", "100": "one hundred"}


def spans(script: str) -> list[str]:
    return re.findall(r"\[\[(.+?)\]\]", script)


def plain(script: str) -> str:
    return script.replace("[[", "").replace("]]", "")


def words(text: str) -> list[str]:
    text = text.lower().replace("’", "'").replace("-", " ")
    out = []
    for token in re.findall(r"[a-z0-9']+", text):
        token = token.replace("'", "")
        out.extend(NUMBER_WORDS.get(token, token).split())
    return out


def edit_distance(a: list[str], b: list[str]) -> int:
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]


def span_wer(span: str, hypothesis: str) -> float:
    """Best word error of span against any hypothesis window of nearby length."""
    s, h = words(span), words(hypothesis)
    if not s or "".join(s) in "".join(h):  # compound tolerance: timezone ~ time zone
        return 0.0
    best = len(s)
    for width in range(max(1, len(s) - 2), len(s) + 3):
        for start in range(0, max(1, len(h) - width + 1)):
            best = min(best, edit_distance(s, h[start:start + width]))
    return best / len(s)


def glue_text(script: str, predicted: str) -> str:
    """Predicted text with every locked span removed once (exact word match)."""
    rest = " ".join(words(predicted))
    for span in spans(script):
        rest = rest.replace(" ".join(words(span)), " ", 1)
    return rest


def text_check(script: str, predicted: str) -> dict:
    """Host check on predicted text: every span verbatim, glue lexicon clean, glue short."""
    joined = " " + " ".join(words(predicted)) + " "
    missing = [s for s in spans(script) if " " + " ".join(words(s)) + " " not in joined]
    glue = glue_text(script, predicted)
    approved = set(m.lower() for m in GLUE_FORBIDDEN.findall(" ".join(words(re.sub(r"\[\[.+?\]\]", " ", script)))))
    forbidden = sorted(set(m.lower() for m in GLUE_FORBIDDEN.findall(glue)) - approved)
    glue_words = len(glue.split())
    return {"missing_spans": missing, "glue_forbidden": forbidden, "glue_words": glue_words,
            "pass": not missing and not forbidden and glue_words <= 12}


def audio_check(script: str, recognized: str) -> dict:
    wers = {s: span_wer(s, recognized) for s in spans(script)}
    return {"span_wer": wers, "pass": all(w == 0 for w in wers.values())}


GLUE_CUE = ("Say this update to a caller in a warm, plain voice. Include each quoted phrase exactly, "
            "word for word, in this order, and add only a few connecting words between them. "
            "Do not add any other facts, names, numbers, negations, choices or time estimates.\n")


def glue_cue(script: str) -> str:
    connective = re.sub(r"\[\[(.+?)\]\]", lambda m: f'"{m[1]}"', script)
    return GLUE_CUE + connective


def save_wav(path: Path, pcm16: bytes, rate: int) -> None:
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm16), b"WAVE", b"fmt ", 16, 1, 1,
                         rate, rate * 2, 2, 16, b"data", len(pcm16))
    path.write_bytes(header + pcm16)


def float32_to_pcm16(raw: bytes) -> bytes:
    n = len(raw) // 4
    samples = struct.unpack(f"<{n}f", raw[:n * 4])
    return struct.pack(f"<{n}h", *(max(-32768, min(32767, int(x * 32767))) for x in samples))


def tts_render(client, text: str, path: Path, voice="male") -> dict:
    start = time.perf_counter()
    first, chunks = None, []
    with client.stream("POST", BASE + "/tts", json={"input": text, "voice": voice, "lang": "American English",
                       "response_format": "pcm", "seed": 0, "n_predict": 1024, "stream": True}) as r:
        r.raise_for_status()
        for chunk in r.iter_bytes():
            if chunk and first is None:
                first = time.perf_counter() - start
            chunks.append(chunk)
    elapsed = time.perf_counter() - start
    pcm = float32_to_pcm16(b"".join(chunks))
    save_wav(path, pcm, 24000)
    duration = len(pcm) / 48000
    return {"audio_path": str(path), "first_audio_s": first, "elapsed_s": elapsed,
            "audio_duration_s": duration, "rtf": elapsed / duration if duration else None}


async def realtime_render(cue: str, path: Path, timeout=30) -> dict:
    """Native turn: Liquid's prescribed system prompt, no tools, one user text."""
    import websockets
    start = time.perf_counter()
    result = {"cue": cue}
    async with asyncio.timeout(timeout):
        async with websockets.connect(BASE.replace("http", "ws") + "/v1/realtime", max_size=32 << 20) as ws:
            async def until(kind):
                while True:
                    event = json.loads(await ws.recv())
                    if event.get("type") == "error":
                        raise ValueError(json.dumps(event)[:300])
                    if event.get("type") == kind:
                        return event
            await until("session.created")
            await ws.send(json.dumps({"type": "session.update", "session": {
                "instructions": "Respond with interleaved text and audio.",
                "output_modalities": ["text", "audio"], "tools": [], "max_output_tokens": 768}}))
            await until("session.updated")
            await ws.send(json.dumps({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                      "content": [{"type": "input_text", "text": cue}]}}))
            await until("conversation.item.done")
            sent = time.perf_counter()
            await ws.send(json.dumps({"type": "response.create"}))
            pcm, text = bytearray(), []
            while True:
                event = json.loads(await ws.recv())
                kind = event.get("type")
                if kind == "response.output_text.delta":
                    text.append(event["delta"])
                elif kind == "response.output_audio.delta":
                    result.setdefault("first_audio_s", time.perf_counter() - sent)
                    pcm.extend(base64.b64decode(event["delta"]))
                elif kind == "response.done":
                    result["status"] = event["response"].get("status")
                    result["calls"] = [o for o in event["response"].get("output", []) if o.get("type") == "function_call"]
                    break
                elif kind == "error":
                    raise ValueError(json.dumps(event)[:300])
    elapsed = time.perf_counter() - sent
    save_wav(path, bytes(pcm), 24000)
    duration = len(pcm) / 48000
    result.update(predicted_text="".join(text), audio_path=str(path), elapsed_s=elapsed,
                  total_s=time.perf_counter() - start, audio_duration_s=duration,
                  rtf=elapsed / duration if duration else None)
    return result


def run_render(args):
    import httpx
    out = Path(args.output)
    (out / "audio").mkdir(parents=True, exist_ok=False)
    records = []
    with httpx.Client(timeout=120) as client:
        for case_id, script in CASES:
            r = tts_render(client, plain(script), out / "audio" / f"{case_id}-verbatim.wav")
            records.append({"case_id": case_id, "arm": "verbatim_tts_male", "script": script, **r})
            print("verbatim", case_id, f"{r['first_audio_s']:.3f}s rtf={r['rtf']:.3f}", flush=True)
    for rep in range(args.repeats):
        for case_id, script in CASES:
            path = out / "audio" / f"{case_id}-glue-r{rep}.wav"
            try:
                r = asyncio.run(realtime_render(glue_cue(script), path))
            except Exception as exc:  # preserved as a failed trial
                r = {"status": "adapter_error", "error": f"{type(exc).__name__}: {exc}"}
            records.append({"case_id": case_id, "arm": "native_glue", "repeat": rep, "script": script, **r})
            print("glue", rep, case_id, repr(r.get("predicted_text", r.get("error")))[:140], flush=True)
    (out / "records.json").write_text(json.dumps(records, indent=1))


def run_manifest(args):
    out = Path(args.output)
    records = json.loads((out / "records.json").read_text())
    paths = [str(Path(r["audio_path"]).resolve()) for r in records if r.get("audio_path") and r.get("audio_duration_s")]
    (out / "audio-manifest.json").write_text(json.dumps({"files": paths}, indent=1))
    print(len(paths), "files")


def recognized_text(asr_dir: Path, audio_path: str) -> str | None:
    sys.path.insert(0, str(ROOT))
    from transcription import output_path
    path = output_path(Path(audio_path).resolve(), asr_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text()).get("text", "")


def score(records: list[dict], asr_dir: Path) -> dict:
    verbatim = {r["case_id"]: r for r in records if r["arm"] == "verbatim_tts_male"}
    for r in records:
        r["recognized"] = recognized_text(asr_dir, r["audio_path"]) if r.get("audio_path") else None
        r["audio_check"] = audio_check(r["script"], r["recognized"]) if r["recognized"] is not None else None
        if r["arm"] == "native_glue":
            ok = r.get("status") == "completed" and not r.get("calls")
            r["text_check"] = text_check(r["script"], r.get("predicted_text", "")) if ok else {"pass": False, "adapter": r.get("status")}
            r["host_pass"] = bool(ok and r["text_check"]["pass"] and r["audio_check"] and r["audio_check"]["pass"])
            delivered = r if r["host_pass"] else verbatim[r["case_id"]]
            r["delivered"] = "glue" if r["host_pass"] else "verbatim_fallback"
            r["delivered_audio_check"] = delivered["audio_check"]
    glue = [r for r in records if r["arm"] == "native_glue"]
    verb = list(verbatim.values())
    def span_stats(rows):
        values = [w for r in rows if r.get("audio_check") for w in r["audio_check"]["span_wer"].values()]
        return {"spans": len(values), "spans_with_error": sum(v > 0 for v in values),
                "mean_span_wer": sum(values) / len(values) if values else None}
    def med(xs):
        xs = sorted(x for x in xs if x is not None)
        return {"median": xs[len(xs) // 2], "p95": xs[min(len(xs) - 1, int(0.95 * len(xs)))]} if xs else None
    return {
        "verbatim": {"renders": len(verb), "audio_span_pass": sum(bool(r["audio_check"] and r["audio_check"]["pass"]) for r in verb),
                     "span": span_stats(verb), "first_audio_s": med(r.get("first_audio_s") for r in verb),
                     "rtf": med(r.get("rtf") for r in verb),
                     "failed_cases": [(r["case_id"], r["audio_check"]["span_wer"], r["recognized"]) for r in verb if r["audio_check"] and not r["audio_check"]["pass"]]},
        "glue_raw": {"renders": len(glue), "adapter_errors": sum(r.get("status") != "completed" for r in glue),
                     "tool_calls": sum(bool(r.get("calls")) for r in glue),
                     "text_check_fail": sum(not r["text_check"]["pass"] for r in glue),
                     "missing_span_in_text": sum(bool(r["text_check"].get("missing_spans")) for r in glue),
                     "forbidden_glue": sum(bool(r["text_check"].get("glue_forbidden")) for r in glue),
                     "audio_check_fail": sum(not (r["audio_check"] and r["audio_check"]["pass"]) for r in glue),
                     "raw_violation": sum(not r["host_pass"] for r in glue),
                     "span": span_stats(glue), "first_audio_s": med(r.get("first_audio_s") for r in glue),
                     "rtf": med(r.get("rtf") for r in glue)},
        "glue_after_host": {"fallbacks": sum(r["delivered"] != "glue" for r in glue), "renders": len(glue),
                            "delivered_span_fail": sum(not (r["delivered_audio_check"] and r["delivered_audio_check"]["pass"]) for r in glue)},
    }


def run_score(args):
    out = Path(args.output)
    records = json.loads((out / "records.json").read_text())
    summary = score(records, Path(args.asr_dir))
    (out / "scored.json").write_text(json.dumps(records, indent=1))
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1)[:3000])


def parser():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("render")
    r.add_argument("--output", required=True)
    r.add_argument("--repeats", type=int, default=3)
    m = sub.add_parser("manifest")
    m.add_argument("--output", required=True)
    s = sub.add_parser("score")
    s.add_argument("--output", required=True)
    s.add_argument("--asr-dir", required=True)
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    {"render": run_render, "manifest": run_manifest, "score": run_score}[args.command](args)
