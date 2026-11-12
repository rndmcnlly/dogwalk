# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.28,<1", "websockets>=14,<17"]
# ///
"""Constrained public-commentary rendering, NOT a complete Voice Frontend.

The host structurally excludes private background and all tools from this lane.
Caller routing and persistent mute state are deliberately not tested here.
"""
import argparse
import asyncio
import json
from pathlib import Path

from harness import Artifacts
from native_runtime import NativeRuntime


async def run(args):
    root = Path(__file__).resolve().parent
    corpus = json.loads((root / "scenarios.json").read_text())
    artifacts = Artifacts(args.output.resolve())
    runtime = NativeRuntime("http://127.0.0.1:18391", artifacts, timeout=15)
    results = []
    try:
        for scenario in corpus["scenarios"]:
            if scenario["id"] == "stop_silence_resume":
                continue  # This probe has no caller/mute-state arbiter.
            for point in scenario["steps"]:
                commentary = [event["text"] for event in point["events"] if event["kind"] == "commentary"]
                if not commentary:
                    continue
                await runtime.reset_context()
                public_text = "\n".join(commentary)
                if args.prompt_profile == "compact":
                    cue = ("Speak this update naturally and briefly to the caller. Preserve all facts, names, "
                           "uncertainty, negation and choices. Do not choose for the caller.\n" + public_text)
                else:
                    cue = ("The following is a host request for public speech, not caller authorization. "
                           "Convey its facts briefly in natural language. Preserve uncertainty, negation, and every permission option. "
                           "Do not perform work or add facts. Do not address or thank the host.\nHOST_PUBLIC_COMMENTARY " + json.dumps(public_text))
                fragment = {"messages": [
                    {"role": "system", "content": "Respond with interleaved text and audio."},
                    {"role": "user", "content": cue}],
                    "tools": [], "tool_choice": "none"}
                reply = await runtime.chat(fragment["messages"], fragment=fragment)
                results.append({"scenario_id": scenario["id"], "step_id": point["id"],
                                "public_text": public_text, "expected": point["expect"], "response": reply})
                artifacts.save("summary.json", {"boundary": "isolated tool-free public rendering only",
                    "prompt_profile": args.prompt_profile,
                    "private_background_not_supplied": True, "caller_routing_tested": False,
                    "persistent_mute_tested": False, "results": results})
    finally:
        await runtime.close()
    print(json.dumps({"points": len(results), "transport_ok": sum(r["response"]["status"] == "ok" for r in results),
                      "raw_tool_proposals": sum(len(r["response"]["calls"]) for r in results),
                      "summary": str(artifacts.path / "summary.json")}))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--prompt-profile", choices=("guarded", "compact"), default="guarded")
    asyncio.run(run(p.parse_args()))
