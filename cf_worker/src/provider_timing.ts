import type { Env } from './types';

// This adapter writes observations, never Voice Call lifecycle state. Receipt time
// orders the activity cursor; provider time remains a separately named clock.
type ProviderEnv = Env & { TWILIO_API_BASE?: string };
type Job = {
  account_sid: string; provider_call_sid: string; root_call_sid: string;
  parent_call_sid: string | null; leg: 'pstn' | 'sip';
  next_metadata_at: number | null; events_after: number | null;
  metadata_generation: number;
  events_done: number; attempts: number; lease_until: number;
};
const ACCOUNT = /^AC[0-9a-f]{32}$/i;
const CALL = /^CA[0-9a-f]{32}$/i;
const STATUSES = new Set(['queued', 'initiated', 'ringing', 'in-progress', 'completed', 'busy', 'failed', 'no-answer', 'canceled']);
const TERMINAL = new Set(['completed', 'busy', 'failed', 'no-answer', 'canceled']);
const EVENTS_DELAY = 16 * 60_000;
const MAX_ATTEMPTS = 8;

/** Twilio's RFC 2822 dates have second precision. Never guess a missing clock. */
export function normalizeTimestamp(value: unknown): number | null {
  if (typeof value !== 'string' || !/^(?:[A-Za-z]{3}, )?\d{1,2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} (?:GMT|UTC|[+-]\d{4})$/.test(value)) return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

/** Only known static webhook routes survive; unknown paths may hold capabilities. */
export function sanitizePath(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 4096) return null;
  try {
    const url = new URL(value, 'https://redacted.invalid');
    if (!['http:', 'https:'].includes(url.protocol)) return null;
    const path = url.pathname;
    return /^\/voice(?:\/(?:after-live|recovery(?:\/confirm)?|claim|confirm|menu|status|provider-status))?\/?$/.test(path) ? path : null;
  } catch { return null; }
}

async function fingerprint(fields: unknown): Promise<string> {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(fields)));
  return Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, '0')).join('');
}

function context(job: Pick<Job, 'provider_call_sid' | 'root_call_sid' | 'parent_call_sid' | 'leg'>) {
  return {
    provider: 'twilio', provider_call_sid: job.provider_call_sid,
    root_call_sid: job.root_call_sid, parent_call_sid: job.parent_call_sid,
    leg: job.leg, clock_id: 'twilio',
  };
}

async function record(env: Env, job: Parameters<typeof context>[0], event: string,
  occurred: number | null, received: number, source: string, keyFields: unknown,
  extra: Record<string, unknown> = {}): Promise<boolean> {
  const detail = {
    ...context(job), ...extra,
    provider_event_key: await fingerprint(keyFields),
    occurred_at_ms: occurred, received_at_ms: received, ts_ms: received,
    timestamp_precision_ms: occurred === null ? null : 1000,
    event_time_source: occurred === null ? 'unknown' : source,
  };
  const result = await env.DB.prepare(`INSERT OR IGNORE INTO call_activity
    (call_sid, ts, source, direction, event, detail) VALUES (?, ?, 'voice', 'internal', ?, ?)`)
    .bind(job.root_call_sid, Math.floor(received / 1000), event, JSON.stringify(detail)).run();
  return result.meta.changes > 0;
}

/** Trusted operator helper. Child correlation must be explicit, never inferred. */
export async function queueTwilioCall(env: Env, accountSid: string, callSid: string,
  rootCallSid: string, parentCallSid: string | null = null, endedAtMs: number | null = null): Promise<void> {
  if (!ACCOUNT.test(accountSid) || !CALL.test(callSid) || !CALL.test(rootCallSid)
    || (parentCallSid !== null && !CALL.test(parentCallSid))) return;
  if (parentCallSid === null ? callSid !== rootCallSid : parentCallSid !== rootCallSid || callSid === rootCallSid) return;
  const now = Date.now();
  const eventsAfter = endedAtMs !== null && Number.isFinite(endedAtMs) ? endedAtMs + EVENTS_DELAY : null;
  await env.DB.prepare(`INSERT INTO provider_call_jobs
    (account_sid, provider_call_sid, root_call_sid, parent_call_sid, leg, next_metadata_at, events_after)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(account_sid, provider_call_sid) DO UPDATE SET
      events_after = coalesce(provider_call_jobs.events_after, excluded.events_after)
    WHERE provider_call_jobs.root_call_sid = excluded.root_call_sid
      AND provider_call_jobs.parent_call_sid IS excluded.parent_call_sid`)
    .bind(accountSid, callSid, rootCallSid, parentCallSid, parentCallSid ? 'sip' : 'pstn', now, eventsAfter).run();
}

/** Caller must already have verified the Twilio signature and existing root. */
export async function observeTwilio(env: Env, form: FormData, rootCallSid: string, receivedMs: number): Promise<void> {
  const account = form.get('AccountSid');
  const sid = form.get('CallSid');
  const parent = form.get('ParentCallSid');
  if (typeof account !== 'string' || !ACCOUNT.test(account) || typeof sid !== 'string'
    || !CALL.test(sid) || !CALL.test(rootCallSid) || !Number.isFinite(receivedMs)) return;
  if (parent !== null && (typeof parent !== 'string' || !CALL.test(parent))) return;
  if (parent === null ? sid !== rootCallSid : parent !== rootCallSid || sid === rootCallSid) return;
  const status = form.get('CallStatus');
  const safeStatus = typeof status === 'string' && STATUSES.has(status) ? status : null;
  await queueTwilioCall(env, account, sid, rootCallSid, parent, safeStatus && TERMINAL.has(safeStatus) ? receivedMs : null);
  if (!safeStatus) return;
  const occurred = normalizeTimestamp(form.get('Timestamp'));
  const rawSeq = form.get('SequenceNumber');
  const seq = typeof rawSeq === 'string' && /^\d+$/.test(rawSeq) && Number.isSafeInteger(Number(rawSeq)) ? Number(rawSeq) : null;
  const job = { provider_call_sid: sid, root_call_sid: rootCallSid, parent_call_sid: parent, leg: parent ? 'sip' as const : 'pstn' as const };
  const inserted = await record(env, job, `provider.twilio.callback.${safeStatus}`, occurred, receivedMs, 'callback.Timestamp',
    [account, sid, rootCallSid, job.leg, 'callback', safeStatus, seq, occurred], { sequence_number: seq, status: safeStatus });
  if (inserted) {
    // An in-flight fetch may describe an earlier callback. Its generation cannot
    // consume this refresh. New observations also cannot erase failure backoff.
    await env.DB.prepare(`UPDATE provider_call_jobs SET metadata_generation = metadata_generation + 1,
      next_metadata_at = CASE WHEN attempts > 0 THEN coalesce(next_metadata_at, max(coalesce(events_after, ?), ?))
        ELSE min(coalesce(next_metadata_at, ?), ?) END
      WHERE account_sid = ? AND provider_call_sid = ? AND root_call_sid = ?`)
      .bind(receivedMs, receivedMs, receivedMs, receivedMs, account, sid, rootCallSid).run();
  }
}

class ProviderError extends Error {}
function baseUrl(env: ProviderEnv): URL {
  let url: URL;
  try { url = new URL(env.TWILIO_API_BASE || 'https://api.twilio.com/2010-04-01'); }
  catch { throw new ProviderError('invalid_api_base'); }
  // Explicit local HTTP is useful for the operator's mock server; never downgrade remote credentials.
  if ((url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)))
    || url.username || url.password || url.search || url.hash) throw new ProviderError('invalid_api_base');
  url.pathname = url.pathname.replace(/\/$/, '');
  return url;
}

async function getJson(env: Env, job: Job, url: URL): Promise<Record<string, unknown>> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(url, {
      headers: { Authorization: `Basic ${btoa(`${job.account_sid}:${env.TWILIO_AUTH_TOKEN}`)}` },
      // workerd rejects redirect:'error' before any I/O. Manual plus the status
      // check below rejects every redirect without forwarding credentials.
      redirect: 'manual', signal: controller.signal,
    });
    if (!response.ok) throw new ProviderError(`http_${response.status}`);
    const data: unknown = await response.json();
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new ProviderError('invalid_json');
    return data as Record<string, unknown>;
  } catch (error) {
    if (error instanceof ProviderError) throw error;
    throw new ProviderError(controller.signal.aborted ? 'request_timeout' : 'request_failed');
  } finally { clearTimeout(timeout); }
}

function resourceUrl(base: URL, job: Job, events = false): URL {
  return new URL(`${base.href}/Accounts/${job.account_sid}/Calls/${job.provider_call_sid}${events ? '/Events' : ''}.json`);
}

async function metadata(env: Env, job: Job, base: URL): Promise<void> {
  const body = await getJson(env, job, resourceUrl(base, job));
  for (const [name, expected] of [['sid', job.provider_call_sid], ['account_sid', job.account_sid], ['parent_call_sid', job.parent_call_sid]] as const) {
    if (body[name] !== undefined && body[name] !== expected) throw new ProviderError('resource_mismatch');
  }
  const received = Date.now();
  for (const [field, kind] of [['date_created', 'created'], ['start_time', 'start_time'], ['end_time', 'end_time']] as const) {
    const occurred = normalizeTimestamp(body[field]);
    // A missing field is not evidence that the provider observed an event.
    if (occurred === null) continue;
    await record(env, job, `provider.twilio.call.${kind}`, occurred, received, `call.${field}`,
      [job.account_sid, job.provider_call_sid, kind, occurred]);
  }
  const terminal = typeof body.status === 'string' && TERMINAL.has(body.status);
  await env.DB.prepare(`UPDATE provider_call_jobs SET next_metadata_at =
      CASE WHEN metadata_generation = ? THEN ? ELSE coalesce(next_metadata_at, ?) END,
    events_after = coalesce(events_after, ?)
    WHERE account_sid = ? AND provider_call_sid = ? AND lease_until = ?`)
    .bind(job.metadata_generation, terminal ? null : received + 60_000, received, terminal ? received + EVENTS_DELAY : null,
      job.account_sid, job.provider_call_sid, job.lease_until).run();
}

function nextPage(value: unknown, expected: URL, base: URL): URL | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value !== 'string') throw new ProviderError('invalid_next_page');
  let next: URL;
  try { next = new URL(value, `${base.href}/`); } catch { throw new ProviderError('invalid_next_page'); }
  if (next.origin !== expected.origin || next.pathname !== expected.pathname || next.username || next.password || next.hash)
    throw new ProviderError('invalid_next_page');
  // Pagination cursors stay in memory and are never logged or persisted.
  return next;
}

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function nonnegative(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

async function events(env: Env, job: Job, base: URL): Promise<void> {
  const expected = resourceUrl(base, job, true);
  let url: URL | null = new URL(`${expected.href}?PageSize=1000`);
  let count = 0;
  const visited = new Set<string>();
  for (let page = 0; url && page < 10; page++) {
    if (visited.has(url.href)) throw new ProviderError('pagination_loop');
    visited.add(url.href);
    const body = await getJson(env, job, url);
    if (!Array.isArray(body.events)) throw new ProviderError('invalid_events');
    const received = Date.now();
    for (const entry of body.events) {
      const item = object(entry);
      // Some resources include identity; when supplied it must match this job.
      if ((item.call_sid !== undefined && item.call_sid !== job.provider_call_sid)
        || (item.account_sid !== undefined && item.account_sid !== job.account_sid)) throw new ProviderError('resource_mismatch');
      const request = object(item.request), response = object(item.response);
      const method = typeof request.method === 'string' && /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)$/i.test(request.method) ? request.method.toUpperCase() : null;
      const code = typeof response.response_code === 'number' && Number.isInteger(response.response_code)
        && response.response_code >= 100 && response.response_code <= 599 ? response.response_code : null;
      const occurred = normalizeTimestamp(response.date_created);
      const fields = { method, request_path: sanitizePath(request.url), response_code: code,
        request_duration_ms: nonnegative(response.request_duration) };
      await record(env, job, 'provider.twilio.webhook.exchange', occurred, received, 'response.date_created',
        [job.account_sid, job.provider_call_sid, 'webhook.exchange', occurred, fields], fields);
      count++;
    }
    url = nextPage(body.next_page_uri, expected, base);
  }
  if (url) throw new ProviderError('pagination_limit');
  if (!count) throw new ProviderError('events_empty');
  await env.DB.prepare(`UPDATE provider_call_jobs SET events_done = 1, attempts = 0, last_error = NULL
    WHERE account_sid = ? AND provider_call_sid = ? AND lease_until = ?`)
    .bind(job.account_sid, job.provider_call_sid, job.lease_until).run();
}

/** Small cron batch, with a compare-and-set database lease shared by all Workers. */
export async function processTwilioJobs(env: Env): Promise<void> {
  const now = Date.now();
  const jobs = await env.DB.prepare(`SELECT * FROM provider_call_jobs WHERE lease_until <= ? AND attempts < ?
    AND ((next_metadata_at IS NOT NULL AND next_metadata_at <= ?)
      OR (events_done = 0 AND events_after IS NOT NULL AND events_after <= ?))
    ORDER BY coalesce(next_metadata_at, events_after) LIMIT 4`).bind(now, MAX_ATTEMPTS, now, now).all<Job>();
  // At most four jobs, each at most eleven eight-second requests. Concurrent jobs
  // finish inside the five-minute lease even with a slow provider.
  await Promise.all(jobs.results.map(async candidate => {
    const lease = Date.now() + 5 * 60_000;
    const job = await env.DB.prepare(`UPDATE provider_call_jobs SET lease_until = ?
      WHERE account_sid = ? AND provider_call_sid = ? AND lease_until <= ? RETURNING *`)
      .bind(lease, candidate.account_sid, candidate.provider_call_sid, now).first<Job>();
    if (!job) return;
    try {
      const base = baseUrl(env);
      if (job.next_metadata_at !== null && job.next_metadata_at <= now) await metadata(env, job, base);
      if (!job.events_done && job.events_after !== null && job.events_after <= now) await events(env, job, base);
    } catch (error) {
      const code = error instanceof ProviderError ? error.message : 'storage_failed';
      const retry = Date.now() + Math.min(6 * 60 * 60_000, 60_000 * 2 ** Math.min(job.attempts, 8));
      await env.DB.prepare(`UPDATE provider_call_jobs SET attempts = attempts + 1, last_error = ?,
        next_metadata_at = CASE WHEN next_metadata_at IS NULL THEN NULL ELSE max(next_metadata_at, ?) END,
        events_after = CASE WHEN events_after IS NULL THEN NULL ELSE max(events_after, ?) END
        WHERE account_sid = ? AND provider_call_sid = ? AND lease_until = ?`)
        .bind(code, retry, retry, job.account_sid, job.provider_call_sid, lease).run();
    } finally {
      await env.DB.prepare(`UPDATE provider_call_jobs SET lease_until = 0
        WHERE account_sid = ? AND provider_call_sid = ? AND lease_until = ?`)
        .bind(job.account_sid, job.provider_call_sid, lease).run();
    }
  }));
}
