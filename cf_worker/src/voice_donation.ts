// Voice Donation (DOMAIN.md, Voice Research): any caller may donate engineering voice samples
// after a spoken consent, without a Dogwalk account. Operational Dogwalk audio stays transient;
// a Donation Session keeps raw audio in the private RESEARCH_AUDIO bucket under a fresh random
// id, with no phone number, CallSid, User, or Handle beside it. Timestamps are the only way
// back to a call, and they are coarse on purpose: Adam annotates sessions from memory.
//
// The flow is deterministic TwiML and needs no keypad. Each round starts a caller-only Media
// Stream to a signed wss URL, plays a tone, says the prompt inside a speech Gather (whose
// transcript is kept), and the Gather's action stops the stream before the next round. This
// Worker receives each stream itself: the WebSocket lives only as long as one round, so it
// buffers the mu-law frames in memory and writes one WAV when the stream stops or closes.
// Never <Record>: this Twilio account served recording media without authentication.
import { challengePhrases } from "./challenge_phrases";
import type { Env } from "./types";

export const CONSENT_VERSION = "voice-donation-consent-2026-11-11";

// Spoken before anything is recorded. Changing the words means a new CONSENT_VERSION.
const CONSENT_SCRIPT =
  "Thank you for offering to donate voice samples. Here is what happens. " +
  "Dogwalk will record your voice while you repeat about eight short sentences and talk freely for a little while, " +
  "about three minutes in all. " +
  "Adam Smith will use the recordings to tune voice recognition for his Dogwalk prototype. " +
  "The raw audio is kept on Adam's Cloudflare storage and is not linked to your phone number. " +
  "You can say stop at any time. " +
  "To have your recording deleted later, tell Adam roughly when you called. " +
  "Do you agree to be recorded? Say yes or no.";

const PHRASE_ROUNDS = 8;
const FREE_SPEECH_PROMPTS = [
  "Tell me about your morning, for about fifteen seconds.",
  "Describe the room you are in right now, for about fifteen seconds.",
  "Tell me about a meal you enjoyed recently, for about fifteen seconds.",
  "Describe a walk or drive you take often, for about fifteen seconds.",
  "Tell me what the weather has been like where you are, for about fifteen seconds.",
];
const MAX_CONSENT_ATTEMPTS = 3;
const STREAM_CAPABILITY_SECONDS = 5 * 60;
// A speech Gather listens for at most a minute; anything past this is a stuck stream.
const MAX_ROUND_SECONDS = 90;
const MAX_FRAME_CHARS = 64 * 1024;
const SAMPLE_RATE = 8000;

const SESSION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SESSION_DATE = /^\d{4}-\d{2}-\d{2}$/;
const ACCOUNT_SID = /^AC[0-9a-f]{32}$/;

const YES_WORDS = new Set(["yes", "yeah", "yep", "yup", "sure", "okay", "ok", "agree", "consent", "absolutely"]);
const NO_WORDS = new Set(["no", "nope", "not", "don", "dont", "stop", "decline", "cancel", "never"]);
export const DONATE_WORDS = new Set(["donate", "donation", "donations", "donating"]);

type RoundKind = "phrase" | "free_speech";
interface StreamRecord {
  complete: boolean;
  frames: number;
  seconds: number;
  inserted_gap_ms: number;
  truncated: boolean;
  started_at: string;
  ended_at: string;
}
interface DonationRound {
  round: number;
  kind: RoundKind;
  prompt: string;
  wav: string;
  transcript: string | null;
  confidence: number | null;
  heard_at: string | null;
  stream: StreamRecord | null;
}
interface DonationSession {
  session: string;
  consent_version: string;
  started_at: string;
  updated_at: string;
  ended_at: string | null;
  ended_by: "completed" | "stop" | null;
  rounds: DonationRound[];
}
interface SessionRef { date: string; session: string }

type Log = (event: string, detail: Record<string, unknown>) => Promise<void>;

// --- TwiML --------------------------------------------------------------

const escapeXml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const Say = (text: string) => `<Say voice="Polly.Joanna">${escapeXml(text)}</Say>`;
const Goodbye = (text: string) => `${Say(text)}<Hangup/>`;
const GatherSpeech = (prompt: string, action: string, timing: string) =>
  `<Gather input="speech" ${timing} actionOnEmptyResult="true" profanityFilter="false" hints="stop" ` +
  `action="${escapeXml(action)}" method="POST" language="en-US">${Say(prompt)}</Gather>`;

const words = (raw: string): string[] => raw.toLowerCase().replace(/[^a-z\s]/g, " ").split(/\s+/).filter(Boolean);
const roundName = (round: number) => `donation-round-${String(round).padStart(2, "0")}`;
const sessionPrefix = (ref: SessionRef) => `donations/${ref.date}/${ref.session}/`;
const now = () => new Date().toISOString();

// A short stop only: "I had to stop at the store" in free speech is a story, not a request.
const saidStop = (heard: string[]) => heard.includes("stop") && heard.length <= 3;

/** Whether a registration-prompt transcript asks to donate voice samples. */
export const asksToDonate = (heard: string[]) => heard.some((word) => DONATE_WORDS.has(word));

function originHost(env: Env, url: URL): string {
  return env.PUBLIC_ORIGIN ? new URL(env.PUBLIC_ORIGIN).host : url.host;
}

/** The consent prompt: nothing is recorded until the caller says yes. */
export function donationConsent(attempts = 1, prefix = ""): string {
  return GatherSpeech(prefix + CONSENT_SCRIPT, `/voice/donation/consent?attempts=${attempts}`,
    'speechTimeout="auto" timeout="6"');
}

// --- storage ------------------------------------------------------------

function bucket(env: Env): R2Bucket {
  if (!env.RESEARCH_AUDIO) throw new Error("RESEARCH_AUDIO is not bound");
  return env.RESEARCH_AUDIO;
}

async function readSession(env: Env, ref: SessionRef): Promise<{ session: DonationSession; etag: string } | null> {
  const object = await bucket(env).get(sessionPrefix(ref) + "session.json");
  return object ? { session: await object.json<DonationSession>(), etag: object.etag } : null;
}

// The round webhooks and each round's stream both write session.json; a conditional put
// makes every change a compare-and-swap, so neither overwrites the other.
async function updateSession(env: Env, ref: SessionRef, change: (session: DonationSession) => void): Promise<DonationSession | null> {
  for (let attempt = 0; attempt < 8; attempt++) {
    const current = await readSession(env, ref);
    if (!current) return null;
    change(current.session);
    current.session.updated_at = now();
    const written = await bucket(env).put(sessionPrefix(ref) + "session.json", JSON.stringify(current.session, null, 2), {
      onlyIf: { etagMatches: current.etag }, httpMetadata: { contentType: "application/json" },
    });
    if (written) return current.session;
  }
  throw new Error("donation session update kept conflicting");
}

async function createSession(env: Env): Promise<{ ref: SessionRef; session: DonationSession }> {
  const started = now();
  const ref = { date: started.slice(0, 10), session: crypto.randomUUID() };
  const phrases = challengePhrases(PHRASE_ROUNDS);
  const [first, second] = shuffled(FREE_SPEECH_PROMPTS);
  // Phrases, a free-speech break halfway, more phrases, and free speech to finish.
  const half = Math.ceil(phrases.length / 2);
  const prompts: [RoundKind, string][] = [
    ...phrases.slice(0, half).map((text): [RoundKind, string] => ["phrase", text]),
    ["free_speech", first],
    ...phrases.slice(half).map((text): [RoundKind, string] => ["phrase", text]),
    ["free_speech", second],
  ];
  const session: DonationSession = {
    session: ref.session, consent_version: CONSENT_VERSION, started_at: started, updated_at: started,
    ended_at: null, ended_by: null,
    rounds: prompts.map(([kind, prompt], index) => ({
      round: index + 1, kind, prompt, wav: `round-${String(index + 1).padStart(2, "0")}.wav`,
      transcript: null, confidence: null, heard_at: null, stream: null,
    })),
  };
  await bucket(env).put(sessionPrefix(ref) + "session.json", JSON.stringify(session, null, 2), {
    httpMetadata: { contentType: "application/json" },
  });
  return { ref, session };
}

function shuffled<T>(items: readonly T[]): T[] {
  const copy = [...items];
  const random = crypto.getRandomValues(new Uint32Array(copy.length));
  for (let i = copy.length - 1; i > 0; i--) {
    const j = random[i] % (i + 1);
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

// --- stream capability --------------------------------------------------

const textEncoder = new TextEncoder();

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function capabilitySignature(env: Env, content: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", textEncoder.encode(env.DOGWALK_IDENTITY_SECRET),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return base64Url(new Uint8Array(await crypto.subtle.sign("HMAC", key, textEncoder.encode(`donation-stream-v1:${content}`))));
}

// The wss URL carries the capability in its path, since Twilio does not allow a query string
// on a Stream URL. It names one round of one session, the Twilio account whose signed webhook
// asked for it, and an expiry a few minutes away.
async function streamCapability(env: Env, ref: SessionRef, round: number, account: string): Promise<string> {
  const expires = Math.floor(Date.now() / 1000) + STREAM_CAPABILITY_SECONDS;
  const content = `${ref.date}.${ref.session}.${round}.${expires}.${account}`;
  return `${content}.${await capabilitySignature(env, content)}`;
}

async function verifyStreamCapability(
  env: Env, capability: string,
): Promise<{ ref: SessionRef; round: number; account: string } | null> {
  const parts = capability.split(".");
  if (parts.length !== 6) return null;
  const [date, session, roundRaw, expiresRaw, account, signature] = parts;
  const round = Number(roundRaw), expires = Number(expiresRaw);
  if (!SESSION_DATE.test(date) || !SESSION_ID.test(session) || !ACCOUNT_SID.test(account)) return null;
  if (!Number.isInteger(round) || round < 1 || !Number.isInteger(expires) || expires < Date.now() / 1000) return null;
  const expected = await capabilitySignature(env, parts.slice(0, 5).join("."));
  if (expected.length !== signature.length) return null;
  let difference = 0;
  for (let i = 0; i < expected.length; i++) difference |= expected.charCodeAt(i) ^ signature.charCodeAt(i);
  return difference === 0 ? { ref: { date, session }, round, account } : null;
}

// --- rounds -------------------------------------------------------------

async function roundTwiml(env: Env, url: URL, ref: SessionRef, round: DonationRound, account: string): Promise<string> {
  const host = originHost(env, url);
  const capability = await streamCapability(env, ref, round.round, account);
  const action = `/voice/donation/round?date=${ref.date}&session=${ref.session}&round=${round.round}`;
  // Free speech waits through longer pauses than a repeated phrase does.
  const timing = round.kind === "phrase" ? 'speechTimeout="auto" timeout="6"' : 'speechTimeout="3" timeout="8"';
  return `<Start><Stream name="${roundName(round.round)}" url="wss://${host}/voice/donation/stream/${capability}" track="inbound_track">` +
    `<Parameter name="session" value="${ref.session}"/><Parameter name="round" value="${round.round}"/></Stream></Start>` +
    `<Play>https://${host}/voice/donation/tone.wav</Play>` +
    GatherSpeech(round.kind === "phrase" ? round.prompt : `Now talk freely. ${round.prompt}`, action, timing);
}

/** /voice/donation/consent: a spoken yes starts a Donation Session; anything else records nothing. */
export async function handleDonationConsent(env: Env, url: URL, form: FormData, log: Log): Promise<string> {
  const attempts = Number(url.searchParams.get("attempts") ?? "1") || 1;
  const heard = words(String(form.get("SpeechResult") ?? ""));
  const declined = heard.some((word) => NO_WORDS.has(word));
  const agreed = !declined && heard.some((word) => YES_WORDS.has(word));
  if (declined) {
    await log("donation.declined", { attempts });
    return Goodbye("No problem. Nothing was recorded. Goodbye.");
  }
  if (!agreed) {
    if (attempts >= MAX_CONSENT_ATTEMPTS) {
      await log("donation.declined", { attempts, unclear: true });
      return Goodbye("I did not hear a yes, so nothing was recorded. Goodbye.");
    }
    return GatherSpeech("Please say yes to donate voice samples, or no to hang up.",
      `/voice/donation/consent?attempts=${attempts + 1}`, 'speechTimeout="auto" timeout="6"');
  }
  const account = String(form.get("AccountSid") ?? "");
  if (!ACCOUNT_SID.test(account) || !env.RESEARCH_AUDIO) {
    await log("donation.unavailable", {});
    return Goodbye("Voice donation is not available right now. Nothing was recorded. Goodbye.");
  }
  const { ref, session } = await createSession(env);
  // The Call Activity event says a donation happened on this call, never which session it was.
  await log("donation.started", { consent_version: CONSENT_VERSION, rounds: session.rounds.length });
  return Say("Thank you. After each tone, repeat the sentence you hear once, in your normal voice. " +
    "Some rounds ask you to talk freely instead.") +
    await roundTwiml(env, url, ref, session.rounds[0], account);
}

/** /voice/donation/round: keep the transcript, stop that round's stream, and go on or finish. */
export async function handleDonationRound(env: Env, url: URL, form: FormData, log: Log): Promise<string> {
  const ref = { date: url.searchParams.get("date") ?? "", session: url.searchParams.get("session") ?? "" };
  const number = Number(url.searchParams.get("round"));
  if (!SESSION_DATE.test(ref.date) || !SESSION_ID.test(ref.session) || !Number.isInteger(number) || number < 1) {
    return Goodbye("That donation session is not available. Goodbye.");
  }
  const transcript = String(form.get("SpeechResult") ?? "");
  const confidence = Number(form.get("Confidence"));
  const stop = saidStop(words(transcript));
  const session = await updateSession(env, ref, (session) => {
    const round = session.rounds[number - 1];
    if (!round || session.ended_at) return;
    round.transcript = transcript;
    round.confidence = Number.isFinite(confidence) ? confidence : null;
    round.heard_at = now();
    if (stop || number === session.rounds.length) {
      session.ended_at = now();
      session.ended_by = stop ? "stop" : "completed";
    }
  });
  const stopStream = `<Stop><Stream name="${roundName(number)}"/></Stop>`;
  if (!session || number > session.rounds.length) return stopStream + Goodbye("That donation session is not available. Goodbye.");
  if (session.ended_by === "stop") {
    await log("donation.ended", { ended_by: "stop", rounds_heard: number });
    return stopStream + Goodbye("Stopped. What you already recorded is kept. " +
      "To have it deleted, tell Adam roughly when you called. Thank you. Goodbye.");
  }
  if (session.ended_at) {
    await log("donation.ended", { ended_by: session.ended_by, rounds_heard: number });
    return stopStream + Goodbye("That is everything. Thank you for donating your voice. Goodbye.");
  }
  const account = String(form.get("AccountSid") ?? "");
  if (!ACCOUNT_SID.test(account)) return stopStream + Goodbye("Voice donation stopped. Goodbye.");
  return stopStream + await roundTwiml(env, url, ref, session.rounds[number], account);
}

// --- Media Stream -------------------------------------------------------

const ULAW = Int16Array.from({ length: 256 }, (_, encoded) => {
  const value = ~encoded & 0xff;
  const magnitude = ((((value & 0x0f) << 3) + 0x84) << ((value & 0x70) >> 4)) - 0x84;
  return value & 0x80 ? -magnitude : magnitude;
});

function wav(samples: Int16Array): ArrayBuffer {
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
export function donationTone(): Response {
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

/**
 * /voice/donation/stream/<capability>: one round's caller-only Media Stream. The caller has
 * already checked the upgrade's Twilio signature; this checks the capability, that the round
 * exists and has no audio yet, and then the start event (account, session, round, format).
 * Anything malformed closes the socket and keeps nothing.
 */
export async function handleDonationStream(req: Request, env: Env, ctx: ExecutionContext, capability: string): Promise<Response> {
  if (req.headers.get("upgrade")?.toLowerCase() !== "websocket") return new Response("expected websocket", { status: 426 });
  const claim = await verifyStreamCapability(env, capability);
  if (!claim || !env.RESEARCH_AUDIO) return new Response("forbidden", { status: 403 });
  const current = await readSession(env, claim.ref);
  const round = current?.session.rounds[claim.round - 1];
  if (!current || !round || current.session.ended_at) return new Response("not found", { status: 404 });
  if (round.stream) return new Response("round already recorded", { status: 409 });

  const [client, server] = Object.values(new WebSocketPair());
  server.accept();
  const frames: Uint8Array[] = [];
  const limit = MAX_ROUND_SECONDS * SAMPLE_RATE;
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
        const mulaw = new Uint8Array(bytes);
        let offset = 0;
        for (const frame of frames) { mulaw.set(frame, offset); offset += frame.length; }
        const samples = new Int16Array(bytes);
        for (let i = 0; i < bytes; i++) samples[i] = ULAW[mulaw[i]];
        const stream: StreamRecord = {
          complete, frames: count, seconds: bytes / SAMPLE_RATE, inserted_gap_ms: gapMs, truncated,
          started_at: startedAt, ended_at: now(),
        };
        await env.RESEARCH_AUDIO!.put(sessionPrefix(claim.ref) + round.wav, wav(samples), {
          httpMetadata: { contentType: "audio/wav" },
          customMetadata: { round: String(claim.round), kind: round.kind, complete: String(complete) },
        });
        await updateSession(env, claim.ref, (session) => { session.rounds[claim.round - 1].stream = stream; });
      }
    } catch (error) {
      console.error("donation stream save failed", error instanceof Error ? error.message : "unknown");
    } finally {
      try { server.close(1000, "saved"); } catch { /* already closed */ }
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
      if (startedAt || start.accountSid !== claim.account || parameters.session !== claim.ref.session
        || String(parameters.round) !== String(claim.round) || format.encoding !== "audio/x-mulaw"
        || Number(format.sampleRate) !== SAMPLE_RATE || Number(format.channels) !== 1) {
        return discard(1008, "unexpected stream");
      }
      startedAt = now();
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
      // mu-law silence so the WAV keeps real time, and the fill is reported.
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
