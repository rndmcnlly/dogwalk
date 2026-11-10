# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["torch==2.8.0", "torchaudio==2.8.0", "numpy==2.2.6", "scipy==1.16.3", "soundfile==0.14.0", "onnx==1.19.1", "onnxruntime==1.23.2"]
# ///
"""Export cached, hash-verified B3. Only existing synthetic Breeze audio is read."""
import copy
import argparse
import hashlib
import itertools
import json
import sys
from pathlib import Path
from math import gcd
import time

import numpy as np
import onnx
import onnxruntime as ort
import soundfile as sf
import torch
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parent
OLD = ROOT.parent / "speaker_verification"
ART = ROOT / "artifacts"
REV = "c5bbe0b76e37df698c403f8844e41304ceab6307"
HASH = "02d14efdc184573873b5c3f62bea847fcdb6919fbba19a8da7af7c1ba36b76a2"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(x):
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    return x / np.linalg.norm(x)


def metrics(a, b):
    an, bn = normalize(a), normalize(b)
    return dict(raw_max_abs=float(np.max(np.abs(a-b))), normalized_max_abs=float(np.max(np.abs(an-bn))),
                cosine=float(an @ bn))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--waveform", action="store_true", help="Also export native 16 kHz waveform frontend")
    args = parser.parse_args()
    ART.mkdir(exist_ok=True)
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    source = OLD / "artifacts" / f"redimnet2-{REV}"
    sys.path.insert(0, str(source))
    from redimnet2.redimnet2 import ReDimNet2Wrap
    sys.path.insert(0, str(OLD))
    from experiment import telephone
    weight = OLD / "artifacts/b3-vox2-lm.pt"
    assert sha(weight) == HASH
    state = torch.load(weight, map_location="cpu", weights_only=True)
    full = ReDimNet2Wrap(**state["model_config"]).eval()
    full.load_state_dict(state["state_dict"], strict=True)
    # Preserve exactly the native forward AFTER spec, including input unsqueeze,
    # backbone time truncation, before-pool offset, pooling, BN and linear head.
    neural = copy.deepcopy(full)
    neural.spec = torch.nn.Identity()
    neural.pad_right_samples = None
    manifest = json.loads((OLD / "generation.json").read_text())
    inputs, pytorch, labels, rows, waveforms = [], [], [], [], []
    with torch.inference_mode():
        for clip in manifest["clips"]:
            path = OLD / clip["path"]
            assert sha(path) == clip["sha256"]
            wave, sr = sf.read(path, dtype="float32")
            d = gcd(sr, 16000)
            for channel, data in [("clean", resample_poly(wave, 16000//d, sr//d).astype(np.float32)),
                                  ("telephone", telephone(wave, sr))]:
                wave_tensor = torch.from_numpy(data[:48000].copy()).unsqueeze(0)
                x = wave_tensor
                if full.pad_right_samples is not None:
                    x = torch.nn.functional.pad(x, (0, full.pad_right_samples))
                feat = full.spec(x)
                a, b = full(wave_tensor), neural(feat)
                assert torch.equal(a, b), "Feature split changed PyTorch output"
                inputs.append(feat.numpy().copy())
                waveforms.append(wave_tensor.numpy().copy())
                pytorch.append(a.numpy().reshape(-1).copy())
                labels.append(f"{clip['speaker']}-{clip['take']}/{channel}")
        shape = list(inputs[0].shape)
        assert all(list(x.shape) == shape for x in inputs)
        torch.onnx.export(neural, torch.from_numpy(inputs[0]), str(ART / "b3.onnx"),
                          input_names=["features"], output_names=["embedding"],
                          opset_version=17, dynamo=False, do_constant_folding=True)
    graph = onnx.load(str(ART / "b3.onnx"))
    onnx.checker.check_model(graph)
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    session = ort.InferenceSession(str(ART / "b3.onnx"), sess_options=opts, providers=["CPUExecutionProvider"])
    onnx_out = []
    for label, feat, pt in zip(labels, inputs, pytorch):
        tick = time.perf_counter()
        out = session.run(None, {"features": feat})[0].reshape(-1)
        onnx_out.append(out)
        row = dict(label=label, **metrics(pt, out), seconds=time.perf_counter()-tick)
        assert row["raw_max_abs"] < 1e-3 and row["normalized_max_abs"] < 1e-5 and row["cosine"] > 0.999999
        rows.append(row)
    score_diffs = [abs(normalize(pytorch[i]) @ normalize(pytorch[j]) - normalize(onnx_out[i]) @ normalize(onnx_out[j]))
                   for i,j in itertools.combinations(range(len(labels)), 2)]
    assert max(score_diffs) < 1e-5
    np.savez(ART / "fixtures.npz", features=np.stack(inputs), waveforms=np.stack(waveforms), pytorch=np.stack(pytorch), onnx=np.stack(onnx_out), labels=labels)
    report = dict(revision=REV, checkpoint_sha256=HASH, checkpoint_bytes=weight.stat().st_size,
                  model_config=state["model_config"], source_license_sha256=sha(source / "LICENSE"),
                  onnx_sha256=sha(ART / "b3.onnx"), onnx_bytes=(ART / "b3.onnx").stat().st_size,
                  feature_shape=shape, embedding_shape=list(pytorch[0].shape), opset=17,
                  waveform_split_bitwise_equal=True, dtype="float32", quantized=False,
                  tolerances=dict(raw_max_abs=1e-3, normalized_max_abs=1e-5, min_cosine=0.999999, score_abs=1e-5),
                  inputs=rows, pair_count=len(score_diffs), max_score_abs_difference=max(score_diffs),
                  versions=dict(torch=torch.__version__, numpy=np.__version__, onnx=onnx.__version__, onnxruntime=ort.__version__))
    (ROOT / "local_results.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps({k:report[k] for k in ["feature_shape", "onnx_bytes", "max_score_abs_difference"]}))
    print("max raw error", max(r["raw_max_abs"] for r in rows))
    if args.waveform:
        assert full.pad_right_samples is None
        class Waveform(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.frontend = full.spec
                self.neural = neural
            def forward(self, waveform):
                features = self.frontend(waveform)
                return self.neural(features), features
        wrapped = Waveform().eval()
        with torch.inference_mode():
            torch.onnx.export(wrapped, torch.from_numpy(waveforms[0]), str(ART / "b3-waveform.onnx"),
                              input_names=["waveform"], output_names=["embedding", "features"],
                              opset_version=17, dynamo=False, do_constant_folding=True)
        onnx.checker.check_model(onnx.load(str(ART / "b3-waveform.onnx")))
        session = ort.InferenceSession(str(ART / "b3-waveform.onnx"), sess_options=opts, providers=["CPUExecutionProvider"])
        waveform_rows, waveform_outputs = [], []
        for label, wave, feat, pt in zip(labels, waveforms, inputs, pytorch):
            embedding, exported_features = session.run(None, {"waveform":wave})
            embedding = embedding.reshape(-1)
            row = dict(label=label, **metrics(pt,embedding), feature_max_abs=float(np.max(np.abs(feat-exported_features))))
            assert row["raw_max_abs"] < 1e-3 and row["normalized_max_abs"] < 1e-5 and row["cosine"] > 0.999999
            waveform_outputs.append(embedding)
            waveform_rows.append(row)
        diffs=[abs(normalize(pytorch[i]) @ normalize(pytorch[j]) - normalize(waveform_outputs[i]) @ normalize(waveform_outputs[j]))
               for i,j in itertools.combinations(range(len(labels)),2)]
        assert max(diffs)<1e-5
        waveform_report=dict(input_shape=[1,48000],input_name="waveform",onnx_sha256=sha(ART / "b3-waveform.onnx"),
            onnx_bytes=(ART / "b3-waveform.onnx").stat().st_size,inputs=waveform_rows,max_score_abs_difference=max(diffs),
            frontend="Unmodified checkpoint TF convolutional frontend, 16 kHz mono float32, exactly 48000 samples")
        (ROOT / "local_waveform_results.json").write_text(json.dumps(waveform_report,indent=2)+"\n")
        np.save(ART / "waveform_onnx.npy",np.stack(waveform_outputs))
        print("Waveform export max raw error",max(r["raw_max_abs"] for r in waveform_rows))


if __name__ == "__main__":
    main()
