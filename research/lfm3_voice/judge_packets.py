"""Round two blinded packets for fresh-context judges and the Orchestrator probe.

CPU only. Writes under artifacts/round2/packets/. Packets carry no arm,
component or source labels: judges see what the caller heard (Parakeet text of
the delivered waveform), whether a Handoff fired, and the expectations.

  python3 -B judge_packets.py composite   # per-scenario composite packets
  python3 -B judge_packets.py speech      # script vs delivered recognition
  python3 -B judge_packets.py orchestrator [--heldout heldout2_cases.json]
"""

import json
import random
import sys
from pathlib import Path

import router_organ as router

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "artifacts/round2/packets"


def composite():
    records = {r["point_id"]: r for r in json.loads((ROOT / "artifacts/round2/composite/scored.json").read_text())}
    suite = json.loads((ROOT / "scenarios.json").read_text())
    key = {}
    packets = []
    for i, scenario in enumerate(suite["scenarios"]):
        steps = []
        for step in scenario["steps"]:
            r = records[f"{scenario['id']}/{step['id']}"]
            rid = f"c{i:02d}-{step['id']}"
            key[rid] = r["point_id"]
            steps.append({"record_id": rid, "events": step["events"], "expect": step["expect"],
                          "candidate": {"handoff_fired": r["handoff"],
                                        "spoken_recognized_by_parakeet": [o.get("recognized") for o in r["outputs"]],
                                        "silent": not r["outputs"]}})
        packets.append({"scenario": f"s{i:02d}", "steps": steps})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "composite.json").write_text(json.dumps(packets, indent=1))
    (OUT / "composite-key.json").write_text(json.dumps(key, indent=1))
    print(len(packets), "scenarios")


def speech():
    rows = json.loads((ROOT / "artifacts/round2/speech/scored.json").read_text())
    verbatim = {r["case_id"]: r for r in rows if r["arm"] == "verbatim_tts_male"}
    items, key = [], {}
    for r in rows:
        # raw candidate (before host check) and what reached the caller
        delivered = r if r["arm"] == "verbatim_tts_male" or r.get("host_pass") else verbatim[r["case_id"]]
        for stage, src in (("raw", r), ("delivered", delivered)):
            if r["arm"] == "verbatim_tts_male" and stage == "delivered":
                continue
            items.append({"approved_script": r["script"].replace("[[", "").replace("]]", ""),
                          "recognized_audio": src.get("recognized"), "_key": (r["case_id"], r["arm"], r.get("repeat"), stage)})
    random.Random(0).shuffle(items)
    for n, item in enumerate(items):
        rid = f"sp{n:03d}"
        key[rid] = item.pop("_key")
        item["record_id"] = rid
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "speech.json").write_text(json.dumps(items, indent=1))
    (OUT / "speech-key.json").write_text(json.dumps(key, indent=1))
    print(len(items), "speech items")


def orchestrator(heldout=None):
    """Every caller turn as a Handoff: ledger cut plus Orchestrator-known state."""
    cases = []
    if heldout is None:
        points = router.caller_points(json.loads((ROOT / "scenarios.json").read_text()))
        stt = {t: v["stt_text"] for t, v in router.load_frozen().items()}
        for p in points:
            s = p["snapshot"]
            cases.append({"_id": p["point_id"], "_gold": p["handoff"],
                          "orchestrator_state": {"pending_handoffs": s["pending_handoffs"], "instructions": s["instructions"],
                                                 "known_facts": s["known_facts"]},
                          "transcript_cut": [{"speaker": sp, "text": stt.get(t, t) if sp == "caller" else t}
                                             for sp, t in s["ledger_prior"]] + [{"speaker": "caller", "text": stt[p["caller_text"]]}]})
        name = "orchestrator-main"
    else:
        for c in json.loads(Path(heldout).read_text())["cases"]:
            s = c["snapshot"]
            cases.append({"_id": c["id"], "_gold": c["handoff"],
                          "orchestrator_state": {"pending_handoffs": s.get("pending_handoffs", []),
                                                 "instructions": s.get("instructions", []), "known_facts": s.get("known_facts", [])},
                          "transcript_cut": s.get("ledger", []) + [{"speaker": "caller", "text": c["caller"]}]})
        name = "orchestrator-heldout"
    random.Random(1).shuffle(cases)
    key = {}
    for n, c in enumerate(cases):
        rid = f"h{n:03d}"
        key[rid] = {"id": c.pop("_id"), "gold_handoff": c.pop("_gold")}
        c["handoff_id"] = rid
    # One file per Handoff: each fresh Orchestrator agent reads only its own entry.
    directory = OUT / name
    directory.mkdir(parents=True, exist_ok=True)
    for c in cases:
        (directory / f"{c['handoff_id']}.json").write_text(json.dumps(c, indent=1))
    (OUT / f"{name}-key.json").write_text(json.dumps(key, indent=1))
    print(len(cases), name)


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "composite":
        composite()
    elif command == "speech":
        speech()
    elif command == "orchestrator":
        orchestrator(sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--heldout" else None)
