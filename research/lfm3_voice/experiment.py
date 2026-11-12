# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.28,<1", "websockets>=14,<17"]
# ///
"""Serial, matched staged-speech bakeoff; exclusive GPU lease is caller-owned.

Caller TTS is cached once across all arms in the same artifact directory. Native
audio means input only here: output is explicitly text then same-model male TTS.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

import harness
import strategies
from native_runtime import NativeRuntime
from policies import POLICIES
from validate_suite import validate_suite


async def run(args):
    root = Path(__file__).resolve().parent
    corpus = json.loads(args.suite.read_text())
    counts = validate_suite(corpus, expected_points=None)
    scenarios = corpus["scenarios"]
    if args.scenario:
        scenarios = [s for s in scenarios if s["id"] in args.scenario]
        if len(scenarios) != len(set(args.scenario)):
            raise ValueError("Unknown or duplicate scenario selection")
    if args.paired_clean:
        scenarios = json.loads(json.dumps(scenarios))
        for scenario in scenarios:
            scenario["audio_condition"] = "clean"
    if args.transport == "http_text" and "native_audio" in args.modes:
        raise ValueError("http_text is an explicitly STT/oracle-mediated alternative, not native audio")
    if args.repeat < 1:
        raise ValueError("repeat must be positive")
    if (args.output / "experiment-provenance.json").exists():
        raise ValueError("Use a fresh output directory; do not overwrite experiment evidence")
    artifacts = harness.Artifacts(args.output.resolve())
    manifest = json.loads((root / "artifacts/model-manifest.json").read_text())
    props = json.loads((root / "artifacts/props.json").read_text())
    fingerprint = harness.digest({"manifest": manifest, "runtime_props": props})
    runtime_class = NativeRuntime if args.transport == "realtime" else harness.Runtime
    runtime = runtime_class(args.base_url, artifacts, timeout=args.timeout, fingerprint=fingerprint)
    runtime.text_only_output = args.transport in {"http_suppressed", "http_text"}
    runtime.text_recipe = args.transport == "http_text"
    runtime.defer_synthesis = args.transport == "realtime"
    runtime.policy_extra = POLICIES[args.policy]
    runtime.raw_asr = args.asr_input_mode == "raw_wav"
    server_sampling = props.get("default_generation_settings", {}).get("params", {})
    provenance = {"model_fingerprint": fingerprint, "sampling": (
                    {"temperature": server_sampling.get("temperature"), "seed": server_sampling.get("seed"),
                     "max_output_tokens": 512, "source": "server defaults; realtime overrides unavailable",
                     "audio_codec_sampling": "not exposed by realtime metadata"} if args.transport == "realtime"
                    else {"temperature": 0, "seed": 0, "max_tokens": 512}),
                  "output": args.transport + "_then_staged_male_tts",
                  "logit_bias": {"125012": -100, "125015": -100} if runtime.text_only_output else None,
                  "realtime_sampling": "server defaults; seed/temperature not exposed" if args.transport == "realtime" else None,
                  "corpus_counts": counts,
                  "suite_sha256": hashlib.sha256(args.suite.read_bytes()).hexdigest(),
                  "source_hashes": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                    for name in ("harness.py", "strategies.py", "experiment.py", "native_runtime.py")},
                  "conditions": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                  "all_tasks_simulated": True}
    artifacts.save("experiment-provenance.json", provenance)
    arms = []
    try:
        for token, expected in (("<|audio_start|>", [125012]), ("<|mixed_start|>", [125015])):
            check = await runtime.request("/tokenize", {"content": token, "add_special": False})
            if (check.get("body") or {}).get("tokens") != expected:
                raise ValueError("Pinned mode-switch token IDs do not match runtime")
        # A one-slot realtime connection retains the slot while idle. Prepare all
        # caller waveforms and ASR before opening one, then stage output afterward.
        runtime.prepared_callers = {}
        frozen = json.loads(args.frozen_callers.read_text()) if args.frozen_callers else None
        for scenario in scenarios:
            for point in scenario["steps"]:
                for event in point["events"]:
                    if event["kind"] != "caller":
                        continue
                    condition = scenario.get("audio_condition", "clean")
                    key = harness.digest({"text": event["text"], "condition": condition})
                    if key in runtime.prepared_callers:
                        continue
                    if frozen is not None:
                        derived_clean = False
                        if key not in frozen and args.paired_clean:
                            source = next((r for r in frozen.values() if r["synthesis"].get("text") == event["text"]), None)
                            if source is None:
                                raise ValueError("Paired clean source not found in frozen callers")
                            record = {"synthesis": source["synthesis"],
                                      "delivered_audio_path": source["synthesis"]["audio_path"],
                                      "paired_from_sha256": source.get("delivered_sha256")}
                            derived_clean = True
                        elif key not in frozen:
                            raise ValueError("Frozen caller manifest does not contain a selected fixture/condition")
                        else:
                            record = json.loads(json.dumps(frozen[key]))
                        if record["synthesis"].get("fingerprint") != fingerprint:
                            raise ValueError("Frozen caller model/runtime fingerprint mismatch")
                        if record["synthesis"].get("text") != event["text"]:
                            raise ValueError("Frozen caller text mismatch")
                        path = Path(record["delivered_audio_path"])
                        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                        if record.get("delivered_sha256", actual_hash) != actual_hash:
                            raise ValueError("Frozen caller waveform hash mismatch")
                        record["delivered_sha256"] = actual_hash
                        if args.refresh_asr or derived_clean:
                            record["previous_asr_artifact_prefix"] = (record.get("stt") or {}).get("prefix")
                            record["stt"] = await runtime.stt(path)
                        runtime.prepared_callers[key] = record
                        continue
                    synthesis = await runtime.tts(event["text"], "female")
                    if synthesis["status"] != "ok":
                        raise ValueError("Caller synthesis failed before candidate testing")
                    record = {"synthesis": synthesis, "delivered_audio_path": synthesis["audio_path"]}
                    if condition == "pstn_8khz":
                        delivered, record["preprocessing"] = harness.narrowband(Path(synthesis["audio_path"]), artifacts)
                        record["delivered_audio_path"] = str(delivered)
                    record["stt"] = await runtime.stt(Path(record["delivered_audio_path"]))
                    record["delivered_sha256"] = hashlib.sha256(Path(record["delivered_audio_path"]).read_bytes()).hexdigest()
                    runtime.prepared_callers[key] = record
        artifacts.save("frozen-callers.json", runtime.prepared_callers)
        for strategy in args.strategies:
            runtime.reconstruct = strategy == "reconstructed"
            for mode in args.modes:
                reports = await harness.replay(runtime, scenarios, strategy, input_mode=mode,
                                               repeat=args.repeat, max_events=args.max_events)
                if runtime.defer_synthesis:
                    await runtime.reset_context()
                    for report in reports:
                        for step in report["steps"]:
                            if step.get("assistant_synthesis", {}).get("status") != "deferred":
                                continue
                            synthesis = await runtime.tts(step["synthesis_script"], "male")
                            step["assistant_synthesis"] = synthesis
                            step["staged_tts_scheduling"] = "after native scenario batch closed; not measured online caller latency"
                            step["estimated_serial_staged_audio_s"] = step["decision_s"] + synthesis.get("original_synthesis_elapsed_s", synthesis["elapsed_s"])
                summary = harness.summarize(reports)
                name = f"{strategy}-{mode}-r{args.repeat}"
                artifacts.save(name + ".json", summary)
                compact = {k: v for k, v in summary.items() if k != "reports"}
                compact.update(strategy=strategy, input_mode=mode, summary_file=name + ".json")
                arms.append(compact)
                artifacts.save("experiment-summary.json", arms)
                print(json.dumps(compact), flush=True)
    finally:
        await runtime.close()
    return arms


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    p.add_argument("--suite", type=Path, default=root / "scenarios.json")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--base-url", default="http://127.0.0.1:18391")
    p.add_argument("--timeout", type=float, default=30)
    p.add_argument("--strategies", nargs="+", choices=strategies.STRATEGIES, default=list(strategies.STRATEGIES))
    p.add_argument("--modes", nargs="+", choices=strategies.INPUT_MODES, default=list(strategies.INPUT_MODES))
    p.add_argument("--scenario", action="append")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--max-events", type=int, default=24)
    p.add_argument("--paired-clean", action="store_true")
    p.add_argument("--transport", choices=("realtime", "http_suppressed", "http_text"), default="realtime")
    p.add_argument("--policy", choices=POLICIES, default="v1")
    p.add_argument("--frozen-callers", type=Path, help="Reuse exact caller waveforms and LFM ASR from an earlier experiment")
    p.add_argument("--refresh-asr", action="store_true", help="Keep frozen waveforms, rerun ASR with the selected preprocessing")
    p.add_argument("--asr-input-mode", choices=("raw_wav", "normalized_16k"), default="normalized_16k",
                   help="Fresh ASR preprocessing; frozen ASR is retained unless --refresh-asr")
    return p


if __name__ == "__main__":
    asyncio.run(run(parser().parse_args()))
