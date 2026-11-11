// Operator calibration tooling for the speaker verification Worker (SPEAKER).
// POST /admin/api/voice-verify?user=<id> scores a mono 16-bit WAV against that
// User's Voice Reference; GET /admin/api/voice-verify/health warms the model on a
// synthetic signal. Neither returns nor logs audio or embeddings, and neither
// authorizes anything: thresholds and decisions belong to the Identity Check.
import type { Env, SpeakerAudio } from "./types";

const MAX_WAV_BYTES = 1 << 20; // 30 s at 16 kHz is 960,000 bytes of samples.

type Json = (value: unknown, status?: number) => Response;

// Error messages from SPEAKER begin with a stable code.
const SPEAKER_STATUS: Record<string, number> = {
  "invalid-input": 400, "insufficient-audio": 422, "incompatible-reference": 409, busy: 503, "inference-failed": 502,
};

function speakerFailure(error: unknown, json: Json): Response {
  const message = error instanceof Error ? error.message : String(error);
  const code = /^([a-z-]+): /.exec(message)?.[1] ?? "";
  if (code in SPEAKER_STATUS) return json({ error: code, detail: message.slice(code.length + 2) }, SPEAKER_STATUS[code]);
  return json({ error: "speaker verification unavailable" }, 503);
}

/** The samples of a mono 16-bit PCM WAV at 8 or 16 kHz, or a reason it is not one. */
export function parseWav(bytes: ArrayBuffer): SpeakerAudio | string {
  const view = new DataView(bytes);
  const tag = (offset: number) => String.fromCharCode(...new Uint8Array(bytes, offset, 4));
  if (bytes.byteLength < 12 || tag(0) !== "RIFF" || tag(8) !== "WAVE") return "not a RIFF/WAVE file";
  let format: { encoding: number; channels: number; rate: number; bits: number } | undefined;
  for (let offset = 12; offset + 8 <= bytes.byteLength;) {
    const id = tag(offset);
    const size = view.getUint32(offset + 4, true);
    const body = offset + 8;
    if (id === "fmt " && size >= 16 && body + 16 <= bytes.byteLength) {
      format = { encoding: view.getUint16(body, true), channels: view.getUint16(body + 2, true),
        rate: view.getUint32(body + 4, true), bits: view.getUint16(body + 14, true) };
    } else if (id === "data") {
      if (!format) return "data chunk before fmt chunk";
      if (format.encoding !== 1 || format.channels !== 1 || format.bits !== 16) return "expected mono 16-bit PCM";
      if (format.rate !== 8000 && format.rate !== 16000) return "expected 8000 or 16000 Hz";
      const end = Math.min(body + size, bytes.byteLength);
      return { audio: bytes.slice(body, end - ((end - body) % 2)), format: "pcm_s16le", sampleRate: format.rate };
    }
    offset = body + size + (size % 2);
  }
  return "no data chunk";
}

// Four seconds of a voiced, gated 16 kHz tone: enough "speech" to pass the
// Worker's activity check, so the health probe (and a warm-up) runs a real inference.
export function syntheticAudio(): SpeakerAudio {
  const rate = 16000, samples = new Int16Array(rate * 4);
  let phase = 0;
  for (let i = 0; i < samples.length; i++) {
    const t = i / rate;
    phase += (2 * Math.PI * (110 + 20 * Math.sin(2 * Math.PI * 0.7 * t))) / rate;
    let voice = 0;
    for (let k = 1; k < 12; k++) voice += Math.sin(k * phase) / k;
    const gate = t > 1.2 && Math.sin(2 * Math.PI * 1.3 * t) > -0.2 ? 1 : 0;
    samples[i] = Math.round(Math.max(-1, Math.min(1, 0.2 * voice * gate)) * 32767);
  }
  return { audio: samples.buffer, format: "pcm_s16le", sampleRate: rate };
}

export async function handleVoiceVerify(req: Request, env: Env, url: URL, json: Json): Promise<Response> {
  const health = url.pathname === "/admin/api/voice-verify/health";
  if (req.method !== (health ? "GET" : "POST")) return json({ error: "method not allowed" }, 405);
  if (!env.SPEAKER) return json({ error: "speaker verification is not bound" }, 503);
  if (health) {
    const started = Date.now();
    try {
      const result = await env.SPEAKER.embed(syntheticAudio());
      return json({ ok: true, embedding_space: result.embeddingSpace, audio_seconds: result.audioSeconds,
        ms: Date.now() - started });
    } catch (error) {
      return speakerFailure(error, json);
    }
  }

  const user = Number(url.searchParams.get("user"));
  if (!Number.isSafeInteger(user) || user <= 0) return json({ error: "valid user is required" }, 400);
  if (Number(req.headers.get("content-length") ?? 0) > MAX_WAV_BYTES) return json({ error: "body too large" }, 413);
  const body = await req.arrayBuffer();
  if (body.byteLength > MAX_WAV_BYTES) return json({ error: "body too large" }, 413);
  const audio = parseWav(body);
  if (typeof audio === "string") return json({ error: audio }, 400);
  const row = await env.DB.prepare("SELECT embedding_space, embedding FROM voice_references WHERE user_id = ?")
    .bind(user).first<{ embedding_space: string; embedding: string }>();
  if (!row) return json({ error: "no voice reference for user" }, 404);
  const started = Date.now();
  try {
    const result = await env.SPEAKER.verify({ ...audio,
      reference: { embedding: JSON.parse(row.embedding) as number[], embeddingSpace: row.embedding_space } });
    return json({ cosine_similarity: result.cosineSimilarity, embedding_space: result.embeddingSpace,
      audio_seconds: result.audioSeconds, speech_seconds: result.speechSeconds, ms: Date.now() - started });
  } catch (error) {
    return speakerFailure(error, json);
  }
}
