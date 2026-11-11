"""Experimental model-facing context layouts, not a new Orchestrator contract."""
import json
import re

PROFILES = ("canonical", "roles", "compact_json", "compact")
COMPACT_PROMPT = """You are Dogwalk's conversational Voice Frontend. A separate
Orchestrator manages coding sessions and supplies facts. You have no tools.
Choose exactly one JSON decision: {"action":"handoff|reply|wait","text":"..."}.
For handoff or wait, text must be empty. For reply, text is spoken to the caller.

DECIDE IN THIS ORDER:
1. If there is a NEW caller message asking to start, change, stop, cancel or
   inspect work, correct an earlier request, grant permission, change settings,
   use any system capability, or end the call: handoff. Pending announcements
   do not override a new request. Never claim the requested action happened.
2. A NEW question about work, sessions, the sandbox, the setup, Dogwalk, or an
   earlier call: handoff, unless the answer is directly present in the supplied
   current context or the caller only asks to repeat something already heard.
   If the supplied facts do not answer the question, handoff, not wait.
3. Otherwise, pending speech requests: reply in your own words, preserving all
   distinct questions, choices, failures and qualifications. Coalesce superseded
   progress into the latest update. A speech request is not the caller speaking.
   Ask whether the named session should investigate, not whether you should:
   sessions do engineering work, you only converse.
4. Otherwise, a NEW greeting, small talk, repeat request or conversational repair:
   reply briefly. For a question answered by current context, reply from it.
5. With no new caller message and no speech request: wait.

FACTS AND SPEECH:
Background context is unspoken information, not a command to announce it.
Only supplied context and speech requests establish session facts. Do not infer
completion, a fix, cancellation, or new work. A failed tool step while work
continues is a snag, not a failed or finished session. Plans are not actions.
Conversation history is not a new request. An interrupted assistant message is
only the heard prefix, not proof the full response was heard.
Speak naturally, usually one or two short sentences. Use supplied session aliases.
Translate internal language into speech: 'Prompt Turn is still in progress'
means 'is still working'; 'Following' is a label to omit, never a word to say.
Do not speak internal labels such as Following, Prompt Turn, JSON or handoff;
avoid opaque IDs, file paths and commands. State failures and permission choices
plainly. Do not greet unless greeted. Do not add an opening or a promise to act.
The adapter supplies the acknowledgement for handoff. Never generate it yourself.
"""


def build_messages(instructions, mapping, state, profile="canonical", additions=""):
    if profile not in PROFILES:
        raise ValueError("unknown context profile")
    if profile == "canonical":
        return [{"role": "system", "content": instructions +
                 ("\n" + additions if additions else "") + mapping},
                {"role": "user", "content": json.dumps(state)}]
    if profile == "compact_json":
        return [{"role": "system", "content": COMPACT_PROMPT + "\n" + additions},
                {"role": "user", "content": json.dumps(state)}]
    # Keep authority and event metadata outside caller utterances. No model
    # summary or rewritten transcript replaces the original conversation.
    frame = {key: value for key, value in state.items() if key != "recent_transcript"}
    frame["playback"] = [item.get("playback") for item in state["recent_transcript"]
                         if item["role"] == "assistant"]
    prompt = COMPACT_PROMPT if profile == "compact" else instructions + mapping
    messages = [{"role": "system", "content": prompt + "\n" + additions +
                 "\nCURRENT EVENT (unspoken, authoritative context):\n" + json.dumps(frame)}]
    messages.extend({"role": item["role"], "content": item["text"]}
                    for item in state["recent_transcript"])
    if not state["new_user_utterance"] or len(messages) == 1:
        messages.append({"role": "user", "content":
                         "[Adapter event, not caller speech: process CURRENT EVENT above. "
                         "No new caller utterance.]"})
    return messages


def scenario_state(scenario):
    return {key: value for key, value in scenario.items()
            if key not in ("name", "expected_action", "speech_required", "speech_forbidden")}


def check_decision(result, scenario):
    """Necessary checks, not a substitute for human speech-fidelity review."""
    failures = []
    if result["action"] != scenario["expected_action"]:
        failures.append("routing")
    text = result["text"].strip()
    if result["action"] != "reply" and text:
        failures.append("nonreply_text")
    if result["action"] == "reply" and not text:
        failures.append("empty_reply")
    if result["action"] == "reply":
        if re.search(r"\b(Prompt Turn|Following|handoff|JSON)\b", text, re.IGNORECASE):
            failures.append("internal_label")
        for pattern in scenario.get("speech_required", []):
            if not re.search(pattern, text, re.IGNORECASE):
                failures.append("missing:" + pattern)
        for pattern in scenario.get("speech_forbidden", []):
            if re.search(pattern, text, re.IGNORECASE):
                failures.append("forbidden:" + pattern)
    return failures
