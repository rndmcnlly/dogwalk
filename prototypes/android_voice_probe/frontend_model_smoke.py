# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.11,<4"]
# ///
"""Opt-in cheap live-model test with FIXTURE prompt and FAKE downstream only.

OPENROUTER_API_KEY must be in the environment. No host provider config is read.
Run: uv run --script frontend_model_smoke.py --live-model
At most 3 requests, sequential, <=220 output tokens each. No automatic retries.
Default model list-price output ceiling $0.00099 plus small fixture input cost;
provider billing is authoritative, not a hard monetary cap. No Worker contacted.
Prints only scenario name, decision, timing and usage, never conversation or keys.
"""
import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

from aiohttp import ClientSession

from frontend_server import (
    DEFAULT_MODEL,
    FIXTURE_PROMPT,
    JSON_MAPPING,
    OpenRouterModel,
    SafeLog,
    Usage,
)


async def run(args):
    tools = Path(__file__).resolve().parent / ".tools"
    tools.mkdir(exist_ok=True)
    log = SafeLog(tools / f"frontend-model-smoke-{uuid.uuid4().hex}.jsonl")
    usage = Usage(log)
    scenarios = json.loads(Path(__file__).with_name("frontend_scenarios.json").read_text())
    instructions = args.instructions_file.read_text() if args.instructions_file else FIXTURE_PROMPT
    passed = 0
    try:
        async with ClientSession() as http:
            model = OpenRouterModel(http, os.environ["OPENROUTER_API_KEY"], args.model, usage)
            for scenario in scenarios[:args.max_calls]:
                name, expected = scenario.pop("name"), scenario.pop("expected_action")
                result = await model.decide([
                    {"role": "system", "content": instructions + JSON_MAPPING},
                    {"role": "user", "content": json.dumps(scenario)}])
                ok = result["action"] == expected
                passed += ok
                record = {"fixture_scenario": name, "passed": ok,
                          "action": result["action"], "usage": log.rows[-1]}
                if args.reply_preview:
                    record["synthetic_fixture_reply"] = result["text"]
                print(json.dumps(record))
        summary = usage.summary()
        # This script has no connected Android session, so not a connected-hour metric.
        summary.pop("connected_seconds")
        summary.pop("short_run_hourly_projection_usd")
        summary.pop("projection_label")
        log.write("fixture_model_smoke.summary", passed=passed, **summary)
        print(json.dumps({"mode": "LIVE model, " + ("SUPPLIED canonical prompt" if args.instructions_file
                          else "FIXTURE prompt") + ", NO downstream integration",
                          "passed": passed, **summary}))
        return 0 if passed == args.max_calls else 1
    finally:
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-model", action="store_true")
    parser.add_argument("--max-calls", type=int, choices=[1, 2, 3], default=3)
    parser.add_argument("--model", default=os.environ.get("FRONTEND_MODEL", DEFAULT_MODEL))
    parser.add_argument("--instructions-file", type=Path,
                        help="Use exported canonical Worker frontend instructions, not the short fixture prompt")
    parser.add_argument("--reply-preview", action="store_true",
                        help="Print generated replies to these synthetic fixtures for manual semantic review")
    args = parser.parse_args()
    if not args.live_model or not os.environ.get("OPENROUTER_API_KEY"):
        parser.error("explicit --live-model and OPENROUTER_API_KEY required")
    try:
        raise SystemExit(asyncio.run(run(args)))
    except Exception as exc:  # noqa: BLE001  # Never print transport exception bodies.
        # Never stringify provider/network exceptions, which can contain URLs/body.
        print(json.dumps({"failed": True, "error_class": type(exc).__name__}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
