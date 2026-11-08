/**
 * Dogwalk Cloudflare Worker
 *
 * TwiML registration, Daytona sandbox lifecycle, Durable Object voice/ACP
 * coordination, and the Access-protected read-only Diagnostics surface.
 * See PSTN_CLOUDFLARE.md for the deployed architecture.
 */

import { createRemoteJWKSet, jwtVerify } from "jose";
import { ADMIN_HTML } from "./admin";
import { LANDING_HTML } from "./landing";
import { PAIR_HTML } from "./pair_page";
import { handleSounds } from "./sounds_page";
import {
  DECODE_FAILURE_KEEP_ROWS, DECODE_FAILURE_MAX_BYTES, describeBrowser, describePlace, sha256Hex, splitCredential,
} from "./pairing";
import type { Env } from "./types";
import { handleTerminalAssets, TERMINAL_HTML } from "./terminal_page";
import { TerminalRelay } from "./terminal_relay";
import { redemptionStatements, USABLE_FOR_PHONE_SQL } from "./invitations";
import { frontendInstructions, VoiceSession } from "./voice_session";
import { DEFAULT_THEME, THEMES } from "./themes";
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
 * own origin.
 *
 * Reference: https://www.twilio.com/docs/usage/webhooks/webhooks-security
 */
async function twilioSignatureValid(
  req: Request,
  env: Env,
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
  const scheme = url.protocol.replace(":", "");
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
  `<Dial action="/voice/after-live" method="POST"><Sip>${escapeXml(uri)}</Sip></Dial>`;

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

interface SandboxAssignment {
  phone_number: string;
  provider_id: string | null;
  identity_hash: string;
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

async function identityHash(env: Env, phone: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(env.DOGWALK_IDENTITY_SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const bytes = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(phone)));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function getAssignment(env: Env, phone: string): Promise<SandboxAssignment | null> {
  return env.DB.prepare(
    `SELECT phone_number, provider_id, identity_hash, state, error,
            provisioning_started_at, last_checked_at
       FROM sandbox_assignments WHERE phone_number = ?`,
  ).bind(phone).first<SandboxAssignment>();
}

async function updateAssignment(env: Env, phone: string, sandbox: DaytonaSandbox): Promise<void> {
  await env.DB.prepare(
    `UPDATE sandbox_assignments
        SET provider_id = ?, state = ?, error = ?, last_checked_at = ?
      WHERE phone_number = ?`,
  ).bind(
    sandbox.id,
    sandbox.state,
    sandbox.errorReason ?? null,
    Math.floor(Date.now() / 1000),
    phone,
  ).run();
}

async function findDaytonaSandboxes(env: Env, hash: string): Promise<DaytonaSandbox[]> {
  const labels = JSON.stringify({
    [DAYTONA_LABEL_MANAGED]: "dogwalk",
    [DAYTONA_LABEL_IDENTITY]: `phone-hmac-v1:${hash}`,
  });
  const query = new URLSearchParams({ labels, limit: "3" });
  const result = await daytonaFetch<{ items: DaytonaSandbox[] }>(env, `/sandbox?${query}`);
  return result.items;
}

async function ensureSandboxAssignment(env: Env, phone: string): Promise<SandboxAssignment> {
  const hash = await identityHash(env, phone);
  const now = Math.floor(Date.now() / 1000);
  const inserted = await env.DB.prepare(
    `INSERT OR IGNORE INTO sandbox_assignments
       (phone_number, identity_hash, state, provisioning_started_at)
     VALUES (?, ?, 'provisioning', ?)`,
  ).bind(phone, hash, now).run();
  let assignment = await getAssignment(env, phone);
  if (!assignment) throw new Error("sandbox assignment insert failed");
  if (assignment.provider_id) return assignment;

  const discovered = await findDaytonaSandboxes(env, hash);
  if (discovered.length > 1) {
    await env.DB.prepare(
      "UPDATE sandbox_assignments SET state = 'conflict', error = ? WHERE phone_number = ?",
    ).bind("multiple matching Daytona sandboxes", phone).run();
    throw new Error("multiple matching Daytona sandboxes");
  }
  if (discovered.length === 1) {
    await updateAssignment(env, phone, discovered[0]);
    return (await getAssignment(env, phone))!;
  }

  let ownsProvisioning = inserted.meta.changes === 1;
  if (!ownsProvisioning && now - assignment.provisioning_started_at >= 30) {
    const claimed = await env.DB.prepare(
      `UPDATE sandbox_assignments SET provisioning_started_at = ?, state = 'provisioning', error = NULL
        WHERE phone_number = ? AND provider_id IS NULL AND provisioning_started_at = ?`,
    ).bind(now, phone, assignment.provisioning_started_at).run();
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
        [DAYTONA_LABEL_IDENTITY]: `phone-hmac-v1:${hash}`,
      },
      public: false,
      autoStopInterval: 15,
      autoArchiveInterval: 10080,
      autoDeleteInterval: -1,
    }),
  });
  await updateAssignment(env, phone, sandbox);
  return (await getAssignment(env, phone))!;
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
  phone: string,
  replaceMissing = true,
): Promise<{ assignment: SandboxAssignment; sandbox?: DaytonaSandbox }> {
  const assignment = await ensureSandboxAssignment(env, phone);
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
        WHERE phone_number = ? AND provider_id = ?`,
    ).bind(now, phone, assignment.provider_id).run();
    if (cleared.meta.changes === 1) {
      await audit(env, "sandbox.missing", phone, null, { provider_id: assignment.provider_id });
    }
    return resolveSandbox(env, phone, false);
  }
  await updateAssignment(env, phone, sandbox);
  return { assignment: (await getAssignment(env, phone))!, sandbox };
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

const jsonResponse = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status,
  headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});

async function diagnosticsSnapshot(env: Env, verbose: boolean): Promise<Record<string, unknown>> {
  const registrationResult = await env.DB.prepare(
    `SELECT r.phone_number, r.registered_at, r.last_seen_at,
            s.provider_id, s.identity_hash, s.state, s.error,
            s.created_at AS sandbox_created_at, s.last_checked_at
       FROM registrations r
       LEFT JOIN sandbox_assignments s ON s.phone_number = r.phone_number
      ORDER BY r.registered_at DESC`,
  ).all<Record<string, unknown>>();
  const registrations = registrationResult.results ?? [];

  await Promise.all(registrations.map(async (row) => {
    const providerId = row.provider_id;
    if (typeof providerId !== "string") return;
    try {
      const sandbox = await getDaytonaSandbox(env, providerId);
      row.state = sandbox.state;
      row.error = sandbox.errorReason ?? null;
      row.desired_state = sandbox.desiredState ?? null;
      await updateAssignment(env, String(row.phone_number), sandbox);
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
         FROM call_activity WHERE call_sid = ?
        ORDER BY id DESC LIMIT 30`,
    ).bind(String(call.call_sid)).all<Record<string, unknown>>();
    call.activity = (activity.results ?? []).reverse();
  }));

  const snapshot = {
    generated_at: Math.floor(Date.now() / 1000),
    verbose,
    registrations,
    invites: inviteSummary,
    live_calls: liveCalls,
    audit: auditResult.results ?? [],
  };
  if (verbose) return snapshot;
  return {
    ...snapshot,
    registrations: registrations.map((row) => ({
      ...row,
      phone_number: maskPhone(String(row.phone_number)),
      provider_id: row.provider_id ? "assigned" : null,
      identity_hash: undefined,
      error: row.error ? "operator attention required" : null,
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

async function handleAdmin(req: Request, env: Env, pathname: string): Promise<Response> {
  if (!(await adminAuthorized(req, env))) {
    const headers = env.ADMIN_PASSWORD ? { "www-authenticate": 'Basic realm="Dogwalk Diagnostics"' } : undefined;
    return new Response("unauthorized", { status: env.ADMIN_PASSWORD ? 401 : 403, headers });
  }
  if (req.method !== "GET") return new Response("method not allowed", { status: 405 });
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
    const calls = await env.DB.prepare(
      `SELECT call_sid, status, started_at, ended_at, duration_seconds FROM voice_calls ORDER BY started_at DESC LIMIT ?`,
    ).bind(limit).all<Record<string, unknown>>();
    const withActivity = await Promise.all(calls.results.map(async (call) => ({
      ...call,
      activity: (await env.DB.prepare(
        "SELECT ts, source, event, detail FROM call_activity WHERE call_sid = ? ORDER BY id",
      ).bind(call.call_sid).all()).results,
    })));
    return jsonResponse(withActivity);
  }
  if (pathname === "/admin/api/conversations") {
    // Conversation content is never part of the redacted default lens.
    if (!verbose) return new Response("conversations require verbose=1", { status: 400 });
    const assignments = await env.DB.prepare("SELECT identity_hash FROM sandbox_assignments").all<{ identity_hash: string }>();
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
  if (url.pathname === "/pair/file" && req.method === "GET") {
    const credential = splitCredential(req.headers.get("authorization")?.replace(/^Bearer\s+/i, "") ?? null);
    if (!credential || !(await knownIdentity(env, credential.identity))) return jsonResponse({ error: "this pairing has expired" }, 401);
    return voiceSessionFor(env, credential.identity).fetch(`https://voice-session/pair/file${url.search}`, {
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
  const row = await env.DB.prepare("SELECT 1 AS ok FROM sandbox_assignments WHERE identity_hash = ?").bind(identity).first();
  return Boolean(row);
}

function voiceSessionFor(env: Env, identity: string): DurableObjectStub {
  return env.VOICE_SESSIONS.get(env.VOICE_SESSIONS.idFromName(identity));
}

// --- /voice routing -----------------------------------------------------

export default {
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
      return handleAfterLive(req, env);
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
      return handleConfirm(req, env);
    }
    if (url.pathname === "/voice/menu" && req.method === "POST") {
      return handleMenu(req, env);
    }
    if (url.pathname === "/voice/status" && req.method === "POST") {
      return handleCallStatus(req, env);
    }
    return new Response("not found", { status: 404 });
  },
};

// --- main /voice entrypoint --------------------------------------------

async function handleVoice(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
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

  if (await isRegistered(env, from)) {
    await env.DB.prepare("UPDATE registrations SET last_seen_at = ? WHERE phone_number = ?")
      .bind(Math.floor(Date.now() / 1000), from)
      .run();
    await audit(env, "caller.registered", from, callSid, {});
    // Talk first: the caller reaches the Voice Agent at once while the Sandbox wakes in
    // parallel. Only a Sandbox the provider reports broken goes to deterministic recovery.
    try {
      const { assignment, sandbox } = await resolveSandbox(env, from);
      if (sandbox && ["error", "build_failed", "destroyed"].includes(sandbox.state)) {
        await audit(env, "sandbox.unavailable", from, callSid, { state: sandbox.state });
        return recoveryMenu("Workspace unavailable. ");
      }
      if (sandbox && ["stopped", "archived", "paused"].includes(sandbox.state)) {
        const started = await daytonaFetch<DaytonaSandbox>(env, `/sandbox/${encodeURIComponent(sandbox.id)}/start`, { method: "POST" });
        await updateAssignment(env, from, started);
        await audit(env, "sandbox.start.requested", from, callSid, { state: sandbox.state });
      } else {
        const state = sandbox?.state ?? assignment.state;
        await audit(env, state === "started" ? "sandbox.ready" : "sandbox.waking", from, callSid, { state });
      }
      // The VoiceSession starts reaching the Agent while Twilio and GPT-Live set up the call.
      if (assignment.provider_id) {
        ctx.waitUntil(prewarmVoiceSession(env, assignment.identity_hash, from, assignment.provider_id));
      }
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
    "This is Dogwalk, a voice line for supervising coding agents. If you have an invite, say your three invite words now.",
    "/voice/claim", 1, invites.map((i) => i.code_words),
  ));
}

async function prewarmVoiceSession(env: Env, identity: string, phone: string, sandboxId: string): Promise<void> {
  try {
    const object = env.VOICE_SESSIONS.get(env.VOICE_SESSIONS.idFromName(identity));
    await object.fetch("https://voice-session/prewarm", {
      method: "POST",
      headers: { "x-dogwalk-phone": phone, "x-dogwalk-sandbox-id": sandboxId, "x-dogwalk-identity": identity },
    });
  } catch (error) {
    console.error("voice session prewarm failed", error);
  }
}

async function handleAfterLive(req: Request, env: Env): Promise<Response> {
  const signature = await twilioSignatureValid(req, env);
  if (!signature.valid) return new Response("forbidden", { status: 403 });
  const form = await req.clone().formData();
  const from = normalizePhone(form.get("From"));
  const callSid = String(form.get("CallSid") ?? "");
  if (!from || !callSid) return twiml(Hangup());
  const handoff = await env.DB.prepare(
    "SELECT action FROM call_handoffs WHERE call_sid = ? AND phone_number = ?",
  ).bind(callSid, from).first<{ action: string }>();
  await env.DB.prepare("DELETE FROM call_handoffs WHERE call_sid = ?").bind(callSid).run();
  if (handoff?.action === "hangup") return twiml(Hangup());
  // A theme switch: back through /voice to a fresh GPT-Live session with the new voice.
  if (handoff?.action === "rejoin") return twiml(Redirect("/voice"));
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
  if (!from || !(await isRegistered(env, from))) return twiml(Hangup());
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
    const assignment = await getAssignment(env, from);
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
  if (!from || String(form.get("Digits") ?? "") !== "3" || !(await isRegistered(env, from))) {
    return recoveryMenu("Cancelled. ");
  }
  const assignment = await getAssignment(env, from);
  if (assignment?.provider_id) {
    try {
      await daytonaFetch(env, `/sandbox/${encodeURIComponent(assignment.provider_id)}`, { method: "DELETE" });
    } catch (error) {
      if (!(error instanceof DaytonaApiError) || error.status !== 404) return recoveryMenu("Replacement failed. ");
    }
    await env.DB.prepare(
      `UPDATE sandbox_assignments SET provider_id = NULL, state = 'missing', error = NULL,
         provisioning_started_at = 0, last_checked_at = unixepoch() WHERE phone_number = ?`,
    ).bind(from).run();
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
  const assignment = await env.DB.prepare(
    `SELECT s.phone_number, s.provider_id, s.identity_hash
       FROM voice_calls c
       JOIN sandbox_assignments s ON s.phone_number = c.phone_number
       JOIN registrations r ON r.phone_number = s.phone_number
      WHERE c.call_sid = ?`,
  ).bind(context.callSid).first<{ phone_number: string; provider_id: string | null; identity_hash: string }>();
  if (assignment && !assignment.provider_id) {
    // Another request may have been provisioning when /voice ran; it has had a few seconds.
    try {
      assignment.provider_id = (await resolveSandbox(env, assignment.phone_number)).assignment.provider_id;
    } catch (error) {
      console.error("sandbox resolution at accept failed", error);
    }
  }
  if (!assignment?.provider_id) {
    await decideLiveCall(env, liveSessionId, "reject", { status_code: 486 });
    return new Response(null, { status: 204 });
  }

  const object = env.VOICE_SESSIONS.get(env.VOICE_SESSIONS.idFromName(assignment.identity_hash));
  const headers = new Headers({
    "x-dogwalk-phone": assignment.phone_number,
    "x-dogwalk-sandbox-id": assignment.provider_id,
    "x-dogwalk-call-sid": context.callSid,
    "x-dogwalk-live-session-id": liveSessionId,
    "x-dogwalk-identity": assignment.identity_hash,
  });
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
    await audit(env, "voice.live.attach.failed", assignment.phone_number, context.callSid, {
      status: attached.status,
      detail: (await attached.text()).slice(0, 200),
    });
    return new Response("sideband attach failed", { status: 500 });
  }
  await audit(env, "voice.live.accepted", assignment.phone_number, context.callSid, { live_session_id: liveSessionId });
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
  await env.DB.prepare(
    `INSERT INTO voice_calls
       (call_sid, phone_number, status, started_at, last_activity_at)
     VALUES (?, ?, 'in-progress', ?, ?)
     ON CONFLICT(call_sid) DO UPDATE SET
       phone_number = excluded.phone_number,
       status = CASE WHEN voice_calls.ended_at IS NULL THEN 'in-progress' ELSE voice_calls.status END,
       last_activity_at = excluded.last_activity_at`,
  ).bind(callSid, phone, now, now).run();
}

async function handleCallStatus(req: Request, env: Env): Promise<Response> {
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
  await env.DB.prepare(
    `INSERT INTO voice_calls
       (call_sid, phone_number, status, started_at, last_activity_at, ended_at, duration_seconds)
     VALUES (?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(call_sid) DO UPDATE SET
       status = excluded.status,
       last_activity_at = excluded.last_activity_at,
       ended_at = excluded.ended_at,
       duration_seconds = excluded.duration_seconds`,
  ).bind(callSid, from, status, now, now, terminal ? now : null, duration || null).run();
  await audit(env, `call.${status}`, from, callSid, duration ? { duration } : {});
  if (terminal) {
    // The telephone call is over: make sure the VoiceSession has noticed, even if the
    // GPT-Live sideband ended without telling it.
    try {
      const assignment = await getAssignment(env, from);
      if (assignment?.identity_hash) {
        const object = env.VOICE_SESSIONS.get(env.VOICE_SESSIONS.idFromName(assignment.identity_hash));
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

  // Read back and ask for confirmation. Stash the matched code in a D1
  // claim_attempt row so the confirm step can pick it up by callSid.
  // The PGPfone ritual: machine echoes what it heard, caller confirms.
  const words = match.split(" ");
  await env.DB.prepare(
    `INSERT OR REPLACE INTO claim_attempt (call_sid, phone_number, code_words, ts)
     VALUES (?, ?, ?, ?)`,
  )
    .bind(callSid, from, match, Math.floor(Date.now() / 1000))
    .run();

  const readback = `I heard: ${words.join(", ")}. Say yes to confirm, or again to retry.`;
  return twiml(
    GatherSpeech(readback, "/voice/confirm", attempts, ["yes", "again"]),
  );
}

// --- /voice/confirm: yes/retry on readback -----------------------------

async function handleConfirm(req: Request, env: Env): Promise<Response> {
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
    return bindClaim(env, from, callSid, pending.code_words);
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
  if (!from || !(await isRegistered(env, from))) {
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
      const assignment = await getAssignment(env, from);
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
  const [registration, redeemed] = await env.DB.batch([...redemptionStatements(env.DB, from, codeWords, now), clearPendingStatement]);

  if (registration.meta.changes === 0) {
    await audit(env, "registration.rejected", from, callSid, {});
    return twiml(Say("That invite is no longer available, or this phone is already registered. Goodbye.") + Hangup());
  }

  await audit(env, "registration.success", from, callSid, { invitation: redeemed.meta.changes > 0 });

  return twiml(
    Say("Registered. This phone number can now use Dogwalk. Goodbye.") + Hangup(),
  );
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

async function isRegistered(env: Env, phone: string): Promise<boolean> {
  return (await env.DB.prepare("SELECT 1 FROM registrations WHERE phone_number = ?")
    .bind(phone)
    .first()) !== null;
}

async function audit(
  env: Env,
  event: string,
  phone: string,
  callSid: string | null,
  detail: Record<string, unknown>,
): Promise<void> {
  try {
    const now = Math.floor(Date.now() / 1000);
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
    : event.startsWith("registration.") || event.startsWith("caller.") || event.startsWith("claim.") || event.startsWith("confirm.") ? "access"
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
