/**
 * Dogwalk Cloudflare Worker
 *
 * TwiML registration, Daytona sandbox lifecycle, Durable Object voice/ACP
 * coordination, and the Access-protected read-only Diagnostics surface.
 * See PSTN_CLOUDFLARE.md for the deployed architecture.
 */

import { createRemoteJWKSet, jwtVerify } from "jose";
import { ADMIN_HTML } from "./admin";
import { OPERATOR_TIMING_HTML } from "./operator_timing";
import { operatorCallContent } from "./operator_content";
import { formatTraceparent, isTraceContext, newRootContext } from "./trace_context";
import { type ActivityRow, type CallRow, logsRequest, openRecordedSpanIds, producerFields, projectLogs, projectSpans, tracesRequest } from "./otlp";
import { observeTwilio, processTwilioJobs } from "./provider_timing";
import { LANDING_HTML } from "./landing";
import { PAIR_HTML } from "./pair_page";
import { handleSounds } from "./sounds_page";
import {
  DECODE_FAILURE_KEEP_ROWS, DECODE_FAILURE_MAX_BYTES, describeBrowser, describePlace, sha256Hex, splitCredential,
} from "./pairing";
import type { Env } from "./types";
import { handleTerminalAssets, TERMINAL_HTML } from "./terminal_page";
import { TerminalRelay } from "./terminal_relay";
import { INVITATION_STATUS_SQL, redemptionStatements, USABLE_FOR_PHONE_SQL } from "./invitations";
import { availableHandles } from "./handles";
import { frontendInstructions, VoiceSession } from "./voice_session";
import { DEFAULT_THEME, THEMES } from "./themes";
import { handleVoiceVerify, warmSpeaker } from "./voice_verify";
import { handlePracticeHeard, handlePracticeResult, handlePracticeStream, practiceReceived, practiceStreamStop, practiceTone, startPractice } from "./challenge_practice";
import { handleReenrollmentHeard, handleReenrollmentResult, reenrollmentStreamStop, startReenrollment } from "./reenrollment";
import {
  type Exits, handleIdentityHeard, handleIdentityResult, handleIdentityStream, handleIdentityWho, identityStreamStop, startHomeCheck,
  startRegistrationEnrollment,
} from "./identity_check";
import { asksToDonate, donationConsent, donationTone, handleDonationConsent, handleDonationRound, handleDonationStream } from "./voice_donation";
export { TerminalRelay, VoiceSession };

// --- env ----------------------------------------------------------------

// --- Twilio signature validation ----------------------------------------

/**
 * Validate the Twilio signature header against the request body and URL.
 * Twilio signs with HMAC-SHA1 of `url + params` (sorted, concatenated) using
 * the auth token. The signature is base64.
 *
 * Twilio signs the exact public URL you configure on the phone number. To make
 * validation deterministic and independent of which hostname actually served
 * the request (the custom domain dogwalk.tools vs the workers.dev hostname),
 * we reconstruct the signed URL from PUBLIC_ORIGIN + the request path when it
 * is set. Configure the Twilio webhook to match PUBLIC_ORIGIN exactly:
 *   Voice: A call comes in     -> POST https://dogwalk.tools/voice
 *   Voice: Call status changes -> POST https://dogwalk.tools/voice/status
 * When PUBLIC_ORIGIN is unset (pure local dev) we fall back to the request's
 * own origin. A Media Stream's WebSocket upgrade is signed over its wss URL,
 * which the Worker sees as https, so that caller passes the scheme.
 *
 * Reference: https://www.twilio.com/docs/usage/webhooks/webhooks-security
 */
async function twilioSignatureValid(
  req: Request,
  env: Env,
  signedScheme?: string,
): Promise<{ valid: boolean; url: string }> {
  const authToken = env.TWILIO_AUTH_TOKEN;
  const sigHeader = req.headers.get("x-twilio-signature") ?? "";
  if (!sigHeader || !authToken) return { valid: false, url: "" };

  // Prefer the configured public origin's host so the signed URL matches what
  // Twilio was told to call, regardless of the serving hostname. Fall back to
  // the request's own host when PUBLIC_ORIGIN is not configured (pure local
  // dev).
  const url = new URL(req.url);
  const configuredHost = env.PUBLIC_ORIGIN ? new URL(env.PUBLIC_ORIGIN).host : url.host;
  const scheme = signedScheme ?? url.protocol.replace(":", "");
  const signedUrl = `${scheme}://${configuredHost}${url.pathname}${url.search}`;

  // Twilio sends POST as application/x-www-form-urlencoded for /voice.
  // Clone before formData() consumes the body.
  const params: [string, string][] = [];
  if (req.method === "POST" && req.headers.get("content-type")?.includes("application/x-www-form-urlencoded")) {
    const form = await req.clone().formData();
    for (const [k, v] of form.entries()) {
      if (typeof v === "string") params.push([k, v]);
    }
  }
  params.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  let s = signedUrl;
  for (const [k, v] of params) s += k + (v ?? "");

  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(authToken),
    { name: "HMAC", hash: "SHA-1" },
    false,
    ["sign"],
  );
  const mac = new Uint8Array(
    await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(s)),
  );
  // base64-encode the mac
  let macB64 = "";
  for (const b of mac) macB64 += String.fromCharCode(b);
  macB64 = btoa(macB64);

  // constant-time-ish comparison
  const expected = macB64;
  if (expected.length !== sigHeader.length) {
    return { valid: false, url: signedUrl };
  }
  let diff = 0;
  for (let i = 0; i < expected.length; i++) {
    diff |= expected.charCodeAt(i) ^ sigHeader.charCodeAt(i);
  }
  return { valid: diff === 0, url: signedUrl };
}

// --- TwiML helpers ------------------------------------------------------

const tw = (s: string) => `<Response>${s}</Response>`;

const Say = (text: string, opts: { voice?: string } = {}) =>
  `<Say voice="${opts.voice ?? "Polly.Joanna"}">${escapeXml(text)}</Say>`;
const Pause = (seconds: number) => `<Pause length="${seconds}"/>`;
const Redirect = (path: string) => `<Redirect method="POST">${escapeXml(path)}</Redirect>`;
const Hangup = () => `<Hangup/>`;
const DialSip = (uri: string) =>
  `<Dial action="/voice/after-live" method="POST"><Sip statusCallback="/voice/provider-status" statusCallbackMethod="POST" statusCallbackEvent="initiated ringing answered completed">${escapeXml(uri)}</Sip></Dial>`;

const GatherSpeech = (
  promptText: string,
  actionPath: string,
  attempts: number,
  hints: string[],
) => {
  const hintAttribute = hints.length > 0
    ? ` hints="${escapeXml(hints.slice(0, 500).join(","))}"`
    : "";
  return `<Gather input="speech" speechTimeout="auto" actionOnEmptyResult="true" action="${actionPath}?attempts=${attempts}" method="POST"${hintAttribute} language="en-US">${Say(promptText)}</Gather>`;
};

const GatherDtmf = (promptText: string, actionPath: string) =>
  `<Gather input="dtmf" numDigits="1" timeout="8" action="${escapeXml(actionPath)}" method="POST">${Say(promptText)}</Gather>`;

const escapeXml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const textEncoder = new TextEncoder();

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function decodeBase64(value: string): Uint8Array {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(normalized + "=".repeat((4 - normalized.length % 4) % 4));
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function hmacSha256(secret: Uint8Array | string, content: string): Promise<Uint8Array> {
  const source = typeof secret === "string" ? textEncoder.encode(secret) : secret;
  const material = new Uint8Array(source.byteLength);
  material.set(source);
  const key = await crypto.subtle.importKey(
    "raw",
    material,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, textEncoder.encode(content)));
}

function equalBytes(left: Uint8Array, right: Uint8Array): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index++) difference |= left[index] ^ right[index];
  return difference === 0;
}

async function sipContextCapability(env: Env, callSid: string): Promise<string> {
  const expiresAt = Math.floor(Date.now() / 1000) + 5 * 60;
  const content = `${callSid}.${expiresAt}`;
  return `${content}.${base64Url(await hmacSha256(env.DOGWALK_IDENTITY_SECRET, `sip-context-v1:${content}`))}`;
}

async function verifySipContext(
  env: Env,
  token: string,
): Promise<{ callSid: string } | null> {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [callSid, expiresRaw, signature] = parts;
  const expiresAt = Number(expiresRaw);
  if (!/^CA[A-Za-z0-9_]{2,64}$/.test(callSid)) return null;
  if (!Number.isInteger(expiresAt) || expiresAt < Math.floor(Date.now() / 1000)) return null;
  const content = `${callSid}.${expiresRaw}`;
  const expected = await hmacSha256(env.DOGWALK_IDENTITY_SECRET, `sip-context-v1:${content}`);
  let actual: Uint8Array;
  try {
    actual = decodeBase64(signature);
  } catch {
    return null;
  }
  return equalBytes(expected, actual) ? { callSid } : null;
}

async function openAiWebhookValid(req: Request, env: Env, body: string): Promise<boolean> {
  if (!env.OPENAI_WEBHOOK_SECRET) return false;
  const webhookId = req.headers.get("webhook-id") ?? "";
  const timestampRaw = req.headers.get("webhook-timestamp") ?? "";
  const timestamp = Number(timestampRaw);
  if (!webhookId || !Number.isInteger(timestamp) || Math.abs(Date.now() / 1000 - timestamp) > 5 * 60) return false;
  const encodedSecret = env.OPENAI_WEBHOOK_SECRET.replace(/^whsec_/, "");
  let secret: Uint8Array;
  try {
    secret = decodeBase64(encodedSecret);
  } catch {
    return false;
  }
  const expected = await hmacSha256(secret, `${webhookId}.${timestampRaw}.${body}`);
  const signatures = (req.headers.get("webhook-signature") ?? "").split(/\s+/);
  return signatures.some((candidate) => {
    const [version, encoded] = candidate.split(",", 2);
    if (version !== "v1" || !encoded) return false;
    try {
      return equalBytes(expected, decodeBase64(encoded));
    } catch {
      return false;
    }
  });
}

// --- ritual constants ---------------------------------------------------

const MAX_ATTEMPTS = 3;
const WORDS_REQUIRED = 3;
const MENU_HINTS = ["status", "hang up"];

/** D1 lookup result for an inbound caller. */
/** Normalize ASR result: lowercase, remove punctuation, and split into tokens. */
function normalizeSpeech(raw: string | null | undefined): string[] {
  if (!raw) return [];
  return raw
    .toLowerCase()
    .replace(/[^a-z\s]/g, " ")
    .trim()
    .split(/\s+/)
    .filter((t) => t.length > 0);
}

/** Return the canonical invite phrase only for an exact normalized match. */
function matchInviteCode(
  spoken: string[],
  candidates: { code_words: string }[],
): string | null {
  if (spoken.length !== WORDS_REQUIRED) return null;
  const target = spoken.join(" ");
  for (const c of candidates) {
    if (c.code_words === target) return c.code_words;
  }
  return null;
}

const normalizePhone = (raw: FormDataEntryValue | null): string | null => {
  const phone = String(raw ?? "").trim();
  return /^\+[1-9]\d{7,14}$/.test(phone) ? phone : null;
};

const attemptsFromRequest = (req: Request): number => {
  const parsed = Number(new URL(req.url).searchParams.get("attempts") ?? "1");
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1;
};

// Invite Codes this phone could redeem now; an Invitation bound to another number is not one.
async function usableInviteCodes(env: Env, phone: string): Promise<{ code_words: string }[]> {
  const now = Math.floor(Date.now() / 1000);
  const candidates = await env.DB.prepare(
    `SELECT i.code_words
       FROM invite_codes i
      WHERE ${USABLE_FOR_PHONE_SQL}
      ORDER BY i.code_words`,
  ).bind(now, phone).all<{ code_words: string }>();
  return candidates.results ?? [];
}

// --- Agent Hosting ------------------------------------------------------

const DAYTONA_LABEL_MANAGED = "dogwalk.dev/managed-by";
const DAYTONA_LABEL_IDENTITY = "dogwalk.dev/identity";

interface DaytonaSandbox {
  id: string;
  name: string;
  state: string;
  desiredState?: string;
  errorReason?: string | null;
  toolboxProxyUrl?: string;
}

/** A User as Access Control resolves one for a call: by Home today, by Identity Check later. */
interface User {
  id: number;
  identity_hash: string;
  handle: string | null;
}

interface SandboxAssignment {
  user_id: number;
  provider_id: string | null;
  state: string;
  error: string | null;
  provisioning_started_at: number;
  last_checked_at: number | null;
}

const daytonaBase = (env: Env) => (env.DAYTONA_API_BASE ?? "https://app.daytona.io/api").replace(/\/$/, "");

class DaytonaApiError extends Error {
  constructor(readonly status: number, detail: string) {
    super(`Daytona ${status}: ${detail}`);
  }
}

async function daytonaFetch<T>(env: Env, path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${daytonaBase(env)}${path}`, {
    ...init,
    headers: {
      authorization: `Bearer ${env.DAYTONA_API_KEY}`,
      ...(init.body ? { "content-type": "application/json" } : {}),
      ...init.headers,
    },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new DaytonaApiError(response.status, (await response.text()).slice(0, 300));
  return response.json<T>();
}

// An opaque, stable key for a new User's Durable Objects and provider label. It derives from
// nothing, so re-homing or roaming never changes which Sandbox and history a User reaches.
function newIdentityHash(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(32)), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

const USER_COLUMNS = "u.id, u.identity_hash, u.handle";
const HOME_TTL_SECONDS = 90 * 86400;

/** The User whose unexpired Home this number is. */
async function homeUser(env: Env, phone: string): Promise<User | null> {
  return env.DB.prepare(
    `SELECT ${USER_COLUMNS} FROM users u
      WHERE u.home_phone = ? AND u.reaped_at IS NULL AND u.home_used_at > unixepoch() - ?`,
  ).bind(phone, HOME_TTL_SECONDS).first<User>();
}

/** The User this Voice Call was resolved to when it entered. */
async function callUser(env: Env, callSid: string): Promise<User | null> {
  return env.DB.prepare(
    `SELECT ${USER_COLUMNS} FROM voice_calls c JOIN users u ON u.id = c.user_id
      WHERE c.call_sid = ? AND u.reaped_at IS NULL`,
  ).bind(callSid).first<User>();
}

async function getAssignment(env: Env, user: User): Promise<SandboxAssignment | null> {
  return env.DB.prepare(
    `SELECT user_id, provider_id, state, error, provisioning_started_at, last_checked_at
       FROM sandbox_assignments WHERE user_id = ?`,
  ).bind(user.id).first<SandboxAssignment>();
}

/**
 * Project the provider's view of a Sandbox onto the assignment, only while the assignment still
 * names `expected` (null when adopting or creating one). False means a concurrent replacement,
 * reap, or provisioning won, and `sandbox` is no longer this User's.
 */
async function updateAssignment(env: Env, user: User, sandbox: DaytonaSandbox, expected: string | null): Promise<boolean> {
  const result = await env.DB.prepare(
    `UPDATE sandbox_assignments
        SET provider_id = ?, state = ?, error = ?, last_checked_at = ?
      WHERE user_id = ? AND provider_id IS ?`,
  ).bind(
    sandbox.id,
    sandbox.state,
    sandbox.errorReason ?? null,
    Math.floor(Date.now() / 1000),
    user.id,
    expected,
  ).run();
  return result.meta.changes === 1;
}

async function findDaytonaSandboxes(env: Env, hash: string): Promise<DaytonaSandbox[]> {
  const labels = JSON.stringify({
    [DAYTONA_LABEL_MANAGED]: "dogwalk",
    [DAYTONA_LABEL_IDENTITY]: `identity-v1:${hash}`,
  });
  const query = new URLSearchParams({ labels, limit: "3" });
  const result = await daytonaFetch<{ items: DaytonaSandbox[] }>(env, `/sandbox?${query}`);
  return result.items;
}

async function ensureSandboxAssignment(env: Env, user: User): Promise<SandboxAssignment> {
  const hash = user.identity_hash;
  const now = Math.floor(Date.now() / 1000);
  const inserted = await env.DB.prepare(
    `INSERT OR IGNORE INTO sandbox_assignments (user_id, state, provisioning_started_at)
     VALUES (?, 'provisioning', ?)`,
  ).bind(user.id, now).run();
  let assignment = await getAssignment(env, user);
  if (!assignment) throw new Error("sandbox assignment insert failed");
  if (assignment.provider_id) return assignment;

  // Never adopt a reaped Sandbox, pending deletion or already deleted (Daytona's label listing is
  // eventually consistent and can show a deleted Sandbox as running for a while), nor one the
  // provider lists as destroying after a replacement.
  const pending = await env.DB.prepare("SELECT provider_id FROM sandbox_deletions WHERE user_id = ?")
    .bind(user.id).all<{ provider_id: string }>();
  const doomed = new Set(pending.results.map((row) => row.provider_id));
  const discovered = (await findDaytonaSandboxes(env, hash))
    .filter((sandbox) => !doomed.has(sandbox.id))
    .filter((sandbox) => ![sandbox.state, sandbox.desiredState].some((state) => state === "destroying" || state === "destroyed"));
  if (discovered.length > 1) {
    await env.DB.prepare(
      "UPDATE sandbox_assignments SET state = 'conflict', error = ? WHERE user_id = ?",
    ).bind("multiple matching Daytona sandboxes", user.id).run();
    throw new Error("multiple matching Daytona sandboxes");
  }
  if (discovered.length === 1) {
    await updateAssignment(env, user, discovered[0], null);
    return (await getAssignment(env, user))!;
  }

  let ownsProvisioning = inserted.meta.changes === 1;
  if (!ownsProvisioning && now - assignment.provisioning_started_at >= 30) {
    const claimed = await env.DB.prepare(
      `UPDATE sandbox_assignments SET provisioning_started_at = ?, state = 'provisioning', error = NULL
        WHERE user_id = ? AND provider_id IS NULL AND provisioning_started_at = ?`,
    ).bind(now, user.id, assignment.provisioning_started_at).run();
    ownsProvisioning = claimed.meta.changes === 1;
  }
  if (!ownsProvisioning) return assignment;

  if (!env.DAYTONA_SNAPSHOT) throw new Error("Daytona snapshot is not configured");

  const sandbox = await daytonaFetch<DaytonaSandbox>(env, "/sandbox", {
    method: "POST",
    body: JSON.stringify({
      name: `dogwalk-${hash.slice(0, 12)}-${crypto.randomUUID().slice(0, 8)}`,
      snapshot: env.DAYTONA_SNAPSHOT,
      // A Daytona Secret name, not a value: the egress proxy substitutes the key into
      // HTTPS headers to allowlisted hosts, so the sandbox only ever holds a placeholder.
      ...(env.DAYTONA_AGENT_SECRET ? { secrets: [{ OPENAI_API_KEY: env.DAYTONA_AGENT_SECRET }] } : {}),
      labels: {
        [DAYTONA_LABEL_MANAGED]: "dogwalk",
        [DAYTONA_LABEL_IDENTITY]: `identity-v1:${hash}`,
      },
      public: false,
      autoStopInterval: 15,
      autoArchiveInterval: 10080,
      autoDeleteInterval: -1,
    }),
  });
  await updateAssignment(env, user, sandbox, null);
  return (await getAssignment(env, user))!;
}

async function getDaytonaSandbox(env: Env, providerId: string): Promise<DaytonaSandbox> {
  return daytonaFetch<DaytonaSandbox>(env, `/sandbox/${encodeURIComponent(providerId)}`);
}

async function executeInSandbox(
  env: Env,
  sandbox: DaytonaSandbox,
  command: string,
  timeout = 10,
): Promise<{ result: string; exitCode: number }> {
  const proxy = (sandbox.toolboxProxyUrl ?? "https://proxy.app.daytona.io/toolbox").replace(/\/$/, "");
  const response = await fetch(`${proxy}/${encodeURIComponent(sandbox.id)}/process/execute`, {
    method: "POST",
    headers: { authorization: `Bearer ${env.DAYTONA_API_KEY}`, "content-type": "application/json" },
    body: JSON.stringify({ command, timeout, envs: { LC_ALL: "C" } }),
    signal: AbortSignal.timeout((timeout + 2) * 1000),
  });
  if (!response.ok) throw new Error(`Daytona toolbox ${response.status}`);
  return response.json<{ result: string; exitCode: number }>();
}

async function resolveSandbox(
  env: Env,
  user: User,
  replaceMissing = true,
  attempt = 0,
): Promise<{ assignment: SandboxAssignment; sandbox?: DaytonaSandbox }> {
  if (attempt > 3) throw new Error("sandbox assignment kept changing");
  const assignment = await ensureSandboxAssignment(env, user);
  if (!assignment.provider_id) return { assignment };
  const now = Math.floor(Date.now() / 1000);
  if (
    assignment.last_checked_at &&
    now - assignment.last_checked_at < 3 &&
    ["creating", "starting", "restoring"].includes(assignment.state)
  ) {
    return { assignment };
  }
  let sandbox: DaytonaSandbox;
  try {
    sandbox = await getDaytonaSandbox(env, assignment.provider_id);
  } catch (error) {
    if (!(error instanceof DaytonaApiError) || error.status !== 404 || !replaceMissing) throw error;
    const cleared = await env.DB.prepare(
      `UPDATE sandbox_assignments
          SET provider_id = NULL, state = 'missing', error = NULL,
              provisioning_started_at = 0, last_checked_at = ?
        WHERE user_id = ? AND provider_id = ?`,
    ).bind(now, user.id, assignment.provider_id).run();
    if (cleared.meta.changes === 1) {
      await audit(env, "sandbox.missing", null, null, { user_id: user.id, provider_id: assignment.provider_id });
    }
    return resolveSandbox(env, user, false, attempt + 1);
  }
  // A reap or replacement that won meanwhile makes this Sandbox someone else's past: look again.
  if (!(await updateAssignment(env, user, sandbox, assignment.provider_id))) {
    return resolveSandbox(env, user, replaceMissing, attempt + 1);
  }
  return { assignment: (await getAssignment(env, user))!, sandbox };
}

// --- Sandbox reaping ----------------------------------------------------

// Identity and Hosting Lifecycle (DOMAIN.md): a Sandbox is reaped once its User has made no
// Voice Call for SANDBOX_REAP_AGE_DAYS. Anything but SANDBOX_REAP_MODE=delete is a dry run that
// only audits sandbox.reap.candidate.
//
// The daily pass is the only party that ever deletes a Sandbox. Reaping takes it out of service
// at once: one transaction, which rechecks idleness, records it in sandbox_deletions and clears
// the assignment, so the User's next call provisions a fresh Sandbox by the ordinary path and
// discovery never adopts the old one. Deleting it at the provider is then cleanup the pass works
// through, this pass and later ones, retrying failures with backoff. The row stays afterwards as
// a tombstone (Daytona's label listing can still show a deleted Sandbox) and as the notice the
// User's next Opening consumes (VoiceSession.sinceLastCall).
const SANDBOX_REAP_CRON = "17 10 * * *";
const SANDBOX_REAP_BATCH = 10;
const SANDBOX_DELETE_BATCH = 20;
// Failed deletions are retried after 1, 2, 4, 8, then 16 days; at this many failures the
// operator is told (sandbox.reap.stuck), and retries go on.
const REAP_STUCK_ATTEMPTS = 3;
// A call row without an end is in progress only while it is younger than this; an older one
// lost its status report (Twilio ends any call within four hours).
const CALL_IN_PROGRESS_SECONDS = 86400;

interface ReapCandidate {
  user_id: number;
  provider_id: string;
  last_call_at: number;
}

// No Voice Call by this User started at or after the cutoff (?1), and none is in progress. A call
// from the User's Home counts before /voice has linked it to the User.
const userIdleSince = (user: string) => `NOT EXISTS (
  SELECT 1 FROM voice_calls c
   WHERE (c.user_id = ${user} OR (c.user_id IS NULL AND c.phone_number = (SELECT home_phone FROM users WHERE id = ${user})))
     AND (c.started_at >= ?1 OR (c.ended_at IS NULL AND c.started_at >= unixepoch() - ${CALL_IN_PROGRESS_SECONDS})))`;

async function reapSandboxes(env: Env): Promise<void> {
  const days = Number(env.SANDBOX_REAP_AGE_DAYS ?? "30");
  if (!Number.isFinite(days) || days < 1) {
    console.error(`SANDBOX_REAP_AGE_DAYS must be at least 1; reaping skipped (${env.SANDBOX_REAP_AGE_DAYS})`);
    return;
  }
  const mode = env.SANDBOX_REAP_MODE === "delete" ? "delete" : "dry-run";
  const now = Math.floor(Date.now() / 1000);
  const cutoff = now - Math.round(days * 86400);
  const { results } = await env.DB.prepare(
    `SELECT s.user_id, s.provider_id,
            COALESCE((SELECT MAX(c.started_at) FROM voice_calls c WHERE c.user_id = s.user_id), s.created_at) AS last_call_at
       FROM sandbox_assignments s
      WHERE s.provider_id IS NOT NULL AND s.created_at < ?1 AND ${userIdleSince("s.user_id")}
      ORDER BY last_call_at LIMIT ?2`,
  ).bind(cutoff, SANDBOX_REAP_BATCH).all<ReapCandidate>();
  for (const candidate of results) {
    const detail = {
      user_id: candidate.user_id, provider_id: candidate.provider_id, mode,
      last_call_at: candidate.last_call_at, idle_days: Math.floor((now - candidate.last_call_at) / 86400),
    };
    if (mode === "dry-run") {
      await audit(env, "sandbox.reap.candidate", null, null, detail);
      continue;
    }
    // Both statements see the same idleness: a call arriving now either blocks the reap or finds
    // the assignment already cleared and provisions fresh.
    const reap = `user_id = ?2 AND provider_id = ?3 AND ${userIdleSince("?2")}`;
    const [, cleared] = await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO sandbox_deletions (provider_id, user_id, reaped_at, next_attempt_at)
         SELECT ?3, ?2, unixepoch(), unixepoch() WHERE EXISTS (SELECT 1 FROM sandbox_assignments WHERE ${reap})`,
      ).bind(cutoff, candidate.user_id, candidate.provider_id),
      env.DB.prepare(
        `UPDATE sandbox_assignments
            SET provider_id = NULL, state = 'reaped', error = NULL, provisioning_started_at = 0, last_checked_at = unixepoch()
          WHERE ${reap}`,
      ).bind(cutoff, candidate.user_id, candidate.provider_id),
    ]);
    if (cleared.meta.changes === 1) await audit(env, "sandbox.reap.started", null, null, detail);
  }
  if (mode === "delete") await deleteReapedSandboxes(env);
}

interface SandboxDeletion {
  provider_id: string;
  user_id: number;
  attempts: number;
}

// Delete reaped Sandboxes at the provider, oldest due first. Nothing here affects any User: the
// Sandboxes are already out of service, so a failure only delays cleanup.
async function deleteReapedSandboxes(env: Env): Promise<void> {
  const { results } = await env.DB.prepare(
    `SELECT provider_id, user_id, attempts FROM sandbox_deletions
      WHERE deleted_at IS NULL AND next_attempt_at <= unixepoch() ORDER BY next_attempt_at LIMIT ?`,
  ).bind(SANDBOX_DELETE_BATCH).all<SandboxDeletion>();
  for (const row of results) {
    const detail = { user_id: row.user_id, provider_id: row.provider_id };
    let alreadyGone = false;
    try {
      await daytonaFetch(env, `/sandbox/${encodeURIComponent(row.provider_id)}`, { method: "DELETE" });
    } catch (error) {
      if (error instanceof DaytonaApiError && error.status === 404) {
        alreadyGone = true;
      } else {
        const message = (error instanceof Error ? error.message : String(error)).slice(0, 200);
        const attempts = row.attempts + 1;
        // Due at the daily pass 1, 2, 4, 8, then 16 days on (an hour early, so the pass finds it due).
        const delay = 86400 * (1 << Math.min(attempts - 1, 4)) - 3600;
        await env.DB.prepare(
          `UPDATE sandbox_deletions SET attempts = ?, last_error = ?, next_attempt_at = unixepoch() + ?
            WHERE provider_id = ? AND deleted_at IS NULL`,
        ).bind(attempts, message, delay, row.provider_id).run();
        await audit(env, "sandbox.reap.failed", null, null, { ...detail, attempts, message });
        if (attempts === REAP_STUCK_ATTEMPTS) await audit(env, "sandbox.reap.stuck", null, null, { ...detail, attempts, message });
        continue;
      }
    }
    const done = await env.DB.prepare(
      "UPDATE sandbox_deletions SET deleted_at = unixepoch(), last_error = NULL WHERE provider_id = ? AND deleted_at IS NULL",
    ).bind(row.provider_id).run();
    if (done.meta.changes === 1) {
      await audit(env, "sandbox.reaped", null, null, { ...detail, attempts: row.attempts, already_gone: alreadyGone });
    }
  }
}

// --- Diagnostics --------------------------------------------------------

const accessKeySets = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

async function adminAuthorized(req: Request, env: Env): Promise<boolean> {
  const authorization = req.headers.get("authorization");
  if (env.ADMIN_PASSWORD && authorization?.startsWith("Basic ")) {
    try {
      const decoded = atob(authorization.slice(6));
      const separator = decoded.indexOf(":");
      const username = decoded.slice(0, separator);
      const password = decoded.slice(separator + 1);
      if (username === "adam" && constantTimeEqual(password, env.ADMIN_PASSWORD)) return true;
    } catch {
      // Fall through to Access JWT validation.
    }
  }

  const teamDomain = env.ACCESS_TEAM_DOMAIN?.replace(/\/$/, "");
  if (!teamDomain || !env.ACCESS_AUD) return false;
  const token = req.headers.get("cf-access-jwt-assertion");
  if (!token) return false;
  try {
    let keySet = accessKeySets.get(teamDomain);
    if (!keySet) {
      keySet = createRemoteJWKSet(new URL(`${teamDomain}/cdn-cgi/access/certs`));
      accessKeySets.set(teamDomain, keySet);
    }
    await jwtVerify(token, keySet, { issuer: teamDomain, audience: env.ACCESS_AUD });
    return true;
  } catch {
    return false;
  }
}

function constantTimeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index++) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

const jsonResponse = (value: unknown, status = 200, extra: Record<string, string> = {}) => new Response(JSON.stringify(value), {
  status,
  headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...extra },
});

async function diagnosticsSnapshot(env: Env, verbose: boolean): Promise<Record<string, unknown>> {
  // One row per User (its Registration, Home, and Sandbox Assignment). Stable, non-capability
  // diagnostic ids preserve graph identity when masked numbers collide or a Home is gone.
  const registrationResult = await env.DB.prepare(
    `SELECT 'user-' || u.id AS diagnostic_id, u.id AS user_id, u.handle, u.home_phone AS phone_number,
            u.home_used_at, u.registered_at, u.last_seen_at, u.reaped_at, u.identity_hash,
            s.provider_id, s.state, s.error, s.created_at AS sandbox_created_at, s.last_checked_at,
            EXISTS (SELECT 1 FROM voice_references vr WHERE vr.user_id = u.id) AS voice_enrolled
       FROM users u
       LEFT JOIN sandbox_assignments s ON s.user_id = u.id
      ORDER BY u.registered_at DESC`,
  ).all<Record<string, unknown>>();
  const registrations = registrationResult.results ?? [];
  const invitationResult = await env.DB.prepare(
    `SELECT 'invitation-' || v.rowid AS diagnostic_id, v.code_words,
            'user-' || v.inviter_user_id AS inviter_id, inviter.handle AS inviter_handle,
            CASE WHEN v.redeemed_by_user_id IS NULL THEN NULL ELSE 'user-' || v.redeemed_by_user_id END AS invitee_id,
            invitee.handle AS invitee_handle, v.redeemed_from AS invitee_phone,
            v.recipient_phone, v.recipient_confirmed_at,
            v.redeemed_at, v.revoked_at, v.hidden_at, i.minted_at, i.expires_at,
            invitee.registered_at, ${INVITATION_STATUS_SQL} AS status
       FROM invitations v JOIN invite_codes i ON i.code_words = v.code_words
       JOIN users inviter ON inviter.id = v.inviter_user_id
       LEFT JOIN users invitee ON invitee.id = v.redeemed_by_user_id
      ORDER BY i.minted_at DESC, v.code_words`,
  ).all<Record<string, unknown>>();
  const invitations = invitationResult.results ?? [];

  await Promise.all(registrations.map(async (row) => {
    const providerId = row.provider_id;
    if (typeof providerId !== "string") return;
    try {
      const sandbox = await getDaytonaSandbox(env, providerId);
      // Only while the assignment still names this Sandbox: a reap or replacement during the
      // read wins, and the snapshot keeps the row as it was read rather than a stale refresh.
      const user = { id: Number(row.user_id), identity_hash: String(row.identity_hash), handle: null };
      if (!(await updateAssignment(env, user, sandbox, providerId))) return;
      row.state = sandbox.state;
      row.error = sandbox.errorReason ?? null;
      row.desired_state = sandbox.desiredState ?? null;
    } catch (error) {
      row.error = error instanceof Error ? error.message : String(error);
    }
  }));

  const inviteSummary = await env.DB.prepare(
    `SELECT COUNT(*) AS total,
            SUM(CASE WHEN expires_at IS NOT NULL AND expires_at <= unixepoch() THEN 1 ELSE 0 END) AS expired,
            SUM(CASE WHEN max_uses IS NULL THEN 1 ELSE 0 END) AS unlimited
       FROM invite_codes`,
  ).first<Record<string, number>>();
  const auditResult = await env.DB.prepare(
    `SELECT id, ts, event, phone_number, call_sid, detail
       FROM audit_log ORDER BY id DESC LIMIT 100`,
  ).all<Record<string, unknown>>();
  const callResult = await env.DB.prepare(
    `SELECT call_sid, phone_number, status, started_at, last_activity_at,
            ended_at, duration_seconds
       FROM voice_calls
      WHERE ended_at IS NULL AND last_activity_at > unixepoch() - 300
      ORDER BY started_at ASC LIMIT 10`,
  ).all<Record<string, unknown>>();
  const liveCalls = callResult.results ?? [];
  await Promise.all(liveCalls.map(async (call) => {
    const activity = await env.DB.prepare(
      `SELECT id, ts, source, direction, event, detail
         FROM call_activity WHERE call_sid = ? AND event != 'voice.timing'
        ORDER BY id DESC LIMIT 30`,
    ).bind(String(call.call_sid)).all<Record<string, unknown>>();
    call.activity = (activity.results ?? []).reverse();
  }));

  const snapshot = {
    generated_at: Math.floor(Date.now() / 1000),
    verbose,
    registrations,
    invitations,
    invites: inviteSummary,
    live_calls: liveCalls,
    audit: auditResult.results ?? [],
  };
  if (verbose) return snapshot;
  return {
    ...snapshot,
    registrations: registrations.map((row) => ({
      ...row,
      phone_number: row.phone_number ? maskPhone(String(row.phone_number)) : null,
      provider_id: row.provider_id ? "assigned" : null,
      identity_hash: undefined,
      error: row.error ? "operator attention required" : null,
    })),
    invitations: invitations.map((row) => ({
      ...row,
      code_words: undefined,
      recipient_phone: row.recipient_phone ? maskPhone(String(row.recipient_phone)) : null,
      invitee_phone: row.invitee_phone ? maskPhone(String(row.invitee_phone)) : null,
    })),
    live_calls: liveCalls.map((call, index) => ({
      ...call,
      phone_number: maskPhone(String(call.phone_number)),
      call_sid: `call-${index + 1}`,
      activity: (call.activity as Record<string, unknown>[]).map(({ detail: _detail, ...activity }) => activity),
    })),
    audit: (auditResult.results ?? []).map((row) => ({
      ...row,
      phone_number: row.phone_number ? maskPhone(String(row.phone_number)) : null,
      call_sid: row.call_sid ? "hidden" : null,
      detail: undefined,
    })),
  };
}

const maskPhone = (phone: string): string => phone.replace(/\d(?=\d{4})/g, "*");

async function diagnosticsState(env: Env, verbose: boolean): Promise<Response> {
  return jsonResponse(await diagnosticsSnapshot(env, verbose));
}

function diagnosticsEvents(env: Env, verbose: boolean): Response {
  const encoder = new TextEncoder();
  let active = true;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const pump = async () => {
        const deadline = Date.now() + 5 * 60 * 1000;
        try {
          while (active && Date.now() < deadline) {
            const snapshot = await diagnosticsSnapshot(env, verbose);
            controller.enqueue(encoder.encode(`event: state\ndata: ${JSON.stringify(snapshot)}\n\n`));
            await new Promise((resolve) => setTimeout(resolve, 10_000));
          }
        } catch (error) {
          if (active) {
            const message = error instanceof Error ? error.message : String(error);
            controller.enqueue(encoder.encode(`event: stream-error\ndata: ${JSON.stringify({ message })}\n\n`));
          }
        } finally {
          if (active) controller.close();
        }
      };
      void pump();
    },
    cancel() {
      active = false;
    },
  });
  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      "x-content-type-options": "nosniff",
    },
  });
}

// Bounds for the operator OTLP export. Logs are row-local, so a page reads only its own
// rows. Spans are matched across the whole call, up to the cap.
const OTLP_MAX_ROWS = 20_000;
const OTLP_LOG_PAGE = 500;
// Cross-call span lookups: 90 IDs per query (D1 allows 100 bound parameters), at most 10
// queries and 1,000 rows each. Exceeding either bound is reported as truncation.
const OTLP_CROSS_CALL_BATCH = 90;
const OTLP_CROSS_CALL_BATCHES = 10;
const OTLP_CROSS_CALL_ROWS = 1_000;

/**
 * Metadata-only OTLP/JSON bodies, ready to POST unchanged to a receiver's /v1/logs or
 * /v1/traces. Paging and truncation travel in headers so the body stays pure OTLP.
 */
async function otlpExport(env: Env, params: URLSearchParams, signal: "logs" | "traces"): Promise<Response> {
  const callSid = params.get("call_sid") ?? "";
  const rawAfter = params.get("after") ?? "0";
  const after = Number(rawAfter);
  if (!/^CA[\w-]{1,78}$/.test(callSid) || !/^\d+$/.test(rawAfter) || !Number.isSafeInteger(after)) {
    return jsonResponse({ error: "valid call_sid and nonnegative integer after are required" }, 400);
  }
  const call = await env.DB.prepare("SELECT call_sid, status, started_at, ended_at, trace_id, span_id FROM voice_calls WHERE call_sid = ?")
    .bind(callSid).first<CallRow>();
  if (!call) return jsonResponse({ error: "unknown call" }, 404);
  if (signal === "logs") {
    const rows = await env.DB.prepare(
      "SELECT id, ts, source, event, detail FROM call_activity WHERE call_sid = ? AND id > ? ORDER BY id LIMIT ?",
    ).bind(callSid, after, OTLP_LOG_PAGE + 1).all<ActivityRow>();
    const page = rows.results.slice(0, OTLP_LOG_PAGE);
    return jsonResponse(logsRequest(await projectLogs(call, page)), 200, {
      "x-dogwalk-cursor": String(page.at(-1)?.id ?? after),
      "x-dogwalk-has-more": String(rows.results.length > OTLP_LOG_PAGE),
    });
  }
  const rows = await env.DB.prepare(
    "SELECT id, ts, source, event, detail FROM call_activity WHERE call_sid = ? ORDER BY id LIMIT ?",
  ).bind(callSid, OTLP_MAX_ROWS + 1).all<ActivityRow>();
  let truncated = rows.results.length > OTLP_MAX_ROWS;
  const own = rows.results.slice(0, OTLP_MAX_ROWS);
  // Work started here may have ended under a later call. Its end rows stay where they were
  // observed; fetch them by span ID (indexed) so the interval can complete.
  const open = openRecordedSpanIds(own);
  if (open.length > OTLP_CROSS_CALL_BATCH * OTLP_CROSS_CALL_BATCHES) truncated = true;
  const later: ActivityRow[] = [];
  for (let i = 0; i < Math.min(open.length, OTLP_CROSS_CALL_BATCH * OTLP_CROSS_CALL_BATCHES); i += OTLP_CROSS_CALL_BATCH) {
    const batch = open.slice(i, i + OTLP_CROSS_CALL_BATCH);
    const found = await env.DB.prepare(
      `SELECT id, ts, source, event, detail FROM call_activity
        WHERE json_extract(detail, '$.span_id') IN (${batch.map(() => "?").join(", ")}) AND call_sid != ?
        ORDER BY id LIMIT ?`,
    ).bind(...batch, callSid, OTLP_CROSS_CALL_ROWS + 1).all<ActivityRow>();
    if (found.results.length > OTLP_CROSS_CALL_ROWS) truncated = true;
    later.push(...found.results.slice(0, OTLP_CROSS_CALL_ROWS));
  }
  return jsonResponse(tracesRequest(await projectSpans(call, [...own, ...later])), 200,
    { "x-dogwalk-truncated": String(truncated) });
}

async function handleAdmin(req: Request, env: Env, pathname: string): Promise<Response> {
  if (!(await adminAuthorized(req, env))) {
    const headers: Record<string, string> = { "cache-control": "no-store", "x-content-type-options": "nosniff" };
    if (env.ADMIN_PASSWORD) headers["www-authenticate"] = 'Basic realm="Dogwalk Diagnostics"';
    return new Response("unauthorized", { status: env.ADMIN_PASSWORD ? 401 : 403, headers });
  }
  if (pathname === "/admin/api/voice-verify" || pathname === "/admin/api/voice-verify/health") {
    return handleVoiceVerify(req, env, new URL(req.url), jsonResponse);
  }
  if (req.method !== "GET") return new Response("method not allowed", { status: 405 });
  if (pathname === "/admin/timing" || pathname === "/admin/timing/") {
    return new Response(OPERATOR_TIMING_HTML, { headers: {
      "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
      "referrer-policy": "no-referrer", "x-content-type-options": "nosniff", "x-frame-options": "DENY",
    } });
  }
  if (pathname === "/admin/api/timing/calls") {
    const calls = await env.DB.prepare(
      "SELECT call_sid, phone_number, status, started_at, ended_at, duration_seconds FROM voice_calls ORDER BY started_at DESC, call_sid LIMIT 50",
    ).all<{ call_sid: string; phone_number: string; status: string; started_at: number; ended_at: number | null; duration_seconds: number | null }>();
    return jsonResponse({ calls: calls.results.map(({ phone_number, ...call }) => ({ ...call, caller: maskPhone(phone_number) })), server_ms: Date.now() });
  }
  if (pathname === "/admin/api/timing/content") {
    const callSid = new URL(req.url).searchParams.get("call_sid") ?? "";
    if (!/^CA[\w-]{1,78}$/.test(callSid)) return jsonResponse({ error: "valid call_sid is required" }, 400);
    try {
      const content = await operatorCallContent(env, callSid);
      return content ? jsonResponse(content) : jsonResponse({ error: "unknown call" }, 404);
    } catch {
      return jsonResponse({ error: "saved content is temporarily unavailable" }, 503);
    }
  }
  if (pathname === "/admin/api/otlp/v1/logs" || pathname === "/admin/api/otlp/v1/traces") {
    return otlpExport(env, new URL(req.url).searchParams, pathname.endsWith("/logs") ? "logs" : "traces");
  }
  if (pathname === "/admin/api/timing") {
    const params = new URL(req.url).searchParams;
    const callSid = params.get("call_sid") ?? "";
    const rawAfter = params.get("after") ?? "0";
    const after = Number(rawAfter);
    if (!/^CA[\w-]{1,78}$/.test(callSid) || !/^\d+$/.test(rawAfter) || !Number.isSafeInteger(after)) {
      return jsonResponse({ error: "valid call_sid and nonnegative integer after are required" }, 400);
    }
    const call = await env.DB.prepare(
      "SELECT call_sid, phone_number, status, started_at, ended_at, duration_seconds FROM voice_calls WHERE call_sid = ?",
    ).bind(callSid).first<{ call_sid: string; phone_number: string; status: string; started_at: number; ended_at: number | null; duration_seconds: number | null }>();
    if (!call) return jsonResponse({ error: "unknown call" }, 404);
    // Cursor is insertion order, not event time: late-flushed observations can have
    // earlier ts_ms. Clients merge by id and sort by observation time when drawing.
    const rows = await env.DB.prepare(
      "SELECT id, ts, source, event, detail FROM call_activity WHERE call_sid = ? AND id > ? ORDER BY id LIMIT 501",
    ).bind(callSid, after).all<{ id: number; ts: number; source: string; event: string; detail: string | null }>();
    const events = rows.results.slice(0, 500).map(({ detail, ...row }) => {
      let parsed: unknown = {};
      try {
        parsed = JSON.parse(detail ?? "{}");
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) parsed = { malformed_detail: true };
      } catch { parsed = { malformed_detail: true }; }
      return { ...row, detail: parsed };
    });
    const { phone_number, ...metadata } = call;
    return jsonResponse({ call: { ...metadata, caller: maskPhone(phone_number) }, events,
      cursor: events.at(-1)?.id ?? after, has_more: rows.results.length > 500, server_ms: Date.now() });
  }
  if (pathname === "/admin" || pathname === "/admin/") {
    return new Response(ADMIN_HTML, {
      headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
    });
  }
  const verbose = new URL(req.url).searchParams.get("verbose") === "1";
  if (pathname === "/admin/api/state") return diagnosticsState(env, verbose);
  if (pathname === "/admin/api/events") return diagnosticsEvents(env, verbose);
  if (pathname === "/admin/api/calls") {
    if (!verbose) return new Response("calls require verbose=1", { status: 400 });
    const limit = Math.min(20, Math.max(1, Number(new URL(req.url).searchParams.get("limit") ?? "5") || 5));
    const timing = new URL(req.url).searchParams.get("timing") === "1";
    const calls = await env.DB.prepare(
      `SELECT call_sid, status, started_at, ended_at, duration_seconds FROM voice_calls ORDER BY started_at DESC LIMIT ?`,
    ).bind(limit).all<Record<string, unknown>>();
    const withActivity = await Promise.all(calls.results.map(async (call) => ({
      ...call,
      activity: (await env.DB.prepare(
        "SELECT ts, source, event, detail FROM call_activity WHERE call_sid = ? AND (? OR event != 'voice.timing') ORDER BY id",
      ).bind(call.call_sid, timing ? 1 : 0).all()).results,
    })));
    return jsonResponse(withActivity);
  }
  if (pathname === "/admin/api/conversations") {
    // Conversation content is never part of the redacted default lens.
    if (!verbose) return new Response("conversations require verbose=1", { status: 400 });
    const assignments = await env.DB.prepare(
      "SELECT u.identity_hash FROM sandbox_assignments s JOIN users u ON u.id = s.user_id",
    ).all<{ identity_hash: string }>();
    const conversations = await Promise.all(assignments.results.map(async ({ identity_hash }) => {
      const object = env.VOICE_SESSIONS.get(env.VOICE_SESSIONS.idFromName(identity_hash));
      return (await object.fetch("https://voice-session/conversation")).json();
    }));
    return jsonResponse(conversations);
  }
  return new Response("not found", { status: 404 });
}

// --- Paired Browser -----------------------------------------------------
//
// A browser claims a Pairing Code it heard in a call, waits for the caller to accept, then
// holds a credential `<identity>.<secret>`: the identity half routes to the sandbox-keyed
// VoiceSession, which alone checks the secret. No secret travels in a URL.

async function handlePair(req: Request, env: Env, url: URL): Promise<Response> {
  if (url.pathname === "/pair" && req.method === "GET") {
    return new Response(PAIR_HTML, {
      headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
    });
  }
  if (url.pathname === "/pair/claim" && req.method === "POST") {
    const body = await req.json<{ code?: unknown }>().catch(() => ({ code: null }));
    const code = typeof body.code === "string" ? body.code.toLowerCase() : "";
    if (!/^[0-9a-f]{20}$/.test(code)) return jsonResponse({ error: "malformed pairing code" }, 400);
    const codeHash = await sha256Hex(code);
    // Single use: the claim consumes the row whether or not the caller accepts.
    const row = await env.DB.prepare(
      "DELETE FROM pairing_codes WHERE code_hash = ? AND expires_at > unixepoch() RETURNING identity_hash",
    ).bind(codeHash).first<{ identity_hash: string }>();
    if (!row) return jsonResponse({ error: "unknown or expired pairing code" }, 404);
    const cf = (req as Request & { cf?: { city?: unknown; region?: unknown; country?: unknown } }).cf;
    return voiceSessionFor(env, row.identity_hash).fetch("https://voice-session/pair/claim", {
      method: "POST",
      headers: {
        "x-dogwalk-code-hash": codeHash,
        "x-dogwalk-browser": describeBrowser(req.headers.get("user-agent") ?? ""),
        "x-dogwalk-place": describePlace(cf),
      },
    });
  }
  // Anonymous decode-failure telemetry: a page heard Pairing Tones but could not decode them.
  // Before a claim nothing says whose call it is, so only coarse diagnostics are kept, and
  // only while some Pairing Code is outstanding; the table is trimmed to its newest rows.
  if (url.pathname === "/pair/decode-failed" && req.method === "POST") {
    const length = Number(req.headers.get("content-length") ?? "");
    if (!Number.isFinite(length) || length <= 0 || length > DECODE_FAILURE_MAX_BYTES) {
      return jsonResponse({ error: "body missing or too large" }, 413);
    }
    const text = await req.text();
    if (text.length > DECODE_FAILURE_MAX_BYTES) return jsonResponse({ error: "body too large" }, 413);
    let body: Record<string, unknown>;
    try {
      body = JSON.parse(text);
    } catch {
      return jsonResponse({ error: "malformed report" }, 400);
    }
    if (body?.stage !== "ecc_failed" && body?.stage !== "ecc_unverified") return jsonResponse({ error: "unknown stage" }, 400);
    const bounded = (value: unknown, max: number) =>
      typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(max, value)) : null;
    const seconds = bounded(body.seconds, 60);
    await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO pairing_decode_failures (ts, stage, baud, seconds, symbols_heard, erasures)
         SELECT ?, ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM pairing_codes WHERE expires_at > unixepoch())`,
      ).bind(
        Date.now(), body.stage, bounded(body.baud, 64), seconds === null ? null : Math.round(seconds * 10) / 10,
        bounded(body.symbols_heard, 64), bounded(body.erasures, 64),
      ),
      env.DB.prepare("DELETE FROM pairing_decode_failures WHERE id <= (SELECT MAX(id) FROM pairing_decode_failures) - ?")
        .bind(DECODE_FAILURE_KEEP_ROWS),
    ]);
    return new Response(null, { status: 204 });
  }
  if (url.pathname === "/pair/claim/status" && req.method === "GET") {
    const ticket = splitCredential(req.headers.get("authorization")?.replace(/^Bearer\s+/i, "") ?? null);
    if (!ticket || !(await knownIdentity(env, ticket.identity))) return jsonResponse({ state: "expired" });
    return voiceSessionFor(env, ticket.identity).fetch("https://voice-session/pair/claim/status", {
      headers: { "x-dogwalk-secret": ticket.secret },
    });
  }
  // Human Takeover terminal: a full-screen page, its vendored assets, and the relay socket.
  if (url.pathname === "/pair/terminal" && req.method === "GET") {
    return new Response(TERMINAL_HTML, {
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-store",
        "referrer-policy": "no-referrer",
        "x-content-type-options": "nosniff",
        // xterm injects its own style elements; scripts are same-origin assets only.
        "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
      },
    });
  }
  if (url.pathname.startsWith("/pair/assets/")) {
    return handleTerminalAssets(url) ?? new Response("not found", { status: 404 });
  }
  if (url.pathname === "/pair/terminal/ws") {
    if (req.headers.get("upgrade")?.toLowerCase() !== "websocket") return new Response("expected a WebSocket", { status: 426 });
    const identity = url.searchParams.get("for") ?? "";
    if (!/^[0-9a-f]{16,128}$/.test(identity) || !(await knownIdentity(env, identity))) {
      return new Response("unknown pairing", { status: 404 });
    }
    const relay = env.TERMINAL_RELAYS.get(env.TERMINAL_RELAYS.idFromName(identity));
    const headers = new Headers(req.headers);
    headers.set("x-dogwalk-identity", identity);
    return relay.fetch("https://terminal-relay/ws", { headers });
  }
  // Workspace files (read from the Sandbox) and Board Assets (captured at draw time, never the Sandbox).
  if ((url.pathname === "/pair/file" || /^\/pair\/asset\/[0-9a-f]{64}$/.test(url.pathname)) && req.method === "GET") {
    const credential = splitCredential(req.headers.get("authorization")?.replace(/^Bearer\s+/i, "") ?? null);
    if (!credential || !(await knownIdentity(env, credential.identity))) return jsonResponse({ error: "this pairing has expired" }, 401);
    return voiceSessionFor(env, credential.identity).fetch(`https://voice-session${url.pathname}${url.search}`, {
      headers: { "x-dogwalk-secret": credential.secret },
    });
  }
  if (url.pathname === "/pair/ws") {
    if (req.headers.get("upgrade")?.toLowerCase() !== "websocket") return new Response("expected a WebSocket", { status: 426 });
    const identity = url.searchParams.get("for") ?? "";
    if (!/^[0-9a-f]{16,128}$/.test(identity) || !(await knownIdentity(env, identity))) {
      return new Response("unknown pairing", { status: 404 });
    }
    return voiceSessionFor(env, identity).fetch("https://voice-session/pair/ws", { headers: req.headers });
  }
  return new Response("not found", { status: 404 });
}

// Only identities with a Sandbox Assignment get a VoiceSession; random ones never create objects.
async function knownIdentity(env: Env, identity: string): Promise<boolean> {
  const row = await env.DB.prepare(
    "SELECT 1 AS ok FROM users u JOIN sandbox_assignments s ON s.user_id = u.id WHERE u.identity_hash = ?",
  ).bind(identity).first();
  return Boolean(row);
}

function voiceSessionFor(env: Env, identity: string): DurableObjectStub {
  return env.VOICE_SESSIONS.get(env.VOICE_SESSIONS.idFromName(identity));
}

// --- /voice routing -----------------------------------------------------

export default {
  async scheduled(event: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    if (event.cron === SANDBOX_REAP_CRON) {
      // Rethrown so the cron invocation records the failure, not just the log.
      ctx.waitUntil(reapSandboxes(env).catch((error) => {
        console.error("sandbox reaping failed", error);
        throw error;
      }));
      return;
    }
    ctx.waitUntil(processTwilioJobs(env));
  },
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(req.url);

    if (url.pathname === "/" && req.method === "GET") {
      return new Response(LANDING_HTML, {
        headers: { "content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=300" },
      });
    }

    // GET /healthz: does not require Twilio validation.
    if (url.pathname === "/healthz") {
      return new Response("ok\n", { headers: { "content-type": "text/plain" } });
    }

    if (url.pathname === "/admin" || url.pathname.startsWith("/admin/")) {
      return handleAdmin(req, env, url.pathname);
    }

    if (url.pathname === "/pair" || url.pathname.startsWith("/pair/")) {
      return handlePair(req, env, url);
    }

    if (url.pathname === "/sounds" || url.pathname.startsWith("/sounds/")) {
      const sounds = await handleSounds(req, url);
      if (sounds) return sounds;
    }

    if (url.pathname === "/voice" && req.method === "POST") {
      return handleVoice(req, env, ctx);
    }
    if (url.pathname === "/openai/live" && req.method === "POST") {
      return handleOpenAiLiveWebhook(req, env);
    }
    if (url.pathname === "/voice/after-live" && req.method === "POST") {
      return handleAfterLive(req, env, ctx);
    }
    if (url.pathname === "/voice/recovery" && req.method === "POST") {
      return handleRecovery(req, env);
    }
    if (url.pathname === "/voice/recovery/confirm" && req.method === "POST") {
      return handleRecoveryConfirm(req, env);
    }
    if (url.pathname === "/voice/claim" && req.method === "POST") {
      return handleClaim(req, env);
    }
    if (url.pathname === "/voice/confirm" && req.method === "POST") {
      return handleConfirm(req, env, ctx);
    }
    if (url.pathname === "/voice/menu" && req.method === "POST") {
      return handleMenu(req, env);
    }
    if (url.pathname.startsWith("/voice/donation")) {
      return handleDonation(req, env, ctx, url);
    }
    if (url.pathname.startsWith("/voice/practice")) {
      return handlePractice(req, env, ctx, url);
    }
    if (url.pathname.startsWith("/voice/identity")) {
      return handleIdentity(req, env, ctx, url);
    }
    if (url.pathname.startsWith("/voice/reenrollment")) {
      return handleReenrollment(req, env, ctx, url);
    }
    if (url.pathname === "/voice/status" && req.method === "POST") {
      return handleCallStatus(req, env, ctx);
    }
    if (url.pathname === "/voice/provider-status" && req.method === "POST") {
      return handleProviderStatus(req, env, ctx);
    }
    return new Response("not found", { status: 404 });
  },
};

// --- main /voice entrypoint --------------------------------------------

async function handleVoice(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const receivedMs = Date.now();
  // Twilio signature check. We clone the body because formData() consumes it.
  const sig = await twilioSignatureValid(req, env);
  if (!sig.valid) {
    return new Response("forbidden", { status: 403 });
  }
  const form = await req.clone().formData();
  const from = normalizePhone(form.get("From"));
  const callSid = String(form.get("CallSid") ?? "");
  if (!from) {
    return twiml(Say("Dogwalk could not verify this phone number. Goodbye.") + Hangup());
  }

  await recordCallStarted(env, from, callSid);
  deferTwilioObservation(env, ctx, form, callSid, receivedMs);
  await audit(env, "call.entered", from, callSid, { signedUrl: sig.url });
  // Caller-ID attestation (STIR/SHAKEN) is the only evidence that From is genuine, since
  // the Twilio signature proves only that Twilio sent this request. Record it once per call
  // with which parameters the carrier supplied (keys only, plus non-identifying values).
  // Re-entering /voice in the same call (after the in-call menu) omits StirVerstat, so only
  // the first entry is evidence.
  const attested = callSid ? await env.DB.prepare(
    "SELECT 1 FROM call_activity WHERE call_sid = ? AND event = 'call.attestation' LIMIT 1",
  ).bind(callSid).first() : null;
  if (!attested) {
    const keys = Array.from(new Set(Array.from(form.keys()))).sort();
    await audit(env, "call.attestation", from, callSid, {
      stir_verstat: form.get("StirVerstat") ? String(form.get("StirVerstat")) : null,
      caller_name_present: Boolean(form.get("CallerName")),
      from_country: form.get("FromCountry") ? String(form.get("FromCountry")) : null,
      parameters: keys,
    });
  }

  // Caller ID selects the User whose Home this is, and the Identity Check (shadow mode,
  // identity_check.ts) asks for evidence once per call: it is scored and reported but never
  // withholds entry, so every Home caller still connects afterwards.
  const user = await homeUser(env, from);
  if (user) {
    // The verifier's model load takes seconds; start it now, in the User's VoiceSession where
    // their challenges are scored, so the Identity Challenge does not wait for it. Background
    // only: TwiML never waits.
    warmSpeaker(env, ctx, user.identity_hash);
    await env.DB.batch([
      env.DB.prepare("UPDATE users SET last_seen_at = unixepoch(), home_used_at = unixepoch() WHERE id = ?").bind(user.id),
      env.DB.prepare("UPDATE voice_calls SET user_id = ? WHERE call_sid = ?").bind(user.id, callSid),
    ]);
    await audit(env, "caller.registered", from, callSid, { user_id: user.id, via: "home" });
    const checked = await env.DB.prepare("SELECT identity IS NOT NULL AS done FROM voice_calls WHERE call_sid = ?")
      .bind(callSid).first<{ done: number }>();
    if (!checked?.done) {
      const log = (event: string, detail: Record<string, unknown>) => audit(env, event, from, callSid, detail);
      // Shadow mode never drops a Home caller: a check that cannot start is recorded, and the call connects.
      const check = await startHomeCheck(env, ctx, new URL(req.url), form, user.id, log).catch(async (error) => {
        await log("identity.error", { stage: "start", message: (error instanceof Error ? error.message : String(error)).slice(0, 200) });
        await endIdentity(env, callSid).catch(() => {});
        return null;
      });
      if (check) {
        // The check runs while the Sandbox wakes; the connection after it reports a broken one.
        ctx.waitUntil(wakeSandbox(env, ctx, user, from, callSid).catch((error) =>
          audit(env, "sandbox.error", from, callSid, { message: String(error instanceof Error ? error.message : error).slice(0, 200) })));
        return twiml(check);
      }
    }
    // Talk first: the caller reaches the Voice Agent at once while the Sandbox wakes in
    // parallel. Only a Sandbox the provider reports broken goes to deterministic recovery.
    try {
      if ((await wakeSandbox(env, ctx, user, from, callSid)) === "broken") return recoveryMenu("Workspace unavailable. ");
      if (!env.OPENAI_PROJECT_ID) throw new Error("OPENAI_PROJECT_ID is not configured");
      const context = await sipContextCapability(env, callSid);
      const sipUri = `sip:${env.OPENAI_PROJECT_ID}@sip.api.openai.com;transport=tls;secure=true?x-dogwalk-context=${encodeURIComponent(context)}`;
      return twiml(DialSip(sipUri));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await audit(env, "sandbox.error", from, callSid, { message: message.slice(0, 200) });
      return twiml(Say("Dogwalk could not reach your workspace. Try again later. Goodbye.") + Hangup());
    }
  }

  // Unregistered caller: begin the registration ritual.
  await audit(env, "claim.ritual.start", from, callSid, {});
  const invites = await usableInviteCodes(env, from);
  return twiml(GatherSpeech(
    "This is Dogwalk, a voice line for supervising coding agents. If you have an invite, say your three invite words now. " +
      "To donate voice samples for research, say donate.",
    "/voice/claim", 1, [...invites.map((i) => i.code_words), "donate"],
  ));
}

// Start the User's Sandbox if it is stopped and the VoiceSession reaching its Agent, without
// waiting for either. "broken" when the provider reports the Sandbox unusable.
async function wakeSandbox(env: Env, ctx: ExecutionContext, user: User, from: string, callSid: string): Promise<"broken" | "ok"> {
  const { assignment, sandbox } = await resolveSandbox(env, user);
  if (sandbox && ["error", "build_failed", "destroyed"].includes(sandbox.state)) {
    await audit(env, "sandbox.unavailable", from, callSid, { state: sandbox.state });
    return "broken";
  }
  if (sandbox && ["stopped", "archived", "paused"].includes(sandbox.state)) {
    const started = await daytonaFetch<DaytonaSandbox>(env, `/sandbox/${encodeURIComponent(sandbox.id)}/start`, { method: "POST" });
    await updateAssignment(env, user, started, sandbox.id);
    await audit(env, "sandbox.start.requested", from, callSid, { state: sandbox.state });
  } else {
    const state = sandbox?.state ?? assignment.state;
    await audit(env, state === "started" ? "sandbox.ready" : "sandbox.waking", from, callSid, { state });
  }
  // The VoiceSession starts reaching the Agent while Twilio and GPT-Live set up the call.
  if (assignment.provider_id) ctx.waitUntil(prewarmVoiceSession(env, user, from, assignment.provider_id));
  return "ok";
}

async function prewarmVoiceSession(env: Env, user: User, phone: string, sandboxId: string): Promise<void> {
  try {
    const object = env.VOICE_SESSIONS.get(env.VOICE_SESSIONS.idFromName(user.identity_hash));
    await object.fetch("https://voice-session/prewarm", {
      method: "POST",
      headers: {
        "x-dogwalk-phone": phone, "x-dogwalk-user": String(user.id),
        "x-dogwalk-sandbox-id": sandboxId, "x-dogwalk-identity": user.identity_hash,
      },
    });
  } catch (error) {
    console.error("voice session prewarm failed", error);
  }
}

async function handleAfterLive(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const receivedMs = Date.now();
  const signature = await twilioSignatureValid(req, env);
  if (!signature.valid) return new Response("forbidden", { status: 403 });
  const form = await req.clone().formData();
  const from = normalizePhone(form.get("From"));
  const callSid = String(form.get("CallSid") ?? "");
  if (!from || !callSid) return twiml(Hangup());
  // Dial's result supplies an explicit parent/child relationship even if an
  // asynchronous SIP progress callback was lost. No SIP URI/capability is saved.
  if (form.get("DialCallSid")) {
    const child = new FormData();
    child.set("AccountSid", String(form.get("AccountSid") ?? ""));
    child.set("CallSid", String(form.get("DialCallSid")));
    child.set("ParentCallSid", callSid);
    child.set("CallStatus", String(form.get("DialCallStatus") ?? "unknown"));
    deferTwilioObservation(env, ctx, child, callSid, receivedMs);
  }
  const handoff = await env.DB.prepare(
    "SELECT action FROM call_handoffs WHERE call_sid = ? AND phone_number = ?",
  ).bind(callSid, from).first<{ action: string }>();
  await env.DB.prepare("DELETE FROM call_handoffs WHERE call_sid = ?").bind(callSid).run();
  if (handoff?.action === "hangup") return twiml(Hangup());
  // A theme switch: back through /voice to a fresh GPT-Live session with the new voice.
  if (handoff?.action === "rejoin") return twiml(Redirect("/voice"));
  if (handoff?.action === "donation") return twiml(Redirect("/voice/donation"));
  if (handoff?.action === "practice") return twiml(Redirect("/voice/practice"));
  if (handoff?.action === "reenrollment") return twiml(Redirect("/voice/reenrollment"));
  return recoveryMenu(handoff?.action === "recovery" ? "Recovery options. " : "Voice dropped. ");
}

function recoveryMenu(prefix = ""): Response {
  return twiml(
    // Ordered by consequence: the first option is the harmless one.
    GatherDtmf(`${prefix}Press 1 to reconnect. 2 to restart the sandbox, which stops running work. 3 to replace it. 9 to hang up.`, "/voice/recovery") +
    Hangup(),
  );
}

async function handleRecovery(req: Request, env: Env): Promise<Response> {
  const signature = await twilioSignatureValid(req, env);
  if (!signature.valid) return new Response("forbidden", { status: 403 });
  const form = await req.clone().formData();
  const from = normalizePhone(form.get("From"));
  const callSid = String(form.get("CallSid") ?? "");
  const digit = String(form.get("Digits") ?? "");
  const user = from && callSid ? await callUser(env, callSid) : null;
  if (!from || !user) return twiml(Hangup());
  if (digit === "9") return twiml(Hangup());
  if (digit === "1" || digit === "0") return twiml(Redirect("/voice"));
  if (digit === "3") {
    return twiml(
      GatherDtmf(
        "Replacement destroys all workspace files and session history. Press 3 again to confirm.",
        "/voice/recovery/confirm",
      ) + Hangup(),
    );
  }
  if (digit === "2") {
    const assignment = await getAssignment(env, user);
    if (!assignment?.provider_id) return twiml(Redirect("/voice"));
    try {
      await daytonaFetch(env, `/sandbox/${encodeURIComponent(assignment.provider_id)}/stop`, { method: "POST" });
      await audit(env, "sandbox.restart.requested", from, callSid, {});
      return twiml(Say("Restarting.") + Pause(5) + Redirect("/voice"));
    } catch {
      return recoveryMenu("Restart failed. ");
    }
  }
  return recoveryMenu();
}

async function handleRecoveryConfirm(req: Request, env: Env): Promise<Response> {
  const signature = await twilioSignatureValid(req, env);
  if (!signature.valid) return new Response("forbidden", { status: 403 });
  const form = await req.clone().formData();
  const from = normalizePhone(form.get("From"));
  const callSid = String(form.get("CallSid") ?? "");
  const user = from && callSid ? await callUser(env, callSid) : null;
  if (!from || String(form.get("Digits") ?? "") !== "3" || !user) {
    return recoveryMenu("Cancelled. ");
  }
  const assignment = await getAssignment(env, user);
  if (assignment?.provider_id) {
    try {
      await daytonaFetch(env, `/sandbox/${encodeURIComponent(assignment.provider_id)}`, { method: "DELETE" });
    } catch (error) {
      if (!(error instanceof DaytonaApiError) || error.status !== 404) return recoveryMenu("Replacement failed. ");
    }
    await env.DB.prepare(
      `UPDATE sandbox_assignments SET provider_id = NULL, state = 'missing', error = NULL,
         provisioning_started_at = 0, last_checked_at = unixepoch() WHERE user_id = ?`,
    ).bind(user.id).run();
    await audit(env, "sandbox.replacement.confirmed", from, callSid, {});
  }
  return twiml(Say("Replacing.") + Pause(5) + Redirect("/voice"));
}

async function handleOpenAiLiveWebhook(req: Request, env: Env): Promise<Response> {
  const body = await req.text();
  if (!(await openAiWebhookValid(req, env, body))) return new Response("invalid webhook signature", { status: 400 });

  let event: Record<string, any>;
  try {
    event = JSON.parse(body);
  } catch {
    return new Response("invalid webhook payload", { status: 400 });
  }
  if (!["live.transport.incoming", "live.call.incoming"].includes(String(event.type))) {
    return new Response(null, { status: 204 });
  }
  if (event.type === "live.transport.incoming" && event.data?.type !== "sip") {
    return new Response(null, { status: 204 });
  }

  const liveSessionId = String(event.data?.session_id ?? "");
  const sipHeaders = Array.isArray(event.data?.sip_headers) ? event.data.sip_headers : [];
  const contextHeader = sipHeaders.find((header: any) =>
    String(header?.name ?? "").toLowerCase() === "x-dogwalk-context"
  );
  const context = liveSessionId && contextHeader
    ? await verifySipContext(env, String(contextHeader.value ?? ""))
    : null;
  if (!context) {
    if (liveSessionId) await decideLiveCall(env, liveSessionId, "reject", { status_code: 403 });
    return new Response(null, { status: 204 });
  }

  // Any assigned Sandbox will do, started or not: the Durable Object waits for the Agent
  // in the background while the caller talks.
  const user = await callUser(env, context.callSid);
  const caller = await env.DB.prepare("SELECT phone_number FROM voice_calls WHERE call_sid = ?")
    .bind(context.callSid).first<{ phone_number: string }>();
  const assignment = user ? await getAssignment(env, user) : null;
  if (user && caller && assignment && !assignment.provider_id) {
    // Another request may have been provisioning when /voice ran; it has had a few seconds.
    try {
      assignment.provider_id = (await resolveSandbox(env, user)).assignment.provider_id;
    } catch (error) {
      console.error("sandbox resolution at accept failed", error);
    }
  }
  if (!user || !caller || !assignment?.provider_id) {
    await decideLiveCall(env, liveSessionId, "reject", { status_code: 486 });
    return new Response(null, { status: 204 });
  }

  const object = env.VOICE_SESSIONS.get(env.VOICE_SESSIONS.idFromName(user.identity_hash));
  const headers = new Headers({
    "x-dogwalk-phone": caller.phone_number,
    "x-dogwalk-user": String(user.id),
    "x-dogwalk-sandbox-id": assignment.provider_id,
    "x-dogwalk-call-sid": context.callSid,
    "x-dogwalk-live-session-id": liveSessionId,
    "x-dogwalk-identity": user.identity_hash,
  });
  const traceparent = await callTraceparent(env, context.callSid);
  if (traceparent) headers.set("traceparent", traceparent);
  const prepare = await object.fetch("https://voice-session/prepare", { method: "POST", headers });
  if (!prepare.ok) {
    await decideLiveCall(env, liveSessionId, "reject", { status_code: prepare.status === 409 ? 486 : 500 });
    return new Response(null, { status: 204 });
  }
  // The caller's Theme casts the voice and presents the Voice Frontend's instructions.
  const prepared = await prepare.json<{ theme?: string }>().catch(() => ({ theme: undefined }));
  const theme = THEMES.find((item) => item.id === prepared.theme) ?? DEFAULT_THEME;
  // Client delegation: GPT-Live only converses. The VoiceSession Durable Object runs the
  // Session Orchestrator and answers each delegation through the sideband.
  const accepted = await decideLiveCall(env, liveSessionId, "accept", {
    session: {
      type: "live",
      model: env.OPENAI_LIVE_MODEL ?? "gpt-live-1",
      instructions: frontendInstructions(theme),
      audio: { output: { voice: theme.voice } },
      delegation: { type: "client", acknowledgement_delay_ms: 400 },
    },
  });
  if (!accepted) return new Response(null, { status: 204 });

  const attached = await object.fetch("https://voice-session/attach", { method: "POST", headers });
  if (!attached.ok) {
    await decideLiveCall(env, liveSessionId, "hangup");
    await audit(env, "voice.live.attach.failed", caller.phone_number, context.callSid, {
      status: attached.status,
      detail: (await attached.text()).slice(0, 200),
    });
    return new Response("sideband attach failed", { status: 500 });
  }
  await audit(env, "voice.live.accepted", caller.phone_number, context.callSid, { live_session_id: liveSessionId });
  return new Response(null, { status: 204 });
}

async function decideLiveCall(
  env: Env,
  sessionId: string,
  action: "accept" | "reject" | "hangup",
  payload?: unknown,
): Promise<boolean> {
  const base = (env.OPENAI_LIVE_API_BASE ?? "https://api.openai.com/v1/live").replace(/\/$/, "");
  const response = await fetch(`${base}/sessions/${encodeURIComponent(sessionId)}/${action}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.OPENAI_API_KEY}`,
      ...(payload ? { "content-type": "application/json" } : {}),
    },
    body: payload ? JSON.stringify(payload) : undefined,
  });
  if (response.ok) return true;
  const detail = await response.text();
  if (response.status === 409 && detail.includes("decision_already_made")) return action === "accept";
  console.error(`GPT-Live ${action} failed`, response.status, detail.slice(0, 300));
  return false;
}

async function recordCallStarted(env: Env, phone: string, callSid: string): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  // The Voice Call's trace context is minted only when its row is created, here where the call
  // begins. A repeated start keeps it, and a row created without one never gains one later.
  const context = newRootContext();
  await env.DB.prepare(
    `INSERT INTO voice_calls
       (call_sid, phone_number, status, started_at, last_activity_at, trace_id, span_id)
     VALUES (?, ?, 'in-progress', ?, ?, ?, ?)
     ON CONFLICT(call_sid) DO UPDATE SET
       phone_number = excluded.phone_number,
       status = CASE WHEN voice_calls.ended_at IS NULL THEN 'in-progress' ELSE voice_calls.status END,
       last_activity_at = excluded.last_activity_at`,
  ).bind(callSid, phone, now, now, context.traceId, context.spanId).run();
}

/** The Voice Call's recorded trace context as a `traceparent`, or none for older calls. */
async function callTraceparent(env: Env, callSid: string): Promise<string | null> {
  const row = await env.DB.prepare("SELECT trace_id, span_id FROM voice_calls WHERE call_sid = ?")
    .bind(callSid).first<{ trace_id: string | null; span_id: string | null }>();
  const context = { traceId: row?.trace_id ?? undefined, spanId: row?.span_id ?? undefined };
  return isTraceContext(context) ? formatTraceparent(context) : null;
}

// Diagnostics are a separate projection. They never dispatch harness events or
// change call/session state, and provider network requests run off the voice path.
function deferTwilioObservation(env: Env, ctx: ExecutionContext, form: FormData, rootCallSid: string, receivedMs: number): void {
  ctx.waitUntil((async () => {
    const known = await env.DB.prepare("SELECT 1 FROM voice_calls WHERE call_sid = ?").bind(rootCallSid).first();
    if (!known) return;
    await observeTwilio(env, form, rootCallSid, receivedMs);
    await processTwilioJobs(env);
  })().catch(() => console.error("provider timing observation failed")));
}

async function handleProviderStatus(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const receivedMs = Date.now();
  if (!(await twilioSignatureValid(req, env)).valid) return new Response("forbidden", { status: 403 });
  const form = await req.formData();
  const rootCallSid = String(form.get("ParentCallSid") ?? form.get("CallSid") ?? "");
  deferTwilioObservation(env, ctx, form, rootCallSid, receivedMs);
  return new Response(null, { status: 204 });
}

async function handleCallStatus(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const receivedMs = Date.now();
  const sig = await twilioSignatureValid(req, env);
  if (!sig.valid) return new Response("forbidden", { status: 403 });
  const form = await req.clone().formData();
  const from = normalizePhone(form.get("From"));
  const callSid = String(form.get("CallSid") ?? "");
  const status = String(form.get("CallStatus") ?? "unknown").toLowerCase();
  const duration = Number(form.get("CallDuration") ?? "0");
  if (!from || !callSid) return new Response("bad request", { status: 400 });
  const now = Math.floor(Date.now() / 1000);
  const terminal = ["completed", "busy", "failed", "no-answer", "canceled"].includes(status);
  if (!terminal) {
    deferTwilioObservation(env, ctx, form, callSid, receivedMs);
    return new Response(null, { status: 204 });
  }
  await env.DB.prepare(
    `INSERT INTO voice_calls
       (call_sid, phone_number, status, started_at, last_activity_at, ended_at, duration_seconds)
     VALUES (?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(call_sid) DO UPDATE SET
       status = excluded.status,
       last_activity_at = excluded.last_activity_at,
       ended_at = excluded.ended_at,
        duration_seconds = excluded.duration_seconds
      WHERE voice_calls.ended_at IS NULL`,
  ).bind(callSid, from, status, now, now, terminal ? now : null, duration || null).run();
  await audit(env, `call.${status}`, from, callSid, duration ? { duration } : {});
  deferTwilioObservation(env, ctx, form, callSid, receivedMs);
  if (terminal) {
    // Rounds live only for the call: a hangup mid-ritual leaves no score or enrollment embedding behind.
    await env.DB.prepare("DELETE FROM challenge_rounds WHERE call_sid = ?").bind(callSid).run()
      .catch((error) => console.error("challenge round cleanup failed", error));
    // The telephone call is over: make sure the VoiceSession has noticed, even if the
    // GPT-Live sideband ended without telling it.
    try {
      const user = await callUser(env, callSid);
      if (user) {
        const object = env.VOICE_SESSIONS.get(env.VOICE_SESSIONS.idFromName(user.identity_hash));
        await object.fetch("https://voice-session/telephone-ended", {
          method: "POST", headers: { "x-dogwalk-call-sid": callSid },
        });
      }
    } catch (error) {
      console.error("telephone-ended notification failed", error);
    }
  }
  return new Response(null, { status: 204 });
}

// --- /voice/donation: Voice Donation (voice_donation.ts) ---------------

// Open to any caller, registered or not; nothing here reads or writes a User. Call Activity
// records that a donation happened on a call, while the Donation Session itself stays unlinked.
async function handleDonation(req: Request, env: Env, ctx: ExecutionContext, url: URL): Promise<Response> {
  if (url.pathname === "/voice/donation/tone.wav" && req.method === "GET") return donationTone();
  const stream = /^\/voice\/donation\/stream\/([A-Za-z0-9._-]{1,300})$/.exec(url.pathname);
  if (stream && req.method === "GET") {
    const signed = (await twilioSignatureValid(req, env, "wss")).valid || (await twilioSignatureValid(req, env)).valid;
    if (!signed) return new Response("forbidden", { status: 403 });
    return handleDonationStream(req, env, ctx, stream[1]);
  }
  if (req.method !== "POST") return new Response("not found", { status: 404 });
  if (!(await twilioSignatureValid(req, env)).valid) return new Response("forbidden", { status: 403 });
  const form = await req.clone().formData();
  const from = normalizePhone(form.get("From"));
  const callSid = String(form.get("CallSid") ?? "");
  const log = (event: string, detail: Record<string, unknown>) => audit(env, event, from, callSid || null, detail);
  if (url.pathname === "/voice/donation") {
    await log("donation.consent.asked", {});
    return twiml(donationConsent());
  }
  try {
    if (url.pathname === "/voice/donation/consent") return twiml(await handleDonationConsent(env, url, form, log));
    if (url.pathname === "/voice/donation/round") return twiml(await handleDonationRound(env, url, form, log));
  } catch (error) {
    await log("donation.error", { message: (error instanceof Error ? error.message : String(error)).slice(0, 200) });
    return twiml(Say("Voice donation stopped because of an error. Thank you. Goodbye.") + Hangup());
  }
  return new Response("not found", { status: 404 });
}

// --- /voice/practice: Challenge Practice (challenge_practice.ts) --------

// The call's User practices against their own Voice Reference; every path ends back at /voice.
async function handlePractice(req: Request, env: Env, ctx: ExecutionContext, url: URL): Promise<Response> {
  if (url.pathname === "/voice/practice/tone.wav" && req.method === "GET") return practiceTone();
  if (url.pathname === "/voice/practice/received.wav" && req.method === "GET") return practiceReceived();
  const stream = /^\/voice\/practice\/stream\/([A-Za-z0-9._-]{1,300})$/.exec(url.pathname);
  if (stream && req.method === "GET") {
    const signed = (await twilioSignatureValid(req, env, "wss")).valid || (await twilioSignatureValid(req, env)).valid;
    if (!signed) return new Response("forbidden", { status: 403 });
    return handlePracticeStream(req, env, ctx, stream[1]);
  }
  if (req.method !== "POST") return new Response("not found", { status: 404 });
  if (!(await twilioSignatureValid(req, env)).valid) return new Response("forbidden", { status: 403 });
  const form = await req.clone().formData();
  const from = normalizePhone(form.get("From"));
  const callSid = String(form.get("CallSid") ?? "");
  if (!from || !callSid) return twiml(Hangup());
  const log = (event: string, detail: Record<string, unknown>) => audit(env, event, from, callSid, detail);
  try {
    if (url.pathname === "/voice/practice") return twiml(await startPractice(env, ctx, url, form, log));
    if (url.pathname === "/voice/practice/heard") return twiml(await handlePracticeHeard(env, ctx, url, form, log));
    if (url.pathname === "/voice/practice/result") return twiml(await handlePracticeResult(env, ctx, url, form, log));
  } catch (error) {
    await log("practice.error", { message: (error instanceof Error ? error.message : String(error)).slice(0, 200) });
    await env.DB.prepare("DELETE FROM challenge_rounds WHERE call_sid = ?").bind(callSid).run().catch(() => {});
    // A failed round webhook still stops its stream, which would otherwise keep listening.
    const stop = url.pathname === "/voice/practice/heard" ? practiceStreamStop(url) : "";
    return twiml(stop + Say("Challenge practice stopped because of an error. Back to Dogwalk.") + Redirect("/voice"));
  }
  return new Response("not found", { status: 404 });
}

// --- /voice/reenrollment: Re-enrollment (reenrollment.ts) ----------------

// The call's User replaces their Voice Reference after passing its check; every path ends back at
// /voice. Its rounds stream to /voice/identity/stream, so only the Gather actions live here.
async function handleReenrollment(req: Request, env: Env, ctx: ExecutionContext, url: URL): Promise<Response> {
  if (req.method !== "POST") return new Response("not found", { status: 404 });
  if (!(await twilioSignatureValid(req, env)).valid) return new Response("forbidden", { status: 403 });
  const form = await req.clone().formData();
  const from = normalizePhone(form.get("From"));
  const callSid = String(form.get("CallSid") ?? "");
  if (!from || !callSid) return twiml(Hangup());
  const log = (event: string, detail: Record<string, unknown>) => audit(env, event, from, callSid, detail);
  try {
    if (url.pathname === "/voice/reenrollment") return twiml(await startReenrollment(env, ctx, url, form, log));
    if (url.pathname === "/voice/reenrollment/heard") return twiml(await handleReenrollmentHeard(env, ctx, url, form, log));
    if (url.pathname === "/voice/reenrollment/result") return twiml(await handleReenrollmentResult(env, ctx, url, form, log));
  } catch (error) {
    await log("reenrollment.error", { message: (error instanceof Error ? error.message : String(error)).slice(0, 200) });
    // Collected embeddings go with the rounds. A failed round webhook still stops its stream.
    await env.DB.prepare("DELETE FROM challenge_rounds WHERE call_sid = ?").bind(callSid).run().catch(() => {});
    const stop = url.pathname === "/voice/reenrollment/heard" ? reenrollmentStreamStop(url) : "";
    return twiml(stop + Say("Re-enrollment stopped because of an error. Your voice profile is unchanged. Back to Dogwalk.") + Redirect("/voice"));
  }
  return new Response("not found", { status: 404 });
}

// --- /voice/identity: the Identity Check (identity_check.ts) -------------

// Before the Voice Agent: a Home call's check or first-call enrollment, Roaming's challenges
// for a caller who names another Handle, and enrollment at the end of Registration.
async function handleIdentity(req: Request, env: Env, ctx: ExecutionContext, url: URL): Promise<Response> {
  if (url.pathname === "/voice/identity/tone.wav" && req.method === "GET") return practiceTone();
  if (url.pathname === "/voice/identity/received.wav" && req.method === "GET") return practiceReceived();
  const stream = /^\/voice\/identity\/stream\/([A-Za-z0-9._-]{1,300})$/.exec(url.pathname);
  if (stream && req.method === "GET") {
    const signed = (await twilioSignatureValid(req, env, "wss")).valid || (await twilioSignatureValid(req, env)).valid;
    if (!signed) return new Response("forbidden", { status: 403 });
    return handleIdentityStream(req, env, ctx, stream[1]);
  }
  if (req.method !== "POST") return new Response("not found", { status: 404 });
  if (!(await twilioSignatureValid(req, env)).valid) return new Response("forbidden", { status: 403 });
  const form = await req.clone().formData();
  const from = normalizePhone(form.get("From"));
  const callSid = String(form.get("CallSid") ?? "");
  if (!from || !callSid) return twiml(Hangup());
  const log = (event: string, detail: Record<string, unknown>) => audit(env, event, from, callSid, detail);
  const exits: Exits = {
    registration: (heard) => inviteTwiml(env, from, callSid, heard),
    donation: () => {
      ctx.waitUntil(audit(env, "donation.requested", from, callSid, { via: "identity check" }));
      return Redirect("/voice/donation");
    },
  };
  try {
    if (url.pathname === "/voice/identity/heard") return twiml(await handleIdentityHeard(env, ctx, url, form, log, exits));
    if (url.pathname === "/voice/identity/result") return twiml(await handleIdentityResult(env, ctx, url, form, log));
    if (url.pathname === "/voice/identity/who") {
      return twiml(await handleIdentityWho(env, ctx, url, form, log, exits, (await homeUser(env, from))?.id ?? null));
    }
  } catch (error) {
    await log("identity.error", { message: (error instanceof Error ? error.message : String(error)).slice(0, 200) });
    // Shadow mode: an error never keeps a Home caller out. A failed round webhook still stops its stream.
    const stop = url.pathname === "/voice/identity/heard" ? identityStreamStop(url) : "";
    await endIdentity(env, callSid).catch(() => {});
    const path = url.searchParams.get("path");
    if (path === "roaming" || path === "register" || url.pathname === "/voice/identity/who") {
      return twiml(stop + Say("The voice check stopped because of an error. Goodbye.") + Hangup());
    }
    return twiml(stop + Say("The voice check stopped because of an error.") + Redirect("/voice"));
  }
  return new Response("not found", { status: 404 });
}

// An Identity Check that ended in an error: recorded as done (so /voice connects rather than
// retrying it), and its rounds, enrollment embeddings included, deleted.
async function endIdentity(env: Env, callSid: string): Promise<void> {
  await env.DB.batch([
    env.DB.prepare("UPDATE voice_calls SET identity = ? WHERE call_sid = ?")
      .bind(JSON.stringify({ outcome: "error", enforcement: "undecided", shadow: true }), callSid),
    env.DB.prepare("DELETE FROM challenge_rounds WHERE call_sid = ?").bind(callSid),
  ]);
}

// --- /voice/claim: receive spoken words --------------------------------

async function handleClaim(req: Request, env: Env): Promise<Response> {
  const sig = await twilioSignatureValid(req, env);
  if (!sig.valid) return new Response("forbidden", { status: 403 });
  const form = await req.clone().formData();
  const from = normalizePhone(form.get("From"));
  const callSid = String(form.get("CallSid") ?? "");
  if (!from) return twiml(Say("Dogwalk could not verify this phone number. Goodbye.") + Hangup());
  const attempts = attemptsFromRequest(req);
  const speechResult = String(form.get("SpeechResult") ?? "");
  const confidence = Number(form.get("Confidence") ?? "0");

  const spoken = normalizeSpeech(speechResult);
  await audit(env, "claim.speech", from, callSid, {
    attempts,
    confidence,
    wordCount: spoken.length,
  });

  if (spoken.length === 0) {
    return retryOrHangup(env, from, callSid, attempts, "I did not hear anything. ");
  }
  if (asksToDonate(spoken)) {
    await audit(env, "donation.requested", from, callSid, { via: "registration prompt" });
    return twiml(Redirect("/voice/donation"));
  }

  const candidates = await usableInviteCodes(env, from);
  const match = matchInviteCode(spoken, candidates);

  if (!match) {
    return retryOrHangup(
      env,
      from,
      callSid,
      attempts,
      "Those words don't match an invite. ",
    );
  }

  return twiml(await inviteReadback(env, from, callSid, match, attempts));
}

// Read back and ask for confirmation. Stash the matched code in a D1 claim_attempt row so the
// confirm step can pick it up by callSid. The PGPfone ritual: machine echoes what it heard,
// caller confirms.
async function inviteReadback(env: Env, from: string, callSid: string, match: string, attempts: number): Promise<string> {
  await env.DB.prepare(
    `INSERT OR REPLACE INTO claim_attempt (call_sid, phone_number, code_words, ts)
     VALUES (?, ?, ?, ?)`,
  )
    .bind(callSid, from, match, Math.floor(Date.now() / 1000))
    .run();
  const readback = `I heard: ${match.split(" ").join(", ")}. Say yes to confirm, or again to retry.`;
  return GatherSpeech(readback, "/voice/confirm", attempts, ["yes", "again"]);
}

// The Identity Check's way into Registration: an Invite Code heard there goes straight to its
// readback; a mention of an invite asks for the words.
async function inviteTwiml(env: Env, from: string, callSid: string, heard: string[]): Promise<string> {
  await audit(env, "claim.ritual.start", from, callSid, { via: "identity check" });
  const invites = await usableInviteCodes(env, from);
  for (let i = 0; i + WORDS_REQUIRED <= heard.length; i++) {
    const match = matchInviteCode(heard.slice(i, i + WORDS_REQUIRED), invites);
    if (match) return inviteReadback(env, from, callSid, match, 1);
  }
  return GatherSpeech("Say your three invite words.", "/voice/claim", 1, invites.map((i) => i.code_words));
}

// --- /voice/confirm: yes/retry on readback -----------------------------

async function handleConfirm(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const sig = await twilioSignatureValid(req, env);
  if (!sig.valid) return new Response("forbidden", { status: 403 });
  const form = await req.clone().formData();
  const from = normalizePhone(form.get("From"));
  const callSid = String(form.get("CallSid") ?? "");
  if (!from) return twiml(Say("Dogwalk could not verify this phone number. Goodbye.") + Hangup());
  const attempts = attemptsFromRequest(req);
  const speech = String(form.get("SpeechResult") ?? "");
  const said = normalizeSpeech(speech)[0] ?? "";
  const confidence = Number(form.get("Confidence") ?? "0");
  await audit(env, "confirm.speech", from, callSid, { attempts, said, confidence });

  // Look up the pending claim for this call.
  const pending = await env.DB.prepare(
    "SELECT code_words FROM claim_attempt WHERE call_sid = ? AND phone_number = ? AND ts > ?",
  )
    .bind(callSid, from, Math.floor(Date.now() / 1000) - 600)
    .first<{ code_words: string }>();

  if (!pending) {
    // No pending claim; re-enter the ritual.
    await audit(env, "confirm.no_pending", from, callSid, {});
    const invites = await usableInviteCodes(env, from);
    return twiml(GatherSpeech("Let's start over. Say your invite words.", "/voice/claim", 1, invites.map((i) => i.code_words)));
  }

  const affirmed = ["yes", "yeah", "yep", "correct", "confirm", "right", "yup", "affirmative"].includes(said);
  const retry = ["again", "retry", "no", "nope", "replay", "repeat", "wrong"].includes(said);

  if (affirmed) {
    return bindClaim(env, ctx, req, form, from, callSid, pending.code_words);
  }

  if (retry || attempts >= MAX_ATTEMPTS) {
    // Drop the pending claim attempt, return to the gather-words ritual.
    await env.DB.prepare("DELETE FROM claim_attempt WHERE call_sid = ?")
      .bind(callSid)
      .run();
    if (attempts >= MAX_ATTEMPTS) {
      await audit(env, "confirm.max_attempts", from, callSid, {});
      return twiml(Say("Too many attempts. Try again later. Goodbye.") + Hangup());
    }
    await audit(env, "confirm.retry", from, callSid, { attempts });
    const invites = await usableInviteCodes(env, from);
    return twiml(GatherSpeech("Let's try again. Say your invite words.", "/voice/claim", attempts + 1, invites.map((i) => i.code_words)));
  }

  // Unclear response: re-prompt the same readback.
  return twiml(
    GatherSpeech(
      "I didn't catch that. Say yes to confirm, or again to retry.",
      "/voice/confirm",
      attempts + 1,
      ["yes", "again"],
    ),
  );
}

// --- /voice/menu: warm sandbox commands --------------------------------

const STATUS_COMMAND = [
  `awk 'NR==1 {printf "uptime_seconds=%d\\n", $1}' /proc/uptime`,
  `awk '/MemTotal/ {t=$2} /MemAvailable/ {a=$2} END {printf "memory_percent=%.0f\\n", 100*(t-a)/t}' /proc/meminfo`,
  `df -P / | awk 'NR==2 {gsub("%", "", $5); print "disk_percent=" $5}'`,
].join("; ");

const formatDuration = (seconds: number): string => {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days} day${days === 1 ? "" : "s"}, ${hours} hour${hours === 1 ? "" : "s"}`;
  if (hours > 0) return `${hours} hour${hours === 1 ? "" : "s"}, ${minutes} minute${minutes === 1 ? "" : "s"}`;
  return `${minutes} minute${minutes === 1 ? "" : "s"}`;
};

function statusSummary(output: string): string {
  const values = new Map<string, number>();
  for (const line of output.split("\n")) {
    const [key, raw] = line.trim().split("=", 2);
    const value = Number(raw);
    if (key && Number.isFinite(value)) values.set(key, value);
  }
  const uptime = values.get("uptime_seconds");
  const memory = values.get("memory_percent");
  const disk = values.get("disk_percent");
  if (uptime === undefined || memory === undefined || disk === undefined) {
    throw new Error("unrecognized status output");
  }
  return `Your workspace has been up ${formatDuration(uptime)}. Memory is ${memory} percent used. Disk is ${disk} percent used.`;
}

async function handleMenu(req: Request, env: Env): Promise<Response> {
  const sig = await twilioSignatureValid(req, env);
  if (!sig.valid) return new Response("forbidden", { status: 403 });
  const form = await req.clone().formData();
  const from = normalizePhone(form.get("From"));
  const callSid = String(form.get("CallSid") ?? "");
  const user = from && callSid ? await callUser(env, callSid) : null;
  if (!from || !user) {
    return twiml(Say("Dogwalk could not verify this registration. Goodbye.") + Hangup());
  }
  const attempts = attemptsFromRequest(req);
  const command = normalizeSpeech(String(form.get("SpeechResult") ?? "")).join(" ");
  await audit(env, "menu.speech", from, callSid, { attempts, command });

  if (["hang up", "hangup", "goodbye", "bye"].includes(command)) {
    return twiml(Say("Goodbye.") + Hangup());
  }
  if (command === "status") {
    try {
      const assignment = await getAssignment(env, user);
      if (!assignment?.provider_id) return twiml(Redirect("/voice"));
      const sandbox = await getDaytonaSandbox(env, assignment.provider_id);
      if (sandbox.state !== "started") return twiml(Redirect("/voice"));
      const result = await executeInSandbox(env, sandbox, STATUS_COMMAND);
      if (result.exitCode !== 0) throw new Error(`status command exited ${result.exitCode}`);
      const summary = statusSummary(result.result);
      await audit(env, "menu.status", from, callSid, {});
      return twiml(Say(summary) + GatherSpeech("Say status or hang up.", "/voice/menu", 1, MENU_HINTS));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await audit(env, "menu.status.error", from, callSid, { message: message.slice(0, 200) });
      return twiml(Say("I could not read workspace status. Say status to retry, or hang up.") +
        GatherSpeech("Say status or hang up.", "/voice/menu", 1, MENU_HINTS));
    }
  }

  if (attempts >= MAX_ATTEMPTS) {
    return twiml(Say("Too many attempts. Goodbye.") + Hangup());
  }
  return twiml(GatherSpeech("Say status or hang up.", "/voice/menu", attempts + 1, MENU_HINTS));
}

// --- bind: register the caller's phone number ---------------------------

async function bindClaim(
  env: Env,
  ctx: ExecutionContext,
  req: Request,
  form: FormData,
  from: string,
  callSid: string,
  codeWords: string,
): Promise<Response> {
  const now = Math.floor(Date.now() / 1000);
  // Expiry, uses, revocation, and a bound recipient are checked in the same transaction that
  // registers the phone and records the Invitation's redeemer (invitations.ts).
  const clearPendingStatement = env.DB.prepare(
    "DELETE FROM claim_attempt WHERE call_sid = ? AND phone_number = ?",
  ).bind(callSid, from);
  // A fresh Handle can collide with a taken one; the UNIQUE failure rolls the batch back.
  let handle = "";
  let results: D1Result[] = [];
  for (let attempt = 0; attempt < 5 && !results.length; attempt++) {
    [handle] = await availableHandles(env.DB, 1);
    try {
      results = await env.DB.batch([
        ...redemptionStatements(env.DB, from, codeWords, now, handle, newIdentityHash()),
        clearPendingStatement,
      ]);
    } catch (error) {
      if (!String(error).includes("UNIQUE")) throw error;
    }
  }
  if (!results.length) throw new Error("could not choose an unused Handle");
  const [registration, , , redeemed] = results;

  if (registration.meta.changes === 0) {
    await audit(env, "registration.rejected", from, callSid, {});
    return twiml(Say("That invite is no longer available. Goodbye.") + Hangup());
  }

  await audit(env, "registration.success", from, callSid, { invitation: redeemed.meta.changes > 0 });

  // The Handle is how the User will name themselves from any other phone: say it twice. Then
  // Registration enrolls the new User's Voice Reference (identity_check.ts), when it can.
  const registered = `Registered. Your name on Dogwalk is ${handle}. Again: ${handle}. This phone is now your home.`;
  const log = (event: string, detail: Record<string, unknown>) => audit(env, event, from, callSid, detail);
  // The Registration is committed: an enrollment that cannot start still ends with the Handle spoken.
  let enrollment: string | null = null;
  try {
    const newUser = await env.DB.prepare("SELECT id FROM users WHERE handle = ? AND reaped_at IS NULL").bind(handle).first<{ id: number }>();
    enrollment = newUser ? await startRegistrationEnrollment(env, ctx, new URL(req.url), form, newUser.id, log, registered) : null;
  } catch (error) {
    await log("identity.error", { stage: "registration", message: (error instanceof Error ? error.message : String(error)).slice(0, 200) });
    await env.DB.prepare("DELETE FROM challenge_rounds WHERE call_sid = ?").bind(callSid).run().catch(() => {});
  }
  return twiml(enrollment ?? Say(`${registered} Goodbye.`) + Hangup());
}

// --- helpers ------------------------------------------------------------

async function retryOrHangup(
  env: Env,
  from: string,
  callSid: string,
  attempts: number,
  prefix: string,
): Promise<Response> {
  if (attempts >= MAX_ATTEMPTS) {
    await audit(env, "claim.max_attempts", from, callSid, { attempts });
    return twiml(Say("Too many attempts. Try again later. Goodbye.") + Hangup());
  }
  await audit(env, "claim.retry", from, callSid, { attempts });
  const invites = await usableInviteCodes(env, from);
  return twiml(
    GatherSpeech(
      `${prefix}Say your invite words.`,
      "/voice/claim",
      attempts + 1,
      invites.map((i) => i.code_words),
    ),
  );
}

async function audit(
  env: Env,
  event: string,
  phone: string | null,
  callSid: string | null,
  detail: Record<string, unknown>,
): Promise<void> {
  try {
    const tsMs = Date.now();
    const now = Math.floor(tsMs / 1000);
    detail = { ...detail, ts_ms: tsMs, ...producerFields(env) };
    const statements = [env.DB.prepare(
      "INSERT INTO audit_log (ts, event, phone_number, call_sid, detail) VALUES (?, ?, ?, ?, ?)",
    ).bind(now, event, phone, callSid, JSON.stringify(detail))];
    if (callSid) {
      const activity = activityMetadata(event);
      statements.push(
        env.DB.prepare(
          `INSERT INTO call_activity (call_sid, ts, source, direction, event, detail)
           VALUES (?, ?, ?, ?, ?, ?)`,
        ).bind(callSid, now, activity.source, activity.direction, event, JSON.stringify(detail)),
        env.DB.prepare(
          "UPDATE voice_calls SET last_activity_at = ? WHERE call_sid = ?",
        ).bind(now, callSid),
      );
    }
    await env.DB.batch(statements);
  } catch (e) {
    // Audit is best-effort; never fail a request because of it.
    console.error("audit failed", e);
  }
}

function activityMetadata(event: string): {
  source: "voice" | "access" | "hosting" | "menu" | "acp";
  direction: "inbound" | "outbound" | "internal";
} {
  const source = event.startsWith("sandbox.") ? "hosting"
    : event.startsWith("menu.") ? "menu"
    : event.startsWith("registration.") || event.startsWith("caller.") || event.startsWith("claim.") || event.startsWith("confirm.")
      || event.startsWith("identity.") || event.startsWith("reenrollment.") ? "access"
    : event.startsWith("acp.") ? "acp"
    : "voice";
  const direction = event.endsWith(".speech") || event === "call.entered" || event.startsWith("call.")
    ? "inbound"
    : "internal";
  return { source, direction };
}

// --- response helper ----------------------------------------------------

const twiml = (body: string) =>
  new Response(tw(body), {
    headers: { "content-type": "text/xml; charset=utf-8" },
  });
