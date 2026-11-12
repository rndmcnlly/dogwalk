# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx"]
# ///
"""Round two router organ: one constrained token, scored by its probability.

The router sees a compact host snapshot (pending Handoffs, active instructions,
known facts, last ledger lines) plus the current caller turn as native audio,
intended text, or LFM STT text. It answers a closed question with one token;
P(work) is read from top_logprobs rather than sampled.

Subcommands (all need the exclusive local GPU lease except `analyze`):

  main      score every caller point of the 40-point suite: framings x arms
  analyze   PR curves, operating points at full main recall (CPU only)
  heldout   score held-out cases once at a frozen framing and thresholds

Host-gate convention: the router is consulted only on caller turns. Non-caller
points (briefs, results, commentary, instructions) never Handoff because the
host never asks; that zero is host behavior and is reported separately.
"""

import argparse
import base64
import json
import math
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:18391"
MODEL = "lfm3-audio-2b-q8"
FROZEN = ROOT / "artifacts/repetitions-v2-16k/frozen-callers.json"
ARMS = ("native_audio", "oracle_text", "lfm_stt")
# Corrections and cancellations of pending work: a miss is critical (PLAN2).
CRITICAL = {"pending_correction/revise", "pending_cancellation/cancel", "delayed_stale_result/hold",
            "growing_context/second", "pstn_eight_khz/repair"}

# --- framings (max three, chosen on the main suite only) -------------------

SYSTEM_ROLE = ("You are a routing classifier for a phone voice assistant. A stronger "
               "Session Orchestrator manages coding agents and owns all agent work, "
               "permission choices and work status. The voice assistant itself can only "
               "chat, recall what the host state already says, and stop or resume talking.")

RULES = ("The turn needs the Session Orchestrator if the caller asks to start, change, "
         "correct, cancel or check agent work, answers a permission choice, or asks about "
         "work state that the host state does not already answer. Quoted, hypothetical or "
         "social talk, speaking-style requests, and requests to be quiet or to resume "
         "speaking do not need it, even when they mention work words or session names.")

FRAMINGS = {
    "yesno_rules": {
        "system": SYSTEM_ROLE + " Answer with one word: yes or no.",
        "question": RULES + " Does the current caller turn need the Session Orchestrator? Answer yes or no.",
        "labels": {"yes": "work", "no": "other"},
    },
    "multiway": {
        "system": SYSTEM_ROLE + " Answer with one word: work, silence, resume, or social.",
        "question": ("Classify the current caller turn. work: the caller asks to start, change, "
                     "correct, cancel or check agent work, answers a permission choice, or asks "
                     "about work state the host state does not already answer. silence: the caller "
                     "asks the assistant to stop talking or stay quiet. resume: the caller lets the "
                     "assistant speak again. social: conversation, quoted or hypothetical text, "
                     "style requests, or recall the host state already answers. "
                     "Answer with one word: work, silence, resume, or social."),
        "labels": {"work": "work", "silence": "silence", "resume": "resume", "social": "social"},
    },
    "yesno_minimal": {
        "system": SYSTEM_ROLE + " Answer with one word: yes or no.",
        "question": "Is the caller asking for something that only the Session Orchestrator can do or know? Answer yes or no.",
        "labels": {"yes": "work", "no": "other"},
    },
}


# --- suite -> caller points with host snapshots ----------------------------

def caller_points(suite: dict) -> list[dict]:
    """Every caller step with gold routing, a gold-history snapshot and class.

    The snapshot is built from earlier events under gold routing (oracle
    history), so the organ is scored on the current turn only.
    """
    points = []
    for scenario in suite["scenarios"]:
        pending, instructions, facts, ledger = {}, [], [], []
        muted = False
        for step in scenario["steps"]:
            caller = None
            for event in step["events"]:
                kind, text = event["kind"], event["text"]
                if kind == "caller":
                    caller = text
                elif kind == "instructions":
                    instructions.append(text)
                elif kind == "brief":
                    facts.append(text)
                elif kind == "backend_result":
                    pending.pop(event.get("handoff"), None)
                    facts.append(text)
                elif kind == "backend_pending":
                    pass  # pending entry already exists from the Handoff itself
                elif kind == "commentary":
                    if muted:
                        facts.append("Held update, not yet spoken: " + text)
                    else:
                        ledger.append(("assistant", text))
            if caller is not None:
                expect = step["expect"]
                cls = ("work" if expect["delegate"] else
                       "silence" if expect["silence"] else
                       "resume" if muted else "social")
                points.append({
                    "point_id": f"{scenario['id']}/{step['id']}",
                    "caller_text": caller, "handoff": expect["delegate"], "class": cls,
                    "critical": f"{scenario['id']}/{step['id']}" in CRITICAL,
                    "audio_condition": scenario.get("audio_condition", "clean"),
                    "snapshot": {"pending_handoffs": list(pending.values()),
                                 "instructions": list(instructions) + (["Caller asked the assistant to stay quiet; speech is muted."] if muted else []),
                                 "known_facts": list(facts),
                                 "ledger_prior": list(ledger[-4:])}})
                ledger.append(("caller", caller))
                if expect["delegate"]:
                    pending[step["id"]] = f"Caller turn handed off, no result yet: \"{caller}\""
                if cls == "silence":
                    muted = True
                elif cls == "resume":
                    muted = False
    return points


def render_snapshot(snapshot: dict, ledger_text=None) -> str:
    """ledger_text maps an oracle caller line to what the host observed (ASR)."""
    lines = ["Host state (from the host program):"]
    for title, key in (("Pending work handed to the Session Orchestrator", "pending_handoffs"),
                       ("Active instructions", "instructions"), ("Known facts", "known_facts")):
        items = snapshot.get(key) or []
        lines.append(f"{title}: " + ("none" if not items else ""))
        lines += [f"- {item}" for item in items]
    ledger = snapshot.get("ledger_prior") or []
    lines.append("Recent conversation: " + ("none" if not ledger else ""))
    for speaker, text in ledger:
        if speaker == "caller" and ledger_text:
            text = ledger_text.get(text, text)
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def build_request(framing: str, snapshot_text: str, *, caller_text=None, audio_path=None,
                  top_logprobs=20) -> dict:
    spec = FRAMINGS[framing]
    content = [{"type": "text", "text": snapshot_text + "\n\nCurrent caller turn:"}]
    if audio_path is not None:
        content.append({"type": "input_audio", "input_audio": {
            "data": base64.b64encode(Path(audio_path).read_bytes()).decode(), "format": "wav"}})
    else:
        content[0]["text"] += f' (transcribed) "{caller_text}"'
    content.append({"type": "text", "text": spec["question"]})
    return {"model": MODEL, "temperature": 0, "seed": 0, "max_tokens": 1, "stream": False,
            "logprobs": True, "top_logprobs": top_logprobs,
            "messages": [{"role": "system", "content": spec["system"]},
                         {"role": "user", "content": content}]}


def class_probs(top: list[dict], labels: dict) -> dict:
    """Normalized class probabilities from first-token alternatives.

    A label matches a token if the token, stripped and lowercased, is a
    non-empty prefix of the label (so 'sil' counts for 'silence') and at
    least two characters or the whole label. Mass outside labels is reported.
    """
    mass = {cls: 0.0 for cls in set(labels.values())}
    covered = 0.0
    for alt in top:
        token = alt["token"].strip().lower()
        p = math.exp(alt["logprob"])
        for word, cls in labels.items():
            if token and word.startswith(token) and (len(token) >= 2 or token == word):
                mass[cls] += p
                covered += p
                break
    total = sum(mass.values())
    probs = {cls: (m / total if total else None) for cls, m in mass.items()}
    return {"probs": probs, "covered_mass": covered}


# --- scoring ---------------------------------------------------------------

def score(client, framing: str, snapshot_text: str, **kwargs) -> dict:
    body = build_request(framing, snapshot_text, **kwargs)
    start = time.perf_counter()
    response = client.post(BASE + "/v1/chat/completions", json=body)
    elapsed = time.perf_counter() - start
    record = {"framing": framing, "elapsed_s": elapsed, "http_status": response.status_code}
    try:
        choice = response.json()["choices"][0]
        top = choice["logprobs"]["content"][0]["top_logprobs"]
        record.update(token=choice["message"].get("content"), top=[{"token": t["token"], "logprob": t["logprob"]} for t in top],
                      timings=response.json().get("timings"), **class_probs(top, FRAMINGS[framing]["labels"]))
        record["p_work"] = record["probs"].get("work")
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        record.update(error=f"{type(exc).__name__}: {exc}", raw=response.text[:500], p_work=None)
    return record


def load_frozen(path=FROZEN) -> dict:
    """oracle caller text -> {audio16k, stt_text, stt_elapsed_s}."""
    out = {}
    for entry in json.loads(Path(path).read_text()).values():
        stt = entry["stt"]
        out[entry["synthesis"]["text"]] = {"audio16k": stt["recognizer_audio_path"],
                                           "stt_text": stt["predicted_text"].strip(),
                                           "stt_elapsed_s": stt.get("elapsed_s")}
    return out


def run_main(args):
    import httpx
    suite = json.loads((ROOT / "scenarios.json").read_text())
    points = caller_points(suite)
    frozen = load_frozen()
    stt_map = {text: info["stt_text"] for text, info in frozen.items()}
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    records = []
    with httpx.Client(timeout=120) as client:
        for framing in args.framings:
            for arm in ARMS:
                for point in points:
                    info = frozen[point["caller_text"]]
                    snap = render_snapshot(point["snapshot"], None if arm == "oracle_text" else stt_map)
                    kwargs = ({"audio_path": info["audio16k"]} if arm == "native_audio" else
                              {"caller_text": point["caller_text"] if arm == "oracle_text" else info["stt_text"]})
                    record = score(client, framing, snap, **kwargs)
                    record.update(arm=arm, point_id=point["point_id"], handoff=point["handoff"],
                                  cls=point["class"], critical=point["critical"],
                                  stt_text=info["stt_text"] if arm == "lfm_stt" else None,
                                  stt_elapsed_s=info["stt_elapsed_s"])
                    records.append(record)
                    print(f"{framing:14} {arm:12} {point['point_id']:36} gold={int(point['handoff'])} "
                          f"p={record['p_work'] if record['p_work'] is None else round(record['p_work'], 4)}",
                          flush=True)
    (out / "records.json").write_text(json.dumps(records, indent=1))
    (out / "points.json").write_text(json.dumps(points, indent=1))


# --- analysis (CPU only) ---------------------------------------------------

def curve(scored: list[tuple[float, bool]]) -> list[dict]:
    """Precision/recall at each distinct threshold (Handoff iff p >= t)."""
    positives = sum(1 for _, gold in scored if gold)
    rows = []
    for t in sorted({p for p, _ in scored}, reverse=True):
        tp = sum(1 for p, gold in scored if gold and p >= t)
        fp = sum(1 for p, gold in scored if not gold and p >= t)
        rows.append({"threshold": t, "tp": tp, "fp": fp, "fn": positives - tp,
                     "precision": tp / (tp + fp) if tp + fp else None,
                     "recall": tp / positives if positives else None})
    return rows


def operating_point(scored: list[tuple[float, bool]]) -> dict:
    """Highest threshold that keeps every positive: t = min positive score."""
    t = min(p for p, gold in scored if gold)
    fp = sorted(p for p, gold in scored if not gold and p >= t)
    max_neg = max((p for p, gold in scored if not gold), default=0.0)
    return {"threshold": t, "false_handoffs": len(fp),
            "negatives": sum(1 for _, gold in scored if not gold),
            "margin": t - max_neg}


def multiway_accuracy(records: list[dict]) -> dict:
    hits = sum(1 for r in records if r.get("probs") and
               max(r["probs"], key=lambda c: r["probs"][c] or 0) == r["cls"])
    return {"argmax_correct": hits, "n": len(records)}


def analyze(records: list[dict]) -> dict:
    cells = {}
    for framing in dict.fromkeys(r["framing"] for r in records):
        for arm in dict.fromkeys(r["arm"] for r in records):
            rows = [r for r in records if r["framing"] == framing and r["arm"] == arm]
            if not rows:
                continue
            errors = [r["point_id"] for r in rows if r["p_work"] is None]
            scored = [(r["p_work"], r["handoff"]) for r in rows if r["p_work"] is not None]
            op = operating_point(scored)
            cell = {"n": len(rows), "errors": errors, "operating_point": op, "curve": curve(scored),
                    "false_at_op": sorted(r["point_id"] for r in rows if not r["handoff"] and r["p_work"] is not None and r["p_work"] >= op["threshold"]),
                    "lowest_positive": min((r for r in rows if r["handoff"]), key=lambda r: r["p_work"])["point_id"],
                    "argmax_false": sorted(r["point_id"] for r in rows if not r["handoff"] and (r["p_work"] or 0) > 0.5),
                    "argmax_missed": sorted(r["point_id"] for r in rows if r["handoff"] and (r["p_work"] or 0) <= 0.5),
                    "median_elapsed_s": sorted(r["elapsed_s"] for r in rows)[len(rows) // 2],
                    "p95_elapsed_s": sorted(r["elapsed_s"] for r in rows)[min(len(rows) - 1, math.ceil(0.95 * len(rows)) - 1)]}
            if framing == "multiway":
                cell["multiway"] = multiway_accuracy(rows)
            cells[f"{framing}|{arm}"] = cell
    return cells


def select(cells: dict) -> str:
    """Framing selection rule, fixed before results: fewest false Handoffs at
    full recall summed over the three arms, then largest summed margin."""
    framings = dict.fromkeys(k.split("|")[0] for k in cells)
    def key(f):
        own = [c for k, c in cells.items() if k.startswith(f + "|")]
        return (sum(c["operating_point"]["false_handoffs"] for c in own),
                -sum(c["operating_point"]["margin"] for c in own))
    return min(framings, key=key)


def run_analyze(args):
    records = json.loads((Path(args.input) / "records.json").read_text())
    cells = analyze(records)
    chosen = select(cells)
    result = {"cells": cells, "selected_framing": chosen,
              "frozen_thresholds": {k.split("|")[1]: c["operating_point"]["threshold"]
                                    for k, c in cells.items() if k.startswith(chosen + "|")}}
    (Path(args.input) / "analysis.json").write_text(json.dumps(result, indent=1))
    for k, c in cells.items():
        op = c["operating_point"]
        print(f"{k:28} t={op['threshold']:.4f} FP@100%={op['false_handoffs']}/{op['negatives']} "
              f"margin={op['margin']:+.3f} argmax FP={len(c['argmax_false'])} miss={len(c['argmax_missed'])} "
              f"low+={c['lowest_positive']} med={c['median_elapsed_s']*1000:.0f}ms"
              + (f" mw={c['multiway']['argmax_correct']}/{c['multiway']['n']}" if 'multiway' in c else ""))
        if op["false_handoffs"]:
            print("   FP:", ", ".join(c["false_at_op"]))
    print("selected framing:", chosen, result["frozen_thresholds"])


# --- held-out --------------------------------------------------------------

def say_wav(text: str, path: Path, voice: str) -> None:
    """Independent synthesizer: macOS say -> 16 kHz PCM16 mono WAV."""
    aiff = path.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(path)], check=True)
    aiff.unlink()


def heldout_snapshot(case: dict) -> dict:
    s = case["snapshot"]
    return {"pending_handoffs": s.get("pending_handoffs", []), "instructions": s.get("instructions", []),
            "known_facts": s.get("known_facts", []),
            "ledger_prior": [(line["speaker"], line["text"]) for line in s.get("ledger", [])]}


def run_heldout(args):
    """Render audio (LFM female /tts and macOS say), ASR it, score once."""
    import httpx
    sys.path.insert(0, str(ROOT))
    from live_probe import wav_to_pcm16, save_wav
    cases = json.loads(Path(args.cases).read_text())["cases"]
    analysis = json.loads(Path(args.analysis).read_text())
    framing, thresholds = analysis["selected_framing"], analysis["frozen_thresholds"]
    out = Path(args.output)
    (out / "audio").mkdir(parents=True, exist_ok=False)
    records = []
    with httpx.Client(timeout=120) as client:
        audio = {}
        for case in cases:
            lfm = out / "audio" / f"{case['id']}-lfm-female.wav"
            r = client.post(BASE + "/tts", json={"input": case["caller"], "voice": "female",
                            "lang": "American English", "response_format": "wav", "seed": 0,
                            "n_predict": 512, "stream": False})
            r.raise_for_status()
            raw = out / "audio" / f"{case['id']}-lfm-female-24k.wav"
            raw.write_bytes(r.content)
            pcm, _ = wav_to_pcm16(raw, target_rate=16000)
            save_wav(lfm, pcm, 16000)
            say = out / "audio" / f"{case['id']}-say.wav"
            say_wav(case["caller"], say, args.say_voice)
            audio[case["id"]] = {"lfm_female": lfm, "macos_say": say}
        for synth in ("lfm_female", "macos_say"):
            for case in cases:
                path = audio[case["id"]][synth]
                start = time.perf_counter()
                r = client.post(BASE + "/v1/chat/completions", json={
                    "model": MODEL, "temperature": 0, "seed": 0, "max_tokens": 256, "stream": False,
                    "messages": [{"role": "system", "content": "Perform ASR in English."},
                                 {"role": "user", "content": [{"type": "input_audio", "input_audio": {
                                     "data": base64.b64encode(path.read_bytes()).decode(), "format": "wav"}}]}]})
                stt_elapsed = time.perf_counter() - start
                stt_text = r.json()["choices"][0]["message"]["content"].strip()
                snap = render_snapshot(heldout_snapshot(case))
                for arm in ARMS:
                    if arm == "oracle_text" and synth != "lfm_female":
                        continue  # text arm does not depend on the synthesizer
                    kwargs = ({"audio_path": path} if arm == "native_audio" else
                              {"caller_text": case["caller"] if arm == "oracle_text" else stt_text})
                    rec = score(client, framing, snap, **kwargs)
                    rec.update(arm=arm, synth=synth if arm != "oracle_text" else "none",
                               case_id=case["id"], handoff=case["handoff"], critical=case.get("critical", False),
                               category=case.get("category"), threshold=thresholds[arm],
                               stt_text=stt_text if arm == "lfm_stt" else None, stt_elapsed_s=stt_elapsed)
                    rec["route"] = rec["p_work"] is not None and rec["p_work"] >= thresholds[arm]
                    records.append(rec)
                    print(f"{synth:10} {arm:12} {case['id']:34} gold={int(case['handoff'])} route={int(rec['route'])} "
                          f"p={rec['p_work']}", flush=True)
    (out / "records.json").write_text(json.dumps(records, indent=1))
    summary = {}
    for key in dict.fromkeys((r["synth"], r["arm"]) for r in records):
        rows = [r for r in records if (r["synth"], r["arm"]) == key]
        summary["|".join(key)] = {
            "positives": sum(r["handoff"] for r in rows), "negatives": sum(not r["handoff"] for r in rows),
            "tp": sum(r["handoff"] and r["route"] for r in rows),
            "fp": sum(not r["handoff"] and r["route"] for r in rows),
            "missed": sorted(r["case_id"] for r in rows if r["handoff"] and not r["route"]),
            "missed_critical": sorted(r["case_id"] for r in rows if r["handoff"] and r["critical"] and not r["route"]),
            "false": sorted(r["case_id"] for r in rows if not r["handoff"] and r["route"])}
    (out / "summary.json").write_text(json.dumps({"framing": framing, "thresholds": thresholds, "cells": summary}, indent=1))
    for k, v in summary.items():
        print(k, f"recall {v['tp']}/{v['positives']} FP {v['fp']}/{v['negatives']}", "missed", v["missed"], "false", v["false"])


def parser():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    m = sub.add_parser("main")
    m.add_argument("--output", required=True)
    m.add_argument("--framings", nargs="+", default=list(FRAMINGS), choices=list(FRAMINGS))
    a = sub.add_parser("analyze")
    a.add_argument("--input", required=True)
    h = sub.add_parser("heldout")
    h.add_argument("--cases", default=str(ROOT / "heldout2_cases.json"))
    h.add_argument("--analysis", required=True)
    h.add_argument("--output", required=True)
    h.add_argument("--say-voice", default="Samantha")
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    {"main": run_main, "analyze": run_analyze, "heldout": run_heldout}[args.command](args)
