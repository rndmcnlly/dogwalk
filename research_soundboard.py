# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["numpy==2.2.6", "scipy==1.16.3", "soundfile==0.14.0", "onnxruntime==1.23.2"]
# ///
"""Generate the private speaker-verification research soundboard.

    uv run --script research_soundboard.py [--no-open] [--rescore]

Reads every voice recording we hold (Voice Donation rounds pulled by voice_research.py,
the earlier phone_voice_collection corpus) and writes one self-contained HTML page,
audio embedded as data URIs, to ~/Library/Application Support/Dogwalk/voice_research/
soundboard.html (0600, directory 0700). Nothing here or in the page goes to Git.

Per clip it gathers: a local Parakeet MLX transcript with word times (uvx parakeet-mlx),
the verifier's 3 s window (context3, copied verbatim from the Voice Reference builder and
speaker_worker/check_preprocess.py), the production cosine to Adam's Voice Reference
(POST /admin/api/voice-verify?user=1; first 30 s only), a local ONNX cosine as a parity
check, the clip's role in the Voice Reference (reconstructed from build_reference.py's
leave-one-out rule), and, for donation rounds, evidence that the IVR's tone and spoken
prompt leaked into the caller-only stream. Parakeet output and production scores are
cached by audio hash in voice_research/.soundboard_cache, so reruns only do new work.
"""
from __future__ import annotations

import argparse, base64, hashlib, html, io, json, os, re, subprocess, sys, urllib.request
from datetime import datetime
from math import gcd
from pathlib import Path

import numpy as np, onnxruntime as ort, soundfile as sf
from scipy.ndimage import label
from scipy.signal import fftconvolve, resample_poly

REPO = Path(__file__).resolve().parent
DATA = Path.home() / "Library/Application Support/Dogwalk"
RESEARCH = DATA / "voice_research"
PHONE = DATA / "phone_voice_collection"
REFERENCE = DATA / "voice_reference/adam.json"
CACHE = RESEARCH / ".soundboard_cache"
OUT = RESEARCH / "soundboard.html"
ONNX = REPO / "speaker_worker/artifacts/b3-waveform.onnx"
HOST = "https://dogwalk.tools"
UA = {"user-agent": "dogwalk-research/1"}  # the zone blocks Python's default user agent
MAX_SCORED_SECONDS = 30  # /admin/api/voice-verify rejects longer audio
THRESHOLD = 0.5
TONE_SECONDS = 0.15
SECONDS_PER_PROMPT_WORD = 0.375  # Polly Joanna at default rate, roughly 160 words a minute
ASSUMED_TONE_START = 0.2  # used when the tone itself is not found in the inbound audio
MODES = ["buds", "speakerphone", "handset_reported"]  # build_reference.py's order of real calls


# --- the verifier's preprocessing --------------------------------------------------------

def context3(wave, sr):
    """build_reference.py's context3, verbatim, also returning the frame-level evidence."""
    skip = int(.5*sr); x = wave[skip:]; n = int(.02*sr)
    frames = x[:len(x)//n*n].reshape(-1, n)
    db = 20*np.log10(np.maximum(np.sqrt(np.mean(frames.astype("float64")**2, axis=1)), 1e-9))
    thr = max(-55., float(np.percentile(db, 20))+12., float(np.percentile(db, 95))-30.)
    active = db > thr
    groups, count = label(active)
    for k in range(1, count+1):
        if (groups == k).sum() < 5: active[groups == k] = False
    if len(active) < 150: return None, float(active.sum()*.02), active, None
    sums = np.convolve(active.astype(int), np.ones(150, dtype=int), "valid")
    s = int(sums.argmax())
    return x[s*n:s*n+3*sr], float(active.sum()*.02), active, s


def shifted_start(active, not_before):
    """The most speech-active 3 s span starting at or after `not_before` seconds of clip time."""
    sums = np.convolve(active.astype(int), np.ones(150, dtype=int), "valid")
    first = max(0, int(np.ceil((not_before - .5) / .02)))
    if first >= len(sums): return None
    return first + int(sums[first:].argmax())


def model_input(window, sr):
    g = gcd(sr, 16000)
    return np.ascontiguousarray(resample_poly(window, 16000//g, sr//g).astype(np.float32))


def unit(v):
    v = np.asarray(v, np.float64).reshape(-1); return v / np.linalg.norm(v)


class Embedder:
    def __init__(self):
        opts = ort.SessionOptions(); opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(str(ONNX), sess_options=opts, providers=["CPUExecutionProvider"])
        self.sha = hashlib.sha256(ONNX.read_bytes()).hexdigest()

    def __call__(self, window, sr):
        y = model_input(window, sr); assert y.shape == (48000,)
        return unit(self.session.run(None, {"waveform": y[None]})[0])


# --- caches and remote scoring -------------------------------------------------------------

def private_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True); path.chmod(0o700)


def write_private(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".partial")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: f.write(text)
    tmp.replace(path); path.chmod(0o600)


def wav_bytes(wave_int16, sr):
    buf = io.BytesIO(); sf.write(buf, wave_int16, sr, subtype="PCM_16", format="WAV"); return buf.getvalue()


class Scorer:
    def __init__(self, rescore: bool):
        self.path = CACHE / "scores.json"
        self.cache = {} if rescore or not self.path.exists() else json.loads(self.path.read_text())
        self.password = None

    def _request(self, method, url, body=None):
        if self.password is None:
            self.password = subprocess.run(["security", "find-generic-password", "-a", "adam", "-s", "dogwalk-admin", "-w"],
                                           capture_output=True, text=True, check=True).stdout.strip()
        auth = base64.b64encode(f"adam:{self.password}".encode()).decode()
        req = urllib.request.Request(url, data=body, method=method, headers={**UA, "authorization": f"Basic {auth}",
                                                                             **({"content-type": "audio/wav"} if body else {})})
        try:
            with urllib.request.urlopen(req, timeout=90) as r: return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return {"http_status": e.code, **json.loads(e.read() or b"{}")}

    def score(self, body: bytes):
        key = hashlib.sha256(body).hexdigest()
        if key not in self.cache:
            if not getattr(self, "warm", False):
                health = self._request("GET", f"{HOST}/admin/api/voice-verify/health")
                print("voice-verify health:", health, file=sys.stderr); self.warm = True
            self.cache[key] = self._request("POST", f"{HOST}/admin/api/voice-verify?user=1", body)
            write_private(self.path, json.dumps(self.cache, indent=1))
        return self.cache[key]


def parakeet(paths: dict[str, Path]) -> dict[str, dict]:
    """Transcripts keyed by audio sha256, running uvx parakeet-mlx once for every uncached clip."""
    out_dir = CACHE / "parakeet"; private_dir(out_dir)
    missing = {sha: p for sha, p in paths.items() if not (out_dir / f"{sha}.json").exists()}
    if missing:
        staging = CACHE / "staging"; private_dir(staging)
        for sha, p in missing.items():
            link = staging / f"{sha}.wav"; link.unlink(missing_ok=True); link.symlink_to(p)
        subprocess.run(["uvx", "parakeet-mlx", "--output-dir", str(out_dir), "--output-format", "json",
                        "--model", "mlx-community/parakeet-tdt-0.6b-v3", *[str(staging / f"{s}.wav") for s in missing]],
                       check=True)
        for sha in missing: (staging / f"{sha}.wav").unlink()
    return {sha: json.loads((out_dir / f"{sha}.json").read_text()) for sha in paths}


def words(transcript: dict) -> list[dict]:
    """Parakeet tokens are subword pieces; a leading space starts a word, punctuation never ends one."""
    found = []
    for sentence in transcript["sentences"]:
        for tok in sentence["tokens"]:
            text = tok["text"]
            if text.startswith(" ") or not found:
                found.append({"w": text.strip(), "s": tok["start"], "e": tok["end"]})
            else:
                found[-1]["w"] += text
                if re.search(r"\w", text): found[-1]["e"] = tok["end"]
    return [{**w, "s": round(w["s"], 3), "e": round(w["e"], 3)} for w in found if re.search(r"\w", w["w"])]


norm = lambda w: re.sub(r"[^a-z0-9']", "", w.lower())


def prompt_runs(heard: list[dict], prompt: str) -> list[dict]:
    """Runs of 3 or more consecutive Parakeet words that are also consecutive in the prompt."""
    p = [norm(w) for w in prompt.split()]
    grams = {tuple(p[i:i+3]) for i in range(len(p) - 2)}
    h = [norm(w["w"]) for w in heard]
    marked = [False] * len(h)
    for i in range(len(h) - 2):
        if tuple(h[i:i+3]) in grams: marked[i] = marked[i+1] = marked[i+2] = True
    runs, i = [], 0
    while i < len(h):
        if not marked[i]: i += 1; continue
        j = i
        while j < len(h) and marked[j]: j += 1
        at = next(k for k in range(len(p) - 2) if tuple(p[k:k+3]) == tuple(h[i:i+3]))
        runs.append({"first": i, "last": j - 1, "s": heard[i]["s"], "e": heard[j-1]["e"], "prompt_at": at,
                     "text": " ".join(w["w"] for w in heard[i:j])})
        i = j
    return runs


def repeats(runs) -> bool:
    """True when the prompt is heard twice: a later run restarts at or before where an earlier one reached.
    A run broken by one misheard word ("golden cowl") still moves forward through the prompt."""
    reach = -1
    for r in runs:
        if r["prompt_at"] <= reach: return True
        reach = r["prompt_at"] + (r["last"] - r["first"])
    return False


# --- page data -----------------------------------------------------------------------------

def peaks(wave, buckets=900):
    n = max(1, len(wave) // buckets); m = len(wave) // n
    x = wave[:m*n].reshape(m, n)
    return [[round(float(a), 3) for a in x.min(1)], [round(float(a), 3) for a in x.max(1)]]


def spectrogram(wave, sr, columns=700):
    nfft, hop = 256, 40
    win = np.hanning(nfft)
    pad = np.pad(wave, (0, nfft))
    frames = np.lib.stride_tricks.sliding_window_view(pad, nfft)[::hop][: len(wave)//hop]
    mag = 20*np.log10(np.abs(np.fft.rfft(frames * win, axis=1))[:, :128] + 1e-6)
    group = max(1, len(mag) // columns)
    mag = mag[: len(mag)//group*group].reshape(-1, group, 128).max(1)
    img = np.clip((mag + 40) / 80, 0, 1)  # -40..+40 dB of rfft magnitude
    return {"cols": len(img), "rows": 128, "data": base64.b64encode((img*255).astype(np.uint8).tobytes()).decode()}


def tone_search(wave, tone):
    """Normalized cross-correlation of the clip against the tone file; 1.0 is a perfect copy."""
    c = fftconvolve(wave.astype(np.float64), tone[::-1].astype(np.float64), "valid")
    sq = np.concatenate([[0.0], np.cumsum(wave.astype(np.float64)**2)])
    local = sq[len(tone):] - sq[:-len(tone)]  # exact sliding energy; FFT round-off breaks near digital silence
    floor = len(tone) * 10**(-60/10)  # ignore spans quieter than -60 dBFS, where any match is noise
    ncc = np.abs(c) / (np.sqrt(np.maximum(local, floor)) * np.linalg.norm(tone))
    i = int(ncc.argmax()); return i / 8000, float(ncc[i])


def collect():
    """Every recording as (clip metadata, wav path)."""
    clips = []
    for meta in sorted(RESEARCH.glob("*/*/session.json")):
        session = json.loads(meta.read_text())
        notes = meta.parent / "annotation.json"  # speaker and microphone notes live beside the pulled copy
        note = json.loads(notes.read_text()) if notes.exists() else {}
        for r in session["rounds"]:
            path = meta.parent / r["wav"]
            if not path.exists(): continue
            clips.append({"id": f"donation-{session['session'][:8]}-r{r['round']:02d}", "source": "donation",
                          "condition": note.get("condition", "unannotated"), "speaker": note.get("speaker", "unconfirmed"),
                          "kind": r["kind"], "prompt": r["prompt"],
                          "spoken_prompt": r["prompt"] if r["kind"] == "phrase" else f"Now talk freely. {r['prompt']}",
                          "twilio_transcript": r.get("transcript"), "twilio_confidence": r.get("confidence"),
                          "heard_at": r.get("heard_at"), "stream": r.get("stream"), "session": session["session"],
                          "round": r["round"], "path": path})
    inspection = PHONE / "speaker_comparison/inspection.json"
    modes = {c["call_sid"]: c["mode"] for c in json.loads(inspection.read_text())["calls"]} if inspection.exists() else {}
    for meta in sorted((PHONE / "calls").glob("*.json")):
        call = json.loads(meta.read_text())
        mode = "synthetic" if call.get("synthetic") else modes.get(call["call_sid"], "unknown")
        for sid, s in sorted(call.get("streams", {}).items(), key=lambda kv: kv[1]["round"]):
            path = PHONE / "audio" / s["file"]
            if not path.exists(): continue
            clips.append({"id": f"{mode}-{s['round']}", "source": "phone collection", "condition": mode,
                          "speaker": "Adam (confirmed)" if mode != "synthetic" else "synthetic", "kind": "phrase",
                          "prompt": s["phrase"], "spoken_prompt": None, "call_sid": call["call_sid"], "round": s["round"],
                          "complete": s.get("complete"), "sha_ok": hashlib.sha256(path.read_bytes()).hexdigest() == s["sha256"],
                          "inserted_silence_ms": s.get("inserted_silence_ms"), "duration": s.get("duration"),
                          "created": call["created"], "real": call.get("synthetic") is False, "path": path})
    return clips


def reference_roles(clips, embed, reference):
    """Replay build_reference.py's selection (without writing anything) to label each phone clip."""
    real = sorted({c["call_sid"]: c["created"] for c in clips if c.get("real")}.items(), key=lambda kv: kv[1])
    mode_of = {sid: mode for mode, (sid, _) in zip(MODES, real)}
    members, embs = [], []
    for c in clips:
        if not c.get("real"): continue
        builder_id = f"{mode_of[c['call_sid']]}-{c['round']}"
        if not (c["complete"] is True and c["sha_ok"]): c["reference"] = "skipped (incomplete or hash mismatch)"; continue
        wave, sr = sf.read(c["path"], dtype="float32")
        x, active, *_ = context3(wave, sr)
        if x is None or active < 1.0: c["reference"] = f"skipped ({active:.2f} s speech)"; continue
        members.append((c, builder_id)); embs.append(embed(x, sr))
    E = np.stack(embs)
    loo = np.array([float(E[i] @ unit(np.delete(E, i, 0).mean(0))) for i in range(len(E))])
    med = np.median(loo); mad = np.median(np.abs(loo - med)); cut = med - 4*1.4826*mad
    keep = [i for i in range(len(E)) if loo[i] >= cut]
    for i, (c, builder_id) in enumerate(members):
        c["reference"] = "in reference" if i in keep else "dropped as outlier"
        c["loo"] = round(float(loo[i]), 4); c["builder_id"] = builder_id
    rebuilt = float(unit(E[keep].mean(0)) @ unit(reference["embedding"]))
    return {"rebuilt_cosine": rebuilt, "kept": len(keep), "stored_clips": reference["clips"],
            "loo_median": float(med), "loo_cut": float(cut)}


def analyse(c, transcript, scorer, embed, ref, tone):
    data = sf.read(c["path"], dtype="int16")
    pcm, sr = data; assert sr == 8000 and pcm.ndim == 1
    wave = (pcm.astype(np.float32) / 32768).astype(np.float32)
    duration = len(wave) / sr
    scored = wave[: MAX_SCORED_SECONDS * sr]
    body = c["path"].read_bytes() if duration <= MAX_SCORED_SECONDS else wav_bytes(pcm[: MAX_SCORED_SECONDS * sr], sr)
    window, speech, active, start = context3(scored, sr)
    heard = words(transcript)
    c.update(duration=round(duration, 3), scored_seconds=round(min(duration, MAX_SCORED_SECONDS), 3),
             words=heard, parakeet_text=transcript["text"].strip(), speech_seconds=round(speech, 2),
             active=base64.b64encode(np.packbits(active)).decode(), active_frames=len(active))
    c["production"] = scorer.score(body)
    c["window"] = None if start is None else round(.5 + start*.02, 3)
    c["local_cosine"] = None if window is None or speech < 1.0 else round(float(embed(window, sr) @ ref), 4)
    c["tone_at"], c["tone_ncc"] = (round(v, 3) for v in tone_search(wave, tone))
    db = 20*np.log10(np.maximum(np.sqrt(np.mean(wave[: len(wave)//160*160].reshape(-1, 160).astype(np.float64)**2, 1)), 1e-9))
    c["noise_floor_db"] = round(float(np.percentile(db, 10)), 1)

    if c["source"] == "donation":
        tone_found = c["tone_ncc"] >= 0.5
        p_start = c["tone_at"] if tone_found else ASSUMED_TONE_START
        est_end = p_start + TONE_SECONDS + SECONDS_PER_PROMPT_WORD * len(c["spoken_prompt"].split())
        runs = prompt_runs(heard, c["spoken_prompt"])
        if c["kind"] == "phrase":  # the caller's repetition is the last pass through the prompt; earlier passes leaked
            leaked = []
            for k in range(1, len(runs) + 1):
                if repeats(runs[:k]): leaked = runs[:k-1]; break
        else:  # a free answer may echo the prompt's words, but only playback falls inside the prompt's time
            leaked = [r for r in runs if r["s"] < est_end + 1.0]
        leaked_idx = {i for r in leaked for i in range(r["first"], r["last"] + 1)}
        onset = next((w["s"] for i, w in enumerate(heard) if i not in leaked_idx and w["s"] >= p_start), duration)
        p_end = min(est_end, onset)  # the caller answers after the prompt, so the estimate cannot pass their first word
        seg = db[int(p_start*50): max(int(p_start*50) + 1, int(p_end*50))]
        elevation = float(np.median(seg) - c["noise_floor_db"]) if len(seg) else 0.0
        verdict = "yes" if tone_found or leaked else ("weak" if elevation > 10 else "no")
        evidence = [f"tone cross-correlation peak {c['tone_ncc']:.2f} at {c['tone_at']:.2f} s ({'found' if tone_found else 'not found; 0.5 needed'})",
                    f"{len(runs)} Parakeet run(s) of 3+ consecutive prompt words" + (": " + "; ".join(f"\"{r['text']}\" at {r['s']:.2f} s" for r in runs) if runs else "")
                    + f"; {len(leaked)} attributed to playback" + (" (the caller's own repetition is expected)" if c["kind"] == "phrase" else " (a leaked run must start within 1 s of the prompt's estimated end)"),
                    f"median level {elevation:+.1f} dB over the clip's noise floor ({c['noise_floor_db']:.0f} dBFS) in {p_start:.2f}–{p_end:.2f} s, before the caller's first word"]
        overlap = 0.0 if c["window"] is None else max(0.0, min(c["window"] + 3, p_end) - max(c["window"], p_start))
        c["prompt_region"] = {"start": round(p_start, 3), "end": round(p_end, 3), "estimated_end": round(est_end, 3),
                              "clamped": p_end < est_end, "tone_found": tone_found, "elevation_db": round(elevation, 1)}
        c["leak_runs"] = leaked
        c["leakage"] = {"verdict": verdict, "evidence": evidence, "window_overlap": round(overlap, 2)}
        shift = shifted_start(active, p_end)
        if overlap > 0 and shift is not None:
            n = int(.02*sr); x = scored[int(.5*sr):]
            c["shifted_window"] = round(.5 + shift*.02, 3)
            c["shifted_cosine"] = round(float(embed(x[shift*n: shift*n + 3*sr], sr) @ ref), 4)
        elif overlap == 0:
            c["shifted_window"], c["shifted_cosine"] = c["window"], c["local_cosine"]
        else:
            c["shifted_window"] = c["shifted_cosine"] = None
        st = c["stream"] or {}
        if st.get("started_at") and c.get("heard_at"):
            t0 = datetime.fromisoformat(st["started_at"].replace("Z", "+00:00"))
            c["heard_offset"] = round((datetime.fromisoformat(c["heard_at"].replace("Z", "+00:00")) - t0).total_seconds(), 3)

    c["peaks"] = peaks(wave); c["spec"] = spectrogram(wave, sr)
    c["audio"] = "data:audio/wav;base64," + base64.b64encode(c["path"].read_bytes()).decode()
    c["file"] = str(c["path"]).replace(str(Path.home()), "~"); del c["path"]
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--no-open", action="store_true"); ap.add_argument("--rescore", action="store_true")
    args = ap.parse_args()
    os.umask(0o077); private_dir(RESEARCH); private_dir(CACHE)
    reference = json.loads(REFERENCE.read_text()); ref = unit(reference["embedding"])
    embed = Embedder()
    assert embed.sha[:16] in reference["embedding_space"], "local ONNX is not the Voice Reference's model"
    tone_path = CACHE / "tone.wav"
    if not tone_path.exists():
        with urllib.request.urlopen(urllib.request.Request(f"{HOST}/voice/donation/tone.wav", headers=UA), timeout=30) as r:
            tone_path.write_bytes(r.read())
    tone, tone_sr = sf.read(tone_path, dtype="float32"); assert tone_sr == 8000

    clips = collect()
    shas = {hashlib.sha256(c["path"].read_bytes()).hexdigest(): c["path"] for c in clips}
    transcripts = parakeet(shas)
    sha_of = {p: s for s, p in shas.items()}
    roles = reference_roles(clips, embed, reference)
    scorer = Scorer(args.rescore)
    for c in clips:
        print("analysing", c["id"], file=sys.stderr)
        analyse(c, transcripts[sha_of[c["path"]]], scorer, embed, ref, tone)

    page = {"generated": datetime.now().astimezone().isoformat(timespec="seconds"), "threshold": THRESHOLD,
            "embedding_space": reference["embedding_space"], "reference": {**roles, "created_at": reference["created_at"],
            "audio_modes": reference["audio_modes"]}, "clips": clips,
            "constants": {"seconds_per_prompt_word": SECONDS_PER_PROMPT_WORD, "assumed_tone_start": ASSUMED_TONE_START,
                          "max_scored_seconds": MAX_SCORED_SECONDS}}
    blob = json.dumps(page, separators=(",", ":")).replace("</", "<\\/")
    write_private(OUT, PAGE.replace("__DATA__", blob))
    print(f"wrote {OUT} ({OUT.stat().st_size/1e6:.1f} MB), {len(clips)} clips", file=sys.stderr)
    if not args.no_open: subprocess.run(["open", str(OUT)])


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Dogwalk voice research soundboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root { --bg:#14161a; --card:#1d2026; --ink:#e6e6e6; --dim:#9aa0a8; --line:#333842;
  --prompt:#f0a040; --tone:#ff5d5d; --win:#4aa3ff; --shift:#5fe0d0; --speech:#6fd36f; --heard:#c58cff; }
body { background:var(--bg); color:var(--ink); font:14px/1.45 -apple-system, system-ui, sans-serif; margin:0 auto; max-width:1100px; padding:24px; }
h1 { font-size:1.4em; margin:0 0 4px; } h2 { font-size:1.1em; margin:28px 0 8px; color:var(--dim); font-weight:600; }
.dim { color:var(--dim); } code { font:12px ui-monospace, monospace; }
.card { background:var(--card); border-radius:10px; padding:14px 16px; margin:14px 0; }
.head { display:flex; flex-wrap:wrap; gap:8px; align-items:baseline; }
.head h3 { margin:0 8px 0 0; font-size:1.05em; }
.badge { font-size:12px; padding:1px 8px; border-radius:99px; background:#2b3038; }
.ok { background:#1f4a2a; } .bad { background:#5a2323; } .warn { background:#5a4a1a; } .info { background:#22395a; }
.viz { position:relative; margin-top:8px; }
canvas { display:block; width:100%; }
canvas.wave { height:120px; cursor:pointer; } canvas.spec { height:96px; cursor:pointer; }
.words { position:relative; height:44px; }
.words span { position:absolute; top:4px; font-size:11px; white-space:nowrap; cursor:pointer; padding:0 2px; border-radius:3px; background:#2b3038; overflow:hidden; }
.words span.leak { background:#6a3b10; } .words span.now { outline:1px solid #fff; }
.words span:nth-child(even) { top:24px; }
audio { width:100%; height:32px; margin-top:6px; }
table { border-collapse:collapse; font-size:12.5px; width:100%; margin-top:8px; }
td { padding:2px 8px 2px 0; vertical-align:top; } td:first-child { color:var(--dim); width:170px; }
.legend span { display:inline-block; margin-right:14px; font-size:12px; } .legend i { display:inline-block; width:12px; height:10px; margin-right:4px; vertical-align:middle; }
.grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:10px; }
.stat { background:var(--card); border-radius:10px; padding:10px 14px; } .stat b { font-size:1.6em; display:block; }
svg text { fill:var(--dim); font-size:11px; } a { color:var(--win); }
</style></head><body>
<h1>Voice research soundboard</h1>
<div class="dim" id="meta"></div>
<div class="grid" id="stats" style="margin-top:14px"></div>
<h2>Production cosine to Adam's Voice Reference, by source and condition</h2>
<svg id="chart" width="100%"></svg>
<div class="legend dim" style="margin:10px 0">
 <span><i style="background:var(--prompt);opacity:.5"></i>estimated tone + IVR prompt</span>
 <span><i style="background:var(--tone)"></i>best tone match</span>
 <span><i style="border:2px solid var(--win)"></i>verifier's 3 s window</span>
 <span><i style="border:2px dashed var(--shift)"></i>window shifted past the prompt</span>
 <span><i style="background:var(--speech)"></i>speech-active frames (verifier)</span>
 <span><i style="background:var(--heard)"></i>Twilio Gather result arrived</span>
 <span><i style="background:#555"></i>past the 30 s scoring limit; first 0.5 s is skipped by the verifier</span>
</div>
<div id="clips"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
const fmt = (v, d = 3) => v == null ? "n/a" : Number(v).toFixed(d);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"})[c]);
const group = c => c.source === "donation" ? `donation, ${c.condition}, ${c.kind.replace("_", " ")}` : `phone collection, ${c.condition}`;
const score = c => c.production && c.production.cosine_similarity;

document.getElementById("meta").innerHTML =
  `Generated ${esc(D.generated)}. Embedding space <code>${esc(D.embedding_space)}</code>. ` +
  `Voice Reference built ${esc(D.reference.created_at)} from ${D.reference.stored_clips} clips; replaying the builder's leave-one-out rule keeps ` +
  `${D.reference.kept} and rebuilds it with cosine ${fmt(D.reference.rebuilt_cosine, 6)} to the stored embedding ` +
  `(${D.reference.rebuilt_cosine > 0.9999 ? "exact" : "MISMATCH: reference roles below are unreliable"}). Threshold ${D.threshold}.`;

// Summary
const bySource = {}, groups = {};
for (const c of D.clips) { bySource[c.source] = (bySource[c.source] || 0) + 1; (groups[group(c)] ||= []).push(c); }
const don = D.clips.filter(c => c.leakage);
const scored = D.clips.filter(c => score(c) != null).map(score).sort((a, b) => a - b);
const q = p => scored.length ? scored[Math.min(scored.length - 1, Math.floor(p * scored.length))] : null;
const stats = [
  ...Object.entries(bySource).map(([k, v]) => [v, `${k} clips`]),
  [`${scored.filter(s => s >= D.threshold).length} / ${scored.length}`, `scored clips at or above ${D.threshold}`],
  [`${fmt(scored[0], 2)} · ${fmt(q(0.5), 2)} · ${fmt(scored.at(-1), 2)}`, "cosine min · median · max"],
  [`${don.filter(c => c.leakage.verdict === "yes").length} / ${don.length}`, `donation rounds with detected prompt leakage (${don.filter(c => c.leakage.verdict === "weak").length} weak)`],
  [`${don.filter(c => c.leakage.window_overlap > 0).length} / ${don.length}`, "donation windows overlapping the estimated prompt"],
];
document.getElementById("stats").innerHTML = stats.map(([b, t]) => `<div class="stat"><b>${b}</b><span class="dim">${t}</span></div>`).join("");

// Strip chart
(function chart() {
  const svg = document.getElementById("chart"), names = Object.keys(groups), row = 30, left = 290, W = 1000;
  const lo = Math.min(0, ...scored), hi = 1, x = v => left + (v - lo) / (hi - lo) * (W - left - 20);
  svg.setAttribute("viewBox", `0 0 ${W} ${names.length * row + 30}`); svg.setAttribute("height", names.length * row + 30);
  let s = "";
  for (let t = Math.ceil(lo * 10) / 10; t <= hi + 1e-9; t += 0.1) s += `<line x1="${x(t)}" x2="${x(t)}" y1="0" y2="${names.length * row}" stroke="#2a2e36"/><text x="${x(t)}" y="${names.length * row + 14}" text-anchor="middle">${t.toFixed(1)}</text>`;
  s += `<line x1="${x(D.threshold)}" x2="${x(D.threshold)}" y1="0" y2="${names.length * row}" stroke="var(--tone)" stroke-width="2"/><text x="${x(D.threshold) + 4}" y="${names.length * row + 26}" fill="var(--tone)">threshold ${D.threshold}</text>`;
  names.forEach((n, i) => {
    const y = i * row + row / 2;
    s += `<text x="0" y="${y + 4}" style="fill:var(--ink)">${esc(n)} (${groups[n].length})</text>`;
    groups[n].forEach((c, j) => {
      const v = score(c);
      if (v == null) { s += `<text x="${left}" y="${y + 4}">${esc(c.production.error || "unscored")}</text>`; return; }
      const fill = c.leakage?.verdict === "yes" ? "var(--prompt)" : c.reference === "dropped as outlier" ? "var(--tone)" : c.reference === "in reference" ? "var(--speech)" : "var(--win)";
      s += `<a href="#${c.id}"><circle cx="${x(v)}" cy="${y + (j % 3 - 1) * 5}" r="5" fill="${fill}" fill-opacity=".85"><title>${esc(c.id)}: ${fmt(v)}</title></circle></a>`;
      if (c.shifted_cosine != null && c.shifted_cosine !== c.local_cosine) s += `<circle cx="${x(c.shifted_cosine)}" cy="${y}" r="4" fill="none" stroke="var(--shift)"><title>${esc(c.id)} shifted window (local): ${fmt(c.shifted_cosine)}</title></circle>`;
    });
  });
  svg.innerHTML = s;
})();

// Clip cards
const container = document.getElementById("clips");
for (const n of Object.keys(groups)) {
  container.insertAdjacentHTML("beforeend", `<h2>${esc(n)}</h2>`);
  for (const c of groups[n]) container.appendChild(card(c));
}

function card(c) {
  const el = document.createElement("div"); el.className = "card"; el.id = c.id;
  const v = score(c), p = c.production || {};
  const badges = [
    v != null ? `<span class="badge ${v >= D.threshold ? "ok" : "bad"}">cosine ${fmt(v)}</span>` : `<span class="badge bad">${esc(p.error || "unscored")} ${esc(p.detail || "")}</span>`,
    c.reference ? `<span class="badge ${c.reference === "in reference" ? "ok" : "warn"}">${esc(c.reference)}</span>` : "",
    c.leakage ? `<span class="badge ${{yes: "bad", weak: "warn", no: "ok"}[c.leakage.verdict]}">prompt leakage: ${c.leakage.verdict}</span>` : "",
    c.duration > D.constants.max_scored_seconds ? `<span class="badge warn">scored first ${D.constants.max_scored_seconds} s of ${fmt(c.duration, 1)} s</span>` : "",
  ].join("");
  const rows = [
    ["prompt", esc(c.prompt) + (c.spoken_prompt && c.spoken_prompt !== c.prompt ? `<br><span class="dim">IVR said: ${esc(c.spoken_prompt)}</span>` : "")],
    ["Parakeet (local)", esc(c.parakeet_text) || '<span class="dim">no words recovered</span>'],
    c.twilio_transcript != null ? ["Twilio speech result", `${esc(c.twilio_transcript)} <span class="dim">(confidence ${fmt(c.twilio_confidence, 2)})</span>`] : null,
    ["kind · condition · speaker", `${esc(c.kind)} · ${esc(c.condition)} · ${esc(c.speaker)}`],
    ["verifier window", c.window == null ? "none (too short)" : `${fmt(c.window, 2)}–${fmt(c.window + 3, 2)} s; ${fmt(c.speech_seconds, 2)} s speech-active after the 0.5 s skip`],
    ["production score", v != null ? `${fmt(v, 4)} on ${fmt(p.audio_seconds, 2)} s (worker speech ${fmt(p.speech_seconds, 2)} s)` : esc(JSON.stringify(p))],
    ["local ONNX parity", c.local_cosine == null ? "n/a" : `${fmt(c.local_cosine, 4)} (Δ ${v != null ? (c.local_cosine - v).toExponential(1) : "n/a"})`],
    c.loo != null ? ["builder leave-one-out", `${fmt(c.loo, 4)} vs cut ${fmt(D.reference.loo_cut, 4)} (median ${fmt(D.reference.loo_median, 4)}); builder id ${esc(c.builder_id)}`] : null,
    c.leakage ? ["prompt region (estimated)", `${fmt(c.prompt_region.start, 2)}–${fmt(c.prompt_region.end, 2)} s: tone ${c.prompt_region.tone_found ? "found" : `assumed at ${D.constants.assumed_tone_start} s`}, then ${D.constants.seconds_per_prompt_word} s per spoken prompt word, ending ${fmt(c.prompt_region.estimated_end, 2)} s` + (c.prompt_region.clamped ? `, clamped to the caller's first word` : "")] : null,
    c.leakage ? ["leakage evidence", c.leakage.evidence.map(esc).join("<br>")] : null,
    c.leakage ? ["window vs prompt", `${fmt(c.leakage.window_overlap, 2)} s overlap; shifted window ${c.shifted_window == null ? "impossible" : fmt(c.shifted_window, 2) + " s"}, local cosine ${fmt(c.shifted_cosine, 4)} vs ${fmt(c.local_cosine, 4)}`] : null,
    c.stream ? ["stream", `${esc(c.stream.started_at)} – ${esc(c.stream.ended_at)}, ${c.stream.frames} frames, ${fmt(c.stream.seconds, 2)} s, inserted gap ${c.stream.inserted_gap_ms} ms, complete ${c.stream.complete}, truncated ${c.stream.truncated}; Gather result ${fmt(c.heard_offset, 2)} s after stream start`] : null,
    c.call_sid ? ["call", `${esc(c.call_sid)} round ${c.round}, complete ${c.complete}, sha ${c.sha_ok ? "ok" : "MISMATCH"}, inserted silence ${c.inserted_silence_ms} ms`] : null,
    ["file", `<code>${esc(c.file)}</code>`],
  ].filter(Boolean);
  el.innerHTML = `<div class="head"><h3>${esc(c.id)}</h3>${badges}<span class="dim">${fmt(c.duration, 2)} s</span></div>
    <audio controls preload="auto" src="${c.audio}"></audio>
    <div class="viz"><canvas class="wave"></canvas><canvas class="spec"></canvas><div class="words"></div></div>
    <table>${rows.map(([k, val]) => `<tr><td>${k}</td><td>${val}</td></tr>`).join("")}</table>`;
  const audio = el.querySelector("audio"), wave = el.querySelector(".wave"), spec = el.querySelector(".spec"), wordsEl = el.querySelector(".words");
  const leakIdx = new Set((c.leak_runs || []).flatMap(r => range(r.first, r.last)));
  c.words.forEach((w, i) => {
    const s = document.createElement("span"); s.textContent = w.w; s.title = `${w.s.toFixed(2)}–${w.e.toFixed(2)} s`;
    s.style.left = (100 * w.s / c.duration) + "%"; s.style.maxWidth = Math.max(2, 100 * (w.e - w.s) / c.duration + 4) + "%";
    if (leakIdx.has(i)) s.classList.add("leak");
    s.onclick = () => { audio.currentTime = w.s; audio.play(); };
    wordsEl.appendChild(s);
  });
  const seek = e => { const r = e.target.getBoundingClientRect(); audio.currentTime = (e.clientX - r.left) / r.width * c.duration; };
  wave.onclick = seek; spec.onclick = seek;
  const specImg = specImage(c.spec);
  const draw = () => { drawWave(wave, c, audio.currentTime); drawSpec(spec, c, specImg, audio.currentTime);
    wordsEl.querySelectorAll("span").forEach((s, i) => s.classList.toggle("now", audio.currentTime >= c.words[i].s && audio.currentTime < c.words[i].e)); };
  let raf = 0; const loop = () => { draw(); raf = audio.paused ? 0 : requestAnimationFrame(loop); };
  audio.addEventListener("play", () => { if (!raf) loop(); }); audio.addEventListener("seeked", draw); audio.addEventListener("pause", draw);
  new ResizeObserver(draw).observe(wave);
  return el;
}
function range(a, b) { const r = []; for (let i = a; i <= b; i++) r.push(i); return r; }

function sized(cv) {
  const r = devicePixelRatio || 1, w = cv.clientWidth * r, h = cv.clientHeight * r;
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const g = cv.getContext("2d"); g.setTransform(1, 0, 0, 1, 0, 0); return [g, w, h, r];
}

function overlays(g, c, W, H, r, full) {
  const X = t => t / c.duration * W;
  g.fillStyle = "rgba(85,85,85,.35)"; g.fillRect(0, 0, X(0.5), H);
  if (c.duration > D.constants.max_scored_seconds) g.fillRect(X(D.constants.max_scored_seconds), 0, W, H);
  if (c.prompt_region) { g.fillStyle = "rgba(240,160,64,.22)"; g.fillRect(X(c.prompt_region.start), 0, X(c.prompt_region.end) - X(c.prompt_region.start), H); }
  if (full && c.prompt_region?.tone_found) { g.fillStyle = "#ff5d5d"; g.fillRect(X(c.tone_at), 0, Math.max(r, X(0.15)), H); }
  if (c.window != null) { g.strokeStyle = "#4aa3ff"; g.lineWidth = 2 * r; g.fillStyle = "rgba(74,163,255,.10)"; g.fillRect(X(c.window), 0, X(3), H); g.strokeRect(X(c.window), r, X(3), H - 2 * r); }
  if (c.shifted_window != null && c.shifted_window !== c.window) { g.setLineDash([6 * r, 4 * r]); g.strokeStyle = "#5fe0d0"; g.strokeRect(X(c.shifted_window), 3 * r, X(3), H - 6 * r); g.setLineDash([]); }
  if (full && c.heard_offset != null) { g.fillStyle = "#c58cff"; g.fillRect(X(c.heard_offset), 0, 2 * r, H); }
}

function drawWave(cv, c, t) {
  const [g, W, H, r] = sized(cv); const [mn, mx] = c.peaks, n = mn.length, X = t => t / c.duration * W;
  g.fillStyle = "#101216"; g.fillRect(0, 0, W, H);
  overlays(g, c, W, H, r, true);
  if (c.active) {
    const bits = atob(c.active); g.fillStyle = "#6fd36f";
    for (let f = 0; f < c.active_frames; f++) if (bits.charCodeAt(f >> 3) & (128 >> (f & 7))) g.fillRect(X(0.5 + f * 0.02), H - 4 * r, Math.max(1, X(0.02)), 4 * r);
  }
  const peak = Math.max(1e-3, ...mx.map(Math.abs), ...mn.map(Math.abs));
  g.fillStyle = "#cfd5dd";
  for (let i = 0; i < n; i++) { const x = i / n * W, y1 = H / 2 - mx[i] / peak * H * 0.45, y2 = H / 2 - mn[i] / peak * H * 0.45; g.fillRect(x, y1, Math.max(1, W / n), Math.max(1, y2 - y1)); }
  g.fillStyle = "#fff"; g.fillRect(X(t), 0, 2 * r, H);
}

function specImage(s) {
  const bytes = Uint8Array.from(atob(s.data), ch => ch.charCodeAt(0));
  const cv = document.createElement("canvas"); cv.width = s.cols; cv.height = s.rows;
  const g = cv.getContext("2d"), img = g.createImageData(s.cols, s.rows);
  for (let x = 0; x < s.cols; x++) for (let y = 0; y < s.rows; y++) {
    const v = bytes[x * s.rows + y] / 255, o = ((s.rows - 1 - y) * s.cols + x) * 4;
    img.data[o] = 255 * Math.min(1, v * 1.8); img.data[o + 1] = 255 * Math.max(0, v * 1.6 - 0.5); img.data[o + 2] = 255 * Math.max(0, 0.5 - Math.abs(v - 0.3)) + 40 * v; img.data[o + 3] = 255;
  }
  g.putImageData(img, 0, 0); return cv;
}

function drawSpec(cv, c, img, t) {
  const [g, W, H, r] = sized(cv); g.imageSmoothingEnabled = true; g.drawImage(img, 0, 0, W, H);
  overlays(g, c, W, H, r, false);
  g.fillStyle = "rgba(255,255,255,.5)"; g.font = `${10 * r}px sans-serif`; g.fillText("4 kHz", 3 * r, 11 * r); g.fillText("880 Hz", 3 * r, H - H * 880 / 4000 - 2 * r);
  g.fillRect(0, H - H * 880 / 4000, 8 * r, r);
  g.fillStyle = "#fff"; g.fillRect(t / c.duration * W, 0, 2 * r, H);
}
</script></body></html>
"""

if __name__ == "__main__":
    main()
