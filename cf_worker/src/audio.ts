import spoken_call_started from "../earcons/call_started.pcm";
import spoken_call_started_ready from "../earcons/call_started_ready.pcm";
import FM_DESIGNS from "./earcon_designs.json";
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
import spoken_follow_started from "../earcons/follow_started.pcm";
import spoken_follow_stopped from "../earcons/follow_stopped.pcm";
import spoken_progress_looking from "../earcons/progress_looking.pcm";
import spoken_progress_changing from "../earcons/progress_changing.pcm";
import spoken_progress_running from "../earcons/progress_running.pcm";
import spoken_progress_still_running from "../earcons/progress_still_running.pcm";
import spoken_progress_failed from "../earcons/progress_failed.pcm";
import spoken_progress_remark from "../earcons/progress_remark.pcm";
import spoken_progress_step from "../earcons/progress_step.pcm";
import spoken_progress_plan_done from "../earcons/progress_plan_done.pcm";
import spoken_progress_tiring from "../earcons/progress_tiring.pcm";
import spoken_pull from "../earcons/pull.pcm";
import prompt_menu from "../earcons/menu.pcm";
import prompt_menu_restart_confirm from "../earcons/menu_restart_confirm.pcm";
import prompt_menu_restarting from "../earcons/menu_restarting.pcm";
import prompt_menu_back from "../earcons/menu_back.pcm";
import prompt_pairing_cue from "../earcons/pairing_cue.pcm";

// Waveform audio for GPT-Live output fixtures: raw mono PCM16LE at 24 kHz, at most 30 s,
// base64-encoded. Earcons are synthesized here; clips come from Sandbox WAV files.
export const SAMPLE_RATE = 24_000;
export const MAX_FIXTURE_SECONDS = 30;

export interface Clip {
  base64: string;
  seconds: number;
}

export type EarconName =
  | "call_started"
  | "call_started_ready"
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
  | "recording_discarded"
  | "follow_started"
  | "follow_stopped"
  | "progress_looking"
  | "progress_changing"
  | "progress_running"
  | "progress_still_running"
  | "progress_failed"
  | "progress_remark"
  | "progress_step"
  | "progress_plan_done"
  | "progress_tiring"
  | "pull";
// fm: the semantic FM vocabulary (earcon_lab.py, SOUND_DESIGN_NOTES.md), the default;
// tonal: the first monophonic chimes; spoken: Fred's labels.
// Tonal is the semantic FM set designed in earcon_lab.py; spoken is short text-to-speech labels.
export type EarconStyle = "tonal" | "spoken";


// Spoken earcons: macOS text-to-speech labels rendered by make_earcons.py (verbose and
// diagnostic). Presentation only; the event names stay neutral.
const SPOKEN: Record<EarconName, Clip> = {
  call_started: pcmClip(spoken_call_started),
  call_started_ready: pcmClip(spoken_call_started_ready),
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
  follow_started: pcmClip(spoken_follow_started),
  follow_stopped: pcmClip(spoken_follow_stopped),
  progress_looking: pcmClip(spoken_progress_looking),
  progress_changing: pcmClip(spoken_progress_changing),
  progress_running: pcmClip(spoken_progress_running),
  progress_still_running: pcmClip(spoken_progress_still_running),
  progress_failed: pcmClip(spoken_progress_failed),
  progress_remark: pcmClip(spoken_progress_remark),
  progress_step: pcmClip(spoken_progress_step),
  progress_plan_done: pcmClip(spoken_progress_plan_done),
  progress_tiring: pcmClip(spoken_progress_tiring),
  pull: pcmClip(spoken_pull),
};

// FM earcons: note lists exported by `earcon_lab.py --export` from the lab's semantic code,
// rendered here by a port of the lab's render(). Regenerate the JSON rather than editing it.
interface FmNote {
  at: number; ms: number; f: number; voices: number[][];
  c: number; m: number; i0: number; i1: number; tau: number; attack: number; k: number; glide: number;
  rough: number; roughRate: number; cut: boolean;
}

// Two-operator FM per voice: sin(carrier + index(t) * sin(modulator)), phase-accumulated so
// the glide stays continuous. The index decays from i0 to i1 (bright attack, mellow tail).
function renderFm({ notes, gain }: { notes: FmNote[]; gain: number }): Clip {
  const end = Math.max(...notes.map((n) => n.at + n.ms));
  const out = new Float32Array(Math.ceil((end / 1000) * SAMPLE_RATE));
  const release = Math.round(0.004 * SAMPLE_RATE);
  for (const n of notes) {
    const start = Math.round((n.at / 1000) * SAMPLE_RATE);
    const full = Math.round((n.ms / 1000) * SAMPLE_RATE);
    const len = n.cut ? Math.round(full * 0.35) : full;
    const attack = Math.max(1, Math.round((n.attack / 1000) * SAMPLE_RATE));
    const weight = n.voices.reduce((sum, [, w]) => sum + w, 0);
    for (const [ratio, w] of n.voices) {
      let pc = 0;
      let pm = 0;
      for (let i = 0; i < len && start + i < out.length; i++) {
        const t = i / SAMPLE_RATE;
        const f = n.f * ratio * (1 - (1 - n.glide) * Math.exp(-t / 0.008));
        pc += (2 * Math.PI * f * n.c) / SAMPLE_RATE;
        pm += (2 * Math.PI * f * n.m) / SAMPLE_RATE;
        const index = n.i1 + (n.i0 - n.i1) * Math.exp((-t * 1000) / n.tau);
        let env = Math.min(1, i / attack, (len - 1 - i) / release);
        if (n.k) env *= Math.exp((-n.k * i) / full);
        if (n.rough) env *= 1 - n.rough * 0.5 * (1 - Math.cos(2 * Math.PI * n.roughRate * t));
        out[start + i] += (w / weight) * env * Math.sin(pc + index * Math.sin(pm)) * gain;
      }
    }
  }
  const samples = Int16Array.from(out, (v) => Math.max(-32768, Math.min(32767, Math.round(v * 32767))));
  return { base64: toBase64(new Uint8Array(samples.buffer)), seconds: out.length / SAMPLE_RATE };
}

const FM_SOURCE = FM_DESIGNS as Record<EarconName, { notes: FmNote[]; gain: number }>;
const FM = Object.fromEntries(
  Object.entries(FM_SOURCE).map(([name, design]) => [name, renderFm(design)]),
) as Record<EarconName, Clip>;

export function earcon(name: EarconName, style: EarconStyle): Clip {
  return (style === "spoken" ? SPOKEN : FM)[name];
}

// Keypad system menu prompts, also rendered by make_earcons.py. They are instructions the
// caller must follow, so they are spoken whatever the earcon style.
export type PromptName = "menu" | "menu_restart_confirm" | "menu_restarting" | "menu_back" | "pairing_cue";

const PROMPTS: Record<PromptName, Clip> = {
  menu: pcmClip(prompt_menu),
  menu_restart_confirm: pcmClip(prompt_menu_restart_confirm),
  menu_restarting: pcmClip(prompt_menu_restarting),
  menu_back: pcmClip(prompt_menu_back),
  pairing_cue: pcmClip(prompt_pairing_cue),
};

export function prompt(name: PromptName): Clip {
  return PROMPTS[name];
}

// Every earcon, Earcon Style, and System Menu prompt, for listings such as the /sounds page.
export const EARCON_NAMES = Object.keys(SPOKEN) as EarconName[];
export const EARCON_STYLES: EarconStyle[] = ["tonal", "spoken"];
export const PROMPT_NAMES = Object.keys(PROMPTS) as PromptName[];

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

export function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}
