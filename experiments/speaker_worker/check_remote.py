# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["numpy==2.2.6"]
# ///
"""Only transmit fixtures created from the existing synthetic corpus by export.py."""
import argparse
import itertools
import json
from pathlib import Path
import time
import urllib.request
import urllib.error

import numpy as np

ROOT = Path(__file__).resolve().parent


def normalize(x):
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    return x / np.linalg.norm(x)


def metrics(a,b):
    a,b=np.asarray(a,dtype=np.float64),np.asarray(b,dtype=np.float64)
    an,bn=normalize(a),normalize(b)
    return dict(raw_max_abs=float(np.max(np.abs(a-b))),normalized_max_abs=float(np.max(np.abs(an-bn))),cosine=float(an@bn))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--token-file",required=True,type=Path)
    parser.add_argument("--output",type=Path,default=ROOT / "remote_results.json")
    parser.add_argument("--waveform",action="store_true")
    args=parser.parse_args()
    token=args.token_file.read_text().strip()
    fixture=np.load(ROOT / "artifacts/fixtures.npz")
    endpoint="/waveform" if args.waveform else "/embed"
    payloads=fixture["waveforms"] if args.waveform else fixture["features"]
    local_onnx=np.load(ROOT / "artifacts/waveform_onnx.npy") if args.waveform else fixture["onnx"]
    def call(path,data=None,auth=True,content_type="application/octet-stream"):
        headers={"Content-Type":content_type,"User-Agent":"Dogwalk-speaker-worker-research/1.0"}
        if auth: headers["Authorization"]=f"Bearer {token}"
        req=urllib.request.Request(args.url.rstrip("/")+path,data=data,headers=headers)
        tick=time.perf_counter()
        try:
            with urllib.request.urlopen(req,timeout=120) as response:
                return response.status,json.loads(response.read()),time.perf_counter()-tick
        except urllib.error.HTTPError as error:
            body=error.read().decode()
            try: body=json.loads(body)
            except json.JSONDecodeError: body=body[:600]
            return error.code,body,time.perf_counter()-tick
        except urllib.error.URLError as error:
            return 0,{"transport_error":str(error.reason)},time.perf_counter()-tick
    checks={}
    status,health_body,_=call("/health",auth=False)
    if status!=200:
        failure=dict(url=args.url,passed=False,stage="health",status=status,failure=health_body,
                     synthetic_only=True,inference_attempted=False)
        args.output.write_text(json.dumps(failure,indent=2)+"\n")
        print(json.dumps(failure,indent=2))
        raise SystemExit(1)
    checks["health"]=status
    for name,path,data,auth,typ,expected in [
        ("unauthorized",endpoint,b"",False,"application/octet-stream",401),
        ("bad_size",endpoint,b"short",True,"application/octet-stream",400),
        ("too_large",endpoint,b"\0"*(payloads[0].nbytes+4),True,"application/octet-stream",400),
        ("bad_type",endpoint,b"",True,"application/json",415),
        ("nonfinite",endpoint,np.full(payloads[0].shape,np.nan,dtype="<f4").tobytes(),True,"application/octet-stream",400),
    ]:
        status,_,_=call(path,data,auth,typ)
        assert status==expected,(name,status)
        checks[name]=status
    report=dict(url=args.url,checks=checks,inputs=[],synthetic_only=True,worker_compute_clock_reliable=False,
                scope="16 kHz float32 waveform to embedding" if args.waveform else "features to embedding, not waveform frontend",passed=False)
    outputs=[]
    for index,(label,payload,pt,onnx) in enumerate(zip(fixture["labels"],payloads,fixture["pytorch"],local_onnx)):
        status,body,seconds=call(endpoint,payload.astype("<f4").tobytes())
        row=dict(label=str(label),status=status,roundtrip_seconds=seconds)
        if status!=200:
            row["failure"]=body
            report["inputs"].append(row)
            break
        raw=np.asarray(body.pop("raw"))
        normalized=np.asarray(body.pop("normalized"))
        if args.waveform:
            frontend=np.asarray(body.pop("frontendFeatures")).reshape(fixture["features"][index].shape)
            row["frontend_feature_max_abs"]=float(np.max(np.abs(frontend-fixture["features"][index])))
        row.update(body,worker_vs_pytorch=metrics(pt,raw),worker_vs_local_onnx=metrics(onnx,raw),
                   worker_normalization_max_abs=float(np.max(np.abs(normalize(raw)-normalized))))
        outputs.append(raw)
        report["inputs"].append(row)
    if len(outputs)==len(fixture["labels"]):
        diffs=[abs(normalize(fixture["pytorch"][i])@normalize(fixture["pytorch"][j])-normalize(outputs[i])@normalize(outputs[j]))
               for i,j in itertools.combinations(range(len(outputs)),2)]
        report.update(pair_count=len(diffs),max_score_abs_difference=max(diffs))
        report["passed"]=bool(all(r["worker_vs_pytorch"]["raw_max_abs"]<1e-3 and
            r["worker_vs_pytorch"]["normalized_max_abs"]<1e-5 and r["worker_vs_pytorch"]["cosine"]>0.999999
            for r in report["inputs"]) and max(diffs)<1e-5)
    args.output.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
    if not report["passed"]: raise SystemExit(1)


if __name__=="__main__": main()
