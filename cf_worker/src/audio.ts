import prompt_menu from "../menu_prompts/menu.pcm";
import prompt_menu_restart_confirm from "../menu_prompts/menu_restart_confirm.pcm";
import prompt_menu_restarting from "../menu_prompts/menu_restarting.pcm";
import prompt_menu_back from "../menu_prompts/menu_back.pcm";
import { sineBlips } from "./caller_stream";

// Waveform audio for GPT-Live output fixtures: raw mono PCM16LE at 24 kHz, at most 30 s,
// base64-encoded. Tones are synthesized here; clips come from Sandbox WAV files.
export const SAMPLE_RATE = 24_000;
export const MAX_FIXTURE_SECONDS = 30;

export interface Clip {
  base64: string;
  seconds: number;
}

// Keypad System Menu prompts, rendered by make_menu_prompts.py with macOS text-to-speech:
// prerecorded because the menu must work when the Voice Agent cannot hear or think straight.
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

// The voicemail beep before a recording: C6 rising to F6, a little below speech.
const beep = sineBlips([{ hz: 1046.5, at: 0, ms: 72 }, { hz: 1396.9, at: 72, ms: 120 }], 2200, SAMPLE_RATE);
export const RECORDING_BEEP: Clip = { base64: toBase64(new Uint8Array(beep.buffer)), seconds: beep.length / SAMPLE_RATE };

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

// Accepts only 16-bit PCM, mono, 24 kHz WAV, which an Agent can produce with
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

export function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}
