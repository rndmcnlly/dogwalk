"""Standard-library validation of comparison reports; no model downloads."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def validate(path, rerun=None):
    r = json.loads(path.read_text())
    assert len(r["audio_sha256"]) == 9 and len(set(r["audio_sha256"].values())) == 9
    assert len(r["input_sha256"]) == 36
    assert len(r["trials"]) == 90
    assert r["trial_sha256"] == hashlib.sha256(json.dumps(r["trials"], sort_keys=True).encode()).hexdigest()
    baseline = json.loads((ROOT / "scores.json").read_text())
    signature = None
    for name, m in r["models"].items():
        assert m["status"] == "ok", (name, m)
        assert len(m["audio"]) == 36 and len(m["pairs"]) == 180
        assert {a["samples"] for a in m["audio"] if a["duration"] == "first3"} == {48000}
        assert all(len(a["seconds"]) == r["timing_repeats"] for a in m["audio"])
        sig = [{k: v for k,v in p.items() if k != "cosine"} for p in m["pairs"]]
        if signature is None:
            signature = sig
        assert sig == signature, "Models used different trials"
        for duration, conditions in m["summary"].items():
            for condition, s in conditions.items():
                pairs = [p for p in m["pairs"] if p["duration"] == duration and p["condition"] == condition]
                g = [p["cosine"] for p in pairs if p["genuine"]]
                i = [p["cosine"] for p in pairs if not p["genuine"]]
                assert len(g) == s["genuine_n"] and len(i) == s["impostor_n"]
                assert s["sample_gap"] == min(g)-max(i)
                assert s["genuine_min"] == min(g) and s["impostor_max"] == max(i)
                assert all(-1.00001 <= p["cosine"] <= 1.00001 for p in pairs)
    for condition, s in baseline["summary"].items():
        for field, value in s.items():
            assert abs(value-r["models"]["ecapa"]["summary"]["full"][condition][field]) < 1e-7
    checks = dict(status="passed", models=list(r["models"]),
        checks=["nine unique WAV hashes", "36 common processed inputs", "90 identical trials per duration",
                "180 scored pairs per model", "first3 contains exactly 48000 samples",
                "finite bounded cosine", "summary gaps and counts recomputed",
                "full ECAPA matches original scores.json within 1e-7"],
        trial_sha256=r["trial_sha256"], report_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    if rerun:
        other = json.loads(rerun.read_text())
        assert other["script_sha256"] == r["script_sha256"]
        assert other["input_sha256"] == r["input_sha256"]
        assert other["trial_sha256"] == r["trial_sha256"]
        assert set(other["models"]) == set(r["models"])
        for name, m in other["models"].items():
            assert m["status"] == "ok" and len(m["pairs"]) == 180
            assert [{k:v for k,v in p.items() if k != "cosine"} for p in m["pairs"]] == signature
        delta = max(abs(a["cosine"]-b["cosine"])
            for name in r["models"] for a,b in zip(r["models"][name]["pairs"], other["models"][name]["pairs"]))
        assert delta < 1e-6
        checks.update(rerun_max_cosine_delta=delta,
                      rerun_sha256=hashlib.sha256(rerun.read_bytes()).hexdigest())
    print(json.dumps(checks, indent=2))
    (ROOT / "comparison_validation.json").write_text(json.dumps(checks, indent=2)+"\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("report", type=Path, nargs="?", default=ROOT / "comparison.json")
    p.add_argument("--rerun", type=Path)
    a = p.parse_args()
    validate(a.report, a.rerun)
