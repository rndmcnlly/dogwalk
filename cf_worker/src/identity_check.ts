// The Identity Check (DOMAIN.md, Access Control), in shadow mode: the deterministic, speech-only
// stage before the Voice Agent. It runs, is scored, and is reported, but never decides entry:
// every outcome records what enforcement would have done, and a Home call then connects as
// before. Rounds have the Identity Challenge's shape and rules (challenge_rounds.ts); this
// module holds the check's words, limits, and paths:
//
// - home: a call from a User's Home hears their Handle and one Identity Challenge, then
//   connects. Full instructions until the User has completed TERSE_AFTER challenges.
// - roaming: a caller who names another User's Handle takes three Identity Challenges against
//   that User's Voice Reference. Unless Roaming admission is on (ROAMING_ADMISSION, off by
//   default), they are never admitted: they hear it is coming soon, and the call ends. When it
//   is on, the unregistered greeting also accepts a Handle, three passed challenges admit the
//   caller as that User (the Home unchanged), the first unmatched voice ends the attempt, and
//   failed attempts pause Roaming per Handle and per calling number (roaming_attempts).
// - enroll: a User without a Voice Reference repeats ENROLL_ANSWERS phrases instead, and their
//   Voice Reference is computed from the answers' embeddings; then the call connects.
// - register: the same enrollment at the end of Registration; then the call ends.
//
// Each answer's transcript is classified deterministically: the phrase, a Handle (switch to
// that User), an Invite Code or a mention of an invite (Registration), a mention of donating
// (Voice Donation), someone else (ask who), or anything else (a wrong phrase, ask again).
// Webhook URLs carry the path and progress; Twilio signs them, so a caller cannot alter them.
import { isAccountSid, Say } from "./caller_stream";
import {
  answerOf, awaitScore, CALL_SID, clearRounds, handleChallengeStream, judge, markScoring, MAX_REPEATS, MIN_SPEECH_SECONDS, nextTake,
  openRound, readRound, receivedCuePlay, type Ritual, scoreDetail, scoreTiming, skipRound, stopTake, takeTwiml,
} from "./challenge_rounds";
import { HANDLE_WORDS } from "./handles";
import { EVEN_WORDS, ODD_WORDS } from "./invitations";
import type { Env } from "./types";

// Configuration, not domain rules.
/** Completed Home challenges after which the greeting stops teaching the ritual. */
export const TERSE_AFTER = 3;
/** Identity Challenges a caller who names a Handle takes, and Re-enrollment's check (reenrollment.ts) with them. */
export const ROAMING_CHALLENGES = 3;
// Roaming admission's attempt limits: failed attempts (an unmatched voice on a matched phrase;
// wrong phrases and thin speech never count) in the trailing window pause Roaming for a Handle,
// or from a calling number across Handles, until they age out of it.
const ROAMING_FAILURES_PER_HANDLE = 3;
const ROAMING_FAILURES_PER_NUMBER = 5;
const ROAMING_WINDOW_SECONDS = 86400;
// A Handle takes one Roaming attempt at a time; an attempt whose call never reported its end
// stops holding the Handle after this long (eight rounds take a few minutes).
const ROAMING_ACTIVE_SECONDS = 600;
// Attempts are kept for the User's next Home Opening, then pruned past this age, read or not.
const ROAMING_KEEP_SECONDS = 90 * 86400;

/** Whether passing Roaming's challenges admits the caller (configuration; shadow when off). */
export const roamingAdmits = (env: Env) => env.ROAMING_ADMISSION === "on";
/** Answers enrollment collects, and the fewest with enough speech that make a Voice Reference. */
export const ENROLL_ANSWERS = 5;
export const MIN_ENROLL_ANSWERS = 3;
// Rounds, judged or not, before a path gives up: wrong phrases and thin speech never count as
// failures, so these bound a line that never yields a judgment.
const MAX_ROUNDS: Record<Path, number> = { home: 4, roaming: 8, enroll: 10, register: 10 };
// Empty transcripts in a row before a path gives up.
const MAX_SILENT_ROUNDS = 2;

type Path = "home" | "roaming" | "enroll" | "register";
const PATHS: Path[] = ["home", "roaming", "enroll", "register"];
const ritualOf = (path: Path): Ritual => (path === "enroll" || path === "register" ? "enrollment" : "identity");

type Log = (event: string, detail: Record<string, unknown>) => Promise<void>;
/** Ways out of the check that other stages own (index.ts): Registration and Voice Donation. */
export interface Exits {
  registration(heard: string[]): Promise<string>;
  donation(): string;
}

const CONNECT = '<Redirect method="POST">/voice</Redirect>';
const GOODBYE = "<Hangup/>";

interface Subject { id: number; identity_hash: string; handle: string; enrolled: number; challenges_completed: number }

async function subjectOf(env: Env, userId: number): Promise<Subject | null> {
  return env.DB.prepare(
    `SELECT u.id, u.identity_hash, u.handle, u.challenges_completed,
            EXISTS (SELECT 1 FROM voice_references vr WHERE vr.user_id = u.id) AS enrolled
       FROM users u WHERE u.id = ? AND u.reaped_at IS NULL AND u.handle IS NOT NULL`,
  ).bind(userId).first<Subject>();
}

// Carried in each round's webhook URLs. start is the round before the path began, so a path
// entered mid-call (Roaming after a Handle is named) gets its own MAX_ROUNDS budget while round
// numbers stay increasing, and a stopped stream's late score can never land on a new round.
interface Progress { path: Path; user: number; silent: number; judged: number; passed: number; start: number }
function progressOf(url: URL): Progress | null {
  const path = url.searchParams.get("path") as Path;
  const user = Number(url.searchParams.get("user"));
  if (!PATHS.includes(path) || !Number.isSafeInteger(user) || user <= 0) return null;
  const count = (name: string) => Number(url.searchParams.get(name) ?? "0") || 0;
  return { path, user, silent: count("silent"), judged: count("judged"), passed: count("passed"), start: count("start") };
}
const query = (p: Progress) => `path=${p.path}&user=${p.user}&silent=${p.silent}&judged=${p.judged}&passed=${p.passed}&start=${p.start}`;
const fresh = (path: Path, user: number, start = 0): Progress => ({ path, user, silent: 0, judged: 0, passed: 0, start });
const outOfRounds = (round: number, progress: Progress) => round - progress.start >= MAX_ROUNDS[progress.path];

// --- outcomes -----------------------------------------------------------

/**
 * Record how the check ended: on the Voice Call (the Opening tells the Session Orchestrator, and
 * a re-entry to /voice skips the check) and in Call Activity. `enforcement` is what an
 * enforcing check would have done; in shadow mode nothing is withheld. Only Roaming admission
 * enforces (shadow false), on the attempts it decides.
 */
async function conclude(env: Env, callSid: string, log: Log, identity: Record<string, unknown>, shadow = true): Promise<void> {
  const record = { ...identity, shadow };
  await env.DB.prepare("UPDATE voice_calls SET identity = ? WHERE call_sid = ?").bind(JSON.stringify(record), callSid).run();
  await clearRounds(env, callSid);
  await log("identity.result", record);
}

/** The words and TwiML that follow a path's end. */
function after(path: Path, spoken: string): string {
  if (path === "register") return Say(`${spoken} Goodbye.`.trim()) + GOODBYE;
  const words = spoken ? Say(spoken) : "";
  return words + (path === "roaming" ? GOODBYE : CONNECT);
}

// --- classification -----------------------------------------------------

const HANDLE_SET = new Set(HANDLE_WORDS);
const EVEN_SET = new Set(EVEN_WORDS), ODD_SET = new Set(ODD_WORDS);

/** A User named in the transcript by their Handle: two adjacent Handle words that are a User's Handle. */
async function namedUser(env: Env, heard: string[]): Promise<Subject | null> {
  for (let i = 0; i + 1 < heard.length; i++) {
    if (!HANDLE_SET.has(heard[i]) || !HANDLE_SET.has(heard[i + 1])) continue;
    const row = await env.DB.prepare("SELECT id FROM users WHERE handle = ? AND reaped_at IS NULL")
      .bind(`${heard[i]} ${heard[i + 1]}`).first<{ id: number }>();
    if (row) return subjectOf(env, row.id);
  }
  return null;
}

/** Three PGP words in Invite Code order, or a mention of an invite. */
const mentionsInvite = (heard: string[]) =>
  heard.some((word) => word.startsWith("invit")) ||
  heard.some((word, i) => EVEN_SET.has(word) && ODD_SET.has(heard[i + 1]) && EVEN_SET.has(heard[i + 2]));
const mentionsDonating = (heard: string[]) => heard.some((word) => word.startsWith("donat"));
const someoneElse = (heard: string[]) => heard.some((word) => word === "someone" || word === "somebody" || word === "else");

type Escape = { to: "user"; user: Subject } | { to: "registration" | "donation" | "who" } | null;

/** What a transcript that is not the phrase asks for, if anything. */
async function escapeOf(env: Env, heard: string[]): Promise<Escape> {
  const user = await namedUser(env, heard);
  if (user) return { to: "user", user };
  if (mentionsInvite(heard)) return { to: "registration" };
  if (mentionsDonating(heard)) return { to: "donation" };
  if (someoneElse(heard)) return { to: "who" };
  return null;
}

// --- rounds -------------------------------------------------------------

async function roundTwiml(
  env: Env, ctx: ExecutionContext, url: URL, callSid: string, account: string, round: number, progress: Progress, subject: Subject,
): Promise<string> {
  const phrase = await openRound(env, callSid, round, ritualOf(progress.path), subject.id);
  return identityTake(env, ctx, url, callSid, account, round, 0, phrase, progress, subject);
}

const identityTake = (
  env: Env, ctx: ExecutionContext, url: URL, callSid: string, account: string, round: number, take: number, phrase: string,
  progress: Progress, subject: Subject,
) => takeTwiml(env, ctx, url, {
  callSid, round, take, phrase, account, ritual: ritualOf(progress.path), identityHash: subject.identity_hash,
  action: `/voice/identity/heard?${query(progress)}&round=${round}&take=${take}`,
});

/** Another phrase, or the path's end once it has run out of rounds. */
async function nextRound(
  env: Env, ctx: ExecutionContext, url: URL, callSid: string, account: string, log: Log, round: number, progress: Progress,
  subject: Subject, spoken: string,
): Promise<string> {
  if (outOfRounds(round, progress)) {
    if (progress.path === "enroll" || progress.path === "register") return finishEnrollment(env, callSid, log, progress, spoken);
    await conclude(env, callSid, log, { path: progress.path, user_id: subject.id, outcome: "undecided",
      enforcement: progress.path === "home" ? "require_roaming" : "deny", judged: progress.judged, passed: progress.passed });
    return after(progress.path, `${spoken} ${progress.path === "home" ? "Skipping the voice check for now." : "That's all for now."}`);
  }
  return (spoken ? Say(spoken) : "") + await roundTwiml(env, ctx, url, callSid, account, round + 1, progress, subject);
}

// --- starts -------------------------------------------------------------

/**
 * A Home call's check, or its first-call enrollment when the User has no Voice Reference. Null
 * when the check cannot run (no verifier, no account to sign streams with), after recording
 * that, so the caller connects at once.
 */
export async function startHomeCheck(
  env: Env, ctx: ExecutionContext, url: URL, form: FormData, userId: number, log: Log,
): Promise<string | null> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  const subject = await subjectOf(env, userId);
  if (!CALL_SID.test(callSid) || !subject || !env.SPEAKER || !isAccountSid(account)) {
    if (CALL_SID.test(callSid)) {
      await conclude(env, callSid, log, { path: "home", user_id: userId, outcome: "unavailable",
        reason: !env.SPEAKER ? "no_verifier" : !subject ? "no_handle" : "no_account", enforcement: "undecided" });
    }
    return null;
  }
  await clearRounds(env, callSid);
  if (!subject.enrolled) return startEnrollment(env, ctx, url, callSid, account, subject, "enroll", log, `Dogwalk, for ${subject.handle}.`);
  const terse = subject.challenges_completed >= TERSE_AFTER;
  await log("identity.started", { path: "home", user_id: subject.id, terse });
  const greeting = terse
    ? `Dogwalk, for ${subject.handle}. Your sentence.`
    : `Dogwalk, for ${subject.handle}. To check that it's you, repeat the sentence after the tone, once, in your normal voice. ` +
      "If you're someone else, say so.";
  return Say(greeting) + await roundTwiml(env, ctx, url, callSid, account, 1, fresh("home", subject.id), subject);
}

/** The end of Registration: the new User enrolls their Voice Reference, then the call ends. Null when it cannot run. */
export async function startRegistrationEnrollment(
  env: Env, ctx: ExecutionContext, url: URL, form: FormData, userId: number, log: Log, spoken: string,
): Promise<string | null> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  const subject = await subjectOf(env, userId);
  if (!CALL_SID.test(callSid) || !subject || subject.enrolled || !env.SPEAKER || !isAccountSid(account)) return null;
  await clearRounds(env, callSid);
  return startEnrollment(env, ctx, url, callSid, account, subject, "register", log, spoken);
}

async function startEnrollment(
  env: Env, ctx: ExecutionContext, url: URL, callSid: string, account: string, subject: Subject, path: "enroll" | "register",
  log: Log, lead: string,
): Promise<string> {
  await log("identity.started", { path, user_id: subject.id });
  return Say(`${lead} So Dogwalk can recognize your voice from any phone, I'll learn it now. ` +
    "I keep a voice profile, never a recording, and it never changes from your calls; you can redo it later. " +
    `After each tone, repeat the sentence once in your normal voice. ${ENROLL_ANSWERS} sentences. Say stop to skip.`) +
    await roundTwiml(env, ctx, url, callSid, account, 1, fresh(path, subject.id), subject);
}

/** A caller named another User's Handle: Roaming's three Identity Challenges against that User's Voice Reference. */
async function startRoaming(
  env: Env, ctx: ExecutionContext, url: URL, form: FormData, log: Log, round: number, subject: Subject, from: Path | "greeting",
): Promise<string> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  await log("identity.started", { path: "roaming", user_id: subject.id, from });
  if (!subject.enrolled) {
    await conclude(env, callSid, log, { path: "roaming", user_id: subject.id, outcome: "no_voice_reference", enforcement: "deny" });
    return after("roaming", `${subject.handle} has no enrolled voice yet, so that name works only from its home phone for now. Goodbye.`);
  }
  if (roamingAdmits(env)) {
    const phone = String(form.get("From") ?? "");
    const paused = await roamingPause(env, subject.id, phone);
    if (paused) {
      await conclude(env, callSid, log, { path: "roaming", user_id: subject.id, outcome: "paused", paused, enforcement: "deny" }, false);
      return after("roaming", paused === "handle"
        ? `Too many failed tries as ${subject.handle} from other phones today. Call from its home phone, or try again tomorrow. Goodbye.`
        : "Too many failed voice checks from this phone today. Try again tomorrow. Goodbye.");
    }
    // Recorded before the first challenge, so hanging up after hearing a level cannot hide a
    // failure. One statement claims the Handle, so calls in parallel cannot all start before any
    // has failed: another call's attempt is in progress until its check concludes or it ends.
    await env.DB.prepare("DELETE FROM roaming_attempts WHERE started_at < unixepoch() - ?").bind(ROAMING_KEEP_SECONDS).run();
    const claimed = await env.DB.prepare(
      `INSERT INTO roaming_attempts (call_sid, user_id, phone_number, started_at)
       SELECT ?1, ?2, ?3, unixepoch() WHERE NOT EXISTS (
         SELECT 1 FROM roaming_attempts a JOIN voice_calls c ON c.call_sid = a.call_sid
          WHERE a.user_id = ?2 AND a.call_sid != ?1 AND a.started_at > unixepoch() - ?4 AND a.admitted_at IS NULL
            AND a.failures = 0 AND c.identity IS NULL AND c.ended_at IS NULL)
       ON CONFLICT(call_sid, user_id) DO NOTHING`,
    ).bind(callSid, subject.id, phone, ROAMING_ACTIVE_SECONDS).run();
    if (!claimed.meta.changes && !await roamingAttempt(env, callSid, subject.id)) {
      await conclude(env, callSid, log, { path: "roaming", user_id: subject.id, outcome: "busy", enforcement: "deny" }, false);
      return after("roaming", `Someone is already checking as ${subject.handle}. Try again in a few minutes. Goodbye.`);
    }
  }
  return Say(`Checking ${subject.handle}. ${ROAMING_CHALLENGES} sentences. After each tone, repeat the sentence once in your normal voice.`) +
    await roundTwiml(env, ctx, url, callSid, account, round + 1, fresh("roaming", subject.id, round), subject);
}

/** Whether this call started a Roaming attempt as this User while admission was on. */
async function roamingAttempt(env: Env, callSid: string, userId: number): Promise<boolean> {
  return Boolean(await env.DB.prepare("SELECT 1 FROM roaming_attempts WHERE call_sid = ? AND user_id = ?").bind(callSid, userId).first());
}

/** Whether Roaming is paused for this Handle or from this number by recent failed attempts. */
async function roamingPause(env: Env, userId: number, phone: string): Promise<"handle" | "number" | null> {
  const row = await env.DB.prepare(
    `SELECT COALESCE(SUM(user_id = ?1), 0) AS handle, COALESCE(SUM(phone_number = ?2), 0) AS number FROM roaming_attempts
      WHERE failures > 0 AND started_at > unixepoch() - ?3 AND (user_id = ?1 OR phone_number = ?2)`,
  ).bind(userId, phone, ROAMING_WINDOW_SECONDS).first<{ handle: number; number: number }>();
  if ((row?.handle ?? 0) >= ROAMING_FAILURES_PER_HANDLE) return "handle";
  if ((row?.number ?? 0) >= ROAMING_FAILURES_PER_NUMBER) return "number";
  return null;
}

/**
 * The unregistered greeting's way into Roaming, when admission is on: a Handle among the
 * words starts that User's challenges. Null when no Handle was named (or admission is off).
 */
export async function startRoamingFromGreeting(
  env: Env, ctx: ExecutionContext, url: URL, form: FormData, heard: string[], log: Log,
): Promise<string | null> {
  if (!roamingAdmits(env) || !CALL_SID.test(String(form.get("CallSid") ?? "")) || !env.SPEAKER
    || !isAccountSid(String(form.get("AccountSid") ?? ""))) return null;
  const subject = await namedUser(env, heard);
  if (!subject) return null;
  await clearRounds(env, String(form.get("CallSid")));
  return startRoaming(env, ctx, url, form, log, 0, subject, "greeting");
}

// --- heard --------------------------------------------------------------

/** /voice/identity/heard: the round's transcript decides whether its audio is scored, or where the caller goes. */
export async function handleIdentityHeard(
  env: Env, ctx: ExecutionContext, url: URL, form: FormData, log: Log, exits: Exits,
): Promise<string> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  const progress = progressOf(url);
  const round = Number(url.searchParams.get("round"));
  const take = Number(url.searchParams.get("take") ?? "0") || 0;
  if (!progress || !CALL_SID.test(callSid) || !Number.isInteger(round) || round < 1 || round <= progress.start
    || round > progress.start + MAX_ROUNDS[progress.path]) return CONNECT;
  const ritual = ritualOf(progress.path);
  const stop = stopTake(ritual, round, take);
  const row = await readRound(env, callSid, round);
  const subject = await subjectOf(env, progress.user);
  if (!row || !subject || row.ritual !== ritual || row.user_id !== subject.id || row.state !== "listening" || row.take !== take
    || !isAccountSid(account)) {
    await conclude(env, callSid, log, { path: progress.path, user_id: progress.user, outcome: "lost_round", enforcement: "undecided" });
    return stop + after(progress.path, "");
  }
  const answer = answerOf(row.phrase, String(form.get("SpeechResult") ?? ""), take);
  const enrolling = ritual === "enrollment";
  switch (answer.kind) {
    case "stop":
      await conclude(env, callSid, log, { path: progress.path, user_id: subject.id, outcome: "stopped", enforcement: "undecided" });
      return stop + after(progress.path, enrolling ? "Skipping enrollment. I'll ask again next call."
        : progress.path === "home" ? "Skipping the voice check." : "Goodbye.");
    case "silent":
      await log("identity.round", { path: progress.path, round, outcome: "silent" });
      if (progress.silent + 1 >= MAX_SILENT_ROUNDS) {
        await conclude(env, callSid, log, { path: progress.path, user_id: subject.id, outcome: "silent", enforcement: "undecided" });
        return stop + after(progress.path, "I didn't hear anything.");
      }
      await skipRound(env, callSid, round);
      return stop + await nextRound(env, ctx, url, callSid, account, log, round, { ...progress, silent: progress.silent + 1 }, subject,
        "I didn't hear the sentence. Here's another.");
    case "repeat":
      await nextTake(env, callSid, round, take);
      await log("identity.repeat", { path: progress.path, round, take: take + 1 });
      return stop + Say("Again.") + await identityTake(env, ctx, url, callSid, account, round, take + 1, row.phrase, progress, subject);
    case "repeat_limit":
      await skipRound(env, callSid, round);
      await log("identity.round", { path: progress.path, round, outcome: "repeat_limit" });
      return stop + await nextRound(env, ctx, url, callSid, account, log, round, { ...progress, silent: 0 }, subject,
        "No more repeats of that one. Here's another.");
    case "matched":
      await markScoring(env, callSid, round);
      // The cue plays while the verifier scores, so it costs the caller no extra wait.
      return stop + receivedCuePlay(env, url, ritual) +
        `<Redirect method="POST">/voice/identity/result?${query({ ...progress, silent: 0 }).replace(/&/g, "&amp;")}&amp;round=${round}</Redirect>`;
    case "wrong_phrase":
      break;
  }
  // Not the phrase: a Handle, an invite, donating, or someone else steers the call; anything else is a wrong phrase.
  const escape = progress.path === "register" ? null : await escapeOf(env, answer.heard);
  if (escape?.to === "user" && escape.user.id === subject.id) {
    // The caller said the name they are being checked as: hear the same sentence again, within
    // the same replay limit as a request to repeat it.
    if (take < MAX_REPEATS) {
      await nextTake(env, callSid, round, take);
      return stop + Say("Your sentence.") + await identityTake(env, ctx, url, callSid, account, round, take + 1, row.phrase, progress, subject);
    }
    await skipRound(env, callSid, round);
    await log("identity.round", { path: progress.path, round, outcome: "repeat_limit" });
    return stop + await nextRound(env, ctx, url, callSid, account, log, round, { ...progress, silent: 0 }, subject,
      "No more repeats of that one. Here's another.");
  }
  if (escape) {
    await skipRound(env, callSid, round);
    await log("identity.escape", { path: progress.path, round, to: escape.to, ...(escape.to === "user" ? { user_id: escape.user.id } : {}) });
    if (escape.to === "user") return stop + await startRoaming(env, ctx, url, form, log, round, escape.user, progress.path);
    if (escape.to === "who") return stop + whoPrompt(1);
    await conclude(env, callSid, log, { path: progress.path, user_id: subject.id, outcome: escape.to, enforcement: "undecided" });
    return stop + (escape.to === "registration" ? await exits.registration(answer.heard) : exits.donation());
  }
  await skipRound(env, callSid, round);
  await log("identity.round", { path: progress.path, round, outcome: "wrong_phrase" });
  return stop + receivedCuePlay(env, url, ritual) + await nextRound(env, ctx, url, callSid, account, log, round, { ...progress, silent: 0 }, subject,
    "That wasn't the sentence. Here's another.");
}

/** The Stop for the stream a /voice/identity/heard request's round and take started, so an error path can stop it. */
export function identityStreamStop(url: URL): string {
  const progress = progressOf(url);
  const round = Number(url.searchParams.get("round")), take = Number(url.searchParams.get("take") ?? "0");
  return progress && Number.isInteger(round) && round >= 1 && Number.isInteger(take) && take >= 0
    ? stopTake(ritualOf(progress.path), round, take) : "";
}

// --- someone else -------------------------------------------------------

const WHO_HINTS = ["invite", "donate"];
function whoPrompt(attempt: number): string {
  return `<Gather input="speech" speechTimeout="auto" actionOnEmptyResult="true" action="/voice/identity/who?attempt=${attempt}" ` +
    `method="POST" hints="${WHO_HINTS.join(",")}" language="en-US">` +
    Say(attempt === 1 ? "Who's calling? Say your Dogwalk name, your three invite words, or donate."
      : "Say your two-word Dogwalk name, your three invite words, or donate.") +
    "</Gather>";
}

/** /voice/identity/who: a caller who said they are someone else names a Handle, an invite, or donating. */
export async function handleIdentityWho(
  env: Env, ctx: ExecutionContext, url: URL, form: FormData, log: Log, exits: Exits, homeUserId: number | null,
): Promise<string> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  const attempt = Number(url.searchParams.get("attempt") ?? "1") || 1;
  if (!CALL_SID.test(callSid)) return GOODBYE;
  const heard = String(form.get("SpeechResult") ?? "").toLowerCase().replace(/[^a-z\s]/g, " ").split(/\s+/).filter(Boolean);
  const escape = await escapeOf(env, heard);
  await log("identity.who", { attempt, to: escape?.to ?? "unknown", ...(escape?.to === "user" ? { user_id: escape.user.id } : {}) });
  if (escape?.to === "user") {
    // Naming this phone's own User is the Home check after all: one challenge then connect, or
    // first-call enrollment when they have no Voice Reference yet. Never Roaming on their own Home.
    if (escape.user.id === homeUserId && isAccountSid(account)) {
      await clearRounds(env, callSid);
      if (!escape.user.enrolled) {
        return startEnrollment(env, ctx, url, callSid, account, escape.user, "enroll", log, `Dogwalk, for ${escape.user.handle}.`);
      }
      return Say(`Dogwalk, for ${escape.user.handle}. Your sentence.`) +
        await roundTwiml(env, ctx, url, callSid, account, 1, fresh("home", escape.user.id), escape.user);
    }
    if (isAccountSid(account)) {
      await clearRounds(env, callSid);
      return startRoaming(env, ctx, url, form, log, 0, escape.user, "home");
    }
  }
  if (escape?.to === "registration" || escape?.to === "donation") {
    await conclude(env, callSid, log, { path: "roaming", outcome: escape.to, enforcement: "undecided" });
    return escape.to === "registration" ? exits.registration(heard) : exits.donation();
  }
  if (attempt < 2 && escape?.to !== "user") return whoPrompt(attempt + 1);
  await conclude(env, callSid, log, { path: "roaming", outcome: "unknown_handle", enforcement: "deny" });
  return Say("I don't know that name. Ask a Dogwalk user for an invite. Goodbye.") + GOODBYE;
}

// --- results ------------------------------------------------------------

/** /voice/identity/result: wait for the round's score, speak its level (never for enrollment), and go on. */
export async function handleIdentityResult(env: Env, ctx: ExecutionContext, url: URL, form: FormData, log: Log): Promise<string> {
  const callSid = String(form.get("CallSid") ?? "");
  const account = String(form.get("AccountSid") ?? "");
  const progress = progressOf(url);
  const round = Number(url.searchParams.get("round"));
  if (!progress || !CALL_SID.test(callSid) || !Number.isInteger(round) || round < 1 || !isAccountSid(account)) return CONNECT;
  const ritual = ritualOf(progress.path);
  const subject = await subjectOf(env, progress.user);
  const { row, wait_ms } = await awaitScore(env, callSid, round);
  if (!subject || !row || row.ritual !== ritual || row.user_id !== subject.id || (row.state !== "scoring" && row.state !== "scored")) {
    await conclude(env, callSid, log, { path: progress.path, user_id: progress.user, outcome: "lost_round", enforcement: "undecided" });
    return after(progress.path, "");
  }
  const verdict = judge(row);
  await log("identity.round", { path: progress.path, round, outcome: verdict.outcome, ...scoreDetail(row), ...scoreTiming(row, wait_ms) });
  // Enrollment keeps a collected answer's embedding until the Voice Reference is computed.
  if (verdict.outcome !== "collected") {
    await env.DB.prepare("DELETE FROM challenge_rounds WHERE call_sid = ? AND round = ?").bind(callSid, round).run();
  }
  const next = (spoken: string, judged = progress.judged, passed = progress.passed) =>
    nextRound(env, ctx, url, callSid, account, log, round, { ...progress, judged, passed }, subject, spoken);

  if (verdict.outcome === "unscored") {
    // A verifier failure ends the check (shadow: the Home call connects); a slow score earns another phrase.
    if (verdict.error) {
      if (ritual === "enrollment") return finishEnrollment(env, callSid, log, progress, "", verdict.error);
      await conclude(env, callSid, log, { path: progress.path, user_id: subject.id, outcome: "unscored", error: verdict.error,
        enforcement: "undecided", judged: progress.judged, passed: progress.passed });
      return after(progress.path, progress.path === "home" ? "I couldn't check your voice this time." : "Voice checks aren't working right now. Goodbye.");
    }
    return next("I couldn't score that one. Here's another.");
  }
  if (verdict.outcome === "not_enough_speech") return next(ritual === "enrollment" ? "I didn't get enough of that one. Here's another." : "Not enough to judge. Here's another.");
  if (verdict.outcome === "collected") {
    if (progress.judged + 1 >= ENROLL_ANSWERS) return finishEnrollment(env, callSid, log, { ...progress, judged: progress.judged + 1 }, "");
    return next("", progress.judged + 1);
  }
  const passed = progress.passed + (verdict.outcome === "no_match" ? 0 : 1);
  if (progress.path === "home") {
    // Teach once, then go terse: Access Control state in D1, never model Memory.
    await env.DB.prepare("UPDATE users SET challenges_completed = challenges_completed + 1 WHERE id = ?").bind(subject.id).run();
    await conclude(env, callSid, log, { path: "home", user_id: subject.id, outcome: verdict.outcome, level: verdict.spoken,
      enforcement: verdict.outcome === "no_match" ? "require_roaming" : "admit" });
    return after("home", verdict.spoken);
  }
  const judged = progress.judged + 1;
  // Admission enforces only an attempt it recorded, so a call that began in shadow mode stays
  // shadow if the flag turns on mid-call.
  if (roamingAdmits(env) && await roamingAttempt(env, callSid, subject.id)) {
    // The first unmatched voice fails the attempt, since no later round could change the
    // decision and each would be one more level to tune an impersonation against.
    // Every judged round must have passed; by construction it has, since a failure ends here.
    if (verdict.outcome === "no_match" || passed !== judged) {
      await env.DB.prepare("UPDATE roaming_attempts SET failures = failures + 1 WHERE call_sid = ? AND user_id = ?")
        .bind(callSid, subject.id).run();
      await conclude(env, callSid, log, { path: "roaming", user_id: subject.id, outcome: "failed", enforcement: "deny", judged, passed }, false);
      return after("roaming", `${verdict.spoken} I can't let you in as ${subject.handle} this time. Goodbye.`);
    }
    if (judged < ROAMING_CHALLENGES) return next(verdict.spoken, judged, passed);
    // Failures by other calls since this attempt began still pause it.
    const paused = await roamingPause(env, subject.id, String(form.get("From") ?? ""));
    if (paused) {
      await conclude(env, callSid, log, { path: "roaming", user_id: subject.id, outcome: "paused", paused, enforcement: "deny", judged, passed }, false);
      return after("roaming", `${verdict.spoken} Calls as ${subject.handle} from other phones are paused right now. Goodbye.`);
    }
    return admitRoaming(env, callSid, log, subject, verdict.spoken, judged);
  }
  if (judged < ROAMING_CHALLENGES) return next(verdict.spoken, judged, passed);
  await conclude(env, callSid, log, { path: "roaming", user_id: subject.id, outcome: passed === ROAMING_CHALLENGES ? "passed" : "failed",
    enforcement: passed === ROAMING_CHALLENGES ? "admit_roaming" : "deny", judged, passed });
  return after("roaming", `${verdict.spoken} Calling as yourself from another phone is coming soon. Until then, call from your home phone. Goodbye.`);
}

/**
 * Three passed challenges: the call becomes the named User's, with a Home call's access, and
 * the Home is unchanged. The User is written before the admission, so a failure between the two
 * leaves an unadmitted call, which /voice resolves by its number as before.
 */
async function admitRoaming(env: Env, callSid: string, log: Log, subject: Subject, spoken: string, judged: number): Promise<string> {
  await env.DB.batch([
    env.DB.prepare("UPDATE voice_calls SET user_id = ? WHERE call_sid = ?").bind(subject.id, callSid),
    env.DB.prepare("UPDATE roaming_attempts SET admitted_at = unixepoch() WHERE call_sid = ? AND user_id = ?").bind(callSid, subject.id),
  ]);
  await conclude(env, callSid, log, { path: "roaming", user_id: subject.id, outcome: "passed", enforcement: "admit_roaming", judged,
    passed: judged, admitted: true }, false);
  return Say(`${spoken} Welcome, ${subject.handle}.`) + CONNECT;
}

// --- enrollment ---------------------------------------------------------

/**
 * The Voice Reference from the collected answers' embeddings: the mean of the L2-normalized
 * embeddings, re-normalized, over answers with enough speech, all in one embedding space.
 */
export function meanEmbedding(embeddings: number[][]): number[] {
  const unit = (vector: number[]) => {
    const norm = Math.hypot(...vector);
    return norm > 0 ? vector.map((value) => value / norm) : vector;
  };
  const sum = new Array<number>(embeddings[0].length).fill(0);
  for (const embedding of embeddings.map(unit)) embedding.forEach((value, i) => { sum[i] += value; });
  return unit(sum);
}

/**
 * The enrollment answers a call collected for a User, with enough speech, in one embedding space:
 * the one most answers share (a verifier deployed mid-call could split them). Re-enrollment
 * (reenrollment.ts) computes its replacement from the same answers.
 */
export async function collectedAnswers(env: Env, callSid: string, userId: number): Promise<{ space: string; embeddings: number[][] }> {
  const { results } = await env.DB.prepare(
    `SELECT embedding_space, embedding FROM challenge_rounds
      WHERE call_sid = ? AND ritual = 'enrollment' AND user_id = ? AND state = 'scored' AND embedding IS NOT NULL AND speech_seconds >= ?`,
  ).bind(callSid, userId, MIN_SPEECH_SECONDS).all<{ embedding_space: string; embedding: string }>();
  const spaces = new Map<string, number[][]>();
  for (const row of results ?? []) spaces.set(row.embedding_space, [...spaces.get(row.embedding_space) ?? [], JSON.parse(row.embedding)]);
  const [space, embeddings] = [...spaces].sort((a, b) => b[1].length - a[1].length)[0] ?? ["", []];
  return { space, embeddings };
}

async function finishEnrollment(env: Env, callSid: string, log: Log, progress: Progress, spoken: string, error?: string): Promise<string> {
  const { space, embeddings } = await collectedAnswers(env, callSid, progress.user);
  const lead = spoken ? `${spoken} ` : "";
  if (embeddings.length < MIN_ENROLL_ANSWERS) {
    await conclude(env, callSid, log, { path: progress.path, user_id: progress.user, outcome: "enrollment_failed", answers: embeddings.length,
      ...(error ? { error } : {}), enforcement: "undecided" });
    return after(progress.path, `${lead}I couldn't hear enough to learn your voice. I'll ask again next call.`);
  }
  // Never replaces a Voice Reference: that is Re-enrollment, which the User starts.
  const stored = await env.DB.prepare(
    `INSERT INTO voice_references (user_id, embedding_space, embedding, enrolled_at) VALUES (?, ?, ?, unixepoch())
     ON CONFLICT(user_id) DO NOTHING`,
  ).bind(progress.user, space, JSON.stringify(meanEmbedding(embeddings))).run();
  await conclude(env, callSid, log, { path: progress.path, user_id: progress.user, outcome: stored.meta.changes ? "enrolled" : "already_enrolled",
    answers: embeddings.length, enforcement: "admit", enrolled_now: Boolean(stored.meta.changes) });
  return after(progress.path, `${lead}Thanks. Your voice is enrolled.`);
}

/** /voice/identity/stream/<capability>: one take's caller-only Media Stream (challenge_rounds.ts). */
export const handleIdentityStream = (req: Request, env: Env, ctx: ExecutionContext, capability: string) =>
  handleChallengeStream(req, env, ctx, capability, "identity");
