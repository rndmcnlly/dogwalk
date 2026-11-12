"""Regenerate RESULTS2.json from ignored round-two artifacts. CPU only.

Judge and Orchestrator-probe outputs are the saved workflow return values
(artifacts/round2/judges/*.json); keys map opaque record ids back to points.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
A = ROOT / "artifacts/round2"
STATE_CHANGING = {"start_prompt_turn", "revise_or_redirect_prompt_turn", "cancel_prompt_turn",
                  "close_session", "delete_history", "respond_permission"}


MODELS = {
    "driver_and_router_prompt_author": "anthropic/claude-opus-5-5",
    "heldout_author": "openai/gpt-6.1-sol",
    "composite_judges": "openai/gpt-6.1-sol",
    "speech_judges": "opencode-go/deepseek-v4.1-flash",
    "orchestrator_stand_ins": "anthropic/claude-opus-5-5 (session default), one fresh agent per Handoff entry",
}


def load(path):
    path = A / path
    return json.loads(path.read_text()) if path.exists() else None


def router_main():
    analysis = load("router-main/analysis.json")
    cells = {}
    for key, c in analysis["cells"].items():
        cells[key] = {"threshold": c["operating_point"]["threshold"],
                      "false_at_full_recall": c["operating_point"]["false_handoffs"],
                      "caller_negatives": c["operating_point"]["negatives"],
                      "margin": c["operating_point"]["margin"], "false_points": c["false_at_op"],
                      "naive_half_false": len(c["argmax_false"]), "naive_half_missed": len(c["argmax_missed"]),
                      "median_decision_s": c["median_elapsed_s"], "p95_decision_s": c["p95_elapsed_s"],
                      **({"multiway_argmax_correct": c["multiway"]["argmax_correct"]} if "multiway" in c else {})}
    return {"selected_framing": analysis["selected_framing"], "frozen_thresholds": analysis["frozen_thresholds"],
            "cells": cells, "host_gated_noncaller_negatives": 15}


def judges():
    out = {}
    composite = load("judges/composite.json")
    if composite:
        key = load("packets/composite-key.json")
        recs = [dict(r, point_id=key.get(r["record_id"])) for r in composite]
        out["composite"] = {"records": len(recs),
                            "routing_pass": sum(r["routing"] == "pass" for r in recs),
                            "semantics_pass": sum(r["semantics"] == "pass" for r in recs),
                            "semantics_fail": sorted(r["point_id"] for r in recs if r["semantics"] == "fail"),
                            "semantics_unresolved": sorted(r["point_id"] for r in recs if r["semantics"] == "unresolved"),
                            "critical": sorted(r["point_id"] for r in recs if r["critical_failure"]),
                            "tags": sorted({t for r in recs for t in r["failure_tags"]})}
    speech = load("judges/speech.json")
    if speech:
        key = load("packets/speech-key.json")
        rows = [dict(i, key=key[i["record_id"]]) for i in speech]
        def tally(pred):
            sel = [r for r in rows if pred(r["key"])]
            return {"n": len(sel), "preserved": sum(r["meaning"] == "preserved" for r in sel),
                    "changed": sum(r["meaning"] == "changed" for r in sel),
                    "incomplete": sum(r["meaning"] == "incomplete" for r in sel),
                    "unresolved": sum(r["meaning"] == "unresolved" for r in sel),
                    "reversal": sum(r["reversal"] for r in sel)}
        out["speech"] = {"verbatim": tally(lambda k: k[1] == "verbatim_tts_male"),
                         "glue_raw": tally(lambda k: k[1] == "native_glue" and k[3] == "raw"),
                         "glue_delivered": tally(lambda k: k[1] == "native_glue" and k[3] == "delivered")}
    for name in ("orchestrator-main", "orchestrator-heldout"):
        decisions = load(f"judges/{name}.json")
        if decisions:
            key = load(f"packets/{name}-key.json")
            rows = [dict(d, **key[d["handoff_id"]]) for d in decisions]
            negatives = [r for r in rows if not r["gold_handoff"]]
            positives = [r for r in rows if r["gold_handoff"]]
            wrong = [r["id"] for r in negatives if {o["op"] for o in r["operations"]} & STATE_CHANGING]
            out[name] = {"handoffs": len(rows), "negatives": len(negatives),
                         "negatives_state_changing": wrong,
                         "negatives_read_status_only": sorted(r["id"] for r in negatives if r["operations"] and r["id"] not in wrong),
                         "negatives_no_operation": sum(not r["operations"] for r in negatives),
                         "positives_with_operation": sum(bool(r["operations"]) for r in positives),
                         "positives": len(positives)}
    return out


def main():
    results = {"schema_version": 1, "round": 2, "models": MODELS,
               "heldout_labels": "share the router's written policy (policy-transfer test, not independently derived labels); author blinding is instruction-only",
               "router_main": router_main(),
               "router_heldout": load("router-heldout/summary.json"),
               "speech": load("speech/summary.json"),
               "composite": load("composite/summary.json"),
               "judges": judges()}
    (ROOT / "RESULTS2.json").write_text(json.dumps(results, indent=1) + "\n")
    print(json.dumps(results["judges"], indent=1)[:4000])


if __name__ == "__main__":
    main()
