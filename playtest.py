# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Compact playtest reports for Dogwalk calls, built for reading by agents.

    uv run --script playtest.py --list            # recent calls, one line each
    uv run --script playtest.py                   # latest call: transcript, orchestrator, stats
    uv run --script playtest.py 2                 # second most recent call (or a Call SID prefix)
    uv run --script playtest.py --only stats      # transcript | orchestrator | stats
    uv run --script playtest.py --full            # no line truncation

Reads the admin Diagnostics API (verbose lens) with the operator password from
macOS Keychain (service `dogwalk-admin`, account `adam`). Output is plain text,
one event per line, truncated to --width so several playtests fit in context.
"""

from __future__ import annotations

import argparse
import base64
import json
import statistics
import subprocess
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

ORIGIN = "https://dogwalk.tools"
PT = ZoneInfo("America/Los_Angeles")


def api(path: str):
    password = subprocess.run(["security", "find-generic-password", "-a", "adam", "-s", "dogwalk-admin", "-w"],
                              capture_output=True, text=True, check=True).stdout.strip()
    request = urllib.request.Request(f"{ORIGIN}{path}", headers={
        "authorization": "Basic " + base64.b64encode(f"adam:{password}".encode()).decode(),
        "user-agent": "dogwalk-playtest/1",  # Cloudflare's bot filter rejects Python-urllib
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def clip(text: str, width: int) -> str:
    indent = len(text) - len(text.lstrip(" "))
    text = " " * indent + " ".join(str(text).split())
    return text if width <= 0 or len(text) <= width else text[: width - 1] + "…"


def pick_call(calls: list[dict], selector: str) -> dict:
    if selector.isdigit():
        return calls[int(selector) - 1]
    matches = [c for c in calls if c["call_sid"].startswith(selector)]
    if len(matches) != 1:
        sys.exit(f"selector {selector!r} matched {len(matches)} recent calls")
    return matches[0]


def call_slice(conversation: dict, call: dict, calls: list[dict]) -> list:
    """This call's part of the cross-call Orchestrator context."""
    context, marks = conversation["orchestrator_context"], conversation.get("call_marks", [])
    for index, mark in enumerate(marks):
        if mark["call_sid"] == call["call_sid"]:
            end = marks[index + 1]["context_index"] if index + 1 < len(marks) else len(context)
            return context[mark["context_index"]:end]
    # Calls before markers existed: the Nth-latest call_started belongs to the Nth-latest call.
    starts = [i for i, item in enumerate(context)
              if item.get("role") == "user" and str(item.get("content", "")).startswith("[harness event] call_started")]
    rank = calls.index(call)
    if rank >= len(starts):
        return []
    begin = starts[-1 - rank]
    later = [s for s in starts if s > begin]
    return context[begin: later[0] if later else len(context)]


def transcript_lines(conversation: dict, call: dict, items: list) -> tuple[str, list[str]]:
    for saved in conversation.get("call_transcripts", []):
        if saved.get("call_sid") == call["call_sid"]:
            return "verbatim", [f"{'Caller' if e['role'] == 'caller' else 'Voice '}: {e['text'].strip()}"
                                for e in saved["entries"]]
    lines, seen = [], set()
    for item in items:  # reconstruct from Handoff blocks; speech after the last Handoff is lost
        content = str(item.get("content", "")) if item.get("role") == "user" else ""
        for line in content.splitlines():
            if line.startswith(("Caller:", "Voice:")) and line not in seen:
                seen.add(line)
                lines.append(line.replace("Voice:", "Voice :", 1))
    return "reconstructed from handoffs", lines


def orchestrator_lines(items: list, width: int) -> list[str]:
    out = []
    for item in items:
        if item.get("role") == "user":
            for line in str(item.get("content", "")).splitlines():
                if line.startswith("[handoff"):
                    out.append(f"» {line.strip('[]')}")
                elif line.startswith("[harness event]"):
                    kind, _, body = line.removeprefix("[harness event] ").partition(":")
                    detail = body
                    try:
                        data = json.loads(body)
                        detail = " ".join(f"{k}={v}" for k, v in data.items() if k != "agent_message") \
                            if kind != "call_started" else f"{len(data.get('managed_sessions', []))} sessions"
                    except json.JSONDecodeError:
                        pass
                    out.append(clip(f"» event {kind}: {detail}", width))
        elif item.get("type") == "function_call":
            args = json.loads(item.get("arguments") or "{}")
            name = item["name"]
            if name in ("say", "brief"):
                out.append(clip(f"  {name.upper():5} {args.get('text', '')}", width))
            elif name == "begin_prompt_turn":
                out.append(clip(f"  prompt {args.get('alias')}: {args.get('prompt', '')}", width))
            else:
                out.append(clip(f"  tool  {name} {json.dumps(args) if args else ''}", width))
        elif item.get("type") == "function_call_output" and '"ok": false' in item.get("output", "").replace('"ok":false', '"ok": false'):
            out.append(clip(f"  !!    {item['output']}", width))
    return out


def stats_lines(call: dict, items: list) -> list[str]:
    activity = call["activity"]
    t0 = activity[0]["ts"] if activity else call["started_at"]
    detail = lambda row: json.loads(row["detail"] or "{}")
    model_ms = [detail(r)["ms"] for r in activity if r["event"] == "orchestrator.model"]
    usage = [detail(r).get("usage") or {} for r in activity if r["event"] == "orchestrator.model"]
    first = lambda name: next((r["ts"] - t0 for r in activity if r["event"] == name), None)
    handoffs = [(r["ts"], detail(r)["delegation_id"]) for r in activity if r["event"] == "voice.delegation.received"]
    says = [(r["ts"], detail(r).get("delegation_id")) for r in activity if r["event"] == "orchestrator.say"]
    waits = []
    for ts, delegation in handoffs:
        spoken = [s for s, d in says if d == delegation and s >= ts]
        waits.append(f"{spoken[0] - ts}s" if spoken else "none")
    calls = [i for i in items if i.get("type") == "function_call"]
    errors = sum(1 for i in items if i.get("type") == "function_call_output" and '"ok": false' in i.get("output", ""))
    names = [c["name"] for c in calls]
    out = [
        f"timeline: sandbox ready +{first('sandbox.ready')}s, voice attached +{first('voice.live.attached')}s, "
        f"ended +{(call.get('ended_at') or activity[-1]['ts']) - t0}s ({call.get('status')})",
        f"handoffs: {len(handoffs)}; first say after handoff: {', '.join(waits) or 'n/a'}"
        + ("" if says else " (say timing needs orchestrator.say events)"),
        f"model calls: {len(model_ms)}"
        + (f", p50 {statistics.median(model_ms) / 1000:.1f}s, max {max(model_ms) / 1000:.1f}s" if model_ms else "")
        + (f", tokens in {sum(u.get('input_tokens', 0) for u in usage)}"
           f" (cached {sum((u.get('input_tokens_details') or {}).get('cached_tokens', 0) for u in usage)})"
           f", out {sum(u.get('output_tokens', 0) for u in usage)}" if usage else ""),
        f"tools: {', '.join(f'{n}×{names.count(n)}' for n in dict.fromkeys(names)) or 'none'}; errors {errors}",
        f"turns: {sum(1 for r in activity if r['event'] == 'acp.turn.started')} started, "
        f"{sum(1 for r in activity if r['event'] == 'acp.turn.stopped')} stopped, "
        f"{sum(1 for r in activity if r['event'] == 'acp.turn.failed')} failed",
    ]
    if "end_call" not in names and call.get("status") == "completed":
        out.append("note: no end_call; the caller or provider hung up")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("call", nargs="?", default="1", help="1 = latest, 2 = previous, or a Call SID prefix")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--only", choices=["transcript", "orchestrator", "stats"])
    parser.add_argument("--full", action="store_true", help="disable truncation")
    parser.add_argument("--width", type=int, default=150)
    args = parser.parse_args()
    width = 0 if args.full else args.width

    calls = api("/admin/api/calls?verbose=1&limit=20")
    if args.list:
        for i, c in enumerate(calls, 1):
            started = datetime.fromtimestamp(c["started_at"], PT).strftime("%m-%d %H:%M")
            print(f"{i:2} {c['call_sid'][:10]} {started} {c.get('duration_seconds') or '?':>4}s {c['status']} "
                  f"handoffs={sum(1 for a in c['activity'] if a['event'] == 'voice.delegation.received')}")
        return
    call = pick_call(calls, args.call)
    conversation = api("/admin/api/conversations?verbose=1")[0]
    items = call_slice(conversation, call, calls)
    started = datetime.fromtimestamp(call["started_at"], PT).strftime("%Y-%m-%d %H:%M PT")
    print(f"call {call['call_sid'][:10]} {started} {call.get('duration_seconds') or '?'}s {call['status']}")
    if args.only in (None, "transcript"):
        source, lines = transcript_lines(conversation, call, items)
        print(f"\n## transcript ({source})")
        print("\n".join(clip(line, width) for line in lines) or "(none)")
    if args.only in (None, "orchestrator"):
        print("\n## orchestrator")
        print("\n".join(orchestrator_lines(items, width)) or "(none)")
    if args.only in (None, "stats"):
        print("\n## stats")
        print("\n".join(stats_lines(call, items)))


if __name__ == "__main__":
    main()
