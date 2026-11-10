# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = [
#   "speechbrain==1.0.3", "torch==2.8.0", "torchaudio==2.8.0",
#   "huggingface-hub==0.36.0", "numpy==2.2.6", "scipy==1.16.3",
#   "soundfile==0.14.0", "onnxruntime==1.23.2",
# ]
# ///
"""Bounded CPU comparison, with shared audio/trials and model-native features.

No thresholds, calibration, authentication decisions or remote audio inference.
"""
import argparse
import hashlib
import io
import itertools
import json
from math import gcd
from pathlib import Path
import platform
import sys
import time
import urllib.request
import zipfile

from experiment import ART, ROOT, ECAPA, save, sha, telephone, versions

ECAPA_REV = "0f99f2d0ebe89ac095bcc5903c4dd8f72b367286"
CAM = "Wespeaker/wespeaker-voxceleb-campplus"
CAM_REV = "acf623ad8ca746e50baa432255cf8fc57c669c45"
REDIM_REV = "c5bbe0b76e37df698c403f8844e41304ceab6307"
WESPEAKER_REV = "9fecd6cb4f47475d01761d87c826298dff4ef18c"
REDIM_HASH = {
    "b3": "02d14efdc184573873b5c3f62bea847fcdb6919fbba19a8da7af7c1ba36b76a2",
    "b6": "bc45f032099f3c9f0fb6ad756bca23daeeba456d00c515056e388824ef36f41b",
}


def download(url, path, limit, expected=None):
    """Bound individual downloads, use atomic writes, verify before use."""
    if not path.exists():
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"Download exceeds {limit} bytes: {url}")
        if expected and hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Download hash mismatch: {url}")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".partial")
        tmp.write_bytes(data)
        tmp.replace(path)
    if expected:
        assert sha(path) == expected, f"Cached file hash mismatch: {path}"
    return path


def load_model(name):
    import numpy as np
    import torch
    from huggingface_hub import hf_hub_download, snapshot_download

    if name == "ecapa":
        from speechbrain.inference.speaker import EncoderClassifier
        snapshot = snapshot_download(ECAPA, revision=ECAPA_REV, cache_dir=str(ART / "hf"),
            allow_patterns=["hyperparams.yaml", "embedding_model.ckpt", "classifier.ckpt",
                            "mean_var_norm_emb.ckpt", "label_encoder.txt"])
        model = EncoderClassifier.from_hparams(source=snapshot, savedir=str(ART / "ecapa"),
            overrides={"pretrained_path": snapshot}, run_opts={"device": "cpu"})
        files = list(Path(snapshot).glob("*.ckpt"))
        for p in files:
            assert sha(p) == sha(ART / "ecapa" / p.name)
        return (lambda data: model.encode_batch(torch.from_numpy(data).unsqueeze(0))), dict(
            model=ECAPA, revision=ECAPA_REV, license="Apache-2.0 model card; VoxCeleb data obligations separate",
            checkpoint_sha256={p.name: sha(p) for p in files},
            checkpoint_bytes=sum(p.stat().st_size for p in files),
            frontend="SpeechBrain encode_batch raw embedding, normalize=False; native feature pipeline")
    if name in ["cam", "cam-pt"]:
        import onnxruntime as ort
        import torchaudio.compliance.kaldi as kaldi
        path = Path(hf_hub_download(CAM, "voxceleb_CAM++.onnx", revision=CAM_REV,
                                    cache_dir=str(ART / "hf")))
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        opts.inter_op_num_threads = 1
        session = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
        pt_model = None
        extra = {}
        if name == "cam-pt":
            # Audit the suspicious released ONNX result using publisher PyTorch weights.
            source = ART / f"wespeaker-{WESPEAKER_REV}"
            for file in ["wespeaker/__init__.py", "wespeaker/models/__init__.py",
                         "wespeaker/models/campplus.py", "wespeaker/models/pooling_layers.py",
                         "wespeaker/models/multi_view_attention.py", "LICENSE"]:
                download(f"https://raw.githubusercontent.com/wenet-e2e/wespeaker/{WESPEAKER_REV}/{file}",
                         source / file, 1_000_000)
            sys.path.insert(0, str(source))
            # The toolkit package initializer imports CLI, diarization and VAD.
            # Namespace only the two model modules, without executing that initializer.
            import types
            for package in ["wespeaker", "wespeaker.models"]:
                module = types.ModuleType(package)
                module.__path__ = [str(source.joinpath(*package.split(".")))]
                sys.modules[package] = module
            from wespeaker.models.campplus import CAMPPlus
            pt_path = Path(hf_hub_download(CAM, "avg_model.pt", revision=CAM_REV, cache_dir=str(ART / "hf")))
            pt_model = CAMPPlus(feat_dim=80, embed_dim=512, pooling_func="TSTP")
            state = torch.load(pt_path, map_location="cpu", weights_only=True)
            # Released checkpoint also contains the training speaker-classification head.
            removed_projection_shape = list(state.pop("projection.weight").shape)
            pt_model.load_state_dict(state, strict=True)
            pt_model.eval()
            extra = dict(pytorch_checkpoint_sha256=sha(pt_path), pytorch_checkpoint_bytes=pt_path.stat().st_size,
                         removed_training_projection_shape=removed_projection_shape,
                         source_revision=WESPEAKER_REV, source_sha256={str(p.relative_to(source)): sha(p)
                             for p in source.rglob("*.py")}, parity=[])

        def embed(data):
            # Matches pinned WeSpeaker bin/infer_onnx.py, not ModelScope's CAM++ frontend.
            f = kaldi.fbank(torch.from_numpy(data).unsqueeze(0) * 32768,
                num_mel_bins=80, frame_length=25, frame_shift=10, dither=0,
                sample_frequency=16000, window_type="hamming", use_energy=False)
            f = f - f.mean(dim=0)
            if pt_model is not None:
                return pt_model(f.unsqueeze(0))
            return torch.from_numpy(session.run(["embs"], {"feats": f.unsqueeze(0).numpy()})[0])
        if pt_model is not None:
            # Fixed feature probes isolate neural adapter/export parity from audio features.
            with torch.inference_mode():
                gen = torch.Generator().manual_seed(1701)
                for frames in [298, 700]:
                    f = torch.randn((1, frames, 80), generator=gen)
                    p = pt_model(f).reshape(-1)
                    o = torch.from_numpy(session.run(["embs"], {"feats": f.numpy()})[0]).reshape(-1)
                    extra["parity"].append(dict(frames=frames, max_abs_difference=float((p-o).abs().max()),
                        cosine=float(torch.nn.functional.cosine_similarity(p, o, dim=0))))
        return embed, dict(model=CAM, revision=CAM_REV, runtime="PyTorch CPU" if pt_model is not None else "ONNX CPUExecutionProvider",
            checkpoint_sha256={path.name: sha(path)}, checkpoint_bytes=path.stat().st_size,
            frontend="80-bin Kaldi fbank, PCM scale 32768, 25/10 ms, hamming, dither=0, utterance CMN",
            frontend_reference=f"wenet-e2e/wespeaker@{WESPEAKER_REV}/wespeaker/bin/infer_onnx.py",
            license="HF card Apache-2.0; WeSpeaker pretrained policy separately follows VoxCeleb CC BY 4.0", **extra)
    size = name.removeprefix("redim-")
    assert size in REDIM_HASH
    archive = download(f"https://codeload.github.com/PalabraAI/redimnet2/zip/{REDIM_REV}",
                       ART / f"redimnet2-{REDIM_REV}.zip", 10_000_000)
    source = ART / f"redimnet2-{REDIM_REV}"
    # Extract only Python implementation and license, no remote setup or Hub execution.
    with zipfile.ZipFile(io.BytesIO(archive.read_bytes())) as z:
        for member in z.infolist():
            parts = Path(member.filename).parts[1:]
            if member.is_dir() or not parts:
                continue
            if ".." in parts:
                raise ValueError("Unsafe archive path")
            if parts[0] != "redimnet2" and parts != ("LICENSE",):
                continue
            out = source.joinpath(*parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(member))
    sys.path.insert(0, str(source))
    from redimnet2.redimnet2 import ReDimNet2Wrap
    weight = download(f"https://github.com/PalabraAI/redimnet2/releases/download/v1.0.0/{size}-vox2-lm.pt",
                      ART / f"{size}-vox2-lm.pt", 60_000_000, REDIM_HASH[size])
    state = torch.load(weight, map_location="cpu", weights_only=True)
    model = ReDimNet2Wrap(**state["model_config"])
    model.load_state_dict(state["state_dict"], strict=True)
    model.eval()
    return (lambda data: model(torch.from_numpy(data).unsqueeze(0))), dict(
        model=f"PalabraAI/redimnet2/{size}-vox2-lm", revision=REDIM_REV, release="v1.0.0",
        license="MIT repository; training-data/distribution obligations remain separate",
        source_archive_sha256=sha(archive), source_license_sha256=sha(source / "LICENSE"),
        checkpoint_sha256={weight.name: sha(weight)}, checkpoint_bytes=weight.stat().st_size,
        parameters=sum(p.numel() for p in model.parameters()), model_config=state["model_config"],
        frontend="Official ReDimNet2Wrap waveform frontend from checkpoint model_config, unmodified")


def trial_list(clips):
    trials = []
    for condition in ["clean", "telephone"]:
        for a, b in itertools.combinations(clips, 2):
            trials.append(dict(condition=condition, a=a["id"], b=b["id"],
                a_channel=condition, b_channel=condition, genuine=a["speaker"] == b["speaker"]))
    for a in [c for c in clips if c["take"] == 0]:
        for b in [c for c in clips if c["take"] != 0]:
            trials.append(dict(condition="clean_anchor_telephone_query", a=a["id"], b=b["id"],
                a_channel="clean", b_channel="telephone", genuine=a["speaker"] == b["speaker"]))
    return trials


def summarize(pairs):
    import numpy as np
    result = {}
    for duration in ["full", "first3"]:
        result[duration] = {}
        for condition in ["clean", "telephone", "clean_anchor_telephone_query"]:
            group = [p for p in pairs if p["duration"] == duration and p["condition"] == condition]
            g = [p["cosine"] for p in group if p["genuine"]]
            i = [p["cosine"] for p in group if not p["genuine"]]
            result[duration][condition] = dict(genuine_n=len(g), impostor_n=len(i),
                genuine_min=min(g), genuine_mean=float(np.mean(g)), genuine_max=max(g),
                impostor_min=min(i), impostor_mean=float(np.mean(i)), impostor_max=max(i),
                sample_gap=min(g)-max(i))
    return result


def run(args):
    import numpy as np
    import soundfile as sf
    import torch
    from scipy.signal import resample_poly
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    manifest = args.manifest.resolve()
    clips = json.loads(manifest.read_text())["clips"]
    assert len(clips) >= 2 and len({c["sha256"] for c in clips}) == len(clips)
    for c in clips:
        c["id"] = f"{c['speaker']}-{c['take']}"
    assert len({c["id"] for c in clips}) == len(clips)
    trials = trial_list(clips)
    tick = time.perf_counter()
    audio = {}
    for clip in clips:
        path = manifest.parent / clip["path"]
        assert sha(path) == clip["sha256"]
        wave, sr = sf.read(path, dtype="float32")
        assert wave.ndim == 1 and np.isfinite(wave).all() and len(wave)/sr >= 3
        d = gcd(sr, 16000)
        channels = dict(clean=resample_poly(wave, 16000//d, sr//d).astype(np.float32),
                        telephone=telephone(wave, sr))
        # Crop AFTER channel simulation: same full waveform processing for all models.
        for channel, data in channels.items():
            for duration, value in [("full", data), ("first3", data[:48000])]:
                audio[(clip["id"], channel, duration)] = np.ascontiguousarray(value)
    preprocess_seconds = time.perf_counter()-tick
    report = dict(python=platform.python_version(), platform=platform.platform(), device="cpu",
        torch_threads=4, interop_threads=1, timing_repeats=args.repeats,
        versions=versions(["speechbrain", "torch", "torchaudio", "huggingface-hub", "numpy", "scipy", "soundfile", "onnxruntime"]),
        generation_manifest_sha256=sha(manifest), script_sha256=sha(Path(__file__)),
        preprocessing_script_sha256=sha(ROOT / "experiment.py"),
        audio_sha256={c["id"]: c["sha256"] for c in clips},
        input_sha256={"/".join(k): hashlib.sha256(v.tobytes()).hexdigest() for k,v in audio.items()},
        preprocess_seconds=preprocess_seconds, trials=trials,
        trial_sha256=hashlib.sha256(json.dumps(trials, sort_keys=True).encode()).hexdigest(), models={})
    for name in args.models:
        tick = time.perf_counter()
        try:
            embed, meta = load_model(name)
            meta["load_seconds_including_resolution_download"] = time.perf_counter()-tick
            embeddings, timings = {}, []
            # One unmeasured 3 s warm-up, then serial interleaved duration/channel trials.
            warm = time.perf_counter()
            with torch.inference_mode():
                embed(next(v for k,v in audio.items() if k[2] == "first3"))
            meta["warmup_seconds"] = time.perf_counter()-warm
            for key, data in audio.items():
                times = []
                reference = None
                for _ in range(args.repeats):
                    t = time.perf_counter()
                    with torch.inference_mode():
                        e = embed(data).reshape(-1).float()
                        assert torch.isfinite(e).all() and torch.linalg.vector_norm(e) > 0
                        e = torch.nn.functional.normalize(e, dim=0)
                    times.append(time.perf_counter()-t)
                    if reference is not None:
                        assert torch.allclose(e, reference, atol=1e-6, rtol=1e-5)
                    reference = e
                embeddings[key] = reference
                timings.append(dict(clip=key[0], channel=key[1], duration=key[2], samples=len(data),
                    dimension=len(reference), seconds=times))
            pairs = []
            for duration in ["full", "first3"]:
                for trial in trials:
                    a = embeddings[(trial["a"], trial["a_channel"], duration)]
                    b = embeddings[(trial["b"], trial["b_channel"], duration)]
                    pairs.append(dict(trial, duration=duration, cosine=float(torch.dot(a, b))))
            meta.update(status="ok", summary=summarize(pairs), audio=timings, pairs=pairs)
            meta["timing_summary"] = {}
            for duration in ["full", "first3"]:
                ts = [s for row in timings if row["duration"] == duration for s in row["seconds"]]
                meta["timing_summary"][duration] = dict(n=len(ts), p50_seconds=float(np.median(ts)),
                    p95_seconds=float(np.percentile(ts, 95)), total_seconds=sum(ts))
            report["models"][name] = meta
            print(name, json.dumps({"summary": meta["summary"], "timing": meta["timing_summary"]}), flush=True)
            del embed, embeddings
        except Exception as exc:
            import traceback
            traceback.print_exc()
            report["models"][name] = dict(status="blocked", error=repr(exc), elapsed_seconds=time.perf_counter()-tick)
        save(args.output, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", choices=["ecapa", "cam", "cam-pt", "redim-b3", "redim-b6"],
                        default=["ecapa", "cam", "cam-pt", "redim-b3", "redim-b6"])
    parser.add_argument("--manifest", type=Path, default=ROOT / "generation.json")
    parser.add_argument("--output", type=Path, default=ROOT / "comparison.json")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    assert args.repeats >= 1
    run(args)
