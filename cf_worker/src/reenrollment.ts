// Re-enrollment (DOMAIN.md, Access Control): the User-started ritual that replaces their Voice
// Reference. The Voice Agent's start_reenrollment tool leaves a 'reenrollment' handoff, so
// /voice/after-live redirects here, and every path ends by redirecting to /voice, which
// reconnects the Voice Agent. Up to three phases, in deterministic TwiML with no model:
//
// - check: Identity Challenges against the current Voice Reference, as many as Roaming takes,
//   and every one must match. This check is enforced even while the Identity Check is in shadow
//   mode: Re-enrollment replaces the credential every later check is scored against, so it must
//   never be weaker than what it protects. A shadow Home check admits anyone holding the Home
//   phone, and an enforcing Roaming check asks three matches from any phone, so this asks both:
//   an admitted call (Home, or admitted by Roaming) and three matches. One "Voice not matched."
//   ends it.
// - enroll: first-call enrollment's answers (identity_check.ts), embedded and collected in the
//   new setting. When the new reference still matches the old one (the same threshold as a
//   challenge), it replaces the old one outright: earlier enrollment does not blend in.
// - confirm: a Voice Transfer. When the new answers do not match the old reference, they are a
//   different voice, which may be intended (a caretaker handing an account back to a recovered
//   patient). The caller hears what continuing gives the new voice, and the current voice
//   must pass one more challenge against the old reference, so it was still there after hearing
//   the consequence. Then the new reference replaces the old one.
//
// A Voice Reference is replaced at most once a day (REPLACE_INTERVAL_SECONDS), so it cannot be
// walked by repeated replacements in one call, and the User's Openings tell them for a few days
// after it changes (voice_session.ts, voiceProfileNotice), from a reenrollment.replaced event
// committed with the replacement.
//
// Rounds have the Identity Challenge's shape and rules (challenge_rounds.ts) and share the
// Identity Check's streams and cues (/voice/identity/stream, tone, received); only the Gather
// actions are this module's. Webhook URLs carry the phase, progress, and the version of the
// Voice Reference the check began against; Twilio signs them.
import { isAccountSid, Say } from "./caller_stream";
import {
  answerOf, awaitScore, CALL_SID, clearRounds, judge, markScoring, MATCH_COSINE, nextTake, noteReturn, openRound, readRound, receivedCuePlay,
  type Ritual, scoreDetail, scoreTiming, skipRound, stopTake, takeTwiml,
} from "./challenge_rounds";
import { collectedAnswers, ENROLL_ANSWERS, enrollmentMark, meanEmbedding, MIN_ENROLL_ANSWERS, ROAMING_CHALLENGES } from "./identity_check";
import type { Env } from "./types";

// Configuration, not domain rules. Rounds, judged or not, before a phase gives up: wrong phrases
// and thin speech never count as failures, so these bound a line that never yields a judgment.
const MAX_ROUNDS: Record<Phase, number> = { check: 8, enroll: 10, confirm: 4 };
/** The least time between replacements of a Voice Reference (enrolled_at, which first enrollment also sets). */
export const REPLACE_INTERVAL_SECONDS = 86400;
// Empty transcripts in a row before Re-enrollment gives up.
const MAX_SILENT_ROUNDS = 2;

type Phase = "check" | "enroll" | "confirm";
const ritualOf = (phase: Phase): Ritual => (phase === "enroll" ? "enrollment" : "identity");
type Log = (event: string, detail: Record<string, unknown>) => Promise<void>;

const RETURN = '<Redirect method="POST">/voice</Redirect>';
const UNCHANGED = "Your voice profile is unchanged.";

interface Subject {
  id: number; identity_hash: string; embedding_space: string | null; embedding: string | null; recent: number; version: string;
}

/**
 * This Voice Call's User and their Voice Reference, as callUser resolves the User. version is a
 * digest of the reference, so the ritual can tell that it is still the one its check began
 * against: a replacement by another call meanwhile ends this one unchanged.
 */
async function callSubject(env: Env, callSid: string): Promise<Subject | null> {
  const row = await env.DB.prepare(
    `SELECT u.id, u.identity_hash, vr.embedding_space, vr.embedding, COALESCE(vr.enrolled_at > unixepoch() - ?, 0) AS recent
       FROM voice_calls c JOIN users u ON u.id = c.user_id LEFT JOIN voice_references vr ON vr.user_id = u.id
      WHERE c.call_sid = ? AND u.reaped_at IS NULL`,
  ).bind(REPLACE_INTERVAL_SECONDS, callSid).first<Omit<Subject, "version">>();
  if (!row) return null;
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${row.embedding_space}\n${row.embedding}`));
  const version = [...new Uint8Array(digest).slice(0, 8)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return { ...row, version };
}

// start is the round before the phase began, so enrollment gets its own MAX_ROUNDS budget while
// round numbers stay increasing, and a stopped stream's late score never lands on a new round.
interface Progress { phase: Phase; silent: number; judged: number; start: number; ref: string }
function progressOf(url: URL): Progress | null {
  const phase = url.searchParams.get("phase");
  const ref = url.searchParams.get("ref") ?? "";
  if ((phase !== "check" && phase !== "enroll" && phase !== "confirm") || !/^[0-9a-f]{16}$/.test(ref)) return null;
  const count = (name: string) => Number(url.searchParams.get(name) ?? "0") || 0;
  return { phase, silent: count("silent"), judged: count("judged"), start: count("start"), ref };
}
const query = (p: Progress) => `phase=${p.phase}&silent=${p.silent}&judged=${p.judged}&start=${p.start}&ref=${p.ref}`;
const outOfRounds = (round: number, progress: Progress) => round - progress.start >= MAX_ROUNDS[progress.phase];

/** Every path's end: rounds (and collected embeddings) deleted, the outcome logged, and back to the Voice Agent. */
async function end(env: Env, callSid: string, log: Log, detail: Record<string, unknown>, spoken: string): Promise<string> {
  // Best-effort: a committed replacement must still be reported as one. clearRounds's own hourly
  // sweep (or the call's end) removes what a failure leaves, and a lost event loses no state.
  await clearRounds(env, callSid).catch(() => {});
  await log("reenrollment.result", detail).catch(() => {});
  await noteReturn(env, callSid, "reenrollment", String(detail.outcome));
  return Say(`${spoken} Back to Dogwalk.`) + RETURN;
}

async function roundTwiml(
  env: Env, ctx: ExecutionContext, url: URL, callSid: string, account: string, round: number, progress: Progress, subject: Subject,
): Promise<string> {
  const phrase = await openRound(env, callSid, round, ritualOf(progress.phase), subject.id);
  return reenrollTake(env, ctx, url, callSid, account, round, 0, phrase, progress, subject);
}

const reenrollTake = (
  env: Env, ctx: ExecutionContext, url: URL, callSid: string, account: string, round: number, take: number, phrase: string,
  progress: Progress, subject: Subject,
) => takeTwiml(env, ctx, url, {
  callSid, round, take, phrase, account, ritual: ritualOf(progress.phase), identityHash: subject.identity_hash,
  action: `/voice/reenrollment/heard?${query(progress)}&round=${round}&take=${take}`,
});

/** Another phrase, or the phase's end once it has run out of rounds. */
async function nextRound(
  env: Env, ctx: ExecutionContext, url: URL, callSid: string, account: string, log: Log, round: number, progress: Progress,
  subject: Subject, spoken: string,
): Promise<string> {
  if (outOfRounds(round, progress)) {
    if (progress.phase === "enroll") return finish(env, ctx, url, callSid, account, log, round, subject, spoken);
    return end(env, callSid, log, { phase: progress.phase, user_id: subject.id, outcome: "undecided", judged: progress.judged },
      `${spoken} That's all for now. ${UNCHANGED}`);
  }
  return (spoken ? Say(spoken) : "") + await roundTwiml(env, ctx, url, callSid, account, round + 1, progress, subject);
}

/** /voice/reenrollment: check for a Voice Reference and the verifier, then start the check. */
export async function startReenrollment(env: Env, ctx: ExecutionContext, url: URL, form: FormData, log: Log): Promise<string> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  const subject = CALL_SID.test(callSid) ? await callSubject(env, callSid) : null;
  if (!subject?.embedding) {
    // First-call enrollment, not Re-enrollment, makes a first Voice Reference, on a Home call.
    await log("reenrollment.unavailable", { reason: "no_voice_reference" });
    if (CALL_SID.test(callSid)) await noteReturn(env, callSid, "reenrollment", "unavailable", "no_voice_reference");
    return Say("Your voice isn't enrolled yet, so there's nothing to replace. Your next call from your home phone will enroll it. Back to Dogwalk.") + RETURN;
  }
  if (!env.SPEAKER || !isAccountSid(account)) {
    await log("reenrollment.unavailable", { reason: env.SPEAKER ? "no_account" : "no_verifier" });
    await noteReturn(env, callSid, "reenrollment", "unavailable", env.SPEAKER ? "no_account" : "no_verifier");
    return Say("Voice re-enrollment is not available right now. Back to Dogwalk.") + RETURN;
  }
  if (subject.recent) {
    await log("reenrollment.result", { phase: "check", user_id: subject.id, outcome: "too_soon" });
    await noteReturn(env, callSid, "reenrollment", "too_soon");
    return Say(`Your voice profile was set in the last day, and it can change only once a day. Try again tomorrow. ${UNCHANGED} Back to Dogwalk.`) + RETURN;
  }
  // Rows outlive a call that hung up mid-round; any start clears those too.
  await clearRounds(env, callSid);
  await log("reenrollment.started", { user_id: subject.id, challenges: ROAMING_CHALLENGES });
  return Say("Re-enrolling replaces your voice profile with one from how you're calling now. " +
    `First, a voice check against your current profile: ${ROAMING_CHALLENGES} sentences, and each must match. ` +
    "After each tone, repeat the sentence once in your normal voice. Say stop to cancel.") +
    await roundTwiml(env, ctx, url, callSid, account, 1, { phase: "check", silent: 0, judged: 0, start: 0, ref: subject.version }, subject);
}

/** /voice/reenrollment/heard: the round's transcript decides whether its audio is scored at all. */
export async function handleReenrollmentHeard(env: Env, ctx: ExecutionContext, url: URL, form: FormData, log: Log): Promise<string> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  const progress = progressOf(url);
  const round = Number(url.searchParams.get("round"));
  const take = Number(url.searchParams.get("take") ?? "0") || 0;
  if (!progress || !CALL_SID.test(callSid) || !Number.isInteger(round) || round <= progress.start
    || round > progress.start + MAX_ROUNDS[progress.phase]) return RETURN;
  const ritual = ritualOf(progress.phase);
  const stop = stopTake(ritual, round, take);
  const row = await readRound(env, callSid, round);
  const subject = await callSubject(env, callSid);
  const toResult = stop + receivedCuePlay(env, url, ritual) +
    `<Redirect method="POST">/voice/reenrollment/result?${query({ ...progress, silent: 0 }).replace(/&/g, "&amp;")}&amp;round=${round}</Redirect>`;
  // A retried callback for a take already matched goes on to its result rather than ending the ritual.
  if (row && subject && row.ritual === ritual && row.user_id === subject.id && row.take === take && (row.state === "scoring" || row.state === "scored")
    && isAccountSid(account)) return toResult;
  if (!row || !subject?.embedding || row.ritual !== ritual || row.user_id !== subject.id || row.state !== "listening" || row.take !== take
    || !isAccountSid(account)) {
    const done = await committed(env, callSid);
    if (done) return stop + done;
    return stop + await end(env, callSid, log, { phase: progress.phase, outcome: "lost_round" }, `Re-enrollment stopped. ${UNCHANGED}`);
  }
  if (subject.version !== progress.ref) return stop + await lostReference(env, callSid, log, progress, subject);
  const answer = answerOf(row.phrase, String(form.get("SpeechResult") ?? ""), take);
  switch (answer.kind) {
    case "stop":
      return stop + await end(env, callSid, log, { phase: progress.phase, user_id: subject.id, outcome: "stopped" },
        `Re-enrollment cancelled. ${UNCHANGED}`);
    case "silent":
      await log("reenrollment.round", { phase: progress.phase, round, outcome: "silent" });
      if (progress.silent + 1 >= MAX_SILENT_ROUNDS) {
        return stop + await end(env, callSid, log, { phase: progress.phase, user_id: subject.id, outcome: "silent" },
          `I didn't hear anything. ${UNCHANGED}`);
      }
      await skipRound(env, callSid, round);
      return stop + await nextRound(env, ctx, url, callSid, account, log, round, { ...progress, silent: progress.silent + 1 }, subject,
        "I didn't hear the sentence. Here's another.");
    case "repeat":
      await nextTake(env, callSid, round, take);
      await log("reenrollment.repeat", { phase: progress.phase, round, take: take + 1 });
      return stop + Say("Again.") + await reenrollTake(env, ctx, url, callSid, account, round, take + 1, row.phrase, progress, subject);
    case "repeat_limit":
      await skipRound(env, callSid, round);
      await log("reenrollment.round", { phase: progress.phase, round, outcome: "repeat_limit" });
      return stop + await nextRound(env, ctx, url, callSid, account, log, round, { ...progress, silent: 0 }, subject,
        "No more repeats of that one. Here's another.");
    // A misheard prompt or a misrecognized word is a wrong phrase, never a voice failure.
    case "wrong_phrase":
      await skipRound(env, callSid, round);
      await log("reenrollment.round", { phase: progress.phase, round, outcome: "wrong_phrase" });
      return stop + receivedCuePlay(env, url, ritual) + await nextRound(env, ctx, url, callSid, account, log, round, { ...progress, silent: 0 },
        subject, "That wasn't the sentence. Here's another.");
    case "matched":
      await markScoring(env, callSid, round);
      // The cue plays while the verifier scores, so it costs the caller no extra wait.
      return toResult;
  }
}

/** The Stop for the stream a /voice/reenrollment/heard request's round and take started, so an error path can stop it. */
export function reenrollmentStreamStop(url: URL): string {
  const progress = progressOf(url);
  const round = Number(url.searchParams.get("round")), take = Number(url.searchParams.get("take") ?? "0");
  return progress && Number.isInteger(round) && round >= 1 && Number.isInteger(take) && take >= 0
    ? stopTake(ritualOf(progress.phase), round, take) : "";
}

/** /voice/reenrollment/result: wait for the round's score; in the check speak its level, in enrollment collect it. */
export async function handleReenrollmentResult(env: Env, ctx: ExecutionContext, url: URL, form: FormData, log: Log): Promise<string> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  const progress = progressOf(url);
  const round = Number(url.searchParams.get("round"));
  if (!progress || !CALL_SID.test(callSid) || !Number.isInteger(round) || round < 1 || !isAccountSid(account)) return RETURN;
  const ritual = ritualOf(progress.phase);
  const subject = await callSubject(env, callSid);
  const { row, wait_ms } = await awaitScore(env, callSid, round);
  if (!subject?.embedding || !row || row.ritual !== ritual || row.user_id !== subject.id || (row.state !== "scoring" && row.state !== "scored")) {
    const done = await committed(env, callSid);
    if (done) return done;
    return end(env, callSid, log, { phase: progress.phase, outcome: "lost_round" }, `Re-enrollment stopped. ${UNCHANGED}`);
  }
  if (subject.version !== progress.ref) return lostReference(env, callSid, log, progress, subject);
  const verdict = judge(row);
  await log("reenrollment.round", { phase: progress.phase, round, outcome: verdict.outcome, ...scoreDetail(row), ...scoreTiming(row, wait_ms) });
  // Enrollment keeps a collected answer's embedding until the new Voice Reference is computed.
  if (verdict.outcome !== "collected") {
    await env.DB.prepare("DELETE FROM challenge_rounds WHERE call_sid = ? AND round = ?").bind(callSid, round).run();
  }
  const next = (spoken: string, judged = progress.judged, phase = progress.phase, start = progress.start) =>
    nextRound(env, ctx, url, callSid, account, log, round, { phase, silent: 0, judged, start, ref: progress.ref }, subject, spoken);

  if (verdict.outcome === "unscored") {
    if (verdict.error) {
      return end(env, callSid, log, { phase: progress.phase, user_id: subject.id, outcome: "unscored", error: verdict.error },
        `Voice checks aren't working right now. ${UNCHANGED}`);
    }
    return next("I couldn't score that one. Here's another.");
  }
  if (verdict.outcome === "not_enough_speech") {
    return next(ritual === "enrollment" ? "I didn't get enough of that one. Here's another." : "Not enough to judge. Here's another.");
  }
  if (verdict.outcome === "collected") {
    if (progress.judged + 1 >= ENROLL_ANSWERS) return finish(env, ctx, url, callSid, account, log, round, subject, "");
    return next(enrollmentMark(progress.judged + 1), progress.judged + 1);
  }
  // The transfer's confirmation: the current voice agrees, or nothing changes.
  if (progress.phase === "confirm") {
    if (verdict.outcome === "no_match") {
      return end(env, callSid, log, { phase: "confirm", user_id: subject.id, outcome: "failed" }, `${verdict.spoken} ${UNCHANGED}`);
    }
    return replace(env, callSid, log, subject, "transferred", `${verdict.spoken} Thanks. This account now answers to the new voice.`);
  }
  // The check: one miss ends it, so every judged round has matched.
  if (verdict.outcome === "no_match") {
    return end(env, callSid, log, { phase: "check", user_id: subject.id, outcome: "failed", judged: progress.judged + 1, passed: progress.judged },
      `${verdict.spoken} ${UNCHANGED}`);
  }
  if (progress.judged + 1 < ROAMING_CHALLENGES) return next(verdict.spoken, progress.judged + 1);
  await log("reenrollment.checked", { user_id: subject.id, passed: progress.judged + 1 });
  // Enrollment's rounds start after the check's, with their own budget.
  return next(`${verdict.spoken} That's you. Now the new profile: ${ENROLL_ANSWERS} sentences, ` +
    "from the phone and place you'll usually call from. After each tone, repeat the sentence once.", 0, "enroll", round);
}

/** The Voice Reference changed after the check began (another call re-enrolled): nothing more changes here. */
const lostReference = (env: Env, callSid: string, log: Log, progress: Progress, subject: Subject) =>
  end(env, callSid, log, { phase: progress.phase, user_id: subject.id, outcome: "lost_reference" },
    "Your voice profile changed during this call, so I've left it as it is now.");

/**
 * A replacement this call already committed, from its reenrollment.replaced receipt: a retried
 * webhook after the rounds were cleared must not tell the caller that nothing changed.
 */
async function committed(env: Env, callSid: string): Promise<string | null> {
  const row = await env.DB.prepare(
    "SELECT json_extract(detail, '$.outcome') AS outcome FROM call_activity WHERE call_sid = ? AND event = 'reenrollment.replaced' LIMIT 1",
  ).bind(callSid).first<{ outcome: string }>();
  if (!row) return null;
  return Say(row.outcome === "transferred" ? "This account now answers to the new voice. Back to Dogwalk."
    : "Your new voice profile replaces the old one. Back to Dogwalk.") + RETURN;
}

/** Cosine similarity of two embeddings, or null when they cannot be compared. */
function cosine(a: number[], b: number[]): number | null {
  if (a.length !== b.length || a.length === 0) return null;
  let dot = 0;
  a.forEach((value, i) => { dot += value * b[i]; });
  const norms = Math.hypot(...a) * Math.hypot(...b);
  return norms > 0 ? dot / norms : null;
}

/**
 * The collected answers' mean replaces the Voice Reference when it still matches the old one;
 * a different voice in the same embedding space goes on to the transfer's confirmation instead.
 */
async function finish(
  env: Env, ctx: ExecutionContext, url: URL, callSid: string, account: string, log: Log, round: number, subject: Subject, spoken: string,
): Promise<string> {
  const { space, embeddings } = await collectedAnswers(env, callSid, subject.id);
  const lead = spoken ? `${spoken} ` : "";
  if (embeddings.length < MIN_ENROLL_ANSWERS) {
    return end(env, callSid, log, { phase: "enroll", user_id: subject.id, outcome: "enrollment_failed", answers: embeddings.length },
      `${lead}I couldn't hear enough to learn your voice. ${UNCHANGED}`);
  }
  const continuity = space === subject.embedding_space ? cosine(meanEmbedding(embeddings), JSON.parse(subject.embedding!)) : null;
  if (continuity === null) {
    // Another embedding space cannot be compared, or confirmed against the old reference.
    return end(env, callSid, log, { phase: "enroll", user_id: subject.id, outcome: "discontinuous", answers: embeddings.length,
      continuity, same_space: false }, `${lead}Those answers can't be compared with your current profile. ${UNCHANGED}`);
  }
  if (continuity >= MATCH_COSINE) {
    return replace(env, callSid, log, subject, "reenrolled", `${lead}Thanks. Your new voice profile replaces the old one.`, continuity);
  }
  // Handing the account to another voice takes a full enrollment, never the three-answer fallback.
  if (embeddings.length < ENROLL_ANSWERS) {
    return end(env, callSid, log, { phase: "enroll", user_id: subject.id, outcome: "enrollment_failed", answers: embeddings.length },
      `${lead}That sounded like a different voice, and handing over the account needs all ${ENROLL_ANSWERS} sentences. ${UNCHANGED}`);
  }
  // The collected answers stay in challenge_rounds until the confirmation decides.
  await log("reenrollment.transfer", { user_id: subject.id, answers: embeddings.length, continuity });
  const progress: Progress = { phase: "confirm", silent: 0, judged: 0, start: round, ref: subject.version };
  return Say(`${lead}That's a different voice from your current profile. Continuing gives this account to the new voice, ` +
    "with its files, memory, and connected accounts. To agree, the current owner repeats one more sentence after the tone. " +
    "To keep things as they are, say stop.") + await roundTwiml(env, ctx, url, callSid, account, round + 1, progress, subject);
}

/**
 * Replace the Voice Reference with the mean of the collected answers. The update is conditioned
 * on the old reference, so a concurrent replacement is never overwritten with one checked against
 * something else, and on the old one being at least a day old.
 */
async function replace(
  env: Env, callSid: string, log: Log, subject: Subject, outcome: "reenrolled" | "transferred", spoken: string, continuity?: number,
): Promise<string> {
  const { space, embeddings } = await collectedAnswers(env, callSid, subject.id);
  if (embeddings.length < MIN_ENROLL_ANSWERS) {
    return end(env, callSid, log, { phase: "enroll", user_id: subject.id, outcome: "lost_round" }, `Re-enrollment stopped. ${UNCHANGED}`);
  }
  const reference = meanEmbedding(embeddings);
  // The User's Openings learn of the change from reenrollment.replaced (voice_session.ts,
  // voiceProfileNotice), so it commits with the replacement, in one transaction, and only when
  // the replacement changed a row; the reenrollment.result that end() logs stays best-effort.
  const [replaced] = await env.DB.batch([
    env.DB.prepare(
      `UPDATE voice_references SET embedding_space = ?, embedding = ?, enrolled_at = unixepoch()
        WHERE user_id = ? AND embedding_space = ? AND embedding = ? AND enrolled_at <= unixepoch() - ?`,
    ).bind(space, JSON.stringify(reference), subject.id, subject.embedding_space, subject.embedding, REPLACE_INTERVAL_SECONDS),
    env.DB.prepare(
      `INSERT INTO call_activity (call_sid, ts, source, direction, event, detail)
       SELECT ?, unixepoch(), 'access', 'internal', 'reenrollment.replaced', ? WHERE changes() = 1`,
    ).bind(callSid, JSON.stringify({ user_id: subject.id, outcome })),
  ]);
  if (!replaced.meta.changes) {
    return end(env, callSid, log, { phase: "enroll", user_id: subject.id, outcome: "lost_reference" }, "Re-enrollment stopped.");
  }
  const detail = outcome === "reenrolled" ? { continuity } : {};
  return end(env, callSid, log, { phase: outcome === "reenrolled" ? "enroll" : "confirm", user_id: subject.id, outcome,
    answers: embeddings.length, ...detail }, spoken);
}
