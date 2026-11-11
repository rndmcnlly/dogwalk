# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["numpy==2.2.6", "scipy==1.16.3", "soundfile==0.14.0"]
# ///
"""Check preprocess.js against the Voice Reference builder's Python preprocessing.

Runs both on deterministic synthetic speech-like PCM at 8 and 16 kHz, plus any
mono 16-bit WAV paths given as arguments, and compares the 48000-sample model
inputs. Needs Node. Nothing is written outside a temporary directory.

    uv run --script speaker_worker/check_preprocess.py [clip.wav ...]
"""
import subprocess
import sys
import tempfile
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.ndimage import label
from scipy.signal import resample_poly

HERE = Path(__file__).resolve().parent
NODE = """
import {readFileSync, writeFileSync} from "node:fs";
import {modelInput} from "%s";
const [pcm, rate, out] = process.argv.slice(1);
const bytes = readFileSync(pcm);
try {
  const {waveform} = modelInput(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.length), Number(rate), 30);
  writeFileSync(out, Buffer.from(waveform.buffer));
} catch (error) {
  if (!error.code) throw error;
  console.log(error.code);
}
""" % (HERE / "preprocess.js").as_uri()


def context3(wave, sr):  # the builder's window, verbatim
    skip = int(.5*sr); x = wave[skip:]; n = int(.02*sr)
    frames = x[:len(x)//n*n].reshape(-1, n)
    db = 20*np.log10(np.maximum(np.sqrt(np.mean(frames.astype("float64")**2, axis=1)), 1e-9))
    thr = max(-55., float(np.percentile(db, 20))+12., float(np.percentile(db, 95))-30.)
    active = db > thr
    groups, count = label(active)
    for k in range(1, count+1):
        if (groups == k).sum() < 5: active[groups == k] = False
    if len(active) < 150: return None, float(active.sum()*.02)
    sums = np.convolve(active.astype(int), np.ones(150, dtype=int), "valid")
    s = int(sums.argmax())
    return x[s*n:s*n+3*sr], float(active.sum()*.02)


def python_input(pcm, sr):
    wave = (np.frombuffer(pcm, "<i2").astype(np.float32) / 32768).astype(np.float32)
    x, speech = context3(wave, sr)
    if x is None or speech < 1.0:  # the builder skips these clips
        return None
    g = gcd(sr, 16000)
    return np.ascontiguousarray(resample_poly(x, 16000//g, sr//g).astype(np.float32))


def synthetic(sr, seconds=6.0, seed=0):
    """Voiced bursts with pauses and a little noise: enough to exercise the window search."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(sr*seconds)) / sr
    f0 = 110 + 20*np.sin(2*np.pi*0.7*t)
    voice = sum(np.sin(2*np.pi*k*np.cumsum(f0)/sr) / k for k in range(1, 12))
    gate = (np.sin(2*np.pi*1.3*t + rng.uniform(0, 6)) > -0.2) * (t > 1.2)
    wave = 0.2*voice*gate + 0.002*rng.standard_normal(len(t))
    return (np.clip(wave, -1, 1)*32767).astype("<i2").tobytes()


def main():
    cases = [(f"synthetic {sr} Hz", synthetic(sr, seed=sr), sr) for sr in (8000, 16000)]
    for path in sys.argv[1:]:
        data, sr = sf.read(path, dtype="int16")
        assert data.ndim == 1, f"{path} is not mono"
        cases.append((Path(path).name, data.astype("<i2").tobytes(), sr))
    worst = 0.0
    with tempfile.TemporaryDirectory() as tmp:
        pcm, out = Path(tmp)/"in.pcm", Path(tmp)/"out.f32"
        for name, data, sr in cases:
            pcm.write_bytes(data)
            out.unlink(missing_ok=True)
            code = subprocess.run(["node", "--input-type=module", "-e", NODE, str(pcm), str(sr), str(out)],
                                  check=True, capture_output=True, text=True).stdout.strip()
            py = python_input(data, sr)
            if py is None or code:
                assert py is None and code == "insufficient-audio", (name, code)
                print(f"{name:40s} insufficient audio in both")
                continue
            js = np.frombuffer(out.read_bytes(), np.float32)
            assert js.shape == py.shape == (48000,), (name, js.shape, py.shape)
            err = float(np.max(np.abs(js.astype(np.float64) - py)))
            worst = max(worst, err)
            print(f"{name:40s} max abs {err:.3e}")
    assert worst < 1e-5, f"preprocess.js diverges from the builder: {worst}"
    print("ok")


if __name__ == "__main__":
    main()
