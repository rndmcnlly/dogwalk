// OTLP/JSON projection of the call_activity journal (issue #4, Phase 1).
//
// The journal stays canonical. This module is a pure, evidence-preserving view of it in
// OpenTelemetry's Stable logs and traces data models, so a stock OTLP receiver can ingest
// Dogwalk observations without a Dogwalk decoder. It never mutates storage or replays actions.
//
// Rules this projection keeps (see the issue for why):
// - Every journal row is one LogRecord. Observations are logs, not zero-duration spans.
// - LogRecords are row-local: each depends only on its own row, so cursor pages never go stale.
// - Spans exist only for exactly matched start/end pairs. A start without an end stays a log
//   that names a span never exported; missing completion never becomes a completed span.
// - Source time (`timeUnixNano`) and observation time (`observedTimeUnixNano`) stay separate.
//   An unknown provider occurrence time is omitted, never replaced with receipt time.
// - Nanosecond encoding is BigInt decimal strings. It does not imply nanosecond accuracy:
//   `dogwalk.timestamp.precision_ms` carries serialized precision, not an uncertainty bound.
// - Metadata only: attributes come from an explicit allowlist below. Content, aliases, error
//   strings, thought tails, and anything unlisted are dropped and counted.
// - All spans are reconstructed after the fact with deterministic IDs, marked
//   `dogwalk.reconstructed`. No claim is made that trace context was propagated at the time.
//
// Attribute names are `dogwalk.*` extensions except Stable semantic conventions
// (service.*, http.response.status_code). Development GenAI and RPC conventions wait for
// Phase 2, when a reviewed revision is pinned deliberately.

import type { Env } from "./types";
import { isTraceContext, type TraceContext } from "./trace_context.ts";  // Explicit extension: Node tests load this module directly.

/**
 * Who produced an observation, stamped into its journal detail at write time. The projection
 * turns these into Resource attributes, so an old row never inherits the build that exports it.
 */
export function producerFields(env: Pick<Env, "CF_VERSION_METADATA" | "DEPLOYMENT_ENVIRONMENT">): Record<string, string> {
  const fields: Record<string, string> = {};
  if (env.CF_VERSION_METADATA?.id) fields.service_version = env.CF_VERSION_METADATA.id;
  if (env.DEPLOYMENT_ENVIRONMENT) fields.deployment_environment = env.DEPLOYMENT_ENVIRONMENT;
  return fields;
}

export type ActivityRow = { id: number; ts: number; source: string; event: string; detail: string | null };
export type CallRow = { call_sid: string; started_at: number; ended_at: number | null; status: string;
  trace_id?: string | null; span_id?: string | null };

type AnyValue =
  | { stringValue: string } | { boolValue: boolean } | { intValue: string } | { doubleValue: number }
  | { arrayValue: { values: AnyValue[] } };
type KeyValue = { key: string; value: AnyValue };
type Detail = Record<string, unknown>;

export type OtlpLogRecord = {
  timeUnixNano?: string; observedTimeUnixNano: string; severityNumber: number; severityText: string;
  eventName: string; attributes: KeyValue[]; droppedAttributesCount?: number;
  traceId?: string; spanId?: string;
};
export type OtlpSpan = {
  traceId: string; spanId: string; parentSpanId?: string; name: string; kind: number;
  startTimeUnixNano: string; endTimeUnixNano: string; attributes: KeyValue[];
  events?: { timeUnixNano: string; name: string; attributes: KeyValue[] }[];
  links?: { traceId: string; spanId: string; attributes: KeyValue[] }[];
  status: { code: number; message?: string };
};

export const PROJECTION_VERSION = "1";
const SCOPE = { name: "dogwalk.call_activity.projection", version: PROJECTION_VERSION };
// Producer identity comes only from what a row recorded when written (see producerFields).
// Rows from before that existed get no service.version or deployment.environment.name.
type Producer = { service_version?: string; deployment_environment?: string };

function producerOf(detail: Detail): Producer {
  const p: Producer = {};
  if (typeof detail.service_version === "string" && detail.service_version) p.service_version = detail.service_version;
  if (typeof detail.deployment_environment === "string" && detail.deployment_environment) p.deployment_environment = detail.deployment_environment;
  return p;
}

function resource(p: Producer): { attributes: KeyValue[] } {
  return { attributes: [
    { key: "service.namespace", value: { stringValue: "dogwalk" } },
    { key: "service.name", value: { stringValue: "dogwalk-worker" } },
    ...(p.service_version ? [{ key: "service.version", value: { stringValue: p.service_version } }] : []),
    ...(p.deployment_environment ? [{ key: "deployment.environment.name", value: { stringValue: p.deployment_environment } }] : []),
  ] };
}

/** One Resource per distinct recorded producer, in first-seen order. */
function byProducer<T extends { producer?: Producer }>(items: T[]): { producer: Producer; items: Omit<T, "producer">[] }[] {
  const groups = new Map<string, { producer: Producer; items: Omit<T, "producer">[] }>();
  for (const { producer = {}, ...item } of items) {
    const key = JSON.stringify([producer.service_version ?? null, producer.deployment_environment ?? null]);
    if (!groups.has(key)) groups.set(key, { producer, items: [] });
    groups.get(key)!.items.push(item);
  }
  return [...groups.values()];
}

const SPAN_KIND = { internal: 1, client: 3 } as const;
const STATUS = { unset: 0, ok: 1, error: 2 } as const;

// ---- Typed attributes -------------------------------------------------------------

function anyValue(value: unknown): AnyValue | null {
  if (typeof value === "string") return { stringValue: value };
  if (typeof value === "boolean") return { boolValue: value };
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isSafeInteger(value) ? { intValue: String(value) } : { doubleValue: value };
  }
  if (Array.isArray(value)) {
    const values = value.map(anyValue).filter((v): v is AnyValue => v !== null);
    return values.length === value.length ? { arrayValue: { values } } : null;
  }
  return null;
}

function kv(key: string, value: unknown): KeyValue | null {
  const v = anyValue(value);
  return v ? { key, value: v } : null;
}

// Journal key -> exported attribute key. Anything not listed is dropped. Keys whose meaning
// depends on the event (`status`, `name`, `method`) are mapped per event in `attributes()`.
const ALLOW: Record<string, string> = {
  seq: "dogwalk.observation.sequence",
  clock_id: "dogwalk.clock.id",
  live_session_id: "dogwalk.live_session.id",
  request_id: "dogwalk.model_request.id",
  tool_call_id: "dogwalk.tool_call.id",
  tool_call_ids: "dogwalk.tool_call.ids",
  rpc_id: "dogwalk.acp.rpc.id",
  agent_connection_id: "dogwalk.agent_connection.id",
  session_id: "dogwalk.acp.session.id",
  acp_session_id: "dogwalk.acp.session.id",
  update: "dogwalk.acp.session_update",
  tool_status: "dogwalk.agent_tool.status",
  tool_kind: "dogwalk.agent_tool.kind",
  stop_reason: "dogwalk.acp.stop_reason",
  outcome: "dogwalk.outcome",
  error: "dogwalk.acp.error_response",
  queue_ms: "dogwalk.queue_ms",
  ms: "dogwalk.observed_interval_ms",
  started_ms: "dogwalk.lifecycle.started_ms",
  ended_ms: "dogwalk.lifecycle.ended_ms",
  delegation_id: "dogwalk.delegation.id",
  type: "dogwalk.live.message_type",
  chars: "dogwalk.content.chars",
  content_kind: "dogwalk.content.kind",
  content_truncated: "dogwalk.content.truncated",
  context_items: "dogwalk.orchestrator.context_items",
  queued_events: "dogwalk.orchestrator.queued_events",
  offset_ms: "dogwalk.media.offset_ms",
  start_ms: "dogwalk.media.start_ms",
  end_ms: "dogwalk.media.end_ms",
  media_clock_id: "dogwalk.media.clock_id",
  provider_clock_id: "dogwalk.media.clock_id",
  provider_session_id: "dogwalk.media.session_id",
  // Twilio evidence (provider_timing.ts already sanitizes these at collection).
  provider: "dogwalk.provider.name",
  provider_call_sid: "dogwalk.twilio.call_sid",
  root_call_sid: "dogwalk.twilio.root_call_sid",
  parent_call_sid: "dogwalk.twilio.parent_call_sid",
  leg: "dogwalk.twilio.leg",
  provider_event_key: "dogwalk.provider.event_fingerprint",
  sequence_number: "dogwalk.provider.sequence",
  request_path: "dogwalk.twilio.webhook.path",
  response_code: "dogwalk.twilio.webhook.response_code",
  request_duration_ms: "dogwalk.twilio.webhook.duration_ms",
  timestamp_precision_ms: "dogwalk.timestamp.precision_ms",
  event_time_source: "dogwalk.timestamp.source",
};
// Read for timestamps or span matching, then represented by fields rather than attributes.
const STRUCTURAL = new Set(["event", "ts_ms", "occurred_at_ms", "received_at_ms", "service_version", "deployment_environment",
  "trace_id", "span_id", "parent_span_id", "link_trace_id", "link_span_id"]);

function attributes(name: string, detail: Detail): { attrs: KeyValue[]; dropped: number } {
  const attrs: KeyValue[] = [];
  let dropped = 0;
  const seen = new Set<string>();
  const push = (key: string, value: unknown) => {
    if (value === undefined || value === null || seen.has(key)) return;
    const pair = kv(key, value);
    if (pair) { attrs.push(pair); seen.add(key); } else dropped++;
  };
  for (const [key, value] of Object.entries(detail)) {
    if (STRUCTURAL.has(key) || value === undefined || value === null) continue;
    if (key === "usage" && value && typeof value === "object") {
      const usage = value as Detail;
      push("dogwalk.usage.input_tokens", usage.input_tokens);
      push("dogwalk.usage.output_tokens", usage.output_tokens);
      continue;
    }
    if (key === "status") {
      if (name === "model.headers") push("http.response.status_code", value);
      else if (name.startsWith("provider.twilio.")) push("dogwalk.twilio.call_status", value);
      else dropped++;
      continue;
    }
    if (key === "name" && name.startsWith("tool.")) { push("dogwalk.orchestrator_tool.name", value); continue; }
    if (key === "method") {
      push(name.startsWith("provider.twilio.") ? "dogwalk.twilio.webhook.method" : "dogwalk.acp.method", value);
      continue;
    }
    const mapped = ALLOW[key];
    if (mapped) push(mapped, value); else dropped++;
  }
  return { attrs, dropped };
}

// ---- Time --------------------------------------------------------------------------

/** Integer milliseconds to decimal-string Unix nanoseconds, without float multiplication. */
export function msToNanos(ms: number): string | null {
  return Number.isSafeInteger(ms) && ms > 0 ? (BigInt(ms) * 1_000_000n).toString() : null;
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

type Observation = {
  row: ActivityRow; name: string; detail: Detail; provider: boolean;
  observedMs: number; sourceMs: number | null; precisionMs: number; timeSource: string;
};

function observe(row: ActivityRow): Observation {
  let detail: Detail = {};
  try {
    const parsed: unknown = JSON.parse(row.detail ?? "{}");
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) detail = parsed as Detail;
  } catch { /* malformed detail stays an empty observation */ }
  const name = row.event === "voice.timing" ? String(detail.event ?? "voice.timing") : row.event;
  const provider = name.startsWith("provider.twilio.");
  if (provider) {
    const observedMs = finite(detail.received_at_ms) ? detail.received_at_ms : row.ts * 1000;
    const sourceMs = finite(detail.occurred_at_ms) ? detail.occurred_at_ms : null;
    return { row, name, detail, provider, observedMs, sourceMs,
      precisionMs: finite(detail.timestamp_precision_ms) ? detail.timestamp_precision_ms : 1000,
      timeSource: sourceMs === null ? "unknown" : String(detail.event_time_source ?? "provider") };
  }
  // Worker rows: the event is the Worker's own observation (a send, a receipt, a transition),
  // so its source and observed time are the same Worker clock reading.
  const precise = finite(detail.ts_ms);
  const ms = precise ? detail.ts_ms as number : row.ts * 1000;
  return { row, name, detail, provider, observedMs: ms, sourceMs: ms,
    precisionMs: precise ? 1 : 1000, timeSource: precise ? "worker.observation" : "worker.journal_seconds" };
}

// ---- Deterministic IDs -------------------------------------------------------------

async function digestHex(parts: unknown[], bytes: number): Promise<string> {
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256",
    new TextEncoder().encode(JSON.stringify(["dogwalk.otlp", PROJECTION_VERSION, ...parts]))));
  const id = hash.slice(0, bytes);
  if (id.every((b) => b === 0)) id[bytes - 1] = 1; // All-zero IDs are invalid in W3C/OTel.
  return Array.from(id, (b) => b.toString(16).padStart(2, "0")).join("");
}
const traceId = (...parts: unknown[]) => digestHex(["trace", ...parts], 16);
const spanId = (...parts: unknown[]) => digestHex(["span", ...parts], 8);

// ---- Projection --------------------------------------------------------------------

type Kind = "model" | "tool" | "turn" | "rpc";
/** The interval a row belongs to, read from that row alone. */
type Role = { kind: Kind; id: unknown; key: string; begin: boolean; finish: boolean };

/** Same exact-identifier scoping the operator diagram uses: never adjacency or order. */
function scopeKey(o: Observation, kind: string, id: unknown): string {
  return JSON.stringify([o.detail.live_session_id ?? "", o.detail.clock_id ?? "", kind, typeof id, id]);
}

/** Context the Worker recorded when the operation started (issue #8, B1), if this row has it. */
function recorded(o: Observation): (TraceContext & { parentSpanId?: string }) | null {
  const context = { traceId: o.detail.trace_id as string, spanId: o.detail.span_id as string };
  if (!isTraceContext(context)) return null;
  const parent = o.detail.parent_span_id;
  return typeof parent === "string" && /^(?!0{16})[0-9a-f]{16}$/.test(parent) ? { ...context, parentSpanId: parent } : context;
}

/** Recorded rows match by their span ID; older rows by the scoped identifiers below. */
function role(o: Observation): Role | null {
  const r = derivedRole(o);
  const context = r && recorded(o);
  return r && context ? { ...r, key: JSON.stringify(["span", context.traceId, context.spanId]) } : r;
}

function derivedRole(o: Observation): Role | null {
  const d = o.detail;
  if (o.name.startsWith("model.") && d.request_id) {
    return { kind: "model", id: d.request_id, key: scopeKey(o, "model", d.request_id),
      begin: o.name === "model.started", finish: o.name === "model.completed" || o.name === "model.failed" };
  }
  if (o.name.startsWith("tool.") && d.tool_call_id) {
    return { kind: "tool", id: d.tool_call_id, key: scopeKey(o, "tool", d.tool_call_id),
      begin: o.name === "tool.started", finish: o.name === "tool.completed" || o.name === "tool.failed" };
  }
  if (o.name.startsWith("acp.turn.") && d.acp_session_id && finite(d.started_ms)) {
    // A Prompt Turn is identified by its session and recorded start, which every lifecycle
    // row repeats. This survives the end being recorded under a different Voice Call.
    const id = [d.acp_session_id, d.started_ms];
    return { kind: "turn", id, key: JSON.stringify(["turn", id]),
      begin: o.name === "acp.turn.started", finish: o.name === "acp.turn.stopped" || o.name === "acp.turn.failed" };
  }
  if (d.rpc_id !== undefined && d.rpc_id !== null && typeof d.agent_connection_id === "string") {
    // JSON-RPC IDs repeat across reconnects, so roundtrips require Agent Connection identity;
    // older rows lack it and stay unlinked logs. Only our requests and the Agent's responses
    // count: Agent-initiated requests and our replies to them share the ID space but not the span.
    const begin = o.name === "acp.sent" && typeof d.method === "string";
    const finish = o.name === "acp.received" && d.method === undefined;
    if (begin || finish) return { kind: "rpc", id: d.rpc_id, begin, finish,
      key: JSON.stringify([d.agent_connection_id, "rpc", typeof d.rpc_id, d.rpc_id]) };
  }
  return null;
}

/** The Voice Call's context: recorded by the front door when the call began, else derived. */
async function callContextOf(call: CallRow): Promise<TraceContext & { recorded: boolean }> {
  const context = { traceId: call.trace_id ?? undefined, spanId: call.span_id ?? undefined };
  return isTraceContext(context) ? { ...context, recorded: true }
    : { traceId: await traceId("voice_call", call.call_sid), spanId: await spanId("voice_call", call.call_sid), recorded: false };
}

/** Span identity derived from any row of the interval, so a start row knows it before the end exists. */
async function identity(call: CallRow, o: Observation, r: Role): Promise<{ traceId: string; spanId: string }> {
  const context = recorded(o);
  if (context) return { traceId: context.traceId, spanId: context.spanId };
  if (r.kind === "turn") {
    const [session, startedMs] = r.id as [string, number];
    return { traceId: await traceId("prompt_turn", session, startedMs), spanId: await spanId("prompt_turn", session, startedMs) };
  }
  // Older rows keep stable derived IDs, independent of the call's recorded context. Identity
  // hashes exactly the matching key, nothing incidental: an RPC response observed after the
  // voice session changed must still name its request's span.
  return { traceId: await traceId("voice_call", call.call_sid), spanId: await spanId(r.kind, call.call_sid, r.key) };
}

/**
 * Row-local LogRecords: each depends only on its own row and call, never on rows written
 * later, so a page fetched by insertion cursor during a live call never goes stale. A start
 * row carries its span's ID at once; if the end never arrives, the log points at a span that
 * is never exported, which is valid OTel and the honest picture.
 */
export type ProjectedLog = OtlpLogRecord & { rowId: number; producer: Producer };
export type ProjectedSpan = OtlpSpan & { producer: Producer };

export async function projectLogs(call: CallRow, rows: ActivityRow[]): Promise<ProjectedLog[]> {
  const callAttr = kv("dogwalk.voice_call.id", call.call_sid)!;
  const logs: ProjectedLog[] = [];
  for (const row of [...rows].sort((a, b) => a.id - b.id)) {
    const o = observe(row);
    const observed = msToNanos(o.observedMs);
    if (!observed) continue;
    const { attrs, dropped } = attributes(o.name, o.detail);
    const failed = /(?:^|\.)failed$/.test(o.name);
    const r = role(o);
    const source = o.sourceMs !== null ? msToNanos(o.sourceMs) : null;
    const record: ProjectedLog = {
      rowId: o.row.id, producer: producerOf(o.detail),
      observedTimeUnixNano: observed,
      ...(source ? { timeUnixNano: source } : {}),
      severityNumber: failed ? 17 : 9, severityText: failed ? "ERROR" : "INFO",
      eventName: o.name,
      attributes: [
        callAttr,
        kv("dogwalk.journal.row_id", o.row.id)!,
        kv("dogwalk.journal.event", o.row.event)!,
        kv("dogwalk.journal.source", o.row.source)!,
        ...(o.provider ? [] : [kv("dogwalk.timestamp.precision_ms", o.precisionMs)!]),
        kv("dogwalk.timestamp.source", o.timeSource)!,
        ...attrs,
      ].filter((a, i, all) => all.findIndex((b) => b.key === a.key) === i),
      ...(r ? await identity(call, o, r) : {}),
    };
    if (dropped) record.droppedAttributesCount = dropped;
    logs.push(record);
  }
  return logs;
}

type Interval = { start: Observation; end: Observation; role: Role };

function matchIntervals(observations: Observation[]): Interval[] {
  const open = new Map<string, { o: Observation; role: Role }>();
  const intervals: Interval[] = [];
  for (const o of observations) {
    const r = role(o);
    if (!r) continue;
    if (r.begin) open.set(r.key, { o, role: r });
    else if (r.finish) {
      const start = open.get(r.key);
      if (start && o.observedMs >= start.o.observedMs) {
        open.delete(r.key);
        intervals.push({ start: start.o, end: o, role: start.role });
      }
    }
  }
  return intervals;
}

/**
 * Span IDs of recorded starts in these rows whose end is not among them. Work can end under a
 * later Voice Call (a Prompt Turn, a slow request), and the journal keeps each row where it
 * was observed; the export looks these IDs up across calls to complete the interval.
 */
export function openRecordedSpanIds(rows: ActivityRow[]): string[] {
  const open = new Set<string>();
  for (const o of [...rows].sort((a, b) => a.id - b.id).map(observe)) {
    const r = role(o), context = recorded(o);
    if (!r || !context) continue;
    if (r.begin) open.add(context.spanId);
    else if (r.finish) open.delete(context.spanId);
  }
  return [...open];
}

/** Spans for exactly matched intervals only. Needs every row of the call (up to the cap). */
export async function projectSpans(call: CallRow, rows: ActivityRow[]): Promise<ProjectedSpan[]> {
  // Insertion order is the matching order; event time is carried, never used to reorder here.
  const observations = [...rows].sort((a, b) => a.id - b.id).map(observe);
  const { traceId: callTrace, spanId: callSpanId, recorded: callRecorded } = await callContextOf(call);
  const spans: ProjectedSpan[] = [];
  const reconstructed = kv("dogwalk.reconstructed", true)!;

  // A Voice Call span exists only once the call has ended. Second precision, from voice_calls.
  const callStart = msToNanos(call.started_at * 1000);
  const callEnd = call.ended_at !== null ? msToNanos(call.ended_at * 1000) : null;
  const callSpanExported = Boolean(callStart && callEnd && call.ended_at! >= call.started_at);
  if (callSpanExported) {
    // voice_calls rows record no producer, so the call span has none.
    spans.push({ producer: {}, traceId: callTrace, spanId: callSpanId, name: "voice_call", kind: SPAN_KIND.internal,
      startTimeUnixNano: callStart!, endTimeUnixNano: callEnd!, status: { code: STATUS.unset },
      attributes: [reconstructed, kv("dogwalk.context", callRecorded ? "recorded_at_start" : "derived")!,
        kv("dogwalk.voice_call.id", call.call_sid)!,
        kv("dogwalk.voice_call.status", call.status)!, kv("dogwalk.timestamp.precision_ms", 1000)!] });
  }

  const modelSpanByTool = new Map<string, { traceId: string; spanId: string }>();
  for (const { start, end, role: r } of matchIntervals(observations)) {
    const lifecycle = r.kind === "turn" && finite(start.detail.started_ms) && finite(end.detail.ended_ms);
    const startNanos = msToNanos(lifecycle ? start.detail.started_ms as number : start.observedMs);
    const endNanos = msToNanos(lifecycle ? end.detail.ended_ms as number : end.observedMs);
    if (!startNanos || !endNanos) continue;
    const ids = await identity(call, start, r);
    const context = recorded(start);
    const attrs: KeyValue[] = [reconstructed, kv("dogwalk.context", context ? "recorded_at_start" : "derived")!, kv("dogwalk.timestamp.precision_ms", Math.max(start.precisionMs, end.precisionMs))!,
      kv("dogwalk.journal.start_row_id", start.row.id)!, kv("dogwalk.journal.end_row_id", end.row.id)!,
      kv("dogwalk.interval.basis", lifecycle ? "recorded_lifecycle" : "worker_observations")!];
    // The span belongs to whoever observed its start; a later build that observed the end is in end_row_id.
    const base = { ...ids, producer: producerOf(start.detail), startTimeUnixNano: startNanos, endTimeUnixNano: endNanos };
    // Recorded context names its own parent. Older model, tool, and RPC rows are this call's
    // Orchestrator work in the journal's own scoping, so they derive the call span as parent,
    // unless the call's context is recorded: then the call is in another trace, so link instead.
    const legacyInRecordedCall = !context && callRecorded && callSpanExported && r.kind !== "turn";
    const parentSpanId = context ? context.parentSpanId : callSpanExported && !callRecorded ? callSpanId : undefined;
    const callLink = legacyInRecordedCall
      ? [{ traceId: callTrace, spanId: callSpanId, attributes: [kv("dogwalk.link.reason", "observed_during_call")!] }] : [];
    if (r.kind === "turn") {
      // Prompt Turns are their own work traces, linked to the call that recorded the start.
      const stop = typeof end.detail.stop_reason === "string" ? end.detail.stop_reason : null;
      const link = context ? { traceId: start.detail.link_trace_id as string, spanId: start.detail.link_span_id as string }
        : callSpanExported ? { traceId: callTrace, spanId: callSpanId } : null;
      spans.push({ ...base, name: "acp.prompt_turn", kind: SPAN_KIND.client,
        status: end.name === "acp.turn.failed" ? { code: STATUS.error }
          : stop === "end_turn" ? { code: STATUS.ok } : { code: STATUS.unset },
        attributes: [...attrs, kv("dogwalk.acp.session.id", (r.id as unknown[])[0])!, ...(stop ? [kv("dogwalk.acp.stop_reason", stop)!] : [])],
        links: link && isTraceContext(link) ? [{ ...link,
          attributes: [kv("dogwalk.link.reason", "turn_started_during_call")!] }] : [] });
    } else if (r.kind === "model") {
      const headers = observations.find((o) => o.name === "model.headers" && role(o)?.key === r.key);
      const headersNanos = headers ? msToNanos(headers.observedMs) : null;
      spans.push({ ...base, parentSpanId, links: callLink, name: "orchestrator.model_request", kind: SPAN_KIND.client,
        status: { code: end.name === "model.completed" ? STATUS.ok : STATUS.error },
        attributes: [...attrs, kv("dogwalk.model_request.id", r.id)!,
          kv("dogwalk.interval.includes", "network_and_worker_overhead")!],
        events: headersNanos ? [{ timeUnixNano: headersNanos, name: "model.headers",
          attributes: attributes(headers!.name, headers!.detail).attrs }] : [] });
      for (const id of Array.isArray(end.detail.tool_call_ids) ? end.detail.tool_call_ids : []) {
        modelSpanByTool.set(scopeKey(start, "tool", id), ids);  // Keyed by scope: tools may carry recorded IDs.
      }
    } else if (r.kind === "tool") {
      // Status follows the outcome the tool reported. Rows written before outcomes were recorded
      // came from a `finally` block, which proves the tool ended, not that it worked: Unset.
      const model = modelSpanByTool.get(scopeKey(start, "tool", r.id));
      const outcome = typeof end.detail.outcome === "string" ? end.detail.outcome
        : end.name === "tool.failed" ? "failed" : "not_recorded";
      spans.push({ ...base, parentSpanId, name: "orchestrator.tool", kind: SPAN_KIND.internal,
        status: { code: outcome === "ok" ? STATUS.ok
          : outcome === "error_result" || outcome === "threw" || outcome === "failed" ? STATUS.error : STATUS.unset },
        attributes: [...attrs, kv("dogwalk.tool_call.id", r.id)!,
          ...(typeof start.detail.name === "string" ? [kv("dogwalk.orchestrator_tool.name", start.detail.name)!] : []),
          kv("dogwalk.outcome", outcome)!],
        links: [...callLink, ...(model ? [{ ...model, attributes: [kv("dogwalk.link.reason", "requested_by_model_response")!] }] : [])] });
    } else {
      spans.push({ ...base, parentSpanId, links: callLink, name: `acp ${String(start.detail.method)}`, kind: SPAN_KIND.client,
        status: { code: end.detail.error === true ? STATUS.error : STATUS.unset },
        attributes: [...attrs, kv("dogwalk.acp.rpc.id", r.id)!, kv("dogwalk.acp.method", start.detail.method)!,
          kv("dogwalk.agent_connection.id", start.detail.agent_connection_id)!] });
    }
  }
  return spans;
}

/** ExportLogsServiceRequest body (OTLP/JSON): one ResourceLogs per recorded producer. */
export function logsRequest(logs: (OtlpLogRecord & { rowId?: number; producer?: Producer })[]) {
  return { resourceLogs: byProducer(logs).map(({ producer, items }) => ({ resource: resource(producer),
    scopeLogs: [{ scope: SCOPE, logRecords: items.map(({ rowId: _rowId, ...log }) => log) }] })) };
}

/** ExportTraceServiceRequest body (OTLP/JSON): one ResourceSpans per recorded producer. */
export function tracesRequest(spans: (OtlpSpan & { producer?: Producer })[]) {
  return { resourceSpans: byProducer(spans).map(({ producer, items }) => ({ resource: resource(producer),
    scopeSpans: [{ scope: SCOPE, spans: items }] })) };
}
