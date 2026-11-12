"""Rebuild the small public aggregate from ignored synthetic experiment records.

No model imports or inference. Historical, superseded diagnostics are excluded
from the matched comparison explicitly, never deleted from the artifact store.
"""
import argparse
import json
from pathlib import Path

from analyze import quantiles, validate_judgment


def cancelled_audio_tail_seconds(rows):
    cancel_sequence = next(r["sequence"] for r in rows if r["direction"] == "send" and r["event"]["type"] == "response.cancel")
    done_sequence = next(r["sequence"] for r in rows if r["sequence"] > cancel_sequence and
                         r["direction"] == "receive" and r["event"]["type"] == "response.done")
    tail = sum(r["event"]["delta"]["bytes"] for r in rows if cancel_sequence < r["sequence"] < done_sequence and
               r["direction"] == "receive" and r["event"]["type"] == "response.output_audio.delta")
    return tail/48000


def arm(path, repetition=None):
    reports = json.loads(path.read_text())["reports"]
    steps = [s for r in reports if repetition is None or r["repeat"] == repetition for s in r["steps"]]
    positive = [s for s in steps if s["expected"]["delegate"]]
    negative = [s for s in steps if not s["expected"]["delegate"]]
    valid_calls = lambda s: [c for c in s.get("response", {}).get("calls", []) if c["valid"] and c.get("dispatchable", True)]
    first_pcm, generation_first_pcm, rtf = [], [], []
    for step in steps:
        response = step.get("response", {})
        if "first_audio_s" in response:
            first_pcm.append(response["first_audio_s"])
            generation_first_pcm.append(response["first_audio_s"] - response.get("input_ready_s", 0))
        if response.get("native_audio_duration_s"):
            rtf.append(response["response_generation_s"] / response["native_audio_duration_s"])
    return {"points": len(steps), "positive": len(positive), "negative": len(negative),
            "routing_pass": sum(s.get("routing_pass", False) for s in steps),
            "missed_handoff": sum(not valid_calls(s) for s in positive),
            "false_handoff_points": sum(bool(valid_calls(s)) for s in negative),
            "duplicate_calls": sum(max(0, len(valid_calls(s))-1) for s in steps),
            "malformed_calls": sum(not c["valid"] for s in steps for c in s.get("response", {}).get("calls", [])),
            "transport_errors": sum(s.get("status") != "ok" for s in steps),
            "raw_silence_violations": sum(bool(s["expected"]["silence"] and s.get("raw_predicted_text", "").strip()) for s in steps),
            "missing_holding_ack": sum(s.get("holding_ack_missing", False) for s in steps),
            "complete_response_s": quantiles([s["decision_s"] for s in steps if "decision_s" in s]),
            "first_pcm_including_input_replay_s": quantiles(first_pcm),
            "response_request_to_first_pcm_s": quantiles(generation_first_pcm),
            "native_generation_realtime_factor": quantiles(rtf)}


def summarize(root):
    result = {"schema_version": 1, "date": "2026-11-12", "verdict": "blocked_by_boundary_failures",
              "scope": "Synthetic offline local feasibility screen, no production tasks or real calls.",
              "model": "LiquidAI/LFM3-Audio-2B-GGUF Q8_0", "runtime": "llama.cpp b12110-cd959bae9",
              "hardware": "Apple M3 Max 40 GPU cores, 64 GiB unified memory",
              "primary": [], "supplementary": {},
              "excluded_diagnostic": {"source": "matrix-native-v1/reconstructed-native_audio-r1.json",
                  "points": 40, "reason": "Current caller audio could be incorrectly labelled historical during reconstruction. Replaced by a corrected matched run."},
              "caveats": ["Main matrices retain historical raw-WAV ASR; explicit 16 kHz preprocessing was tested separately and used for repetitions.",
                  "Native conversation has no male voice selector; staged male TTS is host emulation.",
                  "First PCM is not first useful speech or actual acoustic playout.",
                  "No real backend coding tasks, carrier calls, permissions, deployments, or engineering side effects.",
                  "Independent ASR and judge labels do not establish acoustic truth or production reliability."]}
    for strategy in ("user_envelope", "tool_envelope", "reconstructed"):
        for mode in ("native_audio", "oracle_text", "lfm_stt"):
            name = f"{strategy}-{mode}-r1.json"
            baseline = "matrix-native-v1-corrected" if (strategy, mode) == ("reconstructed", "native_audio") else "matrix-native-v1"
            result["primary"].append({"strategy": strategy, "input_mode": mode,
                "v1": arm(root / baseline / name), "v2": arm(root / "matrix-native-v2" / name)})
    reps = root / "repetitions-v2-16k/tool_envelope-native_audio-r3.json"
    result["supplementary"]["normalized_native_repetitions"] = {
        "strategy": "tool_envelope", "policy": "v2", "aggregate": arm(reps),
        "repetitions": [arm(reps, i) for i in range(3)]}
    result["supplementary"]["heldout_negatives"] = {mode: arm(root / "heldout-v2" / f"tool_envelope-{mode}-r1.json")
                                                    for mode in ("native_audio", "oracle_text", "lfm_stt")}
    result["supplementary"]["paired_frontend"] = {
        condition: {mode: arm(root / folder / f"tool_envelope-{mode}-r1.json")
                    for mode in ("native_audio", "oracle_text", "lfm_stt")}
        for condition, folder in (("clean", "paired-clean-v2-16k"), ("pstn_8khz", "paired-narrow-v2-16k"))}
    result["supplementary"]["http_text_smoke"] = [{"arm": path.stem, **arm(path)}
                                                   for path in sorted((root / "http-text-smoke-v2").glob("*-r1.json"))]
    result["cancellation"] = {}
    for phase in ("cancel-isolated", "cancel-overlap"):
        raw = json.loads((root / phase / "report.json").read_text())
        result["cancellation"][phase] = {k: v for k, v in raw.items() if k != "audio_artifacts"}
        rows = [json.loads(line) for line in (root / phase / "events.jsonl").read_text().splitlines()]
        result["cancellation"][phase]["received_audio_tail_s"] = cancelled_audio_tail_seconds(rows)
    result["paired_asr"] = [{"truth": r["truth"], "condition": r["condition"],
        "explicit_16khz_normalization": r["explicit_16khz_normalization"],
        "recognized": r["response"]["predicted_text"], "elapsed_s": r["response"]["elapsed_s"]}
        for r in json.loads((root / "asr-paired/summary.json").read_text())]
    asr = json.loads((root / "parakeet-final/summary.json").read_text())
    result["waveform_review"] = {"files": len(asr["files"]), "errors": asr["errors"],
                                 "method": "Parakeet v3 via parakeet-mlx 0.7.0, waveform-relative word timestamps; not listening"}
    judging = []
    for directory in ("analysis-v1", "analysis-v2", "judge-followups"):
        for path in sorted((root / directory).glob("*.judge.json")):
            packet = json.loads(path.with_name(path.name.replace(".judge.json", ".json")).read_text())
            raw = json.loads(path.read_text())
            judgment = validate_judgment(packet, raw["answer"])
            records = judgment["records"]
            judging.append({"packet": packet["packet_id"], "group": directory, "model": raw["model"],
                            "records": len(records), "semantic_pass": sum(r["semantics"] == "pass" for r in records),
                            "semantic_fail": sum(r["semantics"] == "fail" for r in records),
                            "semantic_unresolved": sum(r["semantics"] == "unresolved" for r in records)})
    result["judging"] = {"records": sum(j["records"] for j in judging), "packets": judging,
                          "interpretation": "Record-level tallies, not potentially inaccurate free-text judge summary counts. Includes sampled revised arms, not every primary semantic predicate."}
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    p.add_argument("--artifacts", type=Path, default=here / "artifacts")
    p.add_argument("--output", type=Path, default=here / "RESULTS.json")
    args = p.parse_args()
    data = summarize(args.artifacts)
    args.output.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "primary_points": sum(r[v]["points"] for r in data["primary"] for v in ("v1", "v2")),
                      "judged_records": data["judging"]["records"], "waveforms": data["waveform_review"]["files"]}))
