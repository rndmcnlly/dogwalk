// Caller-only Twilio Media Streams received by this Worker, one per speech round. Voice
// Donation (voice_donation.ts) keeps what arrives; Challenge Practice (challenge_practice.ts)
// scores it and keeps nothing. Each round's TwiML starts a stream to a signed wss URL, so the
// WebSocket lives only as long as one round: it buffers the mu-law frames in memory and hands
// the decoded samples to its caller when the stream stops or closes.
import type { Env } from "./types";

export const SAMPLE_RATE = 8000;
const STREAM_CAPABILITY_SECONDS = 5 * 60;
const MAX_FRAME_CHARS = 64 * 1024;
const ACCOUNT_SID = /^AC[0-9a-f]{32}$/;

export const isAccountSid = (value: string) => ACCOUNT_SID.test(value);

// --- round TwiML --------------------------------------------------------

export const escapeXml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
export const Say = (text: string) => `<Say voice="Polly.Joanna">${escapeXml(text)}</Say>`;
export const GatherSpeech = (prompt: string, action: string, timing: string) =>
  `<Gather input="speech" ${timing} actionOnEmptyResult="true" profanityFilter="false" hints="stop" ` +
  `action="${escapeXml(action)}" method="POST" language="en-US">${Say(prompt)}</Gather>`;
/** Start a round's caller-only stream; its custom parameters must come back in the start event. */
export const StartStream = (name: string, url: string, parameters: Record<string, string>) =>
  `<Start><Stream name="${escapeXml(name)}" url="${escapeXml(url)}" track="inbound_track">` +
  Object.entries(parameters).map(([key, value]) => `<Parameter name="${escapeXml(key)}" value="${escapeXml(value)}"/>`).join("") +
  `</Stream></Start>`;
export const StopStream = (name: string) => `<Stop><Stream name="${escapeXml(name)}"/></Stop>`;

export const speechWords = (raw: string): string[] => raw.toLowerCase().replace(/[^a-z\s]/g, " ").split(/\s+/).filter(Boolean);
// A short stop only: "I had to stop at the store" in free speech is a story, not a request.
export const saidStop = (heard: string[]) => heard.includes("stop") && heard.length <= 3;

export function originHost(env: Env, url: URL): string {
  return env.PUBLIC_ORIGIN ? new URL(env.PUBLIC_ORIGIN).host : url.host;
}

// --- capability ---------------------------------------------------------

const textEncoder = new TextEncoder();

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function capabilitySignature(env: Env, purpose: string, content: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", textEncoder.encode(env.DOGWALK_IDENTITY_SECRET),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return base64Url(new Uint8Array(await crypto.subtle.sign("HMAC", key, textEncoder.encode(`${purpose}-stream-v1:${content}`))));
}

/**
 * A wss path segment naming one round (its fields, which may not contain dots), the Twilio
 * account whose signed webhook asked for it, and an expiry a few minutes away. The capability
 * rides in the path because Twilio does not allow a query string on a Stream URL.
 */
export async function streamCapability(env: Env, purpose: string, fields: string[], account: string): Promise<string> {
  const expires = Math.floor(Date.now() / 1000) + STREAM_CAPABILITY_SECONDS;
  const content = [...fields, expires, account].join(".");
  return `${content}.${await capabilitySignature(env, purpose, content)}`;
}

/** The fields and account of an unexpired capability signed for this purpose, or null. */
export async function verifyStreamCapability(
  env: Env, purpose: string, capability: string, fieldCount: number,
): Promise<{ fields: string[]; account: string } | null> {
  const parts = capability.split(".");
  if (parts.length !== fieldCount + 3) return null;
  const fields = parts.slice(0, fieldCount);
  const [expiresRaw, account, signature] = parts.slice(fieldCount);
  const expires = Number(expiresRaw);
  if (!ACCOUNT_SID.test(account) || !Number.isInteger(expires) || expires < Date.now() / 1000) return null;
  const expected = await capabilitySignature(env, purpose, parts.slice(0, fieldCount + 2).join("."));
  if (expected.length !== signature.length) return null;
  let difference = 0;
  for (let i = 0; i < expected.length; i++) difference |= expected.charCodeAt(i) ^ signature.charCodeAt(i);
  return difference === 0 ? { fields, account } : null;
}

// --- audio --------------------------------------------------------------

const ULAW = Int16Array.from({ length: 256 }, (_, encoded) => {
  const value = ~encoded & 0xff;
  const magnitude = ((((value & 0x0f) << 3) + 0x84) << ((value & 0x70) >> 4)) - 0x84;
  return value & 0x80 ? -magnitude : magnitude;
});

/** A mono 16-bit 8 kHz WAV of the samples. */
export function wav(samples: Int16Array): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.byteLength);
  const view = new DataView(buffer);
  const tag = (offset: number, text: string) => { for (let i = 0; i < 4; i++) view.setUint8(offset + i, text.charCodeAt(i)); };
  tag(0, "RIFF"); view.setUint32(4, 36 + samples.byteLength, true); tag(8, "WAVE");
  tag(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, SAMPLE_RATE, true); view.setUint32(28, SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  tag(36, "data"); view.setUint32(40, samples.byteLength, true);
  for (let i = 0; i < samples.length; i++) view.setInt16(44 + i * 2, samples[i], true);
  return buffer;
}

let toneWav: ArrayBuffer | null = null;
/** The round cue: 150 ms of 880 Hz with short fades. Public, since it carries nothing personal. */
export function roundTone(): Response {
  if (!toneWav) {
    const samples = new Int16Array(SAMPLE_RATE * 0.15);
    for (let i = 0; i < samples.length; i++) {
      const fade = Math.min(1, i / 80, (samples.length - i) / 80);
      samples[i] = Math.round(9000 * fade * Math.sin(2 * Math.PI * 880 * i / SAMPLE_RATE));
    }
    toneWav = wav(samples);
  }
  return new Response(toneWav, { headers: { "content-type": "audio/wav", "cache-control": "public, max-age=86400" } });
}

// --- Media Stream -------------------------------------------------------

export interface StreamedAudio {
  samples: Int16Array;
  complete: boolean;
  frames: number;
  seconds: number;
  inserted_gap_ms: number;
  truncated: boolean;
  started_at: string;
  ended_at: string;
}

/**
 * Accept one round's caller-only Media Stream. The caller has already checked the upgrade's
 * Twilio signature and the capability; this checks the start event (account, custom
 * parameters, format) and the frames. Anything malformed closes the socket and hands over
 * nothing. A stream that carried audio is decoded and passed to `ended` inside waitUntil,
 * with `complete` false when the socket closed without a stop event.
 */
export function acceptCallerStream(
  ctx: ExecutionContext,
  expect: { account: string; parameters: Record<string, string>; maxSeconds: number },
  ended: (audio: StreamedAudio) => Promise<void>,
): Response {
  const [client, server] = Object.values(new WebSocketPair());
  server.accept();
  const frames: Uint8Array[] = [];
  const limit = expect.maxSeconds * SAMPLE_RATE;
  let bytes = 0, count = 0, gapMs = 0, truncated = false;
  let startedAt: string | null = null;
  let settled = false;
  let resolveDone = () => {};
  ctx.waitUntil(new Promise<void>((resolve) => { resolveDone = resolve; }));

  const append = (chunk: Uint8Array) => {
    const room = limit - bytes;
    if (chunk.length > room) truncated = true;
    if (room <= 0) return;
    const kept = chunk.length > room ? chunk.subarray(0, room) : chunk;
    frames.push(kept);
    bytes += kept.length;
  };
  const discard = (code: number, reason: string) => {
    if (settled) return;
    settled = true;
    frames.length = 0;
    try { server.close(code, reason); } catch { /* already closed */ }
    resolveDone();
  };
  const finish = async (complete: boolean) => {
    if (settled) return;
    settled = true;
    try {
      if (startedAt && bytes > 0) {
        const samples = new Int16Array(bytes);
        let offset = 0;
        for (const frame of frames) {
          for (let i = 0; i < frame.length; i++) samples[offset + i] = ULAW[frame[i]];
          offset += frame.length;
        }
        frames.length = 0;
        await ended({
          samples, complete, frames: count, seconds: bytes / SAMPLE_RATE, inserted_gap_ms: gapMs, truncated,
          started_at: startedAt, ended_at: new Date().toISOString(),
        });
      }
    } catch (error) {
      console.error("caller stream handling failed", error instanceof Error ? error.message : "unknown");
    } finally {
      try { server.close(1000, "done"); } catch { /* already closed */ }
      resolveDone();
    }
  };

  server.addEventListener("message", (event) => {
    if (settled) return;
    if (typeof event.data !== "string" || event.data.length > MAX_FRAME_CHARS) return discard(1009, "unexpected frame");
    let message: Record<string, any>;
    try {
      message = JSON.parse(event.data);
    } catch {
      return discard(1007, "not json");
    }
    if (message.event === "connected") return;
    if (message.event === "start") {
      const start = message.start ?? {};
      const format = start.mediaFormat ?? {};
      const parameters = start.customParameters ?? {};
      if (startedAt || start.accountSid !== expect.account
        || Object.entries(expect.parameters).some(([name, value]) => String(parameters[name]) !== value)
        || format.encoding !== "audio/x-mulaw" || Number(format.sampleRate) !== SAMPLE_RATE || Number(format.channels) !== 1) {
        return discard(1008, "unexpected stream");
      }
      startedAt = new Date().toISOString();
      return;
    }
    if (message.event === "media") {
      const media = message.media ?? {};
      if (!startedAt || (media.track ?? "inbound") !== "inbound" || typeof media.payload !== "string") {
        return discard(1008, "unexpected media");
      }
      let chunk: Uint8Array;
      try {
        chunk = Uint8Array.from(atob(media.payload), (character) => character.charCodeAt(0));
      } catch {
        return discard(1007, "bad payload");
      }
      // Twilio timestamps are milliseconds from the stream's start. Lost frames are filled with
      // mu-law silence so the audio keeps real time, and the fill is reported.
      const timestamp = Number(media.timestamp);
      const expected = Math.floor(bytes / (SAMPLE_RATE / 1000));
      // A whole 20 ms frame or more must be missing, so timestamp jitter is never "filled".
      if (Number.isFinite(timestamp) && timestamp - expected >= 20) {
        const missing = Math.min(timestamp - expected, 2000);
        append(new Uint8Array(missing * (SAMPLE_RATE / 1000)).fill(0xff));
        gapMs += missing;
      }
      count++;
      append(chunk);
      return;
    }
    if (message.event === "stop") return void finish(true);
  });
  server.addEventListener("close", () => void finish(false));
  server.addEventListener("error", () => void finish(false));
  return new Response(null, { status: 101, webSocket: client });
}
