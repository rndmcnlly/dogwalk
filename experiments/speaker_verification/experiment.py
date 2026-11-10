# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = [
#   "speechbrain==1.0.3", "torch==2.8.0", "torchaudio==2.8.0",
#   "huggingface-hub==0.36.0", "numpy==2.2.6", "scipy==1.16.3",
#   "soundfile==0.14.0",
# ]
# ///
"""Offline synthetic-speaker probe. Never an authentication decision or probability.

generate runs under the existing Breeze Python; score runs in uv's isolated env.
All waveforms are caller-only: no Voice Agent, mix, diarization or echo handling.
"""
import argparse
import hashlib
import importlib.metadata
import json
import platform
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ART = ROOT / "artifacts"
BREEZE = "mlx-community/Breeze-TTS-2-mlx-4bit"
ECAPA = "speechbrain/spkrec-ecapa-voxceleb"
SPEAKERS = [
    ("baritone", 1701, "An adult masculine low baritone, warm grainy resonance, neutral American accent, deliberate conversational pacing and crisp consonants."),
    ("bright", 2701, "An adult feminine high mezzo voice, bright forward resonance, neutral American accent, nimble articulation and lively conversational rhythm."),
    ("contralto", 3701, "An older adult feminine low contralto, dark smooth resonance, light southern English accent, measured precise diction and relaxed conversational pacing."),
]
TEXTS = [
    "The morning train arrives beside the river. I have brought a notebook to record what we discover along the way.",
    "Please leave the package on the kitchen table. After lunch we can walk through the garden and check the weather together.",
    "Yesterday I found a small blue umbrella near the library. Tomorrow I will ask the neighbors whether anyone has lost it.",
]


def save(path, obj):
    path.write_text(json.dumps(obj, indent=2) + "\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def versions(names):
    return {n: importlib.metadata.version(n) for n in names}


def model_revision(model):
    from huggingface_hub import HfApi
    return HfApi().model_info(model).sha


def generate(revision=None):
    import mlx.core as mx
    import numpy as np
    import soundfile as sf
    from huggingface_hub import snapshot_download
    from mlx_audio.tts import load

    # Refuse existence-only resume: it could reuse audio for changed requests.
    if (ROOT / "generation.json").exists() or (ART / "audio").exists():
        raise FileExistsError("Preserve this run. Use a separate copy of this directory for a fresh run.")
    audio_dir = ART / "audio"
    audio_dir.mkdir(parents=True)
    start = time.perf_counter()
    revision = revision or model_revision(BREEZE)
    # Already-cached assets are reused, but resolve to the recorded immutable commit.
    snapshot = snapshot_download(BREEZE, revision=revision)
    model = load(snapshot)
    meta = dict(model=BREEZE, revision=revision, python=platform.python_version(),
                versions=versions(["mlx", "mlx-audio", "transformers", "soundfile"]),
                device=str(mx.default_device()), sample_rate=model.sample_rate,
                load_seconds=time.perf_counter()-start,
                selection="Three designed identities, no human audition or transcript verification",
                license="Research/noncommercial Breeze weights and outputs", clips=[])
    save(ROOT / "generation.json", meta)
    for speaker, seed, design in SPEAKERS:
        anchor = audio_dir / f"{speaker}-0.wav"
        for take, text in enumerate(TEXTS):
            request = dict(text=text, seed=seed+take*19, cfg_scale=4, max_tokens=350, voice="S0")
            if take == 0:
                request["instruct"] = design + " Clean dry close-miked speech. No music, effects, whispering, laughter or shouting."
            else:
                request.update(ref_audio=str(anchor), ref_text=TEXTS[0])
            tick = time.perf_counter()
            results = list(model.generate(**request))  # Serialized GPU generation.
            wave = np.concatenate([np.asarray(r.audio, dtype=np.float32).reshape(-1) for r in results])
            assert wave.size and np.isfinite(wave).all()
            path = audio_dir / f"{speaker}-{take}.wav"
            sf.write(path, wave, model.sample_rate, subtype="PCM_16")
            public_request = dict(request)
            if take:
                public_request["ref_audio"] = str(anchor.relative_to(ROOT))
            record = dict(speaker=speaker, take=take, request=public_request,
                          path=str(path.relative_to(ROOT)), sha256=sha(path),
                          anchor_sha256=sha(anchor), seconds=len(wave)/model.sample_rate,
                          wall_seconds=time.perf_counter()-tick, peak=float(abs(wave).max()),
                          token_count=sum(r.token_count for r in results),
                          possibly_truncated=any(r.token_count >= 350 for r in results))
            meta["clips"].append(record)
            save(ROOT / "generation.json", meta)
            print(f"{speaker}/{take}: {record['seconds']:.2f}s audio in {record['wall_seconds']:.2f}s", flush=True)
            mx.clear_cache()
    meta["total_seconds"] = time.perf_counter()-start
    save(ROOT / "generation.json", meta)


def telephone(wave, rate):
    """300–3400 Hz Butterworth, 8 kHz G.711 mu-law encode/decode, back to 16k.

    audioop implements the actual 8-bit codec, not continuous mu-law companding.
    Offline zero-phase filtering is a controlled approximation, not a PSTN model.
    """
    import audioop
    import numpy as np
    from scipy.signal import butter, resample_poly, sosfiltfilt
    from math import gcd

    filtered = sosfiltfilt(butter(6, [300, 3400], btype="bandpass", fs=rate, output="sos"), wave)
    d = gcd(rate, 8000)
    narrow = resample_poly(filtered, 8000//d, rate//d)
    pcm = (np.clip(narrow, -1, 1)*32767).astype("<i2").tobytes()
    decoded = audioop.ulaw2lin(audioop.lin2ulaw(pcm, 2), 2)
    narrow = np.frombuffer(decoded, dtype="<i2").astype(np.float32)/32768
    return resample_poly(narrow, 2, 1).astype(np.float32)


def score(revision=None):
    import itertools
    import numpy as np
    import soundfile as sf
    import torch
    from scipy.signal import resample_poly
    from math import gcd
    from huggingface_hub import snapshot_download
    from speechbrain.inference.speaker import EncoderClassifier

    torch.set_num_threads(4)
    start = time.perf_counter()
    generation = json.loads((ROOT / "generation.json").read_text())
    clips = generation["clips"]
    assert len(clips) == 9 and len({c["sha256"] for c in clips}) == 9
    revision = revision or model_revision(ECAPA)
    snapshot = snapshot_download(ECAPA, revision=revision, cache_dir=str(ART / "hf"),
                                 allow_patterns=["hyperparams.yaml", "embedding_model.ckpt", "classifier.ckpt", "mean_var_norm_emb.ckpt", "label_encoder.txt"])
    model = EncoderClassifier.from_hparams(source=snapshot, savedir=str(ART / "ecapa"),
                                           overrides={"pretrained_path": snapshot},
                                           run_opts={"device": "cpu"})
    checkpoint_hashes = {p.name: sha(p) for p in Path(snapshot).glob("*.ckpt")}
    for name, digest in checkpoint_hashes.items():
        assert sha(ART / "ecapa" / name) == digest, "Collected checkpoint differs from pinned snapshot"
    loaded = time.perf_counter()
    embeddings = {}
    audio_meta = []
    for clip in clips:
        path = ROOT / clip["path"]
        assert sha(path) == clip["sha256"]
        wave, sr = sf.read(path, dtype="float32")
        assert wave.ndim == 1
        d = gcd(sr, 16000)
        clean = resample_poly(wave, 16000//d, sr//d).astype(np.float32)
        for condition, data in [("clean", clean), ("telephone", telephone(wave, sr))]:
            tick = time.perf_counter()
            with torch.inference_mode():
                emb = model.encode_batch(torch.from_numpy(data).unsqueeze(0)).reshape(-1)
            embeddings[(clip["speaker"], clip["take"], condition)] = torch.nn.functional.normalize(emb, dim=0)
            audio_meta.append(dict(speaker=clip["speaker"], take=clip["take"], condition=condition,
                                   embedding_seconds=time.perf_counter()-tick, samples=len(data),
                                   rms=float(np.sqrt(np.mean(data**2)))))
    pairs = []
    # All 36 distinct unordered pairs per condition, not self-pairs or duplicates.
    for condition in ["clean", "telephone"]:
        for a, b in itertools.combinations(clips, 2):
            x = embeddings[(a["speaker"], a["take"], condition)]
            y = embeddings[(b["speaker"], b["take"], condition)]
            pairs.append(dict(condition=condition, a=f"{a['speaker']}-{a['take']}",
                              b=f"{b['speaker']}-{b['take']}", genuine=a["speaker"] == b["speaker"],
                              anchor_pair=a["take"] == 0 or b["take"] == 0,
                              cosine=float(torch.dot(x, y))))
    # Enrollment/query mismatch: clean anchor, telephone follow-up, ordered trials.
    for a in [c for c in clips if c["take"] == 0]:
        for b in [c for c in clips if c["take"] != 0]:
            x = embeddings[(a["speaker"], 0, "clean")]
            y = embeddings[(b["speaker"], b["take"], "telephone")]
            pairs.append(dict(condition="clean_anchor_telephone_query", a=f"{a['speaker']}-0",
                              b=f"{b['speaker']}-{b['take']}", genuine=a["speaker"] == b["speaker"],
                              anchor_pair=True, cosine=float(torch.dot(x, y))))
    summary = {}
    for condition in ["clean", "telephone", "clean_anchor_telephone_query"]:
        group = [p for p in pairs if p["condition"] == condition]
        g = [p["cosine"] for p in group if p["genuine"]]
        i = [p["cosine"] for p in group if not p["genuine"]]
        summary[condition] = dict(genuine_n=len(g), impostor_n=len(i),
                                 genuine_min=min(g), genuine_mean=float(np.mean(g)), genuine_max=max(g),
                                 impostor_min=min(i), impostor_mean=float(np.mean(i)), impostor_max=max(i),
                                 sample_gap=min(g)-max(i))
    report = dict(model=ECAPA, revision=revision, device="cpu", threads=4,
                  checkpoint_sha256=checkpoint_hashes,
                  python=platform.python_version(), platform=platform.platform(),
                  versions=versions(["speechbrain", "torch", "torchaudio", "huggingface-hub", "numpy", "scipy", "soundfile"]),
                  generation_manifest_sha256=sha(ROOT / "generation.json"),
                  load_seconds=loaded-start, total_seconds=time.perf_counter()-start,
                  model_sample_rate=16000, summary=summary, audio=audio_meta, pairs=pairs)
    save(ROOT / "scores.json", report)
    print(json.dumps({"summary": summary, "total_seconds": report["total_seconds"]}, indent=2))


def test():
    import numpy as np
    from scipy.signal import resample_poly
    rate = 24000
    t = np.arange(rate*2)/rate
    wave = (0.1*np.sin(2*np.pi*1000*t) + 0.1*np.sin(2*np.pi*6000*t)).astype(np.float32)
    output = telephone(wave, rate)
    assert len(output) == 32000 and np.isfinite(output).all()
    spectrum = abs(np.fft.rfft(output[4000:-4000]))
    freqs = np.fft.rfftfreq(len(output[4000:-4000]), 1/16000)
    level = lambda hz: spectrum[np.argmin(abs(freqs-hz))]
    assert level(6000) < level(1000)*0.01
    assert not np.array_equal(output, resample_poly(wave, 2, 3))
    assert not telephone(np.zeros(rate, dtype=np.float32), rate).any()
    print("telephone: length, finite samples, out-of-band rejection, nonidentity and silence checks passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["generate", "score", "test"])
    parser.add_argument("--revision", help="Immutable model commit for this stage (otherwise resolve current HEAD)")
    args = parser.parse_args()
    if args.command == "test":
        test()
    else:
        {"generate": generate, "score": score}[args.command](args.revision)
