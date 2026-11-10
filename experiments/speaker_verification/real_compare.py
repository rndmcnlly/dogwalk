# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = [
#   "speechbrain==1.0.3", "torch==2.8.0", "torchaudio==2.8.0",
#   "huggingface-hub==0.36.0", "numpy==2.2.6", "scipy==1.16.3",
#   "soundfile==0.14.0", "onnxruntime==1.23.2",
# ]
# ///
"""Local, genuine-only phone probe. Detailed outputs MUST remain outside Git.

Default output: private collector runtime/speaker_comparison/run.json.
No collector/production changes, thresholds, remote audio calls or enrollment.
"""
import argparse
import itertools
import json
import os
from pathlib import Path
import platform
import time

from compare import load_model
from experiment import sha, versions

RUNTIME = Path.home() / "Library/Application Support/Dogwalk/phone_voice_collection"
MODES = ["buds", "speakerphone", "handset_reported"]
MODE_EVIDENCE = ["Pixel Buds in-ear Bluetooth on Android, user report",
                 "speakerphone, user report",
                 "normal handset to ear per instruction; Bluetooth toggle not independently confirmed"]


def private_path(path):
    path = path.expanduser().resolve()
    if not path.is_relative_to(RUNTIME.resolve()) or path == RUNTIME.resolve():
        raise ValueError("Outputs must be inside the private collector runtime directory")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    return path


def write(path, data):
    path = private_path(path)
    tmp = path.with_suffix(path.suffix + ".partial")
    with open(tmp, "w") as f:
        os.chmod(tmp, 0o600)
        json.dump(data, f, indent=2)
        f.write("\n")
    tmp.replace(path)


def window(wave, sr):
    """Shared transparent energy gate, NOT a speech VAD or acoustic audit.

    Discard first 500 ms (collector plays a 150 ms 880 Hz beep after stream start).
    Nonoverlap 20 ms frames, RMS threshold max(-55 dBFS, p20+12 dB, p95-30 dB).
    Retain active runs >=100 ms, trim endpoints with 100 ms padding; keep pauses.
    speech3: most active contiguous 3 s, >=2.4 s energy-active, >=3 s active
    overall. context3: same source window regardless of active duration, explicitly
    includes silence and is NOT 3 s voiced speech. No concatenation/padding or
    embedding-driven window selection.
    """
    import numpy as np
    from scipy.ndimage import label
    skip = int(.5*sr)
    x = wave[skip:]
    n = int(.02*sr)
    frames = x[:len(x)//n*n].reshape(-1, n)
    db = 20*np.log10(np.maximum(np.sqrt(np.mean(frames.astype("float64")**2, axis=1)), 1e-9))
    threshold = max(-55., float(np.percentile(db, 20))+12., float(np.percentile(db, 95))-30.)
    active = db > threshold
    groups, count = label(active)
    for k in range(1, count+1):
        mask = groups == k
        if mask.sum() < 5:
            active[mask] = False
    indices = np.flatnonzero(active)
    meta = dict(raw_seconds=len(wave)/sr, skip_seconds=.5, threshold_dbfs=threshold,
        frame_dbfs_percentiles={str(p):float(np.percentile(db,p)) for p in [0,20,50,95,100]},
        active_seconds=float(active.sum()*.02), active_mask=active.tolist(),
        raw_peak=float(np.max(np.abs(wave))), clipped_fraction=float(np.mean(np.abs(wave) >= .999)))
    if len(indices) == 0:
        return {}, dict(meta, excluded="no retained energy run")
    a = max(0, (int(indices[0])-5)*n)
    b = min(len(x), (int(indices[-1])+6)*n)
    data = {"full_trimmed": x[a:b]}
    meta.update(full_bounds_samples=[skip+a, skip+b], trimmed_seconds=(b-a)/sr,
                active_span_seconds=(int(indices[-1])-int(indices[0])+1)*.02)
    # Most energy-active window, ties resolved toward earliest, not most energy.
    if len(active) >= 150:
        sums = np.convolve(active.astype(int), np.ones(150,dtype=int), "valid")
        start = int(sums.argmax())
        data["context3"] = x[start*n:start*n+3*sr]
        meta.update(context3_bounds_samples=[skip+start*n,skip+start*n+3*sr],
                    context3_active_seconds=float(sums[start]*.02))
        if active.sum() >= 150 and sums[start] >= 120:
            data["speech3"] = x[start*n:start*n+3*sr]
            meta.update(short_bounds_samples=[skip+start*n, skip+start*n+3*sr],
                        short_active_seconds=float(sums[start]*.02))
    if "speech3" not in data:
        meta["short_excluded"] = "less than 3 s total energy-active or no 3 s window with 2.4 s energy-active"
    return data, meta


def stats(values):
    import numpy as np
    if not values:
        return dict(n=0, min=None, median=None, max=None)
    return dict(n=len(values), min=float(min(values)), median=float(np.median(values)), max=float(max(values)))


def summarize(pairs, ids):
    result = {}
    for duration in ["full_trimmed", "context3", "speech3"]:
        group = [p for p in pairs if p["duration"] == duration]
        matrix = {}
        for a in MODES:
            matrix[a] = {}
            for b in MODES:
                matrix[a][b] = stats([p["cosine"] for p in group if sorted([p["mode_a"],p["mode_b"]]) == sorted([a,b])])
        same_phrase = [p["cosine"] for p in group if p["mode_a"] != p["mode_b"] and p["same_phrase"]]
        different_phrase = [p["cosine"] for p in group if p["mode_a"] != p["mode_b"] and not p["same_phrase"]]
        within = [p["cosine"] for p in group if p["mode_a"] == p["mode_b"]]
        cross = [p["cosine"] for p in group if p["mode_a"] != p["mode_b"]]
        w, c = stats(within), stats(cross)
        result[duration] = dict(mode_matrix=matrix, within=w, cross=c,
            cross_minus_within_median=None if not within or not cross else c["median"]-w["median"],
            cross_same_phrase=stats(same_phrase), cross_different_phrase=stats(different_phrase))
    return result


def run(args):
    import hashlib
    import numpy as np
    import soundfile as sf
    import torch
    from scipy.signal import resample_poly
    from math import gcd
    os.umask(0o077)
    output = private_path(args.output)
    if output.exists():
        raise FileExistsError("Preserve existing run; pass a different private --output")
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    all_calls = [(p,json.loads(p.read_text())) for p in (RUNTIME/"calls").glob("*.json")]
    calls = sorted([(p,r) for p,r in all_calls if r.get("synthetic") is False], key=lambda x:x[1]["created"])
    assert len(calls) == 3, "Expected exactly three nonsynthetic calls; review chronology before extending"
    report = dict(identity="Adam, all three calls manually confirmed by user in conversation; collector labels unconfirmed",
        mode_evidence=dict(zip(MODES,MODE_EVIDENCE)), synthetic_calls_excluded=sum(r.get("synthetic") is True for _,r in all_calls),
        calls=[], clips=[], preprocessing=window.__doc__, models={}, device="cpu", threads=4,
        interop_threads=1, repeats=args.repeats, python=platform.python_version(), platform=platform.platform(),
        script_sha256=sha(Path(__file__)), adapter_script_sha256=sha(Path(__file__).with_name("compare.py")),
        versions=versions(["torch","torchaudio","numpy","scipy","soundfile","speechbrain","onnxruntime","huggingface-hub"]))
    data = {}
    t = time.perf_counter()
    for mode,(p,call) in zip(MODES,calls):
        report["calls"].append(dict(mode=mode, metadata_file=str(p), metadata_sha256=sha(p), created=call["created"],
            call_sid=call["call_sid"], metadata_speaker=call.get("speaker")))
        streams = sorted(call["streams"].values(), key=lambda s:s["round"])
        assert len(streams)==6 and [s["round"] for s in streams]==list(range(6))
        for stream in streams:
            assert stream["complete"] is True
            path = (RUNTIME / "audio" / stream["file"]).resolve()
            assert path.is_relative_to(RUNTIME.resolve()) and sha(path)==stream["sha256"]
            wave,sr = sf.read(path,dtype="float32")
            assert sr==8000 and wave.ndim==1 and np.isfinite(wave).all()
            windows, meta = window(wave,sr)
            key = f"{mode}-{stream['round']}"
            meta.update(id=key, mode=mode, round=stream["round"], path=str(path), source_sha256=sha(path),
                        phrase=stream["phrase"], inserted_silence_ms=stream["inserted_silence_ms"])
            report["clips"].append(meta)
            for duration, x in windows.items():
                g = gcd(sr,16000)
                y = np.ascontiguousarray(resample_poly(x,16000//g,sr//g).astype(np.float32))
                data[(key,duration)] = y
    report["preprocess_seconds"] = time.perf_counter()-t
    report["processed_sha256"] = {"/".join(k):hashlib.sha256(x.tobytes()).hexdigest() for k,x in data.items()}
    report["counts"] = dict(calls=3, clips=18, full_retained=sum(k[1]=="full_trimmed" for k in data),
                            short_retained=sum(k[1]=="speech3" for k in data),
                            context3_retained=sum(k[1]=="context3" for k in data))
    write(output,report)
    if args.inspect:
        print(json.dumps(dict(counts=report["counts"], modes={mode:dict(
            active_seconds=stats([c["active_seconds"] for c in report["clips"] if c["mode"]==mode]),
            trimmed_seconds=stats([c.get("trimmed_seconds",0) for c in report["clips"] if c["mode"]==mode])) for mode in MODES}),indent=2))
        return
    by_id={c["id"]:c for c in report["clips"]}
    for name in ["ecapa","cam-pt","redim-b3"]:
        t = time.perf_counter()
        embed,meta = load_model(name)
        meta["load_seconds"] = time.perf_counter()-t
        with torch.inference_mode():
            embed(next(iter(data.values())))
        embeddings,timing = {},[]
        for key,x in data.items():
            ts=[]
            for _ in range(args.repeats):
                t=time.perf_counter()
                with torch.inference_mode():
                    e=embed(x).reshape(-1).float()
                    assert torch.isfinite(e).all() and torch.linalg.vector_norm(e)>0
                    e=torch.nn.functional.normalize(e,dim=0)
                ts.append(time.perf_counter()-t)
                if key in embeddings:
                    assert torch.allclose(e,embeddings[key],atol=1e-6,rtol=1e-5)
                embeddings[key]=e
            timing.append(dict(id=key[0],duration=key[1],samples=len(x),seconds=ts))
        pairs=[]
        for duration in ["full_trimmed","context3","speech3"]:
            ids=[key[0] for key in data if key[1]==duration]
            for a,b in itertools.combinations(ids,2):
                x,y=by_id[a],by_id[b]
                pairs.append(dict(a=a,b=b,mode_a=x["mode"],mode_b=y["mode"],duration=duration,
                    genuine=True,same_phrase=x["phrase"]==y["phrase"],
                    cosine=float(torch.dot(embeddings[(a,duration)],embeddings[(b,duration)]))))
        emb_path=private_path(output.with_name(output.stem+f"_{name}_embeddings.npz"))
        np.savez(emb_path,**{"/".join(k):v.numpy() for k,v in embeddings.items()})
        emb_path.chmod(0o600)
        meta.update(pairs=pairs,audio=timing,summary=summarize(pairs,by_id), embeddings_sha256=sha(emb_path),
                    embedding_file=str(emb_path),timing={d:stats([t for a in timing if a["duration"]==d for t in a["seconds"]])
                    for d in ["full_trimmed","context3","speech3"]})
        report["models"][name]=meta
        write(output,report)
        print(name,"completed",flush=True)
        del embed,embeddings
    print(json.dumps(dict(counts=report["counts"],results={n:dict(summary=m["summary"],timing=m["timing"]) for n,m in report["models"].items()}),indent=2))


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,default=RUNTIME/"speaker_comparison/run.json")
    p.add_argument("--inspect",action="store_true")
    p.add_argument("--repeats",type=int,default=3)
    a=p.parse_args()
    assert a.repeats>=1
    run(a)
