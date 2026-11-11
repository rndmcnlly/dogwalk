// OTLP/JSON projection of the call_activity journal (issue #4, Phase 1).
//
// The journal stays canonical. This module is a pure, evidence-preserving view of it in
// OpenTelemetry's Stable logs and traces data models, so a stock OTLP receiver can ingest
// Dogwalk observations without a Dogwalk decoder. It never mutates storage or replays actions.
//
// Rules this projection keeps (see the issue for why):
// - Every journal row is one LogRecord. Observations are logs, not zero-duration spans.
// - Spans exist only for exactly matched start/end pairs. A start without an end stays a log
//   annotated `end_not_observed`; missing completion never becomes a completed span.
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

export type ActivityRow = { id: number; ts: number; source: string; event: string; detail: string | null };
export type CallRow = { call_sid: string; started_at: number; ended_at: number | null; status: string };

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
// Producer identity at observation time was not recorded for historical rows, so
// service.version and deployment.environment.name are absent rather than guessed.
const RESOURCE: KeyValue[] = [
  { key: "service.namespace", value: { stringValue: "dogwalk" } },
  { key: "service.name", value: { stringValue: "dogwalk-worker" } },
];

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
const STRUCTURAL = new Set(["event", "ts_ms", "occurred_at_ms", "received_at_ms"]);

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

type Interval = { start: Observation; end: Observation; kind: "model" | "tool" | "turn" | "rpc"; id: unknown };

/** Same exact-identifier scoping the operator diagram uses: never adjacency or order. */
function scopeKey(o: Observation, kind: string, id: unknown): string {
  return JSON.stringify([o.detail.live_session_id ?? "", o.detail.clock_id ?? "", kind, typeof id, id]);
}

function matchIntervals(observations: Observation[]): { intervals: Interval[]; unmatched: Map<number, string> } {
  const open = new Map<string, Observation>();
  const intervals: Interval[] = [];
  const unmatched = new Map<number, string>();
  for (const o of observations) {
    const d = o.detail;
    let kind: Interval["kind"] | null = null, id: unknown, begin = false, finish = false, key = "";
    if (o.name.startsWith("model.") && d.request_id) {
      kind = "model"; id = d.request_id; begin = o.name === "model.started";
      finish = o.name === "model.completed" || o.name === "model.failed";
      key = scopeKey(o, kind, id);
    } else if (o.name.startsWith("tool.") && d.tool_call_id) {
      kind = "tool"; id = d.tool_call_id; begin = o.name === "tool.started";
      finish = o.name === "tool.completed" || o.name === "tool.failed";
      key = scopeKey(o, kind, id);
    } else if (o.name.startsWith("acp.turn.") && d.acp_session_id && finite(d.started_ms)) {
      // A Prompt Turn is identified by its session and recorded start, which every lifecycle
      // row repeats. This survives the end being recorded under a different Voice Call.
      kind = "turn"; id = [d.acp_session_id, d.started_ms]; begin = o.name === "acp.turn.started";
      finish = o.name === "acp.turn.stopped" || o.name === "acp.turn.failed";
      key = JSON.stringify(["turn", id]);
    } else if ((o.name === "acp.sent" || o.name === "acp.received") && d.rpc_id !== undefined && d.rpc_id !== null
      && typeof d.agent_connection_id === "string") {
      // JSON-RPC IDs repeat across reconnects, so roundtrips require Agent Connection identity.
      // Older rows lack it and stay logs. Agent-initiated requests (with method) are not ours.
      kind = "rpc"; id = d.rpc_id;
      begin = o.name === "acp.sent" && typeof d.method === "string";
      finish = o.name === "acp.received" && d.method === undefined;
      key = JSON.stringify([d.agent_connection_id, "rpc", typeof id, id]);
    }
    if (!kind) continue;
    if (begin) {
      if (open.has(key)) unmatched.set(open.get(key)!.row.id, "superseded_start");
      open.set(key, o);
    } else if (finish) {
      const start = open.get(key);
      if (start && o.observedMs >= start.observedMs) {
        open.delete(key);
        intervals.push({ start, end: o, kind, id });
      } else unmatched.set(o.row.id, "start_not_observed");
    }
  }
  for (const o of open.values()) unmatched.set(o.row.id, "end_not_observed");
  return { intervals, unmatched };
}

export type Projection = { logs: (OtlpLogRecord & { rowId: number })[]; spans: OtlpSpan[] };

export async function projectCall(call: CallRow, rows: ActivityRow[]): Promise<Projection> {
  // Insertion order is the matching order; event time is carried, never used to reorder here.
  const observations = [...rows].sort((a, b) => a.id - b.id).map(observe);
  const { intervals, unmatched } = matchIntervals(observations);
  const callTrace = await traceId("voice_call", call.call_sid);
  const callSpanId = await spanId("voice_call", call.call_sid);
  const spans: OtlpSpan[] = [];
  const membership = new Map<number, { traceId: string; spanId: string }>();
  const reconstructed = kv("dogwalk.reconstructed", true)!;
  const callAttr = kv("dogwalk.voice_call.id", call.call_sid)!;

  // A Voice Call span exists only once the call has ended. Second precision, from voice_calls.
  const callStart = msToNanos(call.started_at * 1000);
  const callEnd = call.ended_at !== null ? msToNanos(call.ended_at * 1000) : null;
  const callSpanExported = Boolean(callStart && callEnd && call.ended_at! >= call.started_at);
  if (callSpanExported) {
    spans.push({ traceId: callTrace, spanId: callSpanId, name: "voice_call", kind: SPAN_KIND.internal,
      startTimeUnixNano: callStart!, endTimeUnixNano: callEnd!, status: { code: STATUS.unset },
      attributes: [reconstructed, callAttr, kv("dogwalk.voice_call.status", call.status)!,
        kv("dogwalk.timestamp.precision_ms", 1000)!] });
  }

  const modelSpanByTool = new Map<string, { traceId: string; spanId: string }>();
  for (const interval of intervals) {
    const { start, end, kind } = interval;
    const lifecycle = kind === "turn" && finite(start.detail.started_ms) && finite(end.detail.ended_ms);
    const startNanos = msToNanos(lifecycle ? start.detail.started_ms as number : start.observedMs);
    const endNanos = msToNanos(lifecycle ? end.detail.ended_ms as number : end.observedMs);
    if (!startNanos || !endNanos) continue;
    const precision = Math.max(start.precisionMs, end.precisionMs);
    const attrs: KeyValue[] = [reconstructed, kv("dogwalk.timestamp.precision_ms", precision)!,
      kv("dogwalk.journal.start_row_id", start.row.id)!, kv("dogwalk.journal.end_row_id", end.row.id)!,
      kv("dogwalk.interval.basis", lifecycle ? "recorded_lifecycle" : "worker_observations")!];
    let span: OtlpSpan;
    if (kind === "turn") {
      // Prompt Turns are their own work traces, linked to the call that recorded the start.
      const [session, startedMs] = interval.id as [string, number];
      const trace = await traceId("prompt_turn", session, startedMs);
      const stop = typeof end.detail.stop_reason === "string" ? end.detail.stop_reason : null;
      span = { traceId: trace, spanId: await spanId("prompt_turn", session, startedMs), name: "acp.prompt_turn",
        kind: SPAN_KIND.client, startTimeUnixNano: startNanos, endTimeUnixNano: endNanos,
        status: end.name === "acp.turn.failed" ? { code: STATUS.error }
          : stop === "end_turn" ? { code: STATUS.ok } : { code: STATUS.unset },
        attributes: [...attrs, kv("dogwalk.acp.session.id", session)!, ...(stop ? [kv("dogwalk.acp.stop_reason", stop)!] : [])],
        links: callSpanExported ? [{ traceId: callTrace, spanId: callSpanId,
          attributes: [kv("dogwalk.link.reason", "turn_start_recorded_during_call")!] }] : [] };
    } else {
      // Model requests and Orchestrator tools are part of this call's Orchestrator work in the
      // journal's own scoping (snapshotted at start), so they are children of the call span.
      const parentSpanId = callSpanExported ? callSpanId : undefined;
      const sid = await spanId(kind, call.call_sid, start.detail.live_session_id ?? null,
        start.detail.clock_id ?? null, kind === "rpc" ? start.detail.agent_connection_id : null, interval.id);
      if (kind === "model") {
        const headers = observations.find((o) => o.name === "model.headers"
          && scopeKey(o, "model", o.detail.request_id) === scopeKey(start, "model", interval.id));
        const headersNanos = headers ? msToNanos(headers.observedMs) : null;
        span = { traceId: callTrace, spanId: sid, parentSpanId, name: "orchestrator.model_request",
          kind: SPAN_KIND.client, startTimeUnixNano: startNanos, endTimeUnixNano: endNanos,
          status: { code: end.name === "model.completed" ? STATUS.ok : STATUS.error },
          attributes: [...attrs, kv("dogwalk.model_request.id", interval.id)!,
            kv("dogwalk.interval.includes", "network_and_worker_overhead")!],
          events: headersNanos ? [{ timeUnixNano: headersNanos, name: "model.headers",
            attributes: attributes(headers!.name, headers!.detail).attrs }] : [] };
        for (const id of Array.isArray(end.detail.tool_call_ids) ? end.detail.tool_call_ids : []) {
          modelSpanByTool.set(scopeKey(start, "tool", id), { traceId: callTrace, spanId: sid });
        }
      } else if (kind === "tool") {
        // tool.completed is emitted in `finally`: it proves the call returned or threw, not success.
        span = { traceId: callTrace, spanId: sid, parentSpanId, name: "orchestrator.tool",
          kind: SPAN_KIND.internal, startTimeUnixNano: startNanos, endTimeUnixNano: endNanos,
          status: { code: end.name === "tool.failed" ? STATUS.error : STATUS.unset },
          attributes: [...attrs, kv("dogwalk.tool_call.id", interval.id)!,
            ...(typeof start.detail.name === "string" ? [kv("dogwalk.orchestrator_tool.name", start.detail.name)!] : []),
            kv("dogwalk.outcome", end.name === "tool.failed" ? "failed" : "not_recorded")!] };
        const model = modelSpanByTool.get(scopeKey(start, "tool", interval.id));
        if (model) span.links = [{ ...model, attributes: [kv("dogwalk.link.reason", "requested_by_model_response")!] }];
      } else {
        span = { traceId: callTrace, spanId: sid, parentSpanId, name: `acp ${String(start.detail.method)}`,
          kind: SPAN_KIND.client, startTimeUnixNano: startNanos, endTimeUnixNano: endNanos,
          status: { code: end.detail.error === true ? STATUS.error : STATUS.unset },
          attributes: [...attrs, kv("dogwalk.acp.rpc.id", interval.id)!, kv("dogwalk.acp.method", start.detail.method)!,
            kv("dogwalk.agent_connection.id", start.detail.agent_connection_id)!] };
      }
    }
    spans.push(span);
    membership.set(start.row.id, { traceId: span.traceId, spanId: span.spanId });
    membership.set(end.row.id, { traceId: span.traceId, spanId: span.spanId });
  }

  const logs = observations.flatMap((o) => {
    const observed = msToNanos(o.observedMs);
    if (!observed) return [];
    const { attrs, dropped } = attributes(o.name, o.detail);
    const failed = /(?:^|\.)failed$/.test(o.name);
    const record: OtlpLogRecord & { rowId: number } = {
      rowId: o.row.id,
      observedTimeUnixNano: observed,
      ...(o.sourceMs !== null && msToNanos(o.sourceMs) ? { timeUnixNano: msToNanos(o.sourceMs)! } : {}),
      severityNumber: failed ? 17 : 9, severityText: failed ? "ERROR" : "INFO",
      eventName: o.name,
      attributes: [
        callAttr,
        kv("dogwalk.journal.row_id", o.row.id)!,
        kv("dogwalk.journal.event", o.row.event)!,
        kv("dogwalk.journal.source", o.row.source)!,
        ...(o.provider ? [] : [kv("dogwalk.timestamp.precision_ms", o.precisionMs)!]),
        kv("dogwalk.timestamp.source", o.timeSource)!,
        ...(unmatched.has(o.row.id) ? [kv("dogwalk.interval.state", unmatched.get(o.row.id))!] : []),
        ...attrs,
      ].filter((a, i, all) => all.findIndex((b) => b.key === a.key) === i),
      ...(membership.get(o.row.id) ?? {}),
    };
    if (dropped) record.droppedAttributesCount = dropped;
    return [record];
  });
  return { logs, spans };
}

/** ExportLogsServiceRequest body (OTLP/JSON). */
export function logsRequest(logs: OtlpLogRecord[]) {
  return { resourceLogs: [{ resource: { attributes: RESOURCE },
    scopeLogs: [{ scope: SCOPE, logRecords: logs.map(({ rowId: _rowId, ...log }: OtlpLogRecord & { rowId?: number }) => log) }] }] };
}

/** ExportTraceServiceRequest body (OTLP/JSON). */
export function tracesRequest(spans: OtlpSpan[]) {
  return { resourceSpans: [{ resource: { attributes: RESOURCE }, scopeSpans: [{ scope: SCOPE, spans }] }] };
}
