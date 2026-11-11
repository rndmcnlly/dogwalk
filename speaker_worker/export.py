# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["torch==2.8.0", "torchaudio==2.8.0", "numpy==2.2.6", "scipy==1.16.3", "onnx==1.19.1", "onnxruntime==1.23.2"]
# ///
"""Export ReDimNet2 B3 (VoxCeleb2 LM) as a 16 kHz waveform-to-embedding ONNX graph.

Downloads the pinned source revision and release checkpoint into artifacts/
(gitignored), verifies both hashes, and writes artifacts/b3-waveform.onnx. The
graph keeps the checkpoint's native TFMelBanks frontend and must hash to ONNX_SHA:
the embedding space names that hash, so a different export is a different space and
every stored Voice Reference would have to be rebuilt.
"""
import copy
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch

ROOT = Path(__file__).resolve().parent
ART = ROOT / "artifacts"
REV = "c5bbe0b76e37df698c403f8844e41304ceab6307"
SOURCE_SHA = "c4e0367c9f844c4e8a7869afd424749d083411b6a37be11e9e0139db46ed8cfb"
CKPT_SHA = "02d14efdc184573873b5c3f62bea847fcdb6919fbba19a8da7af7c1ba36b76a2"
ONNX_SHA = "c2653e3df4e2f5e3f2c495951dd7cf0cb2784226fed67b5f0a7f42d4bbfa7eb1"
SAMPLES = 48000


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url, path, limit, expected):
    if not path.exists():
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f"Download exceeds {limit} bytes: {url}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix(".partial").write_bytes(data)
        path.with_suffix(".partial").replace(path)
    if sha(path.read_bytes()) != expected:
        raise ValueError(f"Hash mismatch: {path}")
    return path


def main():
    archive = fetch(f"https://codeload.github.com/PalabraAI/redimnet2/zip/{REV}",
                    ART / f"redimnet2-{REV}.zip", 10_000_000, SOURCE_SHA)
    source = ART / f"redimnet2-{REV}"
    # Extract only the Python package and license; nothing from the archive is executed but the model code.
    with zipfile.ZipFile(io.BytesIO(archive.read_bytes())) as z:
        for member in z.infolist():
            parts = Path(member.filename).parts[1:]
            if member.is_dir() or not parts or (parts[0] != "redimnet2" and parts != ("LICENSE",)):
                continue
            if ".." in parts:
                raise ValueError("Unsafe archive path")
            out = source.joinpath(*parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(member))
    weight = fetch("https://github.com/PalabraAI/redimnet2/releases/download/v1.0.0/b3-vox2-lm.pt",
                   ART / "b3-vox2-lm.pt", 60_000_000, CKPT_SHA)
    sys.path.insert(0, str(source))
    from redimnet2.redimnet2 import ReDimNet2Wrap

    torch.set_num_threads(4)
    state = torch.load(weight, map_location="cpu", weights_only=True)
    full = ReDimNet2Wrap(**state["model_config"]).eval()
    full.load_state_dict(state["state_dict"], strict=True)
    assert full.pad_right_samples is None
    # The native forward after the frontend, kept as a separate module so the graph
    # matches the experiment's export (outputs: embedding, then frontend features).
    neural = copy.deepcopy(full)
    neural.spec = torch.nn.Identity()

    class Waveform(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.frontend = full.spec
            self.neural = neural

        def forward(self, waveform):
            features = self.frontend(waveform)
            return self.neural(features), features

    example = torch.from_numpy(np.random.default_rng(0).standard_normal((1, SAMPLES), dtype=np.float32) * 0.1)
    out = ART / "b3-waveform.onnx"
    with torch.inference_mode():
        expected = full(example).numpy().reshape(-1)
        torch.onnx.export(Waveform().eval(), example, str(out), input_names=["waveform"],
                          output_names=["embedding", "features"], opset_version=17, dynamo=False,
                          do_constant_folding=True)
    onnx.checker.check_model(onnx.load(str(out)))
    session = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
    got = session.run(["embedding"], {"waveform": example.numpy()})[0].reshape(-1)
    cosine = float(got @ expected / np.linalg.norm(got) / np.linalg.norm(expected))
    digest = sha(out.read_bytes())
    print(f"onnx {digest} cosine-vs-pytorch {cosine:.9f}")
    assert cosine > 0.999999, "ONNX export diverges from PyTorch"
    if digest != ONNX_SHA:
        sys.exit(f"Export hash {digest} differs from pinned {ONNX_SHA}: a new embedding space")


if __name__ == "__main__":
    main()
