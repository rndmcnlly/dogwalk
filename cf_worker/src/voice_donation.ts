// Voice Donation (DOMAIN.md, Voice Research): any caller may donate engineering voice samples
// after a spoken consent, without a Dogwalk account. Operational Dogwalk audio stays transient;
// a Donation Session keeps raw audio in the private RESEARCH_AUDIO bucket under a fresh random
// id, with no phone number, CallSid, User, or Handle beside it. Timestamps are the only way
// back to a call, and they are coarse on purpose: Adam annotates sessions from memory.
//
// The flow is deterministic TwiML and needs no keypad. Each round starts a caller-only Media
// Stream to a signed wss URL (caller_stream.ts), plays a tone, says the prompt inside a speech
// Gather (whose transcript is kept), and the Gather's action stops the stream before the next
// round. This Worker receives each stream itself and writes one WAV when it stops or closes.
// Never <Record>: this Twilio account served recording media without authentication.
import {
  acceptCallerStream, GatherSpeech, isAccountSid, originHost, roundTone, saidStop, Say, speechWords as words, StartStream,
  StopStream, streamCapability, verifyStreamCapability, wav,
} from "./caller_stream";
import type { StreamedAudio } from "./caller_stream";
import { challengePhrases, contentWords } from "./challenge_phrases";
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
// A speech Gather listens for at most a minute; anything past this is a stuck stream.
const MAX_ROUND_SECONDS = 90;
const STREAM_PURPOSE = "donation";

const SESSION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SESSION_DATE = /^\d{4}-\d{2}-\d{2}$/;

const YES_WORDS = new Set(["yes", "yeah", "yep", "yup", "sure", "okay", "ok", "agree", "consent", "absolutely"]);
const NO_WORDS = new Set(["no", "nope", "not", "don", "dont", "stop", "decline", "cancel", "never"]);
export const DONATE_WORDS = new Set(["donate", "donation", "donations", "donating"]);

type RoundKind = "phrase" | "free_speech";
// What session.json keeps about a round's stream: everything but the audio.
type StreamRecord = Omit<StreamedAudio, "samples">;
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

const Goodbye = (text: string) => `${Say(text)}<Hangup/>`;

const roundName = (round: number) => `donation-round-${String(round).padStart(2, "0")}`;
const sessionPrefix = (ref: SessionRef) => `donations/${ref.date}/${ref.session}/`;
const now = () => new Date().toISOString();

/** Whether a registration-prompt transcript asks to donate voice samples. */
export const asksToDonate = (heard: string[]) => heard.some((word) => DONATE_WORDS.has(word));

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

// --- rounds -------------------------------------------------------------

async function roundTwiml(env: Env, url: URL, ref: SessionRef, round: DonationRound, account: string): Promise<string> {
  const host = originHost(env, url);
  // One round of one session: a capability for round 3 cannot stream into round 4.
  const capability = await streamCapability(env, STREAM_PURPOSE, [ref.date, ref.session, String(round.round)], account);
  const action = `/voice/donation/round?date=${ref.date}&session=${ref.session}&round=${round.round}`;
  // Free speech waits through longer pauses than a repeated phrase does.
  const timing = round.kind === "phrase" ? 'speechTimeout="auto" timeout="6"' : 'speechTimeout="3" timeout="8"';
  return StartStream(roundName(round.round), `wss://${host}/voice/donation/stream/${capability}`,
    { session: ref.session, round: String(round.round) }) +
    `<Play>https://${host}/voice/donation/tone.wav</Play>` +
    GatherSpeech(round.kind === "phrase" ? round.prompt : `Now talk freely. ${round.prompt}`, action, timing,
      round.kind === "phrase" ? contentWords(round.prompt) : []);
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
  if (!isAccountSid(account) || !env.RESEARCH_AUDIO) {
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
  const stopStream = StopStream(roundName(number));
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
  if (!isAccountSid(account)) return stopStream + Goodbye("Voice donation stopped. Goodbye.");
  return stopStream + await roundTwiml(env, url, ref, session.rounds[number], account);
}

// --- Media Stream -------------------------------------------------------

/** The round cue, shared with Challenge Practice. */
export const donationTone = roundTone;

/**
 * /voice/donation/stream/<capability>: one round's caller-only Media Stream. The caller has
 * already checked the upgrade's Twilio signature; this checks the capability and that the
 * round exists and has no audio yet, and acceptCallerStream checks the rest.
 */
export async function handleDonationStream(req: Request, env: Env, ctx: ExecutionContext, capability: string): Promise<Response> {
  if (req.headers.get("upgrade")?.toLowerCase() !== "websocket") return new Response("expected websocket", { status: 426 });
  const claim = await verifyStreamCapability(env, STREAM_PURPOSE, capability, 3);
  const [date, session, roundRaw] = claim?.fields ?? [];
  const number = Number(roundRaw);
  if (!claim || !SESSION_DATE.test(date) || !SESSION_ID.test(session) || !Number.isInteger(number) || number < 1
    || !env.RESEARCH_AUDIO) {
    return new Response("forbidden", { status: 403 });
  }
  const ref = { date, session };
  const current = await readSession(env, ref);
  const round = current?.session.rounds[number - 1];
  if (!current || !round || current.session.ended_at) return new Response("not found", { status: 404 });
  if (round.stream) return new Response("round already recorded", { status: 409 });

  return acceptCallerStream(ctx, {
    account: claim.account, parameters: { session, round: String(number) }, maxSeconds: MAX_ROUND_SECONDS,
  }, async ({ samples, ...stream }) => {
    await env.RESEARCH_AUDIO!.put(sessionPrefix(ref) + round.wav, wav(samples), {
      httpMetadata: { contentType: "audio/wav" },
      customMetadata: { round: String(number), kind: round.kind, complete: String(stream.complete) },
    });
    await updateSession(env, ref, (session) => { session.rounds[number - 1].stream = stream; });
  });
}
