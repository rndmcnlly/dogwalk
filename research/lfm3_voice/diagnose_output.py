# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.28,<1", "websockets>=14,<17"]
# ///
"""Compare output-mode controls before running another full matrix."""
import asyncio
import json
from pathlib import Path
from harness import Artifacts, Runtime, audio_part
import strategies


async def main():
    root = Path(__file__).resolve().parent
    artifacts = Artifacts(root / "artifacts/diagnosis-output-01")
    runtime = Runtime("http://127.0.0.1:18391", artifacts, timeout=20)
    wav = root / "artifacts/harness-smoke/cache/c50820bf706d6103d993b8ee737dfca9dada0062bca61620dcd4795bf53ef5d7.wav"
    results = []
    try:
      for input_name, event in (("social_text", {"kind": "caller", "text": "Thank you, how are you today?"}),
                                ("social_audio", {"kind": "caller", "audio": audio_part(wav)["input_audio"]}),
                                ("host_commentary", {"kind": "commentary", "text": "The requested scan found no broken links. Nothing was changed."})):
        for recipe in ("official", "official_no_audio_start", "text_recipe", "text_recipe_no_modes"):
            fragment = strategies.build("user_envelope", [], event)
            if recipe.startswith("text_recipe"):
                fragment["messages"][0]["content"] = fragment["messages"][0]["content"].replace(strategies.TASK_PREFIX, "Respond with text or function calls.\n\n", 1)
            if recipe == "official_no_audio_start":
                fragment["logit_bias"] = {"125012": -100}
            elif recipe == "text_recipe_no_modes":
                fragment["logit_bias"] = {"125012": -100, "125015": -100}
            fragment["max_tokens"] = 128
            reply = await runtime.chat(fragment["messages"], fragment=fragment)
            row = {"input": input_name, "recipe": recipe, "text": reply["predicted_text"], "calls": reply["calls"],
                   "status": reply["status"], "usage": (reply.get("body") or {}).get("usage"), "prefix": reply["prefix"]}
            results.append(row)
            print(json.dumps(row), flush=True)
      fragment = strategies.build("user_envelope", [], {"kind": "caller", "text": "Thank you, how are you today?"})
      template = await runtime.request("/apply-template", fragment)
      prompt = template["body"]["prompt"]
      raw = await runtime.request("/completion", {"prompt": prompt, "temperature": 0, "n_predict": 32,
                                                 "special": True, "n_probs": 1}, label="raw-completion")
      artifacts.save("summary.json", results)
      print(json.dumps({"raw_completion": raw["body"]}))
    finally:
      await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
