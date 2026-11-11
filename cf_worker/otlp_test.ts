// Synthetic OTLP projection test: node --experimental-strip-types cf_worker/otlp_test.ts [out_dir]
// With out_dir, also writes fixture logs.json and traces.json for `otlp_send.py --file`.
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { childOf, formatTraceparent, newRootContext, parseTraceparent, spanFields } from "./src/trace_context.ts";
import { type ActivityRow, type CallRow, logsRequest, msToNanos, openRecordedSpanIds, projectLogs, projectSpans, tracesRequest } from "./src/otlp.ts";

const projectCall = async (call: CallRow, rows: ActivityRow[]) => ({ logs: await projectLogs(call, rows), spans: await projectSpans(call, rows) });

const CALL_A = "CAfixture000000000000000000000000a";
const CALL_B = "CAfixture000000000000000000000000b";
const T0 = 1_790_000_000_000; // Synthetic epoch milliseconds.
const LIVE = "rtc_fixture", CLOCK = "clock-1";
let id = 0;
const timing = (event: string, ms: number, detail: Record<string, unknown> = {}) => ({
  id: ++id, ts: Math.floor(ms / 1000), source: "voice", event: "voice.timing",
  detail: JSON.stringify({ ...detail, event, ts_ms: ms, clock_id: CLOCK, seq: id, live_session_id: LIVE }),
});
const record = (event: string, ms: number, detail: Record<string, unknown>) => ({
  id: ++id, ts: Math.floor(ms / 1000), source: event.startsWith("acp.") ? "acp" : "voice", event,
  detail: JSON.stringify({ ...detail, ts_ms: ms, live_session_id: LIVE }),
});
const twilio = (event: string, received: number, occurred: number | null, extra: Record<string, unknown> = {}) => ({
  id: ++id, ts: Math.floor(received / 1000), source: "voice", event,
  detail: JSON.stringify({ provider: "twilio", provider_call_sid: CALL_A, root_call_sid: CALL_A, parent_call_sid: null,
    leg: "pstn", clock_id: "twilio", provider_event_key: "f".repeat(64), occurred_at_ms: occurred,
    received_at_ms: received, ts_ms: received, timestamp_precision_ms: occurred === null ? null : 1000,
    event_time_source: occurred === null ? "unknown" : "callback.Timestamp", ...extra }),
});

const SECRET = "https://example.invalid/signed?X-Amz-Signature=deadbeef";
const turnStart = T0 + 9000;
const rowsA = [
  timing("live.received", T0 + 100, { type: "conversation.item.input_audio_transcription.delta",
    content_text: "please open my repo", content_kind: "caller", start_ms: 1200, end_ms: 1900, provider_session_id: LIVE }),
  timing("model.started", T0 + 1000, { request_id: "req-1", context_items: 4 }),
  timing("model.headers", T0 + 1800, { request_id: "req-1", status: 200, ms: 800 }),
  timing("model.completed", T0 + 2000, { request_id: "req-1", ms: 1000, tool_call_ids: ["call-1", "call-2"] }),
  record("orchestrator.model", T0 + 2001, { request_id: "req-1", usage: { input_tokens: 1234, output_tokens: 56 } }),
  timing("tool.started", T0 + 2010, { tool_call_id: "call-1", name: "send_prompt" }),
  timing("tool.completed", T0 + 2500, { tool_call_id: "call-1", name: "send_prompt", ms: 490 }),
  timing("tool.started", T0 + 2020, { tool_call_id: "call-2", name: "say" }), // Never completes.
  timing("model.started", T0 + 3000, { request_id: "req-2" }),
  timing("model.failed", T0 + 3500, { request_id: "req-2", ms: 500 }),
  // Same rpc_id on two Agent Connections must not cross-match.
  timing("acp.sent", T0 + 4000, { rpc_id: 7, method: "session/prompt", session_id: "ses_1", agent_connection_id: "conn-a",
    content_text: "do the thing", content_kind: "acp_prompt" }),
  timing("acp.sent", T0 + 4100, { rpc_id: 7, method: "session/prompt", session_id: "ses_2", agent_connection_id: "conn-b" }),
  timing("acp.received", T0 + 4300, { rpc_id: 7, agent_connection_id: "conn-b", error: false }),
  timing("acp.received", T0 + 4600, { rpc_id: 7, agent_connection_id: "conn-a", error: true }),
  // Legacy rows without connection identity stay logs.
  timing("acp.sent", T0 + 4700, { rpc_id: 8, method: "session/cancel" }),
  timing("acp.received", T0 + 4800, { rpc_id: 8 }),
  timing("acp.received", T0 + 4900, { method: "session/update", session_id: "ses_1", update: "agent_message_chunk",
    content_text: "I changed three files", content_kind: "agent_report", agent_connection_id: "conn-a" }),
  record("acp.turn.started", turnStart, { alias: "biscuit", acp_session_id: "ses_1", started_ms: turnStart }),
  record("orchestrator.failed", T0 + 9500, { message: `fetch ${SECRET} failed` }),
  { id: ++id, ts: Math.floor((T0 + 9600) / 1000), source: "voice", event: "call.connected", detail: JSON.stringify({ alias: "biscuit" }) }, // Second-only legacy.
  twilio("provider.twilio.callback.ringing", T0 + 400, null, { status: "ringing" }), // Missing occurrence.
  twilio("provider.twilio.callback.completed", T0 + 60_000, T0 + 58_000, { status: "completed" }), // Whole-second.
  twilio("provider.twilio.callback.in-progress", T0 + 1000, T0 + 1600, { status: "in-progress" }), // Negative lag.
  twilio("provider.twilio.webhook.exchange", T0 + 20 * 60_000, T0 + 500, { method: "POST", request_path: "/voice",
    response_code: 200, request_duration_ms: 87 }), // Late backfill, earlier event time, later row id.
];
const rowsB = [
  record("acp.turn.stopped", turnStart + 120_000, { alias: "biscuit", acp_session_id: "ses_1", started_ms: turnStart,
    ended_ms: turnStart + 120_000, ms: 120_000, stop_reason: "end_turn", thoughts: { chunks: 3, chars: 90, tail: "secret thought" } }),
];

const a = await projectCall({ call_sid: CALL_A, started_at: Math.floor(T0 / 1000), ended_at: Math.floor(T0 / 1000) + 60, status: "completed" }, rowsA);
const b = await projectCall({ call_sid: CALL_B, started_at: Math.floor(T0 / 1000) + 100, ended_at: null, status: "in-progress" }, rowsB);

const attr = (x: { attributes: { key: string; value: any }[] }, key: string) => {
  const v = x.attributes.find((kv) => kv.key === key)?.value;
  return v === undefined ? undefined : v.stringValue ?? v.boolValue ?? v.intValue ?? v.doubleValue ?? v.arrayValue;
};
const logFor = (p: typeof a, rowId: number) => p.logs.find((l) => l.rowId === rowId)!;
const spanNamed = (p: typeof a, name: string) => p.spans.filter((s) => s.name === name);

// Nanoseconds are exact BigInt strings.
assert.equal(msToNanos(T0 + 1), "1790000000001000000");
assert.equal(msToNanos(0), null);
assert.equal(msToNanos(1.5), null);

// Every row is a log; insertion order is preserved for paging.
assert.equal(a.logs.length, rowsA.length);
assert.deepEqual(a.logs.map((l) => l.rowId), rowsA.map((r) => r.id));

// Source versus observed time.
const missing = logFor(a, rowsA[20].id);
assert.equal(missing.timeUnixNano, undefined, "unknown provider occurrence time is omitted");
assert.equal(missing.observedTimeUnixNano, msToNanos(T0 + 400));
assert.equal(attr(missing, "dogwalk.timestamp.source"), "unknown");
const whole = logFor(a, rowsA[21].id);
assert.equal(whole.timeUnixNano, msToNanos(T0 + 58_000));
assert.equal(attr(whole, "dogwalk.timestamp.precision_ms"), "1000");
const negative = logFor(a, rowsA[22].id);
assert.ok(BigInt(negative.timeUnixNano!) > BigInt(negative.observedTimeUnixNano), "negative lag kept, not calibrated");
const late = logFor(a, rowsA[23].id);
assert.ok(BigInt(late.timeUnixNano!) < BigInt(logFor(a, rowsA[1].id).observedTimeUnixNano), "backfill keeps earlier event time");
assert.equal(late.rowId, rowsA[23].id, "backfill keeps its later insertion cursor");
const legacy = logFor(a, rowsA[19].id);
assert.equal(attr(legacy, "dogwalk.timestamp.precision_ms"), "1000");
assert.equal(attr(legacy, "dogwalk.timestamp.source"), "worker.journal_seconds");

// Spans: model OK, model failed, tool status unset, open tool not a span.
const call = spanNamed(a, "voice_call")[0];
assert.ok(call && spanNamed(b, "voice_call").length === 0, "live call gets no completed call span");
const models = spanNamed(a, "orchestrator.model_request");
assert.deepEqual(models.map((s) => s.status.code), [1, 2]);
assert.equal(models[0].events?.[0].name, "model.headers");
assert.equal(attr(models[0].events![0], "http.response.status_code"), "200");
assert.ok(models.every((s) => s.parentSpanId === call.spanId && s.traceId === call.traceId));
const tools = spanNamed(a, "orchestrator.tool");
assert.equal(tools.length, 1, "tool without completion is not a span");
assert.equal(tools[0].status.code, 0, "finally-completion does not imply success");
assert.equal(tools[0].links?.[0].spanId, models[0].spanId, "tool links to the model response that requested it");
const openTool = logFor(a, rowsA[7].id);
assert.match(openTool.spanId!, /^[0-9a-f]{16}$/, "an open start names its span at once");
assert.ok(!a.spans.some((s) => s.spanId === openTool.spanId), "but that span is never exported");
assert.ok(a.logs.every((l) => attr(l, "dogwalk.interval.state") === undefined), "no trace-level state on logs");

// RPC roundtrips are scoped by Agent Connection.
const rpcs = spanNamed(a, "acp session/prompt");
assert.equal(rpcs.length, 2);
const byConn = Object.fromEntries(rpcs.map((s) => [attr(s, "dogwalk.agent_connection.id"), s]));
assert.equal(byConn["conn-a"].endTimeUnixNano, msToNanos(T0 + 4600));
assert.equal(byConn["conn-a"].status.code, 2);
assert.equal(byConn["conn-b"].endTimeUnixNano, msToNanos(T0 + 4300));
assert.equal(spanNamed(a, "acp session/cancel").length, 0, "legacy rows without connection identity stay logs");

// Prompt Turn begun in Call A, ended in Call B: same deterministic identity, never fabricated.
assert.equal(spanNamed(a, "acp.prompt_turn").length, 0);
const turnStartLog = logFor(a, rowsA[17].id), turnStopLog = logFor(b, rowsB[0].id);
assert.equal(turnStartLog.traceId, turnStopLog.traceId, "both halves of a cross-call turn name the same trace");
assert.equal(turnStartLog.spanId, turnStopLog.spanId);
const crossCall = await projectCall({ call_sid: CALL_A, started_at: Math.floor(T0 / 1000), ended_at: Math.floor(T0 / 1000) + 60,
  status: "completed" }, [...rowsA, ...rowsB]);
const turn = spanNamed(crossCall, "acp.prompt_turn")[0];
assert.ok(turn && turn.traceId !== call.traceId, "Prompt Turn is its own work trace");
assert.equal(turn.links?.[0].spanId, call.spanId, "linked to the initiating call, not parented by it");
assert.equal(turn.status.code, 1);
assert.equal(turn.startTimeUnixNano, msToNanos(turnStart));
assert.equal(turn.spanId, turnStartLog.spanId, "the span, once exported, is the one the logs named");

// Row-local: projecting one row alone gives exactly its record in the full projection,
// so a log fetched by cursor before later rows exist never changes.
const callA: CallRow = { call_sid: CALL_A, started_at: Math.floor(T0 / 1000), ended_at: Math.floor(T0 / 1000) + 60, status: "completed" };
for (const row of rowsA) assert.deepEqual((await projectLogs(callA, [row]))[0], logFor(a, row.id), `row ${row.id} is row-local`);
// Matched start and end logs name the exported span.
for (const s of a.spans.filter((s) => s.name !== "voice_call")) {
  const ends = [attr(s, "dogwalk.journal.start_row_id"), attr(s, "dogwalk.journal.end_row_id")].map(Number);
  for (const id of ends) assert.equal(logFor(a, id).spanId, s.spanId, `${s.name} row ${id} names its span`);
}
// An RPC response observed under a different voice session still names its request's span.
const crossLive = [
  { id: 1001, ts: Math.floor(T0 / 1000), source: "voice", event: "voice.timing", detail: JSON.stringify({ event: "acp.sent",
    rpc_id: 42, method: "session/prompt", agent_connection_id: "conn-z", ts_ms: T0, clock_id: CLOCK, live_session_id: "rtc_one" }) },
  { id: 1002, ts: Math.floor(T0 / 1000), source: "voice", event: "voice.timing", detail: JSON.stringify({ event: "acp.received",
    rpc_id: 42, agent_connection_id: "conn-z", ts_ms: T0 + 50, clock_id: CLOCK, live_session_id: "rtc_two" }) },
];
const rpcSpan = (await projectSpans(callA, crossLive)).find((s) => s.name === "acp session/prompt")!;
const rpcLogs = await projectLogs(callA, crossLive);
assert.ok(rpcSpan && rpcLogs.every((l) => l.spanId === rpcSpan.spanId && l.traceId === rpcSpan.traceId));
// Producer identity: Resources come only from what each row recorded.
const produced = (id: number, event: string, ms: number, extra: Record<string, unknown>) => ({ id, ts: Math.floor(ms / 1000),
  source: "voice", event: "voice.timing", detail: JSON.stringify({ event, ts_ms: ms, clock_id: CLOCK, live_session_id: LIVE, ...extra }) });
const v1 = { service_version: "ver-1111", deployment_environment: "production" };
const v2 = { service_version: "ver-2222", deployment_environment: "production" };
const producedRows = [
  produced(2001, "tool.started", T0, { tool_call_id: "t-ok", name: "list_sessions", ...v1 }),
  produced(2002, "tool.completed", T0 + 5, { tool_call_id: "t-ok", outcome: "ok", ...v2 }), // A deploy between start and end.
  produced(2003, "tool.started", T0 + 10, { tool_call_id: "t-err", ...v2 }),
  produced(2004, "tool.completed", T0 + 15, { tool_call_id: "t-err", outcome: "error_result", ...v2 }),
  produced(2005, "tool.started", T0 + 20, { tool_call_id: "t-throw", ...v2 }),
  produced(2006, "tool.failed", T0 + 25, { tool_call_id: "t-throw", outcome: "threw", ...v2 }),
  produced(2007, "tool.started", T0 + 30, { tool_call_id: "t-plain", ...v2 }),
  produced(2008, "tool.completed", T0 + 35, { tool_call_id: "t-plain", outcome: "returned", ...v2 }),
  produced(2009, "live.sent", T0 + 40, { type: "session.commentary.append" }), // Legacy: no producer.
];
const resAttrs = (r: { attributes: { key: string; value: any }[] }) => Object.fromEntries(r.attributes.map((a) => [a.key, a.value.stringValue]));
const producedLogs = logsRequest(await projectLogs(callA, producedRows)).resourceLogs;
assert.deepEqual(producedLogs.map((r) => resAttrs(r.resource)["service.version"]), ["ver-1111", "ver-2222", undefined]);
assert.deepEqual(producedLogs.map((r) => r.scopeLogs[0].logRecords.length), [1, 7, 1]);
assert.equal(resAttrs(producedLogs[1].resource)["deployment.environment.name"], "production");
assert.ok(!("deployment.environment.name" in resAttrs(producedLogs[2].resource)), "legacy rows are not stamped with today's build");
assert.ok(!JSON.stringify(producedLogs).includes('"service_version"'), "producer fields are Resource, not log attributes");
const producedSpans = tracesRequest(await projectSpans(callA, producedRows)).resourceSpans;
const spanStatus = Object.fromEntries(producedSpans.flatMap((r) => r.scopeSpans[0].spans)
  .filter((s) => s.name === "orchestrator.tool").map((s) => [attr(s, "dogwalk.tool_call.id"), [s.status.code, attr(s, "dogwalk.outcome")]]));
assert.deepEqual(spanStatus, { "t-ok": [1, "ok"], "t-err": [2, "error_result"], "t-throw": [2, "threw"], "t-plain": [0, "returned"] });
const okSpanResource = producedSpans.find((r) => r.scopeSpans[0].spans.some((s) => attr(s, "dogwalk.tool_call.id") === "t-ok"))!;
assert.equal(resAttrs(okSpanResource.resource)["service.version"], "ver-1111", "a span belongs to the build that observed its start");
assert.equal(attr(tools[0], "dogwalk.outcome"), "not_recorded", "legacy finally-completion stays Unset");
assert.deepEqual(logsRequest([]).resourceLogs, [], "an empty page is a valid, empty export");

// W3C Trace Context helpers: strict version-00 parsing, valid fresh IDs.
const root = newRootContext(), child = childOf(root);
assert.deepEqual(parseTraceparent(formatTraceparent(root)), root);
assert.equal(child.traceId, root.traceId); assert.notEqual(child.spanId, root.spanId);
for (const bad of [null, "", `00-${"0".repeat(32)}-${root.spanId}-01`, `00-${root.traceId}-${"0".repeat(16)}-01`,
  `ff-${root.traceId}-${root.spanId}-01`, `00-${root.traceId.toUpperCase()}-${root.spanId}-01`, `00-${root.traceId}-${root.spanId}-01-extra`]) {
  assert.equal(parseTraceparent(bad), null, `accepted ${bad}`);
}
assert.deepEqual(spanFields(child, root), { trace_id: root.traceId, span_id: child.spanId, parent_span_id: root.spanId });
assert.ok(!("parent_span_id" in spanFields(newRootContext(), root)), "a parent from another trace is a link, not a parent");

// Context recorded at operation start (B1) wins over derivation, and matches by span ID.
const callCtx = newRootContext(), modelCtx = childOf(callCtx), turnCtx = newRootContext(), promptRpc = childOf(turnCtx);
const recordedCall: CallRow = { ...callA, trace_id: callCtx.traceId, span_id: callCtx.spanId };
const live = (id: number, event: string, ms: number, extra: Record<string, unknown>) => ({ id, ts: Math.floor(ms / 1000),
  source: "voice", event: "voice.timing", detail: JSON.stringify({ event, ts_ms: ms, clock_id: CLOCK, ...extra }) });
const recordedRows = [
  live(3001, "model.started", T0, { request_id: "r-live", live_session_id: "rtc_one", ...spanFields(modelCtx, callCtx) }),
  // The voice session changed mid-request: derived matching would miss this; span IDs do not.
  live(3002, "model.completed", T0 + 900, { request_id: "r-live", live_session_id: "rtc_two", ...spanFields(modelCtx, callCtx) }),
  { id: 3003, ts: Math.floor(T0 / 1000) + 1, source: "acp", event: "acp.turn.started", detail: JSON.stringify({ acp_session_id: "ses_9",
    started_ms: T0 + 1000, ts_ms: T0 + 1000, ...spanFields(turnCtx), link_trace_id: callCtx.traceId, link_span_id: callCtx.spanId }) },
  live(3004, "acp.sent", T0 + 1001, { rpc_id: 5, method: "session/prompt", agent_connection_id: "conn-r", ...spanFields(promptRpc, turnCtx) }),
  live(3005, "acp.received", T0 + 5000, { rpc_id: 5, agent_connection_id: "conn-r", ...spanFields(promptRpc, turnCtx) }),
  { id: 3006, ts: Math.floor(T0 / 1000) + 5, source: "acp", event: "acp.turn.stopped", detail: JSON.stringify({ acp_session_id: "ses_9",
    started_ms: T0 + 1000, ended_ms: T0 + 5001, stop_reason: "end_turn", ts_ms: T0 + 5001, ...spanFields(turnCtx) }) },
];
const recSpans = await projectSpans(recordedCall, recordedRows);
const byName = (name: string) => recSpans.find((s) => s.name === name)!;
assert.deepEqual([byName("voice_call").traceId, byName("voice_call").spanId], [callCtx.traceId, callCtx.spanId]);
assert.equal(attr(byName("voice_call"), "dogwalk.context"), "recorded_at_start");
const recModel = byName("orchestrator.model_request");
assert.deepEqual([recModel.traceId, recModel.spanId, recModel.parentSpanId], [callCtx.traceId, modelCtx.spanId, callCtx.spanId]);
const recTurn = byName("acp.prompt_turn");
assert.deepEqual([recTurn.traceId, recTurn.spanId, recTurn.parentSpanId], [turnCtx.traceId, turnCtx.spanId, undefined]);
assert.deepEqual([recTurn.links![0].traceId, recTurn.links![0].spanId], [callCtx.traceId, callCtx.spanId], "turn links its call");
const recRpc = byName("acp session/prompt");
assert.deepEqual([recRpc.traceId, recRpc.parentSpanId], [turnCtx.traceId, turnCtx.spanId], "session/prompt is inside its turn");
const recLogs = await projectLogs(recordedCall, recordedRows);
assert.ok(recLogs.every((l) => l.traceId && recSpans.some((s) => s.spanId === l.spanId)), "every log names its recorded span");
assert.ok(!JSON.stringify(recLogs).includes("parent_span_id"), "context fields are not duplicated as attributes");

// Older rows under a call with recorded context keep stable derived IDs, and link the call
// instead of naming a parent in another trace.
const legacyUnderRecorded = await projectSpans(recordedCall, rowsA.slice(1, 4));
const legacyModel = legacyUnderRecorded.find((s) => s.name === "orchestrator.model_request")!;
assert.equal(legacyModel.spanId, models[0].spanId, "derived identity does not depend on call context");
assert.notEqual(legacyModel.traceId, callCtx.traceId);
assert.equal(legacyModel.parentSpanId, undefined, "no parent from another trace");
assert.deepEqual([legacyModel.links![0].traceId, legacyModel.links![0].spanId], [callCtx.traceId, callCtx.spanId]);
assert.equal(attr(legacyModel, "dogwalk.context"), "derived");
assert.deepEqual(await projectLogs(recordedCall, [rowsA[1]]), await projectLogs(callA, [rowsA[1]]), "older logs never change identity");

// Cross-call completion: the start's call lists the open span; its end, journaled under a later
// call, completes it once supplied. An interruption detected after eviction names it but never ends it.
const crossTurn = newRootContext();
const turnA = { id: 4001, ts: Math.floor(T0 / 1000), source: "acp", event: "acp.turn.started", detail: JSON.stringify({
  acp_session_id: "ses_x", started_ms: T0, ts_ms: T0, ...spanFields(crossTurn) }) };
const turnB = { id: 4100, ts: Math.floor(T0 / 1000) + 300, source: "acp", event: "acp.turn.stopped", detail: JSON.stringify({
  acp_session_id: "ses_x", started_ms: T0, ended_ms: T0 + 300_000, stop_reason: "end_turn", ts_ms: T0 + 300_000, ...spanFields(crossTurn) }) };
const interrupted = { id: 4200, ts: Math.floor(T0 / 1000) + 400, source: "acp", event: "acp.turn.interrupted", detail: JSON.stringify({
  acp_session_id: "ses_x", started_ms: T0, detected_ms: T0 + 400_000, ts_ms: T0 + 400_000, ...spanFields(crossTurn) }) };
assert.deepEqual(openRecordedSpanIds([turnA]), [crossTurn.spanId]);
assert.deepEqual(openRecordedSpanIds([turnA, turnB]), []);
assert.equal((await projectSpans(callA, [turnA])).filter((s) => s.name === "acp.prompt_turn").length, 0);
const completed = (await projectSpans(callA, [turnA, turnB])).find((s) => s.name === "acp.prompt_turn")!;
assert.equal(completed.spanId, crossTurn.spanId);
assert.equal((await projectSpans(callA, [turnA, interrupted])).filter((s) => s.name === "acp.prompt_turn").length, 0,
  "detecting an interruption is not observing the turn's end");
assert.equal((await projectLogs(callA, [interrupted]))[0].spanId, crossTurn.spanId);

// RPC rows without Agent Connection identity, and Agent-initiated requests, name no span.
assert.equal(logFor(a, rowsA[14].id).spanId, undefined);
const agentRequest = { id: 999, ts: Math.floor(T0 / 1000), source: "voice", event: "voice.timing",
  detail: JSON.stringify({ event: "acp.received", rpc_id: 7, method: "session/request_permission", agent_connection_id: "conn-a", ts_ms: T0 }) };
assert.equal((await projectLogs(callA, [agentRequest]))[0].spanId, undefined);

// Deterministic IDs and valid lengths.
const again = await projectCall({ call_sid: CALL_A, started_at: Math.floor(T0 / 1000), ended_at: Math.floor(T0 / 1000) + 60, status: "completed" }, rowsA);
assert.deepEqual(again.spans.map((s) => s.spanId), a.spans.map((s) => s.spanId));
for (const s of a.spans) {
  assert.match(s.traceId, /^[0-9a-f]{32}$/); assert.match(s.spanId, /^[0-9a-f]{16}$/);
  assert.notEqual(s.traceId, "0".repeat(32)); assert.equal(attr(s, "dogwalk.reconstructed"), true);
}

// Metadata only: no content, alias, error text, thought tail, or signed URL anywhere.
const exported = JSON.stringify([logsRequest(a.logs), tracesRequest(a.spans), logsRequest(b.logs), tracesRequest(b.spans)]);
for (const leak of ["please open my repo", "do the thing", "I changed three files", "biscuit", "secret thought", "X-Amz-Signature", "rowId"]) {
  assert.ok(!exported.includes(leak), `export leaked ${leak}`);
}
assert.ok(logFor(a, rowsA[0].id).droppedAttributesCount! >= 1, "dropped content is counted");
assert.equal(attr(logFor(a, rowsA[0].id), "dogwalk.content.kind"), "caller");

const out = process.argv[2];
if (out) {
  mkdirSync(out, { recursive: true });
  writeFileSync(join(out, "logs.json"), JSON.stringify(logsRequest([...crossCall.logs])));
  writeFileSync(join(out, "traces.json"), JSON.stringify(tracesRequest(crossCall.spans)));
  console.log(`wrote ${out}/logs.json and ${out}/traces.json`);
}
console.log("otlp projection: ok");
