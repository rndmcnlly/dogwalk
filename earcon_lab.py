# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Earcon lab: audition the current tonal earcons against a semantic FM redesign.

    uv run --script earcon_lab.py              # write and open the A/B soundboard
    uv run --script earcon_lab.py --render DIR # write current_*.wav and new_*.wav (needs node)
    uv run --script earcon_lab.py --check      # node --check the page's script
    uv run --script earcon_lab.py --export     # write the designs the Worker plays (needs node)

The new vocabulary is derived from a semantic code, not tuned sound by sound. CODES
classifies each earcon along Adam's axes (source, polarity, layer, normality, demand,
initiator) plus two lab additions (scope, activity); MAP turns each axis value into
synthesis parameters; OVERRIDES records every place the code alone could not separate
two earcons, with the reason. SYNTH_JS is the renderer: plain math on Float32Array at
24 kHz, no randomness, no Web Audio in the synthesis path, so it ports to audio.ts as is.
See SOUND_DESIGN_NOTES.md for the research behind it. Prototype only: nothing here is
deployed, and audio.ts stays the source of truth for the current table.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_earcons import GROUPS, LABELS, SCENES, tonal_notes  # noqa: E402

EXPORT = Path(__file__).resolve().parent / "cf_worker" / "src" / "earcon_designs.json"

# --- Semantic code --------------------------------------------------------------
# source:    who the event is about        -> timbre family (FM carrier:modulator, envelope)
# polarity:  onset / offset / state        -> contour (rise from home, fall from home, level)
# layer:     fg / mid / bg                 -> loudness, length, brightness (FM index)
# normality: routine / abnormal            -> harmonicity (integer vs tritone and sqrt(2) ratios, 75 Hz roughness)
# demand:    none / optional / required    -> repetition (1, 2, or 3 statements of the gesture)
# initiator: caller / news                 -> texture (caller's own action: plain and shorter; news: a dyad, fuller)
# scope:     call / session / turn / step / follow / take / -  -> interval size (lab addition)
# activity:  what a Progress Beat reports  -> a fixed rhythmic shape (lab addition, replaces contour)
CODE_FIELDS = ["source", "polarity", "layer", "normality", "demand", "initiator", "scope", "activity"]
CODES = {
    # name                    source    polarity  layer  normality   demand      initiator scope      activity
    "call_started":          ("system", "onset",  "fg",  "routine",  "none",     "caller", "call",    None),
    "call_started_ready":    ("system", "onset",  "fg",  "routine",  "none",     "caller", "call",    None),
    "dogs_ready":            ("system", "onset",  "mid", "routine",  "none",     "news",   "call",    None),
    "session_created":       ("dog",    "onset",  "mid", "routine",  "none",     "caller", "session", None),
    "session_closed":        ("dog",    "offset", "mid", "routine",  "none",     "caller", "session", None),
    "turn_started":          ("dog",    "onset",  "mid", "routine",  "none",     "caller", "turn",    None),
    "turn_stopped":          ("dog",    "offset", "fg",  "routine",  "none",     "news",   "turn",    None),
    "turn_cancelled":        ("dog",    "offset", "mid", "routine",  "none",     "caller", "turn",    None),
    "turn_truncated":        ("dog",    "offset", "fg",  "abnormal", "none",     "news",   "turn",    None),
    "turn_refused":          ("dog",    "offset", "fg",  "abnormal", "none",     "news",   "turn",    None),
    "turn_failed":           ("dog",    "offset", "fg",  "abnormal", "none",     "news",   "turn",    None),
    "permission_requested":  ("dog",    "state",  "fg",  "routine",  "required", "news",   "-",       None),
    "pull":                  ("dog",    "state",  "fg",  "routine",  "optional", "news",   "-",       None),
    "follow_started":        ("walker", "onset",  "mid", "routine",  "none",     "caller", "follow",  None),
    "follow_stopped":        ("walker", "offset", "mid", "routine",  "none",     "caller", "follow",  None),
    "recording_started":     ("caller", "onset",  "mid", "routine",  "none",     "caller", "take",    None),
    "recording_saved":       ("caller", "offset", "mid", "routine",  "none",     "caller", "take",    None),
    "recording_discarded":   ("caller", "offset", "mid", "abnormal", "none",     "caller", "take",    None),
    "whiteboard_answer":     ("caller", "state",  "mid", "routine",  "none",     "caller", "take",    None),
    "progress_looking":      ("dog",    "state",  "bg",  "routine",  "none",     "news",   "step",    "read"),
    "progress_changing":     ("dog",    "state",  "bg",  "routine",  "none",     "news",   "step",    "edit"),
    "progress_running":      ("dog",    "state",  "bg",  "routine",  "none",     "news",   "step",    "execute"),
    "progress_still_running":("dog",    "state",  "bg",  "routine",  "none",     "news",   "step",    "execute_ongoing"),
    "progress_remark":       ("dog",    "state",  "bg",  "routine",  "none",     "news",   "step",    "speak"),
    "progress_step":         ("dog",    "onset",  "bg",  "routine",  "none",     "news",   "step",    None),
    "progress_plan_done":    ("dog",    "offset", "bg",  "routine",  "none",     "news",   "turn",    None),
    "progress_failed":       ("dog",    "offset", "bg",  "abnormal", "none",     "news",   "step",    None),
    "progress_tiring":       ("dog",    "state",  "bg",  "abnormal", "none",     "news",   "turn",    None),
}

# Where the code alone collides or misleads. Keys: register (x home pitch), contour (pitch
# ratios replacing the polarity contour), cut (last note stops abruptly), rough / roughRate
# (amplitude-modulation depth and Hz), lastMs (last note length). Every entry needs a why.
OVERRIDES = {
    # The opening pair is one phrase across two events (Adam chose "Brightening" in
    # opening_audition.py): the line opens dim and unresolved on the dominant, and the dogs
    # waking answers the same rise fully bright, landing on the tonic. A phrase is
    # [[at ms, length ms, Hz, {note overrides}], ...] in the system family.
    "call_started": {"gain": 0.28, "phrase": [[0, 100, 523.25, {"i0": 0.5, "i1": 0.2}], [100, 230, 783.99, {"i0": 0.7, "i1": 0.2, "k": 2.5}]],
                     "why": "Antecedent of the opening phrase: C to G, dim, ending on the dominant so the wait for the dogs feels unresolved."},
    "dogs_ready": {"gain": 0.28, "phrase": [[0, 90, 783.99, {"i0": 3.2}], [90, 280, 1046.5, {"i0": 3.6, "voices": [[1, 1], [0.75, 0.6]]}]],
                   "why": "Consequent of the opening phrase: G to high C, fully bright, with a fifth under the tonic. Foreground loudness, since it resolves call_started."},
    "call_started_ready": {"gain": 0.28, "phrase": [[0, 80, 523.25, {"i0": 0.6, "i1": 0.2}], [80, 80, 783.99, {"i0": 1.8}], [160, 280, 1046.5, {"i0": 3.6, "voices": [[1, 1], [0.75, 0.6]]}]],
                           "why": "The whole opening phrase at once, brightening as it climbs, for a call whose dogs are already awake (dogs_ready never follows)."},
    "turn_truncated": {"cut": True, "rough": 0,
                       "why": "Same code as turn_failed. Running out of room is a cutoff, not a breakage: the falling tritone stops mid-note, without grit."},
    "turn_refused": {"contour": [0.5, 0.5],
                     "why": "Same code as turn_failed. A refusal is a flat 'no', so it keeps the roughness but drops to a low level pair, after the TNG deny buzz."},
    "progress_tiring": {"register": 0.75, "rough": 0.6, "roughRate": 6, "lastMs": 300,
                        "why": "Weariness reads as a slow 6 Hz wobble rather than 75 Hz grit, and needs length to read as a state."},
}

# Intervals are just-intonation ratios from each family's home pitch. The homes (A4, E5, G5,
# C6) spell one A minor seventh chord, so overlapping earcons stay consonant unless they mean not to.
MAP = {
    "source": {
        # home Hz, carrier and modulator ratios, FM index from i0 decaying to i1 over tau ms,
        # attack ms, decay k (exp(-k t / len); 0 means flat), glide (start pitch ratio),
        # level (loudness trim so a flat or breathy family matches the others' RMS).
        "system": {"home": 783.99, "c": 1, "m": 1, "i0": 2.6, "i1": 0.3, "tau": 25, "attack": 1, "k": 4, "glide": 0.92, "level": 1,
                   "why": "Panel chirp: a bright 1:1 FM attack that mellows, with an 8 ms upward pitch flick."},
        "walker": {"home": 659.25, "c": 1, "m": 2, "i0": 1.4, "i1": 0.2, "tau": 60, "attack": 12, "k": 3, "glide": 1, "level": 0.7,
                   "why": "Hollow 1:2 FM (odd harmonics) with a soft attack: the walker's own, breathier voice."},
        "dog": {"home": 440.0, "c": 3, "m": 1, "i0": 1.6, "i1": 0.6, "tau": 30, "attack": 1.5, "k": 5, "glide": 1, "level": 1,
                "why": "3:1 FM puts the energy on the third harmonic, like the measured TNG beeps; pitch stays at home via the residue."},
        "caller": {"home": 1046.5, "c": 1, "m": 1, "i0": 0.25, "i1": 0.25, "tau": 1, "attack": 5, "k": 0, "glide": 1, "level": 0.3,
                   "why": "A nearly pure, flat tone like a recorder's indicator: plain, because the content is the caller's own voice."},
    },
    "layer": {
        # gain, FM index scale, first and last note ms, gap ms between repeated gestures
        "fg": {"gain": 0.28, "index": 1.0, "first": 100, "last": 190, "gap": 60},
        "mid": {"gain": 0.22, "index": 0.75, "first": 90, "last": 150, "gap": 50},
        "bg": {"gain": 0.14, "index": 0.45, "first": 40, "last": 70, "gap": 30},
    },
    "normality": {
        # interval: null keeps the scope's consonant interval; mDetune multiplies the
        # modulator ratio (sqrt 2 makes the spectrum inharmonic); harmony is the dyad
        # partner for news; rough is AM depth at roughRate Hz (75 Hz from the TNG deny buzz).
        "routine": {"interval": None, "mDetune": 1, "harmony": 1.5, "rough": 0, "roughRate": 75},
        "abnormal": {"interval": 1.41421356, "mDetune": 1.41421356, "harmony": 1.0666667, "rough": 0.5, "roughRate": 75},
    },
    "polarity": {"onset": "rise from home by the interval", "offset": "fall from home by the interval", "state": "level at home"},
    "demand": {"none": {"repeats": 1}, "optional": {"repeats": 2}, "required": {"repeats": 3}},
    "initiator": {"caller": {"durScale": 0.8, "dyad": False}, "news": {"durScale": 1.0, "dyad": True}},
    "scope": {"call": 2, "session": 2, "turn": 1.5, "step": 1.25, "follow": 1.6666667, "take": 1.3333333, "-": 1},
    # [pitch ratio, ms, gap after ms]: fixed shapes standing in for TNG's random burbles.
    "activity": {
        "read": [[1.5, 30, 25], [1.5, 30, 0]],
        "edit": [[0.75, 35, 10], [1, 35, 10], [0.75, 45, 0]],
        "execute": [[1, 16, 10], [1, 16, 10], [1, 16, 0]],
        "execute_ongoing": [[1, 16, 18]] * 7 + [[1, 16, 0]],
        "speak": [[1, 40, 5], [1.2, 40, 5], [0.9, 70, 0]],
    },
    "activityWhy": {
        "read": "two high pips: a glance",
        "edit": "low-high-low: back and forth, digging",
        "execute": "three gated pulses at a 26 ms period, the measured TNG 'processing' gate: machinery",
        "execute_ongoing": "the same gate held for eight pulses: machinery still turning",
        "speak": "a rise and fall like a spoken phrase",
    },
}

# --- Portable synthesis ------------------------------------------------------------
# Everything between the markers is DOM-free and deterministic: design() turns a code into
# note events, render() turns events into a Float32Array, phoneLine() simulates PSTN.
SYNTH_JS = r"""
const SR = 24000;
const MAP = __MAP__;
const CODES = __CODES__;
const OVERRIDES = __OVERRIDES__;
const TONAL = __TONAL__;

// Mirrors synthesize() in audio.ts, for the A side.
function renderTonal(notes, gain) {
  const total = notes.reduce((sum, [, ms]) => sum + Math.round(ms / 1000 * SR), 0);
  const out = new Float32Array(total);
  let offset = 0;
  for (const [f, ms] of notes) {
    const n = Math.round(ms / 1000 * SR);
    for (let i = 0; i < n; i++) {
      const t = i / SR, env = Math.min(1, i / 240) * Math.exp(-5 * (i / n));
      out[offset + i] = (Math.sin(2 * Math.PI * f * t) + 0.3 * Math.sin(4 * Math.PI * f * t)) * env * gain / 1.3;
    }
    offset += n;
  }
  return out;
}

// A code becomes a list of notes: {at, ms, f, voices: [[ratio, weight]], ...family params}.
function design(name) {
  const [source, polarity, layer, normality, demand, initiator, scope, activity] = CODES[name];
  const fam = MAP.source[source], lay = MAP.layer[layer], nor = MAP.normality[normality];
  const ini = MAP.initiator[initiator], over = OVERRIDES[name] ?? {};
  if (over.phrase) {
    const notes = over.phrase.map(([at, ms, f, o]) => ({
      c: fam.c, m: fam.m, i0: fam.i0, i1: fam.i1, tau: fam.tau, attack: fam.attack, k: fam.k, glide: fam.glide,
      rough: 0, roughRate: 75, cut: false, voices: [[1, 1]], at, ms, f, ...o,
    }));
    return { notes, gain: (over.gain ?? lay.gain) * fam.level };
  }
  const home = fam.home * (over.register ?? 1);
  const interval = nor.interval ?? MAP.scope[scope];
  const base = {
    c: fam.c, m: fam.m * nor.mDetune, i0: fam.i0 * lay.index, i1: fam.i1 * lay.index, tau: fam.tau,
    attack: fam.attack, k: fam.k, glide: fam.glide,
    rough: over.rough ?? nor.rough, roughRate: over.roughRate ?? nor.roughRate, cut: false,
  };
  // [pitch ratio, ms, gap after] steps for one statement of the gesture.
  let steps;
  if (activity) steps = MAP.activity[activity];
  else {
    const ratios = over.contour ?? (polarity === "onset" ? [1, interval] : polarity === "offset" ? [1, 1 / interval] : [1]);
    const repeated = MAP.demand[demand].repeats > 1;
    steps = ratios.map((r, i) => {
      const last = i === ratios.length - 1;
      const ms = (last && !repeated ? (over.lastMs ?? lay.last) : lay.first) * ini.durScale;
      return [r, ms, 0];
    });
  }
  const notes = [];
  let at = 0;
  for (let rep = 0; rep < MAP.demand[demand].repeats; rep++) {
    if (rep) at += lay.gap;
    steps.forEach(([r, ms, gap], i) => {
      const last = i === steps.length - 1;
      const voices = last && ini.dyad && !activity ? [[1, 1], [nor.harmony, 0.5]] : [[1, 1]];
      notes.push({ ...base, at, ms, f: home * r, voices, cut: last && !!over.cut });
      at += ms + gap;
    });
  }
  return { notes, gain: lay.gain * fam.level };
}

// Two-operator FM per voice: sin(carrier + index(t) * sin(modulator)), phase-accumulated so
// the glide stays continuous. The index decays from i0 to i1 (bright attack, mellow tail).
function render({ notes, gain }) {
  const end = Math.max(...notes.map(n => n.at + n.ms));
  const out = new Float32Array(Math.ceil(end / 1000 * SR));
  const release = Math.round(0.004 * SR);
  for (const n of notes) {
    const start = Math.round(n.at / 1000 * SR);
    const full = Math.round(n.ms / 1000 * SR), len = n.cut ? Math.round(full * 0.35) : full;
    const attack = Math.max(1, Math.round(n.attack / 1000 * SR));
    const weight = n.voices.reduce((s, [, w]) => s + w, 0);
    for (const [ratio, w] of n.voices) {
      let pc = 0, pm = 0;
      for (let i = 0; i < len && start + i < out.length; i++) {
        const t = i / SR;
        const f = n.f * ratio * (1 - (1 - n.glide) * Math.exp(-t / 0.008));
        pc += 2 * Math.PI * f * n.c / SR;
        pm += 2 * Math.PI * f * n.m / SR;
        const index = n.i1 + (n.i0 - n.i1) * Math.exp(-t * 1000 / n.tau);
        let env = Math.min(1, i / attack, (len - 1 - i) / release);
        if (n.k) env *= Math.exp(-n.k * i / full);
        if (n.rough) env *= 1 - n.rough * 0.5 * (1 - Math.cos(2 * Math.PI * n.roughRate * t));
        out[start + i] += (w / weight) * env * Math.sin(pc + index * Math.sin(pm)) * gain;
      }
    }
  }
  return out;
}

function earconSamples(name, which) {
  // "Current" was the retired plain note set; without it, both sides render the design.
  if (which === "current" && TONAL[name]) { const t = TONAL[name]; return renderTonal(t.notes, t.gain); }
  return render(design(name));
}

// --- PSTN simulation: 300-3400 Hz band, optionally G.711 (8 kHz, 8-bit mu-law). ---
function biquad(x, type, f0, q = Math.SQRT1_2) {
  const w = 2 * Math.PI * f0 / SR, cw = Math.cos(w), alpha = Math.sin(w) / (2 * q);
  const lp = type === "lowpass";
  const b0 = lp ? (1 - cw) / 2 : (1 + cw) / 2, b1 = lp ? 1 - cw : -(1 + cw), b2 = b0;
  const a0 = 1 + alpha, a1 = -2 * cw, a2 = 1 - alpha;
  const y = new Float32Array(x.length);
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  for (let i = 0; i < x.length; i++) {
    const v = (b0 * x[i] + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2) / a0;
    x2 = x1; x1 = x[i]; y2 = y1; y1 = v; y[i] = v;
  }
  return y;
}
function mulaw(x) {
  const s = Math.sign(x), a = Math.min(1, Math.abs(x));
  const q = Math.round(Math.log1p(255 * a) / Math.log(256) * 127) / 127;
  return s * (Math.pow(256, q) - 1) / 255;
}
function phoneLine(x, g711) {
  let y = biquad(biquad(biquad(x, "highpass", 300), "lowpass", 3400), "lowpass", 3400);
  if (g711) {
    for (let i = 0; i < y.length; i += 3) {
      const q = mulaw(y[i]);
      for (let j = i; j < Math.min(i + 3, y.length); j++) y[j] = q;
    }
    y = biquad(biquad(y, "lowpass", 3400), "lowpass", 3400);
  }
  return y;
}
"""

PAGE_JS = r"""
const DATA = __DATA__;
let ctx, style = "new", timers = [], sources = [];
const audio = () => (ctx ??= new AudioContext({ sampleRate: SR }));
const $ = id => document.getElementById(id);

function play(name, which = style) {
  let x = earconSamples(name, which);
  if ($("phone").checked || $("g711").checked) x = phoneLine(x, $("g711").checked);
  const ac = audio(), buf = ac.createBuffer(1, x.length, SR);
  buf.getChannelData(0).set(x);
  const src = ac.createBufferSource(), g = ac.createGain();
  src.buffer = buf; g.gain.value = +$("vol").value;
  src.connect(g); g.connect(ac.destination); src.start(); sources.push(src);
  const tile = document.querySelector(`[data-name="${name}"]`);
  tile?.classList.add(which === "current" ? "playing-a" : "playing-b");
  src.onended = () => tile?.classList.remove("playing-a", "playing-b");
}
function stopAll() { timers.forEach(clearTimeout); timers = []; sources.forEach(s => { try { s.stop(); } catch {} }); sources = []; }
function describe(name) {
  const { notes } = design(name), n = notes[0];
  const labels = notes.map(x => Math.round(x.f) + (x.voices.length > 1 ? "+" + Math.round(x.f * x.voices[1][0]) : ""));
  const runs = [];
  for (const l of labels) runs.length && runs.at(-1)[0] === l ? runs.at(-1)[1]++ : runs.push([l, 1]);
  const pitches = runs.map(([l, n]) => n > 1 ? `${l}\u00d7${n}` : l).join(" ");
  const ms = Math.round(Math.max(...notes.map(x => x.at + x.ms)));
  return `${pitches} Hz · c:m ${n.c}:${+n.m.toFixed(3)} · I ${n.i0.toFixed(1)}→${n.i1.toFixed(1)}${n.rough ? ` · AM ${n.roughRate} Hz` : ""} · ${ms} ms`;
}

const legend = $("legend");
legend.innerHTML = Object.entries(MAP.source).map(([k, v]) => `<div><b>${k}</b>: ${v.why}</div>`).join("")
  + Object.entries(MAP.activityWhy).map(([k, v]) => `<div><b>${k}</b>: ${v}</div>`).join("");

const board = $("board");
for (const [title, names] of DATA.groups) {
  const h = document.createElement("h2"); h.textContent = title; board.append(h);
  const grid = document.createElement("div"); grid.className = "grid"; board.append(grid);
  for (const name of names) {
    const code = CODES[name], over = OVERRIDES[name];
    const chips = DATA.fields.map((f, i) => code[i] && code[i] !== "-" ? `<span class="chip ${f}" title="${f}">${code[i]}</span>` : "").join("");
    const tile = document.createElement("div"); tile.className = "tile"; tile.dataset.name = name;
    tile.innerHTML = `${over ? `<span class="badge" title="${over.why}">override</span>` : ""}`
      + `<div class="name">${name}</div><div class="label">${DATA.labels[name] ?? ""}</div><div class="chips">${chips}</div>`
      + `<button data-as="current">Current</button><button data-as="new">New</button><button data-as="ab">A/B</button>`
      + `<div class="meta">${describe(name)}</div>${over ? `<div class="why">${over.why}</div>` : ""}`;
    tile.addEventListener("click", e => {
      e.stopPropagation();
      const as = e.target.dataset?.as ?? style;
      if (as === "ab") { play(name, "current"); timers.push(setTimeout(() => play(name, "new"), 800)); }
      else play(name, as);
    });
    grid.append(tile);
  }
}
document.querySelectorAll("#style button").forEach(b => b.onclick = () => {
  style = b.dataset.v; document.querySelectorAll("#style button").forEach(x => x.setAttribute("aria-pressed", x === b));
});
for (const [title, steps] of Object.entries(DATA.scenes)) {
  const b = document.createElement("button"); b.className = "scene"; b.textContent = "▶ " + title;
  b.onclick = () => { stopAll(); let at = 0; for (const [name, gap] of steps) { at += gap * 1000; timers.push(setTimeout(() => play(name), at)); } };
  $("scenes").append(b, " ");
}
$("stop").onclick = stopAll;
"""

BOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Dogwalk earcon lab</title>
<style>
  :root { color-scheme: dark; --bg: #16171a; --card: #202226; --card2: #2a2d33; --fg: #e8e6e1; --muted: #9a978f; --a: #e0a458; --b: #7fb6c9; }
  body { margin: 0; padding: 28px; background: var(--bg); color: var(--fg); font: 15px/1.4 -apple-system, system-ui, sans-serif; }
  h1 { font-size: 1.4rem; margin: 0 0 4px; } h2 { font-size: 0.95rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; margin: 26px 0 10px; }
  p.note { color: var(--muted); margin: 0 0 14px; max-width: 80ch; }
  details { color: var(--muted); margin: 0 0 14px; font-size: 13px; max-width: 100ch; } details div { margin: 2px 0; }
  .controls { display: flex; flex-wrap: wrap; gap: 18px; align-items: center; padding: 12px 16px; background: var(--card); border-radius: 10px; position: sticky; top: 0; z-index: 1; }
  .seg button, .tile button, .scene { background: var(--card2); color: var(--fg); border: 0; border-radius: 6px; padding: 6px 12px; font: inherit; cursor: pointer; }
  .seg button[aria-pressed=true] { background: var(--b); color: #111; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 10px; }
  .tile { background: var(--card); border-radius: 10px; padding: 12px; cursor: pointer; position: relative; }
  .tile:hover { background: var(--card2); } .tile.playing-a { outline: 2px solid var(--a); } .tile.playing-b { outline: 2px solid var(--b); }
  .tile .name { font: 12px ui-monospace, monospace; color: var(--muted); } .tile .label { font-size: 1.05rem; margin: 2px 0 6px; }
  .tile .badge { position: absolute; top: 10px; right: 10px; font-size: 11px; color: var(--a); border: 1px solid var(--a); border-radius: 4px; padding: 0 5px; }
  .tile button { padding: 3px 9px; font-size: 13px; margin-right: 4px; } .tile button[data-as=current] { box-shadow: inset 0 -2px var(--a); } .tile button[data-as=new] { box-shadow: inset 0 -2px var(--b); }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }
  .chip { font: 11px ui-monospace, monospace; background: var(--card2); border-radius: 4px; padding: 1px 5px; }
  .chip.source { color: #e8c07a; } .chip.polarity { color: #9fd38f; } .chip.layer { color: #9cc3e6; } .chip.normality { color: #e69c9c; }
  .chip.demand { color: #d6a6e8; } .chip.initiator { color: #c9c9a0; } .chip.scope { color: #a0c9c0; } .chip.activity { color: #f0f0f0; }
  .tile .meta { font: 11px ui-monospace, monospace; color: var(--muted); margin-top: 6px; } .tile .why { font-size: 12px; color: var(--a); margin-top: 4px; }
</style></head><body>
<h1>Dogwalk earcon lab</h1>
<p class="note"><b style="color:var(--a)">Current</b> is the TONAL table parsed from <code>audio.ts</code>; <b style="color:var(--b)">New</b> is derived from the semantic code on each tile (source, polarity, layer, normality, demand, initiator, scope, activity) by <code>earcon_lab.py</code>. Everything is deterministic. <b>Phone line</b> band-limits to 300&ndash;3400 Hz; <b>G.711</b> adds 8 kHz mu-law. A/B plays current, then new.</p>
<details><summary>Families and activity shapes</summary><div id="legend"></div></details>
<div class="controls">
  <span>Scenes play <span class="seg" id="style"><button aria-pressed="false" data-v="current">Current</button> <button aria-pressed="true" data-v="new">New</button></span></span>
  <label><input type="checkbox" id="phone"> Phone line</label>
  <label><input type="checkbox" id="g711"> G.711</label>
  <label>Volume <input type="range" id="vol" min="0" max="2" step="0.05" value="1"></label>
  <span id="scenes"></span> <button class="scene" id="stop">Stop</button>
</div>
<div id="board"></div>
<script>
__SCRIPT__
</script></body></html>
"""

# Node harness for --render: writes 24 kHz mono PCM16 WAVs of both sides.
RENDER_JS = r"""
const fs = require("fs"), path = require("path"), dir = process.argv[2];
function wav(x) {
  const pcm = Buffer.alloc(x.length * 2);
  x.forEach((v, i) => pcm.writeInt16LE(Math.max(-32768, Math.min(32767, Math.round(v * 32767))), i * 2));
  const h = Buffer.alloc(44);
  h.write("RIFF", 0); h.writeUInt32LE(36 + pcm.length, 4); h.write("WAVEfmt ", 8); h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22); h.writeUInt32LE(SR, 24); h.writeUInt32LE(SR * 2, 28);
  h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34); h.write("data", 36); h.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([h, pcm]);
}
for (const name of Object.keys(CODES)) {
  const stats = [];
  for (const which of ["current", "new"]) {
    const x = earconSamples(name, which);
    fs.writeFileSync(path.join(dir, `${which}_${name}.wav`), wav(x));
    const band = phoneLine(x, true);
    const rms = a => Math.sqrt(a.reduce((s, v) => s + v * v, 0) / a.length);
    const peak = Math.max(...x.map(Math.abs));
    stats.push(`${which} ${(x.length / SR * 1000).toFixed(0).padStart(4)}ms peak ${peak.toFixed(2)} rms ${rms(x).toFixed(3)} phone-rms ${rms(band).toFixed(3)}`);
  }
  console.log(name.padEnd(23), stats.join("  |  "));
}
"""


def synth_js() -> str:
    tonal = tonal_notes()
    return (SYNTH_JS.replace("__MAP__", json.dumps(MAP))
            .replace("__CODES__", json.dumps(CODES))
            .replace("__OVERRIDES__", json.dumps(OVERRIDES))
            .replace("__TONAL__", json.dumps(tonal)))


def page_script() -> str:
    data = {"groups": GROUPS, "labels": LABELS, "scenes": SCENES, "fields": CODE_FIELDS}
    return synth_js() + PAGE_JS.replace("__DATA__", json.dumps(data))


def validate() -> None:
    tonal = tonal_notes()  # empty since the plain note set was retired
    missing = set(LABELS) - set(CODES) | set(CODES) - set(LABELS) | (set(tonal) ^ set(CODES) if tonal else set())
    if missing:
        raise SystemExit(f"CODES, LABELS and audio.ts disagree on: {sorted(missing)}")
    for name, override in OVERRIDES.items():
        if not override.get("why"):
            raise SystemExit(f"override for {name} needs a why")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--render", metavar="DIR", help="write current_*.wav and new_*.wav to DIR with node, and print levels")
    parser.add_argument("--check", action="store_true", help="syntax-check the page script with node --check")
    parser.add_argument("--export", action="store_true", help=f"write the designs the Worker renders to {EXPORT.relative_to(EXPORT.parents[2])}")
    args = parser.parse_args()
    validate()
    tmp = Path(tempfile.gettempdir())
    if args.export:
        # The Worker's "fm" earcon style renders these note lists with a port of render().
        script = tmp / "dogwalk_earcon_export.js"
        script.write_text(synth_js() + "\nconsole.log(JSON.stringify(Object.fromEntries(Object.keys(CODES).map(n => [n, design(n)]))));")
        designs = json.loads(subprocess.run(["node", str(script)], check=True, capture_output=True, text=True).stdout)
        EXPORT.write_text(json.dumps(designs, indent=1) + "\n")
        print(f"wrote {len(designs)} designs to {EXPORT}")
        return
    if args.check:
        script = tmp / "dogwalk_earcon_lab.js"
        script.write_text(page_script())
        subprocess.run(["node", "--check", str(script)], check=True)
        print(f"ok {script}")
        return
    if args.render:
        out = Path(args.render)
        out.mkdir(parents=True, exist_ok=True)
        script = tmp / "dogwalk_earcon_render.js"
        script.write_text(synth_js() + RENDER_JS)
        subprocess.run(["node", str(script), str(out)], check=True)
        return
    page = tmp / "dogwalk_earcon_lab.html"
    page.write_text(BOARD_HTML.replace("__SCRIPT__", page_script()))
    subprocess.run(["open", str(page)], check=False)
    print(page)


if __name__ == "__main__":
    main()
