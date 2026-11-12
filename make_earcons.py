# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Render spoken earcons with macOS text-to-speech into the Worker's bundle.

    uv run --script make_earcons.py                 # Fred, the default
    uv run --script make_earcons.py --voice Zarvox  # any `say -v '?'` voice
    uv run --script make_earcons.py --board         # open a soundboard of what is bundled

Each label becomes cf_worker/earcons/<event>.pcm: raw mono PCM16LE at 24 kHz, leveled
a little below speech, ready for a GPT-Live output fixture. Keypad menu prompts render
the same way, a little slower and louder. Rendering happens here
because a Worker cannot run `say`; the files are committed and bundled as Data modules.
Event names are neutral; the spoken labels are presentation and may use the walk's fiction.
"""

from __future__ import annotations

import argparse
import array
import base64
import json
import math
import re
import subprocess
import tempfile
import wave
from pathlib import Path

OUT = Path(__file__).resolve().parent / "cf_worker" / "earcons"

LABELS = {
    "call_started": "Line open.",
    "call_started_ready": "Line open. Dogs awake.",
    "session_created": "New dog.",
    "turn_started": "Dog sent.",
    "turn_stopped": "Dog back.",
    "turn_failed": "Errand failed.",
    "agent_connected": "Dogs awake.",
    "turn_cancelled": "Dog recalled.",
    "turn_truncated": "Dog ran out of room.",
    "turn_refused": "Dog refused.",
    "permission_requested": "Permission needed.",
    "session_closed": "Dog home.",
    "recording_started": "Recording.",
    "recording_saved": "Recording saved.",
    "whiteboard_answer": "Answer received.",
    "recording_discarded": "Recording discarded.",
    "follow_started": "Following.",
    "follow_stopped": "Ranging.",
    "progress_looking": "Sniffing.",
    "progress_changing": "Digging.",
    "progress_running": "Running.",
    "progress_still_running": "Still running.",
    "progress_failed": "Stumbled.",
    "progress_remark": "Woof.",
    "progress_step": "Next step.",
    "progress_plan_done": "Plan done.",
    "progress_context_low": "Tiring.",
    "bid": "Tug.",
}

# Keypad system menu prompts: always spoken whatever the caller's earcon style, because they are
# instructions the caller must understand rather than event markers. Slower and louder.
PROMPTS = {
    "menu": "Menu. One, restart the sandbox. Two, recovery options. Star, back.",
    "menu_restart_confirm": "Restarting stops running work. Press one again to confirm.",
    "menu_restarting": "Restarting the sandbox.",
    "menu_back": "Back.",
}

TARGET_RMS = 0.06  # about -24 dBFS: audible, but quieter than the walker's voice
PROMPT_RMS = 0.09  # about -21 dBFS: closer to speech, since the caller must follow it


def render(text: str, voice: str, rate: int, target_rms: float = TARGET_RMS) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        aiff, wav = Path(tmp) / "label.aiff", Path(tmp) / "label.wav"
        subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1", str(aiff), str(wav)], check=True)
        with wave.open(str(wav)) as w:
            samples = array.array("h", w.readframes(w.getnframes()))
    # Trim leading and trailing near-silence so the clip starts immediately.
    threshold = 300
    start = next((i for i, s in enumerate(samples) if abs(s) > threshold), 0)
    end = len(samples) - next((i for i, s in enumerate(reversed(samples)) if abs(s) > threshold), 0)
    samples = samples[max(0, start - 240): min(len(samples), end + 1200)]
    rms = math.sqrt(sum(s * s for s in samples) / max(1, len(samples))) / 32768
    gain = min(target_rms / rms if rms else 1, 0.6 * 32767 / max(1, max(abs(s) for s in samples)))
    return array.array("h", (max(-32768, min(32767, round(s * gain))) for s in samples)).tobytes()


# --- Soundboard ---------------------------------------------------------------
# A local page for auditioning earcons: Fred's clips are the bundled .pcm files, and the
# tonal ones are synthesized in the browser from the notes in audio.ts, with the same
# envelope, so the page plays exactly what a call would.

AUDIO_TS = OUT.parent / "src" / "audio.ts"
GROUPS = [
    ("Call", ["call_started", "agent_connected", "call_started_ready"]),
    ("Sessions", ["session_created", "session_closed"]),
    ("Prompt Turns", ["turn_started", "turn_stopped", "turn_cancelled", "turn_truncated", "turn_refused", "turn_failed"]),
    ("Attention", ["permission_requested", "bid"]),
    ("Following", ["follow_started", "follow_stopped"]),
    ("Progress Beats", ["progress_looking", "progress_changing", "progress_running", "progress_still_running",
                        "progress_failed", "progress_remark", "progress_step", "progress_plan_done", "progress_context_low"]),
    ("Recording", ["recording_started", "recording_saved", "recording_discarded"]),
    ("Whiteboard", ["whiteboard_answer"]),
]
NEW = {"bid", "follow_started", "follow_stopped"} | {name for name in LABELS if name.startswith("progress_")}
# [earcon, seconds after the previous one]
SCENES = {
    "A followed Prompt Turn": [
        ("turn_started", 0), ("progress_looking", 2.0), ("progress_remark", 2.2), ("progress_step", 1.6),
        ("progress_changing", 2.0), ("progress_running", 2.0), ("progress_still_running", 2.4),
        ("progress_failed", 1.8), ("progress_running", 1.6), ("progress_plan_done", 2.0), ("turn_stopped", 1.4),
    ],
    "Juggling two dogs": [
        ("turn_started", 0), ("progress_looking", 1.8), ("turn_started", 2.2), ("progress_changing", 2.0),
        ("bid", 2.2), ("follow_started", 2.0), ("progress_context_low", 2.0), ("permission_requested", 2.0),
        ("follow_stopped", 2.2), ("turn_stopped", 2.0),
    ],
}


def tonal_notes() -> dict[str, dict]:
    """Parse `name: synthesize([[hz, ms], ...], gain)` lines out of audio.ts's TONAL table.

    The plain note set was retired on 2026-11-08 (tonal now means the FM designs), so this is
    empty unless an older audio.ts is checked out; callers fall back to other renderings.
    """
    source = AUDIO_TS.read_text()
    if "const TONAL" not in source:
        return {}
    table = source[source.index("const TONAL"):source.index("const SPOKEN")]
    notes = {}
    for match in re.finditer(r"^\s+(\w+): synthesize\((\[\[.*?\]\])(?:,\s*([\d.]+))?\),", table, re.M):
        notes[match[1]] = {"notes": json.loads(match[2]), "gain": float(match[3] or 0.28)}
    return notes


def board() -> Path:
    clips = {path.stem: base64.b64encode(path.read_bytes()).decode() for path in sorted(OUT.glob("*.pcm"))}
    labels = {**LABELS, **PROMPTS}
    grouped = GROUPS + [("Keypad menu (always spoken)", list(PROMPTS))]
    placed = {name for _, names in grouped for name in names}
    if extra := [name for name in LABELS if name not in placed]:
        grouped.append(("Other", extra))
    data = {"clips": clips, "tonal": tonal_notes(), "labels": labels, "groups": grouped, "new": sorted(NEW), "scenes": SCENES}
    page = Path(tempfile.gettempdir()) / "dogwalk_earcon_board.html"
    page.write_text(BOARD_HTML.replace("__DATA__", json.dumps(data)))
    return page


BOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Dogwalk earcons</title>
<style>
  :root { color-scheme: dark; --bg: #16171a; --card: #202226; --card2: #2a2d33; --fg: #e8e6e1; --muted: #9a978f; --accent: #e0a458; --new: #7fb685; }
  body { margin: 0; padding: 28px; background: var(--bg); color: var(--fg); font: 15px/1.4 -apple-system, system-ui, sans-serif; }
  h1 { font-size: 1.4rem; margin: 0 0 4px; } h2 { font-size: 0.95rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; margin: 26px 0 10px; }
  p.note { color: var(--muted); margin: 0 0 18px; max-width: 70ch; }
  .controls { display: flex; flex-wrap: wrap; gap: 18px; align-items: center; padding: 12px 16px; background: var(--card); border-radius: 10px; }
  .seg button, .tile button, .scene { background: var(--card2); color: var(--fg); border: 0; border-radius: 6px; padding: 6px 12px; font: inherit; cursor: pointer; }
  .seg button[aria-pressed=true] { background: var(--accent); color: #1a1a1a; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; }
  .tile { background: var(--card); border-radius: 10px; padding: 12px; cursor: pointer; transition: background .1s; position: relative; }
  .tile:hover { background: var(--card2); } .tile.playing { outline: 2px solid var(--accent); }
  .tile .name { font: 12px ui-monospace, monospace; color: var(--muted); } .tile .label { font-size: 1.05rem; margin: 2px 0 8px; }
  .tile .badge { position: absolute; top: 10px; right: 10px; font-size: 11px; color: var(--new); border: 1px solid var(--new); border-radius: 4px; padding: 0 5px; }
  .tile button { padding: 3px 9px; font-size: 13px; margin-right: 4px; } .tile .meta { font: 11px ui-monospace, monospace; color: var(--muted); margin-top: 6px; }
  kbd { background: var(--card2); border-radius: 4px; padding: 0 5px; font-size: 12px; }
</style></head><body>
<h1>Dogwalk earcons</h1>
<p class="note">Fred clips are the bundled files; tonal ones are synthesized from <code>audio.ts</code> with the same envelope. Click a tile to play it in the current style. <b>Phone line</b> band-limits to 300&ndash;3400 Hz, roughly what PSTN leaves you. Green badges mark the following, beat, and pull earcons.</p>
<div class="controls">
  <span>Style <span class="seg" id="style"><button aria-pressed="true" data-v="spoken">Fred</button> <button aria-pressed="false" data-v="tonal">Tonal</button></span></span>
  <label><input type="checkbox" id="phone"> Phone line</label>
  <label>Volume <input type="range" id="vol" min="0" max="2" step="0.05" value="1"></label>
  <span id="scenes"></span> <button class="scene" id="stop">Stop</button>
</div>
<div id="board"></div>
<script>
const DATA = __DATA__;
const SR = 24000;
let ctx, style = "spoken", timers = [], sources = [];
const audio = () => (ctx ??= new AudioContext());

function spokenBuffer(name) {
  const bytes = Uint8Array.from(atob(DATA.clips[name]), c => c.charCodeAt(0));
  const pcm = new Int16Array(bytes.buffer, 0, bytes.length >> 1);
  const buf = audio().createBuffer(1, pcm.length, SR);
  buf.getChannelData(0).set(Float32Array.from(pcm, s => s / 32768));
  return buf;
}
// Mirrors synthesize() in audio.ts.
function tonalBuffer(name) {
  const { notes, gain } = DATA.tonal[name];
  const total = notes.reduce((sum, [, ms]) => sum + Math.round(ms / 1000 * SR), 0);
  const buf = audio().createBuffer(1, total, SR), out = buf.getChannelData(0);
  let offset = 0;
  for (const [f, ms] of notes) {
    const n = Math.round(ms / 1000 * SR);
    for (let i = 0; i < n; i++) {
      const t = i / SR, env = Math.min(1, i / 240) * Math.exp(-5 * (i / n));
      out[offset + i] = (Math.sin(2 * Math.PI * f * t) + 0.3 * Math.sin(4 * Math.PI * f * t)) * env * gain / 1.3;
    }
    offset += n;
  }
  return buf;
}
function play(name, as = style) {
  const kind = (as === "tonal" && DATA.tonal[name]) ? "tonal" : "spoken";
  if (kind === "spoken" && !DATA.clips[name]) return;
  const ac = audio(), src = ac.createBufferSource();
  src.buffer = kind === "tonal" ? tonalBuffer(name) : spokenBuffer(name);
  let node = src;
  if (document.getElementById("phone").checked) {
    for (const [type, freq] of [["highpass", 300], ["lowpass", 3400], ["lowpass", 3400]]) {
      const f = ac.createBiquadFilter(); f.type = type; f.frequency.value = freq; node.connect(f); node = f;
    }
  }
  const g = ac.createGain(); g.gain.value = +document.getElementById("vol").value; node.connect(g); g.connect(ac.destination);
  src.start(); sources.push(src);
  const tile = document.querySelector(`[data-name="${name}"]`);
  tile?.classList.add("playing"); src.onended = () => tile?.classList.remove("playing");
}
function stopAll() { timers.forEach(clearTimeout); timers = []; sources.forEach(s => { try { s.stop(); } catch {} }); sources = []; }

const board = document.getElementById("board"), isNew = new Set(DATA.new);
for (const [title, names] of DATA.groups) {
  const h = document.createElement("h2"); h.textContent = title; board.append(h);
  const grid = document.createElement("div"); grid.className = "grid"; board.append(grid);
  for (const name of names) {
    const tile = document.createElement("div"); tile.className = "tile"; tile.dataset.name = name;
    const tonal = DATA.tonal[name];
    const meta = tonal ? tonal.notes.map(([f, ms]) => f < 1 ? `rest ${ms}` : `${Math.round(f)}Hz ${ms}`).join(" \\u00b7 ") + (tonal.gain !== 0.28 ? `  gain ${tonal.gain}` : "") : "spoken only";
    tile.innerHTML = `${isNew.has(name) ? '<span class="badge">new</span>' : ""}<div class="name">${name}</div><div class="label">${DATA.labels[name] ?? ""}</div>`
      + `<button data-as="spoken">Fred</button>${tonal ? '<button data-as="tonal">Tonal</button>' : ""}<div class="meta">${meta}</div>`;
    tile.addEventListener("click", e => { e.stopPropagation(); play(name, e.target.dataset?.as ?? style); });
    grid.append(tile);
  }
}
document.querySelectorAll("#style button").forEach(b => b.onclick = () => {
  style = b.dataset.v; document.querySelectorAll("#style button").forEach(x => x.setAttribute("aria-pressed", x === b));
});
const scenes = document.getElementById("scenes");
for (const [title, steps] of Object.entries(DATA.scenes)) {
  const b = document.createElement("button"); b.className = "scene"; b.textContent = "\\u25b6 " + title;
  b.onclick = () => { stopAll(); let at = 0; for (const [name, gap] of steps) { at += gap * 1000; timers.push(setTimeout(() => play(name), at)); } };
  scenes.append(b, " ");
}
document.getElementById("stop").onclick = stopAll;
</script></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice", default="Fred")
    parser.add_argument("--rate", type=int, default=220, help="words per minute")
    parser.add_argument("--board", action="store_true", help="write and open a soundboard instead of rendering")
    args = parser.parse_args()
    if args.board:
        page = board()
        subprocess.run(["open", str(page)], check=False)
        print(page)
        return
    OUT.mkdir(exist_ok=True)
    for event, text in LABELS.items():
        pcm = render(text, args.voice, args.rate)
        (OUT / f"{event}.pcm").write_bytes(pcm)
        print(f"{event:22} {len(pcm) / 48000:4.2f}s  {text}")
    for name, text in PROMPTS.items():
        pcm = render(text, args.voice, args.rate - 40, PROMPT_RMS)
        (OUT / f"{name}.pcm").write_bytes(pcm)
        print(f"{name:22} {len(pcm) / 48000:4.2f}s  {text}")


if __name__ == "__main__":
    main()
