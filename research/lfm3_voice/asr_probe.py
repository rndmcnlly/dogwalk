# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.28,<1", "websockets>=14,<17"]
# ///
"""Paired same-waveform ASR: clean/narrowband × raw/explicit-16k input."""
import argparse
import asyncio
import json
from pathlib import Path

from harness import Artifacts, Runtime, audio_part


async def run(args):
    if (args.output / "summary.json").exists():
        raise ValueError("Use a fresh artifact directory")
    runtime = Runtime("http://127.0.0.1:18391", Artifacts(args.output.resolve()), timeout=15)
    frozen = json.loads(args.frozen_callers.read_text())
    results = []
    try:
        for record in frozen.values():
            if "preprocessing" not in record:
                continue
            for condition, path in (("clean", record["synthesis"]["audio_path"]),
                                    ("pstn_8khz", record["delivered_audio_path"])):
                for normalize in (False, True):
                    if normalize:
                        reply = await runtime.stt(Path(path))
                    else:
                        reply = await runtime.chat([{"role": "system", "content": "Perform ASR in English."},
                                                    {"role": "user", "content": [audio_part(Path(path))]}])
                    row = {"truth": record["synthesis"]["text"], "condition": condition,
                           "explicit_16khz_normalization": normalize, "source_audio_path": path,
                           "response": reply}
                    results.append(row)
                    print(json.dumps({k: row[k] for k in ("condition", "explicit_16khz_normalization", "truth")} |
                                     {"recognized": reply["predicted_text"], "elapsed_s": reply["elapsed_s"]}), flush=True)
        runtime.artifacts.save("summary.json", results)
    finally:
        await runtime.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--frozen-callers", type=Path, required=True)
    asyncio.run(run(p.parse_args()))
