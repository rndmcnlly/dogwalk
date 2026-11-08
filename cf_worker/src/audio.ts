import spoken_call_started from "../earcons/call_started.pcm";
import spoken_session_created from "../earcons/session_created.pcm";
import spoken_turn_started from "../earcons/turn_started.pcm";
import spoken_turn_stopped from "../earcons/turn_stopped.pcm";
import spoken_turn_failed from "../earcons/turn_failed.pcm";
import spoken_dogs_ready from "../earcons/dogs_ready.pcm";
import spoken_turn_cancelled from "../earcons/turn_cancelled.pcm";
import spoken_turn_truncated from "../earcons/turn_truncated.pcm";
import spoken_turn_refused from "../earcons/turn_refused.pcm";
import spoken_permission_requested from "../earcons/permission_requested.pcm";
import spoken_session_closed from "../earcons/session_closed.pcm";
import spoken_recording_started from "../earcons/recording_started.pcm";
import spoken_recording_saved from "../earcons/recording_saved.pcm";
import spoken_recording_discarded from "../earcons/recording_discarded.pcm";
import prompt_menu from "../earcons/menu.pcm";
import prompt_menu_restart_confirm from "../earcons/menu_restart_confirm.pcm";
import prompt_menu_restarting from "../earcons/menu_restarting.pcm";
import prompt_menu_back from "../earcons/menu_back.pcm";

// Waveform audio for GPT-Live output fixtures: raw mono PCM16LE at 24 kHz, at most 30 s,
// base64-encoded. Earcons are synthesized here; clips come from Sandbox WAV files.
export const SAMPLE_RATE = 24_000;
export const MAX_FIXTURE_SECONDS = 30;

export interface Clip {
  base64: string;
  seconds: number;
}

// [frequency Hz, duration ms] notes, each with a short attack and exponential decay.
function synthesize(notes: Array<[number, number]>, gain = 0.28): Clip {
  const total = notes.reduce((sum, [, ms]) => sum + Math.round((ms / 1000) * SAMPLE_RATE), 0);
  const samples = new Int16Array(total);
  let offset = 0;
  for (const [frequency, ms] of notes) {
    const length = Math.round((ms / 1000) * SAMPLE_RATE);
    for (let i = 0; i < length; i++) {
      const t = i / SAMPLE_RATE;
      const envelope = Math.min(1, i / 240) * Math.exp(-5 * (i / length));
      // A touch of second harmonic keeps the chime audible through narrowband telephony.
      const wave = Math.sin(2 * Math.PI * frequency * t) + 0.3 * Math.sin(4 * Math.PI * frequency * t);
      samples[offset + i] = Math.round(wave * envelope * gain * 32767 / 1.3);
    }
    offset += length;
  }
  return { base64: toBase64(new Uint8Array(samples.buffer)), seconds: total / SAMPLE_RATE };
}

export type EarconName =
  | "call_started"
  | "session_created"
  | "turn_started"
  | "turn_stopped"
  | "turn_failed"
  | "dogs_ready"
  | "turn_cancelled"
  | "turn_truncated"
  | "turn_refused"
  | "permission_requested"
  | "session_closed"
  | "recording_started"
  | "recording_saved"
  | "recording_discarded";
export type EarconStyle = "spoken" | "tonal";

// Tonal earcons: short note patterns, rising for arrivals and falling for departures.
const TONAL: Record<EarconName, Clip> = {
  call_started: synthesize([[523.25, 140], [783.99, 220]]),
  session_created: synthesize([[523.25, 90], [659.25, 90], [783.99, 140]]),
  turn_started: synthesize([[659.25, 100], [987.77, 140]]),
  turn_stopped: synthesize([[783.99, 110], [1046.5, 170]]),
  turn_failed: synthesize([[493.88, 150], [349.23, 260]]),
  dogs_ready: synthesize([[392, 90], [523.25, 90], [659.25, 90], [783.99, 160]]),
  turn_cancelled: synthesize([[783.99, 110], [659.25, 170]]),
  turn_truncated: synthesize([[783.99, 110], [783.99, 110], [739.99, 200]]),
  turn_refused: synthesize([[523.25, 120], [369.99, 240]]),
  permission_requested: synthesize([[880, 110], [880, 110], [1174.66, 180]]),
  session_closed: synthesize([[783.99, 110], [523.25, 170]]),
  recording_started: synthesize([[1046.5, 260]]),
  recording_saved: synthesize([[1046.5, 100], [1318.51, 180]]),
  recording_discarded: synthesize([[659.25, 120], [440, 220]]),
};

// Spoken earcons: macOS text-to-speech labels rendered by make_earcons.py (verbose and
// diagnostic). Presentation only; the event names stay neutral.
const SPOKEN: Record<EarconName, Clip> = {
  call_started: pcmClip(spoken_call_started),
  session_created: pcmClip(spoken_session_created),
  turn_started: pcmClip(spoken_turn_started),
  turn_stopped: pcmClip(spoken_turn_stopped),
  turn_failed: pcmClip(spoken_turn_failed),
  dogs_ready: pcmClip(spoken_dogs_ready),
  turn_cancelled: pcmClip(spoken_turn_cancelled),
  turn_truncated: pcmClip(spoken_turn_truncated),
  turn_refused: pcmClip(spoken_turn_refused),
  permission_requested: pcmClip(spoken_permission_requested),
  session_closed: pcmClip(spoken_session_closed),
  recording_started: pcmClip(spoken_recording_started),
  recording_saved: pcmClip(spoken_recording_saved),
  recording_discarded: pcmClip(spoken_recording_discarded),
};

export function earcon(name: EarconName, style: EarconStyle): Clip {
  return (style === "tonal" ? TONAL : SPOKEN)[name];
}

// Keypad system menu prompts, also rendered by make_earcons.py. They are instructions the
// caller must follow, so they are spoken whatever the earcon style.
export type PromptName = "menu" | "menu_restart_confirm" | "menu_restarting" | "menu_back";

const PROMPTS: Record<PromptName, Clip> = {
  menu: pcmClip(prompt_menu),
  menu_restart_confirm: pcmClip(prompt_menu_restart_confirm),
  menu_restarting: pcmClip(prompt_menu_restarting),
  menu_back: pcmClip(prompt_menu_back),
};

export function prompt(name: PromptName): Clip {
  return PROMPTS[name];
}

// A tenth of a second of silence: playing any fixture cuts off the model's speech.
export const SILENCE: Clip = { base64: toBase64(new Uint8Array(SAMPLE_RATE / 5)), seconds: 0.1 };

function pcmClip(data: ArrayBuffer): Clip {
  return { base64: toBase64(new Uint8Array(data)), seconds: data.byteLength / (SAMPLE_RATE * 2) };
}

// A caller recording as a WAV file (mono PCM16 at 24 kHz, as reflected by GPT-Live).
export function pcmToWav(chunks: Uint8Array[]): Uint8Array<ArrayBuffer> {
  const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const wav = new Uint8Array(44 + length);
  const view = new DataView(wav.buffer);
  const ascii = (at: number, text: string) => [...text].forEach((c, i) => (wav[at + i] = c.charCodeAt(0)));
  ascii(0, "RIFF"); view.setUint32(4, 36 + length, true); ascii(8, "WAVE");
  ascii(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, SAMPLE_RATE, true); view.setUint32(28, SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  ascii(36, "data"); view.setUint32(40, length, true);
  let offset = 44;
  for (const chunk of chunks) { wav.set(chunk, offset); offset += chunk.length; }
  return wav;
}

export function base64ToBytes(base64: string): Uint8Array {
  return Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
}

// Mean absolute amplitude of a PCM16 chunk, 0..1, for silence detection.
export function loudness(pcm: Uint8Array): number {
  const samples = new Int16Array(pcm.buffer, pcm.byteOffset, Math.floor(pcm.byteLength / 2));
  let sum = 0;
  for (const sample of samples) sum += Math.abs(sample);
  return samples.length ? sum / samples.length / 32768 : 0;
}

// Accepts only 16-bit PCM, mono, 24 kHz WAV, which a dog can produce with
// `ffmpeg -i input -ac 1 -ar 24000 -c:a pcm_s16le output.wav`.
export function wavToClip(bytes: Uint8Array): Clip & { truncated: boolean; totalSeconds: number } {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const tag = (at: number) => String.fromCharCode(...bytes.subarray(at, at + 4));
  if (bytes.length < 12 || tag(0) !== "RIFF" || tag(8) !== "WAVE") throw new Error("not a WAV file");
  let format: { audioFormat: number; channels: number; rate: number; bits: number } | null = null;
  for (let at = 12; at + 8 <= bytes.length;) {
    const id = tag(at);
    const size = view.getUint32(at + 4, true);
    if (id === "fmt ") {
      format = {
        audioFormat: view.getUint16(at + 8, true),
        channels: view.getUint16(at + 10, true),
        rate: view.getUint32(at + 12, true),
        bits: view.getUint16(at + 22, true),
      };
    } else if (id === "data") {
      if (!format || format.audioFormat !== 1 || format.channels !== 1 || format.rate !== SAMPLE_RATE || format.bits !== 16) {
        throw new Error(`WAV must be 16-bit PCM, mono, 24000 Hz (got ${JSON.stringify(format)}); convert with: ffmpeg -i input -ac 1 -ar 24000 -c:a pcm_s16le output.wav`);
      }
      const data = bytes.subarray(at + 8, Math.min(bytes.length, at + 8 + size));
      const limit = MAX_FIXTURE_SECONDS * SAMPLE_RATE * 2;
      const clipped = data.subarray(0, Math.min(data.length, limit) & ~1);
      return {
        base64: toBase64(normalize(clipped)),
        seconds: clipped.length / (SAMPLE_RATE * 2),
        totalSeconds: data.length / (SAMPLE_RATE * 2),
        truncated: data.length > limit,
      };
    }
    at += 8 + size + (size % 2);
  }
  throw new Error("WAV has no data chunk");
}

// Agent-made audio arrives at any loudness; a playtest found a full-scale supersaw jarring
// next to the voice. Scale to a speech-like level (RMS about -20 dBFS, peaks at most -4 dBFS)
// and fade the edges so clips never start or stop with a click.
const TARGET_RMS = 0.1;
const PEAK_CEILING = 0.63;
const FADE_SAMPLES = Math.round(0.015 * SAMPLE_RATE);

function normalize(pcm: Uint8Array): Uint8Array {
  const input = new Int16Array(pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength));
  let sumSquares = 0;
  let peak = 0;
  for (const sample of input) {
    const value = Math.abs(sample) / 32768;
    sumSquares += value * value;
    peak = Math.max(peak, value);
  }
  const rms = Math.sqrt(sumSquares / Math.max(1, input.length));
  const gain = rms > 0 ? Math.min(TARGET_RMS / rms, PEAK_CEILING / peak) : 1;
  const output = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const fade = Math.min(1, i / FADE_SAMPLES, (input.length - 1 - i) / FADE_SAMPLES);
    output[i] = Math.max(-32768, Math.min(32767, Math.round(input[i] * gain * fade)));
  }
  return new Uint8Array(output.buffer);
}

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}
