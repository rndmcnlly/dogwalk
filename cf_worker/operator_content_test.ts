// Isolated synthetic test: node --experimental-strip-types cf_worker/operator_content_test.ts
import assert from "node:assert/strict";
import { operatorCallContent } from "./src/operator_content.ts";

const callSid = "CAcontent";
let mapping: { identity_hash: string | null } | null = { identity_hash: "identity" };
let history: any = {};
let observations: any[] = [];
let reads = 0;
const env: any = {
  DB: { prepare: (sql: string) => ({ bind: (sid: string) => {
    assert.equal(sid, callSid);
    return {
      first: async () => { assert.match(sql, /LEFT JOIN sandbox_assignments/); return mapping; },
      all: async () => ({ results: observations.map((detail, id) => ({ id, detail: JSON.stringify(detail) })) }),
    };
  } }) },
  VOICE_SESSIONS: { idFromName: (id: string) => id, get: () => ({ fetch: async (url: string) => {
    assert.equal(url, "https://voice-session/conversation"); reads++;
    return Response.json(history);
  } }) },
};

mapping = null;
assert.equal(await operatorCallContent(env, callSid), null);
mapping = { identity_hash: null };
assert.equal((await operatorCallContent(env, callSid))?.entries.length, 0);
assert.equal(reads, 0);
mapping = { identity_hash: "identity" };
history = {
  call_transcripts: [
    { call_sid: callSid, entries: [{ role: "caller", text: "first session", startMs: 1, endMs: 100 }] },
    { call_sid: callSid, entries: [{ role: "voice", text: "second session", startMs: 1, endMs: 100 }] },
    { call_sid: "other", entries: [{ role: "caller", text: "PRIVATE OTHER CALL" }] },
  ],
  current_transcript: { call_sid: "other", entries: [{ role: "caller", text: "PRIVATE CURRENT" }] },
  call_marks: [{ call_sid: callSid, context_index: 0 }, { call_sid: "other", context_index: 5 }],
  context_archives: [{ key: "not fetched" }],
  orchestrator_context: [
    { role: "user", content: "[handoff d1]\nCaller: task\n[harness event] turn_stopped: {\"alias\":\"Oak\",\"agent_message\":\"Report\"}" },
    { type: "function_call", name: "say", call_id: "f1", arguments: JSON.stringify({ text: "Hello", delegation_id: "d1" }) },
    { type: "function_call", name: "begin_prompt_turn", call_id: "f2", arguments: JSON.stringify({ alias: "Oak", prompt: "Do work" }) },
    { type: "function_call_output", output: "CAPABILITY SECRET" },
    { type: "reasoning", text: "PRIVATE REASONING" },
    { type: "function_call", name: "say", arguments: JSON.stringify({ text: "OTHER CALL SAY" }) },
  ],
};
observations = [
  { event: "tool.started", tool_call_id: "f1", ts_ms: 1234, live_session_id: "live1" },
  { event: "handoff.queued", delegation_id: "d1", ts_ms: 1200, live_session_id: "live1" },
];
let result = (await operatorCallContent(env, callSid))!;
assert.equal(result.entries.length, 6);
assert.equal(result.entries.find(e => e.kind === "say")?.observed_at_ms, 1234);
assert.equal(result.entries.find(e => e.kind === "handoff")?.observed_at_ms, 1200);
assert.equal(result.entries.find(e => e.kind === "acp_prompt")?.alias, "Oak");
assert.ok(result.entries.filter(e => e.source === "saved_transcript").every(e => e.observed_at_ms === null && !e.live_session_id));
assert.doesNotMatch(JSON.stringify(result), /PRIVATE|SECRET|OTHER CALL SAY/);
assert.match(result.note!, /compaction/);

history.current_transcript = { call_sid: callSid, live_session_id: "live2", entries: [{ role: "caller", text: "current", startMs: 1, endMs: 200 }] };
observations.push({ event: "live.received", type: "session.input_transcript.delta", live_session_id: "live2", end_ms: 200, ts_ms: 2000 });
result = (await operatorCallContent(env, callSid))!;
assert.equal(result.entries.find(e => e.id === "transcript:current:0")?.observed_at_ms, 2000);
observations.push({ ...observations.at(-1), ts_ms: 2001 });
result = (await operatorCallContent(env, callSid))!;
assert.equal(result.entries.find(e => e.id === "transcript:current:0")?.observed_at_ms, null);

history.call_transcripts = [{ call_sid: callSid, entries: Array.from({ length: 405 }, () => ({ role: "voice", text: "x".repeat(33_000) })) }];
result = (await operatorCallContent(env, callSid))!;
assert.equal(result.entries.length, 400);
assert.equal(result.entries[0].text.length, 32_000);
assert.ok(result.entries[0].truncated);
assert.match(result.note!, /truncated at 400/);

history.call_marks = [];
history.call_transcripts = [];
history.current_transcript = null;
assert.equal((await operatorCallContent(env, callSid))?.entries.length, 0);
console.log("operator content: isolated synthetic checks passed");
