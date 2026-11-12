"""CPU-only checks for the frozen evaluation contract, with no inference imports."""

import json
from pathlib import Path
import re


def validate_suite(suite, *, expected_points=40):
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(suite.get("schema_version") == 1, "schema_version must be 1")
    scenarios = suite.get("scenarios")
    require(isinstance(scenarios, list) and bool(scenarios), "scenarios must be nonempty")
    seen_scenarios = set()
    counts = {"scenarios": 0, "points": 0, "positive": 0, "negative": 0,
              "silence": 0, "caller_utterances": 0, "telephone_points": 0}
    for scenario in scenarios:
        sid = scenario.get("id")
        require(isinstance(sid, str) and re.fullmatch(r"[a-z][a-z0-9_]*", sid), "invalid scenario id")
        require(sid not in seen_scenarios, f"duplicate scenario {sid}")
        seen_scenarios.add(sid)
        steps = scenario.get("steps")
        require(isinstance(steps, list) and bool(steps), f"{sid}: steps must be nonempty")
        seen_steps, earlier_handoffs = set(), set()
        for step in steps:
            key = f"{sid}/{step.get('id')}"
            require(step.get("id") not in seen_steps, f"{key}: duplicate step")
            require(isinstance(step.get("id"), str) and re.fullmatch(r"[a-z][a-z0-9_]*", step["id"]),
                    f"{key}: invalid step id")
            seen_steps.add(step["id"])
            expect = step.get("expect", {})
            require(set(expect) == {"delegate", "silence", "required", "forbidden", "consistency"},
                    f"{key}: incomplete expectations")
            require(type(expect["delegate"]) is bool and type(expect["silence"]) is bool,
                    f"{key}: routing and silence must be booleans")
            for field in ("required", "forbidden", "consistency"):
                require(isinstance(expect[field], list) and all(isinstance(v, str) and v.strip()
                        for v in expect[field]), f"{key}: invalid {field}")
            require(not expect["silence"] or not expect["required"], f"{key}: silence requires speech")
            events = step.get("events")
            require(isinstance(events, list) and bool(events), f"{key}: no events")
            for event in events:
                require(event.get("kind") in {"caller", "brief", "commentary", "instructions",
                                               "backend_pending", "backend_result"}, f"{key}: invalid kind")
                require(isinstance(event.get("text"), str) and bool(event["text"].strip()),
                        f"{key}: empty event")
                if event["kind"] in {"backend_pending", "backend_result"}:
                    require("handoff" in event, f"{key}: backend event lacks Handoff")
                if "handoff" in event:
                    require(event["handoff"] in earlier_handoffs, f"{key}: unresolved earlier Handoff")
                counts["caller_utterances"] += event["kind"] == "caller"
            # A same-step reference is not an earlier observed Handoff.
            if expect["delegate"]:
                earlier_handoffs.add(step["id"])
            counts["points"] += 1
            counts["positive"] += expect["delegate"]
            counts["negative"] += not expect["delegate"]
            counts["silence"] += expect["silence"]
            counts["telephone_points"] += scenario.get("audio_condition") == "pstn_8khz"
        counts["scenarios"] += 1
    require(expected_points is None or counts["points"] == expected_points,
            f"expected {expected_points} points, found {counts['points']}")
    return counts


if __name__ == "__main__":
    print(json.dumps(validate_suite(json.loads(Path(__file__).with_name("scenarios.json").read_text())),
                     indent=2))
