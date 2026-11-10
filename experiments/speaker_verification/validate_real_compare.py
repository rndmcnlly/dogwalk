# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["numpy==2.2.6", "scipy==1.16.3"]
# ///
"""Validate private phone outputs and optional rerun. Prints only aggregate checks."""
import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import stat

import numpy as np
from real_compare import RUNTIME, MODES, private_path, window, write


def validate(path, rerun):
    os.umask(0o077)
    path=private_path(path)
    r=json.loads(path.read_text())
    assert r["counts"] == dict(calls=3,clips=18,full_retained=18,short_retained=0,context3_retained=18)
    assert [c["mode"] for c in r["calls"]]==MODES
    assert [c["created"] for c in r["calls"]]==sorted(c["created"] for c in r["calls"])
    assert len(set(c["source_sha256"] for c in r["clips"]))==18
    assert len(r["processed_sha256"])==36
    assert set(r["models"])=={"ecapa","cam-pt","redim-b3"}
    signatures=[]
    max_reconstruction_delta=0.
    for name,m in r["models"].items():
        assert len(m["pairs"])==306 and all(p["genuine"] for p in m["pairs"])
        assert len(m["audio"])==36
        ep=private_path(Path(m["embedding_file"]))
        assert hashlib.sha256(ep.read_bytes()).hexdigest()==m["embeddings_sha256"]
        assert stat.S_IMODE(ep.stat().st_mode)==0o600
        with np.load(ep,allow_pickle=False) as embs:
            assert set(embs.files)==set(r["processed_sha256"])
            for e in embs.values():
                assert np.isfinite(e).all() and abs(float(np.linalg.norm(e))-1)<1e-6
            for p in m["pairs"]:
                score=float(np.dot(embs[p["a"]+"/"+p["duration"]],embs[p["b"]+"/"+p["duration"]]))
                delta=abs(score-p["cosine"])
                assert delta<1e-6
                max_reconstruction_delta=max(max_reconstruction_delta,delta)
        signatures.append([{k:v for k,v in p.items() if k!="cosine"} for p in m["pairs"]])
        for duration in ["full_trimmed","context3"]:
            rows=[p for p in m["pairs"] if p["duration"]==duration]
            assert len(rows)==153
            for a in MODES:
                for b in MODES:
                    s=m["summary"][duration]["mode_matrix"][a][b]
                    values=[p["cosine"] for p in rows if sorted([p["mode_a"],p["mode_b"]])==sorted([a,b])]
                    assert len(values)==(15 if a==b else 36)
                    assert s["median"]==float(np.median(values)) and s["min"]==min(values) and s["max"]==max(values)
    assert signatures[0]==signatures[1]==signatures[2]
    assert stat.S_IMODE(path.stat().st_mode)==0o600
    result=dict(status="passed",calls=3,unique_clips=18,models=3,identical_inputs=36,
        genuine_pairs_per_model=306,short_speech_excluded=18,
        maximum_score_reconstruction_delta=max_reconstruction_delta,
        report_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    if rerun:
        other=json.loads(private_path(rerun).read_text())
        for k in ["script_sha256","adapter_script_sha256","processed_sha256","counts","calls"]:
            assert r[k]==other[k]
        assert set(other["models"])==set(r["models"])
        deltas=[]
        for name,m in r["models"].items():
            om=other["models"][name]
            assert len(om["pairs"])==306
            assert [{k:v for k,v in p.items() if k!="cosine"} for p in om["pairs"]]==signatures[0]
            for k in ["checkpoint_sha256","revision"]:
                assert m[k]==om[k]
            deltas += [abs(a["cosine"]-b["cosine"]) for a,b in zip(m["pairs"],om["pairs"])]
        assert max(deltas)<1e-6
        result["rerun_max_cosine_delta"]=max(deltas)
        result["rerun_sha256"]=hashlib.sha256(rerun.read_bytes()).hexdigest()
    # Offline gate checks, not biometric claims: silence and opening beep cannot
    # become a retained utterance, while a broad modulated signal is retained.
    sr=8000
    silence=np.zeros(sr*10,dtype=np.float32)
    assert not window(silence,sr)[0]
    beep=silence.copy()
    beep[:1200]=.2*np.sin(2*np.pi*880*np.arange(1200)/sr)
    assert not window(beep,sr)[0]
    gen=np.random.default_rng(1701)
    speech_proxy=silence.copy()
    speech_proxy[sr:sr*5]=gen.normal(0,.03,sr*4).astype(np.float32)
    windows,_=window(speech_proxy,sr)
    assert set(windows)=={"full_trimmed","context3","speech3"}
    assert len(windows["speech3"])==3*sr
    result["energy_gate_unit_checks"]="silence and opening beep rejected; long broadband proxy supports 3 s window"
    write(path.with_name("validation.json"),result)
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report",type=Path,default=RUNTIME/"speaker_comparison/run.json")
    p.add_argument("--rerun",type=Path)
    args=p.parse_args()
    validate(args.report,args.rerun)
