# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Audition the opening pair: call_started ("Line open.") and agent_connected ("Dogs awake.").

    uv run --script opening_audition.py

These two set the tone for every call, so they are designed as one musical phrase: the
line opening is an antecedent that leaves something unresolved while the dogs wake, and
agent_connected is the consequent that resolves it. Each candidate also has a one-shot for a
call whose Sandbox is already awake, when agent_connected never plays (voice_session.ts plays it
only if the dogs were waking when the call attached), so that version must resolve alone.

Rendering reuses earcon_lab.py's FM renderer and phone-line simulation, so timbres match
the lab's system family. Optionally the browser speaks a stand-in opening line in the gap,
as GPT-Live would.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from earcon_lab import synth_js  # noqa: E402

# Pitches in C major, all inside the 300-3400 Hz telephone band.
C5, D5, E5, F5, G5, A5, C6 = 523.25, 587.33, 659.25, 698.46, 783.99, 880.0, 1046.5

# Each phrase: [[at ms, length ms, Hz, {overrides}], ...]. Overrides apply to the lab's
# system family (1:1 FM, bright attack mellowing, small upward flick): voices adds dyad
# partners as pitch ratios, i0 sets brightness, k sets how fast a note decays.
CANDIDATES = [
    {
        "title": "Half cadence",
        "why": "Line open climbs C to G and stops on the dominant: a question. Dogs awake answers G to high C, the tonic an octave up, with a fifth underneath. Plainest antecedent and consequent.",
        "open": [[0, 90, C5, {}], [90, 230, G5, {"k": 2.5}]],
        "awake": [[0, 90, G5, {}], [90, 280, C6, {"voices": [[1, 1], [0.75, 0.6]]}]],
        "ready": [[0, 80, C5, {}], [80, 80, G5, {}], [160, 280, C6, {"voices": [[1, 1], [0.75, 0.6]]}]],
    },
    {
        "title": "Suspension",
        "why": "Line open is a held C and F dyad (a sus4) that hangs in the air. Dogs awake lets the F fall to E inside a full C major triad: the classic 4 to 3 resolution, the most 'settled' gesture in tonal music.",
        "open": [[0, 340, C5, {"voices": [[1, 1], [4 / 3, 0.8]], "k": 2, "i0": 1.8}]],
        "awake": [[0, 70, F5, {"k": 1}], [70, 360, C5, {"voices": [[1, 1], [5 / 4, 0.8], [3 / 2, 0.7]], "k": 2.5}]],
        "ready": [[0, 160, C5, {"voices": [[1, 1], [4 / 3, 0.8]], "k": 1, "i0": 1.8}], [160, 360, C5, {"voices": [[1, 1], [5 / 4, 0.8], [3 / 2, 0.7]], "k": 2.5}]],
    },
    {
        "title": "Ladder",
        "why": "One pentatonic ladder split in two. Line open climbs two rungs and stops on D, a passing tone that wants to keep going. Dogs awake climbs the rest, E G C. The gap sits in the middle of the climb, so waiting feels like progress paused.",
        "open": [[0, 80, C5, {}], [95, 170, D5, {"k": 3}]],
        "awake": [[0, 70, E5, {}], [80, 70, G5, {}], [160, 260, C6, {}]],
        "ready": [[0, 60, C5, {}], [70, 60, D5, {}], [140, 60, E5, {}], [210, 60, G5, {}], [280, 260, C6, {}]],
    },
    {
        "title": "Brightening",
        "why": "Progress as timbre, not pitch: line open plays C to G with a dim, nearly pure tone (low FM index), like a panel warming up. Dogs awake plays G to C with the full bright FM chirp and a fifth: same shape, now fully lit.",
        "open": [[0, 100, C5, {"i0": 0.5, "i1": 0.2}], [100, 230, G5, {"i0": 0.7, "i1": 0.2, "k": 2.5}]],
        "awake": [[0, 90, G5, {"i0": 3.2}], [90, 280, C6, {"i0": 3.6, "voices": [[1, 1], [0.75, 0.6]]}]],
        "ready": [[0, 80, C5, {"i0": 0.6, "i1": 0.2}], [80, 80, G5, {"i0": 1.8}], [160, 280, C6, {"i0": 3.6, "voices": [[1, 1], [0.75, 0.6]]}]],
    },
    {
        "title": "Question and answer",
        "why": "A recognizable motif stated twice. Line open: E D G, ending up on the dominant like a spoken question. Dogs awake repeats E D, then lands on high C instead. Hearing the motif return tells you which event this is before it resolves.",
        "open": [[0, 80, E5, {}], [90, 80, D5, {}], [180, 220, G5, {"k": 2.5}]],
        "awake": [[0, 80, E5, {}], [90, 80, D5, {}], [180, 300, C6, {"voices": [[1, 1], [0.75, 0.6]]}]],
        "ready": [[0, 70, E5, {}], [80, 70, D5, {}], [160, 70, G5, {}], [240, 300, C6, {"voices": [[1, 1], [0.75, 0.6]]}]],
    },
]

PAGE_JS = r"""
const CANDIDATES = __CANDIDATES__;
const SYSTEM = MAP.source.system, LAYER = MAP.layer.fg;
// A phrase becomes lab notes in the system family; render() is the lab's FM renderer.
function phrase(steps) {
  const notes = steps.map(([at, ms, f, o]) => ({
    c: SYSTEM.c, m: SYSTEM.m, i0: SYSTEM.i0, i1: SYSTEM.i1, tau: SYSTEM.tau, attack: SYSTEM.attack,
    k: SYSTEM.k, glide: SYSTEM.glide, rough: 0, roughRate: 75, cut: false, voices: [[1, 1]],
    at, ms, f, ...o,
  }));
  return render({ notes, gain: LAYER.gain });
}
let ctx, timers = [], sources = [];
const $ = id => document.getElementById(id);
const audio = () => (ctx ??= new AudioContext({ sampleRate: SR }));
function playSamples(x) {
  if ($("phone").checked || $("g711").checked) x = phoneLine(x, $("g711").checked);
  const ac = audio(), buf = ac.createBuffer(1, x.length, SR);
  buf.getChannelData(0).set(x);
  const src = ac.createBufferSource(), g = ac.createGain();
  src.buffer = buf; g.gain.value = +$("vol").value;
  src.connect(g); g.connect(ac.destination); src.start(); sources.push(src);
  return x.length / SR;
}
function stopAll() {
  timers.forEach(clearTimeout); timers = [];
  sources.forEach(s => { try { s.stop(); } catch {} }); sources = [];
  speechSynthesis.cancel();
}
function speak(text) {
  const u = new SpeechSynthesisUtterance(text); u.rate = 1.05; speechSynthesis.speak(u);
}
// The call opening: line open, the spoken welcome, then dogs awake after the gap.
function sequence(open, awake) {
  stopAll();
  const seconds = playSamples(open);
  if ($("speak").checked) timers.push(setTimeout(() => speak($("line").value), seconds * 1000 + 250));
  timers.push(setTimeout(() => playSamples(awake), +$("gap").value * 1000));
}
const reference = {
  open: () => earconSamples("call_started", "current"), awake: () => earconSamples("agent_connected", "current"),
  labOpen: () => earconSamples("call_started", "new"), labAwake: () => earconSamples("agent_connected", "new"),
};
const list = $("candidates");
const rows = [
  { title: "Current (deployed tonal)", why: "For reference: what plays today.", open: reference.open, awake: reference.awake, ready: null },
  { title: "Lab FM (from the code)", why: "For reference: what the semantic code produced, which you found off.", open: reference.labOpen, awake: reference.labAwake, ready: null },
  ...CANDIDATES.map(c => ({ ...c, open: () => phrase(c.open), awake: () => phrase(c.awake), ready: () => phrase(c.ready) })),
];
rows.forEach((row, i) => {
  const card = document.createElement("div"); card.className = "card" + (i < 2 ? " ref" : "");
  card.innerHTML = `<h3>${i < 2 ? "" : (i - 1) + ". "}${row.title}</h3><p>${row.why}</p>`;
  const buttons = [["Line open", () => playSamples(row.open())], ["Dogs awake", () => playSamples(row.awake())],
    ["▶ Opening sequence", () => sequence(row.open(), row.awake())]];
  if (row.ready) buttons.push(["Already awake (one-shot)", () => { stopAll(); playSamples(row.ready()); if ($("speak").checked) timers.push(setTimeout(() => speak($("line").value), 800)); }]);
  for (const [label, fn] of buttons) {
    const b = document.createElement("button"); b.textContent = label; b.onclick = fn;
    if (label.startsWith("▶")) b.className = "primary";
    card.append(b);
  }
  list.append(card);
});
$("gap").oninput = () => $("gapv").textContent = $("gap").value + " s";
$("stop").onclick = stopAll;
"""

HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Dogwalk opening audition</title>
<style>
  :root { color-scheme: dark; --bg: #16171a; --card: #202226; --card2: #2a2d33; --fg: #e8e6e1; --muted: #9a978f; --accent: #e0a458; }
  body { margin: 0; padding: 28px; background: var(--bg); color: var(--fg); font: 15px/1.45 -apple-system, system-ui, sans-serif; max-width: 980px; }
  h1 { font-size: 1.4rem; margin: 0 0 4px; } p.note { color: var(--muted); max-width: 75ch; }
  .controls { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; padding: 12px 16px; background: var(--card); border-radius: 10px; margin-bottom: 14px; }
  .controls input[type=text] { width: 380px; background: var(--card2); color: var(--fg); border: 0; border-radius: 6px; padding: 5px 8px; font: inherit; }
  .card { background: var(--card); border-radius: 10px; padding: 14px 16px; margin-bottom: 10px; }
  .card.ref { opacity: .8; } .card h3 { margin: 0 0 4px; font-size: 1.05rem; } .card p { margin: 0 0 10px; color: var(--muted); }
  button { background: var(--card2); color: var(--fg); border: 0; border-radius: 6px; padding: 6px 12px; font: inherit; cursor: pointer; margin: 0 6px 4px 0; }
  button.primary { background: var(--accent); color: #1a1a1a; }
</style></head><body>
<h1>The opening pair</h1>
<p class="note">Line open plays as the call connects; Dogs awake plays when the Sandbox finishes waking, anywhere from a few seconds to a minute later, usually after the welcome line. If the Sandbox is already awake, only Line open plays, so each candidate also has an <b>already awake</b> one-shot that resolves on its own. Try the sequence with the welcome spoken in between and a realistic gap.</p>
<div class="controls">
  <label>Gap <input type="range" id="gap" min="1" max="30" step="0.5" value="8"> <span id="gapv">8 s</span></label>
  <label><input type="checkbox" id="speak" checked> Speak the welcome</label>
  <input type="text" id="line" value="Verified. Dogwalk. Last time Juniper fixed the parser tests. Where to?">
  <label><input type="checkbox" id="phone"> Phone line</label>
  <label><input type="checkbox" id="g711"> G.711</label>
  <label>Volume <input type="range" id="vol" min="0" max="2" step="0.05" value="1"></label>
  <button id="stop">Stop</button>
</div>
<div id="candidates"></div>
<script>__SCRIPT__</script></body></html>
"""


def page_script() -> str:
    return synth_js() + PAGE_JS.replace("__CANDIDATES__", json.dumps(CANDIDATES))


def main() -> None:
    tmp = Path(tempfile.gettempdir())
    if "--check" in sys.argv:
        script = tmp / "dogwalk_opening_audition.js"
        script.write_text(page_script())
        subprocess.run(["node", "--check", str(script)], check=True)
        print(f"ok {script}")
        return
    page = tmp / "dogwalk_opening_audition.html"
    page.write_text(HTML.replace("__SCRIPT__", page_script()))
    subprocess.run(["open", str(page)], check=False)
    print(page)


if __name__ == "__main__":
    main()
