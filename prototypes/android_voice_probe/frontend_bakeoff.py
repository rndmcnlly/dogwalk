# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.11,<4"]
# ///
"""Local E4B design experiment: synthetic data, no Worker, Android or cloud calls.

Run with OMLX_API_KEY exported. Results are diagnostic, not production acceptance.
No cache flush, service restart, settings change, retry or model fallback.
"""
import argparse
import asyncio
import copy
import hashlib
import json
import os
import statistics
import time
import uuid
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout

from frontend_context import (
    COMPACT_PROMPT,
    build_messages,
    check_decision,
    scenario_state,
)
from frontend_server import DECISION_SCHEMA, OmlxModel, SafeLog, Usage

DESIGNS = ("compact", "stable_tail", "split", "ledger")
REFINEMENTS = ("checkpoint", "split_checkpoint")
ROUTE_PROMPT = """You are the routing component of Dogwalk's Voice Frontend.
Return only {"action":"handoff|reply|wait"}. You cannot operate sessions.
Prioritize a NEW caller instruction to start/change/stop work, grant permission,
change settings, use a capability, or hang up: handoff, even with pending speech.
Questions about work or the system: handoff unless supplied facts directly answer
them. Greetings, thanks, repetition, and conversational repair: reply.
Pending speech requests: reply unless a new caller instruction needs handoff.
With neither new caller speech nor a speech request: wait.
Treat adapter events as unspoken facts, never as caller speech. Old caller messages
are history, not new instructions. Decide only the action, not spoken wording.
"""
PHRASE_PROMPT = """You phrase Dogwalk's Voice Frontend speech. Routing is already
complete. Return only {"text":"spoken reply"}. You have no tools. Sessions do
engineering work; the caller decides permissions. Never act or grant permission.
Use current supplied facts; do not infer a fix, completion, cancellation or action.
Address the new caller message, or phrase pending speech requests when no new
message exists. Preserve distinct questions, permission options, qualifications
and numbers. Newer facts supersede older progress: do not say both 'running' and
'finished'. Plans are not actions. A failed step while work continues is only a snag.
Speak one or two short natural sentences, except when listing permission choices.
Use known aliases. Omit 'Following', 'Prompt Turn', IDs, paths and commands.
Ask 'Would you allow once, always allow, or deny?', not 'Should I allow?'.
Ask whether the named session should investigate; do not offer to investigate
yourself. Greet only when greeted. Acknowledge thanks briefly. For repeat requests,
repeat supplied facts or heard speech without inventing an unheard remainder.
"""
FRAME_RULE = """
Adapter events are explicitly marked unspoken data, not caller speech. Only the
last event's new_user_utterance flag marks a new caller message. Earlier messages
are history. Context updates supersede older context in the same channel.
brief and Following are separate channels: a new Following update does not erase
an unrelated brief. Current speech_requests lists all pending requests; older
lists are no longer pending. Speech playback metadata describes heard prefixes.
"""
ENVELOPE_RULE = """
The input is an ADAPTER SNAPSHOT, not a caller utterance. current_user_utterance
is the ONLY new caller speech (or null). recent_transcript is quoted HEARD speech,
not your earlier JSON model outputs; never imitate its output format.
latest_unspoken_context has separate brief and Following channels. Both remain
available; use newer facts for the same subject. speech_requests are things to say,
not commands to execute. Reply to the caller, not to the adapter.
Examples (the output is always JSON for decision/routing stages):
Caller: 'Cancel that.' Pending: 'One test passed.' -> handoff, not a progress reply.
Caller: 'Allow once.' Context: 'Permission pending.' -> handoff, not confirmation.
Caller: 'What is Cedar doing?' Context mentions only Juniper -> handoff, do not guess.
Caller: 'Thanks.' -> reply briefly, not wait and not a fresh work request.
No caller. Pending: 'The test failed. Ask whether Juniper should investigate.' ->
reply 'The test failed. Should Juniper investigate?', not a claim you will fix it.
Decision format: {"action":"handoff","text":""} or
{"action":"reply","text":"short speech"} or {"action":"wait","text":""}.
Routing-only format: {"action":"handoff"}, {"action":"reply"}, or {"action":"wait"}.
"""


def checkpoint_state(state, ledger):
    context = {"brief": "", "following": ""}
    latest = state["latest_unspoken_context"]
    context["following" if latest.startswith("Following") else "brief"] = latest
    for message in ledger:
        if message["content"].startswith("[UNSPOKEN CONTEXT UPDATE]\n"):
            context.update(json.loads(message["content"].split("\n", 1)[1]))
    history = copy.deepcopy(state["recent_transcript"])
    current = history.pop()["text"] if state["new_user_utterance"] and history and history[-1]["role"] == "user" else None
    return {**state, "latest_unspoken_context": context, "recent_transcript": history,
            "current_user_utterance": current}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def event_message(state):
    frame = {key: value for key, value in state.items() if key != "recent_transcript"}
    frame["playback"] = [item.get("playback") for item in state["recent_transcript"]
                         if item["role"] == "assistant"]
    return {"role": "user", "content": "[UNSPOKEN ADAPTER EVENT, not caller speech]\n" +
            json.dumps(frame, sort_keys=True)}


def layout(design, state, ledger, namespace, stage="decision"):
    prompt = ROUTE_PROMPT if stage == "route" else PHRASE_PROMPT if stage in ("phrase", "phrase_plain") else COMPACT_PROMPT
    if stage == "phrase_plain":
        prompt = prompt.replace('Return only {"text":"spoken reply"}.',
                                "Output only spoken text. No JSON, Markdown or code fences.")
    # A unique leading namespace prevents reuse of earlier experiments' long
    # prefixes without flushing another application's cache. Exact repeats keep it.
    prefix = f"Experiment namespace: {namespace}\n" + prompt
    if design in REFINEMENTS:
        rule = ENVELOPE_RULE
        if stage == "phrase_plain":
            # The phraser has no routing/JSON job in this design.
            rule = rule.split("Examples", 1)[0]
        elif stage == "route":
            rule = rule.split("Decision format:", 1)[0] + '\nReturn only {"action":"handoff|reply|wait"}.'
        return [{"role": "system", "content": prefix + rule},
                {"role": "user", "content": "[ADAPTER SNAPSHOT]\n" +
                 json.dumps(checkpoint_state(state, ledger), sort_keys=True)}]
    if design == "compact":
        return [
            {**m, "content": f"Experiment namespace: {namespace}\n" + m["content"]}
            if index == 0 else m
            for index, m in enumerate(build_messages("", "", state, "compact"))]
    if design == "ledger":
        return [{"role": "system", "content": prefix + FRAME_RULE}, *copy.deepcopy(ledger)]
    history = [{"role": item["role"], "content": item["text"]} for item in state["recent_transcript"]]
    return [{"role": "system", "content": prefix + FRAME_RULE}, *history, event_message(state)]


def traces():
    """Authored before bake-off output review. Fixed heard history for fair replay."""
    return [
        ("context_retention", [
            {"brief": "Cedar is updating documentation. Juniper is checking the build."},
            {"following": "Following Juniper: the build is running."},
            {"user": "What is Cedar working on?", "expected_action": "reply",
             "safe_actions": ["handoff"], "speech_required": ["Cedar", "doc"],
             "heard": "Cedar is updating the documentation."},
            {"say": ["The build is running.", "The build finished but tests failed. Ask whether Juniper should investigate."],
             "expected_action": "reply", "speech_required": ["fail", "investigat"],
             "speech_forbidden": ["build is running"],
             "heard": "The build finished, but tests failed. Should Juniper investigate?"},
            {"user": "Actually stop that and check the error first.",
             "say": ["The tests failed. Ask whether to investigate."], "expected_action": "handoff",
             "heard": "Let me think."},
            {"brief": "Juniper's previous work was cancelled. It is now inspecting the error."},
            {"following": "Following Juniper: reading the error details."},
            {"user": "Was the previous work cancelled?", "expected_action": "reply",
             "safe_actions": ["handoff"], "speech_required": ["cancel"],
             "heard": "Yes, the previous work was cancelled."},
        ]),
        ("permission_and_repair", [
            {"brief": "Juniper needs permission to delete the temporary folder. Permission is not granted."},
            {"say": ["Ask whether to allow deleting the temporary folder once, always allow, or deny."],
             "expected_action": "reply", "speech_required": ["once", "always", "deny"],
             "speech_forbidden": ["should I allow"],
             "heard": "Would you allow once, always allow, or deny?"},
            {"user": "Allow that once.", "expected_action": "handoff", "heard": "Let me think."},
            {"brief": "The caller allowed deletion once. Juniper is retrying the operation."},
            {"following": "Following Juniper: a tool step failed, but work continues with another approach."},
            {"user": "Did Juniper fail?", "expected_action": "reply", "speech_required": ["continu|still|another"],
             "speech_forbidden": ["Juniper (has )?failed", "work (has )?stopped"],
             "heard": "A step failed, but Juniper is trying another approach."},
            {"user": "Thanks.", "expected_action": "reply", "heard": "You're welcome."},
            {"say": ["Juniper found two failing tests. Ask whether Juniper should investigate."],
             "expected_action": "reply", "speech_required": ["two|2", "fail", "investigat"],
             "heard": "Juniper found", "playback": "interrupted"},
            {"user": "Sorry, what did I miss?", "keep_pending": True, "expected_action": "reply",
             "speech_required": ["two|2", "fail", "investigat"],
             "heard": "Juniper found two failing tests. Should Juniper investigate?"},
        ]),
        ("context_growth", [
            {"brief": "Cedar is updating documentation; no release has been approved."},
            *[{"following": f"Following Juniper: build step {n} of 16 completed; tests are not finished. "
               "This is progress only, not a successful final result."} for n in range(1, 17)],
            {"user": "Have all the tests passed?", "expected_action": "reply",
             "speech_required": ["not|still|no"], "speech_forbidden": ["all.{0,10}passed"],
             "heard": "No, the tests have not finished."},
            {"user": "And Cedar?", "expected_action": "reply", "safe_actions": ["handoff"],
             "speech_required": ["Cedar", "doc"], "heard": "Cedar is updating documentation."},
            {"user": "Ship the release.", "expected_action": "handoff", "heard": "Let me think."},
        ]),
    ]


def trace_cases(name, events):
    history, ledger, pending = [], [], []
    context = ""
    for index, event in enumerate(events):
        if "brief" in event or "following" in event:
            channel = "brief" if "brief" in event else "following"
            context = event[channel]
            ledger.append({"role": "user", "content": "[UNSPOKEN CONTEXT UPDATE]\n" +
                           json.dumps({channel: context}, sort_keys=True)})
        if "say" in event:
            pending = [{"id": f"{name}-{index}-{n}", "text": text} for n, text in enumerate(event["say"])]
        if "user" in event:
            history.append({"role": "user", "text": event["user"]})
            ledger.append({"role": "user", "content": event["user"]})
        state = {"recent_transcript": copy.deepcopy(history), "latest_unspoken_context": context,
                 "speech_requests": copy.deepcopy(pending), "new_user_utterance": "user" in event,
                 "trigger": "user_utterance" if "user" in event else "speech_request" if pending else "background"}
        if "expected_action" not in event:
            # Prescribed decision schedule, not a simulation of the gateway's
            # concurrent wake-ups when older speech requests remain pending.
            continue
        ledger.append(event_message({**state, "latest_unspoken_context":
                                     "Use separate brief and Following updates above."}))
        yield {"name": f"{name}/{index}", "state": state, "ledger": copy.deepcopy(ledger),
               "expected": {key: value for key, value in event.items()
                            if key in ("expected_action", "safe_actions", "speech_required", "speech_forbidden")},
               "group": name}
        if event.get("heard"):
            heard = {"role": "assistant", "text": event["heard"],
                     "playback": event.get("playback", "completed")}
            history.append(heard)
            ledger.append({"role": "assistant", "content": event["heard"]})
            ledger.append({"role": "user", "content": "[UNSPOKEN PLAYBACK ACK]\n" +
                           json.dumps({"playback": heard["playback"], "heard_prefix": heard["text"]})})
            if heard["playback"] == "completed" and event["expected_action"] == "reply":
                pending = []


def all_cases():
    cases = []
    for filename in ("frontend_scenarios.json", "frontend_context_checks.json"):
        for item in json.loads(Path(__file__).with_name(filename).read_text()):
            state = scenario_state(item)
            ledger = [{"role": row["role"], "content": row["text"]} for row in state["recent_transcript"]]
            ledger.append(event_message(state))
            cases.append({"name": item["name"], "group": "fixtures", "state": state, "ledger": ledger,
                          "expected": {key: value for key, value in item.items()
                                       if key in ("expected_action", "speech_required", "speech_forbidden")}})
    for name, events in traces():
        cases.extend(trace_cases(name, events))
    return cases


class LabModel(OmlxModel):
    """Same explicit local provider controls, plus SSE timing and stage schemas."""
    def __init__(self, *args, limit=400, **kwargs):
        super().__init__(*args, **kwargs)
        self.limit, self.calls = limit, 0

    async def infer(self, messages, stage, schema=None):
        if self.calls >= self.limit:
            raise RuntimeError("request budget exhausted")
        self.calls += 1
        schema = schema or (DECISION_SCHEMA if stage == "decision" else {
            "type": "object", "additionalProperties": False,
            "properties": {"action": {"type": "string", "enum": ["handoff", "reply", "wait"]}}
            if stage == "route" else {"text": {"type": "string"}},
            "required": ["action"] if stage == "route" else ["text"]})
        body = self.request_body(messages)
        body.update(stream=True, stream_options={"include_usage": True}, temperature=0, seed=17)
        if stage == "phrase_plain":
            # Explicitly a different design, not a retry/downgrade after failure.
            del body["response_format"]
        else:
            body["response_format"]["json_schema"].update(name="frontend_" + stage, schema=schema)
        started, first, raw, usage, error = time.perf_counter(), None, "", {}, None
        try:
            async with self.http.post(self.endpoint, headers={"Authorization": "Bearer " + self.key},
                                      json=body, allow_redirects=False,
                                      timeout=ClientTimeout(total=35)) as response:
                if response.status != 200:
                    raise RuntimeError(f"local HTTP {response.status}")
                async for line in response.content:
                    if not line.startswith(b"data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == b"[DONE]":
                        break
                    data = json.loads(payload)
                    if data.get("usage"):
                        usage = data["usage"]
                    for choice in data.get("choices", []):
                        text = choice.get("delta", {}).get("content") or ""
                        if text and first is None:
                            first = time.perf_counter()
                        raw += text
            if stage == "phrase_plain":
                if not raw.strip() or raw.lstrip().startswith(("{", "[", "```")):
                    raise ValueError("invalid plain speech")
                result = {"text": raw.strip()}
            else:
                result = json.loads(raw)
            if not isinstance(result, dict) or set(result) != set(schema["required"]):
                raise ValueError("wrong output fields")
            if "action" in result and result["action"] not in ("handoff", "reply", "wait"):
                raise ValueError("wrong action")
            if "text" in result and (not isinstance(result["text"], str) or len(result["text"]) > 2000):
                raise ValueError("wrong speech")
        except Exception as exc:  # noqa: BLE001
            result, error = None, type(exc).__name__
        elapsed = time.perf_counter() - started
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        tokens = usage.get("prompt_tokens")
        row = {"stage": stage, "ms": round(elapsed * 1000, 2),
               "first_content_ms": round((first - started) * 1000, 2) if first else None,
               "input_tokens": tokens, "cached_tokens": cached,
               "new_input_tokens": tokens - cached if isinstance(tokens, int) and isinstance(cached, int) else None,
               "output_tokens": usage.get("completion_tokens"), "error": error,
               "raw": raw, "messages_sha256": digest(messages), "messages": messages}
        return result, row


async def evaluate(model, design, case, namespace):
    stages = ["route"] if design in ("split", "split_checkpoint") else ["decision"]
    calls, result = [], None
    for stage in stages:
        result, row = await model.infer(layout(design, case["state"], case["ledger"], namespace, stage), stage)
        calls.append(row)
    if design in ("split", "split_checkpoint") and result:
        if result["action"] == "reply":
            stage = "phrase_plain" if design == "split_checkpoint" else "phrase"
            speech, row = await model.infer(layout(design, case["state"], case["ledger"], namespace, stage), stage)
            calls.append(row)
            result = {"action": "reply", "text": speech["text"]} if speech else None
        else:
            result["text"] = ""
    failures = check_decision(result, case["expected"]) if result else ["invalid_output"]
    # The existing adapter suppresses wait text and replaces Handoff text. Report
    # raw-format errors separately, rather than pretending these are audible faults.
    delivered = {**result, "text": result["text"] if result["action"] == "reply" else ""} if result else None
    delivered_failures = check_decision(delivered, case["expected"]) if delivered else ["invalid_output"]
    safe_handoff = bool(result and result["action"] in case["expected"].get("safe_actions", []))
    if safe_handoff:
        delivered_failures = [f for f in delivered_failures if f != "routing"]
    return {"design": design, "case": case["name"], "group": case["group"], "result": result,
            "failures": failures, "delivered_failures": delivered_failures,
            "safe_but_unhelpful_handoff": safe_handoff, "calls": calls,
            "ms": round(sum(c["ms"] for c in calls), 2)}


def summarize(rows):
    summary = []
    for design in (*DESIGNS, *REFINEMENTS):
        for condition in ("sequence", "exact_warm", "isolated_prefix"):
            selected = [r for r in rows if r["design"] == design and r["condition"] == condition]
            if not selected:
                continue
            calls = [c for r in selected for c in r["calls"]]
            latencies = sorted(r["ms"] for r in selected)

            def total(key, calls=calls):
                values = [c[key] for c in calls]
                return sum(values) if all(isinstance(value, (int, float)) for value in values) else None
            inp, cached = total("input_tokens"), total("cached_tokens")
            summary.append({"design": design, "condition": condition, "events": len(selected),
                            "raw_pass": sum(not r["failures"] for r in selected),
                            "delivered_pass": sum(not r["delivered_failures"] for r in selected),
                            "safe_handoffs": sum(r["safe_but_unhelpful_handoff"] for r in selected),
                            "calls": len(calls), "median_ms": statistics.median(latencies),
                            "p95_ms": latencies[min(len(latencies)-1, int(.95 * len(latencies)))],
                            "input_tokens": inp, "cached_tokens": cached,
                            "cache_fraction": cached / inp if inp and cached is not None else None,
                            "new_input_tokens": total("new_input_tokens"),
                            "output_tokens": total("output_tokens"),
                            "median_first_content_ms": statistics.median(
                                c["first_content_ms"] for c in calls if c["first_content_ms"] is not None)
                            if any(c["first_content_ms"] is not None for c in calls) else None})
    return summary


async def run(args):
    output = args.output or Path(__file__).with_name(".tools") / ("frontend-bakeoff-" + uuid.uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    cases = all_cases()
    namespace = uuid.uuid4().hex
    manifest = {"model": args.model, "designs": args.designs, "cases": cases,
                "temperature": 0, "seed": 17, "case_hash": digest(cases),
                "prompts": {"compact": COMPACT_PROMPT, "route": ROUTE_PROMPT,
                            "phrase": PHRASE_PROMPT, "frame": FRAME_RULE, "envelope": ENVELOPE_RULE},
                "cache_method": "Unique leading namespace, no service cache flush. Exact replay immediately after sequence event. Isolated-prefix probes use new namespaces, model remains warm.",
                "protocol": "Fixed synthetic heard transcript, not autonomous closed-loop or audio test. Each design sees the same authored events. Ledger retains separate channels, others use existing latest-only snapshot.",
                "request_budget": args.max_requests, "probe_policy": args.probe_policy,
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    rows = []
    async with ClientSession() as http:
        model = LabModel(http, os.environ["OMLX_API_KEY"], args.model, Usage(SafeLog()), limit=args.max_requests)
        await model.prepare()
        with (output / "results.jsonl").open("x") as log:
            async def record(design, case, ns, condition):
                row = await evaluate(model, design, case, ns)
                row["condition"] = condition
                rows.append(row)
                log.write(json.dumps(row) + "\n")
                log.flush()
            for index, case in enumerate(cases):
                offset = index % len(args.designs)
                order = args.designs[offset:] + args.designs[:offset]
                for design in order:
                    ns = f"{namespace}-{design}-{case['group']}"
                    await record(design, case, ns, "sequence")
                    if args.probe_policy == "full" or (args.probe_policy == "small" and index in (0, 2, len(cases)-1)):
                        await record(design, case, ns, "exact_warm")
            # Matched tasks/layouts, but changed leading cache namespace. No cache
            # eviction or server mutation is needed, and cache counters verify it.
            if args.probe_policy == "full":
                for case in [cases[0], cases[2], cases[-1]]:
                    for design in args.designs:
                        await record(design, case, uuid.uuid4().hex, "isolated_prefix")
    report = {"output": str(output.resolve()), "case_hash": manifest["case_hash"],
              "requests": sum(len(r["calls"]) for r in rows), "summary": summarize(rows)}
    (output / "summary.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-model", action="store_true")
    parser.add_argument("--model", default="gemma-4-e4b-it-4bit")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--designs", nargs="+", choices=(*DESIGNS, *REFINEMENTS), default=list(DESIGNS))
    parser.add_argument("--probe-policy", choices=("full", "small", "none"), default="full")
    parser.add_argument("--max-requests", type=int, choices=range(1, 501), default=400)
    args = parser.parse_args()
    if not args.live_model or not os.environ.get("OMLX_API_KEY"):
        parser.error("explicit --live-model and OMLX_API_KEY required")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
