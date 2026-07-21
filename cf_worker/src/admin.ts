export const ADMIN_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dogwalk Mission Control</title>
<style>
:root{color-scheme:dark;--ink:#f4f0e6;--muted:#9c9a91;--line:#343731;--panel:#181b17;--green:#b9f36b;--amber:#f3b85c;--red:#ff796d;--blue:#79c7ff}
*{box-sizing:border-box}body{margin:0;background:#0c0e0c;color:var(--ink);font:15px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}
body:before{content:"";position:fixed;inset:0;pointer-events:none;background:linear-gradient(rgba(255,255,255,.018) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.018) 1px,transparent 1px);background-size:24px 24px}
main{position:relative;max-width:1280px;margin:auto;padding:32px 24px 64px}header{display:flex;align-items:end;justify-content:space-between;border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:24px}
h1{font:700 clamp(28px,5vw,58px)/.9 Georgia,serif;letter-spacing:-.04em;margin:0}.kicker{color:var(--green);text-transform:uppercase;letter-spacing:.16em;font-size:11px;margin-bottom:10px}.live{display:inline-block;width:7px;height:7px;margin-right:8px;border-radius:50%;background:var(--muted)}.live.on{background:var(--green);box-shadow:0 0 12px var(--green)}.stamp{color:var(--muted);text-align:right;font-size:12px}
.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);border:1px solid var(--line);margin-bottom:24px}.metric{background:var(--panel);padding:18px}.metric b{display:block;font:700 30px/1 Georgia,serif;margin-bottom:5px}.metric span{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.1em}
.privacy{display:flex;align-items:center;gap:10px;margin:-8px 0 18px;padding:10px 12px;border:1px solid var(--line);color:var(--muted);font-size:11px}.privacy input{accent-color:var(--amber)}.privacy strong{color:var(--ink);font-weight:600}.privacy.active{border-color:var(--amber);color:var(--amber)}.privacy.active strong{color:var(--amber)}
.grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(320px,.75fr);gap:24px}.section{min-width:0;border:1px solid var(--line);background:rgba(24,27,23,.9)}.section h2{font-size:12px;text-transform:uppercase;letter-spacing:.14em;margin:0;padding:13px 16px;border-bottom:1px solid var(--line);color:var(--muted)}
.calls-section{margin-bottom:24px}.call-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1px;background:var(--line)}.call-card{min-width:0;background:var(--panel)}.call-head{display:flex;justify-content:space-between;gap:12px;padding:14px 16px;border-bottom:1px solid var(--line)}.call-head b{color:var(--blue);font-size:13px}.call-head small{display:block;color:var(--muted);margin-top:3px}.call-status{text-align:right;color:var(--green);text-transform:uppercase;font-size:10px}.call-status:before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 10px var(--green);margin-right:6px}.call-activity{height:230px;overflow:auto;padding:6px 0}.activity-row{display:grid;grid-template-columns:58px 58px 1fr;gap:8px;align-items:start;padding:7px 12px;border-bottom:1px solid #292c27;font-size:10px}.activity-row time{color:var(--muted)}.channel{padding:1px 4px;border:1px solid currentColor;text-align:center;text-transform:uppercase;font-size:8px;letter-spacing:.04em}.channel.voice{color:var(--blue)}.channel.access{color:var(--amber)}.channel.hosting{color:var(--green)}.channel.menu{color:#d5a6ff}.channel.acp{color:#ff8bc6}.activity-row strong{font-weight:500;overflow-wrap:anywhere}.activity-detail{display:block;color:var(--muted);font-weight:400;margin-top:3px;white-space:pre-wrap;word-break:break-word}.standby{padding:30px 16px;color:var(--muted);text-align:center;letter-spacing:.08em;text-transform:uppercase}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:12px 14px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}td{font-size:12px}.phone{color:var(--blue)}.state{display:inline-block;padding:2px 7px;border:1px solid currentColor;border-radius:999px;font-size:10px;text-transform:uppercase}.started{color:var(--green)}.error,.build_failed,.conflict{color:var(--red)}.creating,.starting,.restoring,.provisioning{color:var(--amber)}
.audit{max-height:520px;overflow:auto}.event{display:grid;grid-template-columns:78px 1fr;gap:10px;padding:11px 14px;border-bottom:1px solid var(--line);font-size:11px}.event time{color:var(--muted)}.event strong{display:block;color:var(--ink);font-weight:600}.event small{color:var(--muted)}
.empty,.errorbox{padding:30px;color:var(--muted)}.errorbox{color:var(--red)}button{font:inherit;color:#0c0e0c;background:var(--green);border:0;padding:9px 13px;cursor:pointer}button:disabled{opacity:.5}
@media(max-width:820px){main{padding:22px 14px}.grid{grid-template-columns:minmax(0,1fr)}.summary{grid-template-columns:1fr 1fr}header{align-items:start;gap:20px}.stamp{max-width:140px}.table-wrap{max-width:100%;overflow:auto}table{min-width:560px}}
</style>
</head>
<body><main>
<header><div><div class="kicker"><span class="live" id="live"></span>Cloudflare / Daytona</div><h1>Mission Control</h1></div><div class="stamp"><button id="refresh">Reconnect stream</button><div id="generated">Waiting for signal</div></div></header>
<label class="privacy" id="privacy"><input type="checkbox" id="verbose"><strong>Reveal verbose telemetry</strong><span id="privacy-note">Demo-safe mode masks phone numbers, provider IDs, Call SIDs, and event details.</span></label>
<section class="summary" id="summary"></section><section class="section calls-section"><h2>Live calls / correlated activity</h2><div class="call-grid" id="live-calls"></div></section>
<div class="grid"><section class="section"><h2>Registered phones and sandbox assignments</h2><div class="table-wrap" id="registrations"></div></section><section class="section"><h2>Recent events</h2><div class="audit" id="audit"></div></section></div>
</main>
<script>
const q=(s)=>document.querySelector(s);const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
const when=(value)=>value?new Date(value*1000).toLocaleString():"never";const short=(value)=>value==="assigned"?value:value?String(value).slice(0,12)+"...":"-";
function metric(value,label){const n=el("div",undefined,"metric");n.append(el("b",String(value)),el("span",label));return n}
function render(data){
  q("#generated").textContent="LIVE / Updated "+when(data.generated_at);q("#live").classList.add("on");
  const warm=data.registrations.filter((r)=>r.state==="started").length;
  q("#summary").replaceChildren(metric(data.live_calls.length,"live calls"),metric(data.registrations.length,"registrations"),metric(warm,"started sandboxes"),metric(data.invites.total||0,"invite records"));
  const calls=q("#live-calls");calls.replaceChildren();if(!data.live_calls.length){calls.append(el("div","No calls on the wire","standby"));}else{data.live_calls.forEach((call)=>{
    const card=el("article",undefined,"call-card"),head=el("div",undefined,"call-head"),identity=el("div"),status=el("div",call.status,"call-status");const elapsed=Math.max(0,data.generated_at-call.started_at);identity.append(el("b",call.phone_number),el("small",call.call_sid.slice(0,16)+"... / "+Math.floor(elapsed/60)+"m "+elapsed%60+"s"));head.append(identity,status);card.append(head);const activity=el("div",undefined,"call-activity");
    call.activity.forEach((item)=>{const row=el("div",undefined,"activity-row"),description=el("strong",item.event);if(data.verbose&&item.detail){let detail=item.detail;try{detail=JSON.stringify(JSON.parse(item.detail),null,2)}catch{}description.append(el("small",detail,"activity-detail"))}row.append(el("time",new Date(item.ts*1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"})),el("span",item.source,"channel "+item.source),description);activity.append(row)});card.append(activity);calls.append(card)
  })}
  const host=q("#registrations");host.replaceChildren();if(!data.registrations.length){host.append(el("div","No registrations.","empty"));}else{
    const table=el("table"),head=el("tr");["Phone","Sandbox","State","Last seen"].forEach((x)=>head.append(el("th",x)));const thead=el("thead");thead.append(head);table.append(thead);const body=el("tbody");
    data.registrations.forEach((r)=>{const row=el("tr");row.append(el("td",r.phone_number,"phone"),el("td",short(r.provider_id)));const state=el("span",r.state||"unassigned","state "+(r.state||""));const stateCell=el("td");stateCell.append(state);if(r.error)stateCell.append(el("div",r.error,"error"));row.append(stateCell,el("td",when(r.last_seen_at)));body.append(row)});table.append(body);host.append(table);
  }
  const audit=q("#audit");audit.replaceChildren();data.audit.forEach((a)=>{const row=el("div",undefined,"event");row.append(el("time",new Date(a.ts*1000).toLocaleTimeString()));const detail=el("div");detail.append(el("strong",a.event),el("small",(a.phone_number||"-")+" / "+(a.call_sid||"-")));row.append(detail);audit.append(row)});
}
let source;
function connect(){
  if(source)source.close();q("#live").classList.remove("on");q("#generated").textContent="Connecting telemetry";const verbose=q("#verbose").checked;q("#privacy").classList.toggle("active",verbose);q("#privacy-note").textContent=verbose?"Verbose mode may expose identifiers and event details during screen sharing.":"Demo-safe mode masks phone numbers, provider IDs, Call SIDs, and event details.";
  source=new EventSource("/admin/api/events"+(verbose?"?verbose=1":""));
  source.addEventListener("state",(event)=>{try{render(JSON.parse(event.data))}catch(error){q("#generated").textContent="Invalid telemetry"}});
  source.addEventListener("stream-error",(event)=>{try{const data=JSON.parse(event.data);q("#generated").textContent="Stream error: "+data.message}catch{q("#generated").textContent="Stream error"}});
  source.onerror=()=>{q("#live").classList.remove("on");q("#generated").textContent="Reconnecting telemetry"};
}
q("#refresh").addEventListener("click",connect);q("#verbose").addEventListener("change",connect);connect();
</script></body></html>`;
