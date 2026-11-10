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
</style></head><body><main>
<header class="top"><h1>dogwalk <span class="mut">/ operator timing</span></h1><div><span class="badge">read-only · operator</span> <a href="/admin">Diagnostics</a></div></header>
<div class="controls"><label>Voice Call <select id="calls" aria-label="Voice Call"><option value="">Loading calls…</option></select></label><label><input id="follow" type="checkbox" checked>follow latest live</label><label><input id="pause" type="checkbox">pause fetching</label><label><input id="auto" type="checkbox" checked>autoscroll</label><label><input id="compress" type="checkbox" checked>compress gaps &gt; 2s</label><label><input id="raw" type="checkbox">raw events + metadata (sensitive)</label><button id="retry">Reconnect</button></div>
<div class="summary" role="status" aria-live="polite"><strong id="status">Connecting</strong><span id="lag">No sample yet</span><span id="counts">0 events</span><span id="callMeta"></span></div>
<div class="workspace"><section class="chartbox" aria-label="Observed sequence diagram"><div id="chartScroll"><div id="heads" class="lanehead"></div><svg id="plot" role="img" aria-label="Time flows down; observed event sequence"></svg><div id="empty">No call selected. No synthetic data is shown.</div></div><div class="paging"><button id="older">Older rows</button><span id="window">0 rows</span><button id="newer">Newer rows</button></div></section>
<details id="inspectorPanel"><summary>Event inspector (expand / collapse)</summary><aside><h2>Event inspector</h2><div id="selection" class="mut">Select a mark, span, or message. Keyboard: Tab, then Enter.</div><pre id="inspector">Verbose details are hidden until raw metadata is enabled.</pre><div class="inspect-actions"><button id="copy" disabled>Copy selected JSON</button><button id="download" disabled>Download loaded JSON</button></div><p class="note">Memory only. No credentials or events are saved in browser storage. Exports stay local and may contain sensitive data.</p><div id="related" class="note"></div></aside></details></div>
<section class="legend"><p><b>Evidence:</b> ● observed event; → observed control-message direction; vertical colored bar = matched start/end identifiers; dashed vertical tail = start observed, end not observed; dotted connector = same delegation identifier, not a causal claim. Unmatched endpoints remain individual events.</p><p>Time flows down. Axis: milliseconds since call start (call-start anchor has second precision), with Δ between visible observations. Minimum row spacing separates simultaneous events; this is not a proportional waveform. Long gaps are explicitly labeled when compressed. Old second-only records are marked “±1s precision”; cross-clock differences are wall-clock observations, not latency measurements.</p><p>Caller lane is intentionally empty unless caller-side evidence exists. Transcript receipt and provider media offsets are not phone playout timestamps. Media start_ms/end_ms/offset_ms remain metadata, never Unix time. Orchestrator tools are not ACP Agent tools. ACP roundtrips require an exact scoped RPC identifier match.</p><p id="nested">nested ACP sessions: not observed</p></section>
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
const scope = e => [e.d.live_session_id || '', e.d.clock_id || ''];
const key = (e, kind, id) => JSON.stringify([...scope(e),kind,typeof id,id]);
const ident = v => v !== undefined && v !== null && v !== '';
const live = c => c && !c.ended_at && !['completed','failed','busy','no-answer','canceled','cancelled'].includes(c.status);
const stamp = e => typeof e.detail?.ts_ms === 'number' && Number.isFinite(e.detail.ts_ms) ? e.detail.ts_ms : Number(e.ts) * 1000;
function normalize(e) {
  const d = e.detail && typeof e.detail === 'object' && !Array.isArray(e.detail) ? e.detail : {};
  return {...e, d, t:stamp(e), precise:typeof d.ts_ms === 'number' && Number.isFinite(d.ts_ms), name:e.event === 'voice.timing' ? String(d.event || 'voice.timing') : String(e.event)};
}
function activityKind(e) {
  const type=String(e.d.type || e.name);
  if (/audio.*(?:delta|packet)|session\.usage\.updated/i.test(type)) return 'hidden';
  if (/transcript.*delta/i.test(type)) return /input/i.test(type)?'input transcript':'output transcript';
  if (e.d.update === 'agent_message_chunk') return 'Agent progress';
  return '';
}
function correlationEndpoint(e) { return ['delegation_id','request_id','tool_call_id','rpc_id'].some(k=>ident(e.d[k])) || /^(model|tool|acp\.turn)\./.test(e.name); }
function agentSession(e) { return e.d.acp_session_id || e.d.session_id || ''; }
function agentTool(e) { return e.name === 'acp.received' && ident(e.d.tool_call_id) && ident(agentSession(e)); }
function laneOf(e) {
  if (agentTool(e)) return 'tools:' + agentSession(e);
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
  let latest = 0; for (const e of events.values()) if (Number.isFinite(e.t)) latest = Math.max(latest,e.t);
   $('lag').textContent = sampled ? 'sample age ' + (age/1000).toFixed(1) + 's · last event age ' + (latest ? Math.max(0,(serverAtSample + age - latest)/1000).toFixed(1) + 's' : 'unknown') : 'No sample yet';
  $('callMeta').textContent = call ? call.status + ' · ' + (call.caller || 'masked caller unavailable') + ' · ' + (call.duration_seconds == null ? 'duration pending' : call.duration_seconds + 's') : '';
}
function inspect(e) {
  selected = e || null;
  if (e) $('inspectorPanel').open=true;
  $('selection').textContent = e ? '#' + e.id + ' · ' + e.name + ' · ' + new Date(e.t).toISOString() : 'Select an event';
  const raw = $('raw').checked;
  $('inspector').textContent = e ? JSON.stringify(raw ? original(e) : {id:e.id,ts:e.ts,source:e.source,event:e.event,precision:e.precise ? 'milliseconds' : 'seconds only',note:'Enable raw metadata to inspect details.'},null,2) : 'No event selected.';
  $('copy').disabled = !e || !raw;
  $('download').disabled = !events.size || !raw;
  if (!e) { $('related').textContent = ''; return; }
  const ids = ['delegation_id','request_id','tool_call_id','rpc_id'];
  const related = Array.from(events.values()).filter(other => other.id !== e.id && ids.some(k => ident(e.d[k]) && key(e,k,e.d[k]) === key(other,k,other.d[k]) && (k !== 'tool_call_id' || (agentTool(e) === agentTool(other) && (!agentTool(e) || agentSession(e) === agentSession(other))))));
  $('related').textContent = 'Same scoped identifiers: ' + (related.length ? related.slice(0,30).map(x => '#' + x.id).join(', ') + (related.length > 30 ? '…' : '') : 'none observed');
}
function original(e) { const {d,t,precise,name,collapsed,chars,activity,lastActivityMs,...rest} = e; return rest; }
function compact(all) {
  if ($('raw').checked) return all;
  const rows = [], buckets = new Map();
  for (const e of all) {
    const activity=activityKind(e);
    if (!activity || correlationEndpoint(e)) { rows.push(e); continue; }
    if (activity==='hidden') continue;
    const k = JSON.stringify([...scope(e),laneOf(e),agentSession(e),activity,Math.floor(e.t/5000)]);
    let bucket = buckets.get(k);
    if (!bucket) { bucket = {...e, collapsed:0, chars:0,activity}; buckets.set(k,bucket); rows.push(bucket); }
    bucket.collapsed++; bucket.chars += Number(e.d.chars) || 0; bucket.lastActivityMs=e.t;
  }
  return rows.sort((a,b) => a.t-b.t || a.id-b.id);
}
function draw() {
  const all = Array.from(events.values()).filter(e => Number.isFinite(e.t)).sort((a,b) => a.t-b.t || a.id-b.id);
  const rows = compact(all), end = Math.min(rows.length, pageEnd), start = Math.max(0,end-LIMIT), visible = rows.slice(start,end);
  const lanes = [{id:'caller',title:'Caller',sub:'No phone playout inferred'}, {id:'voice',title:'Voice Frontend',sub:'Provider sideband observations'}, {id:'orchestrator',title:'Session Orchestrator',sub:'Model + Orchestrator tools'}, {id:'transport',title:'Dogwalk transport',sub:'ACP boundary: one Agent connection can host multiple sessions'}];
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
    const session = agentSession(e);
    if (session) addLane('agent:' + session,sessionTitle(session),String(session));
    if (agentTool(e)) addLane(laneOf(e),'Agent tools · ' + (aliases.get(String(session)) || String(session).slice(0,18)),String(session));
    if (e.d.parent_session_id && session) { nested++; addLane('agent:' + e.d.parent_session_id,sessionTitle(e.d.parent_session_id),String(e.d.parent_session_id) + ' · observed parent session'); }
  }
  $('nested').textContent = nested ? 'Nested ACP session metadata: ' + nested + ' observation(s); parent relationships are metadata, not inferred calls.' : 'nested ACP sessions: not observed';
  const hidden = all.filter(e=>activityKind(e)==='hidden' && !correlationEndpoint(e)).length;
  const aggregated = rows.filter(e=>e.collapsed).reduce((n,e)=>n+e.collapsed,0);
  $('counts').textContent = events.size + ' loaded · ' + rows.length + ' display rows · ' + hidden + ' packet/usage events hidden · ' + aggregated + ' chunks in 5s buckets' + (catchingUp ? ' · catching up' : '');
  $('window').textContent = rows.length ? (start+1) + '–' + end + ' / ' + rows.length + ' rows' : '0 rows';
  $('older').disabled = start === 0; $('newer').disabled = end >= rows.length;
  const head = $('heads'); head.replaceChildren(node('div','elapsed / Δ','axishead'));
  const centers=new Map(); let width=122;
  lanes.forEach(l => { const w=l.id==='caller'?76:200; centers.set(l.id,width+w/2); width+=w; const n = node('div',l.title,'lane' + (l.id==='caller'?' caller':'')); n.title = l.title + ': ' + l.sub; n.append(node('small',l.sub)); head.append(n); });
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
  lanes.forEach(l => plot.append(svg('line',{x1:x(l.id),x2:x(l.id),y1:0,y2:height,stroke:'#343731','stroke-dasharray':'3 6'})));
  const defs = svg('defs'); const marker = svg('marker',{id:'arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:6,markerHeight:6,orient:'auto-start-reverse'}); marker.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#79c7ff'})); defs.append(marker); plot.append(defs);
  gaps.forEach(g => { plot.append(svg('line',{x1:0,x2:width,y1:g.y,y2:g.y,stroke:'#71756c','stroke-dasharray':'2 8'}),svg('text',{x:8,y:g.y-5,class:'axis'},'gap ' + g.ms.toFixed(0) + 'ms (compressed)')); });
  const interactive = (group,e,label) => { group.setAttribute('class','event'); group.setAttribute('tabindex','0'); group.setAttribute('role','button'); group.setAttribute('aria-label',label); group.append(svg('title',{},label)); group.addEventListener('click',() => inspect(e)); group.addEventListener('keydown',ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); inspect(e); } }); };
  // Correlation only joins exact identifiers within the emitting live-session and clock.
  // It never pairs by adjacency, alias, event order, or provider media offsets.
  const pending = new Map(), rpcs = new Map(), delegations = new Map(), endpointMs = new Map();
  for (const e of all) {
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
    g.append(svg('text',{x:8,y:yy-3,class:'axis'},(Number.isFinite(origin)?(e.t-origin).toFixed(0):'?') + 'ms'),svg('text',{x:8,y:yy+10,class:'axis'},delta + (!e.precise?' ±1s precision':'')));
    g.append(svg('circle',{cx:xx,cy:yy,r:3.5,fill:e.name.includes('failed')?'#ff796d':'#f3b85c'}));
    const friendly={'model.started':'Model request started','model.completed':'Model completed','model.failed':'Model failed','model.headers':'Model headers received','tool.started':'Tool started','tool.completed':'Tool completed','acp.turn.started':'Prompt Turn started','acp.turn.stopped':'Prompt Turn stopped','acp.turn.failed':'Prompt Turn failed'};
    let label=(friendly[e.name]||e.name) + (d.type?' · ' + d.type:'') + (d.name?' · ' + d.name:'') + (d.update?' · ' + String(d.update):'') + (d.tool_status?' · ' + d.tool_status:'');
    if (agentTool(e)) label=(d.tool_kind || 'Agent tool') + ' #' + String(d.tool_call_id).slice(-7) + ' · ' + (d.tool_status || 'update');
    if (e.collapsed) label=e.activity + ' · ' + e.collapsed + ' chunks' + (e.chars?' / ' + e.chars + ' chars':'') + ' (5s bucket)';
    const matched=endpointMs.get(e.id);
    const duration=Number.isFinite(d.ms)?d.ms + 'ms':matched?'Δ ' + matched.ms + 'ms' + (matched.precise?'':' (seconds only)'):'';
    const metrics=[duration,Number(d.queue_ms)>0?'queue ' + d.queue_ms + 'ms':''].filter(Boolean).join(' · ');
    // Keep labels in their lane; the inspector retains the complete observation.
    g.append(svg('text',{x:xx-94,y:yy-7},label.length>31?label.slice(0,30)+'…':label));
    if (metrics) g.append(svg('text',{x:xx-94,y:yy+13,class:'metric'},metrics));
    interactive(g,e,'#' + e.id + ' ' + label + (metrics?' · ' + metrics:'')); plot.append(g); previous=e.t;
  }
  $('empty').hidden = visible.length>0;
  if (!visible.length) $('empty').textContent = sid ? 'No observations loaded for this call.' : 'No call selected. No synthetic data is shown.';
  if ($('auto').checked && pageEnd===Infinity) $('chartScroll').scrollTop=$('chartScroll').scrollHeight;
  $('download').disabled = !events.size || !$('raw').checked;
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
  generation++; nextEvents=0; controller?.abort(); inspect(null); draw(); kick();
}
async function get(url,signal) {
  const response=await fetch(url,{credentials:'same-origin',cache:'no-store',signal,headers:{Accept:'application/json'}});
  if (response.status===401 || response.status===403) { authBlocked=true; throw new Error('Operator authentication required'); }
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return response.json();
}
function stop() { clearTimeout(timer); timer=null; generation++; controller?.abort(); status(); }
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
$('pause').addEventListener('change',()=>{ stop(); if (!$('pause').checked) kick(); });
document.addEventListener('visibilitychange',()=>{ stop(); if (!document.hidden) { nextCalls=0; nextEvents=0; kick(); } });
$('retry').addEventListener('click',()=>{ stop(); authBlocked=false; failures=0; nextCalls=0; nextEvents=0; message='Reconnecting'; kick(); });
$('auto').addEventListener('change',()=>{ if ($('auto').checked) pageEnd=Infinity; draw(); });
$('compress').addEventListener('change',draw);
$('raw').addEventListener('change',()=>{ pageEnd=Infinity; draw(); inspect(selected); });
$('older').addEventListener('click',()=>{ const size=compact(Array.from(events.values()).sort((a,b)=>a.t-b.t||a.id-b.id)).length; pageEnd=Math.max(LIMIT,Math.min(size,pageEnd)-LIMIT); $('auto').checked=false; draw(); $('chartScroll').scrollTop=0; });
$('newer').addEventListener('click',()=>{ const size=compact(Array.from(events.values()).sort((a,b)=>a.t-b.t||a.id-b.id)).length; pageEnd=pageEnd+LIMIT>=size?Infinity:pageEnd+LIMIT; draw(); $('chartScroll').scrollTop=0; });
$('copy').addEventListener('click',async()=>{ if (!selected || !$('raw').checked) return; try { await navigator.clipboard.writeText(JSON.stringify(original(selected),null,2)); $('selection').textContent='Selected JSON copied locally'; } catch { $('selection').textContent='Clipboard unavailable: select inspector text manually'; } });
$('download').addEventListener('click',()=>{ if (!$('raw').checked) return; const blob=new Blob([JSON.stringify({call,events:Array.from(events.values(),original),cursor,loaded_only:true},null,2)],{type:'application/json'}); const url=URL.createObjectURL(blob), a=node('a'); a.href=url; a.download='dogwalk-timing.json'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); });
window.addEventListener('pagehide',stop);
draw(); kick();
})();
</script></body></html>`;
