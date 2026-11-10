// Operator Diagnostics Surface only. This page has no Paired Browser capability.
export const OPERATOR_TIMING_HTML = String.raw`<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Dogwalk operator timing</title>
<style>
:root{color-scheme:dark;--ink:#f4f0e6;--mut:#9c9a91;--fnt:#71756c;--ln:#343731;--pnl:#181b17;--bg:#0c0e0c;--grn:#b9f36b;--amb:#f3b85c;--red:#ff796d;--blu:#79c7ff;--vio:#d5a6ff;--pnk:#ff8bc6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}main{padding:16px 18px 36px;max-width:1920px;margin:auto}a{color:var(--blu)}h1{font:700 17px ui-sans-serif,system-ui;margin:0}h2{font-size:12px;margin:0 0 8px}.top,.controls,.summary,.paging{display:flex;gap:14px;align-items:center;flex-wrap:wrap}.top{justify-content:space-between;border-bottom:1px solid var(--ln);padding-bottom:12px}.mut,small{color:var(--mut)}.badge{font-size:10px;text-transform:uppercase;border:1px solid var(--fnt);padding:2px 6px}.controls{padding:12px 0}label{display:inline-flex;align-items:center;gap:5px}input{accent-color:var(--amb)}button,select{font:inherit;color:var(--ink);background:var(--pnl);border:1px solid var(--ln);border-radius:3px;padding:5px 8px}button{cursor:pointer}button:hover{border-color:var(--grn)}button:disabled{opacity:.4;cursor:default}select{max-width:100%;width:430px}button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--blu)}.summary{background:var(--pnl);padding:8px 12px;margin-bottom:10px}.summary strong{color:var(--grn)}#status[data-error="true"]{color:var(--red)}.workspace{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:12px}.chartbox{border:1px solid var(--ln);min-width:0}#chartScroll{height:64vh;min-height:300px;max-height:800px;overflow:auto;position:relative;overflow-anchor:none}.lanehead{position:sticky;top:0;z-index:2;background:var(--pnl);display:flex;height:58px;border-bottom:1px solid var(--ln)}.axishead{width:132px;flex-shrink:0;padding:8px;color:var(--mut)}.lanehead .lane{width:210px;flex-shrink:0;padding:7px 10px;border-left:1px solid var(--ln);overflow:hidden;overflow-wrap:anywhere;font-size:11px}.lane small{display:block;font-size:10px}#plot{display:block}svg text{font:10px ui-monospace,SFMono-Regular,Menlo,monospace;fill:var(--ink)}svg .axis{fill:var(--mut)}svg .event{cursor:pointer}svg .event:hover .hit{fill:#b9f36b18}svg .event:focus{outline:none}svg .event:focus .hit{stroke:var(--grn)}.paging{justify-content:space-between;padding:7px;border-top:1px solid var(--ln)}aside{background:var(--pnl);padding:12px;min-width:0}pre{font:11px/1.5 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:46vh;overflow:auto;margin:8px 0}.legend{margin-top:12px;color:var(--mut);font-size:11px}.legend p{margin:5px 0}.legend b{color:var(--ink)}#nested{color:var(--amb)}.note{color:var(--mut);margin:8px 0}.inspect-actions{display:flex;gap:8px;flex-wrap:wrap}#empty{padding:24px;color:var(--mut)}@media(max-width:1000px){.workspace{grid-template-columns:1fr}aside pre{max-height:260px}main{padding:12px}#chartScroll{height:58vh}}
.workspace{grid-template-columns:minmax(0,1fr)}.workspace:has(details[open]){grid-template-columns:minmax(0,1fr) 300px}.workspace>details:not([open]){grid-row:2}summary{cursor:pointer;color:var(--blu);padding:6px 0}.lanehead .lane{width:200px}.lanehead .lane.caller{width:76px;padding:7px 5px}.axishead{width:122px}svg .metric{fill:var(--grn);font-weight:700}@media(max-width:1000px){.workspace:has(details[open]){grid-template-columns:1fr}}
.controls>label{max-width:100%;min-width:0}.controls select{min-width:0;flex:1}.summary{overflow-wrap:anywhere}
.lanehead{display:block;height:84px}.domainrow,.trackrow{display:flex}.domainrow{height:26px}.domainrow .axishead{padding:4px 8px;font-size:10px}.domain{padding:4px 10px;border-left:1px solid var(--ln);font-size:10px;text-transform:uppercase;letter-spacing:.08em;flex-shrink:0}.trackrow .lane{height:58px}
.contentpane{margin-top:12px;border:1px solid var(--ln);padding:12px;min-width:0}.contentlist{max-height:350px;overflow:auto;overflow-wrap:anywhere}.contentitem{display:block;text-align:left;width:100%;margin:6px 0;white-space:normal}.snippet{display:block;white-space:pre-wrap;overflow-wrap:anywhere;margin-top:5px;color:var(--ink)}
</style></head><body><main>
<header class="top"><h1>dogwalk <span class="mut">/ operator timing</span></h1><div><span class="badge">read-only · operator</span> <a href="/admin">Diagnostics</a></div></header>
<div class="controls"><label>Voice Call <select id="calls" aria-label="Voice Call"><option value="">Loading calls…</option></select></label><label><input id="follow" type="checkbox" checked>follow latest live</label><label><input id="pause" type="checkbox">pause fetching</label><label><input id="auto" type="checkbox" checked>autoscroll</label><label><input id="compress" type="checkbox" checked>compress gaps &gt; 2s</label><label><input id="raw" type="checkbox">raw events + metadata</label><label><input id="content" type="checkbox">Show content</label><button id="retry">Reconnect</button></div>
<div class="summary" role="status" aria-live="polite"><strong id="status">Connecting</strong><span id="lag">No sample yet</span><span id="counts">0 events</span><span id="callMeta"></span></div>
<div class="workspace"><section class="chartbox" aria-label="Observed sequence diagram"><div id="chartScroll"><div id="heads" class="lanehead"></div><svg id="plot" role="img" aria-label="Time flows down; observed event sequence"></svg><div id="empty">No call selected. No synthetic data is shown.</div></div><div class="paging"><button id="older">Older rows</button><span id="window">0 rows</span><button id="newer">Newer rows</button></div></section>
<details id="inspectorPanel"><summary>Event inspector (expand / collapse)</summary><aside><h2>Event inspector</h2><div id="selection" class="mut">Select a mark, span, or message. Keyboard: Tab, then Enter.</div><pre id="inspector">Verbose details are hidden until raw metadata is enabled.</pre><div class="inspect-actions"><button id="copy" disabled>Copy selected JSON</button><button id="download" disabled>Download loaded JSON</button></div><p class="note">Memory only. No credentials or events are saved in browser storage. Exports stay local and may contain sensitive data.</p><div id="related" class="note"></div></aside></details></div>
<section id="contentPane" class="contentpane" aria-label="Conversation and boundary content" hidden><h2>Conversation and boundary content</h2><p class="note">Text only. Observed receipt is not telephone playout. Saved media offsets are not event positions. Select a snippet for full text in the inspector.</p><button id="refreshContent">Refresh content</button> <span id="contentStatus" role="status"></span><h2>Observed boundary messages</h2><div id="observedContent" class="contentlist"></div><details><summary>Saved conversation (may overlap observed messages)</summary><div id="savedContent" class="contentlist"></div></details></section>
<section class="legend">
<p><b>Evidence:</b> ● observed event; → observed control-message direction; vertical colored bar = matched start/end identifiers; dashed vertical tail = start observed, end not observed; dotted connector = same delegation identifier, not a causal claim. Unmatched endpoints remain individual events.</p>
<p>Time flows down. Axis: milliseconds since Worker call start (fixed anchor with second precision), with Δ between visible observations. Negative positions precede that anchor. Minimum row spacing separates simultaneous events; this is not a proportional waveform. Long gaps are explicitly labeled when compressed. Old second-only records are marked “±1s precision”; cross-clock differences are wall-clock observations, not latency measurements. Twilio uses occurrence time when known, otherwise receipt time. Numeric millisecond units do not imply millisecond accuracy. Received-minus-occurred is reporting lag plus clock skew, not network latency.</p>
<p>Background bands group tracks by where they run in the current deployment, not by who observed the event. OpenAI reasoning API spans are measured at the Worker and include network/application overhead, not just model compute. The Orchestrator itself runs on Cloudflare; sessions and Agent tools run in the Daytona Sandbox. Internal model calls inside ACP Agents are not exposed by this trace.</p>
<p>Transcript receipt and provider media offsets are not phone playout timestamps. Media start_ms/end_ms/offset_ms remain metadata, never Unix time. Orchestrator tools are not ACP Agent tools. ACP roundtrips require an exact scoped RPC identifier match. Provider root/parent leg metadata and same-leg grouping are relationships, not exact send/receive matches or causal arrows.</p>
<p>Observed text bursts fuse same-stream deltas using a cadence EMA (750–2000ms inactivity threshold), not fixed time buckets or audio VAD. A burst mark stays at its first receipt; the inspector retains every delta ID, receipt time, media offset, split reason, and threshold. Caller, voice, and ACP Session streams are separate. Provider media gaps are used only with explicit matching media/provider clock and session identifiers; offsets are never added to Unix time. Idle means inactivity at the latest server sample, not detected silence or a guaranteed speech-turn end.</p>
<p id="nested">nested ACP sessions: not observed</p></section>
</main><script>
(() => {
'use strict';
const $ = id => document.getElementById(id);
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = String(text); if (cls) n.className = cls; return n; };
const NS = 'http://www.w3.org/2000/svg';
const svg = (tag, attrs, text) => { const n = document.createElementNS(NS, tag); for (const [k,v] of Object.entries(attrs || {})) n.setAttribute(k, String(v)); if (text !== undefined) n.textContent = String(text); return n; };
const LIMIT = 800; // Bound rendered rows, not the insertion cursor or retained evidence.
let calls = [], call = null, sid = '', cursor = 0, events = new Map(), selected = null;
let generation = 0, timer = null, controller = null, running = false, rerun = false;
let authBlocked = false, failures = 0, nextCalls = 0, nextEvents = 0, sampled = 0, serverAtSample = 0;
let pageEnd = Infinity, catchingUp = false, message = 'Connecting', isError = false;
let savedContent = [], contentNote = '', contentTimer = null, contentController = null, contentRunning = false, contentGeneration = 0;
const contentKeys = /^(?:content_?text|text|content|prompt|agent_?message|report|last_?words|instructions|brief|message|transcript|transcript_?text|input|output|arguments|result|label|title|summary|description|beat|detail_?text)$/i;
function displayData(value) {
  if (Array.isArray(value)) return value.map(displayData);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).flatMap(([k,v])=>{
    // Legacy diagnostics retained a short thought tail. This content mode is for
    // messages at interaction boundaries, not private reasoning text.
    if (k==='thoughts') return [[k,Object.fromEntries(Object.entries(v||{}).filter(([name,count])=>['chunks','chars'].includes(name) && typeof count==='number'))]];
    if (/^(?:reasoning|encrypted_content|agent_thought_chunk)$/i.test(k)) return [];
    if (!$('content').checked && (contentKeys.test(k) || (k==='detail' && typeof v==='string'))) return [];
    return [[k,displayData(v)]];
  }));
  return value;
}
function entryClock(e) {
  if (e.precision === 'provider_media_offset') return 'provider media offset ' + (finite(e.start_ms)?e.start_ms:'?') + '–' + (finite(e.end_ms)?e.end_ms:'?') + 'ms · no Unix event position';
  if (e.precision === 'observed' && finite(e.observed_at_ms)) return 'observed ' + new Date(e.observed_at_ms).toISOString() + ' (not playout)';
  return 'historical timing unknown · no event position';
}
function inspectContent(e) {
  if (!$('content').checked) return;
  selected = {contentEntry:e}; $('inspectorPanel').open=true;
  $('selection').textContent = e.kind + ' · ' + entryClock(e);
  $('inspector').textContent = JSON.stringify(e,null,2); $('related').textContent='';
  $('copy').disabled=false;
}
function drawContent() {
  const enabled=$('content').checked; $('contentPane').hidden=!enabled;
  $('observedContent').replaceChildren(); $('savedContent').replaceChildren();
  $('contentStatus').textContent=enabled?contentNote:'';
  if (!enabled) return;
  const observed=compact(Array.from(events.values()).sort((a,b)=>a.t-b.t||a.id-b.id),true).filter(e=>typeof (e.contentText ?? e.d.content_text)==='string');
  function render(target,entries) {
    for (const e of entries.slice(-LIMIT)) {
      const b=node('button',e.kind + ' · ' + entryClock(e) + (e.burst?' · observed text burst · '+e.burst.state+' · '+e.burst.count+' deltas':'') + (e.truncated?' · truncated':''),'contentitem');
      b.append(node('span',e.text.length>320?e.text.slice(0,320)+'…':e.text,'snippet'));
      b.addEventListener('click',()=>inspectContent(e)); target.append(b);
    }
    if (!entries.length) target.append(node('p','No content available in this section.','mut'));
    if (entries.length>LIMIT) target.prepend(node('p','Showing latest ' + LIMIT + ' entries.','mut'));
  }
  render($('observedContent'),observed.map(e=>({id:String(e.id),kind:e.d.content_kind||e.activity||'message',text:e.contentText??e.d.content_text,precision:'observed',observed_at_ms:e.t,truncated:Boolean(e.burst?.truncated||e.d.content_truncated),burst:e.burst,live_session_id:e.d.live_session_id,tool_call_id:e.d.tool_call_id,delegation_id:e.d.delegation_id,acp_session_id:agentSession(e)||undefined})));
  render($('savedContent'),savedContent);
}
function stopContent() { clearTimeout(contentTimer); contentTimer=null; contentGeneration++; contentController?.abort(); }
function kickContent(delay=0) {
  clearTimeout(contentTimer); contentTimer=null;
  if (!$('content').checked || document.hidden || $('pause').checked || authBlocked || !sid || contentRunning) return;
  contentTimer=setTimeout(fetchContent,delay);
}
async function fetchContent() {
  if (contentRunning || !$('content').checked || document.hidden || $('pause').checked || authBlocked || !sid) return;
  contentRunning=true; const gen=contentGeneration, callSid=sid, abort=new AbortController(); contentController=abort;
  const timeout=setTimeout(()=>abort.abort(),15000); let retry=false;
  contentNote='Loading saved content…'; drawContent();
  try {
    const data=await get('/admin/api/timing/content?call_sid='+encodeURIComponent(callSid),abort.signal);
    if (gen!==contentGeneration || sid!==callSid || !$('content').checked) return;
    savedContent=Array.isArray(data.entries)?data.entries.filter(e=>e && typeof e.text==='string'):[];
    contentNote=data.note || (savedContent.length?'Saved content loaded.':'Historical content unavailable.');
  } catch(error) {
    if (gen===contentGeneration) { contentNote='Saved content unavailable: '+error.message+'. Use Refresh content to retry.'; retry=true; }
  } finally {
    clearTimeout(timeout); contentRunning=false; contentController=null;
    if (gen===contentGeneration) { drawContent(); if (live(call)||retry) kickContent(5000); }
    else kickContent();
  }
}
const scope = e => [e.d.live_session_id || '', e.d.clock_id || ''];
const key = (e, kind, id) => JSON.stringify([...scope(e),kind,typeof id,id]);
const ident = v => v !== undefined && v !== null && v !== '';
const live = c => c && !c.ended_at && !['completed','failed','busy','no-answer','canceled','cancelled'].includes(c.status);
const stamp = e => typeof e.detail?.ts_ms === 'number' && Number.isFinite(e.detail.ts_ms) ? e.detail.ts_ms : Number(e.ts) * 1000;
const finite = v => typeof v === 'number' && Number.isFinite(v);
const providerEvent = e => e.name.startsWith('provider.twilio.');
const precisionLabel = e => providerEvent(e) ? e.d.timestamp_precision_ms === 1000 ? '1s precision (1000ms units; not millisecond accuracy)' : 'provider precision unknown' : e.precise ? 'millisecond timestamp units' : 'seconds only (±1s precision)';
const deliveryAge = e => providerEvent(e) && finite(e.d.occurred_at_ms) ? e.received-e.d.occurred_at_ms : null;
function deliveryLabel(e) {
  const age=deliveryAge(e);
  return age === null ? 'occurrence unknown; positioned at receipt' : 'received ' + (age>=0?'+':'') + (age/1000).toFixed(1) + 's later' + (age<0?' · uncertainty: negative clock offset':'') + ' (clock-skew-inclusive)';
}
function normalize(e) {
  const d = e.detail && typeof e.detail === 'object' && !Array.isArray(e.detail) ? e.detail : {};
  const name=e.event === 'voice.timing' ? String(d.event || 'voice.timing') : String(e.event), external=name.startsWith('provider.twilio.');
  const received=external && finite(d.received_at_ms) ? d.received_at_ms : stamp(e);
  return {...e, d, received, t:external && finite(d.occurred_at_ms) ? d.occurred_at_ms : received, precise:external ? false : finite(d.ts_ms), name};
}
function activityKind(e) {
  if (providerEvent(e)) return '';
  const type=String(e.d.type || e.name);
  if (e.d.update === 'agent_message_chunk') return 'Agent progress';
  if (/transcript.*delta/i.test(type)) return /input/i.test(type)?'input transcript':'output transcript';
  if (/audio.*(?:delta|packet)|session\.usage\.updated/i.test(type)) return 'hidden';
  return '';
}
function correlationEndpoint(e) { return ['delegation_id','request_id','tool_call_id','rpc_id'].some(k=>ident(e.d[k])) || /^(model|tool|acp\.turn)\./.test(e.name); }
function agentSession(e) { return e.d.acp_session_id || e.d.session_id || ''; }
function agentTool(e) { return e.name === 'acp.received' && ident(e.d.tool_call_id) && ident(agentSession(e)); }
function laneOf(e) {
  if (providerEvent(e)) return 'twilio:' + (e.d.leg === 'sip' ? 'sip' : 'pstn');
  if (agentTool(e)) return 'tools:' + agentSession(e);
  if (e.name.startsWith('model.') || e.name === 'orchestrator.model') return 'reasoning';
  if (e.name.startsWith('model.') || e.name.startsWith('tool.') || e.name.startsWith('orchestrator.') || e.name.startsWith('handoff.')) return 'orchestrator';
  if (e.name.startsWith('acp.')) return agentSession(e) ? 'agent:' + agentSession(e) : 'transport';
  if (e.name.startsWith('call.')) return 'transport';
  if (e.name.startsWith('live.') || e.name.startsWith('voice.') || e.source === 'voice') return 'voice';
  return 'transport';
}
function status() {
  $('status').textContent = authBlocked ? 'Authentication required: reopen /admin, then reconnect' : document.hidden ? 'Hidden: polling stopped' : $('pause').checked ? 'Paused: fetching stopped' : message;
  $('status').dataset.error = String(isError || authBlocked);
  const age = sampled ? Math.max(0, Date.now() - sampled) : 0;
  let latest = 0; for (const e of events.values()) if (Number.isFinite(e.received)) latest = Math.max(latest,e.received);
   $('lag').textContent = sampled ? 'sample age ' + (age/1000).toFixed(1) + 's · last event age ' + (latest ? Math.max(0,(serverAtSample + age - latest)/1000).toFixed(1) + 's' : 'unknown') : 'No sample yet';
  $('callMeta').textContent = call ? call.status + ' · ' + (call.caller || 'masked caller unavailable') + ' · ' + (call.duration_seconds == null ? 'duration pending' : call.duration_seconds + 's') : '';
}
function inspect(e) {
  if (e?.contentEntry) { if ($('content').checked) { inspectContent(e.contentEntry); return; } e=null; }
  selected = e || null;
  if (e) $('inspectorPanel').open=true;
  $('selection').textContent = e ? '#' + e.id + ' · ' + e.name + ' · ' + new Date(e.t).toISOString() : 'Select an event';
  const raw = $('raw').checked;
  const provenance = e ? {source:e.source,clock_id:e.d.clock_id || 'Worker observation clock (unspecified)',occurred_at_ms:providerEvent(e) ? e.d.occurred_at_ms ?? null : e.t,received_at_ms:e.received,positioned_by:providerEvent(e) && !finite(e.d.occurred_at_ms) ? 'receipt time (occurrence unknown)' : 'event time',precision:precisionLabel(e),event_time_source:e.d.event_time_source || 'Worker observation timestamp',...(providerEvent(e) ? {provider_event_key:e.d.provider_event_key,provider_event_key_meaning:'dedupe fingerprint, not a claimed native event ID',provider_call_sid:e.d.provider_call_sid,root_call_sid:e.d.root_call_sid,parent_call_sid:e.d.parent_call_sid ?? null,leg:e.d.leg,observed_delivery_age_ms:deliveryAge(e),observed_delivery_age:deliveryLabel(e),delivery_age_meaning:'Reporting lag plus clock skew, not network latency; provider timestamp precision also limits this difference.'} : {})} : null;
  $('inspector').textContent = e ? JSON.stringify(raw ? {...original(e),provenance} : {id:e.id,event:e.event,...provenance,burst:e.burst,...($('content').checked && typeof (e.contentText??e.d.content_text)==='string'?{content_kind:e.d.content_kind,content_text:e.contentText??e.d.content_text}:{}),note:'Enable raw metadata to inspect details.'},null,2) : 'No event selected.';
  $('copy').disabled = !e || (!raw && !$('content').checked);
  $('download').disabled = !events.size || !raw;
  if (!e) { $('related').textContent = ''; return; }
  const ids = ['delegation_id','request_id','tool_call_id','rpc_id'];
  if (providerEvent(e)) {
    const related=Array.from(events.values()).filter(other=>other.id!==e.id && providerEvent(other) && ident(e.d.provider_call_sid) && e.d.provider_call_sid===other.d.provider_call_sid);
    $('related').textContent='Related same leg (exact provider_call_sid, not causality): ' + (related.length?related.slice(0,30).map(x=>'#' + x.id).join(', '):'none observed') + (e.d.parent_call_sid ? '. Observed parent-leg metadata: ' + e.d.parent_call_sid + '; no exact message match inferred.' : '');
    return;
  }
  const related = Array.from(events.values()).filter(other => other.id !== e.id && ids.some(k => ident(e.d[k]) && key(e,k,e.d[k]) === key(other,k,other.d[k]) && (k !== 'tool_call_id' || (agentTool(e) === agentTool(other) && (!agentTool(e) || agentSession(e) === agentSession(other))))));
  $('related').textContent = 'Same scoped identifiers: ' + (related.length ? related.slice(0,30).map(x => '#' + x.id).join(', ') + (related.length > 30 ? '…' : '') : 'none observed');
}
function original(e) { const {d,t,received,precise,name,collapsed,chars,activity,lastActivityMs,contentText,burst,...rest} = e; return displayData(rest); }
// This is an observation heuristic, not audio VAD or guaranteed speech-turn detection.
// Cadence is local to one stream; intervening messages on other streams do not flush it.
function streamRole(e) { return activityKind(e)==='Agent progress'?'agent_report':activityKind(e)==='input transcript'?'caller':'voice'; }
function mediaPoint(e) {
  const clock=e.d.media_clock_id || e.d.provider_clock_id || (/^(?:openai|provider|media)(?:[:._-]|$)/i.test(e.d.clock_id||'')?e.d.clock_id:null);
  const session=e.d.provider_session_id || e.d.live_session_id;
  return clock && session && finite(e.d.start_ms) && finite(e.d.end_ms) ? {clock,session,start_ms:e.d.start_ms,end_ms:e.d.end_ms} : null;
}
function compact(all,forContent=false) {
  if ($('raw').checked && !forContent) return all;
  const rows = [], streams = new Map();
  const threshold = s => Math.round(Math.max(750,Math.min(2000,(s.ema[s.basis]??250)*3)));
  function close(s,reason,event) {
    if (!s.row) return;
    s.row.burst.state='completed'; s.row.burst.end_reason=reason;
    if (event) s.row.burst.ended_by_event_id=event.id;
    s.row=null; s.nextReason=reason;
  }
  for (const e of all) {
    const voiceEnd=/^(?:voice\.)?live\.(?:closed|ended|disconnected|detached)$/.test(e.name);
    const acpEnd=/^acp\.turn\.(?:stopped|failed|cancelled)$/.test(e.name);
    const transcriptEnd=/transcript.*(?:done|completed)$/.test(String(e.d.type||''));
    if (voiceEnd || acpEnd || transcriptEnd) {
      for (const s of streams.values()) {
        const sameVoice=s.live===String(e.d.live_session_id||'');
        if ((voiceEnd && sameVoice && s.role!=='agent_report') ||
            (acpEnd && s.role==='agent_report' && s.session===String(agentSession(e))) ||
            (transcriptEnd && sameVoice && s.role===(/input/.test(e.d.type)?'caller':'voice'))) close(s,'explicit '+e.name+(transcriptEnd?' / '+e.d.type:''),e);
      }
    }
    const activity=activityKind(e);
    if (!activity) { rows.push(e); continue; }
    if (activity==='hidden') continue;
    const role=streamRole(e), session=role==='agent_report'?String(agentSession(e)):'';
    const k=JSON.stringify([...scope(e),role,session,e.d.content_kind||'']);
    let s=streams.get(k);
    if (!s) { s={live:String(e.d.live_session_id||''),role,session,ema:{},basis:'receipt',row:null,nextReason:'first observed delta'}; streams.set(k,s); }
    const media=mediaPoint(e), prior=s.lastMedia;
    const useMedia=media && prior && media.clock===prior.clock && media.session===prior.session && media.start_ms>=prior.start_ms && media.end_ms>=prior.end_ms;
    s.basis=useMedia?'provider_media_offset':'receipt';
    const gap=s.last?useMedia?Math.max(0,media.start_ms-prior.end_ms):e.received-s.last.received:null;
    const limit=threshold(s);
    if (s.row && media && prior && (media.clock!==prior.clock || media.session!==prior.session)) close(s,'explicit provider media clock/session changed');
    if (s.row && gap!==null && (gap>limit || gap<0)) close(s,gap<0?'observation clock reversed':'adaptive inactivity gap');
    if (gap!==null && gap>=0 && gap<=limit) s.ema[s.basis]=s.ema[s.basis]===undefined?gap:s.ema[s.basis]*0.75+gap*0.25;
    if (!s.row) {
      s.row={...e,collapsed:0,chars:0,activity,burst:{stream_identity:k,state:'live tail',start_reason:s.nextReason,count:0,chars:0,first_received_at_ms:e.received,last_received_at_ms:e.received,first_media:media,last_media:media,threshold_ms:limit,gap_basis:s.basis,preceding_gap_ms:gap,observations:[]}};
      rows.push(s.row);
    }
    const row=s.row, b=row.burst;
    if (typeof e.d.content_text==='string') row.contentText=(row.contentText||'')+e.d.content_text;
    row.collapsed++; row.chars+=finite(e.d.chars)?e.d.chars:(e.d.content_text||'').length;
    b.count=row.collapsed; b.chars=row.chars; b.last_received_at_ms=e.received; b.last_media=media;
    b.threshold_ms=threshold(s); b.gap_basis=s.basis; b.truncated=Boolean(b.truncated||e.d.content_truncated);
    b.observations.push({id:e.id,received_at_ms:e.received,media,live_session_id:e.d.live_session_id,clock_id:e.d.clock_id,acp_session_id:agentSession(e)||undefined,tool_call_id:e.d.tool_call_id,delegation_id:e.d.delegation_id});
    s.last=e; s.lastMedia=media;
  }
  for (const s of streams.values()) {
    if (!s.row) continue;
    const b=s.row.burst;
    if (call && !live(call)) close(s,'Voice Call ended (no inferred burst-end timestamp)');
    else if (sampled && serverAtSample-b.last_received_at_ms>b.threshold_ms) { b.state='idle'; b.end_reason='inactivity at server sample (not a lifecycle end)'; }
  }
  return rows.sort((a,b) => a.t-b.t || a.id-b.id);
}
function draw() {
  const all = Array.from(events.values()).filter(e => Number.isFinite(e.t)).sort((a,b) => a.t-b.t || a.id-b.id);
  const rows = compact(all), end = Math.min(rows.length, pageEnd), start = Math.max(0,end-LIMIT), visible = rows.slice(start,end);
  const lanes = [{id:'voice',title:'Voice Frontend',sub:'Provider sideband observations'}, {id:'reasoning',title:'Reasoning API',sub:'Worker-measured request spans'}, {id:'orchestrator',title:'Session Orchestrator',sub:'Worker control logic + tools'}, {id:'transport',title:'Dogwalk transport',sub:'ACP boundary: one Agent connection can host multiple sessions'}];
  const laneIds = new Set(lanes.map(l => l.id));
  const addLane = (id,title,sub) => { if (!laneIds.has(id)) { laneIds.add(id); lanes.push({id,title,sub}); } };
  const aliases = new Map(), retained = new Map();
  for (const e of all) {
    if (agentSession(e) && e.d.alias) aliases.set(String(agentSession(e)),String(e.d.alias));
    if (e.name === 'voice.live.attached' && Array.isArray(e.d.managed_sessions)) {
      for (const s of e.d.managed_sessions) {
        if (!s || !s.acp_session_id) continue;
        const id=String(s.acp_session_id); retained.set(id,s);
        if (s.alias) aliases.set(id,String(s.alias));
      }
    }
  }
  const sessionTitle = id => aliases.has(String(id)) ? 'Managed Session · ' + aliases.get(String(id)) : 'ACP Session · ' + String(id).slice(0,18);
  for (const [id,s] of retained) addLane('agent:' + id,sessionTitle(id),id + ' · retained at attach · ' + (s.turn_state||s.session_state||'state unknown'));
  let nested = 0;
  for (const e of all) {
    if (providerEvent(e)) addLane(laneOf(e),'Twilio/' + (e.d.leg === 'sip' ? 'SIP' : 'PSTN'),'Provider clock · occurrence or receipt fallback');
    const session = agentSession(e);
    if (session) addLane('agent:' + session,sessionTitle(session),String(session));
    if (agentTool(e)) addLane(laneOf(e),'Agent tools · ' + (aliases.get(String(session)) || String(session).slice(0,18)),String(session));
    if (e.d.parent_session_id && session) { nested++; addLane('agent:' + e.d.parent_session_id,sessionTitle(e.d.parent_session_id),String(e.d.parent_session_id) + ' · observed parent session'); }
  }
  // Deployment domains are grouping, not protocol identities or timestamp sources.
  // Reasoning API spans include network/application overhead, not only model compute.
  const domains = {
    twilio:{label:'Twilio',color:'#f3b85c'}, openai:{label:'OpenAI',color:'#b9f36b'},
    cloudflare:{label:'Cloudflare',color:'#79c7ff'}, daytona:{label:'Daytona',color:'#d5a6ff'},
  };
  const domainOf = id => id.startsWith('twilio:')?'twilio':['voice','reasoning'].includes(id)?'openai':['orchestrator','transport'].includes(id)?'cloudflare':'daytona';
  const sessionOrder = lanes.filter(l=>l.id.startsWith('agent:')).map(l=>l.id.slice(6));
  const orderOf = id => ({'twilio:pstn':0,'twilio:sip':1,voice:0,reasoning:1,orchestrator:0,transport:1}[id]
    ?? (sessionOrder.indexOf(id.slice(id.indexOf(':')+1))*2 + (id.startsWith('tools:')?1:0)));
  lanes.sort((a,b)=>Object.keys(domains).indexOf(domainOf(a.id))-Object.keys(domains).indexOf(domainOf(b.id)) || orderOf(a.id)-orderOf(b.id));
  $('nested').textContent = nested ? 'Nested ACP session metadata: ' + nested + ' observation(s); parent relationships are metadata, not inferred calls.' : 'nested ACP sessions: not observed';
  const hidden = all.filter(e=>activityKind(e)==='hidden' && !correlationEndpoint(e)).length;
  const aggregated = rows.filter(e=>e.collapsed).reduce((n,e)=>n+e.collapsed,0);
  $('counts').textContent = events.size + ' loaded · ' + rows.length + ' display rows · ' + hidden + ' packet/usage events hidden · ' + aggregated + ' deltas in observed text bursts' + (catchingUp ? ' · catching up' : '');
  $('window').textContent = rows.length ? (start+1) + '–' + end + ' / ' + rows.length + ' rows' : '0 rows';
  $('older').disabled = start === 0; $('newer').disabled = end >= rows.length;
  const head = $('heads'), groupRow=node('div',undefined,'domainrow'), trackRow=node('div',undefined,'trackrow');
  groupRow.append(node('div','runs at','axishead')); trackRow.append(node('div','elapsed / Δ','axishead'));
  head.replaceChildren(groupRow,trackRow);
  const centers=new Map(); let width=122;
  const groups=[];
  lanes.forEach(l => {
    const w=200, domain=domainOf(l.id), info=domains[domain];
    centers.set(l.id,width+w/2);
    const last=groups.at(-1); if(last?.domain===domain) last.width+=w; else groups.push({domain,x:width,width:w});
    width+=w;
    const n=node('div',l.title,'lane'); n.dataset.lane=l.id; n.dataset.domain=domain;
    n.style.background=info.color+'0b'; n.title=l.title+': '+l.sub; n.append(node('small',l.sub)); trackRow.append(n);
  });
  groups.forEach(g=>{const info=domains[g.domain], n=node('div',info.label,'domain');n.dataset.domain=g.domain;n.style.width=g.width+'px';n.style.color=info.color;n.style.background=info.color+'16';groupRow.append(n);});
  const x = id => centers.get(id);
  head.style.width = width + 'px';
  const plot = $('plot'); plot.replaceChildren(); plot.setAttribute('width',width);
  const positions = new Map(); let y = 24, previous = null;
  const gaps = [];
  for (const e of visible) {
    const delta = previous === null ? 0 : Math.max(0,e.t-previous);
    const compressed = $('compress').checked && delta > 2000;
    // Minimum spacing is intentional. Uncompressed gaps use 12 px per second.
    y += previous === null ? 12 : compressed ? 66 : Math.max(38,delta*0.012);
    if (compressed) gaps.push({y:y-32,ms:delta});
    positions.set(e.id,y); previous = e.t;
  }
  const height = y + 42; plot.setAttribute('height',height);
  groups.forEach(g=>plot.append(svg('rect',{x:g.x,y:0,width:g.width,height,fill:domains[g.domain].color,'fill-opacity':0.045,'data-domain':g.domain,'pointer-events':'none'})));
  lanes.forEach(l => plot.append(svg('line',{x1:x(l.id),x2:x(l.id),y1:0,y2:height,stroke:'#343731','stroke-dasharray':'3 6'})));
  const defs = svg('defs'); const marker = svg('marker',{id:'arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:6,markerHeight:6,orient:'auto-start-reverse'}); marker.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#79c7ff'})); defs.append(marker); plot.append(defs);
  gaps.forEach(g => { plot.append(svg('line',{x1:0,x2:width,y1:g.y,y2:g.y,stroke:'#71756c','stroke-dasharray':'2 8'}),svg('text',{x:8,y:g.y-5,class:'axis'},'gap ' + g.ms.toFixed(0) + 'ms (compressed)')); });
  const interactive = (group,e,label) => { group.setAttribute('class','event'); group.setAttribute('tabindex','0'); group.setAttribute('role','button'); group.setAttribute('aria-label',label); group.append(svg('title',{},label)); group.addEventListener('click',() => inspect(e)); group.addEventListener('keydown',ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); inspect(e); } }); };
  // Correlation only joins exact identifiers within the emitting live-session and clock.
  // It never pairs by adjacency, alias, event order, or provider media offsets.
  const pending = new Map(), rpcs = new Map(), delegations = new Map(), endpointMs = new Map();
  for (const e of all) {
    if (providerEvent(e)) continue; // Provider metadata never supplies Worker span or message endpoints.
    const d = e.d; let kind = '', id, begin = false, finish = false;
    if (e.name.startsWith('model.')) { kind='model'; id=d.request_id; begin=e.name==='model.started'; finish=['model.completed','model.failed'].includes(e.name); }
    if (e.name.startsWith('tool.')) { kind='orchestrator-tool'; id=d.tool_call_id; begin=e.name==='tool.started'; finish=['tool.completed','tool.failed'].includes(e.name); }
    if (e.name.startsWith('acp.turn.') && agentSession(e)) { kind='Prompt Turn'; id=agentSession(e); begin=e.name==='acp.turn.started'; finish=['acp.turn.stopped','acp.turn.failed'].includes(e.name); }
    if (agentTool(e)) { kind='agent-tool:' + agentSession(e); id=d.tool_call_id; begin=['pending','in_progress'].includes(d.tool_status); finish=['completed','failed','cancelled'].includes(d.tool_status); }
    if (kind && ident(id)) {
      const k = key(e,kind,id);
      if (begin && !pending.has(k)) pending.set(k,e);
      if (finish && pending.has(k)) { const a = pending.get(k); pending.delete(k); const label=agentTool(e)?'Agent tool ' + (d.tool_kind || a.d.tool_kind || 'unknown') + ' #' + String(id).slice(-7):kind; span(a,e,laneOf(a),label); }
    }
    if (e.name === 'acp.sent' && ident(d.rpc_id) && d.method) rpcs.set(key(e,'rpc',d.rpc_id),e);
    if (e.name === 'acp.received' && ident(d.rpc_id) && !d.method) {
      const k=key(e,'rpc',d.rpc_id), a=rpcs.get(k);
      if (a) { rpcs.delete(k); const target=agentSession(a) ? 'agent:' + agentSession(a) : 'transport'; span(a,e,'transport','ACP roundtrip'); if (target !== 'transport' && positions.has(e.id)) arrow(e,target,'transport','matched RPC response'); }
    }
    if (ident(d.delegation_id)) {
      const k=key(e,'delegation',d.delegation_id), a=delegations.get(k);
      if (a && laneOf(a)!==laneOf(e) && positions.has(a.id) && positions.has(e.id)) {
        plot.append(svg('line',{x1:x(laneOf(a)),y1:positions.get(a.id),x2:x(laneOf(e)),y2:positions.get(e.id),stroke:'#71756c','stroke-dasharray':'3 5'}));
      }
      delegations.set(k,e);
    }
  }
  for (const a of pending.values()) {
    if (!positions.has(a.id)) continue;
    const xx=x(laneOf(a))-8, yy=positions.get(a.id);
    plot.append(svg('line',{x1:xx,x2:xx,y1:yy,y2:height-10,stroke:'#b9f36b','stroke-dasharray':'4 4',opacity:.6}),svg('text',{x:xx+10,y:height-14},'start observed; end not observed'));
  }
  function span(a,b,lane,label) {
    if (b.t>=a.t) endpointMs.set(b.id,{ms:b.t-a.t,precise:a.precise && b.precise});
    if (!positions.has(a.id) || !positions.has(b.id) || b.t<a.t) return;
    const yy=positions.get(a.id), by=positions.get(b.id), xx=x(lane)-8;
    const g=svg('g');
    const lifecycle=label==='Prompt Turn' && Number.isFinite(a.d.started_ms) && Number.isFinite(b.d.ended_ms) && b.d.ended_ms>=a.d.started_ms;
    const duration=lifecycle ? b.d.ended_ms-a.d.started_ms : b.t-a.t;
    g.append(svg('rect',{x:xx-3,y:yy,width:6,height:Math.max(3,by-yy),fill:label.includes('tool')?'#d5a6ff':'#b9f36b',opacity:.7}));
    g.append(svg('text',{x:xx+12,y:(yy+by)/2-4},label + ' ' + duration + 'ms' + (!a.precise || !b.precise ? ' (second-only)' : '')));
    interactive(g,b,label + ': matched #' + a.id + ' to #' + b.id + ', ' + (lifecycle?'recorded lifecycle ':'observation interval ') + duration + 'ms'); plot.append(g);
  }
  function arrow(e,from,to,label) {
    const yy=positions.get(e.id), g=svg('g');
    g.append(svg('line',{x1:x(from),x2:x(to),y1:yy,y2:yy,stroke:'#79c7ff','marker-end':'url(#arrow)'}));
    interactive(g,e,label); plot.append(g);
  }
  previous = null;
  const origin = Number(call?.started_at)*1000;
  for (const e of visible) {
    const yy=positions.get(e.id), lane=laneOf(e), xx=x(lane), d=e.d;
    if (e.name==='live.sent') arrow(e,'transport','voice','Observed live control send');
    if (e.name==='live.received') arrow(e,'voice','transport','Observed live control receipt, not phone playout');
    if (e.name==='acp.sent' && agentSession(e)) arrow(e,'transport','agent:' + agentSession(e),'Observed ACP send');
    if (e.name==='acp.received' && d.method && agentSession(e)) arrow(e,'agent:' + agentSession(e),'transport','Observed ACP notification/request receipt');
    const g=svg('g');
    g.append(svg('rect',{x:0,y:yy-15,width,height:31,fill:selected?.id===e.id?'#b9f36b15':'transparent',class:'hit'}));
    const delta=previous===null?'': ' Δ' + (e.t-previous).toFixed(0);
    g.append(svg('text',{x:8,y:yy-3,class:'axis'},(Number.isFinite(origin)?(e.t-origin).toFixed(0):'?') + 'ms'),svg('text',{x:8,y:yy+10,class:'axis'},delta + (providerEvent(e)?e.d.timestamp_precision_ms===1000?' 1s precision':' precision ?':!e.precise?' ±1s precision':'')));
    g.append(svg('circle',{cx:xx,cy:yy,r:3.5,fill:e.name.includes('failed')?'#ff796d':'#f3b85c'}));
    const friendly={'model.started':'Model request started','model.completed':'Model completed','model.failed':'Model failed','model.headers':'Model headers received','tool.started':'Tool started','tool.completed':'Tool completed','acp.turn.started':'Prompt Turn started','acp.turn.stopped':'Prompt Turn stopped','acp.turn.failed':'Prompt Turn failed'};
    let label=(friendly[e.name]||e.name) + (d.type?' · ' + d.type:'') + (d.name && e.name.startsWith('tool.')?' · ' + d.name:'') + (d.update?' · ' + String(d.update):'') + (d.tool_status?' · ' + d.tool_status:'');
    if (agentTool(e)) label=(d.tool_kind || 'Agent tool') + ' #' + String(d.tool_call_id).slice(-7) + ' · ' + (d.tool_status || 'update');
    if (e.collapsed) label='observed text burst · ' + e.activity + ' · ' + e.collapsed + ' deltas / ' + e.chars + ' chars · ' + e.burst.state;
    const matched=endpointMs.get(e.id);
    const duration=Number.isFinite(d.ms)?d.ms + 'ms':matched?'Δ ' + matched.ms + 'ms' + (matched.precise?'':' (seconds only)'):'';
    const metrics=providerEvent(e)?deliveryLabel(e):[duration,Number(d.queue_ms)>0?'queue ' + d.queue_ms + 'ms':''].filter(Boolean).join(' · ');
    // Keep labels in their lane; the inspector retains the complete observation.
    g.append(svg('text',{x:xx-94,y:yy-7},label.length>31?label.slice(0,30)+'…':label));
    if (metrics) g.append(svg('text',{x:xx-94,y:yy+13,class:'metric'},metrics));
    interactive(g,e,'#' + e.id + ' ' + label + (metrics?' · ' + metrics:'')); plot.append(g); previous=e.t;
  }
  $('empty').hidden = visible.length>0;
  if (!visible.length) $('empty').textContent = sid ? 'No observations loaded for this call.' : 'No call selected. No synthetic data is shown.';
  if ($('auto').checked && pageEnd===Infinity) $('chartScroll').scrollTop=$('chartScroll').scrollHeight;
  $('download').disabled = !events.size || !$('raw').checked;
  drawContent();
}
function updateCalls() {
  const select=$('calls'); select.replaceChildren(node('option','Select a Voice Call'));
  select.firstChild.value='';
  const list=calls.slice(); if (call && !list.some(c=>c.call_sid===sid)) list.push(call);
  list.sort((a,b)=>Number(b.started_at)-Number(a.started_at));
  list.forEach(c=>{ const o=node('option',(live(c)?'LIVE · ':'') + new Date(Number(c.started_at)*1000).toLocaleString() + ' · ' + c.status + ' · ' + (c.caller||'masked caller') + ' · ' + c.call_sid); o.value=c.call_sid; select.append(o); });
  select.value=sid;
  const latest=list.find(live);
  if ($('follow').checked && latest && latest.call_sid!==sid) choose(latest.call_sid);
  else if (!sid && list.length) choose(list[0].call_sid);
}
function choose(value) {
  $('calls').value=value;
  if (value===sid) return;
  sid=value; call=calls.find(c=>c.call_sid===sid)||null; events=new Map(); cursor=0; selected=null; pageEnd=Infinity; sampled=0; catchingUp=false;
  generation++; nextEvents=0; controller?.abort(); stopContent(); savedContent=[]; contentNote=''; inspect(null); draw(); kick(); kickContent();
}
async function get(url,signal) {
  const response=await fetch(url,{credentials:'same-origin',cache:'no-store',signal,headers:{Accept:'application/json'}});
  if (response.status===401 || response.status===403) { authBlocked=true; throw new Error('Operator authentication required'); }
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return response.json();
}
function stop() { clearTimeout(timer); timer=null; generation++; controller?.abort(); stopContent(); status(); }
function kick() {
  clearTimeout(timer); timer=null;
  if (document.hidden || $('pause').checked || authBlocked) { status(); return; }
  if (running) { rerun=true; return; }
  timer=setTimeout(poll,0);
}
async function poll() {
  if (running || document.hidden || $('pause').checked || authBlocked) return;
  running=true; rerun=false; const gen=generation; controller=new AbortController();
  const timeout=setTimeout(()=>controller?.abort(),15000);
  try {
    if (Date.now()>=nextCalls) {
      const data=await get('/admin/api/timing/calls',controller.signal);
      if (gen!==generation) return;
      calls=Array.isArray(data.calls)?data.calls:[]; nextCalls=Date.now()+5000; updateCalls();
      if (gen!==generation) return;
    }
    if (sid && Date.now()>=nextEvents) {
      // At most four pages per pass. Yield to the UI before continuing a large backlog.
      for (let page=0;page<4;page++) {
        const before=cursor;
        const data=await get('/admin/api/timing?call_sid=' + encodeURIComponent(sid) + '&after=' + cursor,controller.signal);
        if (gen!==generation) return;
        if (!Array.isArray(data.events) || !Number.isSafeInteger(data.cursor) || data.cursor<cursor) throw new Error('Invalid timing response/cursor');
        for (const e of data.events) if (Number.isSafeInteger(e.id)) events.set(e.id,normalize(e));
        cursor=data.cursor; call=data.call||call; sampled=Date.now(); serverAtSample=Number(data.server_ms)||sampled;
        catchingUp=Boolean(data.has_more);
        if (catchingUp && cursor<=before) throw new Error('Catch-up stalled: cursor did not advance');
        if (!catchingUp) break;
      }
      nextEvents=Date.now()+(catchingUp?100:1000); draw();
    }
    failures=0; isError=false; message=catchingUp?'Catching up':'Connected · polling 1s';
  } catch (error) {
    if (gen===generation) { failures++; isError=true; message='Retrying: ' + error.message; }
  } finally {
    clearTimeout(timeout); controller=null; running=false; status();
    if (!document.hidden && !$('pause').checked && !authBlocked) {
      const delay=rerun?0:failures?Math.min(30000,1000*Math.pow(2,Math.min(failures-1,5))):Math.max(100,Math.min(nextCalls, sid?nextEvents:Infinity)-Date.now());
      timer=setTimeout(poll,delay);
    }
  }
}
$('calls').addEventListener('change',()=>{ $('follow').checked=false; choose($('calls').value); });
$('follow').addEventListener('change',()=>{ updateCalls(); kick(); });
$('pause').addEventListener('change',()=>{ stop(); if (!$('pause').checked) { kick(); kickContent(); } });
document.addEventListener('visibilitychange',()=>{ stop(); if (!document.hidden) { nextCalls=0; nextEvents=0; kick(); kickContent(); } });
$('retry').addEventListener('click',()=>{ stop(); authBlocked=false; failures=0; nextCalls=0; nextEvents=0; message='Reconnecting'; kick(); kickContent(); });
$('auto').addEventListener('change',()=>{ if ($('auto').checked) pageEnd=Infinity; draw(); });
$('compress').addEventListener('change',draw);
$('raw').addEventListener('change',()=>{ pageEnd=Infinity; draw(); inspect(selected); });
$('content').addEventListener('change',()=>{ stopContent(); drawContent(); inspect(selected); if ($('content').checked) kickContent(); });
$('refreshContent').addEventListener('click',()=>{ stopContent(); kickContent(); });
$('older').addEventListener('click',()=>{ const size=compact(Array.from(events.values()).sort((a,b)=>a.t-b.t||a.id-b.id)).length; pageEnd=Math.max(LIMIT,Math.min(size,pageEnd)-LIMIT); $('auto').checked=false; draw(); $('chartScroll').scrollTop=0; });
$('newer').addEventListener('click',()=>{ const size=compact(Array.from(events.values()).sort((a,b)=>a.t-b.t||a.id-b.id)).length; pageEnd=pageEnd+LIMIT>=size?Infinity:pageEnd+LIMIT; draw(); $('chartScroll').scrollTop=0; });
$('copy').addEventListener('click',async()=>{ if (!selected || (!$('raw').checked && !$('content').checked)) return; try { await navigator.clipboard.writeText($('inspector').textContent); $('selection').textContent='Selected JSON copied locally'; } catch { $('selection').textContent='Clipboard unavailable: select inspector text manually'; } });
$('download').addEventListener('click',()=>{ if (!$('raw').checked) return; const blob=new Blob([JSON.stringify(displayData({call,events:Array.from(events.values(),original),...($('content').checked?{saved_content:savedContent}:{}),cursor,loaded_only:true}),null,2)],{type:'application/json'}); const url=URL.createObjectURL(blob), a=node('a'); a.href=url; a.download='dogwalk-timing.json'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); });
window.addEventListener('pagehide',stop);
draw(); kick();
})();
</script></body></html>`;
