// Paired Browser page (DOMAIN.md), served at GET /pair: listens for the Pairing Code the call
// plays as keypad tones, claims it, waits for the caller to accept, then shows live Managed
// Session status and a read-only Workspace file viewer over /pair/ws.
//
// String.raw keeps every backslash in the page script literal, so regexes and "\n" escapes
// reach the browser as written. The page must therefore contain no backtick and no dollar-brace
// sequence anywhere (a backtick is written "\x60" in the script). The decoder core between the
// CORE markers is ported from prototypes/acoustic_pairing.html and must match src/pairing.ts:
// 10 data bytes, 6 parity bytes, 62.5 ms symbols, no gap, one copy, 32 symbols.

export const PAIR_HTML = String.raw`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dogwalk: Paired Browser</title>
<style>
:root{
  color-scheme:light;
  --paper:#f5f1e8;--card:#fbf9f3;--ink:#1c2b25;--muted:#5d685f;--faint:#8a9188;
  --line:#ddd5c6;--rule:#c9c0ad;--accent:#bf4d2b;--accent-ink:#fff;
  --ok:#2e7d4f;--bad:#b3261e;--warn:#a86a00;--fix:#d68910;--info:#2f6690;
  --highlight:#fbe9d0;--code-bg:#efe9dc;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,"Cascadia Code",monospace;
  --serif:Georgia,"Times New Roman",serif;
  --radius:8px;
}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
a{color:inherit}
[hidden]{display:none!important}
.page{max-width:1180px;margin:0 auto;padding:0 clamp(14px,4vw,40px) 48px}
header.top{display:flex;align-items:baseline;gap:14px;padding:18px 0;border-bottom:1px solid var(--rule);flex-wrap:wrap}
.mark{font-weight:700;letter-spacing:-.02em;text-decoration:none;font-size:17px}
.mark b{color:var(--accent)}
.crumb{font:500 12px/1.5 var(--mono);color:var(--muted);letter-spacing:.06em;text-transform:uppercase}
h2{margin:22px 0 10px;font:600 12px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.status{margin:18px 0 6px;font:600 clamp(19px,3vw,24px)/1.3 var(--serif)}
.status.ok{color:var(--ok)}
.status.bad{color:var(--bad)}
.small{font-size:13px}
.muted{color:var(--muted)}
.mono{font-family:var(--mono)}
button{font:inherit;padding:7px 14px;border-radius:var(--radius);border:1px solid var(--rule);background:var(--card);color:var(--ink);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink);font-weight:600}
button:disabled{opacity:.5;cursor:default}
button.link{border:0;background:none;padding:0;color:var(--info);text-decoration:underline;text-underline-offset:2px;cursor:pointer;text-align:left;word-break:break-all}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:12px 0}
.steps{margin:6px 0 14px;padding-left:1.4em;max-width:62ch}
.steps li{margin:3px 0}
.meter{height:8px;background:var(--line);border-radius:4px;overflow:hidden;max-width:520px}
.meter>div{height:100%;width:0;background:var(--accent);transition:width 60ms linear}
.stream{font-family:var(--mono);font-size:15px;letter-spacing:.1em;word-break:break-all;min-height:1.5em}
.frame{border-top:1px solid var(--line);padding:6px 0;font-family:var(--mono);font-size:13px}
.symrow{white-space:nowrap;overflow-x:auto}
.symrow .lbl{display:inline-block;width:3.2em;color:var(--muted)}
.sym{display:inline-block;width:1.1ch;text-align:center}
.sym.erased{color:var(--bad)}
.sym.wrong{background:color-mix(in srgb,var(--bad) 28%,transparent)}
.sym.fixed{background:color-mix(in srgb,var(--fix) 35%,transparent)}
.sep{display:inline-block;width:1.4ch;text-align:center;color:var(--faint)}
.verdict{font-family:var(--sans);font-size:13px}
.verdict.ok{color:var(--ok)}
.verdict.bad{color:var(--bad)}
details summary{cursor:pointer}
#log{max-height:180px;overflow:auto;white-space:pre-wrap;margin-top:6px}
.diag{margin-top:28px}
.box{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px}
.pairing{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center;justify-content:space-between}
.pairing .who{font-weight:600}
.permissions{background:var(--highlight);border:1px solid var(--accent);border-radius:var(--radius);padding:10px 14px;margin-top:14px}
.permissions h2{margin:0 0 6px;color:var(--accent)}
.permissions ul{margin:0;padding-left:1.2em}
.cols{display:grid;grid-template-columns:1fr;gap:0 28px}
@media(min-width:960px){.cols{grid-template-columns:minmax(0,1.1fr) minmax(0,1fr)}}
.card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:var(--radius);padding:12px 14px;margin:0 0 12px}
.card.turn-in_progress,.card.turn-queued{border-left-color:var(--info)}
.card.turn-failed{border-left-color:var(--bad)}
.card.turn-stopped{border-left-color:var(--ok)}
.card-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 10px}
.alias{margin:0;font:600 22px/1.2 var(--serif)}
.badge{font:600 11px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;padding:4px 7px;border-radius:999px;background:var(--line);color:var(--ink)}
.badge.in_progress,.badge.queued{background:var(--info);color:#fff}
.badge.failed{background:var(--bad);color:#fff}
.badge.stopped{background:var(--ok);color:#fff}
.badge.follow{background:var(--accent);color:var(--accent-ink)}
.card p{margin:6px 0}
.card .title{font-weight:600}
.plan,.tools{list-style:none;margin:6px 0;padding:0}
.plan li{display:flex;gap:8px;align-items:baseline}
.plan .box-mark{flex:none;width:.9em;height:.9em;border:1.5px solid var(--muted);border-radius:3px;transform:translateY(.1em)}
.plan .completed .box-mark{background:var(--ok);border-color:var(--ok)}
.plan .in_progress .box-mark{background:linear-gradient(90deg,var(--info) 50%,transparent 50%);border-color:var(--info)}
.plan .completed span:last-child{color:var(--muted);text-decoration:line-through}
.tools li{padding:3px 0;border-top:1px dashed var(--line);font-size:14px}
.tools .kind{font:600 11px/1 var(--mono);color:var(--muted);text-transform:uppercase;margin-right:6px}
.tools .paths{display:flex;flex-wrap:wrap;gap:2px 10px;font:12.5px/1.4 var(--mono)}
.failure{color:var(--bad);font-weight:600}
.remark{font-style:italic}
.report{margin-top:6px}
.closed-group>summary{color:var(--muted);margin:8px 0}
.crumbs{display:flex;flex-wrap:wrap;gap:2px;align-items:center;font:13px/1.6 var(--mono)}
.crumbs .slash{color:var(--faint)}
.entries{list-style:none;margin:8px 0;padding:0;max-height:360px;overflow:auto;border:1px solid var(--line);border-radius:var(--radius);background:var(--card)}
.entries li{display:flex;justify-content:space-between;gap:10px;padding:4px 10px;border-top:1px solid var(--line);font:13px/1.5 var(--mono)}
.entries li:first-child{border-top:0}
.entries .meta{color:var(--faint);white-space:nowrap}
.viewer{margin-top:10px}
.viewer-head{display:flex;justify-content:space-between;align-items:center;gap:10px;font:12.5px/1.4 var(--mono);color:var(--muted);margin-bottom:6px;word-break:break-all}
pre.plain{margin:0;white-space:pre-wrap;word-break:break-word;font:13px/1.5 var(--mono);background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:10px 12px;max-height:70vh;overflow:auto}
.md{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:4px 14px;max-height:70vh;overflow:auto;word-wrap:break-word}
.md h1,.md h2,.md h3,.md h4,.md h5,.md h6{font:600 1.05em/1.3 var(--sans);text-transform:none;letter-spacing:0;color:var(--ink);margin:14px 0 6px}
.md h1{font-size:1.35em}.md h2{font-size:1.2em}
.md pre{white-space:pre-wrap;background:var(--code-bg);padding:8px 10px;border-radius:6px;font:12.5px/1.45 var(--mono)}
.md code{font-family:var(--mono);font-size:.92em;background:var(--code-bg);padding:0 3px;border-radius:3px}
.md pre code{background:none;padding:0}
.md a{color:var(--info)}
</style>
</head>
<body>
<div class="page">
<header class="top">
  <a class="mark" href="/"><b>dog</b>walk.tools</a>
  <span class="crumb">Paired Browser</span>
</header>
<main>
<p id="status" class="status" role="status" aria-live="polite"></p>

<section id="pairScreen" hidden>
  <ol class="steps">
    <li>Use a different device from the phone in the call (a laptop or tablet). Phones give the browser silence while a call holds the microphone.</li>
    <li>Put the phone on speaker, near this device.</li>
    <li>Press Listen and allow the microphone.</li>
    <li>In the call, ask the walker to pair a browser, and say when this page is listening. The call plays about two seconds of keypad tones.</li>
    <li>The call describes this browser: press one to accept it (star refuses).</li>
  </ol>
  <div class="row">
    <button id="listen" class="primary">Listen</button>
    <button id="stop" disabled>Stop</button>
  </div>
  <div class="meter"><div id="level"></div></div>
  <div id="levelText" class="mono small muted">&nbsp;</div>
  <p class="small muted">Symbols as heard, slot by slot (&middot; is an unclear slot):</p>
  <div id="live" class="stream"></div>
  <div id="frames"></div>
</section>

<section id="pairedScreen" hidden>
  <div class="box pairing">
    <div>
      <div class="who" id="who"></div>
      <div class="small muted" id="expiry"></div>
      <div class="small muted" id="conn"></div>
    </div>
    <button id="unpair">Unpair</button>
  </div>
  <div id="permissions" class="permissions" hidden></div>
  <div class="cols">
    <div>
      <h2>Sessions</h2>
      <p id="sandbox" class="small muted"></p>
      <div id="sessions"></div>
      <div id="closed"></div>
    </div>
    <div>
      <h2>Files</h2>
      <nav id="crumbs" class="crumbs" aria-label="Path"></nav>
      <ul id="entries" class="entries"></ul>
      <p id="fileNote" class="small muted"></p>
      <div id="viewer" class="viewer" hidden>
        <div class="viewer-head"><span id="viewerPath"></span><button id="closeViewer">Close</button></div>
        <div id="viewerBody"></div>
      </div>
    </div>
  </div>
</section>

<details class="diag small"><summary class="muted">Diagnostics log</summary><div id="log" class="mono muted"></div></details>
</main>
</div>

<script>
"use strict";
// ==== CORE START: the decoder, shared by the page, the AudioWorklet (via toString), and offline tests ====
const ROWS = [697, 770, 852, 941];
const COLS = [1209, 1336, 1477, 1633];
const KEYPAD = ["123A", "456B", "789C", "*0#D"];
const ALPHABET = "0123456789ABCD*#"; // nibble value -> keypad symbol
const CODE_BYTES = 10;   // 80 random bits
const PARITY_BYTES = 6;  // Reed-Solomon parity
const SYMBOL_MS = 62.5;  // tones touch, no gap
const N_SLOTS = (CODE_BYTES + PARITY_BYTES) * 2;

// Goertzel classifier: each hop labels the latest 25.6 ms window with a keypad symbol or null.
// Rectangular window, as in classic DTMF receivers: its 39 Hz resolution keeps the nearest
// neighbors (73 Hz apart) out of the main lobe. Hops are a quarter window, so a 62.5 ms slot
// holds about six windows that lie entirely inside it.
class Classifier {
  constructor(sampleRate, opts) {
    opts = opts || {};
    this.win = Math.round(0.0256 * sampleRate);
    this.hop = Math.round(this.win / 4);
    this.buf = new Float32Array(this.win);
    this.pos = 0;
    this.count = 0;
    this.coef = ROWS.concat(COLS).map((f) => 2 * Math.cos(2 * Math.PI * f / sampleRate));
    this.minRms = opts.minRms ?? 0.003;
    this.purity = opts.purity ?? 0.5;
    this.dominance = opts.dominance ?? 4;
    this.twist = opts.twist ?? 10;
  }
  push(samples, onResult) {
    for (let i = 0; i < samples.length; i++) {
      this.buf[this.pos] = samples[i];
      this.pos = (this.pos + 1) % this.win;
      this.count++;
      if (this.count >= this.win && (this.count - this.win) % this.hop === 0) onResult(this.classify());
    }
  }
  classify() {
    const N = this.win;
    const s1 = new Float64Array(8), s2 = new Float64Array(8);
    let energy = 0;
    for (let j = 0; j < N; j++) {
      const x = this.buf[(this.pos + j) % N];
      energy += x * x;
      for (let k = 0; k < 8; k++) {
        const s0 = x + this.coef[k] * s1[k] - s2[k];
        s2[k] = s1[k];
        s1[k] = s0;
      }
    }
    // Normalized so a sinusoid of amplitude A reports A^2/2, its mean power.
    const P = new Array(8);
    for (let k = 0; k < 8; k++) P[k] = 2 * (s1[k] * s1[k] + s2[k] * s2[k] - this.coef[k] * s1[k] * s2[k]) / (N * N);
    const mean = energy / N;
    const rms = Math.sqrt(mean);
    const best = (from) => {
      const group = P.slice(from, from + 4);
      const order = group.map((p, i) => [p, i]).sort((a, b) => b[0] - a[0]);
      return { index: order[0][1], power: order[0][0], runnerUp: order[1][0] };
    };
    const r = best(0), c = best(4);
    const twistDb = Math.abs(10 * Math.log10((r.power + 1e-12) / (c.power + 1e-12)));
    const ok = rms >= this.minRms
      && r.power + c.power >= this.purity * mean
      && r.power >= this.dominance * r.runnerUp
      && c.power >= this.dominance * c.runnerUp
      && twistDb <= this.twist;
    return { sym: ok ? KEYPAD[r.index][c.index] : null, rms };
  }
}

// ---- Reed-Solomon over GF(256), errors and erasures (after Wikiversity's "Reed-Solomon codes for coders").
// Primitive polynomial 0x11d, generator roots 2^0..2^(n-1), as in src/pairing.ts.
// Polynomials are arrays, highest degree first.
const GF_EXP = new Uint8Array(512), GF_LOG = new Uint8Array(256);
(() => {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    GF_EXP[i] = x; GF_LOG[x] = i;
    x <<= 1; if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i++) GF_EXP[i] = GF_EXP[i - 255];
})();
const gfMul = (a, b) => (a && b ? GF_EXP[GF_LOG[a] + GF_LOG[b]] : 0);
const gfDiv = (a, b) => (a ? GF_EXP[(GF_LOG[a] + 255 - GF_LOG[b]) % 255] : 0);
const gfPow = (x, p) => GF_EXP[(((GF_LOG[x] * p) % 255) + 255) % 255];
const gfInv = (x) => GF_EXP[255 - GF_LOG[x]];
const polyScale = (p, x) => p.map((c) => gfMul(c, x));
function polyAdd(p, q) {
  const r = new Array(Math.max(p.length, q.length)).fill(0);
  p.forEach((c, i) => { r[i + r.length - p.length] = c; });
  q.forEach((c, i) => { r[i + r.length - q.length] ^= c; });
  return r;
}
function polyMul(p, q) {
  const r = new Array(p.length + q.length - 1).fill(0);
  for (let j = 0; j < q.length; j++) for (let i = 0; i < p.length; i++) r[i + j] ^= gfMul(p[i], q[j]);
  return r;
}
function polyEval(p, x) {
  let y = p[0];
  for (let i = 1; i < p.length; i++) y = gfMul(y, x) ^ p[i];
  return y;
}
// Only used to show the corrected parity symbols next to the raw ones.
function rsEncode(msg, nsym) {
  let gen = [1];
  for (let i = 0; i < nsym; i++) gen = polyMul(gen, [1, gfPow(2, i)]);
  const out = [...msg, ...new Array(nsym).fill(0)];
  for (let i = 0; i < msg.length; i++) {
    const coef = out[i];
    if (coef) for (let j = 1; j < gen.length; j++) out[i + j] ^= gfMul(gen[j], coef);
  }
  return [...msg, ...out.slice(msg.length)];
}
const rsSyndromes = (msg, nsym) => [0, ...Array.from({ length: nsym }, (_, i) => polyEval(msg, gfPow(2, i)))];

function rsCorrectErrata(msg, synd, errPos) {
  const coefPos = errPos.map((p) => msg.length - 1 - p);
  let errLoc = [1];
  for (const i of coefPos) errLoc = polyMul(errLoc, polyAdd([1], [gfPow(2, i), 0]));
  const product = polyMul([...synd].reverse(), errLoc);
  const errEval = product.slice(product.length - errLoc.length).reverse();
  const X = coefPos.map((p) => gfPow(2, -(255 - p)));
  const E = new Array(msg.length).fill(0);
  X.forEach((Xi, i) => {
    const XiInv = gfInv(Xi);
    let locPrime = 1;
    X.forEach((Xj, j) => { if (j !== i) locPrime = gfMul(locPrime, 1 ^ gfMul(XiInv, Xj)); });
    if (locPrime === 0) throw new Error("could not find error magnitude");
    const y = gfMul(Xi, polyEval([...errEval].reverse(), XiInv));
    E[errPos[i]] = gfDiv(y, locPrime);
  });
  return polyAdd(msg, E);
}

function rsErrorLocator(synd, nsym, eraseCount) {
  let errLoc = [1], oldLoc = [1];
  const shift = synd.length > nsym ? synd.length - nsym : 0;
  for (let i = 0; i < nsym - eraseCount; i++) {
    const K = i + shift;
    let delta = synd[K];
    for (let j = 1; j < errLoc.length; j++) delta ^= gfMul(errLoc[errLoc.length - 1 - j], synd[K - j]);
    oldLoc = [...oldLoc, 0];
    if (delta) {
      if (oldLoc.length > errLoc.length) {
        const newLoc = polyScale(oldLoc, delta);
        oldLoc = polyScale(errLoc, gfInv(delta));
        errLoc = newLoc;
      }
      errLoc = polyAdd(errLoc, polyScale(oldLoc, delta));
    }
  }
  while (errLoc.length && errLoc[0] === 0) errLoc.shift();
  const errs = errLoc.length - 1;
  if ((errs - eraseCount) * 2 + eraseCount > nsym) throw new Error("too many errors");
  return errLoc;
}

function rsForneySyndromes(synd, pos, n) {
  const fsynd = synd.slice(1);
  for (const p of pos) {
    const x = gfPow(2, n - 1 - p);
    for (let j = 0; j < fsynd.length - 1; j++) fsynd[j] = gfMul(fsynd[j], x) ^ fsynd[j + 1];
  }
  return fsynd;
}

// Returns { data, errata: corrected byte positions } or throws when the frame is beyond repair.
function rsDecode(received, nsym, erasePos) {
  const msg = [...received];
  for (const p of erasePos) msg[p] = 0;
  if (erasePos.length > nsym) throw new Error("too many erasures");
  const synd = rsSyndromes(msg, nsym);
  if (Math.max(...synd) === 0) return { data: msg.slice(0, msg.length - nsym), errata: [...erasePos] };
  const fsynd = rsForneySyndromes(synd, erasePos, msg.length);
  const errLoc = rsErrorLocator(fsynd, nsym, erasePos.length);
  const reversed = [...errLoc].reverse();
  const errPos = [];
  for (let i = 0; i < msg.length; i++) if (polyEval(reversed, gfPow(2, i)) === 0) errPos.push(msg.length - 1 - i);
  if (errPos.length !== errLoc.length - 1) throw new Error("Chien search found the wrong number of errors");
  const fixed = rsCorrectErrata(msg, synd, [...erasePos, ...errPos]);
  if (Math.max(...rsSyndromes(fixed, nsym)) > 0) throw new Error("could not correct");
  return { data: fixed.slice(0, fixed.length - nsym), errata: [...erasePos, ...errPos] };
}

// ---- Framing
const bytesToSymbols = (bytes) => bytes.map((b) => ALPHABET[b >> 4] + ALPHABET[b & 15]).join("");
const toHex = (bytes) => [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
const hexToBytes = (hex) => hex.match(/../g).map((h) => parseInt(h, 16));

// Symbol clock: votes of the windows lying entirely inside each slot. start and slot are in
// samples; labels[i] is the label of the window starting at (firstHop + i) * hop.
function slotVotes(labels, firstHop, hop, win, start, slot, nSlots) {
  const votes = Array.from({ length: nSlots }, () => ({}));
  labels.forEach((sym, i) => {
    if (!sym) return;
    const a = (firstHop + i) * hop;
    const k = Math.floor((a - start) / slot);
    if (k < 0 || k >= nSlots || a + win > start + (k + 1) * slot) return;
    votes[k][sym] = (votes[k][sym] || 0) + 1;
  });
  return votes;
}
// A slot with no votes, or no clear majority, is an erasure.
function slotSymbols(votes) {
  return votes.map((v) => {
    const order = Object.entries(v).sort((a, b) => b[1] - a[1]);
    if (!order.length || (order[1] && order[0][1] < 2 * order[1][1])) return null;
    return order[0][0];
  });
}
// Fit the clock: search the frame's start offset and a small rate error, maximizing agreement.
function alignSlots(labels, firstHop, hop, win, slot, nSlots) {
  const guess = firstHop * hop + 0.4 * win;
  let best = null;
  for (let scale = 0.99; scale <= 1.0101; scale += 0.005) {
    for (let d = -slot / 2; d <= slot / 2; d += hop / 2) {
      const votes = slotVotes(labels, firstHop, hop, win, guess + d, slot * scale, nSlots);
      let score = 0;
      for (const v of votes) {
        const counts = Object.values(v).sort((a, b) => b - a);
        if (counts.length) score += counts[0] - counts.slice(1).reduce((s, c) => s + c, 0);
      }
      if (!best || score > best.score) best = { score, votes };
    }
  }
  return slotSymbols(best.votes);
}

// Streaming frame assembler: hop labels in, live slot symbols and finished frames out.
class FrameReceiver {
  constructor(sampleRate, hop, win, onLive, onFrame) {
    Object.assign(this, { sampleRate, hop, win, onLive, onFrame });
    this.slot = SYMBOL_MS / 1000 * sampleRate;
    this.nSlots = N_SLOTS;
    this.hopIndex = 0;
    this.frame = null;
    this.silent = 0;
  }
  feed(sym) {
    const i = this.hopIndex++;
    if (!this.frame) {
      if (!sym) return;
      this.frame = { firstHop: i, labels: [], shown: 0 };
      this.silent = 0;
    }
    const f = this.frame;
    f.labels.push(sym);
    this.silent = sym ? 0 : this.silent + 1;
    // Live view: each slot with the naive clock as it completes.
    const start = f.firstHop * this.hop + 0.4 * this.win;
    const done = Math.min(this.nSlots, Math.floor((i * this.hop - start) / this.slot));
    if (done > f.shown) {
      const live = slotSymbols(slotVotes(f.labels, f.firstHop, this.hop, this.win, start, this.slot, this.nSlots));
      for (let k = f.shown; k < done; k++) this.onLive(live[k], k);
      f.shown = done;
    }
    const silenceOver = this.silent * this.hop > 0.25 * this.sampleRate;
    const pastEnd = (i - f.firstHop) * this.hop > (this.nSlots + 2) * this.slot;
    if (silenceOver || pastEnd) this.finish();
  }
  finish() {
    const f = this.frame;
    this.frame = null;
    if (!f || f.labels.filter(Boolean).length < this.nSlots) return; // a blip, not a frame
    this.onFrame(decodeSlots(alignSlots(f.labels, f.firstHop, this.hop, this.win, this.slot, this.nSlots)));
  }
}

// Raw: the data slots as heard. ECC: Reed-Solomon with unclear slots as erasures. Only ecc is claimed.
function decodeSlots(symbols) {
  const bytes = [], erased = [];
  for (let b = 0; b < symbols.length / 2; b++) {
    const hi = symbols[2 * b], lo = symbols[2 * b + 1];
    if (hi === null || lo === null) { erased.push(b); bytes.push(0); }
    else bytes.push((ALPHABET.indexOf(hi) << 4) | ALPHABET.indexOf(lo));
  }
  const raw = erased.some((b) => b < CODE_BYTES) ? null : toHex(bytes.slice(0, CODE_BYTES));
  let ecc = null, errata = [], error = null;
  try {
    const result = rsDecode(bytes, PARITY_BYTES, erased);
    ecc = toHex(result.data);
    errata = result.errata;
  } catch (e) { error = e.message; }
  return { symbols, erased, raw, ecc, errata, error };
}
// A frame repaired with all its redundancy spent (say, six erasures) is unchecked: any data
// would fit. Claim only frames with parity left over to confirm the repair.
function claimable(frame) {
  const wrong = Math.max(0, frame.errata.length - frame.erased.length);
  return frame.ecc !== null && frame.erased.length + 2 * wrong < PARITY_BYTES;
}
// ==== CORE END ====

// ==== WORKLET START ====
// The AudioWorklet runs the same Classifier, serialized with toString and loaded from a Blob URL.
const WORKLET_SRC = "const ROWS = " + JSON.stringify(ROWS) + ", COLS = " + JSON.stringify(COLS)
  + ", KEYPAD = " + JSON.stringify(KEYPAD) + ";\n"
  + Classifier.toString() + "\n"
  + "class PairingProcessor extends AudioWorkletProcessor {\n"
  + "  constructor() { super(); this.c = new Classifier(sampleRate); }\n"
  + "  process(inputs) {\n"
  + "    const ch = inputs[0] && inputs[0][0];\n"
  + "    if (ch) this.c.push(ch, (r) => this.port.postMessage(r));\n"
  + "    return true;\n"
  + "  }\n"
  + "}\n"
  + "registerProcessor(\"pairing-dtmf\", PairingProcessor);\n";
// ==== WORKLET END ====

const $ = (id) => document.getElementById(id);
const HOME = "/home/daytona";
const TOKEN_KEY = "dogwalk.pair.token";
const DOT = "·";

// Builds an element; string children become text nodes, so server strings are never parsed as HTML.
function el(tag, props, children) {
  const node = document.createElement(tag);
  for (const key in props || {}) {
    const value = props[key];
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.slice(0, 2) === "on") node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of [].concat(children == null ? [] : children)) {
    if (child != null && child !== false) node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

function log(line) {
  const box = $("log");
  box.textContent = new Date().toLocaleTimeString() + " " + line + "\n" + box.textContent.slice(0, 8000);
}
function setStatus(text, tone) {
  $("status").textContent = text;
  $("status").className = "status " + (tone || "");
}
function showScreen(name) {
  $("pairScreen").hidden = name !== "pair";
  $("pairedScreen").hidden = name !== "paired";
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---- Listening and claiming
let listener = null;     // { stop } while the microphone is open
let latestHop = null;    // newest classifier result, for the meter
let claimState = "idle"; // idle | listening | claiming | waiting
const rejectedCodes = new Set();

async function startListening() {
  if (listener) return;
  $("listen").disabled = true;
  $("live").textContent = "";
  $("frames").replaceChildren();
  let ctx = null, stream = null;
  try {
    if (!navigator.mediaDevices) throw new Error("this page needs HTTPS for microphone access");
    ctx = new AudioContext();
    await ctx.resume();
    // Speech processing would treat steady tones as noise and remove them.
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
    });
    const granted = stream.getAudioTracks()[0].getSettings();
    log("mic " + (granted.sampleRate || ctx.sampleRate) + " Hz, context " + ctx.sampleRate + " Hz, echoCancellation="
      + granted.echoCancellation + ", noiseSuppression=" + granted.noiseSuppression + ", autoGainControl=" + granted.autoGainControl);
    const source = ctx.createMediaStreamSource(stream);
    const sink = ctx.createGain();
    sink.gain.value = 0;
    sink.connect(ctx.destination);
    const probe = new Classifier(ctx.sampleRate); // same window and hop as the worklet's
    const rx = new FrameReceiver(ctx.sampleRate, probe.hop, probe.win, showLiveSymbol, onFrame);
    const onHop = (r) => { latestHop = r; rx.feed(r.sym); };
    let node;
    try {
      await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET_SRC], { type: "application/javascript" })));
      node = new AudioWorkletNode(ctx, "pairing-dtmf");
      node.port.onmessage = (e) => onHop(e.data);
      log("decoding in an AudioWorklet");
    } catch (error) {
      // Fallback for browsers without blob-module worklets.
      node = ctx.createScriptProcessor(2048, 1, 1);
      node.onaudioprocess = (e) => probe.push(e.inputBuffer.getChannelData(0), onHop);
      log("AudioWorklet unavailable (" + error.message + "), using ScriptProcessor");
    }
    source.connect(node);
    node.connect(sink);
    const opened = { stream, ctx };
    listener = { stop: () => { opened.stream.getTracks().forEach((t) => t.stop()); opened.ctx.close(); } };
    claimState = "listening";
    $("stop").disabled = false;
    setStatus("Listening for pairing tones");
    requestAnimationFrame(drawMeter);
  } catch (error) {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (ctx) ctx.close();
    setStatus("Could not use the microphone: " + error.message, "bad");
    $("listen").disabled = false;
  }
}

function stopListening() {
  if (listener) listener.stop();
  listener = null;
  latestHop = null;
  $("listen").disabled = false;
  $("stop").disabled = true;
  $("level").style.width = "0";
  $("levelText").innerHTML = "&nbsp;";
}

function drawMeter() {
  if (!listener) return;
  if (latestHop) {
    const dbfs = 20 * Math.log10(latestHop.rms + 1e-9);
    $("level").style.width = Math.max(0, Math.min(100, (dbfs + 70) / 70 * 100)) + "%";
    $("levelText").textContent = dbfs.toFixed(0) + " dBFS" + (latestHop.sym ? ", tone " + latestHop.sym : "");
  }
  requestAnimationFrame(drawMeter);
}

function showLiveSymbol(sym, k) {
  const live = $("live");
  if (k === 0) live.textContent = "";
  if (k === CODE_BYTES * 2) live.textContent += "|";
  live.textContent += sym == null ? DOT : sym;
}

function symbolRow(label, symbols, classFor) {
  const row = el("div", { class: "symrow" }, el("span", { class: "lbl" }, label));
  symbols.forEach((s, k) => {
    if (k === CODE_BYTES * 2) row.append(el("span", { class: "sep" }, "|"));
    row.append(el("span", { class: "sym " + (classFor(k) || "") }, s == null ? DOT : s));
  });
  return row;
}

// Diagnostics per frame: the raw slots against the ECC-corrected frame.
function showFrame(frame) {
  const corrected = frame.ecc ? bytesToSymbols(rsEncode(hexToBytes(frame.ecc), PARITY_BYTES)).split("") : null;
  const fixed = new Set(frame.errata);
  const wrong = frame.errata.length - frame.erased.length;
  const verdict = frame.ecc
    ? (claimable(frame) ? "ECC ok: " : "ECC unverified: ") + frame.ecc.match(/.{1,4}/g).join(" ") + " (repaired " + frame.erased.length + " unclear and "
      + Math.max(0, wrong) + " wrong of " + PARITY_BYTES + " parity bytes)"
    : "ECC failed: " + frame.error;
  const box = el("div", { class: "frame" }, [
    symbolRow("raw", frame.symbols, (k) => {
      if (frame.symbols[k] === null) return "erased";
      if (corrected && corrected[k] !== frame.symbols[k]) return "wrong";
    }),
    corrected ? symbolRow("ecc", corrected, (k) => (fixed.has(k >> 1) ? "fixed" : "")) : null,
    el("div", { class: "verdict " + (claimable(frame) ? "ok" : "bad") }, verdict),
  ]);
  const frames = $("frames");
  frames.prepend(box);
  while (frames.children.length > 4) frames.lastChild.remove();
}

function onFrame(frame) {
  showFrame(frame);
  log("frame: " + frame.erased.length + " unclear bytes, raw " + (frame.raw || "-") + ", ecc " + (frame.ecc || frame.error));
  if (frame.ecc && !claimable(frame)) log("not claimed: the repair used all the parity, so it is unverified");
  if (claimable(frame) && claimState === "listening" && !rejectedCodes.has(frame.ecc)) claim(frame.ecc);
}

async function claim(code) {
  claimState = "claiming";
  setStatus("Heard a code, claiming");
  let response, body;
  try {
    response = await fetch("/pair/claim", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ code }),
    });
    body = await response.json().catch(() => ({}));
  } catch (error) {
    return claimFailed("Could not reach dogwalk.tools (" + error.message + "). Still listening.");
  }
  if (response.ok && typeof body.ticket === "string") {
    stopListening();
    claimState = "waiting";
    return waitForAnswer(body.ticket);
  }
  if (response.status === 404) {
    rejectedCodes.add(code);
    return claimFailed("That code is unknown or expired. Still listening: ask the walker in the call for new tones.");
  }
  claimFailed("Claim failed (" + response.status + "): " + (body.error || response.statusText) + ". Still listening.");
}

function claimFailed(message) {
  claimState = listener ? "listening" : "idle";
  setStatus(message, "bad");
}

async function waitForAnswer(ticket) {
  setStatus("Waiting for you to press one in the call");
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    await sleep(1000);
    let response, body;
    try {
      response = await fetch("/pair/claim/status", { headers: { authorization: "Bearer " + ticket } });
      body = await response.json().catch(() => ({}));
    } catch (error) {
      log("claim status check failed: " + error.message);
      continue;
    }
    if (!response.ok) return pairingFailed("Pairing check failed (" + response.status + "): " + (body.error || response.statusText) + ".");
    if (body.state === "waiting") continue;
    if (body.state === "accepted" && typeof body.token === "string") {
      sessionStorage.setItem(TOKEN_KEY, body.token);
      claimState = "idle";
      return connect();
    }
    if (body.state === "refused") return pairingFailed("The pairing was refused in the call.");
    return pairingFailed("The pairing request expired before it was accepted.");
  }
  pairingFailed("No answer in the call within 45 seconds.");
}

function pairingFailed(message) {
  claimState = "idle";
  showScreen("pair");
  setStatus(message + " Press Listen to try again.", "bad");
}

// ---- The paired connection
let socket = null;
let backoff = 1000;
let reconnectTimer = null;
let pingTimer = null;
let pairing = null;     // the hello message, with idle_expires_at refreshed by pongs
let serverError = "";

function connect() {
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (!token) return;
  clearTimeout(reconnectTimer);
  showScreen("paired");
  if (!pairing) setStatus("Connecting");
  setConnection("Connecting");
  // The identity half routes the socket to its Durable Object; the secret only travels in the auth message.
  const identity = token.split(".")[0];
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host
    + "/pair/ws?for=" + encodeURIComponent(identity));
  socket = ws;
  ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token }));
  ws.onmessage = (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch (error) { return; }
    onMessage(message);
  };
  ws.onclose = (event) => onClose(ws, event);
}

function send(message) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(message));
  return true;
}

function onClose(ws, event) {
  if (ws !== socket) return;
  socket = null;
  clearInterval(pingTimer);
  log("socket closed " + event.code + (event.reason ? " " + event.reason : ""));
  if (event.code === 4001 || event.code === 4003) {
    sessionStorage.removeItem(TOKEN_KEY);
    pairing = null;
    showScreen("pair");
    if (event.code === 4003) setStatus("Unpaired. Press Listen to pair again.");
    else setStatus("This pairing is no longer valid" + (serverError ? " (" + serverError + ")" : "") + ". Press Listen to pair again.", "bad");
    serverError = "";
    return;
  }
  setConnection("Disconnected, reconnecting in " + Math.round(backoff / 1000) + " s");
  reconnectTimer = setTimeout(connect, backoff);
  backoff = Math.min(backoff * 2, 30000);
}

function onMessage(m) {
  switch (m.type) {
    case "hello":
      pairing = m;
      backoff = 1000;
      setStatus("Paired", "ok");
      setConnection("Connected");
      renderPairing();
      clearInterval(pingTimer);
      pingTimer = setInterval(() => send({ type: "ping" }), 30000);
      if (!listedOnce) listDir(currentDir);
      break;
    case "pong":
      if (pairing && m.idle_expires_at) { pairing.idle_expires_at = m.idle_expires_at; renderPairing(); }
      break;
    case "status": renderStatus(m); break;
    case "list": if (m.id === pendingList) renderListing(m); break;
    case "file": if (m.id === pendingRead) renderFile(m); break;
    case "error":
      if (m.id != null) { if (m.id === pendingList || m.id === pendingRead) $("fileNote").textContent = "Error: " + m.error; }
      else { serverError = String(m.error || ""); log("server error: " + serverError); }
      break;
    case "unpaired": log("unpaired by request"); break;
  }
}

function setConnection(text) { $("conn").textContent = text; }

function clock(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
function fromNow(ms) {
  const minutes = Math.max(0, Math.round((ms - Date.now()) / 60000));
  return minutes < 60 ? minutes + " min" : Math.floor(minutes / 60) + " h " + (minutes % 60) + " min";
}
function renderPairing() {
  if (!pairing) return;
  $("who").textContent = (pairing.device || "This browser") + (pairing.place ? ", " + pairing.place : "");
  const parts = [];
  if (pairing.idle_expires_at) parts.push("Ends if idle until " + clock(pairing.idle_expires_at) + " (in " + fromNow(pairing.idle_expires_at) + ")");
  if (pairing.expires_at) parts.push("ends at " + clock(pairing.expires_at) + " at the latest");
  $("expiry").textContent = parts.join(", ");
}

function unpair() {
  if (!confirm("Unpair this browser? You will need to pair it again from the call.")) return;
  if (send({ type: "unpair" })) return; // the server replies, then closes with 4003
  clearTimeout(reconnectTimer);
  socket = null;
  sessionStorage.removeItem(TOKEN_KEY);
  pairing = null;
  showScreen("pair");
  setStatus("Unpaired. Press Listen to pair again.");
}

// ---- Session status
const openReports = new Set();
let closedOpen = false;
const TURN_LABELS = { idle: "idle", queued: "queued", in_progress: "in progress", stopped: "stopped", failed: "failed" };

function renderStatus(m) {
  const sandbox = m.sandbox || {};
  $("sandbox").textContent = (sandbox.agent_ready ? "Agent ready" : "Agent not ready") + ", "
    + (sandbox.call_attached ? "call attached" : "no call attached") + ". Updated " + new Date(m.at || Date.now()).toLocaleTimeString() + ".";

  const permissions = m.pending_permissions || [];
  $("permissions").hidden = !permissions.length;
  $("permissions").replaceChildren(
    el("h2", null, "Permission requests: answer in the call"),
    el("ul", null, permissions.map((p) => el("li", null, [el("b", null, p.alias), ": ", p.title]))));

  const sessions = m.sessions || [];
  const open = sessions.filter((s) => s.session_state !== "closed");
  const closed = sessions.filter((s) => s.session_state === "closed");
  $("sessions").replaceChildren(...(open.length ? open.map(sessionCard) : [el("p", { class: "muted" }, "No open sessions.")]));
  if (!closed.length) { $("closed").replaceChildren(); return; }
  const group = el("details", { class: "closed-group", open: closedOpen, ontoggle: () => { closedOpen = group.open; } },
    [el("summary", null, "Closed sessions (" + closed.length + ")"), ...closed.map(sessionCard)]);
  $("closed").replaceChildren(group);
}

function badge(text, kind) { return el("span", { class: "badge " + (kind || "") }, text); }

function sessionCard(s) {
  const head = el("div", { class: "card-head" }, [
    el("h3", { class: "alias" }, s.alias),
    badge(TURN_LABELS[s.turn_state] || s.turn_state, s.turn_state),
    s.session_state !== "ready" ? badge(s.session_state) : null,
    s.followed ? badge("followed", "follow") : null,
    s.stop_reason ? el("span", { class: "small muted" }, s.stop_reason) : null,
  ]);
  const card = el("article", { class: "card turn-" + s.turn_state }, [
    head,
    s.directory ? el("button", { class: "link mono small", onclick: () => listDir(resolvePath("", s.directory)) }, s.directory) : null,
    s.title ? el("p", { class: "title" }, s.title) : null,
    s.assignment ? el("p", null, [el("span", { class: "muted" }, "Assignment: "), s.assignment]) : null,
  ]);
  const plan = s.plan || [];
  if (plan.length) {
    card.append(el("ul", { class: "plan" }, plan.map((step) =>
      el("li", { class: step.status }, [el("span", { class: "box-mark", title: step.status }), el("span", null, step.content)]))));
  }
  const tools = s.recent_tool_calls || [];
  if (tools.length) {
    card.append(el("ul", { class: "tools" }, tools.map((call) => el("li", null, [
      el("span", { class: "kind" }, call.kind),
      call.title, " ",
      el("span", { class: "small muted" }, "(" + call.status + (call.seconds != null ? ", " + Math.round(call.seconds) + " s" : "") + ")"),
      (call.paths || []).length ? el("div", { class: "paths" }, call.paths.map((p) =>
        el("button", { class: "link", onclick: () => openFile(resolvePath(s.directory, p)) }, p))) : null,
    ]))));
  }
  if (s.remark) card.append(el("p", { class: "remark" }, s.remark));
  if (s.failure) card.append(el("p", { class: "failure" }, s.failure));
  if (s.report) {
    const report = el("details", { class: "report", open: openReports.has(s.alias), ontoggle: () => {
      if (report.open) openReports.add(s.alias); else openReports.delete(s.alias);
    } }, [el("summary", null, "Latest report"), markdownBlock(s.report)]);
    card.append(report);
  }
  if (s.usage && typeof s.usage === "object") {
    const parts = Object.entries(s.usage).filter((e) => typeof e[1] !== "object").map((e) => e[0] + " " + e[1]);
    if (parts.length) card.append(el("p", { class: "small muted" }, "Usage: " + parts.join(", ")));
  }
  return card;
}

// A tool-call path is relative to its session's directory, which is relative to /home/daytona.
function resolvePath(directory, path) {
  let full = String(path || "");
  if (full.charAt(0) !== "/") {
    const dir = String(directory || "");
    full = (dir.charAt(0) === "/" ? dir : HOME + "/" + dir) + "/" + full;
  }
  const parts = [];
  for (const part of full.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") parts.pop(); else parts.push(part);
  }
  return "/" + parts.join("/");
}

// ---- Files
let nextId = 1;
let pendingList = 0;
let pendingRead = 0;
let currentDir = HOME;
let listedOnce = false;

function listDir(path) {
  pendingList = nextId++;
  if (!send({ type: "list", id: pendingList, path })) { $("fileNote").textContent = "Not connected."; return; }
  $("fileNote").textContent = "Loading " + path;
}

function openFile(path) {
  pendingRead = nextId++;
  if (!send({ type: "read", id: pendingRead, path })) { $("fileNote").textContent = "Not connected."; return; }
  $("fileNote").textContent = "Opening " + path;
}

function renderCrumbs(path) {
  const parts = path.split("/").filter(Boolean);
  const nodes = [el("button", { class: "link", onclick: () => listDir("/") }, "/")];
  parts.forEach((part, i) => {
    if (i) nodes.push(el("span", { class: "slash" }, "/"));
    const target = "/" + parts.slice(0, i + 1).join("/");
    nodes.push(el("button", { class: "link", onclick: () => listDir(target) }, part));
  });
  $("crumbs").replaceChildren(...nodes);
}

function size(bytes) {
  if (bytes == null) return "";
  return bytes < 1024 ? bytes + " B" : bytes < 1048576 ? (bytes / 1024).toFixed(1) + " KB" : (bytes / 1048576).toFixed(1) + " MB";
}

function renderListing(m) {
  listedOnce = true;
  currentDir = m.path;
  renderCrumbs(m.path);
  $("fileNote").textContent = "";
  const entries = (m.entries || []).slice().sort((a, b) => (b.dir - a.dir) || String(a.name).localeCompare(String(b.name)));
  const items = entries.map((e) => {
    const target = resolvePath(m.path, e.name);
    return el("li", null, [
      el("button", { class: "link", onclick: () => (e.dir ? listDir(target) : openFile(target)) }, e.name + (e.dir ? "/" : "")),
      el("span", { class: "meta" }, (e.dir ? "" : size(e.size) + " ") + (e.modified || "")),
    ]);
  });
  $("entries").replaceChildren(...(items.length ? items : [el("li", { class: "muted" }, "Empty directory")]));
}

function renderFile(m) {
  $("fileNote").textContent = "";
  $("viewerPath").textContent = m.path + " (" + size(m.size) + (m.truncated ? ", truncated" : "") + ")";
  const body = /\.(md|markdown|mdx)$/i.test(m.path) ? markdownBlock(m.text || "") : el("pre", { class: "plain" }, m.text || "");
  $("viewerBody").replaceChildren(body);
  $("viewer").hidden = false;
  $("viewer").scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// ---- A small markdown renderer: the source is HTML-escaped first, so no raw HTML survives.
const FENCE = /^\s*(\x60{3,}|~{3,})/;
function escapeHtml(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function inlineMarkdown(text) {
  const spans = [];
  text = text.replace(/\x60([^\x60]+)\x60/g, (_, code) => { spans.push(code); return "\u0000" + (spans.length - 1) + "\u0000"; });
  text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, href) =>
    /^(https?:\/\/|\/|#)/i.test(href) ? "<a href=\"" + href + "\" target=\"_blank\" rel=\"noopener noreferrer\">" + label + "</a>" : label);
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_]+)__/g, "<strong>$1</strong>");
  text = text.replace(/\*([^*\s][^*]*)\*/g, "<em>$1</em>").replace(/(^|[^\w])_([^_\s][^_]*)_(?=[^\w]|$)/g, "$1<em>$2</em>");
  return text.replace(/\u0000(\d+)\u0000/g, (_, i) => "<code>" + spans[+i] + "</code>");
}
function renderMarkdown(source) {
  const lines = escapeHtml(source).replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let paragraph = [], list = null;
  const flushParagraph = () => { if (paragraph.length) out.push("<p>" + inlineMarkdown(paragraph.join(" ")) + "</p>"); paragraph = []; };
  const flushList = () => {
    if (list) out.push("<" + list.tag + ">" + list.items.map((item) => "<li>" + inlineMarkdown(item) + "</li>").join("") + "</" + list.tag + ">");
    list = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = FENCE.exec(line);
    if (fence) {
      flushParagraph(); flushList();
      const code = [];
      for (i++; i < lines.length && lines[i].trim().indexOf(fence[1]) !== 0; i++) code.push(lines[i]);
      out.push("<pre><code>" + code.join("\n") + "</code></pre>");
      continue;
    }
    const heading = /^(#{1,6})\s+(.*?)[\s#]*$/.exec(line);
    if (heading) {
      flushParagraph(); flushList();
      out.push("<h" + heading[1].length + ">" + inlineMarkdown(heading[2]) + "</h" + heading[1].length + ">");
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flushParagraph(); flushList(); out.push("<hr>"); continue; }
    const item = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(line);
    if (item) {
      flushParagraph();
      const tag = /\d/.test(item[1]) ? "ol" : "ul";
      if (list && list.tag !== tag) flushList();
      if (!list) list = { tag, items: [] };
      list.items.push(item[2]);
      continue;
    }
    if (!line.trim()) { flushParagraph(); flushList(); continue; }
    if (list && /^\s{2,}/.test(line)) { list.items[list.items.length - 1] += " " + line.trim(); continue; }
    flushList();
    paragraph.push(line.trim());
  }
  flushParagraph(); flushList();
  return out.join("\n");
}
function markdownBlock(source) {
  const box = el("div", { class: "md" });
  box.innerHTML = renderMarkdown(source); // escaped above: only the renderer's own tags are HTML
  return box;
}

// ---- Start
$("listen").addEventListener("click", startListening);
$("stop").addEventListener("click", () => {
  stopListening();
  claimState = "idle";
  setStatus("Stopped. Press Listen to start again.");
});
$("unpair").addEventListener("click", unpair);
$("closeViewer").addEventListener("click", () => { $("viewer").hidden = true; $("viewerBody").replaceChildren(); });
document.addEventListener("visibilitychange", () => {
  // Backgrounded tabs lose their sockets: reconnect at once on return instead of waiting out the backoff.
  if (document.visibilityState === "visible" && !socket && sessionStorage.getItem(TOKEN_KEY)) { backoff = 1000; connect(); }
});
setInterval(renderPairing, 30000);
renderCrumbs(HOME);
if (sessionStorage.getItem(TOKEN_KEY)) connect();
else { showScreen("pair"); setStatus("Press Listen to start"); }
</script>
</body>
</html>
`;
