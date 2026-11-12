// Challenge Practice (DOMAIN.md, Access Control): a User rehearses Identity Challenges and
// hears a spoken match level after each. It never gates entry, keeps no audio, and never
// changes the Voice Reference. The Voice Agent's start_challenge_practice tool leaves a
// 'practice' handoff, so /voice/after-live redirects here, and the loop ends by redirecting to
// /voice, which reconnects the Voice Agent.
//
// Deterministic TwiML, no model. Rounds have the Identity Challenge's shape and rules
// (challenge_rounds.ts), including scoring that starts when the answer ends; this module holds
// only practice's words and limits. A matched
// phrase's Gather action redirects to /voice/practice/result, which waits for the score and
// speaks a level, never the number. A short "say that again" replays the phrase as a fresh take.
import { answerReceivedCue, isAccountSid, roundTone, Say } from "./caller_stream";
import {
  answerOf, awaitScore, CALL_SID, clearRounds, handleChallengeStream, judge, markScoring, nextTake, openRound,
  readRound, receivedCuePlay, scoreDetail, scoreTiming, skipRound, stopTake, takeTwiml,
} from "./challenge_rounds";
import type { Env } from "./types";

// Scored rounds before practice ends. Rounds that judge nothing (a wrong phrase, silence, too
// little speech) do not count toward it, but every round counts toward MAX_ATTEMPTS, which
// bounds the loop on a line that never yields a judgment.
const MAX_JUDGED_ROUNDS = 10;
const MAX_ATTEMPTS = 20;
// Empty transcripts in a row before practice gives up and returns to the Voice Agent.
const MAX_SILENT_ROUNDS = 2;

const RETURN = '<Redirect method="POST">/voice</Redirect>';

type Log = (event: string, detail: Record<string, unknown>) => Promise<void>;
type Outcome = "strong_match" | "match" | "no_match" | "not_enough_speech" | "wrong_phrase" | "repeat_limit" | "silent" | "unscored";

// Carried in each round's signed webhook URLs: empty transcripts in a row, and rounds judged.
interface Progress { attempts: number; silent: number; judged: number }
const progressOf = (url: URL, attempts: number): Progress => ({
  attempts, silent: Number(url.searchParams.get("silent") ?? "0") || 0, judged: Number(url.searchParams.get("judged") ?? "0") || 0,
});

/** This Voice Call's User and whether they have a Voice Reference, as callUser resolves the User. */
async function practiceUser(env: Env, callSid: string): Promise<{ id: number; identity_hash: string; enrolled: number } | null> {
  return env.DB.prepare(
    `SELECT u.id, u.identity_hash, EXISTS (SELECT 1 FROM voice_references vr WHERE vr.user_id = u.id) AS enrolled
       FROM voice_calls c JOIN users u ON u.id = c.user_id WHERE c.call_sid = ? AND u.reaped_at IS NULL`,
  ).bind(callSid).first();
}

async function endPractice(env: Env, callSid: string, log: Log, reason: string, progress: Progress, spoken: string): Promise<string> {
  await env.DB.prepare("DELETE FROM challenge_rounds WHERE call_sid = ?").bind(callSid).run();
  await log("practice.ended", { reason, attempts: progress.attempts, judged: progress.judged });
  return Say(`${spoken} Back to Dogwalk.`) + RETURN;
}

async function roundTwiml(
  env: Env, ctx: ExecutionContext, url: URL, callSid: string, round: number, account: string, progress: Progress,
): Promise<string> {
  const user = await practiceUser(env, callSid);
  if (!user) throw new Error("practice lost its User");
  const phrase = await openRound(env, callSid, round, "practice", user.id);
  return practiceTake(env, ctx, url, callSid, round, 0, phrase, account, user.identity_hash, progress);
}

const practiceTake = (
  env: Env, ctx: ExecutionContext, url: URL, callSid: string, round: number, take: number, phrase: string, account: string,
  identityHash: string, progress: Progress,
) => takeTwiml(env, ctx, url, {
  callSid, round, take, phrase, account, ritual: "practice", identityHash,
  action: `/voice/practice/heard?round=${round}&take=${take}&silent=${progress.silent}&judged=${progress.judged}`,
});

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
  const user = CALL_SID.test(callSid) ? await practiceUser(env, callSid) : null;
  if (!user?.enrolled) {
    await log("practice.unavailable", { reason: "no_voice_reference" });
    return Say("Challenge practice needs an enrolled voice, and yours is not enrolled yet. Back to Dogwalk.") + RETURN;
  }
  if (!env.SPEAKER || !isAccountSid(account)) {
    await log("practice.unavailable", { reason: env.SPEAKER ? "no_account" : "no_verifier" });
    return Say("Challenge practice is not available right now. Back to Dogwalk.") + RETURN;
  }
  // Rows outlive a call that hung up mid-round; any practice start clears those too.
  await clearRounds(env, callSid);
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
  const stop = stopTake("practice", round, take);
  const row = await readRound(env, callSid, round);
  if (!row || row.ritual !== "practice" || row.state !== "listening" || row.take !== take || !isAccountSid(account)) {
    return stop + await endPractice(env, callSid, log, "lost_round", progress, "Challenge practice stopped.");
  }
  const answer = answerOf(row.phrase, String(form.get("SpeechResult") ?? ""), take);
  switch (answer.kind) {
    case "stop":
      return stop + await endPractice(env, callSid, log, "stop", progress, "Practice over.");
    case "silent":
      await log("practice.round", { round, outcome: "silent" satisfies Outcome });
      if (progress.silent + 1 >= MAX_SILENT_ROUNDS) {
        return stop + await endPractice(env, callSid, log, "silence", progress, "I didn't hear anything.");
      }
      await skipRound(env, callSid, round);
      return stop + await nextRound(env, ctx, url, callSid, account, log, round, { ...progress, silent: progress.silent + 1 }, "I didn't hear the sentence.");
    // "Say that again": the same phrase in a fresh take, counted as neither a round nor a miss.
    case "repeat": {
      await nextTake(env, callSid, round, take);
      await log("practice.repeat", { round, take: take + 1 });
      const user = await practiceUser(env, callSid);
      return stop + Say("Again.") + await practiceTake(env, ctx, url, callSid, round, take + 1, row.phrase, account, user?.identity_hash ?? "", progress);
    }
    // Past the replay limit the request is still not an answer: no received cue, a fresh phrase.
    case "repeat_limit":
      await skipRound(env, callSid, round);
      await log("practice.round", { round, outcome: "repeat_limit" satisfies Outcome });
      return stop + await nextRound(env, ctx, url, callSid, account, log, round, { ...progress, silent: 0 }, "No more repeats of that one.");
    // A misheard prompt or a misrecognized word is a wrong phrase, never a voice failure.
    case "wrong_phrase":
      await skipRound(env, callSid, round);
      await log("practice.round", { round, outcome: "wrong_phrase" satisfies Outcome });
      return stop + receivedCuePlay(env, url, "practice") +
        await nextRound(env, ctx, url, callSid, account, log, round, { ...progress, silent: 0 }, "That wasn't the sentence.");
    case "matched":
      await markScoring(env, callSid, round);
      // The cue plays while the verifier scores, so it costs the caller no extra wait.
      return stop + receivedCuePlay(env, url, "practice") +
        `<Redirect method="POST">/voice/practice/result?round=${round}&amp;judged=${progress.judged}</Redirect>`;
  }
}

/** The Stop for the stream a /voice/practice/heard request's round and take started, so an error path can stop it. */
export function practiceStreamStop(url: URL): string {
  const round = Number(url.searchParams.get("round")), take = Number(url.searchParams.get("take") ?? "0");
  return Number.isInteger(round) && round >= 1 && round <= MAX_ATTEMPTS && Number.isInteger(take) && take >= 0
    ? stopTake("practice", round, take) : "";
}

/** /voice/practice/result: wait for the round's score, speak its level, and go on. */
export async function handlePracticeResult(env: Env, ctx: ExecutionContext, url: URL, form: FormData, log: Log): Promise<string> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  const round = Number(url.searchParams.get("round"));
  if (!CALL_SID.test(callSid) || !Number.isInteger(round) || round < 1 || round > MAX_ATTEMPTS || !isAccountSid(account)) return RETURN;
  const progress = { ...progressOf(url, round), silent: 0 };
  const { row, wait_ms } = await awaitScore(env, callSid, round);
  if (!row || row.ritual !== "practice" || (row.state !== "scoring" && row.state !== "scored")) {
    return endPractice(env, callSid, log, "lost_round", progress, "Challenge practice stopped.");
  }
  await env.DB.prepare("DELETE FROM challenge_rounds WHERE call_sid = ? AND round = ?").bind(callSid, round).run();
  const verdict = judge(row);
  if (verdict.outcome === "unscored" && !verdict.error) {
    await log("practice.round", { round, outcome: "unscored" satisfies Outcome, ...scoreTiming(row, wait_ms) });
    return nextRound(env, ctx, url, callSid, account, log, round, progress, "I couldn't score that one.");
  }
  if (verdict.outcome === "unscored") {
    await log("practice.round", { round, outcome: "unscored" satisfies Outcome, error: verdict.error, ...scoreTiming(row, wait_ms) });
    return endPractice(env, callSid, log, "verifier_error", progress, "Challenge practice is not available right now.");
  }
  // A score too thin to judge is kept for calibration even though no level is spoken.
  await log("practice.round", { round, outcome: verdict.outcome, ...scoreDetail(row), ...scoreTiming(row, wait_ms) });
  if (verdict.outcome === "not_enough_speech" || verdict.outcome === "collected") {
    return nextRound(env, ctx, url, callSid, account, log, round, progress, "Not enough to judge.");
  }
  return nextRound(env, ctx, url, callSid, account, log, round, { ...progress, judged: progress.judged + 1 }, verdict.spoken);
}

/** The round cue, the same tone Voice Donation plays. */
export const practiceTone = roundTone;
/** The answer-received cue, which the Identity Check shares. */
export const practiceReceived = answerReceivedCue;

/** /voice/practice/stream/<capability>: one take's caller-only Media Stream (challenge_rounds.ts). */
export const handlePracticeStream = (req: Request, env: Env, ctx: ExecutionContext, capability: string) =>
  handleChallengeStream(req, env, ctx, capability, "practice");
