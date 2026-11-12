# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.28,<1", "websockets>=14,<17"]
# ///
"""Bounded serial serving-path diagnosis. Explicitly invokes the local model."""
import asyncio
import json
from pathlib import Path

from harness import Artifacts, Runtime, audio_part, TOOLS
from strategies import TASK_PREFIX


async def main():
    root = Path(__file__).resolve().parent
    artifacts = Artifacts(root / "artifacts" / "diagnosis-01")
    runtime = Runtime("http://127.0.0.1:18391", artifacts, timeout=30)
    audio = root / "artifacts/harness-smoke/cache/0ba1c622ed9ac29d3dd87b6e0639cdfd4819a07e1046feb5d285120d946caf95.wav"
    tokens = {}
    results = []
    try:
        for token in ("<|audio_start|>", "<|mixed_start|>", "<|text_start|>", "<|audio_end|>"):
            response = await runtime.request("/tokenize", {"content": token, "add_special": False}, label="tokenize")
            tokens[token] = (response.get("body") or {}).get("tokens")
        artifacts.save("tokens.json", tokens)
        policy = "You are a voice frontend. You cannot inspect or fix code. For any work request, call delegate with no arguments. Do not solve the task yourself."
        for prefix_name, prefix in (("official_tool", TASK_PREFIX), ("text_only", ""),
                                    ("function_only", "Respond with function calls.\n\n")):
            for input_name, content in (("realistic_text", "Please inspect the code and fix the login bug."),
                                        ("explicit_text", "Call delegate with no arguments now."),
                                        ("realistic_audio", [audio_part(audio)])):
                messages = [{"role": "system", "content": prefix + policy}, {"role": "user", "content": content}]
                if input_name == "explicit_text":
                    await runtime.request("/apply-template", {"messages": messages, "tools": TOOLS}, label=prefix_name + "-template")
                for suppress in (False, True):
                    biases = {str(t): -100 for token, ids in tokens.items() if token in ("<|audio_start|>", "<|mixed_start|>") for t in (ids or [])}
                    fragment = {"tools": TOOLS, "tool_choice": "auto", "max_tokens": 128}
                    if suppress:
                        fragment["logit_bias"] = biases
                    reply = await runtime.chat(messages, fragment=fragment)
                    result = {"prefix": prefix_name, "input": input_name, "suppress_audio_tokens": suppress,
                              "status": reply["status"], "text": reply["predicted_text"], "calls": reply["calls"],
                              "usage": (reply.get("body") or {}).get("usage"), "elapsed_s": reply["elapsed_s"],
                              "artifact_prefix": reply["prefix"]}
                    results.append(result)
                    print(json.dumps(result), flush=True)
        artifacts.save("summary.json", results)
    finally:
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
