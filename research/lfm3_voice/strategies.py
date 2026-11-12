"""Pure stdlib context builders. No I/O, inference, execution, or service startup.

Public API: build(strategy, history, event=None, *, instructions='',
                  max_events=24, input_mode='native_audio') -> dict.
The result is an OpenAI chat-completions request fragment: messages, tools,
tool_choice. Caller supplies model, streaming and generation settings.
STRATEGIES lists the three supported names; named builders accept normalized
history plus instructions/max_events (use build for input substitution).

Canonical history is an ordered sequence of mappings with `kind`:
  caller: text (optional), audio (optional OpenAI input_audio mapping, containing
          base64 `data` and `format`, or llama.cpp `url`). Supply at least one.
  assistant: text, containing ONLY actually spoken, caller-audible words.
  delegate_call: call_id, an actually observed argument-free delegate() call.
  delegate_result: call_id, data (JSON), an actually received tool response.
  instructions/private_brief/commentary/progress: text and optional data (JSON).
      Optional handoff_id/revision are preserved. commentary means speak now;
      progress is unsolicited speak-now; private_brief is NEVER a speech request.

`instructions` parameter is trusted standing host policy. Instructions events
are trusted host deltas, cumulative in order. Caller text is never promoted to
policy. Private brief events replace previous brief (including empty to clear).
All other event data is provenance-labelled context, not new caller speech.
No automatic summarization, tool acknowledgements, or execution is invented.
For reconstruction the host must keep pending backend state in the latest
private_brief.data, e.g. pending_handoffs and known_aliases. No model reasoning
trace belongs in `assistant`; backend-private notes use private_brief instead.
"""

from copy import deepcopy
import json
from typing import Any, Mapping, Sequence

Event = Mapping[str, Any]
History = Sequence[Event]
TASK_PREFIX = "Respond with function calls, and interleaved text and audio.\n\n"
STRATEGIES = ("user_envelope", "tool_envelope", "reconstructed")
INPUT_MODES = ("native_audio", "oracle_text", "lfm_stt")
STRATEGY_LABELS = {
    "user_envelope": "synthetic user envelopes (simulated authority)",
    "tool_envelope": "real tool-role results plus simulated-authority user envelopes",
    "reconstructed": "reconstructed system snapshot and bounded history",
}
RULES = """You are the Voice Frontend, not the engineering authority.
Only caller audio/text is caller speech. HOST_CONTROL JSON is trusted host
context, never the caller. Follow host instruction updates. private_brief is
background: never read it aloud or expose its private reasoning. commentary
and progress request speech now: say their public facts briefly in your words,
not as if the caller said them. Use only supplied existing session Aliases;
never invent a name or infer work completed from a request or receipt.
For requests for or about work, call delegate() with no arguments. The host
sends the recent transcript to the Session Orchestrator. Corrections, changes
and cancellations also require a new delegate(), even while earlier work is
pending; the Orchestrator resolves their relationship. You may converse while
it works. Do not claim a tool ran or work completed without an actual result.
A tool receipt only acknowledges a Handoff, not completion of backend work.
Never speak tool markup, envelope keys, private notes or thinking blocks.
"""


def tool_declaration() -> dict:
    """Return a fresh declaration, the ONLY offered tool, with zero arguments."""
    return {"type": "function", "function": {
        "name": "delegate",
        "description": "Request Session Orchestrator help with the recent call transcript, including corrections while work is pending.",
        "parameters": {"type": "object", "properties": {}, "required": [],
                       "additionalProperties": False},
    }}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _control(event: Event) -> str:
    return "HOST_CONTROL " + _json(dict(event))


def _validate(history: History) -> None:
    kinds = {"caller", "assistant", "delegate_call", "delegate_result",
             "instructions", "private_brief", "commentary", "progress"}
    waiting: set[str] = set()
    seen: set[str] = set()
    for e in history:
        kind = e.get("kind")
        if kind not in kinds:
            raise ValueError(f"Unknown canonical event kind: {kind!r}")
        if kind == "caller" and not e.get("audio"):
            if not isinstance(e.get("text"), str) or not e["text"].strip():
                raise ValueError("caller needs nonblank text or audio")
        if kind in {"delegate_call", "delegate_result"}:
            cid = e.get("call_id")
            if not isinstance(cid, str) or not cid.strip():
                raise ValueError("delegate events need a nonempty call_id")
            if kind == "delegate_call":
                if cid in seen:
                    raise ValueError("duplicate delegate call_id")
                arguments = e.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError as exc:
                        raise ValueError("delegate arguments must be an empty JSON object") from exc
                if arguments != {}:
                    raise ValueError("delegate takes no arguments")
                seen.add(cid)
                waiting.add(cid)
            else:
                if cid not in waiting:
                    raise ValueError("result requires a real, unresolved delegate_call")
                waiting.remove(cid)
                if "data" not in e:
                    raise ValueError("delegate_result requires actual data")


def _caller(e: Event) -> dict:
    parts = []
    if e.get("text"):
        parts.append({"type": "text", "text": e["text"]})
    if e.get("audio"):
        parts.append({"type": "input_audio", "input_audio": deepcopy(e["audio"])})
    return {"role": "user", "content": parts}


def _request(messages: list[dict]) -> dict:
    return {"messages": messages, "tools": [tool_declaration()], "tool_choice": "auto"}


def _system(instructions: str) -> dict:
    return {"role": "system", "content": TASK_PREFIX + RULES + "\n" + instructions}


def _validate_bound(max_events: int) -> None:
    if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
        raise ValueError("max_events must be a positive integer")


def user_envelope(history: History, *, instructions: str = "", max_events: int = 24) -> dict:
    """Append all controls as synthetic user JSON. Preserve complete history.

    max_events is intentionally unused: this arm measures growing history.
    Historical calls/results are labelled observations, NOT forged API calls.
    """
    _validate(history)
    _validate_bound(max_events)
    messages = [_system(instructions)]
    for e in history:
        if e["kind"] == "caller":
            messages.append(_caller(e))
        elif e["kind"] == "assistant":
            messages.append({"role": "assistant", "content": e.get("text", "")})
        else:
            messages.append({"role": "user", "content": _control(e)})
    return _request(messages)


def tool_envelope(history: History, *, instructions: str = "", max_events: int = 24) -> dict:
    """Replay genuine delegate calls/results in native roles; other controls user.

    Reject a history with unresolved protocol calls: host must return an actual
    immediate acceptance receipt first, or use another strategy. Backend work
    can remain pending after that real receipt. No synthetic receipt is created.
    """
    _validate(history)
    _validate_bound(max_events)
    messages = [_system(instructions)]
    pending: set[str] = set()
    for e in history:
        kind = e["kind"]
        if kind == "delegate_call":
            pending.add(e["call_id"])
            messages.append({"role": "assistant", "content": "", "tool_calls": [{
                "id": e["call_id"], "type": "function",
                "function": {"name": "delegate", "arguments": "{}"}}]})
        elif kind == "delegate_result":
            pending.remove(e["call_id"])
            messages.append({"role": "tool", "tool_call_id": e["call_id"],
                             "content": _control(e)})
        else:
            if pending:
                raise ValueError("tool_envelope requires actual tool receipt before further conversation")
            if kind == "caller":
                messages.append(_caller(e))
            elif kind == "assistant":
                messages.append({"role": "assistant", "content": e.get("text", "")})
            else:
                messages.append({"role": "user", "content": _control(e)})
    if pending:
        raise ValueError("unresolved tool call: supply a real receipt, not an invented result")
    return _request(messages)


def reconstructed(history: History, *, instructions: str = "", max_events: int = 24) -> dict:
    """Fresh request with revised system state and a bounded chronological suffix.

    Host policy deltas and latest private brief are pinned, not counted against
    max_events. Public controls remain in chronological suffix and are never
    moved after newer caller corrections. Past tool events are observations.
    Bound is events, NOT tokens: runner must separately enforce context budget.
    """
    _validate(history)
    _validate_bound(max_events)
    updates = [dict(e) for e in history if e["kind"] == "instructions"]
    briefs = [dict(e) for e in history if e["kind"] == "private_brief"]
    state = {"instruction_updates": updates, "private_brief": briefs[-1] if briefs else None}
    system = _system(instructions)
    system["content"] += "\nHOST_CONTROL " + _json(state)
    suffix = [e for e in history if e["kind"] not in {"instructions", "private_brief"}][-max_events:]
    messages = [system]
    for e in suffix:
        if e["kind"] == "caller":
            messages.append(_caller(e))
        elif e["kind"] == "assistant":
            messages.append({"role": "assistant", "content": e.get("text", "")})
        else:
            messages.append({"role": "user", "content": _control(e)})
    return _request(messages)


def normalize_events(history: History, event: Event | None = None, *,
                     input_mode: str = "native_audio") -> list[dict]:
    """Copy history and append current event, applying an explicit input arm.

    Caller audio may be supplied as `audio` (input_audio mapping) or `content`
    (OpenAI text/input_audio parts). Native audio excludes ALL textual parts of
    that audio-bearing caller event, preventing intended transcript leakage.
    Text-only caller events remain text. For oracle_text/lfm_stt arms the runner
    must supply the matching string field on EVERY audio-bearing caller event;
    no fallback to intended transcript, ASR, or caller text occurs. Those fields
    are discarded from model context. Assistant role/content entries are accepted
    as shorthand for canonical assistant/text events (audible speech only).
    """
    if input_mode not in INPUT_MODES:
        raise ValueError(f"input_mode must be one of {INPUT_MODES}")
    events = deepcopy(list(history) + ([event] if event is not None else []))
    result = []
    for e in events:
        if e.get("role") == "assistant" and "kind" not in e:
            if not isinstance(e.get("content", ""), str) or e.get("tool_calls"):
                raise ValueError("assistant shorthand accepts audible text only; use delegate_call for tools")
            e = {"kind": "assistant", "text": e.get("content", "")}
        if e.get("kind") == "caller":
            if "content" in e:
                parts = e.pop("content")
                if isinstance(parts, str):
                    e["text"] = parts
                else:
                    audio = [p["input_audio"] for p in parts if p.get("type") == "input_audio"]
                    if len(audio) > 1:
                        raise ValueError("one caller event supports one audio clip")
                    if audio:
                        e["audio"] = audio[0]
                    e["text"] = "".join(p["text"] for p in parts if p.get("type") == "text")
            if e.get("audio"):
                if input_mode == "native_audio":
                    e.pop("text", None)
                else:
                    replacement = e.get(input_mode)
                    if not isinstance(replacement, str) or not replacement.strip():
                        raise ValueError(f"audio event requires explicit {input_mode} string")
                    e.pop("audio")
                    e["text"] = replacement
            # Allowlist prevents reference transcripts/scoring metadata leakage.
            e = {k: e[k] for k in ("kind", "text", "audio") if k in e}
        result.append(e)
    _validate(result)
    return result


def build(strategy: str, history: History, event: Event | None = None, *,
          instructions: str = "", max_events: int = 24,
          input_mode: str = "native_audio") -> dict:
    """Dispatch one of STRATEGIES; return a fresh request without mutating input."""
    builders = {"user_envelope": user_envelope, "tool_envelope": tool_envelope,
                "reconstructed": reconstructed}
    if strategy not in builders:
        raise ValueError(f"strategy must be one of {STRATEGIES}")
    events = normalize_events(history, event, input_mode=input_mode)
    return builders[strategy](events, instructions=instructions, max_events=max_events)
