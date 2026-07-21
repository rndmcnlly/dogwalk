/**
 * Dogwalk Cloudflare Worker
 *
 * TwiML registration, Daytona sandbox lifecycle, warm-call status menu, and
 * Access-protected Mission Control. No OpenAI or OpenCode yet.
 *
 * See TWILIO_CF_DAYTONA.md for the design-fiction transcript this implements.
 */

import { createRemoteJWKSet, jwtVerify } from "jose";
import { ADMIN_HTML } from "./admin";

// --- env ----------------------------------------------------------------

export interface Env {
  DB: D1Database;
  TWILIO_AUTH_TOKEN: string;
  DAYTONA_API_KEY: string;
  DOGWALK_IDENTITY_SECRET: string;
  DAYTONA_API_BASE?: string;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
  ADMIN_PASSWORD?: string;
}

// --- Twilio signature validation ----------------------------------------

/**
 * Validate the Twilio signature header against the request body and URL.
 * Twilio signs with HMAC-SHA1 of `url + params` (sorted, concatenated) using
 * the auth token. The signature is base64. We reconstruct the URL from the
 * incoming request, preferring the x-forwarded-proto/host that Cloudflare
 * sets in front of workers.dev.
 *
 * Reference: https://www.twilio.com/docs/usage/webhooks/webhooks-security
 */
async function twilioSignatureValid(
  req: Request,
  authToken: string,
): Promise<{ valid: boolean; url: string }> {
  const sigHeader = req.headers.get("x-twilio-signature") ?? "";
  if (!sigHeader || !authToken) return { valid: false, url: "" };

  // Reconstruct the URL Twilio signed: the fully-qualified webhook URL it hit.
  // In production (workers.dev), Twilio calls https://... and Cloudflare sets
  // x-forwarded-proto and x-forwarded-host. In local dev, neither header is
  // set, so fall back to the request's own protocol and host.
  const url = new URL(req.url);
  const xfProto = req.headers.get("x-forwarded-proto") ?? url.protocol.replace(":", "");
  const xfHost = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
  const signedUrl = `${xfProto}://${xfHost}${url.pathname}${url.search}`;

  // Twilio sends POST as application/x-www-form-urlencoded for /voice.
  // Clone before formData() consumes the body.
  const form = await req.clone().formData();
  const params: [string, string][] = [];
  for (const [k, v] of form.entries()) {
    if (typeof v === "string") params.push([k, v]);
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

const escapeXml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

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

async function usableInviteCodes(env: Env): Promise<{ code_words: string }[]> {
  const now = Math.floor(Date.now() / 1000);
  const candidates = await env.DB.prepare(
    `SELECT i.code_words
       FROM invite_codes i
      WHERE (i.expires_at IS NULL OR i.expires_at > ?)
        AND (i.max_uses IS NULL OR
             (SELECT COUNT(*) FROM registrations r WHERE r.invite_code = i.code_words) < i.max_uses)
      ORDER BY i.code_words`,
  ).bind(now).all<{ code_words: string }>();
  return candidates.results ?? [];
}

// --- Agent Hosting ------------------------------------------------------

const DAYTONA_LABEL_MANAGED = "dogwalk.dev/managed-by";
const DAYTONA_LABEL_IDENTITY = "dogwalk.dev/identity";
const MAX_WAKE_POLLS = 6;

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
  if (!response.ok) throw new Error(`Daytona ${response.status}: ${(await response.text()).slice(0, 300)}`);
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

  const sandbox = await daytonaFetch<DaytonaSandbox>(env, "/sandbox", {
    method: "POST",
    body: JSON.stringify({
      name: `dogwalk-${hash.slice(0, 16)}`,
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

async function sandboxReady(env: Env, sandbox: DaytonaSandbox): Promise<boolean> {
  if (sandbox.state !== "started") return false;
  try {
    const probe = await executeInSandbox(env, sandbox, "printf ready", 5);
    return probe.exitCode === 0 && probe.result === "ready";
  } catch {
    return false;
  }
}

async function resolveSandbox(env: Env, phone: string): Promise<{ assignment: SandboxAssignment; sandbox?: DaytonaSandbox }> {
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
  const sandbox = await getDaytonaSandbox(env, assignment.provider_id);
  await updateAssignment(env, phone, sandbox);
  return { assignment: (await getAssignment(env, phone))!, sandbox };
}

// --- Mission Control ----------------------------------------------------

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

async function missionControlSnapshot(env: Env, verbose: boolean): Promise<Record<string, unknown>> {
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

async function missionControlState(env: Env, verbose: boolean): Promise<Response> {
  return jsonResponse(await missionControlSnapshot(env, verbose));
}

function missionControlEvents(env: Env, verbose: boolean): Response {
  const encoder = new TextEncoder();
  let active = true;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const pump = async () => {
        const deadline = Date.now() + 5 * 60 * 1000;
        try {
          while (active && Date.now() < deadline) {
            const snapshot = await missionControlSnapshot(env, verbose);
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
    const headers = env.ADMIN_PASSWORD ? { "www-authenticate": 'Basic realm="Dogwalk Mission Control"' } : undefined;
    return new Response("unauthorized", { status: env.ADMIN_PASSWORD ? 401 : 403, headers });
  }
  if (req.method !== "GET") return new Response("method not allowed", { status: 405 });
  if (pathname === "/admin" || pathname === "/admin/") {
    return new Response(ADMIN_HTML, {
      headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
    });
  }
  const verbose = new URL(req.url).searchParams.get("verbose") === "1";
  if (pathname === "/admin/api/state") return missionControlState(env, verbose);
  if (pathname === "/admin/api/events") return missionControlEvents(env, verbose);
  return new Response("not found", { status: 404 });
}

// --- /voice routing -----------------------------------------------------

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);

    // GET /healthz: does not require Twilio validation.
    if (url.pathname === "/healthz") {
      return new Response("ok\n", { headers: { "content-type": "text/plain" } });
    }

    if (url.pathname === "/admin" || url.pathname.startsWith("/admin/")) {
      return handleAdmin(req, env, url.pathname);
    }

    if (url.pathname === "/voice" && req.method === "POST") {
      return handleVoice(req, env);
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

async function handleVoice(req: Request, env: Env): Promise<Response> {
  // Twilio signature check. We clone the body because formData() consumes it.
  const sig = await twilioSignatureValid(req, env.TWILIO_AUTH_TOKEN);
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

  if (await isRegistered(env, from)) {
    await env.DB.prepare("UPDATE registrations SET last_seen_at = ? WHERE phone_number = ?")
      .bind(Math.floor(Date.now() / 1000), from)
      .run();
    await audit(env, "caller.registered", from, callSid, {});
    const wakePoll = Math.max(0, Number(new URL(req.url).searchParams.get("wake") ?? "0") || 0);
    try {
      const { assignment, sandbox } = await resolveSandbox(env, from);
      if (sandbox && await sandboxReady(env, sandbox)) {
        await audit(env, "sandbox.ready", from, callSid, { state: sandbox.state });
        return twiml(GatherSpeech("Workspace awake. Say status or hang up.", "/voice/menu", 1, MENU_HINTS));
      }

      if (sandbox && ["stopped", "archived", "paused"].includes(sandbox.state)) {
        const started = await daytonaFetch<DaytonaSandbox>(env, `/sandbox/${encodeURIComponent(sandbox.id)}/start`, { method: "POST" });
        await updateAssignment(env, from, started);
        await audit(env, "sandbox.start.requested", from, callSid, { state: sandbox.state });
      }

      if (sandbox && ["error", "build_failed", "destroyed"].includes(sandbox.state)) {
        await audit(env, "sandbox.unavailable", from, callSid, { state: sandbox.state });
        return twiml(Say("Your workspace needs operator attention. Goodbye.") + Hangup());
      }

      if (wakePoll >= MAX_WAKE_POLLS) {
        await audit(env, "sandbox.wake.timeout", from, callSid, { state: sandbox?.state ?? assignment.state });
        return twiml(Say("Your workspace is taking longer than expected. Try again in a few minutes. Goodbye.") + Hangup());
      }
      return twiml(
        Say("Welcome back. Waking your workspace, one moment.") +
        Pause(8) +
        Redirect(`/voice?wake=${wakePoll + 1}`),
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await audit(env, "sandbox.error", from, callSid, { message: message.slice(0, 200) });
      return twiml(Say("Dogwalk could not reach your workspace. Try again later. Goodbye.") + Hangup());
    }
  }

  // Unregistered caller: begin the registration ritual.
  await audit(env, "claim.ritual.start", from, callSid, {});
  const invites = await usableInviteCodes(env);
  return twiml(GatherSpeech("Dogwalk. Say your invite words.", "/voice/claim", 1, invites.map((i) => i.code_words)));
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
  const sig = await twilioSignatureValid(req, env.TWILIO_AUTH_TOKEN);
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
  return new Response(null, { status: 204 });
}

// --- /voice/claim: receive spoken words --------------------------------

async function handleClaim(req: Request, env: Env): Promise<Response> {
  const sig = await twilioSignatureValid(req, env.TWILIO_AUTH_TOKEN);
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

  const candidates = await usableInviteCodes(env);
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
  const sig = await twilioSignatureValid(req, env.TWILIO_AUTH_TOKEN);
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
    const invites = await usableInviteCodes(env);
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
    const invites = await usableInviteCodes(env);
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
  const sig = await twilioSignatureValid(req, env.TWILIO_AUTH_TOKEN);
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
  const registrationStatement = env.DB.prepare(
    `INSERT INTO registrations (phone_number, invite_code, registered_at, last_seen_at)
     SELECT ?, i.code_words, ?, ?
       FROM invite_codes i
      WHERE i.code_words = ?
        AND (i.expires_at IS NULL OR i.expires_at > ?)
        AND (i.max_uses IS NULL OR
             (SELECT COUNT(*) FROM registrations r WHERE r.invite_code = i.code_words) < i.max_uses)
        AND NOT EXISTS (SELECT 1 FROM registrations WHERE phone_number = ?)`,
  )
    .bind(from, now, now, codeWords, now, from);
  const clearPendingStatement = env.DB.prepare(
    "DELETE FROM claim_attempt WHERE call_sid = ? AND phone_number = ?",
  ).bind(callSid, from);
  const [registration] = await env.DB.batch([registrationStatement, clearPendingStatement]);

  if (registration.meta.changes === 0) {
    await audit(env, "registration.rejected", from, callSid, {});
    return twiml(Say("That invite is no longer available, or this phone is already registered. Goodbye.") + Hangup());
  }

  await audit(env, "registration.success", from, callSid, {});

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
  const invites = await usableInviteCodes(env);
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
