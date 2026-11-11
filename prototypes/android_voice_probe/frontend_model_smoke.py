# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.11,<4"]
# ///
"""Opt-in cheap live-model test with FIXTURE prompt and FAKE downstream only.

OPENROUTER_API_KEY (default) or OMLX_API_KEY must be in the environment.
Local: --provider omlx --model ID_FROM_MODELS (loopback only, no API billing;
compute/power cost unknown). No host provider config is read or changed.
Run: uv run --script frontend_model_smoke.py --live-model
At most 3 cloud requests or 24 local requests, sequential, <=220 output tokens
each. No automatic retries.
Default model list-price output ceiling $0.00099 plus small fixture input cost;
provider billing is authoritative, not a hard monetary cap. No Worker contacted.
Prints only scenario name, decision, timing and usage, never conversation or keys.
"""
import argparse
import asyncio
import hashlib
import json
import uuid
from pathlib import Path

from aiohttp import ClientSession

from frontend_context import (
    COMPACT_PROMPT,
    PROFILES,
    build_messages,
    check_decision,
    scenario_state,
)
from frontend_server import (
    FIXTURE_PROMPT,
    JSON_MAPPING,
    SafeLog,
    Usage,
    add_provider_args,
    configure_provider,
    configured_model,
)


async def run(args):
    tools = Path(__file__).resolve().parent / ".tools"
    tools.mkdir(exist_ok=True)
    log = SafeLog(tools / f"frontend-model-smoke-{uuid.uuid4().hex}.jsonl")
    usage = Usage(log)
    scenarios = json.loads(args.scenarios_file.read_text())
    if len(scenarios) < args.max_calls:
        raise ValueError("fewer scenarios than requested calls")
    instructions = args.instructions_file.read_text() if args.instructions_file else FIXTURE_PROMPT
    passed = 0
    try:
        async with ClientSession() as http:
            model = configured_model(http, args, usage)
            for scenario in scenarios[:args.max_calls]:
                name, expected = scenario["name"], scenario["expected_action"]
                state = scenario_state(scenario)
                messages = build_messages(instructions, JSON_MAPPING, state, args.context_profile)
                try:
                    result = await model.decide(messages)
                except Exception as exc:  # noqa: BLE001
                    print(json.dumps({"fixture_scenario": name, "passed": False,
                                      "context_profile": args.context_profile,
                                      "error_class": type(exc).__name__, "usage": log.rows[-1]}))
                    continue
                failures = check_decision(result, scenario)
                ok = not failures
                passed += ok
                record = {"fixture_scenario": name, "passed": ok,
                          "routing_passed": result["action"] == expected,
                          "failures": failures,
                          "action": result["action"], "usage": log.rows[-1],
                          "context_profile": args.context_profile}
                if args.reply_preview:
                    record["synthetic_fixture_reply"] = result["text"]
                print(json.dumps(record))
        summary = usage.summary()
        # This script has no connected Android session, so not a connected-hour metric.
        summary.pop("connected_seconds")
        summary.pop("short_run_hourly_projection_usd")
        summary.pop("projection_label")
        log.write("fixture_model_smoke.summary", passed=passed, **summary)
        print(json.dumps({"mode": "LIVE model, synthetic fixtures, NO downstream integration",
                            "context_profile": args.context_profile,
                            "provider": args.provider, "model": args.model,
                            "instructions_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
                            "compact_prompt_sha256": hashlib.sha256(COMPACT_PROMPT.encode()).hexdigest()
                            if args.context_profile.startswith("compact") else None,
                            "scenarios_sha256": hashlib.sha256(args.scenarios_file.read_bytes()).hexdigest(),
                            "acceptance": "routing, output contract, and supplied speech patterns; manual review still required",
                           "passed": passed, **summary}))
        return 0 if passed == args.max_calls else 1
    finally:
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-model", action="store_true")
    parser.add_argument("--max-calls", type=int, choices=range(1, 25), default=3,
                        help="Up to 3 cloud calls or 24 local calls, no retries")
    add_provider_args(parser)
    parser.add_argument("--context-profile", choices=PROFILES, default="canonical")
    parser.add_argument("--scenarios-file", type=Path,
                        default=Path(__file__).with_name("frontend_scenarios.json"))
    parser.add_argument("--instructions-file", type=Path,
                        help="Use exported canonical Worker frontend instructions, not the short fixture prompt")
    parser.add_argument("--reply-preview", action="store_true",
                        help="Print generated replies to these synthetic fixtures for manual semantic review")
    args = parser.parse_args()
    if not args.live_model:
        parser.error("explicit --live-model required")
    if args.provider != "omlx" and args.max_calls > 3:
        parser.error("more than 3 calls is enabled only for local oMLX")
    configure_provider(parser, args)
    try:
        raise SystemExit(asyncio.run(run(args)))
    except Exception as exc:  # noqa: BLE001  # Never print transport exception bodies.
        # Never stringify provider/network exceptions, which can contain URLs/body.
        print(json.dumps({"failed": True, "error_class": type(exc).__name__}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
