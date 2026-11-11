import type { Env } from "./types";

export interface ContentEntry {
  id: string;
  kind: "caller" | "voice" | "handoff" | "say" | "brief" | "instructions" | "acp_prompt" | "agent_report";
  text: string;
  source: "saved_transcript" | "orchestrator_history";
  observed_at_ms: number | null;
  start_ms?: number;
  end_ms?: number;
  live_session_id?: string;
  tool_call_id?: string;
  delegation_id?: string;
  acp_session_id?: string;
  alias?: string;
  precision: "observed" | "provider_media_offset" | "unknown";
  truncated: boolean;
}

const MAX_TEXT = 32_000;
const MAX_ENTRIES = 400;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const object = (value: unknown): Record<string, any> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, any> : {};
const list = (value: unknown): any[] => Array.isArray(value) ? value : [];
function parsed(value: unknown): Record<string, any> {
  try { return object(typeof value === "string" ? JSON.parse(value) : value); } catch { return {}; }
}

// This is an operator-only projection. Its caller must check admin auth and Call SID.
// Never share this response with a Paired Browser or turn it into a provider wire dump.
// Reading the Durable Object does not ensure/wake a Sandbox or establish ACP.
export async function operatorCallContent(env: Env, callSid: string): Promise<{ entries: ContentEntry[]; note?: string } | null> {
  const call = await env.DB.prepare(
    `SELECT u.identity_hash FROM voice_calls c
     LEFT JOIN users u ON u.id = c.user_id WHERE c.call_sid = ?`,
  ).bind(callSid).first<{ identity_hash: string | null }>();
  if (!call) return null;
  if (!call.identity_hash) return { entries: [], note: "No User maps this call to conversation history." };
  let history: Record<string, any>;
  try {
    const session = env.VOICE_SESSIONS.get(env.VOICE_SESSIONS.idFromName(call.identity_hash));
    const response = await session.fetch("https://voice-session/conversation");
    if (!response.ok) return { entries: [], note: "Retained conversation history is unavailable." };
    history = object(await response.json());
  } catch {
    // Do not log conversation bodies or capability-bearing errors.
    return { entries: [], note: "Retained conversation history is unavailable." };
  }
  const notes = new Set<string>([
    "Bounded retained evidence only, not a complete conversation archive. Media offsets are not Unix time or telephone playout.",
    "Historical function calls show requested speech/briefs/prompts, not confirmed sends. Agent reports are retained turn_stopped tails.",
  ]);
  const entries: ContentEntry[] = [];
  const add = (entry: ContentEntry) => {
    if (entries.length >= MAX_ENTRIES) { notes.add("Content entries truncated at 400."); return; }
    if (entry.text.length > MAX_TEXT) { entry.text = entry.text.slice(0, MAX_TEXT); entry.truncated = true; }
    if (entry.truncated) notes.add("Some content text is truncated.");
    entries.push(entry);
  };
  // Match only exact identifiers in this call, not alias, adjacency, or attach time.
  const rows = await env.DB.prepare(
    "SELECT id, detail FROM call_activity WHERE call_sid = ? AND event = 'voice.timing' ORDER BY id LIMIT 6001",
  ).bind(callSid).all<{ id: number; detail: string }>();
  if (rows.results.length > 6000) notes.add("Timing correlation limited to the first 6000 timing rows.");
  const timing = rows.results.slice(0, 6000).map(row => parsed(row.detail));
  const observe = (entry: ContentEntry, matches: Record<string, any>[]) => {
    if (matches.length !== 1 || !finite(matches[0].ts_ms)) return;
    entry.observed_at_ms = matches[0].ts_ms;
    entry.precision = "observed";
    if (typeof matches[0].live_session_id === "string") entry.live_session_id = matches[0].live_session_id;
  };
  const transcripts = list(history.call_transcripts).map((value, index) => ({ value: object(value), id: `saved:${index}` }))
    .filter(({ value }) => value.call_sid === callSid);
  const current = object(history.current_transcript);
  if (current.call_sid === callSid) transcripts.push({ value: current, id: "current" });
  for (const { value, id } of transcripts) {
    list(value.entries).forEach((raw, ordinal) => {
      const item = object(raw);
      if ((item.role !== "caller" && item.role !== "voice") || typeof item.text !== "string") return;
      const entry: ContentEntry = {
        id: `transcript:${id}:${ordinal}`, kind: item.role, text: item.text,
        source: "saved_transcript", observed_at_ms: null,
        precision: finite(item.startMs) || finite(item.endMs) ? "provider_media_offset" : "unknown", truncated: false,
        ...(finite(item.startMs) ? { start_ms: item.startMs } : {}),
        ...(finite(item.endMs) ? { end_ms: item.endMs } : {}),
        ...(typeof value.live_session_id === "string" ? { live_session_id: value.live_session_id } : {}),
      };
      // Saved transcripts historically omitted live IDs. Never guess which resumed
      // voice session an offset belongs to. Ordinals survive in the stable entry ID.
      if (entry.live_session_id && finite(entry.end_ms)) {
        observe(entry, timing.filter(d => d.event === "live.received" && d.live_session_id === entry.live_session_id &&
          d.type === `session.${item.role === "caller" ? "input" : "output"}_transcript.delta` && d.end_ms === entry.end_ms));
        if (entry.precision === "observed") notes.add("Transcript observation times mark receipt of the matching final delta, not utterance start or audible delivery.");
      }
      add(entry);
    });
  }
  if (!transcripts.length) notes.add("No saved/current transcript retained for this call.");
  if (transcripts.some(({ value }) => typeof value.live_session_id !== "string"))
    notes.add("Saved transcripts without live-session IDs remain media-offset-only evidence; voice-session identity was not guessed.");

  const context = list(history.orchestrator_context);
  const marks = list(history.call_marks).map(object);
  const first = marks.findIndex(mark => mark.call_sid === callSid);
  if (list(history.context_archives).length) notes.add("Context compaction archives exist; removed history is not recovered by this endpoint.");
  if (first < 0) {
    notes.add("No retained call mark safely scopes Orchestrator history to this call.");
  } else {
    let next = first + 1;
    while (next < marks.length && marks[next].call_sid === callSid) next++;
    const start = marks[first].context_index;
    const end = next < marks.length ? marks[next].context_index : context.length;
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start || end > context.length) {
      notes.add("Invalid retained call marks; Orchestrator history omitted rather than guessed.");
    } else {
      if (start === 0) notes.add("A zero call mark may reflect compaction; this slice may contain only a retained tail.");
      for (let index = start; index < end; index++) {
        const item = object(context[index]);
        if (item.type === "function_call" && ["say", "brief", "begin_prompt_turn"].includes(item.name)) {
          const args = parsed(item.arguments);
          const text = item.name === "begin_prompt_turn" ? args.prompt : args.text;
          if (typeof text !== "string") continue;
          const entry: ContentEntry = { id: `context:${index}`, kind: item.name === "begin_prompt_turn" ? "acp_prompt" : item.name,
            text, source: "orchestrator_history", observed_at_ms: null, precision: "unknown", truncated: false,
            ...(typeof item.call_id === "string" ? { tool_call_id: item.call_id } : {}),
            ...(typeof args.delegation_id === "string" ? { delegation_id: args.delegation_id } : {}),
            ...(typeof args.alias === "string" ? { alias: args.alias } : {}) };
          if (entry.tool_call_id) observe(entry, timing.filter(d => d.event === "tool.started" && d.tool_call_id === entry.tool_call_id));
          add(entry);
        }
        // User messages contain batched handoffs/harness events. Ignore all other
        // messages, reasoning items, function results, and arbitrary tool arguments.
        if (item.role !== "user") continue;
        const text = typeof item.content === "string" ? item.content : list(item.content)
          .filter(block => block?.type === "input_text" && typeof block.text === "string").map(block => block.text).join("\n");
        const blocks = text.split(/(?=^\[(?:handoff |harness event\]))/m);
        blocks.forEach((block, ordinal) => {
          const handoff = /^\[handoff ([^\]\n]+)\]\n([\s\S]*)/.exec(block);
          if (handoff) {
            const entry: ContentEntry = { id: `context:${index}:${ordinal}`, kind: "handoff", text: handoff[2].trimEnd(),
              source: "orchestrator_history", observed_at_ms: null, precision: "unknown", truncated: false, delegation_id: handoff[1] };
            observe(entry, timing.filter(d => d.event === "handoff.queued" && d.delegation_id === entry.delegation_id));
            add(entry);
          }
          const stopped = /^\[harness event\] turn_stopped: (.*)$/m.exec(block);
          if (stopped) {
            const report = parsed(stopped[1]);
            if (typeof report.agent_message !== "string" || !report.agent_message) return;
            add({ id: `context:${index}:${ordinal}:report`, kind: "agent_report", text: report.agent_message,
              source: "orchestrator_history", observed_at_ms: null, precision: "unknown", truncated: true,
              ...(typeof report.alias === "string" ? { alias: report.alias } : {}) });
          }
        });
      }
    }
  }
  if (!entries.length) notes.add("No content evidence retained within this call's safe scope.");
  return { entries, note: [...notes].join(" ") };
}
