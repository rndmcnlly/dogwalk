// Operator calibration tooling for the speaker verification Worker (SPEAKER).
// POST /admin/api/voice-verify?user=<id> scores a mono 16-bit WAV against that
// User's Voice Reference; GET /admin/api/voice-verify/health warms the model and embeds a
// synthetic signal, reporting whether that request loaded the model. Neither returns nor logs audio or embeddings, and neither
// authorizes anything: thresholds and decisions belong to the Identity Check.
// warmSpeaker readies the verifier when a call arrives, and userSpeaker routes a User's
// challenges through their VoiceSession so they are scored where it was warmed.
import type { Env, SpeakerAudio, SpeakerReference, SpeakerVerifier } from "./types";

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
// Worker's activity check, so the health probe runs a real inference.
function syntheticAudio(): SpeakerAudio {
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

// --- the verifier, hosted by a User's VoiceSession ----------------------
//
// A verifier instance is warm only on the machine that loaded it (speaker_worker/README.md,
// Latency), and Cloudflare routes each webhook and Media Stream of a call independently, so a
// warm from /voice rarely helps the stream that scores a challenge seconds later. A Durable
// Object stays in the location where it was created, one single-threaded instance at a time,
// and a Service Binding call runs on the same thread of the same server as its caller
// (Cloudflare docs, Service bindings and Durable Objects data location, 2026-11). So every
// warm and score for one User goes through that User's VoiceSession, which calls SPEAKER from
// one place, and the instance it warmed at call arrival is the one that scores.

const SPEAKER_META = "x-dogwalk-speaker";

/**
 * SPEAKER as seen from the VoiceSession keyed by this identity hash. Same calls, same errors
 * (a message starting with the verifier's code), plus `cold` on each result: whether that
 * score had to load the model, the observation that shows whether warming reached it.
 */
export function userSpeaker(env: Env, identityHash: string): SpeakerVerifier | null {
  if (!env.SPEAKER) return null;
  const object = env.VOICE_SESSIONS.get(env.VOICE_SESSIONS.idFromName(identityHash));
  const call = async <T>(op: string, input?: SpeakerAudio & { reference?: SpeakerReference }): Promise<T> => {
    const meta = input ? { sampleRate: input.sampleRate, reference: input.reference } : null;
    const response = await object.fetch(`https://voice-session/speaker/${op}`, {
      method: "POST", body: input?.audio, headers: meta ? { [SPEAKER_META]: JSON.stringify(meta) } : {},
    });
    const result = await response.json<T & { error?: string }>();
    if (result.error) throw new Error(result.error);
    return result;
  };
  return {
    warm: () => call("warm"),
    embed: (input) => call("embed", input),
    verify: (input) => call("verify", input),
  };
}

/** The VoiceSession's side of userSpeaker: /speaker/warm, /speaker/embed, /speaker/verify. */
export async function hostSpeaker(env: Env, request: Request, op: string): Promise<Response> {
  const speaker = env.SPEAKER;
  if (!speaker) return Response.json({ error: "unavailable: no verifier" });
  if (request.method !== "POST" || !["warm", "embed", "verify"].includes(op)) return new Response("not found", { status: 404 });
  try {
    const started = Date.now();
    // warm() first, as health does, so each result says whether it loaded the model (cold)
    // apart from queueing behind another request in the same isolate.
    const { cold } = await speaker.warm();
    if (op === "warm") {
      console.log("speaker warm", { cold, ms: Date.now() - started, hosted: true });
      return Response.json({ cold });
    }
    const meta = JSON.parse(request.headers.get(SPEAKER_META) ?? "{}") as { sampleRate?: number; reference?: SpeakerReference };
    const audio: SpeakerAudio = { audio: await request.arrayBuffer(), format: "pcm_s16le", sampleRate: Number(meta.sampleRate) };
    if (op === "embed") return Response.json({ ...await speaker.embed(audio), cold });
    if (!meta.reference) throw new Error("invalid-input: no reference");
    return Response.json({ ...await speaker.verify({ ...audio, reference: meta.reference }), cold });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : String(error) });
  }
}

/**
 * Starts loading the verifier in the background ahead of scoring (an Identity Check,
 * enrollment, Challenge Practice), so a challenge later in the call skips the 3–5 s model
 * load. With an identity hash the warm goes through that User's VoiceSession, where their
 * challenges are scored (userSpeaker); without one it warms this machine. Returns nothing to
 * await: the work goes to waitUntil and never delays TwiML, and every failure, including an
 * unbound SPEAKER, is logged and dropped.
 */
export function warmSpeaker(env: Env, ctx: Pick<ExecutionContext, "waitUntil">, identityHash?: string): void {
  const speaker = identityHash ? userSpeaker(env, identityHash) : env.SPEAKER;
  if (!speaker) return;
  ctx.waitUntil((async () => {
    try {
      const started = Date.now();
      const { cold } = await speaker.warm();
      console.log("speaker warm", { cold, ms: Date.now() - started });
    } catch (error) {
      console.error("speaker warm failed", error instanceof Error ? error.message : String(error));
    }
  })());
}

export async function handleVoiceVerify(req: Request, env: Env, url: URL, json: Json): Promise<Response> {
  const health = url.pathname === "/admin/api/voice-verify/health";
  if (req.method !== (health ? "GET" : "POST")) return json({ error: "method not allowed" }, 405);
  if (!env.SPEAKER) return json({ error: "speaker verification is not bound" }, 503);
  if (health) {
    // warm() first, so the response says whether this request loaded the model (cold) apart
    // from time spent queued behind other requests in the same isolate.
    const started = Date.now();
    try {
      const { cold } = await env.SPEAKER.warm();
      const warmMs = Date.now() - started;
      const result = await env.SPEAKER.embed(syntheticAudio());
      return json({ ok: true, embedding_space: result.embeddingSpace, audio_seconds: result.audioSeconds,
        cold, warm_ms: warmMs, ms: Date.now() - started });
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
