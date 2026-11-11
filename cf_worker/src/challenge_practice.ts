// Challenge Practice (DOMAIN.md, Access Control): a User rehearses Identity Challenges and
// hears a spoken match level after each. It never gates entry, keeps no audio, and never
// changes the Voice Reference. The Voice Agent's start_challenge_practice tool leaves a
// 'practice' handoff, so /voice/after-live redirects here, and the loop ends by redirecting to
// /voice, which reconnects the Voice Agent.
//
// Deterministic TwiML, no model. Each round inserts a practice_rounds row with a fresh
// phrase, starts a caller-only Media Stream (caller_stream.ts), plays a tone, and says the
// phrase inside a speech Gather hinted with the phrase's words. The Gather's action checks the
// transcript first: every content word, in order, or the round is a wrong phrase and is not
// scored. A short "say that again" replays the phrase as a fresh take with its own stream. A matched phrase marks the row 'scoring' and stops the stream, so the
// stream's stop event (which can only follow that response) scores the audio in memory with
// SPEAKER and writes back only the cosine and speech seconds. The action redirects to
// /voice/practice/result, which waits for that score and speaks a level, never the number.
import {
  acceptCallerStream, GatherSpeech, isAccountSid, originHost, roundTone, SAMPLE_RATE, saidStop, Say, speechWords, StartStream,
  StopStream, streamCapability, verifyStreamCapability,
} from "./caller_stream";
import { askedToRepeat, challengePhrase, contentWords, phraseMatches } from "./challenge_phrases";
import type { Env, SpeakerReference } from "./types";
import { syntheticAudio } from "./voice_verify";

// Configuration (DOMAIN.md, Identity Check: thresholds are configuration, not domain rules).
/** At or above this cosine, a round is a strong match. */
export const STRONG_MATCH_COSINE = 0.7;
/** At or above this cosine, a round matches; below it, it does not. The Identity Check's starting threshold. */
export const MATCH_COSINE = 0.5;
/** Less speech than this in a round is not enough to judge. */
export const MIN_SPEECH_SECONDS = 2.0;
/**
 * The most audio the verifier accepts (speaker_worker MAX_SECONDS). A longer round sends its
 * last this-many seconds: the verifier still picks the most speech-active 3 s window inside
 * what it gets, and the Gather closes on the caller's end of speech, so the tail always holds
 * the answer while the head holds the tone and the spoken prompt.
 */
export const MAX_VERIFY_SECONDS = 30;
const MAX_ROUNDS = 10;
// Empty transcripts in a row before practice gives up and returns to the Voice Agent.
const MAX_SILENT_ROUNDS = 2;
// Replays of one phrase on request ("say that again") before practice moves to a fresh one.
const MAX_REPEATS = 3;
// A speech Gather listens for at most a minute; anything past this is a stuck stream.
const MAX_ROUND_SECONDS = 90;
// How long the result step waits for the verifier: Twilio gives a webhook 15 seconds, and a
// fresh verifier instance takes about 4.
const RESULT_WAIT_MS = 10_000;
const RESULT_POLL_MS = 200;
const STREAM_PURPOSE = "practice";
const CALL_SID = /^[A-Za-z0-9_]{1,64}$/;

const RETURN = '<Redirect method="POST">/voice</Redirect>';

type Log = (event: string, detail: Record<string, unknown>) => Promise<void>;
type Outcome = "strong_match" | "match" | "no_match" | "not_enough_speech" | "wrong_phrase" | "silent" | "unscored";

interface RoundRow {
  phrase: string;
  take: number;
  state: "listening" | "scoring" | "scored" | "skipped";
  cosine: number | null;
  speech_seconds: number | null;
  error: string | null;
}

// Each replay of a phrase is a new take with its own stream, so a stopped take's late audio
// can never be scored for a later one.
const roundName = (round: number, take: number) =>
  `practice-round-${String(round).padStart(2, "0")}${take ? `-${take}` : ""}`;

/** The spoken level for a scored round; the cosine itself is never spoken. */
export function matchLevel(cosine: number): { outcome: Outcome; spoken: string } {
  if (cosine >= STRONG_MATCH_COSINE) return { outcome: "strong_match", spoken: "Strong voice match." };
  if (cosine >= MATCH_COSINE) return { outcome: "match", spoken: "Voice matched." };
  return { outcome: "no_match", spoken: "Voice not matched." };
}

/** The Voice Reference of this Voice Call's User, as callUser resolves the User. */
async function callReference(env: Env, callSid: string): Promise<SpeakerReference | null> {
  const row = await env.DB.prepare(
    `SELECT vr.embedding_space, vr.embedding FROM voice_calls c JOIN users u ON u.id = c.user_id
       JOIN voice_references vr ON vr.user_id = u.id
      WHERE c.call_sid = ? AND u.reaped_at IS NULL`,
  ).bind(callSid).first<{ embedding_space: string; embedding: string }>();
  return row ? { embedding: JSON.parse(row.embedding) as number[], embeddingSpace: row.embedding_space } : null;
}

async function readRound(env: Env, callSid: string, round: number): Promise<RoundRow | null> {
  return env.DB.prepare(
    "SELECT phrase, take, state, cosine, speech_seconds, error FROM practice_rounds WHERE call_sid = ? AND round = ?",
  ).bind(callSid, round).first<RoundRow>();
}

async function endPractice(env: Env, callSid: string, log: Log, reason: string, rounds: number, spoken: string): Promise<string> {
  await env.DB.prepare("DELETE FROM practice_rounds WHERE call_sid = ?").bind(callSid).run();
  await log("practice.ended", { reason, rounds });
  return Say(`${spoken} Back to Dogwalk.`) + RETURN;
}

async function roundTwiml(env: Env, url: URL, callSid: string, round: number, account: string, silent: number): Promise<string> {
  const phrase = challengePhrase();
  await env.DB.prepare(
    `INSERT INTO practice_rounds (call_sid, round, phrase, state, created_at) VALUES (?, ?, ?, 'listening', unixepoch())
     ON CONFLICT(call_sid, round) DO UPDATE SET phrase = excluded.phrase, take = 0, state = 'listening', streamed = 0,
       cosine = NULL, speech_seconds = NULL, error = NULL, created_at = excluded.created_at`,
  ).bind(callSid, round, phrase).run();
  return takeTwiml(env, url, callSid, round, 0, phrase, account, silent);
}

// One take of a round: a stream, the tone, and the phrase inside a Gather hinted with its words.
async function takeTwiml(
  env: Env, url: URL, callSid: string, round: number, take: number, phrase: string, account: string, silent: number,
): Promise<string> {
  const host = originHost(env, url);
  const capability = await streamCapability(env, STREAM_PURPOSE, [callSid, String(round), String(take)], account);
  return StartStream(roundName(round, take), `wss://${host}/voice/practice/stream/${capability}`, { round: String(round), take: String(take) }) +
    `<Play>https://${host}/voice/practice/tone.wav</Play>` +
    GatherSpeech(phrase, `/voice/practice/heard?round=${round}&take=${take}&silent=${silent}`, 'speechTimeout="auto" timeout="6"',
      contentWords(phrase));
}

async function nextRound(
  env: Env, url: URL, callSid: string, account: string, log: Log, round: number, silent: number, spoken: string,
): Promise<string> {
  if (round >= MAX_ROUNDS) return endPractice(env, callSid, log, "round_limit", round, `${spoken} That was the last round.`);
  return Say(`${spoken} Here's another.`) + await roundTwiml(env, url, callSid, round + 1, account, silent);
}

/** /voice/practice: check the Voice Reference and the verifier, warm it, and start round one. */
export async function startPractice(env: Env, ctx: ExecutionContext, url: URL, form: FormData, log: Log): Promise<string> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  if (!CALL_SID.test(callSid) || !(await callReference(env, callSid))) {
    await log("practice.unavailable", { reason: "no_voice_reference" });
    return Say("Challenge practice needs an enrolled voice, and yours is not enrolled yet. Back to Dogwalk.") + RETURN;
  }
  if (!env.SPEAKER || !isAccountSid(account)) {
    await log("practice.unavailable", { reason: env.SPEAKER ? "no_account" : "no_verifier" });
    return Say("Challenge practice is not available right now. Back to Dogwalk.") + RETURN;
  }
  // A fresh verifier instance takes about 4 seconds; warm it while the caller hears the intro.
  const speaker = env.SPEAKER;
  ctx.waitUntil(speaker.embed(syntheticAudio()).then(() => undefined, () => undefined));
  // Rows outlive a call that hung up mid-round; any practice start clears those too.
  await env.DB.prepare("DELETE FROM practice_rounds WHERE call_sid = ? OR created_at < unixepoch() - 3600").bind(callSid).run();
  await log("practice.started", { max_rounds: MAX_ROUNDS });
  return Say("Challenge practice. After each tone, repeat the sentence once in your normal voice, " +
    "and you will hear how well your voice matched. Nothing is recorded. Say stop to finish.") +
    await roundTwiml(env, url, callSid, 1, account, 0);
}

/** /voice/practice/heard: the round's transcript decides whether its audio is scored at all. */
export async function handlePracticeHeard(env: Env, url: URL, form: FormData, log: Log): Promise<string> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  const round = Number(url.searchParams.get("round"));
  const take = Number(url.searchParams.get("take") ?? "0") || 0;
  const silent = Number(url.searchParams.get("silent") ?? "0") || 0;
  if (!CALL_SID.test(callSid) || !Number.isInteger(round) || round < 1 || round > MAX_ROUNDS) return RETURN;
  const stop = StopStream(roundName(round, take));
  const row = await readRound(env, callSid, round);
  if (!row || row.state !== "listening" || row.take !== take || !isAccountSid(account)) {
    return stop + await endPractice(env, callSid, log, "lost_round", round - 1, "Challenge practice stopped.");
  }
  const transcript = String(form.get("SpeechResult") ?? "");
  const heard = speechWords(transcript);
  if (saidStop(heard)) return stop + await endPractice(env, callSid, log, "stop", round - 1, "Practice over.");
  if (heard.length === 0) {
    await log("practice.round", { round, outcome: "silent" satisfies Outcome });
    if (silent + 1 >= MAX_SILENT_ROUNDS) {
      return stop + await endPractice(env, callSid, log, "silence", round, "I didn't hear anything.");
    }
    await env.DB.prepare("UPDATE practice_rounds SET state = 'skipped' WHERE call_sid = ? AND round = ?").bind(callSid, round).run();
    return stop + await nextRound(env, url, callSid, account, log, round, silent + 1, "I didn't hear the sentence.");
  }
  // "Say that again": the same phrase in a fresh take, counted as neither a round nor a miss.
  if (askedToRepeat(heard) && take < MAX_REPEATS) {
    await env.DB.prepare("UPDATE practice_rounds SET take = ?, streamed = 0 WHERE call_sid = ? AND round = ? AND take = ?")
      .bind(take + 1, callSid, round, take).run();
    await log("practice.repeat", { round, take: take + 1 });
    return stop + Say("Again.") + await takeTwiml(env, url, callSid, round, take + 1, row.phrase, account, silent);
  }
  // Every content word, in order: a misheard prompt or a misrecognized word is a wrong phrase,
  // never a voice failure.
  if (!phraseMatches(row.phrase, transcript)) {
    await env.DB.prepare("UPDATE practice_rounds SET state = 'skipped' WHERE call_sid = ? AND round = ?").bind(callSid, round).run();
    await log("practice.round", { round, outcome: "wrong_phrase" satisfies Outcome });
    return stop + await nextRound(env, url, callSid, account, log, round, 0, "That wasn't the sentence.");
  }
  // Committed before the response that stops the stream, so the stream's end sees it.
  await env.DB.prepare("UPDATE practice_rounds SET state = 'scoring' WHERE call_sid = ? AND round = ? AND state = 'listening'")
    .bind(callSid, round).run();
  return stop + `<Redirect method="POST">/voice/practice/result?round=${round}</Redirect>`;
}

/** /voice/practice/result: wait for the round's score, speak its level, and go on. */
export async function handlePracticeResult(env: Env, url: URL, form: FormData, log: Log): Promise<string> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  const round = Number(url.searchParams.get("round"));
  if (!CALL_SID.test(callSid) || !Number.isInteger(round) || round < 1 || round > MAX_ROUNDS || !isAccountSid(account)) return RETURN;
  const deadline = Date.now() + RESULT_WAIT_MS;
  let row = await readRound(env, callSid, round);
  while (row?.state === "scoring" && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, RESULT_POLL_MS));
    row = await readRound(env, callSid, round);
  }
  if (!row || (row.state !== "scoring" && row.state !== "scored")) {
    return endPractice(env, callSid, log, "lost_round", round - 1, "Challenge practice stopped.");
  }
  await env.DB.prepare("DELETE FROM practice_rounds WHERE call_sid = ? AND round = ?").bind(callSid, round).run();
  if (row.state === "scoring") {
    await log("practice.round", { round, outcome: "unscored" satisfies Outcome });
    return nextRound(env, url, callSid, account, log, round, 0, "I couldn't score that one.");
  }
  if (row.error && row.error !== "insufficient-audio") {
    await log("practice.round", { round, outcome: "unscored" satisfies Outcome, error: row.error });
    return endPractice(env, callSid, log, "verifier_error", round, "Challenge practice is not available right now.");
  }
  if (row.error || row.cosine === null || (row.speech_seconds ?? 0) < MIN_SPEECH_SECONDS) {
    // The score is kept for calibration even though no level is spoken.
    await log("practice.round", { round, outcome: "not_enough_speech" satisfies Outcome, cosine: row.cosine,
      speech_seconds: row.speech_seconds, ...(row.error ? { error: row.error } : {}) });
    return nextRound(env, url, callSid, account, log, round, 0, "Not enough to judge.");
  }
  const level = matchLevel(row.cosine);
  await log("practice.round", { round, outcome: level.outcome, cosine: row.cosine, speech_seconds: row.speech_seconds });
  return nextRound(env, url, callSid, account, log, round, 0, level.spoken);
}

/** The round cue, the same tone Voice Donation plays. */
export const practiceTone = roundTone;

/**
 * /voice/practice/stream/<capability>: one round's caller-only Media Stream. The caller has
 * already checked the upgrade's Twilio signature. One stream per round; its audio stays in
 * memory and is scored only if the round's transcript matched its phrase.
 */
export async function handlePracticeStream(req: Request, env: Env, ctx: ExecutionContext, capability: string): Promise<Response> {
  if (req.headers.get("upgrade")?.toLowerCase() !== "websocket") return new Response("expected websocket", { status: 426 });
  const claim = await verifyStreamCapability(env, STREAM_PURPOSE, capability, 3);
  const [callSid, roundRaw, takeRaw] = claim?.fields ?? [];
  const round = Number(roundRaw), take = Number(takeRaw);
  if (!claim || !CALL_SID.test(callSid) || !Number.isInteger(round) || round < 1 || !Number.isInteger(take) || take < 0) {
    return new Response("forbidden", { status: 403 });
  }
  const claimed = await env.DB.prepare(
    "UPDATE practice_rounds SET streamed = 1 WHERE call_sid = ? AND round = ? AND take = ? AND state = 'listening' AND streamed = 0",
  ).bind(callSid, round, take).run();
  if (!claimed.meta.changes) return new Response("round not listening", { status: 409 });

  return acceptCallerStream(ctx, { account: claim.account, parameters: { round: String(round), take: String(take) }, maxSeconds: MAX_ROUND_SECONDS },
    async ({ samples }) => {
      const row = await readRound(env, callSid, round);
      if (row?.state !== "scoring" || row.take !== take) return;
      const reference = await callReference(env, callSid);
      let result: { cosine: number | null; speech: number | null; error: string | null };
      try {
        if (!env.SPEAKER || !reference) throw new Error("unavailable: no verifier or Voice Reference");
        const tail = samples.subarray(Math.max(0, samples.length - MAX_VERIFY_SECONDS * SAMPLE_RATE));
        const audio = tail.buffer.slice(tail.byteOffset, tail.byteOffset + tail.byteLength) as ArrayBuffer;
        const verified = await env.SPEAKER.verify({ audio, format: "pcm_s16le", sampleRate: SAMPLE_RATE, reference });
        result = { cosine: verified.cosineSimilarity, speech: verified.speechSeconds, error: null };
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        result = { cosine: null, speech: null, error: /^([a-z-]+): /.exec(message)?.[1] ?? "unavailable" };
      }
      await env.DB.prepare(
        `UPDATE practice_rounds SET state = 'scored', cosine = ?, speech_seconds = ?, error = ?
          WHERE call_sid = ? AND round = ? AND take = ? AND state = 'scoring'`,
      ).bind(result.cosine, result.speech, result.error, callSid, round, take).run();
    });
}
