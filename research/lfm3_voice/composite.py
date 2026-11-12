# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "websockets>=14,<17"]
# ///
"""Round two composite: router organ + host gate + speech organ, 40 points.

Per caller turn the frozen router (selected framing, native audio, frozen
threshold) decides Handoff; otherwise its argmax class picks silence, resume or
social. The host owns everything else, and each point records which component
decided it:

- host: briefs, pending notices and backend results are never spoken;
  commentary is spoken verbatim (male /tts) unless muted, then held; resume
  speaks held updates verbatim; a Handoff gets a fixed host acknowledgment.
- router: Handoff versus not, and the silence/resume/social class.
- native: social turns get a native /v1/realtime reply to caller audio with
  public host facts as text context (no tools).

Modes: `run` (LFM only), `manifest`, then Parakeet, then `score` (CPU).
"""

import argparse
import asyncio
import base64
import json
import time
from pathlib import Path

import router_organ as router
import speech_organ as speech

ROOT = Path(__file__).resolve().parent
ACK = "Okay, I'm passing that along now."
SOCIAL_CUE = ("You are the friendly voice of a phone assistant. A separate system handles all coding "
              "work; you never start, change or report work yourself. Use only the host facts below. "
              "Private background facts may answer a direct question but must not be volunteered. "
              "If the facts do not answer the caller, say you do not know rather than guessing. "
              "Reply to the caller's audio in one or two short sentences.\n\n")


async def native_social(context: str, audio16k: str, path: Path, timeout=30) -> dict:
    import websockets
    from live_probe import wav_to_pcm16
    pcm, _ = wav_to_pcm16(Path(audio16k), target_rate=16000)
    result = {}
    async with asyncio.timeout(timeout):
        async with websockets.connect(router.BASE.replace("http", "ws") + "/v1/realtime", max_size=32 << 20) as ws:
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
                "output_modalities": ["text", "audio"], "tools": [], "max_output_tokens": 512}}))
            await until("session.updated")
            await ws.send(json.dumps({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                      "content": [{"type": "input_text", "text": context}]}}))
            await until("conversation.item.done")
            for offset in range(0, len(pcm), 6400):
                await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                          "audio": base64.b64encode(pcm[offset:offset + 6400]).decode()}))
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            await until("input_audio_buffer.committed")
            sent = time.perf_counter()
            await ws.send(json.dumps({"type": "response.create"}))
            out, text = bytearray(), []
            while True:
                event = json.loads(await ws.recv())
                kind = event.get("type")
                if kind == "response.output_text.delta":
                    text.append(event["delta"])
                elif kind == "response.output_audio.delta":
                    result.setdefault("first_audio_s", time.perf_counter() - sent)
                    out.extend(base64.b64decode(event["delta"]))
                elif kind == "response.done":
                    result["status"] = event["response"].get("status")
                    break
    speech.save_wav(path, bytes(out), 24000)
    result.update(predicted_text="".join(text), audio_path=str(path.resolve()),
                  audio_duration_s=len(out) / 48000, elapsed_s=time.perf_counter() - sent)
    return result


def public_context(state: dict) -> str:
    lines = ["Host facts:"] + ([f"- {f}" for f in state["facts"]] or ["- none"])
    lines += ["Private background (answer only if directly asked):"] + [f"- {b}" for b in state["private"]]
    lines += ["Recent conversation:"] + [f"{s}: {t}" for s, t in state["ledger"][-4:]]
    return SOCIAL_CUE + "\n".join(lines)


def run(args):
    import httpx
    suite = json.loads((ROOT / "scenarios.json").read_text())
    analysis = json.loads(Path(args.analysis).read_text())
    framing, arm = analysis["selected_framing"], "native_audio"
    threshold = analysis["frozen_thresholds"][arm]
    frozen = router.load_frozen()
    out = Path(args.output).resolve()
    (out / "audio").mkdir(parents=True, exist_ok=False)
    records = []
    with httpx.Client(timeout=120) as client:
        def say(text, name):
            return speech.tts_render(client, text, out / "audio" / f"{name}.wav")
        for scenario in suite["scenarios"]:
            state = {"pending": {}, "instructions": [], "facts": [], "private": [], "ledger": [],
                     "held": [], "muted": False}
            for step in scenario["steps"]:
                pid = f"{scenario['id']}/{step['id']}"
                rec = {"point_id": pid, "gold_handoff": step["expect"]["delegate"],
                       "gold_silence": step["expect"]["silence"], "outputs": [], "decided_by": []}
                for event in step["events"]:
                    kind, text = event["kind"], event["text"]
                    if kind == "instructions":
                        state["instructions"].append(text)
                        rec["decided_by"].append("host:instructions_unspoken")
                    elif kind == "brief":
                        (state["private"] if "private" in text.lower() or "do not volunteer" in text.lower()
                         else state["facts"]).append(text)
                        rec["decided_by"].append("host:brief_unspoken")
                    elif kind == "backend_pending":
                        rec["decided_by"].append("host:pending_unspoken")
                    elif kind == "backend_result":
                        state["pending"].pop(event.get("handoff"), None)
                        state["facts"].append(text)
                        rec["decided_by"].append("host:result_unspoken")
                    elif kind == "commentary":
                        if state["muted"]:
                            state["held"].append(text)
                            rec["decided_by"].append("host:commentary_held_while_muted")
                        else:
                            r = say(text, pid.replace("/", "-") + "-commentary")
                            rec["outputs"].append({"source": "host_verbatim_tts", "script": text, **r})
                            state["ledger"].append(("assistant", text))
                            rec["decided_by"].append("host:commentary_verbatim")
                    elif kind == "caller":
                        info = frozen[text]
                        snapshot = {"pending_handoffs": list(state["pending"].values()),
                                    "instructions": state["instructions"] + (["Caller asked the assistant to stay quiet; speech is muted."] if state["muted"] else []),
                                    "known_facts": state["facts"] + state["private"] + [f"Held update, not yet spoken: {h}" for h in state["held"]],
                                    "ledger_prior": state["ledger"][-4:]}
                        decision = router.score(client, framing, router.render_snapshot(snapshot), audio_path=info["audio16k"])
                        probs = decision.get("probs") or {}
                        handoff = decision["p_work"] is not None and decision["p_work"] >= threshold
                        cls = "work" if handoff else max((c for c in probs if c != "work"), key=lambda c: probs[c] or 0)
                        raw_cls = cls
                        if cls == "resume" and not state["muted"]:
                            cls = "social"  # host: resume can only lift an existing mute
                        rec.update(router={k: decision.get(k) for k in ("p_work", "probs", "elapsed_s")}, router_raw_class=raw_cls,
                                   handoff=handoff, router_class=cls, caller_text=text, stt_text=info["stt_text"],
                                   ledger_cut=None)
                        state["ledger"].append(("caller", info["stt_text"]))
                        rec["decided_by"].append(f"router:{raw_cls}" + (f"->host:{cls}" if cls != raw_cls else ""))
                        if handoff:
                            state["pending"][step["id"]] = f"Caller turn handed off, no result yet: \"{info['stt_text']}\""
                            rec["ledger_cut"] = list(state["ledger"])
                            if not state["muted"]:
                                r = say(ACK, pid.replace("/", "-") + "-ack")
                                rec["outputs"].append({"source": "host_ack_tts", "script": ACK, **r,
                                                       "first_pcm_after_caller_s": decision["elapsed_s"] + r["first_audio_s"]})
                                rec["decided_by"].append("host:ack")
                        elif cls == "silence":
                            state["muted"] = True
                            rec["decided_by"].append("host:mute")
                        elif cls == "resume":
                            state["muted"] = False
                            for held in state["held"]:
                                r = say(held, pid.replace("/", "-") + "-held")
                                rec["outputs"].append({"source": "host_verbatim_tts_held", "script": held, **r,
                                                       "first_pcm_after_caller_s": decision["elapsed_s"] + r["first_audio_s"]})
                                state["ledger"].append(("assistant", held))
                            state["held"] = []
                            rec["decided_by"].append("host:resume_speak_held")
                        elif state["muted"]:
                            rec["decided_by"].append("host:social_suppressed_while_muted")
                        else:
                            try:
                                r = asyncio.run(native_social(public_context(state), info["audio16k"],
                                                              out / "audio" / (pid.replace("/", "-") + "-social.wav")))
                                r["first_pcm_after_caller_s"] = decision["elapsed_s"] + r.get("first_audio_s", float("nan"))
                            except Exception as exc:
                                r = {"status": "adapter_error", "error": f"{type(exc).__name__}: {exc}"}
                            rec["outputs"].append({"source": "native_social", "context": public_context(state), **r})
                            if r.get("predicted_text"):
                                state["ledger"].append(("assistant", r["predicted_text"]))
                records.append(rec)
                spoke = [o["source"] for o in rec["outputs"]]
                print(f"{pid:36} gold_h={int(rec['gold_handoff'])} h={int(rec.get('handoff', False))} "
                      f"cls={rec.get('router_class', '-'):8} spoke={spoke}", flush=True)
    (out / "records.json").write_text(json.dumps(records, indent=1))


def run_manifest(args):
    out = Path(args.output)
    records = json.loads((out / "records.json").read_text())
    paths = sorted({o["audio_path"] for r in records for o in r["outputs"] if o.get("audio_duration_s")})
    (out / "audio-manifest.json").write_text(json.dumps({"files": paths}, indent=1))
    print(len(paths), "files")


def score(records: list[dict], asr_dir: Path | None) -> dict:
    for r in records:
        r["handoff"] = r.get("handoff", False)
        r["routing_ok"] = r["handoff"] == r["gold_handoff"]
        r["spoke"] = bool(r["outputs"])
        r["silence_violation"] = r["gold_silence"] and r["spoke"]
        r["host_only"] = all(d.startswith("host:") for d in r["decided_by"])
        if asr_dir:
            for o in r["outputs"]:
                o["recognized"] = speech.recognized_text(asr_dir, o["audio_path"]) if o.get("audio_path") else None
    positives = [r for r in records if r["gold_handoff"]]
    negatives = [r for r in records if not r["gold_handoff"]]
    latency = sorted(o["first_pcm_after_caller_s"] for r in records for o in r["outputs"]
                     if o.get("first_pcm_after_caller_s") is not None)
    decision = sorted(r["router"]["elapsed_s"] for r in records if r.get("router"))
    pct = lambda xs, q: xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None
    return {
        "routing_correct": sum(r["routing_ok"] for r in records), "points": len(records),
        "missed_handoffs": [r["point_id"] for r in positives if not r["handoff"]],
        "false_handoffs": [r["point_id"] for r in negatives if r["handoff"]],
        "silence_violations": [r["point_id"] for r in records if r["silence_violation"]],
        "host_only_points": sum(r["host_only"] for r in records),
        "host_only_routing_correct": sum(r["host_only"] and r["routing_ok"] for r in records),
        "router_points": sum(not r["host_only"] for r in records),
        "router_points_routing_correct": sum((not r["host_only"]) and r["routing_ok"] for r in records),
        "required_silence_points_by_host": sum(r["gold_silence"] and r["host_only"] for r in records),
        "decision_latency_s": {"median": pct(decision, 0.5), "p95": pct(decision, 0.95)},
        "first_pcm_after_caller_s": {"median": pct(latency, 0.5), "p95": pct(latency, 0.95), "n": len(latency)},
    }


def run_score(args):
    out = Path(args.output)
    records = json.loads((out / "records.json").read_text())
    summary = score(records, Path(args.asr_dir) if args.asr_dir else None)
    (out / "scored.json").write_text(json.dumps(records, indent=1))
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


def parser():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--output", required=True)
    r.add_argument("--analysis", required=True)
    m = sub.add_parser("manifest")
    m.add_argument("--output", required=True)
    s = sub.add_parser("score")
    s.add_argument("--output", required=True)
    s.add_argument("--asr-dir")
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    {"run": run, "manifest": run_manifest, "score": run_score}[args.command](args)
