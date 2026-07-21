export const ADMIN_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dogwalk Diagnostics</title>
<style>
:root{color-scheme:dark;--ink:#f4f0e6;--mut:#9c9a91;--fnt:#71756c;--ln:#343731;--pnl:#181b17;--bg:#0c0e0c;--grn:#b9f36b;--amb:#f3b85c;--red:#ff796d;--blu:#79c7ff;--vio:#d5a6ff;--pnk:#ff8bc6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}
a{color:var(--blu)}
main{max-width:1200px;margin:0 auto;padding:16px 18px 48px}
.top{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--ln);padding-bottom:10px;margin-bottom:12px;gap:16px;flex-wrap:wrap}
.brand{font:700 16px/1 ui-sans-serif,system-ui;letter-spacing:.01em}
.brand .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--mut);margin-right:7px;vertical-align:baseline}
.brand .dot.on{background:var(--grn);box-shadow:0 0 10px var(--grn)}
.brand small{color:var(--mut);font:400 12px ui-monospace,monospace;margin-left:8px}
.lens{text-align:right;font-size:11px;color:var(--mut);line-height:1.6}
.ro{display:inline-block;border:1px solid var(--fnt);color:var(--fnt);padding:0 6px;font-size:9px;text-transform:uppercase;letter-spacing:.08em;border-radius:3px}
.controls{display:flex;align-items:center;gap:14px;justify-content:flex-end;margin-top:4px}
.controls label{color:var(--mut);font-size:11px;cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.controls input{accent-color:var(--amb)}
.controls button{font:inherit;font-size:11px;color:var(--bg);background:var(--grn);border:0;padding:5px 10px;cursor:pointer;border-radius:3px}
.layout{display:grid;grid-template-columns:360px 1fr;border:1px solid var(--ln);min-height:70vh}
.tree{border-right:1px solid var(--ln);overflow:auto;background:rgba(24,27,23,.4);max-height:78vh}
.treehead{position:sticky;top:0;z-index:2;background:#141712;padding:7px 12px;border-bottom:1px solid var(--ln);color:var(--fnt);font-size:9px;text-transform:uppercase;letter-spacing:.1em;display:flex;justify-content:space-between}
.node{display:block;width:100%;text-align:left;background:none;border:0;border-bottom:1px solid #191c17;color:var(--ink);font:inherit;padding:4px 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
.node:hover{background:rgba(255,255,255,.03)}
.node.sel{background:rgba(185,243,107,.10);border-left:2px solid var(--grn);padding-left:6px}
.node.root{background:#111410;font-weight:700;letter-spacing:.02em}
.node .tw{color:var(--fnt);display:inline-block;width:12px}
.node .ico{display:inline-block;width:15px;text-align:center;color:var(--fnt)}
.node .cnt{color:var(--fnt);font-size:10px}
.node .tag{float:right;color:var(--fnt);font-size:8px;text-transform:uppercase;letter-spacing:.05em;border:1px solid #2c2f28;padding:0 4px;border-radius:3px;margin-left:8px}
.node .nw{color:var(--amb);border-color:var(--amb)}
.d0{padding-left:8px}.d1{padding-left:26px}.d2{padding-left:44px}.d3{padding-left:62px}
.al{color:var(--vio);font-weight:700}.blu{color:var(--blu)}.grn{color:var(--grn)}.amb{color:var(--amb)}.red{color:var(--red)}.pnk{color:var(--pnk)}.mut{color:var(--mut)}.vio{color:var(--vio)}
.st{display:inline-block;padding:0 5px;border:1px solid currentColor;border-radius:99px;font-size:9px;text-transform:uppercase;margin-left:4px}
.st.started,.st.ready,.st.connected{color:var(--grn)}.st.creating,.st.starting,.st.restoring,.st.provisioning{color:var(--amb)}.st.error,.st.build_failed,.st.conflict{color:var(--red)}
.detail{overflow:auto;max-height:78vh}
.dhead{padding:12px 16px;border-bottom:1px solid var(--ln)}
.crumb{color:var(--fnt);font-size:10px;margin-bottom:7px}.crumb b{color:var(--mut)}
.dhead .t{font:700 16px ui-sans-serif,system-ui}
.dhead .meta{color:var(--mut);font-size:11px;margin-top:6px;word-break:break-word}
.sec{padding:10px 16px;border-bottom:1px solid #23261f}
.sec h4{margin:0 0 8px;color:var(--fnt);font-size:9px;text-transform:uppercase;letter-spacing:.1em}
.nwbox{margin:0;padding:8px 16px;background:rgba(243,184,92,.08);border-bottom:1px solid var(--ln);color:var(--amb);font-size:11px}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:5px 10px;border-bottom:1px solid #23261f;font-size:11px;vertical-align:top}
th{color:var(--fnt);text-transform:uppercase;font-size:9px;letter-spacing:.06em}
.tail{max-height:340px;overflow:auto}
.tailhead{position:sticky;top:0;background:#141712;padding:6px 12px;border-bottom:1px solid var(--ln);color:var(--fnt);font-size:9px;text-transform:uppercase;letter-spacing:.1em;display:flex;justify-content:space-between}
.tr{display:grid;grid-template-columns:64px 56px 1fr;gap:8px;padding:5px 12px;border-bottom:1px solid #20231d;font-size:10px;align-items:start}
.tr time{color:var(--mut)}
.chan{padding:0 4px;border:1px solid currentColor;text-align:center;text-transform:uppercase;font-size:8px}
.chan.voice{color:var(--blu)}.chan.access{color:var(--amb)}.chan.hosting{color:var(--grn)}.chan.menu{color:var(--vio)}.chan.acp{color:var(--pnk)}
.tr .dt{display:block;color:var(--mut);margin-top:3px;white-space:pre-wrap;word-break:break-word}
.standby{padding:34px 16px;color:var(--fnt);text-align:center;text-transform:uppercase;letter-spacing:.08em;font-size:11px}
.errorbox{padding:20px 16px;color:var(--red)}
@media(max-width:820px){.layout{grid-template-columns:1fr}.tree{border-right:0;border-bottom:1px solid var(--ln);max-height:40vh}.top{align-items:flex-start}}
</style>
</head>
<body><main>
<div class="top">
  <div class="brand"><span class="dot" id="live"></span>dogwalk <small>diagnostics</small></div>
  <div class="lens">
    <span class="ro">read-only mirror</span> &middot; operator
    <div class="controls">
      <label><input type="checkbox" id="verbose"> reveal verbose telemetry</label>
      <button id="refresh">reconnect</button>
    </div>
    <div id="generated" class="mut">waiting for signal</div>
  </div>
</div>
<div class="layout">
  <div class="tree"><div class="treehead"><span>runtime tree</span><span id="privacy" class="mut">demo-safe</span></div><div id="tree"></div></div>
  <div class="detail" id="detail"><div class="standby">select a node</div></div>
</div>
</main>
<script>
const q=(s)=>document.querySelector(s);
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined&&text!==null)n.textContent=String(text);if(cls)n.className=cls;return n};
const when=(v)=>v?new Date(v*1000).toLocaleString():"never";
const clock=(v)=>v?new Date(v*1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"}):"";
const elapsed=(from,now)=>{const s=Math.max(0,(now||0)-(from||0));return Math.floor(s/60)+"m "+(s%60)+"s"};

let DATA={registrations:[],live_calls:[],audit:[],verbose:false};
let SEL=null; // {kind, id}

// --- tree ---------------------------------------------------------------
function node(depth,opts){
  const b=el("button",undefined,"node d"+depth+(opts.root?" root":"")+(SEL&&SEL.kind===opts.kind&&SEL.id===opts.id?" sel":""));
  b.append(el("span",opts.twisty||"\\u00a0","tw"),el("span",opts.icon||"\\u00a0","ico"));
  const lbl=el("span",undefined,opts.cls);lbl.textContent=opts.label;b.append(lbl);
  if(opts.count!==undefined)b.append(document.createTextNode(" "),el("span","("+opts.count+")","cnt"));
  if(opts.state)b.append(stateChip(opts.state));
  if(opts.tag){const t=el("span",opts.tag,"tag"+(opts.notWired?" nw":""));b.append(t)}
  if(opts.onSelect)b.addEventListener("click",()=>{SEL={kind:opts.kind,id:opts.id};renderDetail();markSel()});
  return b;
}
function stateChip(s){return el("span",s,"st "+String(s).toLowerCase())}
function markSel(){document.querySelectorAll(".node").forEach((n)=>n.classList.remove("sel"));}

function renderTree(){
  const t=q("#tree");t.replaceChildren();
  const now=DATA.generated_at;

  // ROOT: Voice Calls (wired)
  t.append(node(0,{root:true,icon:"\\u260e",label:"Voice Calls",count:DATA.live_calls.length,tag:"voice"}));
  if(!DATA.live_calls.length)t.append(el("div","no calls on the wire","standby"));
  DATA.live_calls.forEach((c)=>{
    t.append(node(1,{kind:"call",id:c.call_sid,icon:"\\u260e",cls:"blu",label:c.phone_number,state:c.status||"connected",onSelect:true}));
  });

  // ROOT: Agent Connections / Managed Sessions (not wired yet)
  t.append(node(0,{root:true,icon:"\\u21c4",label:"Agent Connections",tag:"acp / session mgmt",notWired:true}));
  t.append(el("div","managed sessions & prompt turns: telemetry not wired","standby"));

  // ROOT: Sandbox Assignments (wired via registrations)
  const started=DATA.registrations.filter((r)=>r.state==="started").length;
  t.append(node(0,{root:true,icon:"\\u25eb",label:"Sandbox Assignments",count:started+" started",tag:"hosting"}));
  DATA.registrations.filter((r)=>r.provider_id).forEach((r)=>{
    t.append(node(1,{kind:"sandbox",id:r.phone_number,icon:"\\u25eb",cls:"mut",label:sandboxLabel(r),state:r.state||"unknown",onSelect:true}));
  });

  // ROOT: Access Control (wired via registrations)
  t.append(node(0,{root:true,icon:"\\u2691",label:"Access Control",count:DATA.registrations.length,tag:"access"}));
  DATA.registrations.forEach((r)=>{
    t.append(node(1,{kind:"registration",id:r.phone_number,icon:"\\u260e",cls:"blu",label:r.phone_number,onSelect:true}));
  });

  // ROOT: Event Log (wired via audit)
  t.append(node(0,{kind:"eventlog",id:"all",root:true,icon:"\\u2630",label:"Event Log",count:DATA.audit.length,tag:"audit",onSelect:true}));
}
function sandboxLabel(r){return r.provider_id==="assigned"?"assigned":(r.provider_id?String(r.provider_id).slice(0,10)+"\\u2026":"unassigned")}

// --- detail -------------------------------------------------------------
function renderDetail(){
  const d=q("#detail");d.replaceChildren();
  if(!SEL){d.append(el("div","select a node","standby"));return}
  if(SEL.kind==="call")return renderCall(d,DATA.live_calls.find((c)=>c.call_sid===SEL.id));
  if(SEL.kind==="sandbox"||SEL.kind==="registration")return renderReg(d,DATA.registrations.find((r)=>r.phone_number===SEL.id),SEL.kind);
  if(SEL.kind==="eventlog")return renderAudit(d);
  d.append(el("div","select a node","standby"));
}
function crumb(d,parts){const c=el("div",undefined,"crumb");parts.forEach((p,i)=>{if(i)c.append(el("b"," \\u203a "));c.append(document.createTextNode(p))});d.append(c)}
function head(d,parts,title,titleCls,meta){const h=el("div",undefined,"dhead");const c=el("div",undefined,"crumb");parts.forEach((p,i)=>{if(i)c.append(el("b"," \\u203a "));c.append(document.createTextNode(p))});h.append(c);h.append(el("span",title,"t "+(titleCls||"")));if(meta)h.append(el("div",meta,"meta"));d.append(h)}

function renderCall(d,c){
  if(!c){d.append(el("div","call ended","standby"));return}
  head(d,["Voice Calls",c.phone_number],c.phone_number,"blu",
    "CallSid "+c.call_sid+" \\u00b7 "+elapsed(c.started_at,DATA.generated_at)+" \\u00b7 status "+(c.status||"connected"));
  const sec=el("div",undefined,"sec");sec.append(el("h4","Correlated call activity"));
  const th=el("div",undefined,"tailhead");th.append(el("span","sources: voice / access / hosting / menu / acp"),el("span","tailing \\u25bc","grn"));
  d.append(sec);const wrap=el("div",undefined,"tail");wrap.append(th);
  (c.activity||[]).slice().reverse().forEach((a)=>{
    const row=el("div",undefined,"tr");row.append(el("time",clock(a.ts)),el("span",a.source,"chan "+a.source));
    const ev=el("span");ev.append(document.createTextNode(a.event));
    if(DATA.verbose&&a.detail){let dt=a.detail;try{dt=JSON.stringify(JSON.parse(a.detail),null,2)}catch{}ev.append(el("span",dt,"dt"))}
    row.append(ev);wrap.append(row);
  });
  if(!(c.activity||[]).length)wrap.append(el("div","no activity yet","standby"));
  d.append(wrap);
}
function renderReg(d,r,kind){
  if(!r){d.append(el("div","not found","standby"));return}
  if(kind==="sandbox"){
    head(d,["Sandbox Assignments",r.phone_number],sandboxLabel(r),"mut",
      "phone "+r.phone_number+" \\u00b7 state "+(r.state||"unknown")+(r.desired_state?" \\u2192 "+r.desired_state:""));
  }else{
    head(d,["Access Control",r.phone_number],r.phone_number,"blu",
      "registered "+when(r.registered_at)+" \\u00b7 last seen "+when(r.last_seen_at));
  }
  const s=el("div",undefined,"sec");s.append(el("h4","Assignment"));
  const tb=el("table"),body=el("tbody");
  const rows=[["Sandbox state",r.state||"\\u2014"],["Provider id",r.provider_id||"\\u2014"],["Last checked",when(r.last_checked_at)],["Error",r.error||"none"]];
  rows.forEach(([k,v])=>{const tr=el("tr");tr.append(el("th",k),el("td",v));body.append(tr)});
  tb.append(body);s.append(tb);d.append(s);
  const nw=el("div",undefined,"sec");nw.append(el("h4","Managed Sessions on this sandbox"),el("div","Session Management telemetry not wired yet.","standby"));d.append(nw);
}
function renderAudit(d){
  head(d,["Event Log"],"Audit tail","","most recent "+DATA.audit.length+" events \\u00b7 "+(DATA.verbose?"verbose":"redacted"));
  const wrap=el("div",undefined,"tail");
  const th=el("div",undefined,"tailhead");th.append(el("span","event \\u00b7 phone \\u00b7 call"),el("span","newest first","grn"));wrap.append(th);
  DATA.audit.forEach((a)=>{
    const row=el("div",undefined,"tr");row.append(el("time",clock(a.ts)),el("span",a.source||"\\u2013","chan "+(a.source||"")));
    const ev=el("span");ev.append(document.createTextNode(a.event));
    ev.append(el("span",(a.phone_number||"-")+" / "+(a.call_sid||"-"),"dt"));
    row.append(ev);wrap.append(row);
  });
  if(!DATA.audit.length)wrap.append(el("div","no events","standby"));
  d.append(wrap);
}

// --- stream -------------------------------------------------------------
function render(data){
  DATA=data;
  q("#generated").textContent="live \\u00b7 updated "+clock(data.generated_at);
  q("#live").classList.add("on");
  q("#privacy").textContent=data.verbose?"verbose \\u2014 identifiers visible":"demo-safe";
  q("#privacy").style.color=data.verbose?"var(--amb)":"var(--fnt)";
  renderTree();renderDetail();
}
let source;
function connect(){
  if(source)source.close();
  q("#live").classList.remove("on");q("#generated").textContent="connecting telemetry";
  const verbose=q("#verbose").checked;
  source=new EventSource("/admin/api/events"+(verbose?"?verbose=1":""));
  source.addEventListener("state",(e)=>{try{render(JSON.parse(e.data))}catch{q("#generated").textContent="invalid telemetry"}});
  source.addEventListener("stream-error",(e)=>{try{q("#generated").textContent="stream error: "+JSON.parse(e.data).message}catch{q("#generated").textContent="stream error"}});
  source.onerror=()=>{q("#live").classList.remove("on");q("#generated").textContent="reconnecting telemetry"};
}
q("#refresh").addEventListener("click",connect);
q("#verbose").addEventListener("change",connect);
connect();
</script>
</body></html>`;

// Scoped Diagnostic View: the caller lens. Read-only, capability-scoped to one
// Voice Call, served at /v/<token>. Friendly Voice-Interaction language; no
// operator roots, no opaque identifiers. Fetches /v/<token>/data (relative).
export const SCOPED_VIEW_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Your Dogwalk session</title>
<style>
:root{color-scheme:dark;--ink:#f4f0e6;--mut:#9c9a91;--fnt:#71756c;--ln:#343731;--pnl:#181b17;--bg:#0c0e0c;--grn:#b9f36b;--amb:#f3b85c;--red:#ff796d;--blu:#79c7ff;--vio:#d5a6ff;--pnk:#ff8bc6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:680px;margin:0 auto;padding:22px 18px 56px}
.top{border-bottom:1px solid var(--ln);padding-bottom:12px;margin-bottom:16px}
.brand{font:700 17px/1 ui-sans-serif}.brand .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--grn);box-shadow:0 0 10px var(--grn);margin-right:7px}
.brand small{color:var(--mut);font-weight:400;margin-left:6px}
.lens{margin-top:8px;font-size:12px;color:var(--mut)}
.ro{display:inline-block;border:1px solid var(--fnt);color:var(--fnt);padding:0 6px;font-size:9px;text-transform:uppercase;letter-spacing:.08em;border-radius:3px}
.card{border:1px solid var(--ln);background:var(--pnl);border-radius:8px;margin-bottom:14px;overflow:hidden}
.card h2{margin:0;padding:11px 14px;border-bottom:1px solid var(--ln);color:var(--mut);font:600 11px/1 ui-monospace,monospace;text-transform:uppercase;letter-spacing:.1em}
.row{padding:10px 14px;border-bottom:1px solid #23261f;display:flex;gap:10px;align-items:baseline}
.row:last-child{border-bottom:0}
.row time{color:var(--mut);font:11px ui-monospace,monospace;flex:0 0 66px}
.row .ev{flex:1}
.chan{font:8px ui-monospace,monospace;text-transform:uppercase;border:1px solid currentColor;padding:0 4px;border-radius:2px;flex:0 0 auto}
.chan.voice{color:var(--blu)}.chan.access{color:var(--amb)}.chan.hosting{color:var(--grn)}.chan.menu{color:var(--vio)}.chan.acp{color:var(--pnk)}
.big{font:700 22px/1 Georgia,serif;padding:14px}.big small{display:block;color:var(--mut);font:400 12px ui-sans-serif;margin-top:6px}
.share{border:1px solid #2c3a1f;background:#0f1a0a;border-radius:8px;padding:14px;color:var(--ink);font-size:13px;line-height:1.6}
.share b{color:var(--grn)}
.mut{color:var(--mut)}.grn{color:var(--grn)}.amb{color:var(--amb)}
.standby{padding:26px;color:var(--fnt);text-align:center;font-size:12px}
</style>
</head>
<body><main>
<div class="top">
  <div class="brand"><span class="dot"></span>dogwalk <small>your session</small></div>
  <div class="lens"><span class="ro">read-only</span> &middot; this call only <span id="stamp" class="mut"></span></div>
</div>
<div id="root"><div class="standby">loading&hellip;</div></div>
<div class="share">
  This link is safe to share when you report a problem. It shows only <b>this call</b> and nothing from any other call. It cannot see your future calls.
</div>
</main>
<script>
const el=(t,x,c)=>{const n=document.createElement(t);if(x!==undefined&&x!==null)n.textContent=String(x);if(c)n.className=c;return n};
const clock=(v)=>v?new Date(v*1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"}):"";
// "working"/"done" are Voice-Interaction conversational projections, not core state.
function callState(c){if(!c)return"finished";if(c.ended_at)return"finished";return"in progress"}
function render(d){
  const root=document.getElementById("root");root.replaceChildren();
  document.getElementById("stamp").textContent=" \\u00b7 updated "+clock(d.generated_at);
  const summary=el("div",undefined,"card");
  summary.append(el("h2","Your call"));
  const big=el("div",undefined,"big");big.textContent=callState(d.call);
  const sub=el("small");sub.textContent=(d.call&&d.call.started_at?"started "+new Date(d.call.started_at*1000).toLocaleString():"")+(d.sandbox_state?" \\u00b7 workspace "+d.sandbox_state:"");
  big.append(sub);summary.append(big);root.append(summary);

  const act=el("div",undefined,"card");act.append(el("h2","What happened this call"));
  const items=(d.activity||[]);
  if(!items.length){act.append(el("div","nothing recorded yet","standby"))}
  else items.forEach((a)=>{const r=el("div",undefined,"row");r.append(el("time",clock(a.ts)),el("span",a.source,"chan "+a.source),el("span",a.event,"ev"));act.append(r)});
  root.append(act);
}
async function load(){
  try{
    const res=await fetch(location.pathname.replace(/\\/$/,"")+"/data",{headers:{accept:"application/json"}});
    if(!res.ok)throw new Error("unavailable");
    render(await res.json());
  }catch(e){document.getElementById("root").replaceChildren(el("div","This view is unavailable.","standby"))}
}
load();
setInterval(load,10000);
</script>
</body></html>`;
