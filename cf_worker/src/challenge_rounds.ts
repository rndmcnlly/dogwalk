// Challenge rounds (DOMAIN.md, Identity Challenge): the one spoken shape that Challenge Practice
// (challenge_practice.ts), the Identity Check, and enrollment (identity_check.ts) share. A
// round is a challenge_rounds row with a fresh phrase, a caller-only Media Stream
// (caller_stream.ts), a tone, and the phrase inside a speech Gather hinted with the phrase's
// words. Scoring is cheap and judges nothing by itself, so it does not wait for the transcript:
// the stream's endpointer starts a score on the audio so far whenever the caller's answer ends,
// and the row keeps only the latest (spec_). The Gather's action reads the transcript first
// (answerOf): anything but the phrase discards that score unused, and a matched phrase marks
// the row 'scoring' and stops the stream, so the stream's stop event (which can only follow
// that response) uses the speculative score if the caller has not talked since, or scores the
// whole stream in memory. Scores go through the User's VoiceSession (userSpeaker) and write
// back only the cosine and speech seconds, or for enrollment the answer's embedding. A
// ritual's result step waits for that (awaitScore) and judges it (judge). Every answer the
// Gather captures (scored or a wrong phrase, never silence or a repeat request) first hears the
// received cue, so the wait for its verdict is never silent. Rituals differ only in their
// words, their limits, and what follows a round.
import {
  acceptCallerStream, GatherSpeech, originHost, SAMPLE_RATE, saidStop, speechWords, StartStream, StopStream,
  streamCapability, verifyStreamCapability,
} from "./caller_stream";
import { askedToRepeat, challengePhrase, contentWords, phraseMatches } from "./challenge_phrases";
import type { Env, SpeakerReference } from "./types";
import { userSpeaker, warmSpeaker } from "./voice_verify";

// Configuration (DOMAIN.md, Identity Check: thresholds are configuration, not domain rules).
/** At or above this cosine, a round is a strong match. */
export const STRONG_MATCH_COSINE = 0.7;
/** At or above this cosine, a round matches; below it, it does not. The Identity Check's starting threshold. */
export const MATCH_COSINE = 0.5;
/**
 * Less speech than this in a round, as the verifier measures it, is not enough to judge. Cutting
 * enrolled phone answers short (2026-11-11) cost a genuine cosine 0.07 on average at 1.5 s and
 * 0.21 at 1.3 s; at 2.0 the floor rejected strong matches (0.75 and 0.77 at 1.9 and 1.64 s).
 */
export const MIN_SPEECH_SECONDS = 1.5;
/**
 * The most audio the verifier accepts (speaker_worker MAX_SECONDS). A longer round sends its
 * last this-many seconds: the verifier still picks the most speech-active 3 s window inside
 * what it gets, and the Gather closes on the caller's end of speech, so the tail always holds
 * the answer while the head holds the tone and the spoken prompt.
 */
export const MAX_VERIFY_SECONDS = 30;
// Replays of one phrase on request ("say that again") before the ritual moves to a fresh one.
export const MAX_REPEATS = 3;
// Speculative scores started per take (one per end of the answer the stream hears). A phrase
// answer ends once; this bounds verifier calls from a caller who keeps pausing.
const MAX_SPECULATIVE_SCORES = 4;
// A speech Gather listens for at most a minute; anything past this is a stuck stream.
const MAX_ROUND_SECONDS = 90;
// How long a result step waits for the verifier: Twilio gives a webhook 15 seconds, and a
// fresh verifier instance takes about 4.
const RESULT_WAIT_MS = 10_000;
const RESULT_POLL_MS = 100;
// The answer ends at a one-second pause (Twilio's shortest numeric speechTimeout): "auto" waited
// up to four seconds after a caller's last word, and no repetition held a pause over 0.8 s.
// Callers start about 0.7 s after the prompt, so five seconds of silence means no answer.
const ROUND_GATHER_TIMING = 'speechTimeout="1" timeout="5"';
export const CALL_SID = /^[A-Za-z0-9_]{1,64}$/;

export type Ritual = "practice" | "identity" | "enrollment";
// Enrollment is part of the Identity Check's stage, so it shares its webhooks, cues, and streams.
type Stage = "practice" | "identity";
const stageOf = (ritual: Ritual): Stage => (ritual === "practice" ? "practice" : "identity");

export interface RoundRow {
  ritual: Ritual;
  user_id: number;
  phrase: string;
  take: number;
  state: "listening" | "scoring" | "scored" | "skipped";
  cosine: number | null;
  speech_seconds: number | null;
  error: string | null;
  cold: number | null;
  embedded: number;
  heard_ms: number | null;
  spec_started_ms: number | null;
  speculative: number;
}

// A take's speculative score, discarded unused whenever the take ends without a matched phrase.
const DISCARD_SPECULATIVE = "spec_seq = NULL, spec_started_ms = NULL, spec_cosine = NULL, spec_speech_seconds = NULL, " +
  "spec_error = NULL, spec_cold = NULL, spec_embedding_space = NULL, spec_embedding = NULL";

export const roundName = (ritual: Ritual, round: number, take: number) =>
  `${stageOf(ritual)}-round-${String(round).padStart(2, "0")}${take ? `-${take}` : ""}`;

/** The spoken level for a scored round; the cosine itself is never spoken. */
export function matchLevel(cosine: number): { outcome: "strong_match" | "match" | "no_match"; spoken: string } {
  if (cosine >= STRONG_MATCH_COSINE) return { outcome: "strong_match", spoken: "Strong voice match." };
  if (cosine >= MATCH_COSINE) return { outcome: "match", spoken: "Voice matched." };
  return { outcome: "no_match", spoken: "Voice not matched." };
}

// --- rows ---------------------------------------------------------------

export async function readRound(env: Env, callSid: string, round: number): Promise<RoundRow | null> {
  return env.DB.prepare(
    `SELECT ritual, user_id, phrase, take, state, cosine, speech_seconds, error, cold, embedding IS NOT NULL AS embedded,
            heard_ms, spec_started_ms, speculative
       FROM challenge_rounds WHERE call_sid = ? AND round = ?`,
  ).bind(callSid, round).first<RoundRow>();
}

/** Start a round with a fresh phrase, replacing any earlier row of that number. */
export async function openRound(env: Env, callSid: string, round: number, ritual: Ritual, userId: number): Promise<string> {
  const phrase = challengePhrase();
  await env.DB.prepare(
    `INSERT INTO challenge_rounds (call_sid, round, ritual, user_id, phrase, state, created_at)
       VALUES (?, ?, ?, ?, ?, 'listening', unixepoch())
     ON CONFLICT(call_sid, round) DO UPDATE SET ritual = excluded.ritual, user_id = excluded.user_id, phrase = excluded.phrase,
       take = 0, state = 'listening', streamed = 0, cosine = NULL, speech_seconds = NULL, error = NULL, cold = NULL,
       embedding_space = NULL, embedding = NULL, heard_ms = NULL, speculative = 0, ${DISCARD_SPECULATIVE},
       created_at = excluded.created_at`,
  ).bind(callSid, round, ritual, userId, phrase).run();
  return phrase;
}

/** Drop every round of the call, and any row a call that hung up mid-round left behind. */
export async function clearRounds(env: Env, callSid: string): Promise<void> {
  await env.DB.prepare("DELETE FROM challenge_rounds WHERE call_sid = ? OR created_at < unixepoch() - 3600").bind(callSid).run();
}

/**
 * A round that ends without a matched phrase. Its speculative score, if one started, is
 * discarded unread: once 'skipped', the stream can neither store nor use one.
 */
export async function skipRound(env: Env, callSid: string, round: number): Promise<void> {
  await env.DB.prepare(`UPDATE challenge_rounds SET state = 'skipped', ${DISCARD_SPECULATIVE} WHERE call_sid = ? AND round = ?`)
    .bind(callSid, round).run();
}

// --- TwiML --------------------------------------------------------------

export const roundTonePlay = (env: Env, url: URL, ritual: Ritual) =>
  `<Play>https://${originHost(env, url)}/voice/${stageOf(ritual)}/tone.wav</Play>`;
export const receivedCuePlay = (env: Env, url: URL, ritual: Ritual) =>
  `<Play>https://${originHost(env, url)}/voice/${stageOf(ritual)}/received.wav</Play>`;

/**
 * One take of a round: a stream, the tone, and the phrase inside a Gather hinted with its words.
 * It also warms the verifier where this User's challenges are scored, some seconds from now: in
 * a playtest (2026-11-11) a level came about 5 s after the answer, where a warm verifier takes
 * under 1.
 */
export async function takeTwiml(env: Env, ctx: ExecutionContext, url: URL, take: {
  callSid: string; round: number; take: number; phrase: string; account: string; ritual: Ritual; identityHash: string;
  action: string;
}): Promise<string> {
  warmSpeaker(env, ctx, take.identityHash);
  const stage = stageOf(take.ritual);
  const capability = await streamCapability(env, stage, [take.callSid, String(take.round), String(take.take)], take.account);
  return StartStream(roundName(take.ritual, take.round, take.take), `wss://${originHost(env, url)}/voice/${stage}/stream/${capability}`,
    { round: String(take.round), take: String(take.take) }) +
    roundTonePlay(env, url, take.ritual) +
    GatherSpeech(take.phrase, take.action, ROUND_GATHER_TIMING, contentWords(take.phrase));
}

/** The Stop for a take's stream, so every response to its Gather (error paths too) ends it. */
export const stopTake = (ritual: Ritual, round: number, take: number) => StopStream(roundName(ritual, round, take));

// --- answers ------------------------------------------------------------

/**
 * What one Gather heard, in the order every ritual applies: a short stop, nothing, a short
 * request to hear the sentence again (past MAX_REPEATS replays, repeat_limit), the phrase
 * (every content word, in order), or anything else, which a ritual may classify further and
 * otherwise treats as a wrong phrase: never a voice failure.
 */
export function answerOf(phrase: string, transcript: string, take: number):
  { kind: "stop" | "silent" | "repeat" | "repeat_limit" | "matched" | "wrong_phrase"; heard: string[] } {
  const heard = speechWords(transcript);
  if (saidStop(heard)) return { kind: "stop", heard };
  if (heard.length === 0) return { kind: "silent", heard };
  if (askedToRepeat(heard)) return { kind: take < MAX_REPEATS ? "repeat" : "repeat_limit", heard };
  return { kind: phraseMatches(phrase, transcript) ? "matched" : "wrong_phrase", heard };
}

/** A replay of the round's phrase as a fresh take, so the stopped take's late audio is never scored. */
export async function nextTake(env: Env, callSid: string, round: number, take: number): Promise<void> {
  await env.DB.prepare(`UPDATE challenge_rounds SET take = ?, streamed = 0, ${DISCARD_SPECULATIVE} WHERE call_sid = ? AND round = ? AND take = ?`)
    .bind(take + 1, callSid, round, take).run();
}

/**
 * Mark a matched answer for scoring. Committed before the response that stops the stream, so
 * the stream's end sees it. The time measures how much head start a speculative score had.
 */
export async function markScoring(env: Env, callSid: string, round: number): Promise<void> {
  await env.DB.prepare("UPDATE challenge_rounds SET state = 'scoring', heard_ms = ? WHERE call_sid = ? AND round = ? AND state = 'listening'")
    .bind(Date.now(), callSid, round).run();
}

// --- results ------------------------------------------------------------

/** Wait for a matched round's score; wait_ms is how long the caller waited in silence for it. */
export async function awaitScore(env: Env, callSid: string, round: number): Promise<{ row: RoundRow | null; wait_ms: number }> {
  const started = Date.now(), deadline = started + RESULT_WAIT_MS;
  let row = await readRound(env, callSid, round);
  while (row?.state === "scoring" && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, RESULT_POLL_MS));
    row = await readRound(env, callSid, round);
  }
  // Workers clocks advance across I/O, so this is real waiting time.
  return { row, wait_ms: Date.now() - started };
}

export type Judgment =
  | { outcome: "unscored"; error?: string }
  | { outcome: "not_enough_speech"; error?: string }
  | { outcome: "collected" }
  | { outcome: "strong_match" | "match" | "no_match"; spoken: string };

/**
 * A scored row's verdict: still scoring past the wait or a verifier failure is unscored; too
 * little speech to judge (the verifier's refusal or MIN_SPEECH_SECONDS) is neither a pass nor a
 * failure; otherwise a level, or for enrollment an answer collected.
 */
export function judge(row: RoundRow): Judgment {
  if (row.state !== "scored") return { outcome: "unscored" };
  if (row.error && row.error !== "insufficient-audio") return { outcome: "unscored", error: row.error };
  const scored = row.ritual === "enrollment" ? row.embedded === 1 : row.cosine !== null;
  if (row.error || !scored || (row.speech_seconds ?? 0) < MIN_SPEECH_SECONDS) {
    return { outcome: "not_enough_speech", ...(row.error ? { error: row.error } : {}) };
  }
  return row.ritual === "enrollment" ? { outcome: "collected" } : matchLevel(row.cosine!);
}

/**
 * Whether a matched round's score came from the end of the answer rather than the stream's
 * stop, how long before the transcript it started (negative when the endpointer was slower
 * than the Gather), and how long the caller then waited for it.
 */
export const scoreTiming = (row: RoundRow, wait_ms: number) =>
  row.speculative && row.heard_ms !== null && row.spec_started_ms !== null
    ? { speculative: true, head_start_ms: row.heard_ms - row.spec_started_ms, wait_ms } : { speculative: false, wait_ms };

/** What a round's Call Activity row may say about its score: never audio, a transcript, or an embedding. */
export const scoreDetail = (row: RoundRow) => ({
  ...(row.ritual === "enrollment" ? {} : { cosine: row.cosine }), speech_seconds: row.speech_seconds,
  ...(row.error ? { error: row.error } : {}), ...(row.cold === null ? {} : { cold: row.cold === 1 }),
});

// --- the stream ---------------------------------------------------------

/** The User a round is scored against or enrolls: the VoiceSession that hosts their verifier, and their Voice Reference. */
async function roundSubject(env: Env, userId: number): Promise<{ identityHash: string; reference: SpeakerReference | null } | null> {
  const row = await env.DB.prepare(
    `SELECT u.identity_hash, vr.embedding_space, vr.embedding FROM users u LEFT JOIN voice_references vr ON vr.user_id = u.id
      WHERE u.id = ? AND u.reaped_at IS NULL`,
  ).bind(userId).first<{ identity_hash: string; embedding_space: string | null; embedding: string | null }>();
  if (!row) return null;
  const reference = row.embedding && row.embedding_space
    ? { embedding: JSON.parse(row.embedding) as number[], embeddingSpace: row.embedding_space } : null;
  return { identityHash: row.identity_hash, reference };
}

/**
 * /voice/<practice|identity>/stream/<capability>: one take's caller-only Media Stream. The caller
 * has already checked the upgrade's Twilio signature. Its audio stays in memory. Scoring (for
 * enrollment, embedding) starts speculatively at each end of the answer the endpointer hears,
 * and the take's row keeps only the latest result. At the stream's stop, a matched phrase
 * ('scoring') uses that score when the caller has not talked since it started, and otherwise
 * scores the whole stream; any other state leaves the score unused.
 */
export async function handleChallengeStream(
  req: Request, env: Env, ctx: ExecutionContext, capability: string, stage: Stage,
): Promise<Response> {
  if (req.headers.get("upgrade")?.toLowerCase() !== "websocket") return new Response("expected websocket", { status: 426 });
  const claim = await verifyStreamCapability(env, stage, capability, 3);
  const [callSid, roundRaw, takeRaw] = claim?.fields ?? [];
  const round = Number(roundRaw), take = Number(takeRaw);
  if (!claim || !CALL_SID.test(callSid) || !Number.isInteger(round) || round < 1 || !Number.isInteger(take) || take < 0) {
    return new Response("forbidden", { status: 403 });
  }
  const rituals = stage === "practice" ? "('practice')" : "('identity', 'enrollment')";
  const claimed = await env.DB.prepare(
    `UPDATE challenge_rounds SET streamed = 1
      WHERE call_sid = ? AND round = ? AND take = ? AND state = 'listening' AND streamed = 0 AND ritual IN ${rituals}`,
  ).bind(callSid, round, take).run();
  if (!claimed.meta.changes) return new Response("round not listening", { status: 409 });

  type Result = { cosine: number | null; speech: number | null; error: string | null; cold: number | null;
    space: string | null; embedding: string | null };
  // The User and ritual are fixed for the round, so they are read once per take.
  let subject: ReturnType<typeof roundSubject> | null = null;
  let ritual: Ritual | null = null;
  const score = async (samples: Int16Array): Promise<Result> => {
    const result: Result = { cosine: null, speech: null, error: null, cold: null, space: null, embedding: null };
    try {
      if (!ritual || !subject) {
        const row = await readRound(env, callSid, round);
        if (!row) throw new Error("unavailable: round gone");
        ritual = row.ritual;
        subject = roundSubject(env, row.user_id);
      }
      const who = await subject;
      const speaker = who && userSpeaker(env, who.identityHash);
      if (!speaker) throw new Error("unavailable: no verifier or User");
      const tail = samples.subarray(Math.max(0, samples.length - MAX_VERIFY_SECONDS * SAMPLE_RATE));
      const audio = tail.buffer.slice(tail.byteOffset, tail.byteOffset + tail.byteLength) as ArrayBuffer;
      if (ritual === "enrollment") {
        const embedded = await speaker.embed({ audio, format: "pcm_s16le", sampleRate: SAMPLE_RATE });
        Object.assign(result, { speech: embedded.speechSeconds, space: embedded.embeddingSpace,
          embedding: JSON.stringify(embedded.embedding), cold: embedded.cold === undefined ? null : Number(embedded.cold) });
      } else {
        if (!who.reference) throw new Error("unavailable: no Voice Reference");
        const verified = await speaker.verify({ audio, format: "pcm_s16le", sampleRate: SAMPLE_RATE, reference: who.reference });
        Object.assign(result, { cosine: verified.cosineSimilarity, speech: verified.speechSeconds,
          cold: verified.cold === undefined ? null : Number(verified.cold) });
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      result.error = /^([a-z-]+): /.exec(message)?.[1] ?? "unavailable";
    }
    return result;
  };

  // The latest speculative score (its number in this take, when it started, and its write to the
  // row), and whether the caller has talked since, which makes it stale.
  type Attempt = { seq: number; started: number; stored: Promise<void> };
  let latest: Attempt | null = null;
  let attempts = 0;
  let talkedSince = false;
  const answerEnded = (samples: Int16Array) => {
    talkedSince = false;
    // A caller who keeps stopping and starting gets no more verifier calls: the stop scores
    // the whole stream instead.
    if (++attempts > MAX_SPECULATIVE_SCORES) {
      latest = null;
      return;
    }
    const attempt: Attempt = { seq: attempts, started: Date.now(), stored: Promise.resolve() };
    latest = attempt;
    attempt.stored = score(samples).then(async (result) => {
      if (latest !== attempt) return;
      // Only while the take awaits its transcript or its matched phrase awaits a score (a
      // skipped or replayed take's score is dropped here, never written), and never over a
      // later attempt's score.
      await env.DB.prepare(
        `UPDATE challenge_rounds SET spec_seq = ?, spec_started_ms = ?, spec_cosine = ?, spec_speech_seconds = ?, spec_error = ?,
           spec_cold = ?, spec_embedding_space = ?, spec_embedding = ?
          WHERE call_sid = ? AND round = ? AND take = ? AND state IN ('listening', 'scoring') AND (spec_seq IS NULL OR spec_seq < ?)`,
      ).bind(attempt.seq, attempt.started, result.cosine, result.speech, result.error, result.cold, result.space, result.embedding,
        callSid, round, take, attempt.seq).run();
    }).catch((error) => console.error("speculative score failed", error instanceof Error ? error.message : "unknown"));
    ctx.waitUntil(attempt.stored);
  };

  return acceptCallerStream(ctx, { account: claim.account, parameters: { round: String(round), take: String(take) }, maxSeconds: MAX_ROUND_SECONDS },
    async ({ samples }) => {
      const row = await readRound(env, callSid, round);
      if (row?.state !== "scoring" || row.take !== take) return;
      if (latest && !talkedSince) {
        await latest.stored;
        const used = await env.DB.prepare(
          `UPDATE challenge_rounds SET state = 'scored', cosine = spec_cosine, speech_seconds = spec_speech_seconds, error = spec_error,
             cold = spec_cold, embedding_space = spec_embedding_space, embedding = spec_embedding, speculative = 1
            WHERE call_sid = ? AND round = ? AND take = ? AND state = 'scoring' AND spec_seq = ?`,
        ).bind(callSid, round, take, latest.seq).run();
        if (used.meta.changes) return;
      }
      const result = await score(samples);
      await env.DB.prepare(
        `UPDATE challenge_rounds SET state = 'scored', cosine = ?, speech_seconds = ?, error = ?, cold = ?, embedding_space = ?, embedding = ?
          WHERE call_sid = ? AND round = ? AND take = ? AND state = 'scoring'`,
      ).bind(result.cosine, result.speech, result.error, result.cold, result.space, result.embedding, callSid, round, take).run();
    },
    { end: answerEnded, resume: () => { talkedSince = true; } });
}
