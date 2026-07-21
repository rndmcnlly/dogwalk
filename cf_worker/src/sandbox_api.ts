import type { Env } from "./types";

const CAPABILITY_PATH = "/home/daytona/.config/dogwalk/sandbox-capability.json";
const MAX_FILES = 16;
const MAX_FILE_BYTES = 512 * 1024;
const MAX_BUNDLE_BYTES = 1024 * 1024;
const REVIEW_TTL_SECONDS = 7 * 24 * 60 * 60;
const SERVICE_LINK_TTL_SECONDS = 60 * 60;

export interface HostingSandbox {
  id: string;
  toolboxProxyUrl?: string;
}

interface SandboxActor {
  phone_number: string;
  provider_id: string;
}

interface ReviewBundleRow {
  id: string;
  title: string;
  default_path: string;
  expires_at: number;
}

interface ServiceRow {
  id: string;
  name: string;
  port: number;
  provider_id: string;
  updated_at: number;
}

export async function handleSandboxApi(request: Request, env: Env): Promise<Response | null> {
  const pathname = new URL(request.url).pathname;
  if (pathname === "/api/sandbox/review-bundles" && request.method === "POST") {
    const actor = await authenticateSandbox(request, env);
    if (!actor) return json({ error: "unauthorized" }, 401);
    return publishReviewBundle(request, env, actor);
  }
  if (pathname === "/api/sandbox/ephemeral-services" && request.method === "POST") {
    const actor = await authenticateSandbox(request, env);
    if (!actor) return json({ error: "unauthorized" }, 401);
    return registerEphemeralService(request, env, actor);
  }
  if (pathname.startsWith("/b/") && ["GET", "HEAD"].includes(request.method)) {
    return serveReviewBundle(request, env);
  }
  return null;
}

export async function provisionSandboxCapability(
  env: Env,
  sandbox: HostingSandbox,
  phone: string,
): Promise<void> {
  const token = await scopedToken(env, `sandbox-capability-v1:${phone}:${sandbox.id}`);
  const tokenHash = await sha256Hex(token);
  const current = await env.DB.prepare(
    "SELECT provider_id, token_hash FROM sandbox_capabilities WHERE phone_number = ?",
  ).bind(phone).first<{ provider_id: string; token_hash: string }>();
  if (current?.provider_id === sandbox.id && current.token_hash === tokenHash) return;

  const proxy = (sandbox.toolboxProxyUrl ?? "https://proxy.app.daytona.io/toolbox").replace(/\/$/, "");
  const base = `${proxy}/${encodeURIComponent(sandbox.id)}`;
  const headers = { authorization: `Bearer ${env.DAYTONA_API_KEY}` };
  const folderUrl = new URL(`${base}/files/folder`);
  folderUrl.searchParams.set("path", "/home/daytona/.config/dogwalk");
  folderUrl.searchParams.set("mode", "0700");
  const folder = await fetch(folderUrl, { method: "POST", headers, signal: AbortSignal.timeout(10_000) });
  if (!folder.ok && folder.status !== 409) throw new Error(`Sandbox capability folder failed (${folder.status})`);

  const credential = JSON.stringify({
    version: 1,
    api_base: env.PUBLIC_ORIGIN ?? "https://dogwalk.tools",
    token,
  }) + "\n";
  const form = new FormData();
  form.append("file", new Blob([credential], { type: "application/json" }), "sandbox-capability.json");
  const uploadUrl = new URL(`${base}/files/upload`);
  uploadUrl.searchParams.set("path", CAPABILITY_PATH);
  const upload = await fetch(uploadUrl, { method: "POST", headers, body: form, signal: AbortSignal.timeout(10_000) });
  if (!upload.ok) throw new Error(`Sandbox capability upload failed (${upload.status})`);

  const permissionsUrl = new URL(`${base}/files/permissions`);
  permissionsUrl.searchParams.set("path", CAPABILITY_PATH);
  permissionsUrl.searchParams.set("owner", "daytona");
  permissionsUrl.searchParams.set("group", "daytona");
  permissionsUrl.searchParams.set("mode", "0600");
  const permissions = await fetch(permissionsUrl, { method: "POST", headers, signal: AbortSignal.timeout(10_000) });
  if (!permissions.ok) throw new Error(`Sandbox capability permissions failed (${permissions.status})`);

  await env.DB.prepare(
    `INSERT INTO sandbox_capabilities (phone_number, provider_id, token_hash, issued_at)
     VALUES (?, ?, ?, unixepoch())
     ON CONFLICT(phone_number) DO UPDATE SET
       provider_id = excluded.provider_id,
       token_hash = excluded.token_hash,
       issued_at = excluded.issued_at`,
  ).bind(phone, sandbox.id, tokenHash).run();
}

export async function listReviewBundles(env: Env, phone: string): Promise<Array<Record<string, unknown>>> {
  const result = await env.DB.prepare(
    `SELECT id, title, default_path, file_count, byte_count, created_at, expires_at
       FROM review_bundles
      WHERE phone_number = ? AND expires_at > unixepoch()
      ORDER BY created_at DESC LIMIT 20`,
  ).bind(phone).all<Record<string, unknown>>();
  return result.results ?? [];
}

export async function deleteReviewBundle(env: Env, phone: string, bundleId: string): Promise<void> {
  const result = await env.DB.batch([
    env.DB.prepare(
      "DELETE FROM review_bundle_files WHERE bundle_id IN (SELECT id FROM review_bundles WHERE id = ? AND phone_number = ?)",
    ).bind(bundleId, phone),
    env.DB.prepare("DELETE FROM review_bundles WHERE id = ? AND phone_number = ?").bind(bundleId, phone),
  ]);
  if (result[1].meta.changes !== 1) throw new Error("Review Bundle is unavailable");
}

export async function textReviewBundle(env: Env, phone: string, bundleId: string): Promise<void> {
  const bundle = await env.DB.prepare(
    `SELECT id, title, default_path, expires_at FROM review_bundles
      WHERE id = ? AND phone_number = ? AND expires_at > unixepoch()`,
  ).bind(bundleId, phone).first<ReviewBundleRow>();
  if (!bundle) throw new Error("Review Bundle is unavailable");
  const token = await reviewToken(env, bundle.id);
  const origin = env.PUBLIC_ORIGIN ?? "https://dogwalk.tools";
  const url = `${origin}/b/${token}/${bundle.default_path.split("/").map(encodeURIComponent).join("/")}`;
  await sendSms(env, phone, `${bundle.title}: ${url}`, "review_bundle", bundle.id);
}

export async function listEphemeralServices(env: Env, phone: string): Promise<Array<Record<string, unknown>>> {
  const result = await env.DB.prepare(
    `SELECT e.id, e.name, e.port, e.updated_at FROM ephemeral_services e
      JOIN sandbox_assignments s ON s.phone_number = e.phone_number AND s.provider_id = e.provider_id
      WHERE e.phone_number = ? AND e.active = 1
      ORDER BY updated_at DESC LIMIT 20`,
  ).bind(phone).all<Record<string, unknown>>();
  return result.results ?? [];
}

export async function textEphemeralService(env: Env, phone: string, serviceId: string): Promise<void> {
  const service = await env.DB.prepare(
    `SELECT e.id, e.name, e.port, e.provider_id, e.updated_at FROM ephemeral_services e
      JOIN sandbox_assignments s ON s.phone_number = e.phone_number AND s.provider_id = e.provider_id
      WHERE e.id = ? AND e.phone_number = ? AND e.active = 1`,
  ).bind(serviceId, phone).first<ServiceRow>();
  if (!service) throw new Error("Ephemeral Service is unavailable");
  const preview = await daytona<{ token: string; url: string }>(
    env,
    `/sandbox/${encodeURIComponent(service.provider_id)}/ports/${service.port}/signed-preview-url?expiresInSeconds=${SERVICE_LINK_TTL_SECONDS}`,
  );
  await sendSms(env, phone, `${service.name}: ${preview.url}`, "ephemeral_service", service.id);
}

export async function sendSms(
  env: Env,
  phone: string,
  body: string,
  kind = "message",
  providerId: string | null = null,
): Promise<{ providerId: string; status: string }> {
  if (!env.TWILIO_ACCOUNT_SID || !env.TWILIO_FROM_NUMBER) throw new Error("SMS is not configured");
  const recent = await env.DB.prepare(
    "SELECT COUNT(*) AS count FROM sms_log WHERE phone_number = ? AND created_at > unixepoch() - 600",
  ).bind(phone).first<{ count: number }>();
  if ((recent?.count ?? 0) >= 5) throw new Error("SMS rate limit reached");
  const base = (env.TWILIO_API_BASE ?? "https://api.twilio.com/2010-04-01").replace(/\/$/, "");
  const response = await fetch(`${base}/Accounts/${encodeURIComponent(env.TWILIO_ACCOUNT_SID)}/Messages.json`, {
    method: "POST",
    headers: {
      authorization: `Basic ${btoa(`${env.TWILIO_ACCOUNT_SID}:${env.TWILIO_AUTH_TOKEN}`)}`,
      "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
    },
    body: new URLSearchParams({
      To: phone,
      From: env.TWILIO_FROM_NUMBER,
      Body: body.slice(0, 800),
      StatusCallback: `${env.PUBLIC_ORIGIN ?? "https://dogwalk.tools"}/sms/status`,
    }),
    signal: AbortSignal.timeout(10_000),
  });
  const result: Record<string, unknown> = await response.json<Record<string, unknown>>().catch(() => ({}));
  if (!response.ok) throw new Error(`SMS provider rejected message (${response.status})`);
  const messageId = String(result.sid ?? providerId ?? "");
  const status = String(result.status ?? "queued");
  await env.DB.prepare(
    `INSERT INTO sms_log (phone_number, kind, provider_id, status, created_at, updated_at)
     VALUES (?, ?, ?, ?, unixepoch(), unixepoch())`,
  ).bind(phone, kind, messageId, status).run();
  return { providerId: messageId, status };
}

async function publishReviewBundle(request: Request, env: Env, actor: SandboxActor): Promise<Response> {
  let body: any;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON" }, 400);
  }
  if (body?.version !== 1 || typeof body.title !== "string" || !body.title.trim() || body.title.length > 80) {
    return json({ error: "invalid bundle metadata" }, 400);
  }
  if (!Array.isArray(body.files) || body.files.length < 1 || body.files.length > MAX_FILES) {
    return json({ error: "invalid file count" }, 400);
  }
  const decoded: Array<{ path: string; mediaType: string; bytes: Uint8Array }> = [];
  const paths = new Set<string>();
  let total = 0;
  for (const file of body.files) {
    if (!validBundlePath(file?.path) || paths.has(file.path) || typeof file.content_base64 !== "string") {
      return json({ error: "invalid file" }, 400);
    }
    let bytes: Uint8Array;
    try {
      bytes = decodeBase64(file.content_base64);
    } catch {
      return json({ error: "invalid file encoding" }, 400);
    }
    if (bytes.byteLength > MAX_FILE_BYTES) return json({ error: "file too large" }, 413);
    total += bytes.byteLength;
    if (total > MAX_BUNDLE_BYTES) return json({ error: "bundle too large" }, 413);
    paths.add(file.path);
    decoded.push({ path: file.path, mediaType: safeMediaType(file.media_type), bytes });
  }
  const sessionId = boundedString(body.context?.session_id, 200);
  const defaultPath = decoded.find((file) => /(^|\/)index\.md$/i.test(file.path))?.path ??
    decoded.find((file) => /\.md$/i.test(file.path))?.path ?? decoded[0].path;
  const id = crypto.randomUUID();
  const now = Math.floor(Date.now() / 1000);
  const tokenHash = await sha256Hex(await reviewToken(env, id));
  const statements = [env.DB.prepare(
    `INSERT INTO review_bundles
       (id, phone_number, public_token_hash, title, session_id, default_path,
        file_count, byte_count, created_at, expires_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  ).bind(id, actor.phone_number, tokenHash, body.title.trim(), sessionId, defaultPath, decoded.length, total, now, now + REVIEW_TTL_SECONDS)];
  for (const file of decoded) {
    statements.push(env.DB.prepare(
      `INSERT INTO review_bundle_files (bundle_id, path, media_type, byte_count, content)
       VALUES (?, ?, ?, ?, ?)`,
    ).bind(id, file.path, file.mediaType, file.bytes.byteLength, file.bytes.buffer));
  }
  await env.DB.batch(statements);
  return json({ ok: true, bundle_id: id, title: body.title.trim(), file_count: decoded.length, expires_in_days: 7 }, 201);
}

async function registerEphemeralService(request: Request, env: Env, actor: SandboxActor): Promise<Response> {
  let body: any;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON" }, 400);
  }
  const name = typeof body?.name === "string" ? body.name.trim() : "";
  const port = Number(body?.port);
  if (body?.version !== 1 || !/^[A-Za-z0-9][A-Za-z0-9 _.-]{0,47}$/.test(name) ||
    !Number.isInteger(port) || port < 1024 || port > 65535 || port === 8765) {
    return json({ error: "invalid service" }, 400);
  }
  const sessionId = boundedString(body.context?.session_id, 200);
  const existing = await env.DB.prepare(
    `SELECT id FROM ephemeral_services
      WHERE phone_number = ? AND provider_id = ? AND name = ? AND port = ? AND active = 1`,
  ).bind(actor.phone_number, actor.provider_id, name, port).first<{ id: string }>();
  const id = existing?.id ?? crypto.randomUUID();
  await env.DB.prepare(
    `INSERT INTO ephemeral_services
       (id, phone_number, provider_id, name, port, session_id, created_at, updated_at, active)
     VALUES (?, ?, ?, ?, ?, ?, unixepoch(), unixepoch(), 1)
     ON CONFLICT(id) DO UPDATE SET name = excluded.name, port = excluded.port,
       session_id = excluded.session_id, updated_at = unixepoch(), active = 1`,
  ).bind(id, actor.phone_number, actor.provider_id, name, port, sessionId).run();
  return json({ ok: true, service_id: id, name, port }, existing ? 200 : 201);
}

async function serveReviewBundle(request: Request, env: Env): Promise<Response> {
  const parts = new URL(request.url).pathname.split("/").filter(Boolean);
  const token = parts[1] ?? "";
  const path = parts.slice(2).map((part) => decodeURIComponent(part)).join("/");
  if (!/^[a-f0-9]{64}$/.test(token) || !validBundlePath(path)) return new Response("not found", { status: 404 });
  const bundle = await env.DB.prepare(
    `SELECT id, title FROM review_bundles
      WHERE public_token_hash = ? AND expires_at > unixepoch()`,
  ).bind(await sha256Hex(token)).first<{ id: string; title: string }>();
  if (!bundle) return new Response("not found", { status: 404 });
  const file = await env.DB.prepare(
    "SELECT media_type, content FROM review_bundle_files WHERE bundle_id = ? AND path = ?",
  ).bind(bundle.id, path).first<{ media_type: string; content: ArrayBuffer | number[] }>();
  if (!file) return new Response("not found", { status: 404 });
  const content = file.content instanceof ArrayBuffer
    ? new Uint8Array(file.content)
    : Uint8Array.from(file.content);
  const headers = new Headers({
    "content-type": file.media_type,
    "cache-control": "private, max-age=300",
    "x-robots-tag": "noindex, nofollow, noarchive",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
  });
  if (request.method === "HEAD") return new Response(null, { headers });
  if (file.media_type.startsWith("text/markdown")) {
    const source = new TextDecoder().decode(content);
    return new Response(markdownPage(bundle.title, path, source), {
      headers: { ...Object.fromEntries(headers), "content-type": "text/html; charset=utf-8" },
    });
  }
  return new Response(content, { headers });
}

async function authenticateSandbox(request: Request, env: Env): Promise<SandboxActor | null> {
  const authorization = request.headers.get("authorization") ?? "";
  if (!authorization.startsWith("Bearer ")) return null;
  const token = authorization.slice(7);
  if (token.length < 32 || token.length > 200) return null;
  return env.DB.prepare(
    `SELECT c.phone_number, c.provider_id
       FROM sandbox_capabilities c
       JOIN sandbox_assignments s ON s.phone_number = c.phone_number
      WHERE c.token_hash = ? AND c.provider_id = s.provider_id`,
  ).bind(await sha256Hex(token)).first<SandboxActor>();
}

async function reviewToken(env: Env, id: string): Promise<string> {
  return scopedToken(env, `review-bundle-v1:${id}`);
}

async function scopedToken(env: Env, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(env.DOGWALK_IDENTITY_SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const bytes = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message)));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function sha256Hex(value: string): Promise<string> {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function daytona<T>(env: Env, path: string): Promise<T> {
  const base = (env.DAYTONA_API_BASE ?? "https://app.daytona.io/api").replace(/\/$/, "");
  const response = await fetch(`${base}${path}`, {
    headers: { authorization: `Bearer ${env.DAYTONA_API_KEY}` },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`Hosting provider rejected request (${response.status})`);
  return response.json<T>();
}

function decodeBase64(value: string): Uint8Array {
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) throw new Error("base64");
  const binary = atob(value);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function validBundlePath(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 500 &&
    !value.startsWith("/") && !value.includes("\\") && !/[\x00-\x1f]/.test(value) &&
    value.split("/").every((part) => part && part !== "." && part !== "..");
}

function safeMediaType(value: unknown): string {
  const type = typeof value === "string" ? value.toLowerCase() : "";
  if (type === "image/png" || type === "image/jpeg") return type;
  if (type.startsWith("text/markdown")) return "text/markdown; charset=utf-8";
  if (type.startsWith("text/")) return "text/plain; charset=utf-8";
  return "application/octet-stream";
}

function boundedString(value: unknown, max: number): string {
  return typeof value === "string" ? value.slice(0, max) : "";
}

function markdownPage(title: string, filename: string, source: string): string {
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)}</title><style>body{max-width:900px;margin:4rem auto;padding:0 1.2rem;background:#f6f3ea;color:#20231d;font:16px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}header{border-bottom:1px solid #bbb;padding-bottom:1rem;margin-bottom:2rem}h1{font:700 2.3rem/1 Georgia,serif}small{color:#666}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body><header><h1>${escapeHtml(title)}</h1><small>${escapeHtml(filename)}</small></header><pre>${escapeHtml(source)}</pre></body></html>`;
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}
