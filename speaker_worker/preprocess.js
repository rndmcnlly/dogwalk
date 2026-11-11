// Audio preprocessing for the speaker embedding model, ported from the Voice
// Reference builder: decode 16-bit PCM, pick the most speech-active 3 s after
// skipping 0.5 s, and resample 8 kHz to 16 kHz exactly as scipy's
// resample_poly(x, 2, 1) does. Pure functions, so Node can check them against
// the Python builder without the Worker runtime.

export const MODEL_RATE = 16000;
export const WINDOW_SECONDS = 3;
export const SKIP_SECONDS = 0.5;
export const MIN_SPEECH_SECONDS = 1;
const FRAME_SECONDS = 0.02;
const MIN_RUN_FRAMES = 5;

export class SpeakerError extends Error {
  constructor(code, detail) {
    super(`${code}: ${detail}`);
    this.code = code;
  }
}

/** Signed 16-bit little-endian PCM to float samples in [-1, 1), like soundfile's float32 read. */
export function decodePcm16(buffer) {
  const view = new DataView(buffer);
  const out = new Float32Array(buffer.byteLength >> 1);
  for (let i = 0; i < out.length; i++) out[i] = view.getInt16(2 * i, true) / 32768;
  return out;
}

// numpy.percentile with its default linear interpolation.
function percentile(values, q) {
  const sorted = Float64Array.from(values).sort();
  const pos = (q / 100) * (sorted.length - 1);
  const lo = Math.floor(pos);
  const hi = Math.min(lo + 1, sorted.length - 1);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

/**
 * The builder's `context3` window: frame energies in dB, an adaptive threshold,
 * active runs shorter than 100 ms dropped, then the 3 s span holding the most
 * active frames. Returns null when fewer than 3 s follow the skip.
 */
export function speechWindow(wave, rate) {
  const x = wave.subarray(Math.floor(SKIP_SECONDS * rate));
  const n = Math.floor(FRAME_SECONDS * rate);
  const frames = Math.floor(x.length / n);
  const db = new Float64Array(frames);
  for (let f = 0; f < frames; f++) {
    let sum = 0;
    for (let i = f * n; i < (f + 1) * n; i++) sum += x[i] * x[i];
    db[f] = 20 * Math.log10(Math.max(Math.sqrt(sum / n), 1e-9));
  }
  const threshold = Math.max(-55, percentile(db, 20) + 12, percentile(db, 95) - 30);
  const active = Uint8Array.from(db, (v) => (v > threshold ? 1 : 0));
  for (let start = 0; start < frames; ) {
    if (!active[start]) { start++; continue; }
    let end = start;
    while (end < frames && active[end]) end++;
    if (end - start < MIN_RUN_FRAMES) active.fill(0, start, end);
    start = end;
  }
  const speechSeconds = active.reduce((a, b) => a + b, 0) * FRAME_SECONDS;
  const span = Math.round(WINDOW_SECONDS / FRAME_SECONDS);
  if (frames < span) return { window: null, speechSeconds };
  let count = 0;
  for (let f = 0; f < span; f++) count += active[f];
  let best = count, bestStart = 0;
  for (let s = 1; s + span <= frames; s++) {
    count += active[s + span - 1] - active[s - 1];
    if (count > best) { best = count; bestStart = s; }
  }
  return { window: x.subarray(bestStart * n, bestStart * n + WINDOW_SECONDS * rate), speechSeconds };
}

function besselI0(x) {
  let sum = 1, term = 1;
  for (let k = 1; k < 50; k++) {
    term *= (x / (2 * k)) ** 2;
    sum += term;
    if (term < sum * 1e-17) break;
  }
  return sum;
}

// scipy.signal.firwin(41, 0.5, window=("kaiser", 5.0)), cast to float32 as
// resample_poly does for float32 input, then scaled by up = 2.
const HALF_LEN = 20;
const TAPS = (() => {
  const numtaps = 2 * HALF_LEN + 1, beta = 5, cutoff = 0.5;
  const h = new Float64Array(numtaps);
  let total = 0;
  for (let i = 0; i < numtaps; i++) {
    const m = i - HALF_LEN;
    const sinc = m === 0 ? 1 : Math.sin(Math.PI * cutoff * m) / (Math.PI * cutoff * m);
    const r = (2 * i) / (numtaps - 1) - 1;
    h[i] = cutoff * sinc * (besselI0(beta * Math.sqrt(1 - r * r)) / besselI0(beta));
    total += h[i];
  }
  return Float32Array.from(h, (v) => Math.fround(v / total) * 2);
})();

/** 8 kHz to 16 kHz, zero-padded at the edges: scipy's resample_poly(x, 2, 1). */
export function upsample2(x) {
  const y = new Float32Array(2 * x.length);
  for (let m = 0; m < y.length; m++) {
    let acc = 0;
    // y[m] = sum_i h[i] * u[m + HALF_LEN - i], u the zero-stuffed input.
    for (let i = (m + HALF_LEN) & 1; i < TAPS.length; i += 2) {
      const k = (m + HALF_LEN - i) >> 1;
      if (k >= 0 && k < x.length) acc += TAPS[i] * x[k];
    }
    y[m] = acc;
  }
  return y;
}

/** PCM bytes to the model's 48000-sample 16 kHz input, or an explicit SpeakerError. */
export function modelInput(buffer, sampleRate, maxSeconds) {
  if (!(buffer instanceof ArrayBuffer)) throw new SpeakerError("invalid-input", "audio must be an ArrayBuffer");
  if (sampleRate !== 8000 && sampleRate !== 16000) throw new SpeakerError("invalid-input", "sampleRate must be 8000 or 16000");
  if (buffer.byteLength % 2) throw new SpeakerError("invalid-input", "pcm_s16le audio has an odd byte length");
  const audioSeconds = buffer.byteLength / 2 / sampleRate;
  if (audioSeconds > maxSeconds) throw new SpeakerError("invalid-input", `audio exceeds ${maxSeconds} s`);
  const minSeconds = SKIP_SECONDS + WINDOW_SECONDS;
  if (audioSeconds < minSeconds) throw new SpeakerError("insufficient-audio", `${audioSeconds.toFixed(2)} s of audio, need ${minSeconds} s`);
  const { window, speechSeconds } = speechWindow(decodePcm16(buffer), sampleRate);
  if (!window || speechSeconds < MIN_SPEECH_SECONDS)
    throw new SpeakerError("insufficient-audio", `${speechSeconds.toFixed(2)} s of speech, need ${MIN_SPEECH_SECONDS} s`);
  const waveform = sampleRate === MODEL_RATE ? Float32Array.from(window) : upsample2(window);
  return { waveform, audioSeconds, speechSeconds };
}
