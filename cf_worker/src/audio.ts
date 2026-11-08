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

export const EARCONS = {
  // Presentation only (the walk's jingle); event names stay neutral.
  call_started: synthesize([[523.25, 140], [783.99, 220]]),
  turn_stopped: synthesize([[783.99, 110], [1046.5, 170]]),
} satisfies Record<string, Clip>;

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
        base64: toBase64(clipped),
        seconds: clipped.length / (SAMPLE_RATE * 2),
        totalSeconds: data.length / (SAMPLE_RATE * 2),
        truncated: data.length > limit,
      };
    }
    at += 8 + size + (size % 2);
  }
  throw new Error("WAV has no data chunk");
}

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}
