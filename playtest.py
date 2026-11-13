# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Compact playtest reports for Dogwalk calls, built for reading by agents.

    uv run --script playtest.py --list            # recent calls, one line each
    uv run --script playtest.py                   # latest call: transcript, orchestrator, boundary, stats, reminders
    uv run --script playtest.py 2                 # second most recent call (or a Call SID prefix)
    uv run --script playtest.py --only stats      # transcript | orchestrator | boundary | stats | reminders
    uv run --script playtest.py --deploys         # Worker deployments interleaved with calls (PT)
    uv run --script playtest.py --full            # no line truncation
    uv run --script playtest.py --unmask          # operator only: show phone numbers and Invite Code words

Reads the admin Diagnostics API (verbose lens) with the operator password from macOS Keychain
(service `dogwalk-admin`, account `adam`). Output is plain text, one event per line, truncated
to --width so several playtests fit in context. Phone numbers, spoken digit runs, and Invite
Code words are masked unless --unmask; URL query strings are always dropped.

Kept small on purpose: the call-analysis skill (.agents/skills/call-analysis/SKILL.md) holds
the review checklist. Something moves into this file only after repeated use or a stumble,
and a Reminder is added when context was available but nobody reached for it.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import statistics
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ORIGIN = "https://dogwalk.tools"
PT = ZoneInfo("America/Los_Angeles")
ROOT = Path(__file__).resolve().parent
ACCOUNT_ID = "890753ccd6f0bbbfbe2c030cc44f6a87"  # Adam's personal Cloudflare account, as in deploy.py
CF_PROFILE = Path.home() / "Library/Preferences/cloudflare/config/personal.json"
EVICTION_SECONDS = 900  # a Durable Object holding only outbound WebSockets is evicted after 15 min
SECTIONS = ["transcript", "orchestrator", "boundary", "stats", "reminders", "timing"]
INVITATION_TOOLS = {"create_invitation", "list_invitations", "set_invitation_recipient", "revoke_invitation",
                    "inspect_registration"}


def api(path: str):
    password = subprocess.run(["security", "find-generic-password", "-a", "adam", "-s", "dogwalk-admin", "-w"],
                              capture_output=True, text=True, check=True).stdout.strip()
    request = urllib.request.Request(f"{ORIGIN}{path}", headers={
        "authorization": "Basic " + base64.b64encode(f"adam:{password}".encode()).decode(),
        "user-agent": "dogwalk-playtest/1",  # Cloudflare's bot filter rejects Python-urllib
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def deployments() -> list[dict] | None:
    """Recent Worker deployments, authenticated as deploy.py does; the token is never printed."""
    env = {**os.environ, "CLOUDFLARE_ACCOUNT_ID": ACCOUNT_ID}
    try:
        # A real CLI call refreshes the personal profile's OAuth bearer before we read it.
        subprocess.run(["cf", "workers", "list", "--profile", "personal"], env=env, capture_output=True, check=True)
        env["CLOUDFLARE_API_TOKEN"] = json.loads(CF_PROFILE.read_text())["oauth_token"]
        result = subprocess.run(["npx", "wrangler", "deployments", "list", "--json"], cwd=ROOT / "cf_worker",
                                env=env, capture_output=True, text=True, timeout=60, check=True)
        return [{"ts": datetime.fromisoformat(d["created_on"].replace("Z", "+00:00")).timestamp(),
                 "version": (d.get("versions") or [{"version_id": d["id"]}])[0]["version_id"][:8]}
                for d in json.loads(result.stdout)]
    except (OSError, subprocess.SubprocessError, KeyError, ValueError):
        return None


# Masking: transcripts are Adam's conversations, and Invitations carry other people's numbers.
PHONE = re.compile(r"(?<![\w.])(?:\+[1-9]\d{7,14}|(?:\+?1[\s.-]?)?\(?[2-9]\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{4})(?!\w)")
DIGIT = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
SPOKEN_DIGITS = re.compile(rf"\b(?:plus\s+)?(?:{DIGIT}[,\s]+){{6,}}{DIGIT}\b", re.IGNORECASE)
URL_QUERY = re.compile(r"(https?://[^\s\"'?]+)\?[^\s\"']+")


def mask_phone(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return f"+1XXX..{digits[-2:]}" if len(digits) == 10 or (len(digits) == 11 and digits[0] == "1") \
        else f"+XXX..{digits[-2:]}"


def invite_codes(context: list) -> set[str]:
    """Invite Code words named in invitation tool arguments and results anywhere in the context."""
    codes, names = set(), {i.get("call_id"): i.get("name") for i in context if i.get("type") == "function_call"}

    def walk(value):
        if isinstance(value, dict):
            for key, inner in value.items():
                if key in ("words", "invitation") and isinstance(inner, str) and inner.strip():
                    codes.add(inner.lower().strip())
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)
    for item in context:
        if item.get("type") == "function_call" and item.get("name") in INVITATION_TOOLS:
            walk(loads(item.get("arguments")))
        elif item.get("type") == "function_call_output" and names.get(item.get("call_id")) in INVITATION_TOOLS:
            walk(loads(item.get("output")))
    return codes


def masker(codes: set[str], unmask: bool):
    words = sorted({word for code in codes for word in code.split()}, key=len, reverse=True)

    def mask(text: str) -> str:
        text = URL_QUERY.sub(r"\1?…", str(text))
        if unmask:
            return text
        text = SPOKEN_DIGITS.sub("(spoken digits)", PHONE.sub(mask_phone, text))
        for word in words:
            text = re.sub(rf"\b{re.escape(word)}\b", word[0] + "***", text, flags=re.IGNORECASE)
        return text
    return mask


def loads(text) -> dict:
    try:
        value = text if isinstance(text, (dict, list)) else json.loads(text or "{}")
    except (json.JSONDecodeError, TypeError):
        return {"raw": str(text)}
    return value if isinstance(value, dict) else {"value": value}


def detail(row: dict) -> dict:
    return loads(row.get("detail"))


def event_ms(row: dict) -> float:
    """Place late provider evidence by occurrence time, retaining receipt separately."""
    data = detail(row)
    occurred = data.get("occurred_at_ms")
    return occurred if isinstance(occurred, (int, float)) else data.get("ts_ms", row["ts"] * 1000)


def timing_lines(call: dict) -> list[str]:
    activity = call.get("activity", [])
    rows = sorted(enumerate(activity), key=lambda pair: (event_ms(pair[1]), pair[0]))
    precise = sum("ts_ms" in detail(row) for row in activity)
    out = [f"Worker receipt/send and provider occurrence times, NOT telephone playout; {precise}/{len(activity)} records have ms-unit timestamps",
           "provider start_ms/end_ms/offset_ms are session-relative media offsets, not Unix time"]
    for _, row in rows:
        data = dict(detail(row))
        stamp = event_ms(row)
        label = data.pop("event", row["event"]) if row["event"] == "voice.timing" else row["event"]
        data.pop("ts_ms", None)
        precision = "" if "ts_ms" in detail(row) else " [seconds only]"
        if data.get("provider"):
            precision = " [provider seconds]" if data.get("timestamp_precision_ms") == 1000 else " [event time unknown; receipt]"
        out.append(f"+{stamp - call['started_at'] * 1000:010.0f}ms {label}{precision} "
                   + json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return out


def text_of(item: dict) -> str:
    content = item.get("content", "")
    return " ".join(p.get("text", "") for p in content if isinstance(p, dict)) if isinstance(content, list) else str(content)


def failed(output: dict) -> bool:
    return output.get("ok") is False or "error" in output


def clip(text: str, width: int) -> str:
    indent = len(text) - len(text.lstrip(" "))
    text = " " * indent + " ".join(str(text).split())
    return text if width <= 0 or len(text) <= width else text[: width - 1] + "…"


def pt(ts: float, fmt: str = "%H:%M") -> str:
    return datetime.fromtimestamp(ts, PT).strftime(fmt)


def pick_call(calls: list[dict], selector: str) -> dict:
    if selector.isdigit():
        if not 1 <= int(selector) <= len(calls):
            sys.exit(f"only {len(calls)} recent calls")
        return calls[int(selector) - 1]
    matches = [c for c in calls if c["call_sid"].startswith(selector)]
    if len(matches) != 1:
        sys.exit(f"selector {selector!r} matched {len(matches)} recent calls")
    return matches[0]


def call_bounds(conversation: dict, call: dict, calls: list[dict]) -> tuple[int, int]:
    """This call's part of the cross-call Orchestrator context, as (start, end) indices. Each voice
    session (a Theme change starts one) adds a mark, so a call spans its marks up to another call's."""
    context, marks = conversation.get("orchestrator_context", []), conversation.get("call_marks", [])
    own = [i for i, mark in enumerate(marks) if mark["call_sid"] == call["call_sid"]]
    if own:
        later = [m["context_index"] for m in marks[own[0]:] if m["call_sid"] != call["call_sid"]]
        return marks[own[0]]["context_index"], later[0] if later else len(context)
    # Calls before markers existed: the Nth-latest call_started belongs to the Nth-latest call.
    starts = [i for i, item in enumerate(context)
              if item.get("role") == "user" and "[harness event] call_started" in text_of(item)]
    rank = calls.index(call)
    if rank >= len(starts):
        return 0, 0
    later = [s for s in starts if s > starts[-1 - rank]]
    return starts[-1 - rank], later[0] if later else len(context)


def harness_events(context: list, start: int, end: int) -> list[tuple[str, str]]:
    """Harness events of one call. A call's call_ended is delivered with the next call_started, so
    events before this call's first call_started belong to the previous call and the next call's
    leading events belong to this one."""
    events = [(kind, body.strip()) for item in context[start:] if item.get("role") == "user"
              for line in text_of(item).splitlines() if line.startswith("[harness event] ")
              for kind, _, body in [line.removeprefix("[harness event] ").partition(":")]]
    first = next((i for i, (kind, _) in enumerate(events) if kind == "call_started"), 0)
    count = sum(1 for item in context[start:end] if item.get("role") == "user"
                for line in text_of(item).splitlines() if line.startswith("[harness event] "))
    after = events[count:]
    following = next((i for i, (kind, _) in enumerate(after) if kind == "call_started"), len(after))
    return events[first:count] + after[:following]


def saved_transcripts(conversation: dict, call: dict) -> list[dict]:
    return [s for s in conversation.get("call_transcripts", []) if s.get("call_sid") == call["call_sid"]]


def transcript_lines(conversation: dict, call: dict, items: list) -> tuple[str, list[str]]:
    saved = saved_transcripts(conversation, call)
    if saved:  # one per voice session; a Theme change starts a new one
        lines = []
        for number, session in enumerate(saved, 1):
            if len(saved) > 1:
                lines.append(f"-- voice session {number}, saved {session.get('ended_at')}")
            lines += [f"{'Caller' if e.get('role') == 'caller' else 'Voice '}: {str(e.get('text', '')).strip()}"
                      for e in session.get("entries", [])]
        return "verbatim", lines
    lines, seen = [], set()
    for item in items:  # reconstruct from Handoff blocks; speech after the last Handoff is lost
        for line in (text_of(item) if item.get("role") == "user" else "").splitlines():
            if line.startswith(("Caller:", "Voice:")) and line not in seen:
                seen.add(line)
                lines.append(line.replace("Voice:", "Voice :", 1))
    return "reconstructed from handoffs", lines


def orchestrator_lines(items: list) -> list[str]:
    out = []
    for item in items:
        if item.get("role") == "user":
            for line in text_of(item).splitlines():
                if line.startswith("[handoff"):
                    out.append(f"» {line.strip('[]')}")
                elif line.startswith("[harness event]"):
                    kind, _, body = line.removeprefix("[harness event] ").partition(":")
                    data = loads(body)
                    text = body if "raw" in data else f"{len(data.get('managed_sessions', []))} sessions" \
                        if kind == "call_started" else " ".join(f"{k}={v}" for k, v in data.items() if k != "agent_message")
                    out.append(f"» event {kind}: {text}")
        elif item.get("type") == "function_call":
            args, name = loads(item.get("arguments")), item.get("name")
            if name in ("say", "brief"):
                out.append(f"  {name.upper():5} {args.get('text', '')}")
            elif name == "begin_prompt_turn":
                out.append(f"  prompt {args.get('alias')}: {args.get('prompt', '')}")
            elif name == "steer_prompt_turn":
                out.append(f"  steer  {args.get('alias')} ({args.get('when')}): {args.get('guidance', '')}")
            else:
                out.append(f"  tool  {name} {json.dumps(args) if args else ''}")
        elif item.get("type") == "function_call_output" and failed(loads(item.get("output"))):
            out.append(f"  !!    {item.get('output')}")
    return out


def boundary_lines(items: list) -> list[str]:
    """Theme words and phone numbers must never cross into Agent prompts or steering (DOMAIN.md, Themes; Invitation)."""
    source = (ROOT / "cf_worker/src/themes.ts").read_text()
    lexicon = {word: theme for theme, body in re.findall(r'\bid:\s*"(\w+)".*?\blexicon:\s*\[([^\]]*)\]', source, re.DOTALL)
               for word in re.findall(r'"([^"]+)"', body)}
    prompts = [loads(i.get("arguments")) for i in items
               if i.get("type") == "function_call" and i.get("name") in ("begin_prompt_turn", "steer_prompt_turn")]
    out = []
    for args in prompts:
        prompt, alias = str(args.get("prompt", args.get("guidance", ""))), args.get("alias")
        for word, theme in lexicon.items():
            for match in re.finditer(rf"\b{re.escape(word)}(?:s|es|ed|ing)?\b", prompt, re.IGNORECASE):
                out.append(f"!! {theme} word {match.group(0)!r} in prompt to {alias}: "
                           f"…{prompt[max(0, match.start() - 50): match.end() + 50]}…")
        for match in [*PHONE.finditer(prompt), *SPOKEN_DIGITS.finditer(prompt)]:
            out.append(f"!! phone number {match.group(0)} in prompt to {alias}")
    return [f"{len(prompts)} prompts scanned for {len(lexicon)} Theme words (themes.ts) and phone numbers: "
            f"{len(out)} hits"] + out


def stats_lines(call: dict, items: list) -> list[str]:
    activity = call.get("activity", [])
    t0 = activity[0]["ts"] if activity else call["started_at"]
    model = [detail(r) for r in activity if r["event"] == "orchestrator.model"]
    model_ms = [m["ms"] for m in model if "ms" in m]
    usage = [m.get("usage") or {} for m in model]
    first = lambda name: next((f"+{r['ts'] - t0}s" for r in activity if r["event"] == name), "n/a")
    handoffs = [(event_ms(r), detail(r).get("delegation_id")) for r in activity if r["event"] == "voice.delegation.received"]
    says = [(event_ms(r), detail(r).get("delegation_id")) for r in activity
            if r["event"] == "orchestrator.say" and detail(r).get("ok") is not False]
    waits = []
    for ts, delegation in handoffs:
        spoken = [s for s, d in says if d == delegation and s >= ts]
        waits.append(f"{min(spoken) - ts:.0f}ms" if spoken else "none")
    names = [i.get("name") for i in items if i.get("type") == "function_call"]
    errors = sum(1 for i in items if i.get("type") == "function_call_output" and failed(loads(i.get("output"))))
    closes = [f"{detail(r).get('reason')} {(detail(r).get('usage') or {}).get('seconds')}s"
              for r in activity if r["event"] == "voice.live.closed"]
    themes = [detail(r).get("theme") for r in activity if r["event"] == "voice.theme"]
    out = [
        f"timeline: sandbox ready {first('sandbox.ready')}, voice attached {first('voice.live.attached')}, "
        f"ended +{(call.get('ended_at') or (activity[-1]['ts'] if activity else t0)) - t0}s ({call.get('status')})",
        f"voice sessions: {sum(1 for r in activity if r['event'] == 'voice.live.attached')}; "
        f"closes: {', '.join(closes) or 'none'}" + (f"; theme changes: {', '.join(map(str, themes))}" if themes else ""),
        f"handoffs: {len(handoffs)}; first say accepted after handoff (not heard): {', '.join(waits) or 'n/a'}",
        f"model calls: {len(model_ms)}"
        + (f", p50 {statistics.median(model_ms) / 1000:.1f}s, max {max(model_ms) / 1000:.1f}s" if model_ms else "")
        + (f", tokens in {sum(u.get('input_tokens', 0) for u in usage)}"
           f" (cached {sum((u.get('input_tokens_details') or {}).get('cached_tokens', 0) for u in usage)})"
           f", out {sum(u.get('output_tokens', 0) for u in usage)}" if usage else ""),
        f"tools: {', '.join(f'{n}×{names.count(n)}' for n in dict.fromkeys(names)) or 'none'}; errors {errors}",
        f"turns: {sum(1 for r in activity if r['event'] == 'agent.turn.started')} started, "
        f"{sum(1 for r in activity if r['event'] == 'agent.turn.stopped')} stopped, "
        f"{sum(1 for r in activity if r['event'] == 'agent.turn.failed')} failed, "
        f"{sum(1 for r in activity if r['event'] == 'agent.turn.steered')} steered",
    ]
    fixtures = [(r["event"].removeprefix("voice.fixture."), detail(r).get("label")) for r in activity
                if r["event"].startswith("voice.fixture.")]
    if fixtures:
        out.append("audio: " + ", ".join(f"{label} {outcome}" for outcome, label in fixtures))
    recordings = [f"{r['event'].removeprefix('voice.recording.')} {json.dumps(detail(r))}" for r in activity
                  if r["event"].startswith("voice.recording.")]
    keys = "".join(str(detail(r).get("key", "")) for r in activity if r["event"] == "voice.keypad")
    if recordings:
        out.append("recordings: " + "; ".join(recordings))
    if keys:
        out.append(f"keypad: {keys}")
    for r in activity:
        if r["event"] == "call.attestation":
            out.append(f"caller id: {json.dumps({k: v for k, v in detail(r).items() if k != 'parameters'})}")
        if r["event"] == "voice.input.levels":
            out.append(f"caller audio outside recording: {r['detail']}")
    if "end_call" not in names and call.get("status") == "completed":
        out.append("note: no end_call; the caller or provider hung up")
    return out


def reminder_lines(conversation: dict, call: dict, bounds: tuple[int, int], deploys: list[dict] | None) -> list[str]:
    """Context that was available but that an analyst forgot to reach for; each names where to look next.
    Add one (with its incident) whenever a review later finds it missed something sitting in the data."""
    activity, t0 = call.get("activity", []), call["started_at"]
    end = call.get("ended_at") or (activity[-1]["ts"] if activity else t0)
    context = conversation.get("orchestrator_context", [])
    items, events = context[bounds[0]:bounds[1]], harness_events(context, *bounds)
    out = []
    # 2026-11-08: a failure was blamed on a deploy that had landed before the call began.
    if deploys is None:
        out.append("deploy times unavailable: check `--deploys` before attributing behavior to a deploy")
    for deploy in deploys or []:
        if t0 - 300 <= deploy["ts"] <= end:
            when = "during the call" if deploy["ts"] >= t0 else "within 5 min before it"
            out.append(f"deploy {deploy['version']} at {pt(deploy['ts'], '%H:%M:%S')} PT, {when}: the Durable Object may "
                       "run old code until the Sandbox stops (deploy.py); a paired tab may be stale")
    # 2026-11: outbound-only WebSockets let the Durable Object be evicted at 15 min; alarms fixed it.
    duration = call.get("duration_seconds") or end - t0
    if duration >= EVICTION_SECONDS - 20:
        out.append(f"call ran {duration}s, past the {EVICTION_SECONDS}s eviction mark: confirm voice.live.closed reasons "
                   "and call_ended in stats/orchestrator; an end near 900 s is a keepalive regression")
    if any(k == "call_ended" and "not observed" in b for k, b in events):
        out.append("call_ended says voice end not observed: the Durable Object missed the live close; check activity "
                   "around the end and whether the transcript was recovered")
    attached = sum(1 for r in activity if r["event"] == "voice.live.attached")
    if len(saved_transcripts(conversation, call)) < attached:
        out.append(f"transcripts saved {len(saved_transcripts(conversation, call))} of {attached} voice sessions: "
                   "the transcript section may be reconstructed; see conversations current_transcript")
    # 2026-11-08: a 76.7k-token context with no Memory went unremarked until the second pass.
    if not conversation.get("memory") and (conversation.get("context_tokens") or 0) > 60000:
        out.append(f"no Memory at {conversation.get('context_tokens')} context tokens: compact_context never ran; "
                   "read conversations memory, memory_versions, context_archives")
    latest = {}
    for item in items:
        if item.get("type") == "function_call_output":
            for view in [loads(item.get("output")), *loads(item.get("output")).get("invitations", [])]:
                if isinstance(view, dict) and isinstance(view.get("words"), str) and "status" in view:
                    latest[view["words"]] = view["status"]
                if isinstance(view, dict) and isinstance(view.get("invitation"), dict):
                    inner = view["invitation"]
                    if isinstance(inner.get("words"), str):
                        latest[inner["words"]] = inner.get("status")
    if active := [w for w, status in latest.items() if status == "active"]:
        out.append(f"{len(active)} invitation(s) last reported active ({', '.join(active)}): a live code can still "
                   "register someone; confirm with the caller or list_invitations")
    # 2026-11-08: "unknown component Image" came from a paired tab loaded before the deploy.
    for kind, body in events:
        if kind == "whiteboard_error" and "unknown component" in body:
            error = str(loads(body).get("error", body))
            out.append(f"whiteboard_error {error[:70]!r}: the paired page may predate the deploy (stale tab), "
                       "compare with cf_worker/src/pair_page.ts")
    agent_errors = sorted({m for r in activity if r["event"].startswith("agent.")
                           for m in re.findall(r"SseError|ServerUnavailable|disconnected|connect\.failed|out_of_memory",
                                               r["event"] + " " + str(r.get("detail")))})
    if agent_errors:
        out.append(f"Agent connection errors ({', '.join(agent_errors)}): check agent.turn.failed in activity and the "
                   "Sandbox gateway/OpenCode logs (disk, memory) before blaming the Orchestrator")
    # A cold start or a new voice session (Theme change) rewrites the prefix, so a miss then is expected.
    fresh = [r["ts"] for r in activity if r["event"] == "voice.live.attached"]
    misses = [r["ts"] - t0 for r in activity if r["event"] == "orchestrator.model"
              and (detail(r).get("usage") or {}).get("input_tokens", 0) > 20000
              and ((detail(r).get("usage") or {}).get("input_tokens_details") or {}).get("cached_tokens", 1) == 0
              and not any(0 <= r["ts"] - f <= 90 for f in fresh)]
    if misses:
        out.append(f"prompt cache misses at +{', +'.join(map(str, misses))}s, away from a voice session start: "
                   "something changed the context prefix (orchestrator.model usage in activity)")
    money = [i for i in items if i.get("type") == "function_call_output" or i.get("role") == "user"
             if re.search(r"¢|\bcents\b|\bpaid\b", text_of(i) + str(i.get("output", "")))]
    if money:  # not "cost": every turn_stopped carries the Agent session's own cost
        out.append(f"paid work mentioned in {len(money)} context items: check that the caller consented before any paid "
                   "generation and that the spend was reported (orchestrator section)")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("call", nargs="?", default="1", help="1 = latest, 2 = previous, or a Call SID prefix")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--only", choices=SECTIONS)
    parser.add_argument("--deploys", action="store_true", help="list Worker deployments against recent calls")
    parser.add_argument("--no-deploys", action="store_true", help="skip the deploy lookup (about 2 s)")
    parser.add_argument("--unmask", action="store_true", help="operator only: phone numbers and Invite Code words")
    parser.add_argument("--full", action="store_true", help="disable truncation")
    parser.add_argument("--width", type=int, default=150)
    args = parser.parse_args()
    width = 0 if args.full else args.width

    calls = api("/admin/api/calls?verbose=1&limit=20" + ("&timing=1" if args.only == "timing" else ""))
    conversation = (api("/admin/api/conversations?verbose=1") or [{}])[0]
    mask = masker(invite_codes(conversation.get("orchestrator_context", [])), args.unmask)
    emit = lambda lines: print("\n".join(clip(mask(line), width) for line in lines) or "(none)")
    if args.list:
        emit([f"{i:2} {c['call_sid'][:10]} {pt(c['started_at'], '%m-%d %H:%M')} {c.get('duration_seconds') or '?':>4}s "
              f"{c['status']} handoffs={sum(1 for a in c.get('activity', []) if a['event'] == 'voice.delegation.received')}"
              for i, c in enumerate(calls, 1)])
        return
    deploys = None if args.no_deploys or args.only not in (None, "reminders") and not args.deploys else deployments()
    if args.deploys:
        if deploys is None:
            sys.exit("wrangler deployments list failed (token withheld)")
        rows = [(d["ts"], f"   deploy {d['version']}") for d in deploys]
        for c in calls:
            start, stop = c["started_at"], c.get("ended_at") or c["started_at"]
            hit = [d["version"] for d in deploys if start - 300 <= d["ts"] <= stop]
            rows.append((start, f"{'!!' if hit else '  '} call   {c['call_sid'][:10]} to {pt(stop)} "
                                f"{c.get('duration_seconds') or '?'}s" + (f" overlaps deploy {', '.join(hit)}" if hit else "")))
        print("## deploys and calls, PT (!! = deploy during the call or within 5 min before)")
        oldest = min((d["ts"] for d in deploys), default=0) - 3600  # wrangler lists only the last 10 deploys
        emit([f"{pt(ts, '%m-%d %H:%M')} {text}" for ts, text in sorted(rows) if ts >= oldest])
        return

    call = pick_call(calls, args.call)
    bounds = call_bounds(conversation, call, calls)
    items = conversation.get("orchestrator_context", [])[bounds[0]:bounds[1]]
    print(f"call {call['call_sid'][:10]} {pt(call['started_at'], '%Y-%m-%d %H:%M PT')} "
          f"{call.get('duration_seconds') or '?'}s {call['status']}")
    if args.only in (None, "transcript"):
        source, lines = transcript_lines(conversation, call, items)
        print(f"\n## transcript ({source})")
        emit(lines)
    if args.only in (None, "orchestrator"):
        print("\n## orchestrator")
        emit(orchestrator_lines(items))
    if args.only in (None, "boundary"):
        print("\n## boundary (Agent prompts)")
        emit(boundary_lines(items))
    if args.only in (None, "stats"):
        print("\n## stats")
        emit(stats_lines(call, items))
    if args.only == "timing":
        print("\n## timing (use --full to avoid truncating metadata)")
        emit(timing_lines(call))
    if reminders := reminder_lines(conversation, call, bounds, deploys) if args.only in (None, "reminders") else []:
        print("\n## reminders (available context worth reaching for)")
        emit([f"- {line}" for line in reminders])


if __name__ == "__main__":
    main()
