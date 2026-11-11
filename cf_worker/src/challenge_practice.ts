// Challenge Practice (DOMAIN.md, Access Control): a User rehearses Identity Challenges and
// hears a spoken match level after each. It never gates entry, keeps no audio, and never
// changes the Voice Reference. The Voice Agent's start_challenge_practice tool leaves a
// 'practice' handoff, so /voice/after-live redirects here, and the loop ends by redirecting to
// /voice, which reconnects the Voice Agent.
//
// Deterministic TwiML, no model. Each round inserts a practice_rounds row with a fresh
// phrase, starts a caller-only Media Stream (caller_stream.ts), plays a tone, and says the
// phrase inside a speech Gather hinted with the phrase's words. Every answer the Gather captures
// (scored or a wrong phrase, never silence or a repeat request) first hears the received cue,
// so the wait for its verdict is never silent. The Gather's action checks the
// transcript first: every content word, in order, or the round is a wrong phrase and any score
// is discarded unused. A short "say that again" replays the phrase as a fresh take with its own
// stream. Scoring does not wait for the transcript: the stream's endpointer starts SPEAKER on
// the audio so far whenever the caller's answer ends, and the row keeps only the latest result.
// A matched phrase marks the row 'scoring' and stops the stream, so the stream's stop event
// (which can only follow that response) uses that score if the caller has not talked since, or
// scores the whole stream in memory, writing back only the cosine and speech seconds. The action
// redirects to /voice/practice/result, which waits for the score and speaks a level, never the number.
import {
  acceptCallerStream, answerReceivedCue, GatherSpeech, isAccountSid, originHost, roundTone, SAMPLE_RATE, saidStop, Say, speechWords, StartStream,
  StopStream, streamCapability, verifyStreamCapability,
} from "./caller_stream";
import { askedToRepeat, challengePhrase, contentWords, phraseMatches } from "./challenge_phrases";
import type { Env, SpeakerReference } from "./types";
import { warmSpeaker } from "./voice_verify";

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
// Scored rounds before practice ends. Rounds that judge nothing (a wrong phrase, silence, too
// little speech) do not count toward it, but every round counts toward MAX_ATTEMPTS, which
// bounds the loop on a line that never yields a judgment.
const MAX_JUDGED_ROUNDS = 10;
const MAX_ATTEMPTS = 20;
// Empty transcripts in a row before practice gives up and returns to the Voice Agent.
const MAX_SILENT_ROUNDS = 2;
// Replays of one phrase on request ("say that again") before practice moves to a fresh one.
const MAX_REPEATS = 3;
// Speculative scores started per take (one per end of the answer the stream hears). A phrase
// answer ends once; this bounds verifier calls from a caller who keeps pausing.
const MAX_SPECULATIVE_SCORES = 4;
// A speech Gather listens for at most a minute; anything past this is a stuck stream.
const MAX_ROUND_SECONDS = 90;
// How long the result step waits for the verifier: Twilio gives a webhook 15 seconds, and a
// fresh verifier instance takes about 4.
const RESULT_WAIT_MS = 10_000;
const RESULT_POLL_MS = 100;
// The answer ends at a one-second pause (Twilio's shortest numeric speechTimeout): "auto" waited
// up to four seconds after a caller's last word, and no repetition held a pause over 0.8 s.
// Callers start about 0.7 s after the prompt, so five seconds of silence means no answer.
const ROUND_GATHER_TIMING = 'speechTimeout="1" timeout="5"';
const STREAM_PURPOSE = "practice";
const CALL_SID = /^[A-Za-z0-9_]{1,64}$/;

const RETURN = '<Redirect method="POST">/voice</Redirect>';

type Log = (event: string, detail: Record<string, unknown>) => Promise<void>;
type Outcome = "strong_match" | "match" | "no_match" | "not_enough_speech" | "wrong_phrase" | "repeat_limit" | "silent" | "unscored";

interface RoundRow {
  phrase: string;
  take: number;
  state: "listening" | "scoring" | "scored" | "skipped";
  cosine: number | null;
  speech_seconds: number | null;
  error: string | null;
  heard_ms: number | null;
  spec_started_ms: number | null;
  speculative: number;
}

// A take's speculative score, discarded unused whenever the take ends without a matched phrase.
const DISCARD_SPECULATIVE = "spec_seq = NULL, spec_started_ms = NULL, spec_cosine = NULL, spec_speech_seconds = NULL, spec_error = NULL";

// Each replay of a phrase is a new take with its own stream, so a stopped take's late audio
// can never be scored for a later one.
// Carried in each round's signed webhook URLs: empty transcripts in a row, and rounds judged.
interface Progress { attempts: number; silent: number; judged: number }
const progressOf = (url: URL, attempts: number): Progress => ({
  attempts, silent: Number(url.searchParams.get("silent") ?? "0") || 0, judged: Number(url.searchParams.get("judged") ?? "0") || 0,
});

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
    `SELECT phrase, take, state, cosine, speech_seconds, error, heard_ms, spec_started_ms, speculative
       FROM practice_rounds WHERE call_sid = ? AND round = ?`,
  ).bind(callSid, round).first<RoundRow>();
}

async function endPractice(env: Env, callSid: string, log: Log, reason: string, progress: Progress, spoken: string): Promise<string> {
  await env.DB.prepare("DELETE FROM practice_rounds WHERE call_sid = ?").bind(callSid).run();
  await log("practice.ended", { reason, attempts: progress.attempts, judged: progress.judged });
  return Say(`${spoken} Back to Dogwalk.`) + RETURN;
}

async function roundTwiml(
  env: Env, ctx: ExecutionContext, url: URL, callSid: string, round: number, account: string, progress: Progress,
): Promise<string> {
  const phrase = challengePhrase();
  await env.DB.prepare(
    `INSERT INTO practice_rounds (call_sid, round, phrase, state, created_at) VALUES (?, ?, ?, 'listening', unixepoch())
     ON CONFLICT(call_sid, round) DO UPDATE SET phrase = excluded.phrase, take = 0, state = 'listening', streamed = 0,
       cosine = NULL, speech_seconds = NULL, error = NULL, heard_ms = NULL, speculative = 0, ${DISCARD_SPECULATIVE},
       created_at = excluded.created_at`,
  ).bind(callSid, round, phrase).run();
  return takeTwiml(env, ctx, url, callSid, round, 0, phrase, account, progress);
}

// One take of a round: a stream, the tone, and the phrase inside a Gather hinted with its words.
// It also warms the verifier, which scores this take some seconds from now: in a playtest
// (2026-11-11) a level came about 5 s after the answer, where a warm verifier takes under 1.
async function takeTwiml(
  env: Env, ctx: ExecutionContext, url: URL, callSid: string, round: number, take: number, phrase: string, account: string,
  progress: Progress,
): Promise<string> {
  warmSpeaker(env, ctx);
  const host = originHost(env, url);
  const capability = await streamCapability(env, STREAM_PURPOSE, [callSid, String(round), String(take)], account);
  return StartStream(roundName(round, take), `wss://${host}/voice/practice/stream/${capability}`, { round: String(round), take: String(take) }) +
    `<Play>https://${host}/voice/practice/tone.wav</Play>` +
    GatherSpeech(phrase, `/voice/practice/heard?round=${round}&take=${take}&silent=${progress.silent}&judged=${progress.judged}`, ROUND_GATHER_TIMING,
      contentWords(phrase));
}

async function nextRound(
  env: Env, ctx: ExecutionContext, url: URL, callSid: string, account: string, log: Log, round: number, progress: Progress,
  spoken: string,
): Promise<string> {
  if (progress.judged >= MAX_JUDGED_ROUNDS || round >= MAX_ATTEMPTS) {
    return endPractice(env, callSid, log, "round_limit", progress, `${spoken} That was the last round.`);
  }
  return Say(`${spoken} Here's another.`) + await roundTwiml(env, ctx, url, callSid, round + 1, account, { ...progress, attempts: round + 1 });
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
  // Rows outlive a call that hung up mid-round; any practice start clears those too.
  await env.DB.prepare("DELETE FROM practice_rounds WHERE call_sid = ? OR created_at < unixepoch() - 3600").bind(callSid).run();
  await log("practice.started", { max_judged_rounds: MAX_JUDGED_ROUNDS, max_attempts: MAX_ATTEMPTS });
  return Say("Challenge practice. After each tone, repeat the sentence once in your normal voice, " +
    "and you will hear how well your voice matched. Nothing is recorded. Say stop to finish.") +
    await roundTwiml(env, ctx, url, callSid, 1, account, { attempts: 1, silent: 0, judged: 0 });
}

/** /voice/practice/heard: the round's transcript decides whether its audio is scored at all. */
export async function handlePracticeHeard(env: Env, ctx: ExecutionContext, url: URL, form: FormData, log: Log): Promise<string> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  const round = Number(url.searchParams.get("round"));
  const take = Number(url.searchParams.get("take") ?? "0") || 0;
  if (!CALL_SID.test(callSid) || !Number.isInteger(round) || round < 1 || round > MAX_ATTEMPTS) return RETURN;
  const progress = progressOf(url, round);
  const stop = StopStream(roundName(round, take));
  const row = await readRound(env, callSid, round);
  if (!row || row.state !== "listening" || row.take !== take || !isAccountSid(account)) {
    return stop + await endPractice(env, callSid, log, "lost_round", progress, "Challenge practice stopped.");
  }
  const transcript = String(form.get("SpeechResult") ?? "");
  const heard = speechWords(transcript);
  if (saidStop(heard)) return stop + await endPractice(env, callSid, log, "stop", progress, "Practice over.");
  if (heard.length === 0) {
    await log("practice.round", { round, outcome: "silent" satisfies Outcome });
    if (progress.silent + 1 >= MAX_SILENT_ROUNDS) {
      return stop + await endPractice(env, callSid, log, "silence", progress, "I didn't hear anything.");
    }
    await env.DB.prepare(`UPDATE practice_rounds SET state = 'skipped', ${DISCARD_SPECULATIVE} WHERE call_sid = ? AND round = ?`)
      .bind(callSid, round).run();
    return stop + await nextRound(env, ctx, url, callSid, account, log, round, { ...progress, silent: progress.silent + 1 }, "I didn't hear the sentence.");
  }
  // "Say that again": the same phrase in a fresh take, counted as neither a round nor a miss.
  if (askedToRepeat(heard)) {
    if (take < MAX_REPEATS) {
      await env.DB.prepare(`UPDATE practice_rounds SET take = ?, streamed = 0, ${DISCARD_SPECULATIVE} WHERE call_sid = ? AND round = ? AND take = ?`)
        .bind(take + 1, callSid, round, take).run();
      await log("practice.repeat", { round, take: take + 1 });
      return stop + Say("Again.") + await takeTwiml(env, ctx, url, callSid, round, take + 1, row.phrase, account, progress);
    }
    // Past the replay limit the request is still not an answer: no received cue, a fresh phrase,
    // and any speculative score of the request discarded unused.
    await env.DB.prepare(`UPDATE practice_rounds SET state = 'skipped', ${DISCARD_SPECULATIVE} WHERE call_sid = ? AND round = ?`)
      .bind(callSid, round).run();
    await log("practice.round", { round, outcome: "repeat_limit" satisfies Outcome });
    return stop + await nextRound(env, ctx, url, callSid, account, log, round, { ...progress, silent: 0 }, "No more repeats of that one.");
  }
  // Every content word, in order: a misheard prompt or a misrecognized word is a wrong phrase,
  // never a voice failure. Its speculative score, if one started, is discarded unread: once
  // 'skipped', the stream can neither store nor use one.
  const received = `<Play>https://${originHost(env, url)}/voice/practice/received.wav</Play>`;
  if (!phraseMatches(row.phrase, transcript)) {
    await env.DB.prepare(`UPDATE practice_rounds SET state = 'skipped', ${DISCARD_SPECULATIVE} WHERE call_sid = ? AND round = ?`)
      .bind(callSid, round).run();
    await log("practice.round", { round, outcome: "wrong_phrase" satisfies Outcome });
    return stop + received + await nextRound(env, ctx, url, callSid, account, log, round, { ...progress, silent: 0 }, "That wasn't the sentence.");
  }
  // Committed before the response that stops the stream, so the stream's end sees it. The time
  // measures how much head start a speculative score had over this webhook.
  await env.DB.prepare("UPDATE practice_rounds SET state = 'scoring', heard_ms = ? WHERE call_sid = ? AND round = ? AND state = 'listening'")
    .bind(Date.now(), callSid, round).run();
  // The cue plays while the verifier scores, so it costs the caller no extra wait.
  return stop + received + `<Redirect method="POST">/voice/practice/result?round=${round}&amp;judged=${progress.judged}</Redirect>`;
}

/** The Stop for the stream a /voice/practice/heard request's round and take started, so an error path can stop it. */
export function practiceStreamStop(url: URL): string {
  const round = Number(url.searchParams.get("round")), take = Number(url.searchParams.get("take") ?? "0");
  return Number.isInteger(round) && round >= 1 && round <= MAX_ATTEMPTS && Number.isInteger(take) && take >= 0
    ? StopStream(roundName(round, take)) : "";
}

/** /voice/practice/result: wait for the round's score, speak its level, and go on. */
export async function handlePracticeResult(env: Env, ctx: ExecutionContext, url: URL, form: FormData, log: Log): Promise<string> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  const round = Number(url.searchParams.get("round"));
  if (!CALL_SID.test(callSid) || !Number.isInteger(round) || round < 1 || round > MAX_ATTEMPTS || !isAccountSid(account)) return RETURN;
  const progress = { ...progressOf(url, round), silent: 0 };
  const started = Date.now(), deadline = started + RESULT_WAIT_MS;
  let row = await readRound(env, callSid, round);
  while (row?.state === "scoring" && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, RESULT_POLL_MS));
    row = await readRound(env, callSid, round);
  }
  // How long the caller waited in silence for the score, for tuning (Workers clocks advance across I/O).
  const wait_ms = Date.now() - started;
  if (!row || (row.state !== "scoring" && row.state !== "scored")) {
    return endPractice(env, callSid, log, "lost_round", progress, "Challenge practice stopped.");
  }
  await env.DB.prepare("DELETE FROM practice_rounds WHERE call_sid = ? AND round = ?").bind(callSid, round).run();
  // Whether the score came from the end of the answer rather than the stream's stop, and how
  // long before /heard it started (negative when the endpointer was slower than the Gather).
  const timing = row.speculative && row.heard_ms !== null && row.spec_started_ms !== null
    ? { speculative: true, head_start_ms: row.heard_ms - row.spec_started_ms, wait_ms } : { speculative: false, wait_ms };
  if (row.state === "scoring") {
    await log("practice.round", { round, outcome: "unscored" satisfies Outcome, ...timing });
    return nextRound(env, ctx, url, callSid, account, log, round, progress, "I couldn't score that one.");
  }
  if (row.error && row.error !== "insufficient-audio") {
    await log("practice.round", { round, outcome: "unscored" satisfies Outcome, error: row.error, ...timing });
    return endPractice(env, callSid, log, "verifier_error", progress, "Challenge practice is not available right now.");
  }
  if (row.error || row.cosine === null || (row.speech_seconds ?? 0) < MIN_SPEECH_SECONDS) {
    // The score is kept for calibration even though no level is spoken.
    await log("practice.round", { round, outcome: "not_enough_speech" satisfies Outcome, cosine: row.cosine,
      speech_seconds: row.speech_seconds, ...(row.error ? { error: row.error } : {}), ...timing });
    return nextRound(env, ctx, url, callSid, account, log, round, progress, "Not enough to judge.");
  }
  const level = matchLevel(row.cosine);
  await log("practice.round", { round, outcome: level.outcome, cosine: row.cosine, speech_seconds: row.speech_seconds, ...timing });
  return nextRound(env, ctx, url, callSid, account, log, round, { ...progress, judged: progress.judged + 1 }, level.spoken);
}

/** The round cue, the same tone Voice Donation plays. */
export const practiceTone = roundTone;
/** The answer-received cue, which the Identity Check will share. */
export const practiceReceived = answerReceivedCue;

/**
 * /voice/practice/stream/<capability>: one take's caller-only Media Stream. The caller has
 * already checked the upgrade's Twilio signature. Its audio stays in memory. Scoring is cheap
 * and judges nothing by itself, so it starts speculatively at each end of the answer the
 * endpointer hears, before the transcript arrives, and the take's row keeps only the latest
 * result. At the stream's stop, a matched phrase ('scoring') uses that score when the caller
 * has not talked since it started, and otherwise scores the whole stream; any other state
 * leaves the score unused.
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

  let reference: Promise<SpeakerReference | null> | null = null;
  const score = async (samples: Int16Array): Promise<{ cosine: number | null; speech: number | null; error: string | null }> => {
    try {
      reference ??= callReference(env, callSid);
      const voice = await reference;
      if (!env.SPEAKER || !voice) throw new Error("unavailable: no verifier or Voice Reference");
      const tail = samples.subarray(Math.max(0, samples.length - MAX_VERIFY_SECONDS * SAMPLE_RATE));
      const audio = tail.buffer.slice(tail.byteOffset, tail.byteOffset + tail.byteLength) as ArrayBuffer;
      const verified = await env.SPEAKER.verify({ audio, format: "pcm_s16le", sampleRate: SAMPLE_RATE, reference: voice });
      return { cosine: verified.cosineSimilarity, speech: verified.speechSeconds, error: null };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return { cosine: null, speech: null, error: /^([a-z-]+): /.exec(message)?.[1] ?? "unavailable" };
    }
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
        `UPDATE practice_rounds SET spec_seq = ?, spec_started_ms = ?, spec_cosine = ?, spec_speech_seconds = ?, spec_error = ?
          WHERE call_sid = ? AND round = ? AND take = ? AND state IN ('listening', 'scoring') AND (spec_seq IS NULL OR spec_seq < ?)`,
      ).bind(attempt.seq, attempt.started, result.cosine, result.speech, result.error, callSid, round, take, attempt.seq).run();
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
          `UPDATE practice_rounds SET state = 'scored', cosine = spec_cosine, speech_seconds = spec_speech_seconds, error = spec_error,
             speculative = 1 WHERE call_sid = ? AND round = ? AND take = ? AND state = 'scoring' AND spec_seq = ?`,
        ).bind(callSid, round, take, latest.seq).run();
        if (used.meta.changes) return;
      }
      const result = await score(samples);
      await env.DB.prepare(
        `UPDATE practice_rounds SET state = 'scored', cosine = ?, speech_seconds = ?, error = ?
          WHERE call_sid = ? AND round = ? AND take = ? AND state = 'scoring'`,
      ).bind(result.cosine, result.speech, result.error, callSid, round, take).run();
    },
    { end: answerEnded, resume: () => { talkedSince = true; } });
}
