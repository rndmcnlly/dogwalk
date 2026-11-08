// Paired Browser page (DOMAIN.md), served at GET /pair: listens for the Pairing Code the call
// plays as keypad tones, claims it, waits for the caller to accept, then shows live Managed
// Session status, a timeline of the call, a read-only Workspace file viewer, Human Takeover
// launchers, and the Whiteboard (boards the walker draws for the caller to read or answer) over /pair/ws.
// Media files (images, PDF, audio, video, downloads) come from GET /pair/file as blob URLs.
//
// String.raw keeps every backslash in the page script literal, so regexes and "\n" escapes
// reach the browser as written. The page must therefore contain no backtick and no dollar-brace
// sequence anywhere (a backtick is written "\x60" in the script). The decoder core between the
// CORE markers is ported from prototypes/acoustic_pairing.html and must match src/pairing.ts:
// 10 data bytes, 6 parity bytes, 32 symbols back to back with no gap, one copy, at a symbol rate
// the Voice Agent picks from BAUD_RATES. The page is not told the rate: it tries each one whose
// frame length fits the heard frame and keeps the one that Reed-Solomon confirms.

export const PAIR_HTML = String.raw`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dogwalk: Paired Browser</title>
<style>
/* Palette in sync with src/landing.ts (light and dark); the rest are this page's own states. */
:root{
  color-scheme:light dark;
  --paper:#f4efe3;--card:#fbf8f1;--ink:#1b2a24;--body:#33413a;--muted:#56625a;--faint:#8a9188;
  --line:#ddd4c3;--rule:#c6bca7;--leash:#b2441f;--leash-soft:#f1dccf;--grass:#2f5d4a;
  --accent:var(--leash);--accent-ink:#fff;
  --ok:#2e7d4f;--bad:#b3261e;--warn:#a86a00;--fix:#d68910;--info:#2f6690;
  --highlight:var(--leash-soft);--code-bg:#ece5d5;
  --role-caller:var(--leash);--role-voice:var(--grass);--role-orchestrator:#6a4c93;--role-system:var(--muted);
  --agent-s:55%;--agent-l:33%;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,"Cascadia Code",monospace;
  --radius:8px;
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#111815;--card:#18211d;--ink:#ece6d7;--body:#cfd3c8;--muted:#a3ada3;--faint:#6f7a72;
    --line:#26322d;--rule:#34423b;--leash:#ec8a62;--leash-soft:#3a2219;--grass:#8cc2a6;
    --accent-ink:#1b120d;
    --ok:#74c69d;--bad:#ff8f85;--warn:#e3b45a;--fix:#e0a040;--info:#8cb9e6;
    --code-bg:#202b26;--role-orchestrator:#bba6e8;
    --agent-s:60%;--agent-l:72%;
  }
}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
a{color:inherit}
[hidden]{display:none!important}
button:focus-visible,a:focus-visible,summary:focus-visible,input:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.offscreen{position:fixed;left:-9999px;top:0}
.page{max-width:1280px;margin:0 auto;padding:0 clamp(14px,4vw,40px) 48px}
header.top{display:flex;align-items:center;gap:8px 14px;padding:14px 0;border-bottom:1px solid var(--rule);flex-wrap:wrap}
.mark{font-weight:700;letter-spacing:-.02em;text-decoration:none;font-size:17px}
.mark b{color:var(--accent)}
.crumb{font:500 12px/1.5 var(--mono);color:var(--muted);letter-spacing:.06em;text-transform:uppercase}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-left:auto}
.chip{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;border:1px solid var(--line);background:var(--card);font:600 12px/1.3 var(--sans);white-space:nowrap}
.chip::before{content:"";flex:none;width:.65em;height:.65em;border-radius:50%;background:var(--faint)}
.chip.ok::before{background:var(--ok)}
.chip.warn::before{background:var(--warn)}
.chip.off::before{background:transparent;box-shadow:inset 0 0 0 1.5px var(--muted)}
h2{margin:22px 0 10px;font:600 12px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.status{margin:18px 0 6px;font:600 clamp(19px,3vw,24px)/1.3 var(--serif)}
.status.ok{color:var(--ok)}
.status.bad{color:var(--bad)}
.small{font-size:13px}
.muted{color:var(--muted)}
.mono{font-family:var(--mono)}
button{font:inherit;padding:7px 14px;border-radius:var(--radius);border:1px solid var(--rule);background:var(--card);color:var(--ink);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink);font-weight:600}
button:disabled{opacity:.5;cursor:default}
button.link{border:0;background:none;padding:0;color:var(--info);text-decoration:underline;text-underline-offset:2px;cursor:pointer;text-align:left;word-break:break-all}
button.mini{padding:1px 8px;font-size:12px;line-height:1.5;flex:none}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:12px 0}
.steps{margin:6px 0 14px;padding-left:1.4em;max-width:62ch}
.steps li{margin:3px 0}
.meter{height:8px;background:var(--line);border-radius:4px;overflow:hidden;max-width:520px}
.meter>div{height:100%;width:0;background:var(--accent);transition:width 60ms linear}
.stream{font-family:var(--mono);font-size:15px;letter-spacing:.1em;word-break:break-all;min-height:1.5em}
.frame{border-top:1px solid var(--line);padding:6px 0;font-family:var(--mono);font-size:13px}
.symrow{white-space:nowrap;overflow-x:auto}
.symrow .lbl{display:inline-block;width:3.2em;color:var(--muted)}
.sym{display:inline-block;width:1.1ch;text-align:center}
.sym.erased{color:var(--bad)}
.sym.wrong{background:color-mix(in srgb,var(--bad) 28%,transparent)}
.sym.fixed{background:color-mix(in srgb,var(--fix) 35%,transparent)}
.sep{display:inline-block;width:1.4ch;text-align:center;color:var(--faint)}
.verdict{font-family:var(--sans);font-size:13px}
.verdict.ok{color:var(--ok)}
.verdict.bad{color:var(--bad)}
details summary{cursor:pointer}
#log{max-height:180px;overflow:auto;white-space:pre-wrap;margin-top:6px}
.diag{margin-top:28px}
.box{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px}
.pairing{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center;justify-content:space-between}
.pairing .who{font-weight:600}
.prefs{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center;font-size:13px}
.prefs label{display:inline-flex;gap:6px;align-items:center;min-height:32px;cursor:pointer}
.prefs input{width:18px;height:18px;accent-color:var(--accent)}
.permissions{background:var(--highlight);border:1px solid var(--accent);border-radius:var(--radius);padding:10px 14px;margin-top:14px}
.permissions h2{margin:0 0 6px;color:var(--accent)}
.permissions ul{margin:0;padding-left:1.2em}

/* Tabs: Live, Files, Hands. Live shows sessions and the call timeline side by side on a wide
   screen; on a narrow one a segmented control picks one of the two columns. */
.tabs{position:sticky;top:0;z-index:5;display:flex;gap:2px;background:var(--paper);border-bottom:1px solid var(--rule);margin:14px 0 12px;overflow-x:auto}
.tab{border:0;border-bottom:3px solid transparent;border-radius:0;background:none;padding:10px 14px;min-height:46px;font-weight:600;color:var(--muted);white-space:nowrap}
.tab[aria-selected=true]{color:var(--ink);border-bottom-color:var(--accent)}
.tab .key{font:500 11px/1 var(--mono);color:var(--faint);margin-left:6px}
@media (hover:none){.tab .key{display:none}}
.count{display:inline-block;min-width:1.6em;margin-left:6px;padding:2px 6px;border-radius:999px;background:var(--accent);color:var(--accent-ink);font:700 11px/1.2 var(--sans);text-align:center}
.panel{display:none;min-width:0}
.panel>h2:first-child,.live-col>h2:first-child{margin-top:4px}
.panes[data-tab=live] #panelLive,.panes[data-tab=files] #panelFiles,.panes[data-tab=hands] #panelHands{display:block}
.segmented{display:inline-flex;margin:0 0 10px;border:1px solid var(--rule);border-radius:var(--radius);overflow:hidden}
.segmented button{border:0;border-radius:0;background:var(--card);padding:6px 14px;min-height:38px;font-weight:600;color:var(--muted)}
.segmented button+button{border-left:1px solid var(--rule)}
.segmented button[aria-pressed=true]{background:var(--accent);color:var(--accent-ink)}
.segmented button[aria-pressed=true] .count{background:var(--accent-ink);color:var(--accent)}
.live-col{min-width:0}
.live-cols[data-view=sessions] #liveFeed,.live-cols[data-view=timeline] #liveSessions{display:none}
@media (min-width:1100px){
  #liveSwitch{display:none}
  .live-cols{display:grid;grid-template-columns:minmax(0,.85fr) minmax(0,1.15fr);gap:0 28px;align-items:start}
  .live-cols[data-view] #liveFeed,.live-cols[data-view] #liveSessions{display:block}
  #liveFeed{position:sticky;top:58px}
}
.explain{margin:-4px 0 10px}

/* What the walker shows: a toast for navigations, a banner for tabs only a click may open */
.toast{position:fixed;left:50%;bottom:20px;transform:translateX(-50%);z-index:20;max-width:min(560px,calc(100vw - 28px));padding:10px 16px;border-radius:var(--radius);background:var(--ink);color:var(--paper);box-shadow:0 4px 16px rgb(0 0 0 / .3);font-size:14px}
.toast .note{display:block;opacity:.8;font-size:13px}
.banner{position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:20;width:min(720px,calc(100vw - 20px));display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;padding:12px 14px;border:2px solid var(--accent);border-radius:var(--radius);background:var(--highlight);box-shadow:0 6px 20px rgb(0 0 0 / .3)}
.banner .text{flex:1 1 16em;min-width:0}
.banner .note{display:block;font-size:13px;color:var(--body)}
.banner .host{display:block;font:700 19px/1.3 var(--mono);color:var(--ink);overflow-wrap:anywhere}
.card.flash{animation:flash 2.4s ease-out}
@keyframes flash{0%,40%{box-shadow:0 0 0 4px var(--accent);background:var(--highlight)}100%{box-shadow:0 0 0 0 transparent}}

/* Sessions */
.card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:var(--radius);padding:12px 14px;margin:0 0 12px}
.card.turn-in_progress,.card.turn-queued{border-left-color:var(--info)}
.card.turn-failed{border-left-color:var(--bad)}
.card.turn-stopped{border-left-color:var(--ok)}
.card-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 10px}
.alias{margin:0;font:600 22px/1.2 var(--serif);display:inline-flex;align-items:center;gap:8px}
.alias::before{content:"";width:.5em;height:.5em;border-radius:50%;background:hsl(var(--h,200) var(--agent-s) var(--agent-l))}
.badge{font:600 11px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;padding:4px 7px;border-radius:999px;background:var(--line);color:var(--ink)}
.badge.in_progress,.badge.queued{background:var(--info);color:var(--paper)}
.badge.failed{background:var(--bad);color:var(--paper)}
.badge.stopped{background:var(--ok);color:var(--paper)}
.badge.follow{background:var(--accent);color:var(--accent-ink)}
.card p{margin:6px 0}
.card .title{font-weight:600}
.pathline{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:2px}
.plan,.tools{list-style:none;margin:6px 0;padding:0}
.plan li{display:flex;gap:8px;align-items:baseline}
.plan .box-mark{flex:none;width:.9em;height:.9em;border:1.5px solid var(--muted);border-radius:3px;transform:translateY(.1em)}
.plan .completed .box-mark{background:var(--ok);border-color:var(--ok)}
.plan .in_progress .box-mark{background:linear-gradient(90deg,var(--info) 50%,transparent 50%);border-color:var(--info)}
.plan .completed span:last-child{color:var(--muted);text-decoration:line-through}
.tools li{padding:3px 0;border-top:1px dashed var(--line);font-size:14px}
.tools .kind{font:600 11px/1 var(--mono);color:var(--muted);text-transform:uppercase;margin-right:6px}
.tools .paths{display:flex;flex-wrap:wrap;gap:2px 10px;font:12.5px/1.4 var(--mono)}
.failure{color:var(--bad);font-weight:600}
.remark{font-style:italic}
.report{margin-top:6px}
.closed-group>summary{color:var(--muted);margin:8px 0}

/* Call timeline: one color per role, always with a text label beside it */
.feed-bar{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 8px}
.filter.f-caller{--c:var(--role-caller)}.filter.f-voice{--c:var(--role-voice)}.filter.f-orchestrator{--c:var(--role-orchestrator)}
.filter{--c:var(--role-system);display:inline-flex;align-items:center;gap:6px;padding:5px 11px;border-radius:999px;font:600 12.5px/1.2 var(--sans);min-height:32px}
.filter::before{content:"";width:.7em;height:.7em;border-radius:50%;background:var(--c)}
.filter[aria-pressed=false]{color:var(--faint);border-style:dashed}
.filter[aria-pressed=false]::before{background:transparent;box-shadow:inset 0 0 0 1.5px var(--c)}
.filter.f-agent::before{background:conic-gradient(hsl(20 var(--agent-s) var(--agent-l)) 0 33%,hsl(140 var(--agent-s) var(--agent-l)) 0 66%,hsl(260 var(--agent-s) var(--agent-l)) 0)}
.feed-wrap{position:relative}
.feed-list{list-style:none;margin:0;padding:10px;height:clamp(320px,calc(100dvh - 240px),1000px);overflow:auto;overscroll-behavior:contain;background:var(--card);border:1px solid var(--line);border-radius:var(--radius)}
.feed-empty{position:absolute;inset:40px 20px auto;text-align:center;pointer-events:none}
.fe{--c:var(--role-system);margin:0 0 8px;padding:6px 10px;border-left:3px solid var(--c);border-radius:6px;max-width:94%;overflow-wrap:anywhere}
.fe.f-caller{--c:var(--role-caller);margin-left:auto;border-left:0;border-right:3px solid var(--c);background:color-mix(in srgb,var(--c) 11%,var(--card))}
.fe.f-voice{--c:var(--role-voice);background:color-mix(in srgb,var(--c) 9%,var(--card))}
.fe.f-caller .fe-text,.fe.f-voice .fe-text{font-size:16.5px;line-height:1.45}
.fe.f-agent{--c:hsl(var(--h,200) var(--agent-s) var(--agent-l))}
.fe.f-orchestrator{--c:var(--role-orchestrator);color:var(--body)}
.fe.f-system,.fe.lane-tool{padding:1px 10px;margin-bottom:4px;font-size:12.5px;color:var(--muted)}
.fe.f-system{border-left-style:dotted}
.fe-head,.fe-line,.fe-fold summary{display:flex;flex-wrap:wrap;gap:0 8px;align-items:baseline}
.fe-who{font:700 12px/1.4 var(--sans);color:var(--c);letter-spacing:.02em}
.fe-lane{font:500 10.5px/1.4 var(--mono);color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.fe time{margin-left:auto;font:11px/1.4 var(--mono);color:var(--faint);white-space:nowrap}
.fe-line .fe-inline{flex:1 1 12em;min-width:0}
.fe-text{white-space:pre-wrap}
.fe-fold summary{list-style:none;color:var(--muted)}
.fe-fold summary::-webkit-details-marker{display:none}
.fe-fold summary::after{content:"show";font:11px/1.4 var(--mono);color:var(--info);text-decoration:underline}
.fe-fold[open] summary::after{content:"hide"}
.fe-peek{flex-basis:100%;order:9;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fe-fold[open] .fe-peek{display:none}
.fe-folded{font-size:13px;max-height:40vh;overflow:auto;margin-top:4px}
.fe .md{border:0;background:none;padding:0;max-height:none;overflow:visible}
.clamped{max-height:9.5em;overflow:hidden;-webkit-mask-image:linear-gradient(#000 65%,transparent);mask-image:linear-gradient(#000 65%,transparent)}
.fe-more{font-size:12.5px}
.feed-list.hide-caller .f-caller,.feed-list.hide-voice .f-voice,.feed-list.hide-agent .f-agent,
.feed-list.hide-orchestrator .f-orchestrator,.feed-list.hide-system .f-system{display:none}
.pill{position:absolute;left:50%;bottom:14px;transform:translateX(-50%);border-radius:999px;background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:600;box-shadow:0 2px 8px rgb(0 0 0 / .25)}

/* Files */
.files-cols{display:block}
@media (min-width:1100px){
  .files-cols{display:grid;grid-template-columns:minmax(0,.8fr) minmax(0,1.2fr);gap:0 28px;align-items:start}
  .files-cols .viewer{position:sticky;top:58px;margin-top:4px}
}
.recent{list-style:none;margin:0;padding:0}
.recent li{display:flex;gap:8px;align-items:baseline;padding:4px 0;border-top:1px dashed var(--line);font:13px/1.5 var(--mono)}
.recent li:first-child{border-top:0}
.recent .dir{color:var(--faint);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1 1 auto;min-width:0}
.who-tag{flex:none;font:600 11px/1.4 var(--sans);color:hsl(var(--h,200) var(--agent-s) var(--agent-l))}
.crumbs{display:flex;flex-wrap:wrap;gap:2px;align-items:center;font:13px/1.6 var(--mono)}
.crumbs .slash{color:var(--faint)}
.entries{list-style:none;margin:8px 0;padding:0;max-height:360px;overflow:auto;border:1px solid var(--line);border-radius:var(--radius);background:var(--card)}
/* Name, size, modified: the name takes the room; on a narrow list the size drops under it and the date goes. */
.entries{container-type:inline-size}
.entries li{display:grid;grid-template-columns:minmax(0,1fr) 7.5ch 6.5ch;gap:0 10px;align-items:baseline;padding:5px 10px;border-top:1px solid var(--line);font:13px/1.5 var(--mono)}
.entries li.muted{display:block}
.entries li:first-child{border-top:0}
.entries .name{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;word-break:normal}
.entries .meta{color:var(--faint);white-space:nowrap;text-align:right;font-size:12px}
@container (max-width:400px){
  .entries li{grid-template-columns:minmax(0,1fr);padding:4px 10px}
  .entries .when{display:none}
  .entries .size{text-align:left;font-size:11px;line-height:1.3}
  .entries .size:empty{display:none}
}
.viewer{margin-top:10px}
.viewer-head{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px 10px;font:12.5px/1.4 var(--mono);color:var(--muted);margin-bottom:6px;word-break:break-all}
.viewer-actions{display:flex;gap:6px;flex:none}
#viewerNote{margin:0 0 6px}
#viewerNote:empty{display:none}
.md{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:4px 14px;max-height:75vh;overflow:auto;word-wrap:break-word}
.md h1,.md h2,.md h3,.md h4,.md h5,.md h6{font:600 1.05em/1.3 var(--sans);text-transform:none;letter-spacing:0;color:var(--ink);margin:14px 0 6px}
.md h1{font-size:1.35em}.md h2{font-size:1.2em}
.md pre{white-space:pre-wrap;background:var(--code-bg);padding:8px 10px;border-radius:6px;font:12.5px/1.45 var(--mono)}
.md code{font-family:var(--mono);font-size:.92em;background:var(--code-bg);padding:0 3px;border-radius:3px}
.md pre code{background:none;padding:0}
.md a{color:var(--info)}
.md ul,.md ol{padding-left:1.5em;margin:6px 0}
.md li{margin:2px 0}
.md li>ul,.md li>ol{margin:2px 0}
.md li>p{margin:4px 0}
.md blockquote{margin:8px 0;padding:0 0 0 12px;border-left:3px solid var(--rule);color:var(--muted)}
.md hr{border:0;border-top:1px solid var(--line);margin:12px 0}
.md-code{position:relative}
.md-code pre{padding-top:22px}
.md-lang{position:absolute;top:4px;right:8px;font:500 10.5px/1.4 var(--mono);color:var(--faint);letter-spacing:.06em;text-transform:uppercase}
.md-table{overflow-x:auto;margin:8px 0;border:1px solid var(--line);border-radius:var(--radius);background:var(--card)}
.md-table table{border-collapse:collapse;font-size:.92em;line-height:1.4;min-width:100%}
.md-table th,.md-table td{padding:4px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;min-width:6ch}
.md-table th{background:var(--code-bg);color:var(--ink);font-weight:600;white-space:nowrap}
.md-table tbody tr:last-child td{border-bottom:0}
.md-table td code,.md-table th code{white-space:nowrap}
pre.code{counter-reset:line;margin:0;font:12.5px/1.5 var(--mono);background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:8px 10px 8px 4px;max-height:75vh;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}
pre.code .ln{display:block;padding-left:6ch;text-indent:-6ch}
pre.code .ln::before{counter-increment:line;content:counter(line);display:inline-block;width:5ch;margin-right:1ch;text-align:right;text-indent:0;color:var(--faint);-webkit-user-select:none;user-select:none}
.img-wrap{border:1px solid var(--line);border-radius:var(--radius);overflow:auto;max-height:75vh;text-align:center;cursor:zoom-in;background:repeating-conic-gradient(var(--line) 0 25%,var(--card) 0 50%) 0 0/16px 16px}
.img-wrap img{display:inline-block;max-width:100%;max-height:73vh;vertical-align:middle}
.img-wrap.actual{cursor:zoom-out;text-align:left}
.img-wrap.actual img{max-width:none;max-height:none}
iframe.pdf{display:block;width:100%;height:75vh;border:1px solid var(--line);border-radius:var(--radius);background:#fff}
audio{width:100%}
video{display:block;width:100%;max-height:75vh;background:#000;border-radius:var(--radius)}
.table-wrap{max-height:75vh;overflow:auto;border:1px solid var(--line);border-radius:var(--radius);background:var(--card)}
.table-wrap table{border-collapse:collapse;font:12.5px/1.4 var(--mono)}
.table-wrap th,.table-wrap td{padding:4px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;max-width:36ch;overflow:hidden;text-overflow:ellipsis}
.table-wrap th{position:sticky;top:0;background:var(--code-bg)}
.html-bar{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;margin:0 0 6px}
.html-bar .small{flex:1 1 18em;margin:0}
.html-stage{background:#fff;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
.html-stage iframe{display:block;width:100%;height:clamp(360px,calc(100dvh - 210px),2400px);border:0;background:#fff}
.html-stage:fullscreen{border:0;border-radius:0}
.html-stage:fullscreen iframe{height:100%}
.html-stage:-webkit-full-screen{width:100%;height:100%;border:0;border-radius:0}
.html-stage:-webkit-full-screen iframe{height:100%}

/* Whiteboard (on Hands, or in a tab of its own): boards the walker draws in openui-lang. Tones and chart series take the page's palette. */
.whiteboard{margin:18px 0 0;--wb-1:var(--info);--wb-2:var(--leash);--wb-3:var(--grass);--wb-4:var(--role-orchestrator);--wb-5:var(--warn);--wb-6:var(--bad)}
#wbAway{margin-top:18px}
.wb-board{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:var(--radius);padding:12px 14px;min-width:0}
.wb-board.answered{border-left-color:var(--ok)}
.wb-board.flash{animation:flash 2.4s ease-out}
.wb-head{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;margin:0 0 10px}
.wb-head .wb-note{font:600 18px/1.3 var(--serif)}
.wb-head time{margin-left:auto;font:11px/1.4 var(--mono);color:var(--faint)}
.wb-stack{display:flex;flex-direction:column;gap:12px;min-width:0}
.wb-row{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:flex-start}
.wb-row>*{flex:1 1 12em;min-width:0}
.wb-row>button,.wb-row>.wb-badge{flex:0 0 auto}
.wb-card{display:flex;flex-direction:column;gap:10px;min-width:0;border:1px solid var(--line);border-radius:var(--radius);padding:10px 12px;background:var(--paper)}
.wb-card[data-decision]{border-color:var(--accent)}
.wb-card-head{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:baseline}
.wb-card-head:has(>.wb-decision[hidden]:only-child){display:none}
.wb-card-head h3{margin:0;font:600 15.5px/1.35 var(--sans)}
.wb-decision{font:600 11px/1 var(--mono);letter-spacing:.05em;text-transform:uppercase;padding:4px 7px;border-radius:999px;background:var(--accent);color:var(--accent-ink)}
.md.wb-md{border:0;background:none;padding:0;max-height:none;overflow:visible}
.wb-md>:first-child{margin-top:0}
.wb-md>:last-child{margin-bottom:0}
.tone-neutral{--t:var(--muted)}.tone-info{--t:var(--info)}.tone-success{--t:var(--ok)}.tone-warning{--t:var(--warn)}.tone-error{--t:var(--bad)}
.wb-badge{display:inline-block;align-self:flex-start;font:600 11px/1 var(--mono);letter-spacing:.05em;text-transform:uppercase;padding:4px 8px;border-radius:999px;border:1px solid var(--t);color:var(--t)}
.wb-callout{border-left:4px solid var(--t);background:color-mix(in srgb,var(--t) 10%,var(--card));border-radius:6px;padding:8px 12px}
.wb-callout>strong{display:block;margin-bottom:2px}
.wb-kv{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 14px;margin:0}
.wb-kv dt{color:var(--muted)}
.wb-kv dd{margin:0;overflow-wrap:anywhere}
.wb-board .md-table{margin:0}
.wb-board .num{text-align:right;font-variant-numeric:tabular-nums}
.wb-stat{display:flex;flex-direction:column;gap:2px;padding:8px 12px;border:1px solid var(--line);border-radius:var(--radius);background:var(--card)}
.wb-stat .label{font-size:13px;color:var(--muted)}
.wb-stat .value{font:600 26px/1.15 var(--serif);font-variant-numeric:tabular-nums}
.wb-stat .delta{font-size:13px;font-weight:600;color:var(--t)}
.wb-progress .top{display:flex;justify-content:space-between;gap:10px;font-size:14px;margin-bottom:4px}
.wb-progress .bar{height:10px;border-radius:5px;background:var(--line);overflow:hidden}
.wb-progress .bar>div{height:100%;background:var(--accent)}
.wb-steps{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.wb-steps li{display:flex;gap:8px;align-items:flex-start;min-width:0}
.wb-steps li>div{min-width:0;flex:1}
.wb-steps .title{font-weight:600}
.wb-steps .mark{flex:none;width:1.05em;height:1.05em;margin-top:.22em;border-radius:50%;border:2px solid var(--muted)}
.wb-steps .done .mark{background:var(--ok);border-color:var(--ok)}
.wb-steps .current .mark{border-color:var(--info);background:radial-gradient(var(--info) 38%,transparent 44%)}
.wb-steps .blocked .mark{border-color:var(--bad);border-radius:3px;background:repeating-linear-gradient(45deg,var(--bad) 0 2px,transparent 2px 5px)}
.wb-steps .done .title{color:var(--muted)}
.wb-steps.row{flex-direction:row;flex-wrap:wrap;gap:8px 6px}
.wb-steps.row li{flex:1 1 10em}
.wb-steps.row li+li::before{content:"\2192";flex:none;color:var(--faint);font-size:18px;line-height:1.2}
.wb-chart{margin:0;min-width:0}
.wb-chart svg{display:block;width:100%;height:auto}
.wb-chart text{font:11px var(--sans);fill:var(--muted)}
.wb-chart text.val{fill:var(--ink);font-weight:600}
.wb-chart text.inside{fill:#fff;font-weight:600}
.wb-chart .grid{stroke:var(--line)}
.wb-chart .axis{stroke:var(--rule)}
.wb-legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:13px;margin-top:4px}
.wb-key::before{content:"";display:inline-block;width:.8em;height:.8em;margin-right:6px;border-radius:3px;background:var(--c);vertical-align:-.05em}
.wb-numbers>summary{font-size:12.5px;color:var(--muted);margin-top:4px}
.wb-numbers .md-table{margin-top:4px}
.wb-choice{border:0;margin:0;padding:0;min-width:0}
.wb-choice legend{font-weight:600;padding:0;margin-bottom:6px}
.wb-options{display:flex;flex-direction:column;gap:6px}
.wb-option{display:flex;gap:10px;align-items:flex-start;min-height:44px;padding:8px 10px;border:1px solid var(--line);border-radius:var(--radius);background:var(--card);cursor:pointer}
.wb-option:has(input:checked){border-color:var(--accent);background:var(--highlight)}
.wb-option input{flex:none;width:18px;height:18px;margin:2px 0 0;accent-color:var(--accent)}
.wb-opt-desc{display:block;font-size:13px;color:var(--muted)}
.wb-field{display:flex;flex-direction:column;gap:4px;min-width:0}
.wb-field>span:first-child{font-weight:600}
.wb-input{width:100%;min-height:40px;padding:8px 10px;font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--rule);border-radius:var(--radius)}
textarea.wb-input{resize:vertical;min-height:4.5em}
.wb-input:focus-visible,.wb-option input:focus-visible,.wb-slider input:focus-visible{outline:3px solid var(--accent);outline-offset:1px}
.wb-slider{display:flex;gap:10px;align-items:center}
.wb-slider input{flex:1;min-width:0;accent-color:var(--accent)}
.wb-slider output{min-width:5ch;text-align:right;font:600 14px/1 var(--mono)}
.wb-button{min-height:40px}
.wb-button[aria-pressed=true]{box-shadow:0 0 0 2px var(--card),0 0 0 4px var(--ink)}
.wb-button.dim{opacity:.55}
.wb-actions{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;margin-top:12px;padding-top:10px;border-top:1px dashed var(--line)}
.wb-actions .wb-field{flex-basis:100%}
.wb-actions .spacer{flex:1}
.wb-answered{margin-top:12px;padding:8px 12px;border-left:4px solid var(--ok);border-radius:6px;background:color-mix(in srgb,var(--ok) 10%,var(--card))}
.wb-answered>strong{display:block;margin-bottom:4px}
.wb-errors ul{margin:4px 0;padding-left:1.2em;font:12.5px/1.45 var(--mono);overflow-wrap:anywhere}
.wb-errors p{margin:4px 0 0}
.wb-board>.wb-errors{margin-bottom:12px}
.wb-tabs .segmented{margin-bottom:8px;max-width:100%;overflow-x:auto}
.wb-details>summary{font-weight:600}
.wb-details>.wb-stack{margin-top:8px}
.wb-older{margin-top:10px}
.wb-older>summary{color:var(--muted)}
.wb-past{margin:6px 0 0 12px}
.wb-past>summary{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline}
.wb-past>summary time{font:11px/1.4 var(--mono);color:var(--faint)}
.wb-past .wb-head{display:none}
.wb-past>.wb-board{margin-top:6px}

/* Hands */
.launchers{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));max-width:720px}
.launchers .box p{margin:6px 0 10px}

@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{transition:none!important;animation:none!important;scroll-behavior:auto!important}
}
</style>
</head>
<body>
<div class="page">
<header class="top">
  <a class="mark" href="/"><b>dog</b>walk.tools</a>
  <span class="crumb">Paired Browser</span>
  <div id="chips" class="chips" hidden>
    <span id="chipCall" class="chip"></span>
    <span id="chipAgents" class="chip"></span>
    <span id="chipExpiry" class="chip"></span>
  </div>
</header>
<main>
<p id="status" class="status" role="status" aria-live="polite"></p>

<section id="pairScreen" hidden>
  <ol class="steps">
    <li>Use a different device from the phone in the call (a laptop or tablet). Phones give the browser silence while a call holds the microphone.</li>
    <li>Put the phone on speaker, near this device.</li>
    <li>Press Listen and allow the microphone.</li>
    <li>In the call, ask the walker to pair a browser, and say when this page is listening. After the walker says "Pairing tones", the call plays a few seconds of keypad tones.</li>
    <li>The call describes this browser: press one to accept it (star refuses).</li>
  </ol>
  <p class="small muted">If the page hears the tones but cannot decode them, ask the walker to send the tones slower.</p>
  <div class="row">
    <button id="listen" class="primary">Listen</button>
    <button id="stop" disabled>Stop</button>
  </div>
  <div class="meter"><div id="level"></div></div>
  <div id="levelText" class="mono small muted">&nbsp;</div>
  <p class="small muted">Tones as heard while a frame arrives, one mark per tone (&middot; is an unusually long silence); the rate is worked out when the frame ends:</p>
  <div id="live" class="stream"></div>
  <div id="frames"></div>
</section>

<section id="pairedScreen" hidden>
  <div id="showBanner" class="banner" role="alert" hidden>
    <span class="text"><b id="bannerText"></b><span id="bannerNote" class="note"></span></span>
    <button id="bannerOpen" class="primary"></button>
    <button id="bannerDismiss">Dismiss</button>
  </div>
  <div id="toast" class="toast" role="status" aria-live="polite" hidden></div>
  <div class="box pairing">
    <div>
      <div class="who" id="who"></div>
      <div class="small muted" id="expiry"></div>
      <div class="small muted" id="conn"></div>
    </div>
    <div class="prefs">
      <label><input type="checkbox" id="wakeToggle"> Keep screen on</label>
      <label><input type="checkbox" id="alertToggle"> Alerts when this tab is hidden</label>
      <button id="unpair">Unpair</button>
    </div>
    <div id="prefNote" class="small muted" role="status"></div>
  </div>
  <div id="permissions" class="permissions" hidden></div>

  <nav id="tabs" class="tabs" role="tablist" aria-label="Views">
    <button id="tab-live" class="tab" role="tab" aria-controls="panelLive">Live<span id="feedCount" class="count" hidden></span><span class="key">1</span></button>
    <button id="tab-files" class="tab" role="tab" aria-controls="panelFiles">Files<span class="key">2</span></button>
    <button id="tab-hands" class="tab" role="tab" aria-controls="panelHands">Hands<span id="boardCount" class="count" title="A new whiteboard" hidden>board</span><span class="key">3</span></button>
  </nav>

  <div id="panes" class="panes" data-tab="live">
    <section id="panelLive" class="panel" role="tabpanel" aria-labelledby="tab-live">
      <div id="liveSwitch" class="segmented" role="group" aria-label="Show">
        <button id="seg-sessions" aria-pressed="false">Sessions</button>
        <button id="seg-timeline" aria-pressed="false">Timeline<span id="feedCountSeg" class="count" hidden></span></button>
      </div>
      <div id="liveCols" class="live-cols" data-view="timeline">
        <div id="liveSessions" class="live-col">
          <h2>Sessions</h2>
          <p class="explain small muted">Where each session stands</p>
          <p id="sandbox" class="small muted"></p>
          <div id="sessions"></div>
          <div id="closed"></div>
        </div>
        <div id="liveFeed" class="live-col">
          <h2>Call timeline</h2>
          <p class="explain small muted">What was said and done, as it happens</p>
          <div id="feedFilters" class="feed-bar" role="group" aria-label="Show or hide"></div>
          <div class="feed-wrap">
            <ol id="feedList" class="feed-list" aria-live="off"></ol>
            <p id="feedEmpty" class="feed-empty muted small">Nothing yet. What is said and done in the call appears here as it happens.</p>
            <button id="feedPill" class="pill" hidden>New messages below</button>
          </div>
        </div>
      </div>
    </section>

    <section id="panelFiles" class="panel" role="tabpanel" aria-labelledby="tab-files">
      <div class="files-cols">
        <div>
          <h2>Recently touched</h2>
          <ul id="recent" class="recent"></ul>
          <h2>Browse</h2>
          <nav id="crumbs" class="crumbs" aria-label="Path"></nav>
          <ul id="entries" class="entries"></ul>
          <p id="fileNote" class="small muted"></p>
        </div>
        <div id="viewer" class="viewer" hidden>
          <div class="viewer-head">
            <span id="viewerPath"></span>
            <span class="viewer-actions">
              <button id="copyPath" class="mini">Copy path</button>
              <button id="downloadFile" class="mini">Download</button>
              <button id="closeViewer" class="mini" title="Close (Escape)">Close</button>
            </span>
          </div>
          <p id="viewerNote" class="small muted" role="status"></p>
          <div id="viewerBody"></div>
        </div>
      </div>
    </section>

    <!-- ==== HANDS START: launchers for a terminal and a desktop, each opening in its own browser tab ==== -->
    <section id="panelHands" class="panel" role="tabpanel" aria-labelledby="tab-hands">
      <h2>Hands</h2>
      <p class="muted">Take the controls yourself when a dog is stuck on something only a person can do: a CAPTCHA, a login, a consent page, a program with its own screen. Each opens in a new full-screen tab and ends when this browser is unpaired.</p>
      <div class="launchers">
        <div class="box">
          <button id="handsTerminal">Open terminal</button>
          <p class="small muted">A shell in the sandbox that survives closing the tab (tmux), good for TUIs such as opencode.</p>
        </div>
        <div class="box">
          <button id="handsDesktop">Open desktop</button>
          <p class="small muted">The sandbox's graphical desktop with a browser, for a laptop or tablet. The link lasts up to an hour.</p>
        </div>
        <div class="box">
          <button id="handsWhiteboard">Open whiteboard</button>
          <p class="small muted">What the walker draws for you to read or answer (choices, edit reviews, charts), in a tab of its own. It is also below.</p>
        </div>
      </div>
      <p id="handsNote" class="small muted" role="status"></p>
      <div id="wbHome">
        <section id="whiteboard" class="whiteboard" aria-labelledby="wbHeading">
          <h2 id="wbHeading">Whiteboard</h2>
          <p id="wbEmpty" class="small muted">Nothing drawn yet. When something is easier to see or decide on a screen, the walker draws it here, and you can answer here or aloud.</p>
          <div id="wbCurrent"></div>
          <details id="wbOlder" class="wb-older" hidden><summary>Earlier boards (<span id="wbOlderCount">0</span>)</summary><div id="wbOlderList"></div></details>
        </section>
      </div>
      <p id="wbAway" class="box small" hidden>The whiteboard is open in its own tab. <button id="wbBack" class="mini">Bring it back here</button></p>
    </section>
    <!-- ==== HANDS END ==== -->
  </div>
</section>

<details class="diag small"><summary class="muted">Diagnostics log</summary><div id="log" class="mono muted"></div></details>
</main>
</div>

<script>
"use strict";
// ==== CORE START: the decoder, shared by the page, the AudioWorklet (via toString), and offline tests ====
const ROWS = [697, 770, 852, 941];
const COLS = [1209, 1336, 1477, 1633];
const KEYPAD = ["123A", "456B", "789C", "*0#D"];
const ALPHABET = "0123456789ABCD*#"; // nibble value -> keypad symbol
const CODE_BYTES = 10;   // 80 random bits
const PARITY_BYTES = 6;  // Reed-Solomon parity
const BAUD_RATES = [4, 6, 8, 12, 16]; // symbols per second the call may use; tones touch, no gap
const N_SLOTS = (CODE_BYTES + PARITY_BYTES) * 2;
const DOT = "·";        // an unclear slot or stretch

// Goertzel classifier: each hop labels the latest 25.6 ms window with a keypad symbol or null.
// Rectangular window, as in classic DTMF receivers: its 39 Hz resolution keeps the nearest
// neighbors (73 Hz apart) out of the main lobe. Hops are a quarter window, so even a 62.5 ms
// slot (16 baud) holds about six windows that lie entirely inside it; slower rates get more.
class Classifier {
  constructor(sampleRate, opts) {
    opts = opts || {};
    this.win = Math.round(0.0256 * sampleRate);
    this.hop = Math.round(this.win / 4);
    this.buf = new Float32Array(this.win);
    this.pos = 0;
    this.count = 0;
    this.coef = ROWS.concat(COLS).map((f) => 2 * Math.cos(2 * Math.PI * f / sampleRate));
    this.minRms = opts.minRms ?? 0.003;
    this.purity = opts.purity ?? 0.5;
    this.dominance = opts.dominance ?? 4;
    this.twist = opts.twist ?? 10;
  }
  push(samples, onResult) {
    for (let i = 0; i < samples.length; i++) {
      this.buf[this.pos] = samples[i];
      this.pos = (this.pos + 1) % this.win;
      this.count++;
      if (this.count >= this.win && (this.count - this.win) % this.hop === 0) onResult(this.classify());
    }
  }
  classify() {
    const N = this.win;
    const s1 = new Float64Array(8), s2 = new Float64Array(8);
    let energy = 0;
    for (let j = 0; j < N; j++) {
      const x = this.buf[(this.pos + j) % N];
      energy += x * x;
      for (let k = 0; k < 8; k++) {
        const s0 = x + this.coef[k] * s1[k] - s2[k];
        s2[k] = s1[k];
        s1[k] = s0;
      }
    }
    // Normalized so a sinusoid of amplitude A reports A^2/2, its mean power.
    const P = new Array(8);
    for (let k = 0; k < 8; k++) P[k] = 2 * (s1[k] * s1[k] + s2[k] * s2[k] - this.coef[k] * s1[k] * s2[k]) / (N * N);
    const mean = energy / N;
    const rms = Math.sqrt(mean);
    const best = (from) => {
      const group = P.slice(from, from + 4);
      const order = group.map((p, i) => [p, i]).sort((a, b) => b[0] - a[0]);
      return { index: order[0][1], power: order[0][0], runnerUp: order[1][0] };
    };
    const r = best(0), c = best(4);
    const twistDb = Math.abs(10 * Math.log10((r.power + 1e-12) / (c.power + 1e-12)));
    const ok = rms >= this.minRms
      && r.power + c.power >= this.purity * mean
      && r.power >= this.dominance * r.runnerUp
      && c.power >= this.dominance * c.runnerUp
      && twistDb <= this.twist;
    return { sym: ok ? KEYPAD[r.index][c.index] : null, rms };
  }
}

// ---- Reed-Solomon over GF(256), errors and erasures (after Wikiversity's "Reed-Solomon codes for coders").
// Primitive polynomial 0x11d, generator roots 2^0..2^(n-1), as in src/pairing.ts.
// Polynomials are arrays, highest degree first.
const GF_EXP = new Uint8Array(512), GF_LOG = new Uint8Array(256);
(() => {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    GF_EXP[i] = x; GF_LOG[x] = i;
    x <<= 1; if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i++) GF_EXP[i] = GF_EXP[i - 255];
})();
const gfMul = (a, b) => (a && b ? GF_EXP[GF_LOG[a] + GF_LOG[b]] : 0);
const gfDiv = (a, b) => (a ? GF_EXP[(GF_LOG[a] + 255 - GF_LOG[b]) % 255] : 0);
const gfPow = (x, p) => GF_EXP[(((GF_LOG[x] * p) % 255) + 255) % 255];
const gfInv = (x) => GF_EXP[255 - GF_LOG[x]];
const polyScale = (p, x) => p.map((c) => gfMul(c, x));
function polyAdd(p, q) {
  const r = new Array(Math.max(p.length, q.length)).fill(0);
  p.forEach((c, i) => { r[i + r.length - p.length] = c; });
  q.forEach((c, i) => { r[i + r.length - q.length] ^= c; });
  return r;
}
function polyMul(p, q) {
  const r = new Array(p.length + q.length - 1).fill(0);
  for (let j = 0; j < q.length; j++) for (let i = 0; i < p.length; i++) r[i + j] ^= gfMul(p[i], q[j]);
  return r;
}
function polyEval(p, x) {
  let y = p[0];
  for (let i = 1; i < p.length; i++) y = gfMul(y, x) ^ p[i];
  return y;
}
// Only used to show the corrected parity symbols next to the raw ones.
function rsEncode(msg, nsym) {
  let gen = [1];
  for (let i = 0; i < nsym; i++) gen = polyMul(gen, [1, gfPow(2, i)]);
  const out = [...msg, ...new Array(nsym).fill(0)];
  for (let i = 0; i < msg.length; i++) {
    const coef = out[i];
    if (coef) for (let j = 1; j < gen.length; j++) out[i + j] ^= gfMul(gen[j], coef);
  }
  return [...msg, ...out.slice(msg.length)];
}
const rsSyndromes = (msg, nsym) => [0, ...Array.from({ length: nsym }, (_, i) => polyEval(msg, gfPow(2, i)))];

function rsCorrectErrata(msg, synd, errPos) {
  const coefPos = errPos.map((p) => msg.length - 1 - p);
  let errLoc = [1];
  for (const i of coefPos) errLoc = polyMul(errLoc, polyAdd([1], [gfPow(2, i), 0]));
  const product = polyMul([...synd].reverse(), errLoc);
  const errEval = product.slice(product.length - errLoc.length).reverse();
  const X = coefPos.map((p) => gfPow(2, -(255 - p)));
  const E = new Array(msg.length).fill(0);
  X.forEach((Xi, i) => {
    const XiInv = gfInv(Xi);
    let locPrime = 1;
    X.forEach((Xj, j) => { if (j !== i) locPrime = gfMul(locPrime, 1 ^ gfMul(XiInv, Xj)); });
    if (locPrime === 0) throw new Error("could not find error magnitude");
    const y = gfMul(Xi, polyEval([...errEval].reverse(), XiInv));
    E[errPos[i]] = gfDiv(y, locPrime);
  });
  return polyAdd(msg, E);
}

function rsErrorLocator(synd, nsym, eraseCount) {
  let errLoc = [1], oldLoc = [1];
  const shift = synd.length > nsym ? synd.length - nsym : 0;
  for (let i = 0; i < nsym - eraseCount; i++) {
    const K = i + shift;
    let delta = synd[K];
    for (let j = 1; j < errLoc.length; j++) delta ^= gfMul(errLoc[errLoc.length - 1 - j], synd[K - j]);
    oldLoc = [...oldLoc, 0];
    if (delta) {
      if (oldLoc.length > errLoc.length) {
        const newLoc = polyScale(oldLoc, delta);
        oldLoc = polyScale(errLoc, gfInv(delta));
        errLoc = newLoc;
      }
      errLoc = polyAdd(errLoc, polyScale(oldLoc, delta));
    }
  }
  while (errLoc.length && errLoc[0] === 0) errLoc.shift();
  const errs = errLoc.length - 1;
  if ((errs - eraseCount) * 2 + eraseCount > nsym) throw new Error("too many errors");
  return errLoc;
}

function rsForneySyndromes(synd, pos, n) {
  const fsynd = synd.slice(1);
  for (const p of pos) {
    const x = gfPow(2, n - 1 - p);
    for (let j = 0; j < fsynd.length - 1; j++) fsynd[j] = gfMul(fsynd[j], x) ^ fsynd[j + 1];
  }
  return fsynd;
}

// Returns { data, errata: corrected byte positions } or throws when the frame is beyond repair.
function rsDecode(received, nsym, erasePos) {
  const msg = [...received];
  for (const p of erasePos) msg[p] = 0;
  if (erasePos.length > nsym) throw new Error("too many erasures");
  const synd = rsSyndromes(msg, nsym);
  if (Math.max(...synd) === 0) return { data: msg.slice(0, msg.length - nsym), errata: [...erasePos] };
  const fsynd = rsForneySyndromes(synd, erasePos, msg.length);
  const errLoc = rsErrorLocator(fsynd, nsym, erasePos.length);
  const reversed = [...errLoc].reverse();
  const errPos = [];
  for (let i = 0; i < msg.length; i++) if (polyEval(reversed, gfPow(2, i)) === 0) errPos.push(msg.length - 1 - i);
  if (errPos.length !== errLoc.length - 1) throw new Error("Chien search found the wrong number of errors");
  const fixed = rsCorrectErrata(msg, synd, [...erasePos, ...errPos]);
  if (Math.max(...rsSyndromes(fixed, nsym)) > 0) throw new Error("could not correct");
  return { data: fixed.slice(0, fixed.length - nsym), errata: [...erasePos, ...errPos] };
}

// ---- Framing
const bytesToSymbols = (bytes) => bytes.map((b) => ALPHABET[b >> 4] + ALPHABET[b & 15]).join("");
const toHex = (bytes) => [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
const hexToBytes = (hex) => hex.match(/../g).map((h) => parseInt(h, 16));

// Symbol clock: votes of the windows lying entirely inside each slot. start and slot are in
// samples; labels[i] is the label of the window starting at (firstHop + i) * hop.
function slotVotes(labels, firstHop, hop, win, start, slot, nSlots) {
  const votes = Array.from({ length: nSlots }, () => ({}));
  labels.forEach((sym, i) => {
    if (!sym) return;
    const a = (firstHop + i) * hop;
    const k = Math.floor((a - start) / slot);
    if (k < 0 || k >= nSlots || a + win > start + (k + 1) * slot) return;
    votes[k][sym] = (votes[k][sym] || 0) + 1;
  });
  return votes;
}
// A slot with no votes, or no clear majority, is an erasure.
function slotSymbols(votes) {
  return votes.map((v) => {
    const order = Object.entries(v).sort((a, b) => b[1] - a[1]);
    if (!order.length || (order[1] && order[0][1] < 2 * order[1][1])) return null;
    return order[0][0];
  });
}
// Fit the clock: search the frame's start offset and a small rate error, maximizing agreement.
function alignSlots(labels, firstHop, hop, win, slot, nSlots) {
  const guess = firstHop * hop + 0.4 * win;
  let best = null;
  for (let scale = 0.99; scale <= 1.0101; scale += 0.005) {
    for (let d = -slot / 2; d <= slot / 2; d += hop / 2) {
      const votes = slotVotes(labels, firstHop, hop, win, guess + d, slot * scale, nSlots);
      let score = 0;
      for (const v of votes) {
        const counts = Object.values(v).sort((a, b) => b - a);
        if (counts.length) score += counts[0] - counts.slice(1).reduce((s, c) => s + c, 0);
      }
      if (!best || score > best.score) best = { score, votes };
    }
  }
  return { symbols: slotSymbols(best.votes), score: best.score };
}

// Streaming frame assembler: hop labels in, a live change stream and finished frames out.
// A frame opens at the first tone after silence and closes after 0.25 s of silence, or once it
// outlasts the slowest frame. Its rate is found at the end: see decodeFrame.
const STEADY_HOPS = 3;  // a tone held this many hops (about 19 ms) is real, not a blip
const GAP_HOPS = 6;      // a toneless stretch this long (about 38 ms) separates tones, so a repeat shows again
const UNCLEAR_HOPS = 32; // longer than any designed gap (about 0.2 s): shows as a dot
class FrameReceiver {
  constructor(sampleRate, hop, win, onLive, onFrame) {
    Object.assign(this, { sampleRate, hop, win, onLive, onFrame });
    this.maxHops = (N_SLOTS / Math.min(...BAUD_RATES) + 0.5) * sampleRate / hop;
    this.hopIndex = 0;
    this.frame = null;
    this.silent = 0;
  }
  feed(sym) {
    const i = this.hopIndex++;
    if (!this.frame) {
      if (!sym) return;
      this.frame = { firstHop: i, labels: [], run: { sym: null, len: 0 }, live: "", heard: null, unclear: false };
      this.silent = 0;
      this.onLive("");
    }
    const f = this.frame;
    f.labels.push(sym);
    this.silent = sym ? 0 : this.silent + 1;
    // Live view: the rate is unknown until the frame ends, so show debounced changes of tone.
    if (sym === f.run.sym) f.run.len++;
    else f.run = { sym, len: 1 };
    if (!sym && f.run.len === GAP_HOPS) f.heard = null;
    if (!sym && f.run.len === UNCLEAR_HOPS) f.unclear = true;
    if (sym && f.run.len === STEADY_HOPS && sym !== f.heard) {
      f.live += (f.unclear && f.live ? DOT : "") + sym;
      f.heard = sym;
      f.unclear = false;
      this.onLive(f.live);
    }
    const silenceOver = this.silent * this.hop > 0.25 * this.sampleRate;
    if (silenceOver || i - f.firstHop > this.maxHops) this.finish();
  }
  finish() {
    const f = this.frame;
    this.frame = null;
    if (f) {
      const frame = decodeFrame(f.labels, f.firstHop, this.hop, this.win, this.sampleRate);
      if (frame) this.onFrame(frame);
    }
  }
}

// The frame's span runs from the first to the last steady tone, so stray blips at either end
// (speech before the tones, a click after) neither open nor stretch it.
function steadySpan(labels) {
  let first = -1, last = -1, runStart = 0;
  for (let i = 1; i <= labels.length; i++) {
    if (i < labels.length && labels[i] && labels[i] === labels[runStart]) continue;
    if (labels[runStart] && i - runStart >= STEADY_HOPS) {
      if (first < 0) first = runStart;
      last = i - 1;
    }
    runStart = i;
  }
  return first < 0 ? null : { first, last };
}

// Tries each allowed rate whose frame length is within 25% of the heard span; keeps the claimable
// decode with the fewest repaired bytes, or else the best-fitting clock as a failure to display.
// Returns null for a blip that no rate could explain.
function decodeFrame(labels, firstHop, hop, win, sampleRate) {
  const span = steadySpan(labels);
  if (!span || labels.slice(span.first, span.last + 1).filter(Boolean).length < N_SLOTS) return null;
  const seconds = ((span.last - span.first) * hop + win) / sampleRate;
  const trimmed = labels.slice(span.first, span.last + 1);
  let decoded = null, closest = null;
  for (const baud of BAUD_RATES) {
    if (Math.abs(seconds - N_SLOTS / baud) > 0.25 * N_SLOTS / baud) continue;
    const fit = alignSlots(trimmed, firstHop + span.first, hop, win, sampleRate / baud, N_SLOTS);
    const frame = Object.assign(decodeSlots(fit.symbols), { baud, seconds, score: fit.score });
    if (claimable(frame) && (!decoded || frame.errata.length < decoded.errata.length
      || (frame.errata.length === decoded.errata.length && frame.score > decoded.score))) decoded = frame;
    if (!closest || frame.score > closest.score) closest = frame;
  }
  if (!closest) return null;
  return decoded ? Object.assign(decoded, { rateFound: true }) : Object.assign(closest, { rateFound: false });
}

// Raw: the data slots as heard. ECC: Reed-Solomon with unclear slots as erasures. Only ecc is claimed.
function decodeSlots(symbols) {
  const bytes = [], erased = [];
  for (let b = 0; b < symbols.length / 2; b++) {
    const hi = symbols[2 * b], lo = symbols[2 * b + 1];
    if (hi === null || lo === null) { erased.push(b); bytes.push(0); }
    else bytes.push((ALPHABET.indexOf(hi) << 4) | ALPHABET.indexOf(lo));
  }
  const raw = erased.some((b) => b < CODE_BYTES) ? null : toHex(bytes.slice(0, CODE_BYTES));
  let ecc = null, errata = [], error = null;
  try {
    const result = rsDecode(bytes, PARITY_BYTES, erased);
    ecc = toHex(result.data);
    errata = result.errata;
  } catch (e) { error = e.message; }
  return { symbols, erased, raw, ecc, errata, error };
}
// A frame repaired with all its redundancy spent (say, six erasures) is unchecked: any data
// would fit. Claim only frames with parity left over to confirm the repair.
function claimable(frame) {
  const wrong = Math.max(0, frame.errata.length - frame.erased.length);
  return frame.ecc !== null && frame.erased.length + 2 * wrong < PARITY_BYTES;
}
// ==== CORE END ====

// ==== WORKLET START ====
// The AudioWorklet runs the same Classifier, serialized with toString and loaded from a Blob URL.
const WORKLET_SRC = "const ROWS = " + JSON.stringify(ROWS) + ", COLS = " + JSON.stringify(COLS)
  + ", KEYPAD = " + JSON.stringify(KEYPAD) + ";\n"
  + Classifier.toString() + "\n"
  + "class PairingProcessor extends AudioWorkletProcessor {\n"
  + "  constructor() { super(); this.c = new Classifier(sampleRate); }\n"
  + "  process(inputs) {\n"
  + "    const ch = inputs[0] && inputs[0][0];\n"
  + "    if (ch) this.c.push(ch, (r) => this.port.postMessage(r));\n"
  + "    return true;\n"
  + "  }\n"
  + "}\n"
  + "registerProcessor(\"pairing-dtmf\", PairingProcessor);\n";
// ==== WORKLET END ====

const $ = (id) => document.getElementById(id);
const HOME = "/home/daytona";
const TOKEN_KEY = "dogwalk.pair.token";

// Builds an element; string children become text nodes, so server strings are never parsed as HTML.
function el(tag, props, children) {
  const node = document.createElement(tag);
  for (const key in props || {}) {
    const value = props[key];
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.slice(0, 2) === "on") node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of [].concat(children == null ? [] : children)) {
    if (child != null && child !== false) node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

function log(line) {
  const box = $("log");
  box.textContent = new Date().toLocaleTimeString() + " " + line + "\n" + box.textContent.slice(0, 8000);
}
function setStatus(text, tone) {
  $("status").textContent = text;
  $("status").className = "status " + (tone || "");
}
function showScreen(name) {
  $("pairScreen").hidden = name !== "pair";
  $("pairedScreen").hidden = name !== "paired";
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---- Listening and claiming
let listener = null;     // { stop } while the microphone is open
let latestHop = null;    // newest classifier result, for the meter
let claimState = "idle"; // idle | listening | claiming | waiting
const rejectedCodes = new Set();

async function startListening() {
  if (listener) return;
  $("listen").disabled = true;
  $("live").textContent = "";
  $("frames").replaceChildren();
  let ctx = null, stream = null;
  try {
    if (!navigator.mediaDevices) throw new Error("this page needs HTTPS for microphone access");
    ctx = new AudioContext();
    await ctx.resume();
    // Speech processing would treat steady tones as noise and remove them.
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
    });
    const granted = stream.getAudioTracks()[0].getSettings();
    log("mic " + (granted.sampleRate || ctx.sampleRate) + " Hz, context " + ctx.sampleRate + " Hz, echoCancellation="
      + granted.echoCancellation + ", noiseSuppression=" + granted.noiseSuppression + ", autoGainControl=" + granted.autoGainControl);
    const source = ctx.createMediaStreamSource(stream);
    const sink = ctx.createGain();
    sink.gain.value = 0;
    sink.connect(ctx.destination);
    const probe = new Classifier(ctx.sampleRate); // same window and hop as the worklet's
    const rx = new FrameReceiver(ctx.sampleRate, probe.hop, probe.win, showLive, onFrame);
    const onHop = (r) => { latestHop = r; rx.feed(r.sym); };
    let node;
    try {
      await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET_SRC], { type: "application/javascript" })));
      node = new AudioWorkletNode(ctx, "pairing-dtmf");
      node.port.onmessage = (e) => onHop(e.data);
      log("decoding in an AudioWorklet");
    } catch (error) {
      // Fallback for browsers without blob-module worklets.
      node = ctx.createScriptProcessor(2048, 1, 1);
      node.onaudioprocess = (e) => probe.push(e.inputBuffer.getChannelData(0), onHop);
      log("AudioWorklet unavailable (" + error.message + "), using ScriptProcessor");
    }
    source.connect(node);
    node.connect(sink);
    const opened = { stream, ctx };
    listener = { stop: () => { opened.stream.getTracks().forEach((t) => t.stop()); opened.ctx.close(); } };
    claimState = "listening";
    $("stop").disabled = false;
    setStatus("Listening for pairing tones");
    requestAnimationFrame(drawMeter);
  } catch (error) {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (ctx) ctx.close();
    setStatus("Could not use the microphone: " + error.message, "bad");
    $("listen").disabled = false;
  }
}

function stopListening() {
  if (listener) listener.stop();
  listener = null;
  latestHop = null;
  $("listen").disabled = false;
  $("stop").disabled = true;
  $("level").style.width = "0";
  $("levelText").innerHTML = "&nbsp;";
}

function drawMeter() {
  if (!listener) return;
  if (latestHop) {
    const dbfs = 20 * Math.log10(latestHop.rms + 1e-9);
    $("level").style.width = Math.max(0, Math.min(100, (dbfs + 70) / 70 * 100)) + "%";
    $("levelText").textContent = dbfs.toFixed(0) + " dBFS" + (latestHop.sym ? ", tone " + latestHop.sym : "");
  }
  requestAnimationFrame(drawMeter);
}

function showLive(text) {
  $("live").textContent = text;
}

function symbolRow(label, symbols, classFor) {
  const row = el("div", { class: "symrow" }, el("span", { class: "lbl" }, label));
  symbols.forEach((s, k) => {
    if (k === CODE_BYTES * 2) row.append(el("span", { class: "sep" }, "|"));
    row.append(el("span", { class: "sym " + (classFor(k) || "") }, s == null ? DOT : s));
  });
  return row;
}

// Diagnostics per frame: the raw slots against the ECC-corrected frame.
function showFrame(frame) {
  const corrected = frame.ecc ? bytesToSymbols(rsEncode(hexToBytes(frame.ecc), PARITY_BYTES)).split("") : null;
  const fixed = new Set(frame.errata);
  const wrong = frame.errata.length - frame.erased.length;
  const verdict = frame.ecc
    ? (claimable(frame) ? "ECC ok: " : "ECC unverified: ") + frame.ecc.match(/.{1,4}/g).join(" ") + " (repaired " + frame.erased.length + " unclear and "
      + Math.max(0, wrong) + " wrong of " + PARITY_BYTES + " parity bytes)"
    : "ECC failed: " + frame.error;
  const rate = (frame.rateFound ? "decoded at " : "no rate decoded; closest ") + frame.baud + " baud ("
    + frame.seconds.toFixed(1) + " s of tones)";
  const box = el("div", { class: "frame" }, [
    el("div", { class: "small muted" }, rate),
    symbolRow("raw", frame.symbols, (k) => {
      if (frame.symbols[k] === null) return "erased";
      if (corrected && corrected[k] !== frame.symbols[k]) return "wrong";
    }),
    corrected ? symbolRow("ecc", corrected, (k) => (fixed.has(k >> 1) ? "fixed" : "")) : null,
    el("div", { class: "verdict " + (claimable(frame) ? "ok" : "bad") }, verdict),
  ]);
  const frames = $("frames");
  frames.prepend(box);
  while (frames.children.length > 4) frames.lastChild.remove();
}

function onFrame(frame) {
  showFrame(frame);
  log("frame: " + frame.seconds.toFixed(2) + " s, " + (frame.rateFound ? "decoded at " : "closest ") + frame.baud
    + " baud, " + frame.erased.length + " unclear bytes, raw " + (frame.raw || "-") + ", ecc " + (frame.ecc || frame.error));
  if (frame.ecc && !claimable(frame)) log("not claimed: the repair used all the parity, so it is unverified");
  if (claimable(frame) && claimState === "listening" && !rejectedCodes.has(frame.ecc)) claim(frame.ecc);
  else if (!claimable(frame) && claimState === "listening") {
    setStatus("Heard tones but could not decode them. Still listening: ask the walker to send the tones slower.", "bad");
    reportDecodeFailure(frame);
  }
}

// Anonymous telemetry, so the call can tell "heard nothing" from "heard tones, could not
// decode": coarse counts only, never symbols or code bits. Best effort.
function reportDecodeFailure(frame) {
  const report = {
    stage: frame.ecc ? "ecc_unverified" : "ecc_failed",
    baud: frame.baud,
    seconds: Math.round(frame.seconds * 10) / 10,
    symbols_heard: frame.symbols.filter((s) => s !== null).length,
    erasures: frame.erased.length,
  };
  fetch("/pair/decode-failed", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(report),
  }).catch(() => {});
}

async function claim(code) {
  claimState = "claiming";
  setStatus("Heard a code, claiming");
  let response, body;
  try {
    response = await fetch("/pair/claim", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ code }),
    });
    body = await response.json().catch(() => ({}));
  } catch (error) {
    return claimFailed("Could not reach dogwalk.tools (" + error.message + "). Still listening.");
  }
  if (response.ok && typeof body.ticket === "string") {
    stopListening();
    claimState = "waiting";
    return waitForAnswer(body.ticket);
  }
  if (response.status === 404) {
    rejectedCodes.add(code);
    return claimFailed("That code is unknown or expired. Still listening: ask the walker in the call for new tones.");
  }
  claimFailed("Claim failed (" + response.status + "): " + (body.error || response.statusText) + ". Still listening.");
}

function claimFailed(message) {
  claimState = listener ? "listening" : "idle";
  setStatus(message, "bad");
}

async function waitForAnswer(ticket) {
  setStatus("Waiting for you to press one in the call");
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    await sleep(1000);
    let response, body;
    try {
      response = await fetch("/pair/claim/status", { headers: { authorization: "Bearer " + ticket } });
      body = await response.json().catch(() => ({}));
    } catch (error) {
      log("claim status check failed: " + error.message);
      continue;
    }
    if (!response.ok) return pairingFailed("Pairing check failed (" + response.status + "): " + (body.error || response.statusText) + ".");
    if (body.state === "waiting") continue;
    if (body.state === "accepted" && typeof body.token === "string") {
      sessionStorage.setItem(TOKEN_KEY, body.token);
      claimState = "idle";
      return connect();
    }
    if (body.state === "refused") return pairingFailed("The pairing was refused in the call.");
    return pairingFailed("The pairing request expired before it was accepted.");
  }
  pairingFailed("No answer in the call within 45 seconds.");
}

function pairingFailed(message) {
  claimState = "idle";
  showScreen("pair");
  setStatus(message + " Press Listen to try again.", "bad");
}

// ---- Small helpers shared by the paired screen
const stored = (key, fallback) => { try { const v = localStorage.getItem(key); return v == null ? fallback : v; } catch (error) { return fallback; } };
const store = (key, value) => { try { localStorage.setItem(key, value); } catch (error) { /* private mode */ } };
const REDUCED_MOTION = matchMedia("(prefers-reduced-motion: reduce)");
const WIDE = matchMedia("(min-width: 1100px)");
const basename = (path) => String(path).split("/").filter(Boolean).pop() || "/";

function ago(ms) {
  const seconds = Math.round((Date.now() - ms) / 1000);
  if (seconds < 10) return "now";
  if (seconds < 60) return seconds + " s ago";
  if (seconds < 3600) return Math.round(seconds / 60) + " min ago";
  if (seconds < 86400) return Math.round(seconds / 3600) + " h ago";
  return new Date(ms).toLocaleDateString();
}
// A timestamp that tick() keeps current; the exact clock time is in its tooltip.
function timeNode(ms) {
  const at = Number(ms) || Date.now();
  return el("time", { datetime: new Date(at).toISOString(), "data-at": at, title: new Date(at).toLocaleTimeString() }, ago(at));
}

function copyText(text, button) {
  const done = () => {
    if (!button) return;
    const label = button.textContent;
    button.textContent = "Copied";
    setTimeout(() => { button.textContent = label; }, 1200);
  };
  const fallback = () => {
    const area = el("textarea", { class: "offscreen" }, text);
    document.body.append(area);
    area.select();
    try { document.execCommand("copy"); done(); } catch (error) { log("copy failed: " + error.message); }
    area.remove();
  };
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback);
  else fallback();
}
function copyButton(path) {
  const button = el("button", { class: "mini", title: "Copy " + path, "aria-label": "Copy path " + path, onclick: () => copyText(path, button) }, "Copy");
  return button;
}

// ---- The paired connection
let socket = null;
let backoff = 1000;
let reconnectTimer = null;
let pingTimer = null;
let pairing = null;     // the hello message, with idle_expires_at refreshed by pongs
let serverError = "";
let lastStatus = null;  // the newest status message, for the chips and the recent paths
let unpairRequested = false; // this page asked to unpair, so a 4003 close is expected

function connect() {
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (!token) return;
  clearTimeout(reconnectTimer);
  showScreen("paired");
  if (!pairing) setStatus("Connecting");
  setConnection("Connecting");
  // The identity half routes the socket to its Durable Object; the secret only travels in the auth message.
  const identity = token.split(".")[0];
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host
    + "/pair/ws?for=" + encodeURIComponent(identity));
  socket = ws;
  ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token }));
  ws.onmessage = (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch (error) { return; }
    onMessage(message);
  };
  ws.onclose = (event) => onClose(ws, event);
}

function send(message) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(message));
  return true;
}

function onClose(ws, event) {
  if (ws !== socket) return;
  socket = null;
  clearInterval(pingTimer);
  log("socket closed " + event.code + (event.reason ? " " + event.reason : ""));
  renderChips();
  if (event.code === 4001 || event.code === 4003) {
    forgetPairing();
    // 4003 without our own request: the server allows one Paired Browser, so another one replaced this one.
    if (event.code === 4003 && unpairRequested) setStatus("Unpaired. Press Listen to pair again.");
    else if (event.code === 4003) setStatus("This browser was unpaired (another browser may have paired in its place). Press Listen to pair again.", "bad");
    else setStatus("This pairing is no longer valid" + (serverError ? " (" + serverError + ")" : "") + ". Press Listen to pair again.", "bad");
    serverError = "";
    unpairRequested = false;
    return;
  }
  setConnection("Disconnected, reconnecting in " + Math.round(backoff / 1000) + " s");
  reconnectTimer = setTimeout(connect, backoff);
  backoff = Math.min(backoff * 2, 30000);
}

function onMessage(m) {
  switch (m.type) {
    case "hello":
      pairing = m;
      backoff = 1000;
      setStatus("Paired", "ok");
      setConnection("Connected");
      renderPairing();
      clearInterval(pingTimer);
      pingTimer = setInterval(() => send({ type: "ping" }), 30000);
      if (!listedOnce) listDir(currentDir);
      viewingSent = ""; // a new socket: the server has heard nothing yet
      reportViewing(true);
      break;
    case "pong":
      if (pairing && m.idle_expires_at) { pairing.idle_expires_at = m.idle_expires_at; renderPairing(); }
      break;
    case "status": renderStatus(m); break;
    case "feed": onFeed(m); break;
    case "list": if (m.id === pendingList) renderListing(m); break;
    case "file": if (m.id === pendingRead) renderTextFile(m); break;
    case "launch": settleLaunch(m); break;
    case "show": onShow(m); break;
    case "board": wbOnBoard(m.board); break;
    case "boards": wbOnBoards(m.boards); break;
    case "error":
      if (m.id != null) {
        if (m.id === pendingList) $("fileNote").textContent = "Error: " + m.error;
        else if (m.id === pendingRead) readFailed(String(m.error || ""));
        else settleLaunch(m);
      } else { serverError = String(m.error || ""); log("server error: " + serverError); }
      break;
    case "unpaired": log("unpaired by request"); break;
  }
}

function setConnection(text) { $("conn").textContent = text; }

function clock(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
function fromNow(ms) {
  const minutes = Math.max(0, Math.round((ms - Date.now()) / 60000));
  return minutes < 60 ? minutes + " min" : Math.floor(minutes / 60) + " h " + (minutes % 60) + " min";
}
function renderPairing() {
  if (!pairing) return;
  $("who").textContent = (pairing.device || "This browser") + (pairing.place ? ", " + pairing.place : "");
  const parts = [];
  if (pairing.idle_expires_at) parts.push("Ends if idle until " + clock(pairing.idle_expires_at) + " (in " + fromNow(pairing.idle_expires_at) + ")");
  if (pairing.expires_at) parts.push("ends at " + clock(pairing.expires_at) + " at the latest");
  $("expiry").textContent = parts.join(", ");
  renderChips();
}

// ---- Header chips: is the call on, can the Agents be reached, how long the pairing lasts
function setChip(id, text, tone) {
  const chip = $(id);
  chip.textContent = text;
  chip.className = "chip " + (tone || "");
}
function renderChips() {
  $("chips").hidden = !pairing;
  if (!pairing) return;
  const live = !!socket && socket.readyState === WebSocket.OPEN && !!lastStatus;
  const sandbox = (lastStatus && lastStatus.sandbox) || {};
  if (!live) {
    setChip("chipCall", "Call: unknown", "");
    setChip("chipAgents", "Agents: unknown", "");
  } else {
    setChip("chipCall", sandbox.call_attached ? "Call connected" : "No call", sandbox.call_attached ? "ok" : "off");
    setChip("chipAgents", sandbox.agent_ready ? "Agents reachable" : "Agents not reachable", sandbox.agent_ready ? "ok" : "warn");
  }
  // An open page pings, so the idle limit never arrives; the hard limit is the real end.
  const ends = pairing.expires_at || pairing.idle_expires_at;
  if (ends) setChip("chipExpiry", "Pairing ends in " + fromNow(ends), ends - Date.now() < 10 * 60000 ? "warn" : "");
  else setChip("chipExpiry", "Paired", "ok");
}

function forgetPairing() {
  sessionStorage.removeItem(TOKEN_KEY);
  pairing = null;
  lastStatus = null;
  prevTurns = null;
  prevPermissions = null;
  closeViewer();
  clearFeed();
  wbClear();
  hideBanner();
  renderChips();
  showScreen("pair");
}

function unpair() {
  if (!confirm("Unpair this browser? You will need to pair it again from the call.")) return;
  unpairRequested = true;
  if (send({ type: "unpair" })) return; // the server replies, then closes with 4003
  unpairRequested = false;
  clearTimeout(reconnectTimer);
  socket = null;
  forgetPairing();
  setStatus("Unpaired. Press Listen to pair again.");
}

// ---- Tabs: Live (sessions and the call timeline), Files, Hands.
// A wide screen shows both Live columns; a narrow one shows the column its segmented control picks.
const TABS = ["live", "files", "hands"];
const TAB_KEY = "dogwalk.pair.tab";
const LIVE_KEY = "dogwalk.pair.liveView";
// The Sessions and Call timeline tabs ("sessions", "feed") became the one Live tab and its two views.
const savedTab = stored(TAB_KEY, "live");
if (savedTab === "sessions" || savedTab === "feed") {
  store(TAB_KEY, "live");
  store(LIVE_KEY, savedTab === "sessions" ? "sessions" : "timeline");
}
let currentTab = TABS.indexOf(stored(TAB_KEY, "live")) >= 0 ? stored(TAB_KEY, "live") : "live";
let liveView = stored(LIVE_KEY, "timeline") === "sessions" ? "sessions" : "timeline";

function showTab(name) {
  if (TABS.indexOf(name) < 0) return;
  currentTab = name;
  store(TAB_KEY, name);
  $("panes").dataset.tab = name;
  for (const tab of TABS) {
    const button = $("tab-" + tab);
    button.setAttribute("aria-selected", String(tab === name));
    button.tabIndex = tab === name ? 0 : -1;
  }
  onFeedShown();
  wbSeen();
  reportViewing();
}
function showLiveView(view) {
  liveView = view === "sessions" ? "sessions" : "timeline";
  store(LIVE_KEY, liveView);
  $("liveCols").dataset.view = liveView;
  $("seg-sessions").setAttribute("aria-pressed", String(liveView === "sessions"));
  $("seg-timeline").setAttribute("aria-pressed", String(liveView === "timeline"));
  onFeedShown();
}
// Opens the Live tab with view in front (on a wide screen both columns show anyway).
function showLive(view) {
  showLiveView(view);
  showTab("live");
}

// ---- What the user is looking at, so the walker can sense it:
// {"type":"viewing","view":"live"|"files"|"hands"|"whiteboard"|"hidden","path":"...","kind":"file"|"dir"},
// where "whiteboard" is the Hands tab with the whiteboard in view, or the whiteboard's own tab while it is visible.
// Sent once it has held still for a second, never twice alike, and never while the tab is hidden,
// apart from the one "hidden" message; becoming visible or reconnecting sends the current view at once.
const VIEWING_MS = 1000;
let viewingFolder = ""; // the folder the file browser last navigated to; closing the viewer clears it
let viewingSent = "";
let viewingTimer = null;
function currentViewing() {
  if (wbTabVisible()) return { type: "viewing", view: "whiteboard" };
  if (document.hidden) return { type: "viewing", view: "hidden" };
  if (currentTab === "hands" && wbOnScreen) return { type: "viewing", view: "whiteboard" };
  if (currentTab !== "files") return { type: "viewing", view: currentTab };
  if (viewer.path) return { type: "viewing", view: "files", path: viewer.path, kind: "file" };
  if (viewingFolder) return { type: "viewing", view: "files", path: viewingFolder, kind: "dir" };
  return { type: "viewing", view: "files" };
}
function reportViewing(now) {
  clearTimeout(viewingTimer);
  if (!now) { viewingTimer = setTimeout(() => reportViewing(true), VIEWING_MS); return; }
  const message = currentViewing();
  const text = JSON.stringify(message);
  if (text !== viewingSent && send(message)) viewingSent = text;
}
function onFeedShown() {
  if (!feedVisible()) return;
  feedUnseen = 0;
  renderFeedCount();
  if (feedStick) scrollFeedToEnd();
}
const feedVisible = () => currentTab === "live" && (WIDE.matches || liveView === "timeline");

// ---- Session status
const openReports = new Set();
let closedOpen = false;
const TURN_LABELS = { idle: "idle", queued: "queued", in_progress: "in progress", stopped: "stopped", failed: "failed" };

function renderStatus(m) {
  lastStatus = m;
  renderChips();
  $("sandbox").replaceChildren("Updated ", timeNode(m.at || Date.now()), ".");

  const permissions = m.pending_permissions || [];
  $("permissions").hidden = !permissions.length;
  $("permissions").replaceChildren(
    el("h2", null, "Permission requests: answer in the call"),
    el("ul", null, permissions.map((p) => el("li", null, [el("b", null, p.alias), ": ", p.title]))));

  const sessions = m.sessions || [];
  const open = sessions.filter((s) => s.session_state !== "closed");
  const closed = sessions.filter((s) => s.session_state === "closed");
  $("sessions").replaceChildren(...(open.length ? open.map(sessionCard) : [el("p", { class: "muted" }, "No open sessions.")]));
  if (!closed.length) $("closed").replaceChildren();
  else {
    const group = el("details", { class: "closed-group", open: closedOpen, ontoggle: () => { closedOpen = group.open; } },
      [el("summary", null, "Closed sessions (" + closed.length + ")"), ...closed.map(sessionCard)]);
    $("closed").replaceChildren(group);
  }
  renderRecent(sessions);
  checkAlerts(m);
}

function badge(text, kind) { return el("span", { class: "badge " + (kind || "") }, text); }

function sessionCard(s) {
  const head = el("div", { class: "card-head" }, [
    el("h3", { class: "alias", style: "--h:" + aliasHue(s.alias) }, s.alias),
    badge(TURN_LABELS[s.turn_state] || s.turn_state, s.turn_state),
    s.session_state !== "ready" ? badge(s.session_state) : null,
    s.followed ? badge("followed", "follow") : null,
    s.stop_reason ? el("span", { class: "small muted" }, s.stop_reason) : null,
  ]);
  const directory = s.directory ? resolvePath("", s.directory) : "";
  const card = el("article", { class: "card turn-" + s.turn_state, "data-alias": s.alias }, [
    head,
    directory ? el("div", { class: "pathline" }, [
      el("button", { class: "link mono small", onclick: () => { showTab("files"); listDir(directory); } }, s.directory),
      copyButton(directory),
    ]) : null,
    s.title ? el("p", { class: "title" }, s.title) : null,
    s.assignment ? el("p", null, [el("span", { class: "muted" }, "Assignment: "), s.assignment]) : null,
  ]);
  const plan = s.plan || [];
  if (plan.length) {
    card.append(el("ul", { class: "plan" }, plan.map((step) =>
      el("li", { class: step.status }, [el("span", { class: "box-mark", title: step.status }), el("span", null, step.content)]))));
  }
  const tools = s.recent_tool_calls || [];
  if (tools.length) {
    card.append(el("ul", { class: "tools" }, tools.map((call) => el("li", null, [
      el("span", { class: "kind" }, call.kind),
      call.title, " ",
      el("span", { class: "small muted" }, "(" + call.status + (call.seconds != null ? ", " + Math.round(call.seconds) + " s" : "") + ")"),
      (call.paths || []).length ? el("div", { class: "paths" }, call.paths.map((p) =>
        el("button", { class: "link", onclick: () => openFile(resolvePath(s.directory, p)) }, p))) : null,
    ]))));
  }
  if (s.remark) card.append(el("p", { class: "remark" }, s.remark));
  if (s.failure) card.append(el("p", { class: "failure" }, s.failure));
  if (s.report) {
    const report = el("details", { class: "report", open: openReports.has(s.alias), ontoggle: () => {
      if (report.open) openReports.add(s.alias); else openReports.delete(s.alias);
    } }, [el("summary", null, "Latest report"), markdownBlock(s.report)]);
    card.append(report);
  }
  if (s.usage && typeof s.usage === "object") {
    const parts = Object.entries(s.usage).filter((e) => typeof e[1] !== "object").map((e) => e[0] + " " + e[1]);
    if (parts.length) card.append(el("p", { class: "small muted" }, "Usage: " + parts.join(", ")));
  }
  return card;
}

// A tool-call path is relative to its session's directory, which is relative to /home/daytona.
function resolvePath(directory, path) {
  let full = String(path || "");
  if (full.charAt(0) !== "/") {
    const dir = String(directory || "");
    full = (dir.charAt(0) === "/" ? dir : HOME + "/" + dir) + "/" + full;
  }
  const parts = [];
  for (const part of full.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") parts.pop(); else parts.push(part);
  }
  return "/" + parts.join("/");
}

// A stable hue per Alias, so each Agent keeps its color in the feed and on its card.
function aliasHue(alias) {
  let hash = 0;
  for (const ch of String(alias || "")) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return Math.round((hash * 137.508) % 360);
}

// ---- Recently touched: paths from every session's latest tool calls, newest first.
// Tool calls carry no timestamps, only their order within a session, so the lists interleave from the newest end.
function recentPaths(sessions) {
  const lists = sessions.slice().reverse().map((s) => ({ s, calls: (s.recent_tool_calls || []).slice().reverse() }));
  const depth = Math.max(0, ...lists.map((l) => l.calls.length));
  const seen = new Set(), out = [];
  for (let i = 0; i < depth; i++) {
    for (const l of lists) {
      const call = l.calls[i];
      if (!call) continue;
      for (const p of call.paths || []) {
        const full = resolvePath(l.s.directory, p);
        if (seen.has(full)) continue;
        seen.add(full);
        out.push({ path: full, alias: l.s.alias, kind: call.kind });
      }
    }
  }
  return out.slice(0, 15);
}
function renderRecent(sessions) {
  const items = recentPaths(sessions).map((r) => {
    const dir = r.path.slice(0, r.path.length - basename(r.path).length).replace(HOME + "/", "~/");
    return el("li", null, [
      el("button", { class: "link", title: r.path, onclick: () => openFile(r.path) }, basename(r.path)),
      el("span", { class: "dir", title: r.path }, dir),
      el("span", { class: "who-tag", style: "--h:" + aliasHue(r.alias) }, r.alias),
      copyButton(r.path),
    ]);
  });
  $("recent").replaceChildren(...(items.length ? items : [el("li", { class: "muted" }, "No files touched yet.")]));
}

// ---- Alerts: a notification and a soft chime while this tab is hidden (off by default)
const ALERT_KEY = "dogwalk.pair.alerts";
let alertsOn = stored(ALERT_KEY, "0") === "1";
let chimeContext = null;
let prevTurns = null;       // alias -> turn_state from the previous status
let prevPermissions = null; // "alias: title" strings from the previous status

async function toggleAlerts(event) {
  const want = event.target.checked;
  $("prefNote").textContent = "";
  if (want && "Notification" in window && Notification.permission !== "granted") {
    const answer = await Notification.requestPermission().catch(() => "denied");
    if (answer !== "granted") $("prefNote").textContent = "Notifications are blocked, so alerts will only chime.";
  }
  // The chime's audio context has to start inside a click.
  if (want && !chimeContext && window.AudioContext) chimeContext = new AudioContext();
  if (chimeContext) chimeContext.resume().catch(() => {});
  alertsOn = want;
  store(ALERT_KEY, want ? "1" : "0");
}

function chime() {
  if (!chimeContext) return;
  const now = chimeContext.currentTime;
  [660, 880].forEach((freq, i) => {
    const osc = chimeContext.createOscillator(), gain = chimeContext.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0, now + i * 0.18);
    gain.gain.linearRampToValueAtTime(0.08, now + i * 0.18 + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.18 + 0.5);
    osc.connect(gain).connect(chimeContext.destination);
    osc.start(now + i * 0.18);
    osc.stop(now + i * 0.18 + 0.55);
  });
}

function raiseAlert(title, body) {
  log("alert: " + title + ": " + body);
  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification(title, { body, tag: title }); } catch (error) { log("notification failed: " + error.message); }
  }
  chime();
}

function checkAlerts(m) {
  const turns = new Map((m.sessions || []).map((s) => [s.alias, s.turn_state]));
  const permissions = new Set((m.pending_permissions || []).map((p) => p.alias + ": " + p.title));
  if (prevTurns && alertsOn && document.hidden) {
    for (const p of permissions) if (!prevPermissions.has(p)) raiseAlert("Permission request", p);
    for (const [alias, state] of turns) {
      const before = prevTurns.get(alias);
      if ((before === "in_progress" || before === "queued") && state !== "in_progress" && state !== "queued") {
        raiseAlert(alias + "'s turn ended", state === "failed" ? "It failed." : "It is ready for you.");
      }
    }
  }
  prevTurns = turns;
  prevPermissions = permissions;
}

// ---- Keep screen on (Screen Wake Lock); the browser drops the lock whenever the tab is hidden
const WAKE_KEY = "dogwalk.pair.wake";
let wakeWanted = stored(WAKE_KEY, "0") === "1";
let wakeLock = null;

async function acquireWake() {
  if (!wakeWanted || wakeLock || !("wakeLock" in navigator) || document.visibilityState !== "visible") return;
  try {
    wakeLock = await navigator.wakeLock.request("screen");
    wakeLock.addEventListener("release", () => { wakeLock = null; });
  } catch (error) {
    $("prefNote").textContent = "Could not keep the screen on: " + error.message;
  }
}
function toggleWake(event) {
  wakeWanted = event.target.checked;
  store(WAKE_KEY, wakeWanted ? "1" : "0");
  $("prefNote").textContent = "";
  if (wakeWanted) acquireWake();
  else if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
}

// ---- Call timeline: what was said and done in the call, newest at the bottom
const FEED_CAP = 500;
const FILTER_KEY = "dogwalk.pair.feedHidden";
const ROLES = ["caller", "voice", "agent", "orchestrator", "system"];
const ROLE_LABELS = { caller: "Caller", voice: "Walker", agent: "Agents", orchestrator: "Walker thinking", system: "System" };
const LANE_LABELS = {
  thinking: "context for the voice", say: "asked the voice to say", instructions: "instructions to the voice", prompt: "brief",
  tool: "tool", report: "report", event: "event", keypad: "keypad",
};
const feedEntries = new Map(); // id -> { entry, node }, oldest first
const feedOpen = new Set();    // ids the reader expanded
let feedHidden = new Set();
try { feedHidden = new Set(JSON.parse(stored(FILTER_KEY, "[]"))); } catch (error) { /* keep the default */ }
let feedStick = true;          // follow the newest entry until the reader scrolls up
let feedUnseen = 0;            // entries that arrived while the reader was elsewhere

function renderFilters() {
  $("feedFilters").replaceChildren(...ROLES.map((role) => el("button", {
    class: "filter f-" + role,
    "aria-pressed": String(!feedHidden.has(role)),
    title: (feedHidden.has(role) ? "Show " : "Hide ") + ROLE_LABELS[role],
    onclick: () => {
      if (feedHidden.has(role)) feedHidden.delete(role); else feedHidden.add(role);
      store(FILTER_KEY, JSON.stringify([...feedHidden]));
      applyFilters();
      renderFilters();
    },
  }, ROLE_LABELS[role])));
}
function applyFilters() {
  const list = $("feedList");
  for (const role of ROLES) list.classList.toggle("hide-" + role, feedHidden.has(role));
  if (feedStick) scrollFeedToEnd();
}

function clearFeed() {
  feedEntries.clear();
  feedOpen.clear();
  $("feedList").replaceChildren();
  $("feedEmpty").hidden = false;
  feedUnseen = 0;
  feedStick = true;
  renderFeedCount();
}

function onFeed(m) {
  const list = $("feedList");
  if (m.snapshot) { clearFeed(); }
  let added = 0;
  for (const entry of m.entries || []) {
    if (!entry || typeof entry.id !== "number") continue;
    const node = feedNode(entry);
    const known = feedEntries.get(entry.id);
    if (known) known.node.replaceWith(node);
    else {
      list.append(node);
      if (!feedHidden.has(entry.role)) added++;
    }
    feedEntries.set(entry.id, { entry, node });
  }
  while (feedEntries.size > FEED_CAP) {
    const oldest = feedEntries.keys().next().value;
    feedEntries.get(oldest).node.remove();
    feedEntries.delete(oldest);
    feedOpen.delete(oldest);
  }
  $("feedEmpty").hidden = feedEntries.size > 0;
  if (m.snapshot) { scrollFeedToEnd(); return; }
  if (feedStick) scrollFeedToEnd();
  else if (added) { $("feedPill").hidden = false; }
  if (added && !feedVisible()) { feedUnseen += added; renderFeedCount(); }
}

function renderFeedCount() {
  for (const id of ["feedCount", "feedCountSeg"]) {
    $(id).textContent = feedUnseen ? String(Math.min(feedUnseen, 99)) + (feedUnseen > 99 ? "+" : "") : "";
    $(id).hidden = !feedUnseen;
  }
}

function scrollFeedToEnd() {
  const list = $("feedList");
  list.scrollTop = list.scrollHeight;
  $("feedPill").hidden = true;
}

function feedNode(e) {
  const role = ROLES.indexOf(e.role) >= 0 ? e.role : "system";
  const lane = String(e.lane || "");
  const text = String(e.text || "");
  let who = role === "agent" ? String(e.who || "Agent") : ROLE_LABELS[role];
  if (role === "orchestrator" && lane === "prompt") who = "Walker to " + (e.who || "an Agent");
  const attrs = { class: "fe f-" + role + (lane ? " lane-" + lane : ""), "data-id": e.id };
  if (role === "agent") attrs.style = "--h:" + aliasHue(e.who);
  const label = el("span", { class: "fe-who" }, who);
  const laneLabel = lane ? el("span", { class: "fe-lane" }, LANE_LABELS[lane] || lane) : null;
  const time = timeNode(e.at);

  // Harness events, keypad presses and finished tool calls fit one compact line.
  if (role === "system" || lane === "tool") {
    return el("li", attrs, el("div", { class: "fe-line" }, [label, laneLabel, el("span", { class: "fe-inline" }, text), time]));
  }
  // Background context and system instructions to the voice are long and rarely needed: folded.
  if (lane === "thinking" || lane === "instructions") {
    const fold = el("details", { class: "fe-fold", open: feedOpen.has(e.id), ontoggle: () => {
      if (fold.open) feedOpen.add(e.id); else feedOpen.delete(e.id);
    } }, [
      el("summary", null, [label, laneLabel, time, el("span", { class: "fe-peek" }, text.split("\n")[0].slice(0, 160))]),
      el("div", { class: "fe-text fe-folded" }, text),
    ]);
    return el("li", attrs, fold);
  }
  const body = lane === "report" ? markdownBlock(text) : el("div", { class: "fe-text" }, text);
  const node = el("li", attrs, [el("div", { class: "fe-head" }, [label, laneLabel, time]), body]);
  if (text.length > 420 || text.split("\n").length > 7) {
    const expanded = feedOpen.has(e.id);
    body.classList.toggle("clamped", !expanded);
    const more = el("button", { class: "link small fe-more", "aria-expanded": String(expanded), onclick: () => {
      const open = body.classList.toggle("clamped") === false;
      if (open) feedOpen.add(e.id); else feedOpen.delete(e.id);
      more.textContent = open ? "less" : "more";
      more.setAttribute("aria-expanded", String(open));
    } }, expanded ? "less" : "more");
    node.append(more);
  }
  return node;
}

// ---- Files: browsing over the socket
let nextId = 1;
let pendingList = 0;
let pendingRead = 0;
let currentDir = HOME;
let listedOnce = false;

function listDir(path) {
  pendingList = nextId++;
  if (!send({ type: "list", id: pendingList, path })) { $("fileNote").textContent = "Not connected."; return; }
  $("fileNote").textContent = "Loading " + path;
}

function renderCrumbs(path) {
  const parts = path.split("/").filter(Boolean);
  const nodes = [el("button", { class: "link", onclick: () => listDir("/") }, "/")];
  parts.forEach((part, i) => {
    if (i) nodes.push(el("span", { class: "slash" }, "/"));
    const target = "/" + parts.slice(0, i + 1).join("/");
    nodes.push(el("button", { class: "link", onclick: () => listDir(target) }, part));
  });
  $("crumbs").replaceChildren(...nodes);
}

function size(bytes) {
  if (bytes == null) return "";
  const scaled = (n, unit) => (n < 10 ? n.toFixed(1) : String(Math.round(n))) + " " + unit;
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return scaled(bytes / 1024, "KB");
  if (bytes < 1073741824) return scaled(bytes / 1048576, "MB");
  return scaled(bytes / 1073741824, "GB");
}

// The sandbox reports modification times as strings, possibly in Go's time.String() form
// ("2026-11-08 10:01:02.5 +0000 UTC"); a time without a zone is read as local.
function parseModified(text) {
  const m = /^(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d(?::\d\d)?)(\.\d+)?\s*(Z|[+-]\d\d:?\d\d)?/.exec(String(text || ""));
  if (!m) return Date.parse(text);
  let zone = m[4] || "";
  if (zone.length === 5) zone = zone.slice(0, 3) + ":" + zone.slice(3);
  return Date.parse(m[1] + "T" + m[2] + (m[3] ? m[3].slice(0, 4) : "") + zone);
}
// "5 min", "3 h", "Nov 8" within this year, else the year.
function shortWhen(ms) {
  const seconds = (Date.now() - ms) / 1000;
  if (seconds < 60) return "now";
  if (seconds < 3600) return Math.floor(seconds / 60) + " min";
  if (seconds < 86400) return Math.floor(seconds / 3600) + " h";
  const date = new Date(ms);
  if (date.getFullYear() === new Date().getFullYear()) return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return String(date.getFullYear());
}
function whenNode(text) {
  const ms = parseModified(text);
  if (!text || !Number.isFinite(ms)) return el("span", { class: "meta when", title: text || "" }, "");
  return el("span", { class: "meta when", title: new Date(ms).toLocaleString() }, shortWhen(ms));
}

function renderListing(m) {
  listedOnce = true;
  currentDir = m.path;
  viewingFolder = m.path;
  reportViewing();
  renderCrumbs(m.path);
  $("fileNote").textContent = "";
  const entries = (m.entries || []).slice().sort((a, b) => (b.dir - a.dir) || String(a.name).localeCompare(String(b.name)));
  const items = entries.map((e) => {
    const target = resolvePath(m.path, e.name);
    const label = e.name + (e.dir ? "/" : "");
    return el("li", null, [
      el("button", { class: "link name", title: label, onclick: () => (e.dir ? listDir(target) : openFile(target, e.size)) }, label),
      el("span", { class: "meta size" }, e.dir ? "" : size(e.size)),
      whenNode(e.modified),
    ]);
  });
  $("entries").replaceChildren(...(items.length ? items : [el("li", { class: "muted" }, "Empty directory")]));
}

// ---- File viewers: text arrives over the socket (read); media is fetched from /pair/file into a blob URL
const EXT_KIND = {};
const kinds = {
  image: "png jpg jpeg gif webp avif bmp ico svg",
  pdf: "pdf",
  audio: "mp3 wav m4a aac ogg oga opus flac",
  video: "mp4 m4v webm mov ogv",
  markdown: "md markdown mdx",
  html: "html htm",
  json: "json geojson ipynb webmanifest",
  csv: "csv",
  tsv: "tsv tab",
  binary: "mid midi zip gz tgz bz2 xz zst tar 7z rar jar whl so dylib dll exe bin o a class pyc wasm sqlite sqlite3 db "
    + "woff woff2 ttf otf eot psd xcf blend fbx glb npy npz pt pth onnx safetensors parquet doc docx xls xlsx ppt pptx key pages numbers dmg iso",
};
for (const kind in kinds) for (const ext of kinds[kind].split(" ")) EXT_KIND[ext] = kind;
const TEXT_KINDS = new Set(["markdown", "html", "json", "csv", "tsv", "text"]);
const WARN_BYTES = 10 * 1024 * 1024;
const TABLE_ROWS = 500;

function fileKind(path) {
  const ext = (/\.([^./]+)$/.exec(basename(path)) || [])[1];
  return EXT_KIND[String(ext || "").toLowerCase()] || "text";
}

// The open viewer; seq invalidates replies and fetches that belong to a file no longer shown.
const viewer = { path: "", size: null, kind: "", seq: 0, url: "", blob: null, abort: null };

function resetViewer(path, knownSize) {
  if (viewer.url) URL.revokeObjectURL(viewer.url);
  if (viewer.abort) viewer.abort.abort();
  Object.assign(viewer, { path, size: knownSize == null ? null : Number(knownSize), kind: fileKind(path), seq: viewer.seq + 1, url: "", blob: null, abort: null });
  renderViewerHead();
  $("viewerNote").textContent = "";
}
function renderViewerHead(extra) {
  const bits = [viewer.size != null ? size(viewer.size) : "", extra || ""].filter(Boolean);
  $("viewerPath").textContent = viewer.path + (bits.length ? " (" + bits.join(", ") + ")" : "");
}
function setViewerBody(node) {
  $("viewerBody").replaceChildren(node);
  $("viewer").hidden = false;
}

function openFile(path, knownSize) {
  showTab("files");
  resetViewer(path, knownSize);
  reportViewing();
  $("fileNote").textContent = "";
  const kind = viewer.kind;
  if (kind === "binary") {
    return downloadCard(/\.midi?$/i.test(path) ? "Browsers cannot play MIDI files." : "This file cannot be shown here.");
  }
  if (TEXT_KINDS.has(kind)) {
    pendingRead = nextId++;
    if (!send({ type: "read", id: pendingRead, path })) return viewerFailed("Not connected.");
    setViewerBody(el("p", { class: "muted" }, "Opening " + basename(path)));
    return;
  }
  if (viewer.size != null && viewer.size > WARN_BYTES) return sizeWarning(viewer.size);
  loadMedia(false);
}

function closeViewer() {
  if (viewer.url) URL.revokeObjectURL(viewer.url);
  if (viewer.abort) viewer.abort.abort();
  Object.assign(viewer, { path: "", size: null, kind: "", seq: viewer.seq + 1, url: "", blob: null, abort: null });
  pendingRead = 0;
  $("viewer").hidden = true;
  $("viewerBody").replaceChildren();
  viewingFolder = "";
  reportViewing();
}

function viewerFailed(message) {
  setViewerBody(el("p", { class: "failure" }, message));
}

// Fetches a file's bytes with the pairing token in a header. limit > 0 stops before reading a larger body.
async function fetchFile(path, limit, controller) {
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (!token) throw new Error("This browser is not paired.");
  const response = await fetch("/pair/file?path=" + encodeURIComponent(path), {
    headers: { authorization: "Bearer " + token }, signal: controller.signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error ? String(body.error) : "The server answered " + response.status + ".");
  }
  const length = Number(response.headers.get("content-length") || 0);
  if (limit && length > limit) {
    controller.abort();
    const error = new Error("large");
    error.tooBig = length;
    throw error;
  }
  return response.blob();
}

async function loadMedia(force) {
  const seq = viewer.seq, path = viewer.path, kind = viewer.kind;
  const controller = new AbortController();
  viewer.abort = controller;
  setViewerBody(el("p", { class: "muted" }, "Loading " + basename(path)));
  let blob;
  try {
    blob = await fetchFile(path, force ? 0 : WARN_BYTES, controller);
  } catch (error) {
    if (seq !== viewer.seq) return;
    if (error.tooBig) { viewer.size = error.tooBig; renderViewerHead(); return sizeWarning(error.tooBig); }
    return viewerFailed(error.message);
  }
  if (seq !== viewer.seq) return;
  viewer.abort = null;
  viewer.blob = blob;
  viewer.size = blob.size;
  viewer.url = URL.createObjectURL(blob);
  renderViewerHead();
  setViewerBody(mediaNode(kind, viewer.url, path));
}

function mediaNode(kind, url, path) {
  if (kind === "image") {
    // SVG too goes through <img>, where its scripts never run.
    const wrap = el("div", { class: "img-wrap", title: "Click for actual size" });
    const img = el("img", { src: url, alt: basename(path) });
    img.addEventListener("load", () => { $("viewerNote").textContent = img.naturalWidth + " by " + img.naturalHeight + " pixels. Click the image to toggle actual size."; });
    img.addEventListener("error", () => { $("viewerNote").textContent = "This browser could not decode the image."; });
    wrap.addEventListener("click", () => wrap.classList.toggle("actual"));
    wrap.append(img);
    return wrap;
  }
  if (kind === "pdf") {
    return el("div", null, [
      el("iframe", { class: "pdf", src: url, title: basename(path) }),
      el("p", { class: "small" }, el("a", { href: url, target: "_blank", rel: "noopener" }, "Open in a new tab")),
    ]);
  }
  const media = el(kind, { src: url, controls: true, preload: "metadata", playsinline: kind === "video" });
  media.addEventListener("loadedmetadata", () => {
    const d = media.duration;
    $("viewerNote").textContent = isFinite(d) ? "Duration " + Math.floor(d / 60) + ":" + String(Math.floor(d % 60)).padStart(2, "0")
      + (kind === "video" && media.videoWidth ? ", " + media.videoWidth + " by " + media.videoHeight : "") : "";
  });
  media.addEventListener("error", () => { $("viewerNote").textContent = "This browser cannot play this file. Try Download."; });
  return media;
}

function sizeWarning(bytes) {
  setViewerBody(el("div", { class: "box" }, [
    el("p", null, "This file is " + size(bytes) + ". Loading it may take a while and use mobile data."),
    el("div", { class: "row" }, [
      el("button", { class: "primary", onclick: () => loadMedia(true) }, "Load anyway"),
      el("button", { onclick: () => downloadFile(viewer.path) }, "Download"),
    ]),
  ]));
}

function downloadCard(note) {
  setViewerBody(el("div", { class: "box" }, [
    el("p", null, [el("b", null, basename(viewer.path)), viewer.size != null ? " (" + size(viewer.size) + ")" : ""]),
    note ? el("p", { class: "small muted" }, note) : null,
    el("button", { class: "primary", onclick: () => downloadFile(viewer.path) }, "Download"),
  ]));
}

async function downloadFile(path) {
  if (!path) return;
  $("viewerNote").textContent = "Preparing the download";
  let blob = viewer.path === path ? viewer.blob : null;
  try {
    if (!blob) blob = await fetchFile(path, 0, new AbortController());
  } catch (error) {
    $("viewerNote").textContent = "Download failed: " + error.message;
    return;
  }
  const url = URL.createObjectURL(blob);
  const link = el("a", { href: url, download: basename(path) });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  if (viewer.path === path) $("viewerNote").textContent = "";
}

function readFailed(error) {
  pendingRead = 0;
  if (/binary/i.test(error)) return downloadCard("This looks like a binary file.");
  if (/directory/i.test(error)) { const path = viewer.path; closeViewer(); return listDir(path); }
  viewerFailed("Error: " + error);
}

function renderTextFile(m) {
  pendingRead = 0;
  viewer.size = m.size;
  renderViewerHead(m.truncated ? "only the first part is shown" : "");
  const text = String(m.text || "");
  const kind = viewer.kind;
  if (kind === "markdown") return setViewerBody(markdownBlock(text));
  if (kind === "html") return setViewerBody(htmlBlock(text));
  if (kind === "json") {
    try { return setViewerBody(codeBlock(JSON.stringify(JSON.parse(text), null, 2))); }
    catch (error) { $("viewerNote").textContent = m.truncated ? "Too long to pretty-print; shown as is." : "Not valid JSON; shown as is."; }
  }
  if (kind === "csv" || kind === "tsv") return setViewerBody(tableBlock(text, kind === "tsv" ? "\t" : ","));
  setViewerBody(codeBlock(text));
}

// Source with line numbers (XML, and HTML under its Source toggle, shown as text, never rendered).
function codeBlock(text) {
  const pre = el("pre", { class: "code" });
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  for (const line of lines) pre.append(el("span", { class: "ln" }, line));
  return pre;
}

// ---- HTML reports: rendered in an isolated frame, with a Source toggle.
// The frame is sandboxed with allow-scripts only (never allow-same-origin, -popups, -forms,
// -top-navigation or -modals), so the report runs in an opaque origin: its scripts cannot reach
// this page, the pairing token in sessionStorage, or any storage of ours. The CSP meta below makes
// it self-contained: nothing loads from or reaches the network (fetch, images, styles, fonts, frames).
// Residual risk: CSP cannot stop a script from navigating its own frame to an outside URL (or a
// WebRTC connection, or a DNS prefetch); what such a request could carry is limited to the report's
// own contents, since nothing else is reachable from inside the frame.
const REPORT_CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline' data:; img-src data: blob:; "
  + "font-src data:; media-src data: blob:; base-uri 'none'; form-action 'none'";
// The meta goes before any of the document's own markup (after a leading doctype, if any). The HTML
// parser then opens the html and head elements for it itself, folds a later <html> tag's attributes
// into that element, and ignores a later <head> tag, so the policy is the first thing in head
// whether or not the document has html or head tags, and nothing in the document can precede it.
function isolateHtml(text) {
  const lead = /^﻿?(\s|<!--[\s\S]*?-->)*<!doctype[^>]*>/i.exec(text);
  const doctype = lead ? lead[0] : "<!doctype html>";
  return doctype + "<meta http-equiv=\"Content-Security-Policy\" content=\"" + REPORT_CSP + "\">"
    + (lead ? text.slice(lead[0].length) : text);
}
function htmlBlock(text) {
  const frame = el("iframe", { sandbox: "allow-scripts", referrerpolicy: "no-referrer", title: basename(viewer.path) });
  frame.srcdoc = isolateHtml(text);
  const stage = el("div", { class: "html-stage" }, frame);
  const source = codeBlock(text);
  source.hidden = true;
  const note = el("p", { class: "small muted" }, "Shown isolated: scripts run, but nothing loads from or reaches the network; images and styles must be inline.");
  const toggle = el("button", { class: "mini", "aria-pressed": "false", onclick: () => {
    const showSource = stage.hidden === false;
    stage.hidden = showSource;
    source.hidden = !showSource;
    note.hidden = showSource;
    full.hidden = showSource || !canFull;
    toggle.setAttribute("aria-pressed", String(showSource));
  } }, "Source");
  const canFull = !!(stage.requestFullscreen || stage.webkitRequestFullscreen);
  const full = el("button", { class: "mini", hidden: !canFull, onclick: () => {
    const request = stage.requestFullscreen || stage.webkitRequestFullscreen;
    Promise.resolve(request.call(stage)).catch((error) => { $("viewerNote").textContent = "Full screen failed: " + error.message; });
  } }, "Full screen");
  return el("div", null, [el("div", { class: "html-bar" }, [note, toggle, full]), stage, source]);
}

// Quote-aware CSV/TSV parsing, stopping after max rows.
function parseDelimited(text, sep, max) {
  const rows = [];
  let row = [], field = "", quoted = false, i = 0;
  for (; i < text.length && rows.length < max; i++) {
    const c = text[i];
    if (quoted) {
      if (c !== '"') field += c;
      else if (text[i + 1] === '"') { field += '"'; i++; }
      else quoted = false;
    } else if (c === '"' && field === "") quoted = true;
    else if (c === sep) { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = "";
    } else field += c;
  }
  if (rows.length < max && (field || row.length)) { row.push(field); rows.push(row); }
  return { rows, more: i < text.length };
}
function tableBlock(text, sep) {
  const parsed = parseDelimited(text, sep, TABLE_ROWS + 1);
  const rows = parsed.rows;
  if (!rows.length) return el("p", { class: "muted" }, "Empty file.");
  const head = rows[0], body = rows.slice(1, TABLE_ROWS + 1);
  $("viewerNote").textContent = body.length + " rows" + (parsed.more || rows.length > TABLE_ROWS + 1 ? ", the first " + TABLE_ROWS + " shown" : "")
    + ", " + head.length + " columns.";
  return el("div", { class: "table-wrap" }, el("table", null, [
    el("thead", null, el("tr", null, head.map((cell) => el("th", { scope: "col", title: cell }, cell)))),
    el("tbody", null, body.map((r) => el("tr", null, r.map((cell) => el("td", { title: cell.length > 40 ? cell : null }, cell))))),
  ]));
}

// ---- A small markdown renderer: the source is HTML-escaped first, so no raw HTML survives.
// Blocks: headings, fenced code (with a language label), GitHub-style pipe tables, blockquotes,
// rules, nested lists and paragraphs. A list item's lines are rendered again as blocks, so items
// can hold nested lists, code and quotes; a blockquote's lines likewise.
const FENCE = /^ {0,3}(\x60{3,}|~{3,})(.*)$/;
const FENCE_CLOSE = /^ {0,3}(\x60{3,}|~{3,})\s*$/;
const HEADING = /^ {0,3}(#{1,6})(?:\s+(.*?))?(?:\s+#+)?\s*$/;
const RULE = /^ {0,3}([-*_])(\s*\1){2,}\s*$/;
const QUOTE = /^ {0,3}&gt; ?/;
const LIST_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])(\s+)(.*)$/;
const TABLE_DELIMITER = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
function escapeHtml(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function inlineMarkdown(text) {
  const spans = [];
  const hold = (html) => { spans.push(html); return "\u0000" + (spans.length - 1) + "\u0000"; };
  // Code spans first (a run of n backticks closes on a run of exactly n), then backslash escapes.
  text = text.replace(/(?<!\x60)(\x60+)(?!\x60)([\s\S]*?[^\x60])\1(?!\x60)/g, (_, ticks, code) =>
    hold("<code>" + (/^ .*[^ ].* $/.test(code) ? code.slice(1, -1) : code) + "</code>"));
  text = text.replace(/\\([\\*_\[\]()#+\-.!|~\x60{}])/g, (_, c) => hold(c));
  text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, href) =>
    /^(https?:\/\/|\/|#)/i.test(href) ? "<a href=\"" + href + "\" target=\"_blank\" rel=\"noopener noreferrer\">" + label + "</a>" : label);
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_]+)__/g, "<strong>$1</strong>");
  text = text.replace(/\*([^*\s][^*]*)\*/g, "<em>$1</em>").replace(/(^|[^\w])_([^_\s][^_]*)_(?=[^\w]|$)/g, "$1<em>$2</em>");
  text = text.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  // Twice: an escaped character can sit inside a held link label.
  return text.replace(/\u0000(\d+)\u0000/g, (_, i) => spans[+i]).replace(/\u0000(\d+)\u0000/g, (_, i) => spans[+i]);
}
// Columns of leading whitespace, with tabs to the next multiple of four.
function indentOf(line) {
  let n = 0;
  for (const c of line) { if (c === " ") n++; else if (c === "\t") n += 4 - n % 4; else break; }
  return n;
}
function dedent(line, columns) {
  let n = 0, i = 0;
  while (i < line.length && n < columns && (line[i] === " " || line[i] === "\t")) { n += line[i] === "\t" ? 4 - n % 4 : 1; i++; }
  return line.slice(i);
}

// ---- Pipe tables: a header row, a delimiter row (---, :--, --:, :-:), then body rows that hold a pipe.
// \| is a literal pipe, and a pipe inside a code span does not split the cell.
function splitTableRow(line) {
  let text = line.trim();
  if (text[0] === "|") text = text.slice(1);
  if (text.endsWith("|") && !text.endsWith("\\|")) text = text.slice(0, -1);
  const cells = [];
  let cell = "";
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === "\\" && text[i + 1] === "|") { cell += "\\|"; i++; continue; }
    if (c === "\x60") {
      let n = 1;
      while (text[i + n] === "\x60") n++;
      let end = -1;
      for (let j = i + n; j < text.length && end < 0; j++) {
        if (text[j] !== "\x60") continue;
        let m = 1;
        while (text[j + m] === "\x60") m++;
        if (m === n) end = j; else j += m - 1;
      }
      const stop = end < 0 ? i + n : end + n;
      cell += text.slice(i, stop);
      i = stop - 1;
      continue;
    }
    if (c === "|") { cells.push(cell.trim()); cell = ""; continue; }
    cell += c;
  }
  cells.push(cell.trim());
  // Inside code spans the escape still means a pipe; outside, inlineMarkdown unescapes it.
  return cells.map((cell) => cell.replace(/(\x60+)([\s\S]*?)\1/g, (span) => span.replace(/\\\|/g, "|")));
}
function tableAt(lines, i) {
  const line = lines[i], next = lines[i + 1];
  if (next == null || line.indexOf("|") < 0 || next.indexOf("|") < 0 || !TABLE_DELIMITER.test(next)) return null;
  const head = splitTableRow(line);
  const aligns = splitTableRow(next).map((cell) => {
    const left = cell[0] === ":", right = cell[cell.length - 1] === ":";
    return left && right ? "center" : right ? "right" : left ? "left" : "";
  });
  if (head.length !== aligns.length) return null;
  const rows = [];
  let j = i + 2;
  for (; j < lines.length; j++) {
    const row = lines[j];
    if (!row.trim() || row.indexOf("|") < 0 || FENCE.test(row) || QUOTE.test(row) || HEADING.test(row)) break;
    const cells = splitTableRow(row).slice(0, head.length);
    while (cells.length < head.length) cells.push("");
    rows.push(cells);
  }
  const cell = (tag, text, k) => "<" + tag + (tag === "th" ? " scope=\"col\"" : "")
    + (aligns[k] ? " style=\"text-align:" + aligns[k] + "\"" : "") + ">" + inlineMarkdown(text) + "</" + tag + ">";
  const html = "<div class=\"md-table\"><table><thead><tr>" + head.map((text, k) => cell("th", text, k)).join("") + "</tr></thead>"
    + (rows.length ? "<tbody>" + rows.map((r) => "<tr>" + r.map((text, k) => cell("td", text, k)).join("") + "</tr>").join("") + "</tbody>" : "")
    + "</table></div>";
  return { html, next: j };
}

// ---- Lists: siblings sit within one column of the first marker; anything indented further belongs
// to the current item (nested lists included). A blank line ends the list unless more of it follows.
function listAt(lines, i) {
  const first = LIST_ITEM.exec(lines[i]);
  const ordered = /\d/.test(first[2]);
  const base = indentOf(first[1]);
  const items = [];
  let item = null;
  const sibling = (line) => {
    const m = LIST_ITEM.exec(line);
    return m && indentOf(m[1]) <= base + 1 && /\d/.test(m[2]) === ordered && !RULE.test(line) ? m : null;
  };
  let j = i;
  for (; j < lines.length; j++) {
    const line = lines[j];
    const m = sibling(line);
    if (m) { item = { lines: [m[4]] }; items.push(item); continue; }
    if (!line.trim()) {
      let k = j + 1;
      while (k < lines.length && !lines[k].trim()) k++;
      if (k < lines.length && (sibling(lines[k]) || indentOf(lines[k]) >= base + 2)) { item.lines.push(""); continue; }
      break;
    }
    if (indentOf(line) >= base + 2) { item.lines.push(line); continue; }
    // A lazy continuation of the item's paragraph, unless it starts a block of its own.
    const previous = item.lines[item.lines.length - 1];
    if (previous && previous.trim() && !LIST_ITEM.test(line) && !FENCE.test(line) && !HEADING.test(line) && !RULE.test(line) && !QUOTE.test(line)) {
      item.lines.push(line.trim());
      continue;
    }
    break;
  }
  const tag = ordered ? "ol" : "ul";
  const start = ordered ? parseInt(first[2], 10) : 1;
  const html = "<" + tag + (start !== 1 ? " start=\"" + start + "\"" : "") + ">" + items.map((it) => {
    while (it.lines.length && !it.lines[it.lines.length - 1].trim()) it.lines.pop();
    // The first line has its marker stripped; the rest share their smallest indent.
    const rest = it.lines.slice(1).filter((line) => line.trim());
    const shift = rest.length ? Math.min(...rest.map(indentOf)) : 0;
    const body = [it.lines[0]].concat(it.lines.slice(1).map((line) => dedent(line, shift)));
    // A plain first paragraph sits in the item without a paragraph margin.
    return "<li>" + renderBlocks(body).replace(/^<p>([\s\S]*?)<\/p>\n?/, "$1") + "</li>";
  }).join("") + "</" + tag + ">";
  return { html, next: j };
}

function renderBlocks(lines) {
  const out = [];
  let paragraph = [];
  const flush = () => {
    if (!paragraph.length) return;
    // Two trailing spaces or a trailing backslash break the line.
    const joined = paragraph.map((line, k) => {
      const last = k === paragraph.length - 1;
      const text = line.trim();
      if (!last && / {2,}$/.test(line)) return text + "\u0001";
      if (!last && /\\$/.test(text)) return text.slice(0, -1) + "\u0001";
      return text + (last ? "" : " ");
    }).join("");
    out.push("<p>" + inlineMarkdown(joined).replace(/\u0001/g, "<br>") + "</p>");
    paragraph = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim()) { flush(); continue; }
    const fence = FENCE.exec(line);
    if (fence && !(fence[1][0] === "\x60" && fence[2].indexOf("\x60") >= 0)) {
      flush();
      const marker = fence[1];
      const language = (/^[\w+#.-]+/.exec(fence[2].trim()) || [""])[0];
      const code = [];
      for (i++; i < lines.length; i++) {
        const close = FENCE_CLOSE.exec(lines[i]);
        if (close && close[1][0] === marker[0] && close[1].length >= marker.length) break;
        code.push(lines[i]);
      }
      const pre = "<pre><code" + (language ? " class=\"language-" + language + "\"" : "") + ">" + code.join("\n") + "</code></pre>";
      out.push(language ? "<div class=\"md-code\"><span class=\"md-lang\">" + language + "</span>" + pre + "</div>" : pre);
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      const level = heading[1].length;
      out.push("<h" + level + ">" + inlineMarkdown(heading[2] || "") + "</h" + level + ">");
      continue;
    }
    const table = tableAt(lines, i);
    if (table) { flush(); out.push(table.html); i = table.next - 1; continue; }
    if (RULE.test(line)) { flush(); out.push("<hr>"); continue; }
    if (QUOTE.test(line)) {
      flush();
      const quoted = [];
      for (; i < lines.length && QUOTE.test(lines[i]); i++) quoted.push(lines[i].replace(QUOTE, ""));
      i--;
      out.push("<blockquote>" + renderBlocks(quoted) + "</blockquote>");
      continue;
    }
    if (LIST_ITEM.test(line)) {
      flush();
      const list = listAt(lines, i);
      out.push(list.html);
      i = list.next - 1;
      continue;
    }
    paragraph.push(line);
  }
  flush();
  return out.join("\n");
}
function renderMarkdown(source) {
  return renderBlocks(escapeHtml(source).replace(/\r\n?/g, "\n").split("\n"));
}
function markdownBlock(source) {
  const box = el("div", { class: "md" });
  box.innerHTML = renderMarkdown(source); // escaped above: only the renderer's own tags are HTML
  return box;
}

// ==== HANDS START: launchers for a terminal and a desktop into the sandbox ====
// Each opens in its own full-screen browser tab (DOMAIN.md, Human Takeover).
// Protocol: the page sends {"type":"launch","kind":"terminal"|"desktop","id":n}; the server answers
// {"type":"launch","id":n,"url":"..."} or {"type":"error","id":n,"error":"..."} (onMessage routes both here).
const launches = new Map(); // id -> { tab, kind }

// Must run synchronously inside the click handler: a blank tab opened now is allowed by popup
// blockers, and gets its real address when the reply arrives.
function launch(kind) {
  const id = nextId++;
  const tab = window.open("about:blank", "_blank");
  if (!tab) { $("handsNote").textContent = "The browser blocked the new tab. Allow pop-ups for this site and try again."; return; }
  if (!send({ type: "launch", kind, id })) {
    tab.close();
    $("handsNote").textContent = "Not connected.";
    return;
  }
  launches.set(id, { tab, kind });
  $("handsNote").textContent = "Opening the " + kind;
}
function launchTerminal() { launch("terminal"); }
function launchDesktop() { launch("desktop"); }

function settleLaunch(m) {
  const pending = launches.get(m.id);
  if (!pending) return;
  launches.delete(m.id);
  if (m.type === "launch" && typeof m.url === "string" && /^https:\/\//.test(m.url)) {
    pending.tab.opener = null;
    pending.tab.location.href = m.url;
    $("handsNote").textContent = "";
    return;
  }
  pending.tab.close();
  $("handsNote").textContent = "Could not open the " + pending.kind + ": " + String(m.error || "no address came back");
}
$("handsTerminal").addEventListener("click", launchTerminal);
$("handsDesktop").addEventListener("click", launchDesktop);
// ==== HANDS END ====

// ==== WHITEBOARD START: boards the walker draws in openui-lang, answered on this page ====
// Protocol: the server sends {"type":"board","board":{"id","at","program","note","status":"open"|"answered","answer"}}
// for a new or updated board, and {"type":"boards","boards":[...]} (newest last) after the feed snapshot.
// The page answers {"type":"board_answer","id","answer":{"button","value","values","decisions","note"}}
// and reports, once per board, a program it could not fully draw: {"type":"board_error","id","error"}.
// An answer is the caller's input to the walker, like keypad digits; it never authorizes anything.
// The dialect is OpenChamber's openui-lang (voice_agent_skills/whiteboard.md): one "name = Component(args)"
// statement per line, positional arguments, root = Stack([...]). Upstream extras kept because they are
// cheap: null skips an optional argument, the last definition of a name wins, a call may sit inline,
// and // starts a comment. Not supported: $state, @functions, Query, Mutation, operators.

// Boards kept on the page. 1: each new board replaces the last. More: older boards stack, collapsed, beneath the newest.
const WB_KEEP = 5;

// ---- Tokens: a newline ends a statement only outside brackets, so a long call may wrap, unless the
// next line starts a new statement ("name ="): then an unclosed bracket or string above is the error.
const WB_IDENT = /[A-Za-z_][A-Za-z0-9_]*/y;
const WB_NUMBER = /-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/y;
const WB_STATEMENT = /[ \t]*[A-Za-z_][A-Za-z0-9_]*[ \t]*=/y;
function wbTokens(source) {
  const tokens = [];
  let i = 0, line = 1, depth = 0;
  const push = (t, v) => tokens.push({ t, v, line });
  while (i < source.length) {
    const c = source[i];
    if (c === "\n") {
      WB_STATEMENT.lastIndex = i + 1;
      if (depth && WB_STATEMENT.test(source)) { push("bad", "a bracket that was never closed"); depth = 0; }
      if (!depth) push("nl");
      line++;
      i++;
      continue;
    }
    if (c === " " || c === "\t" || c === "\r" || c === " ") { i++; continue; }
    if (c === "/" && source[i + 1] === "/") { while (i < source.length && source[i] !== "\n") i++; continue; }
    if (c === "\"" || c === "'") {
      let j = i + 1, text = "", closed = false;
      while (j < source.length && source[j] !== "\n") {
        const d = source[j];
        if (d === c) { closed = true; j++; break; }
        if (d === "\\" && j + 1 < source.length) {
          const e = source[j + 1], hex = source.slice(j + 2, j + 6);
          if (e === "u" && /^[0-9a-fA-F]{4}$/.test(hex)) { text += String.fromCharCode(parseInt(hex, 16)); j += 6; continue; }
          text += e === "n" ? "\n" : e === "t" ? "\t" : e === "r" ? "" : e; // \" \\ \' and the rest stand for themselves
          j += 2;
          continue;
        }
        text += d;
        j++;
      }
      push(closed ? "str" : "bad", closed ? text : "a string with no closing quote (write a line break inside a string as \\n)");
      i = j;
      continue;
    }
    if (/[0-9.]/.test(c) || (c === "-" && /[0-9.]/.test(source[i + 1] || ""))) {
      WB_NUMBER.lastIndex = i;
      const m = WB_NUMBER.exec(source);
      if (m) { push("num", Number(m[0])); i += m[0].length; continue; }
    }
    if (/[A-Za-z_]/.test(c)) {
      WB_IDENT.lastIndex = i;
      const m = WB_IDENT.exec(source);
      push("id", m[0]);
      i += m[0].length;
      continue;
    }
    if ("([{".indexOf(c) >= 0) depth++;
    if (")]}".indexOf(c) >= 0) depth = Math.max(0, depth - 1);
    push("([{}]),:=".indexOf(c) >= 0 ? c : "bad", "the character " + JSON.stringify(c)
      + (c === "$" || c === "@" ? " (state and functions are not supported)" : ""));
    i++;
  }
  push("eof");
  return tokens;
}

// A program may arrive wrapped in a Markdown fence or <openui-lang> tags; drop the wrapper.
function wbUnfence(source) {
  const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
  const wrapper = /^\s*(\x60{3,}|~{3,}|<\/?openui-lang>)/;
  while (lines.length && !lines[0].trim()) lines.shift();
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  if (lines.length && wrapper.test(lines[0])) lines[0] = "";
  if (lines.length && wrapper.test(lines[lines.length - 1])) lines[lines.length - 1] = "";
  return lines.join("\n");
}

// ---- Statements: name = Expression, one per line. A broken statement is reported and skipped.
function wbParse(source) {
  const tokens = wbTokens(wbUnfence(source));
  const statements = new Map(); // name -> { expr, line }; the last definition wins
  const order = [];
  const errors = [];
  let k = 0;
  const peek = () => tokens[k];
  const describe = (tok) => tok.t === "nl" ? "the end of the line" : tok.t === "eof" ? "the end of the program"
    : tok.t === "bad" ? tok.v : tok.t === "str" ? "the string " + JSON.stringify(tok.v.slice(0, 30))
    : tok.t === "num" ? "the number " + tok.v : tok.t === "id" ? tok.v : JSON.stringify(tok.t);
  const fail = (message) => { throw new Error("line " + peek().line + ": " + message); };
  const expect = (t, what) => { if (peek().t !== t) fail("expected " + what + " but found " + describe(peek())); return tokens[k++]; };
  function list(close) {
    const items = [];
    while (peek().t !== close) {
      items.push(expr());
      if (peek().t === ",") k++;
      else if (peek().t !== close) fail("expected a comma or " + close + " but found " + describe(peek()));
    }
    k++;
    return items;
  }
  function expr() {
    const tok = peek();
    if (tok.t === "str" || tok.t === "num") { k++; return { k: "lit", v: tok.v }; }
    if (tok.t === "[") { k++; return { k: "arr", items: list("]") }; }
    if (tok.t === "{") {
      k++;
      const entries = [];
      while (peek().t !== "}") {
        const key = peek();
        if (key.t !== "id" && key.t !== "str") fail("expected a key but found " + describe(key));
        k++;
        expect(":", "a colon after the key " + key.v);
        entries.push([key.v, expr()]);
        if (peek().t === ",") k++;
        else if (peek().t !== "}") fail("expected a comma or } but found " + describe(peek()));
      }
      k++;
      return { k: "obj", entries };
    }
    if (tok.t === "id") {
      k++;
      if (tok.v === "true" || tok.v === "false") return { k: "lit", v: tok.v === "true" };
      if (tok.v === "null") return { k: "lit", v: null };
      if (peek().t === "(") { k++; return { k: "call", name: tok.v, args: list(")"), line: tok.line }; }
      return { k: "ref", name: tok.v, line: tok.line };
    }
    fail("expected a value but found " + describe(tok));
  }
  while (peek().t !== "eof") {
    if (peek().t === "nl") { k++; continue; }
    try {
      const name = expect("id", "a statement such as root = Stack([...])");
      expect("=", "= after " + name.v);
      const value = expr();
      if (peek().t !== "nl" && peek().t !== "eof") fail("expected the end of the statement " + name.v + " but found " + describe(peek()) + " (one statement per line)");
      if (!statements.has(name.v)) order.push(name.v);
      statements.set(name.v, { expr: value, line: name.line });
    } catch (error) {
      errors.push(error.message);
      while (peek().t !== "nl" && peek().t !== "eof") k++;
    }
  }
  return { statements, order, errors };
}

// ---- Components: positional arguments in this order; [prop, kind, required].
const WB_SPECS = {
  Stack: [["children", "nodes", 1]],
  Row: [["children", "nodes", 1]],
  Card: [["title", "str", 1], ["children", "nodes", 1]],
  Tabs: [["tabs", "nodes", 1]],
  Tab: [["label", "str", 1], ["children", "nodes", 1]],
  Details: [["title", "str", 1], ["children", "nodes", 1]],
  Text: [["markdown", "str", 1]],
  Badge: [["label", "str", 1], ["tone", "tone"]],
  Callout: [["markdown", "str", 1], ["tone", "tone"], ["title", "str"]],
  KeyValue: [["items", "pairs", 1]],
  Table: [["columns", "strs", 1], ["rows", "rows", 1]],
  Stat: [["label", "str", 1], ["value", "strnum", 1], ["delta", "str"], ["tone", "tone"]],
  Progress: [["label", "str", 1], ["value", "num", 1], ["max", "pos"]],
  Steps: [["items", "steps", 1], ["direction", "direction"]],
  BarChart: [["labels", "strs", 1], ["series", "series", 1], ["unit", "str"], ["whole", "bool"]],
  LineChart: [["labels", "strs", 1], ["series", "series", 1], ["unit", "str"]],
  Series: [["name", "str", 1], ["values", "nums", 1]],
  Choice: [["name", "str", 1], ["options", "options", 1], ["label", "str"], ["multiple", "bool"]],
  Input: [["name", "str", 1], ["label", "str"], ["placeholder", "str"], ["multiline", "bool"]],
  DateInput: [["name", "str", 1], ["label", "str"], ["time", "bool"], ["value", "date"]],
  Slider: [["name", "str", 1], ["min", "num", 1], ["max", "num", 1], ["label", "str"], ["step", "pos"], ["value", "num"], ["unit", "str"]],
  Button: [["label", "str", 1], ["variant", "variant"], ["value", "str"]],
};
// What may not sit anywhere inside each container; Tabs holds only Tab, and Series sits only in a chart.
const WB_FORBIDS = { Card: ["Card", "Tabs"], Row: ["Row", "Tabs", "Details"], Tab: ["Tabs"], Details: ["Card", "Tabs", "Details"] };
class WbNode {
  constructor(type, props, line) { this.type = type; this.props = props; this.line = line; }
}
const wbEnum = (choices) => (v, problems) => choices.indexOf(v) >= 0 ? v : (problems.push("must be one of " + choices.join(", ")), undefined);
const wbStr = (v) => typeof v === "string" ? v : typeof v === "number" ? String(v) : undefined;
const wbNum = (v) => typeof v === "number" && isFinite(v) ? v : typeof v === "string" && v.trim() && isFinite(+v) ? +v : undefined;
const wbIsObject = (v) => v != null && typeof v === "object" && !Array.isArray(v) && !(v instanceof WbNode);
// Each kind returns the cleaned value, or undefined when it is unusable; problems collects what to report.
function wbList(v, problems, what, item) {
  if (!Array.isArray(v)) { problems.push("must be a list of " + what); return undefined; }
  const out = [];
  for (const x of v) {
    const y = item(x);
    if (y === undefined) problems.push("has an entry that is not " + what.replace(/s$/, "") + " (left out)");
    else out.push(y);
  }
  return out;
}
const WB_KINDS = {
  str: (v, p) => { const s = wbStr(v); if (s === undefined) p.push("must be a string"); return s; },
  num: (v, p) => { const n = wbNum(v); if (n === undefined) p.push("must be a number"); return n; },
  pos: (v, p) => { const n = wbNum(v); if (!(n > 0)) { p.push("must be a positive number"); return undefined; } return n; },
  bool: (v, p) => typeof v === "boolean" ? v : (p.push("must be true or false"), undefined),
  strnum: (v, p) => typeof v === "string" || typeof v === "number" ? v : (p.push("must be a string or a number"), undefined),
  tone: wbEnum(["neutral", "info", "success", "warning", "error"]),
  direction: wbEnum(["column", "row"]),
  variant: wbEnum(["primary", "secondary"]),
  date: (v, p) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$/.test(v) ? v : (p.push("must look like 2026-11-08 or 2026-11-08T14:30"), undefined),
  nodes: (v, p) => wbList(v, p, "components", (x) => x instanceof WbNode ? x : undefined),
  strs: (v, p) => wbList(v, p, "strings", wbStr),
  nums: (v, p) => wbList(v, p, "numbers or nulls", (x) => x === null ? null : wbNum(x)),
  rows: (v, p) => wbList(v, p, "rows (lists of strings or numbers)", (row) => Array.isArray(row) && row.every((c) => typeof c === "string" || typeof c === "number") ? row : undefined),
  pairs: (v, p) => wbList(v, p, "{label, value} objects", (x) => wbIsObject(x) && wbStr(x.label) !== undefined && (typeof x.value === "string" || typeof x.value === "number")
    ? { label: wbStr(x.label), value: x.value } : undefined),
  steps: (v, p) => wbList(v, p, "{title, markdown?, status?} objects", (x) => wbIsObject(x) && wbStr(x.title) !== undefined
    && (x.markdown == null || typeof x.markdown === "string") && (x.status == null || ["done", "current", "todo", "blocked"].indexOf(x.status) >= 0)
    ? { title: wbStr(x.title), markdown: x.markdown || "", status: x.status || "todo" } : undefined),
  options: (v, p) => wbList(v, p, "options (strings or {label, description?} objects)", (x) => wbStr(x) !== undefined ? { label: wbStr(x), description: "" }
    : wbIsObject(x) && wbStr(x.label) !== undefined ? { label: wbStr(x.label), description: wbStr(x.description) || "" } : undefined),
  series: (v, p) => wbList(v, p, "Series(...) components", (x) => x instanceof WbNode && x.type === "Series" ? x : undefined),
};

function wbComponent(name, args, line, errors) {
  const at = "line " + line + ": ";
  if (!Object.prototype.hasOwnProperty.call(WB_SPECS, name)) {
    errors.push(at + "unknown component " + name + " (left out; the components are " + Object.keys(WB_SPECS).join(", ") + ")");
    return undefined;
  }
  const spec = WB_SPECS[name];
  if (args.length > spec.length) {
    errors.push(at + name + " takes at most " + spec.length + " arguments (" + spec.map((s) => s[0]).join(", ") + "); the extra ones were left out");
  }
  const props = {};
  for (let i = 0; i < spec.length; i++) {
    const [prop, kind, required] = spec[i];
    const raw = args[i];
    if (raw == null) {
      if (required) { errors.push(at + name + " needs " + prop + " as argument " + (i + 1) + " (left out)"); return undefined; }
      continue;
    }
    const problems = [];
    const value = WB_KINDS[kind](raw, problems);
    for (const problem of problems) errors.push(at + name + " " + prop + " " + problem);
    if (value === undefined) {
      if (required) return undefined;
      continue;
    }
    props[prop] = value;
  }
  if (name === "Slider" && !(props.max > props.min)) { errors.push(at + "Slider max must be greater than min (left out)"); return undefined; }
  return new WbNode(name, props, line);
}

// ---- A program: statements resolved from root, with references, cycles and unused names reported.
function wbProgram(source) {
  const parsed = wbParse(source);
  const errors = parsed.errors.slice();
  const values = new Map(), visiting = new Set();
  function resolve(name, line) {
    if (values.has(name)) return values.get(name);
    const statement = parsed.statements.get(name);
    if (!statement) { errors.push("line " + line + ": " + name + " is not defined"); return undefined; }
    if (visiting.has(name)) { errors.push("line " + statement.line + ": " + name + " contains itself"); return undefined; }
    visiting.add(name);
    const value = evaluate(statement.expr);
    visiting.delete(name);
    values.set(name, value);
    return value;
  }
  function evaluate(e) {
    if (e.k === "lit") return e.v;
    if (e.k === "arr") return e.items.map(evaluate).filter((v) => v !== undefined);
    if (e.k === "ref") return resolve(e.name, e.line);
    if (e.k === "call") return wbComponent(e.name, e.args.map(evaluate), e.line, errors);
    const o = Object.create(null);
    for (const [key, v] of e.entries) o[key] = evaluate(v);
    return o;
  }
  // Without a root statement, the first Stack is the root (as upstream does).
  const rootName = parsed.statements.has("root") ? "root"
    : parsed.order.find((n) => parsed.statements.get(n).expr.k === "call" && parsed.statements.get(n).expr.name === "Stack");
  if (!rootName) {
    errors.push(parsed.statements.size ? "no root: the first line should be root = Stack([...])" : "the program has no statements");
    return { root: null, errors };
  }
  const root = resolve(rootName, parsed.statements.get(rootName).line);
  if (root !== undefined && !(root instanceof WbNode)) errors.push("root must be a component, such as root = Stack([...])");
  for (const name of parsed.order) {
    if (!values.has(name) && name !== rootName) errors.push("line " + parsed.statements.get(name).line + ": " + name + " is defined but root never reaches it, so it was not drawn");
  }
  return { root: root instanceof WbNode ? root : null, errors };
}

// ---- Rendering. Markdown goes through the page's escaping renderer; every other string becomes a text node.
const SVG_NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs, children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const key in attrs || {}) if (attrs[key] != null) node.setAttribute(key, String(attrs[key]));
  for (const child of [].concat(children == null ? [] : children)) if (child != null) node.append(child instanceof Node ? child : String(child));
  return node;
}
function wbInline(tag, cls, text) {
  const node = el(tag, cls ? { class: cls } : null);
  node.innerHTML = inlineMarkdown(escapeHtml(text)); // escaped first: only the renderer's own tags are HTML
  return node;
}
function wbMd(source) {
  const box = markdownBlock(source);
  box.classList.add("wb-md");
  return box;
}
let wbUid = 0;

function wbRender(node, ancestors, ctx) {
  if (!(node instanceof WbNode)) return null;
  const parent = ancestors[ancestors.length - 1];
  const misplaced = (node.type === "Tab" && parent !== "Tabs") || (parent === "Tabs" && node.type !== "Tab") || node.type === "Series"
    ? (parent || "the root") + " cannot hold " + node.type
    : ancestors.filter((a) => (WB_FORBIDS[a] || []).indexOf(node.type) >= 0).map((a) => a + " cannot hold " + node.type)[0];
  if (misplaced) ctx.errors.push("line " + node.line + ": " + misplaced + " (drawn anyway)");
  const inner = ancestors.concat([node.type]);
  const kids = (list) => list.map((child) => wbRender(child, inner, ctx));
  const p = node.props;
  switch (node.type) {
    case "Stack": return el("div", { class: "wb-stack" }, kids(p.children));
    case "Row": return el("div", { class: "wb-row" }, kids(p.children));
    case "Card": {
      const head = el("div", { class: "wb-card-head" }, [p.title ? wbInline("h3", "", p.title) : null, el("span", { class: "wb-decision", hidden: true })]);
      return el("section", { class: "wb-card" }, [head].concat(kids(p.children)));
    }
    case "Tab": return el("div", { class: "wb-stack", role: "tabpanel" }, kids(p.children));
    case "Tabs": {
      const panels = kids(p.tabs);
      const buttons = p.tabs.map((tab, i) => el("button", { type: "button", role: "tab", onclick: () => pick(i) }, tab.props.label || tab.type));
      const pick = (i) => {
        buttons.forEach((b, j) => { b.setAttribute("aria-selected", String(i === j)); b.setAttribute("aria-pressed", String(i === j)); });
        panels.forEach((panel, j) => { if (panel) panel.hidden = i !== j; });
      };
      pick(0);
      return el("div", { class: "wb-tabs" }, [el("div", { class: "segmented", role: "tablist" }, buttons)].concat(panels));
    }
    case "Details": return el("details", { class: "wb-details" }, [wbInline("summary", "", p.title), el("div", { class: "wb-stack" }, kids(p.children))]);
    case "Text": return wbMd(p.markdown);
    case "Badge": return el("span", { class: "wb-badge tone-" + (p.tone || "neutral") }, p.label);
    case "Callout": return el("div", { class: "wb-callout tone-" + (p.tone || "info") }, [p.title ? el("strong", null, p.title) : null, wbMd(p.markdown)]);
    case "KeyValue": return el("dl", { class: "wb-kv" }, [].concat(...p.items.map((it) => [el("dt", null, it.label), wbInline("dd", "", String(it.value))])));
    case "Table": return el("div", { class: "md-table" }, el("table", null, [
      el("thead", null, el("tr", null, p.columns.map((c) => wbInline("th", "", c)))),
      el("tbody", null, p.rows.map((row) => el("tr", null, p.columns.map((_, i) => {
        const cell = row[i] == null ? "" : row[i];
        return wbInline("td", typeof cell === "number" ? "num" : "", String(cell));
      })))),
    ]));
    case "Stat": return el("div", { class: "wb-stat tone-" + (p.tone || "neutral") }, [
      el("span", { class: "label" }, p.label), el("span", { class: "value" }, String(p.value)), p.delta ? el("span", { class: "delta" }, p.delta) : null]);
    case "Progress": {
      const max = p.max || 100;
      const pct = Math.max(0, Math.min(100, p.value / max * 100));
      return el("div", { class: "wb-progress" }, [
        el("div", { class: "top" }, [el("span", null, p.label), el("span", { class: "small muted mono" }, (p.max ? p.value + " of " + p.max + ", " : "") + Math.round(pct) + "%")]),
        el("div", { class: "bar", role: "progressbar", "aria-label": p.label, "aria-valuemin": 0, "aria-valuemax": max, "aria-valuenow": p.value }, el("div", { style: "width:" + pct + "%" })),
      ]);
    }
    case "Steps": return el("ol", { class: "wb-steps " + (p.direction === "row" ? "row" : "column") }, p.items.map((it) => el("li", { class: it.status }, [
      el("span", { class: "mark", title: it.status }), el("span", { class: "offscreen" }, it.status + ": "),
      el("div", null, [wbInline("div", "title", it.title), it.markdown ? wbMd(it.markdown) : null]),
    ])));
    case "BarChart": case "LineChart": return wbChart(node, ctx);
    case "Choice": {
      const group = "wb" + (++wbUid);
      const inputs = p.options.map((o) => el("input", { type: p.multiple ? "checkbox" : "radio", name: group, value: o.label }));
      ctx.field(p.name, node.line, inputs,
        () => p.multiple ? inputs.filter((x) => x.checked).map((x) => x.value) : (inputs.find((x) => x.checked) || { value: null }).value,
        (v) => { const want = [].concat(v == null ? [] : v); for (const x of inputs) x.checked = want.indexOf(x.value) >= 0; });
      return el("fieldset", { class: "wb-choice" }, [
        el("legend", null, p.label || p.name),
        el("div", { class: "wb-options" }, p.options.map((o, i) => el("label", { class: "wb-option" }, [
          inputs[i], el("span", null, [el("span", null, o.label), o.description ? el("span", { class: "wb-opt-desc" }, o.description) : null])]))),
      ]);
    }
    case "Input": {
      const input = el(p.multiline ? "textarea" : "input", { class: "wb-input", type: p.multiline ? null : "text", rows: p.multiline ? 4 : null, placeholder: p.placeholder || null });
      ctx.field(p.name, node.line, [input], () => input.value, (v) => { input.value = v == null ? "" : String(v); });
      return el("label", { class: "wb-field" }, [el("span", null, p.label || p.name), input]);
    }
    case "DateInput": {
      const input = el("input", { class: "wb-input", type: p.time ? "datetime-local" : "date" });
      if (p.value) input.value = p.time || p.value.length <= 10 ? p.value : p.value.slice(0, 10);
      ctx.field(p.name, node.line, [input], () => input.value || null, (v) => { input.value = v == null ? "" : String(v); });
      return el("label", { class: "wb-field" }, [el("span", null, p.label || p.name), input]);
    }
    case "Slider": {
      const input = el("input", { type: "range", min: p.min, max: p.max, step: p.step || "any", "aria-label": p.label || p.name });
      input.value = String(p.value != null ? p.value : p.min);
      const output = el("output");
      const show = () => { output.textContent = wbFormat(Number(input.value), p.unit); };
      input.addEventListener("input", show);
      show();
      ctx.field(p.name, node.line, [input], () => Number(input.value), (v) => { if (v != null) input.value = String(v); show(); });
      return el("div", { class: "wb-field" }, [el("span", null, p.label || p.name), el("div", { class: "wb-slider" }, [input, output])]);
    }
    case "Button": {
      const button = el("button", { type: "button", class: "wb-button" + (p.variant === "primary" ? " primary" : "") }, p.label);
      const value = p.value ? p.value : null;
      ctx.buttons.push({ node: button, label: p.label, value });
      if (value != null && ctx.items.indexOf(value) < 0) ctx.items.push(value);
      return button;
    }
  }
  return el("span");
}

// ---- Charts: inline SVG drawn at the width it gets, with a legend and the numbers as a table.
function wbFormat(v, unit) {
  if (v == null) return "no data";
  const n = Number(v.toPrecision(12));
  const text = Math.abs(n) >= 10000 ? n.toLocaleString() : String(Math.round(n * 100) / 100);
  if (!unit) return text;
  if (/^[$£€¥]$/.test(unit)) return unit + text;
  return text + (unit === "%" ? "" : " ") + unit;
}
function wbScale(values, zero) {
  let lo = values.length ? Math.min(...values) : 0, hi = values.length ? Math.max(...values) : 1;
  if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  if (hi === lo) { const pad = Math.abs(hi) / 10 || 1; hi += pad; if (!zero || lo !== 0) lo -= pad; }
  const raw = (hi - lo) / 4, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * mag;
  const scale = { lo: Math.floor(lo / step + 1e-9) * step, hi: Math.ceil(hi / step - 1e-9) * step, ticks: [] };
  for (let v = scale.lo; v <= scale.hi + step / 2; v += step) scale.ticks.push(Number(v.toPrecision(12)));
  return scale;
}
const wbColor = (i) => "var(--wb-" + (i % 6 + 1) + ")";
function wbClip(text, chars) { return text.length > chars ? text.slice(0, Math.max(1, chars - 1)) + "…" : text; }
// On the axis and bars, % and currency stay beside each number; a word unit is said once, above the axis.
const wbShortUnit = (unit) => /^[%$£€¥]$/.test(unit || "") ? unit : "";
// The y axis and its gridlines, and the plot margins its labels need.
function wbFrame(W, H, values, zero, unit) {
  const scale = wbScale(values, zero), short = wbShortUnit(unit);
  const m = { l: 10 + 6.5 * Math.max(...scale.ticks.map((t) => wbFormat(t, short).length)), r: 12, t: unit && !short ? 22 : 16, b: 34 };
  const y = (v) => m.t + (H - m.t - m.b) * (1 - (v - scale.lo) / (scale.hi - scale.lo));
  const parts = unit && !short ? [svg("text", { x: 2, y: 11 }, unit)] : [];
  for (const t of scale.ticks) {
    parts.push(svg("line", { class: "grid", x1: m.l, x2: W - m.r, y1: y(t), y2: y(t) }));
    parts.push(svg("text", { x: m.l - 6, y: y(t) + 4, "text-anchor": "end" }, wbFormat(t, short)));
  }
  return { m, y, parts, scale };
}
function wbXLabels(labels, xAt, yPos, slot) {
  const every = Math.max(1, Math.ceil(36 / Math.max(1, slot)));
  const chars = Math.max(3, Math.floor(slot * every / 6.5));
  return labels.map((label, i) => i % every ? null
    : svg("text", { x: xAt(i), y: yPos, "text-anchor": "middle" }, [wbClip(label, chars), svg("title", null, label)]));
}
function wbBarSvg(labels, series, unit, W, at) {
  const H = 240;
  const values = [];
  series.forEach((s) => labels.forEach((_, i) => { if (at(s, i) != null) values.push(at(s, i)); }));
  const f = wbFrame(W, H, values, true, unit), m = f.m, y = f.y, parts = f.parts;
  const n = Math.max(1, labels.length), gw = (W - m.l - m.r) / n, bw = Math.min(44, gw * 0.8 / Math.max(1, series.length));
  labels.forEach((label, i) => {
    const x0 = m.l + gw * i + (gw - bw * series.length) / 2;
    series.forEach((s, k) => {
      const v = at(s, i);
      if (v == null) return;
      const top = y(Math.max(v, 0)), bottom = y(Math.min(v, 0));
      parts.push(svg("rect", { x: x0 + k * bw + 1, y: top, width: Math.max(1, bw - 2), height: Math.max(1, bottom - top), rx: 2, style: "fill:" + wbColor(k) },
        svg("title", null, label + ", " + s.props.name + ": " + wbFormat(v, unit))));
      if (bw >= 22 && n * series.length <= 16) parts.push(svg("text", { class: "val", x: x0 + k * bw + bw / 2, y: v >= 0 ? top - 4 : bottom + 12, "text-anchor": "middle" }, wbFormat(v, wbShortUnit(unit))));
    });
  });
  parts.push(svg("line", { class: "axis", x1: m.l, x2: W - m.r, y1: y(0), y2: y(0) }));
  return { H, parts: parts.concat(wbXLabels(labels, (i) => m.l + gw * (i + 0.5), H - m.b + 18, gw)) };
}
function wbLineSvg(labels, series, unit, W, at) {
  const H = 240;
  const values = [];
  series.forEach((s) => labels.forEach((_, i) => { if (at(s, i) != null) values.push(at(s, i)); }));
  const f = wbFrame(W, H, values, false, unit), m = f.m, y = f.y, parts = f.parts;
  m.l += 8; m.r += 8;
  const n = labels.length, slot = (W - m.l - m.r) / Math.max(1, n - 1);
  const x = (i) => n <= 1 ? (m.l + W - m.r) / 2 : m.l + slot * i;
  series.forEach((s, k) => {
    // A null breaks the line; one series also fills the area beneath it.
    const runs = [[]];
    labels.forEach((_, i) => { const v = at(s, i); if (v == null) runs.push([]); else runs[runs.length - 1].push([x(i), y(v)]); });
    for (const run of runs.filter((r) => r.length)) {
      const path = run.map((pt, j) => (j ? "L" : "M") + pt[0].toFixed(1) + " " + pt[1].toFixed(1)).join(" ");
      if (series.length === 1) parts.push(svg("path", { d: path + " L" + run[run.length - 1][0].toFixed(1) + " " + y(f.scale.lo) + " L" + run[0][0].toFixed(1) + " " + y(f.scale.lo) + " Z", style: "fill:" + wbColor(k) + ";opacity:.15" }));
      parts.push(svg("path", { d: path, style: "fill:none;stroke-width:2.5;stroke-linejoin:round;stroke:" + wbColor(k) }));
    }
    labels.forEach((label, i) => {
      const v = at(s, i);
      if (v != null) parts.push(svg("circle", { cx: x(i), cy: y(v), r: n > 40 ? 2 : 3.5, style: "fill:" + wbColor(k) }, svg("title", null, label + ", " + s.props.name + ": " + wbFormat(v, unit))));
    });
  });
  return { H, parts: parts.concat(wbXLabels(labels, x, H - m.b + 18, n <= 1 ? W : slot)) };
}
// whole: one series of parts adding up to a whole, as one stacked bar with shares.
function wbWholeSvg(labels, s, unit, W, at) {
  const H = 44;
  const values = labels.map((_, i) => Math.max(0, at(s, i) || 0));
  const total = values.reduce((a, b) => a + b, 0) || 1;
  const parts = [];
  let x = 0;
  values.forEach((v, i) => {
    const w = W * v / total, share = Math.round(v / total * 100) + "%";
    parts.push(svg("rect", { x, y: 4, width: Math.max(0, w - 2), height: 36, rx: 3, style: "fill:" + wbColor(i) }, svg("title", null, labels[i] + ": " + wbFormat(v, unit) + " (" + share + ")")));
    if (w > 44) parts.push(svg("text", { class: "inside", x: x + w / 2 - 1, y: 26, "text-anchor": "middle" }, share));
    x += w;
  });
  return { H, parts };
}
function wbChart(node, ctx) {
  const p = node.props, line = node.type === "LineChart";
  const labels = p.labels, series = p.series;
  for (const s of series) {
    if (s.props.values.length !== labels.length) ctx.errors.push("line " + s.line + ": Series " + JSON.stringify(s.props.name) + " has " + s.props.values.length + " values for " + labels.length + " labels (drawn anyway)");
  }
  const whole = !line && p.whole;
  if (whole && series.length !== 1) ctx.errors.push("line " + node.line + ": BarChart with whole takes exactly one series (drew the first)");
  const at = (s, i) => { const v = s.props.values[i]; return typeof v === "number" && isFinite(v) ? v : null; };
  const kind = line ? "Line chart" : whole ? "Parts of a whole" : "Bar chart";
  const label = kind + ": " + series.map((s) => s.props.name + ": " + labels.map((l, i) => l + " " + wbFormat(at(s, i), p.unit)).join(", ")).join("; ");
  const figure = el("figure", { class: "wb-chart" });
  let drawn = 0;
  const draw = (width) => {
    width = Math.max(280, Math.round(width));
    if (width === drawn || !series.length) return;
    drawn = width;
    const g = line ? wbLineSvg(labels, series, p.unit, width, at) : whole ? wbWholeSvg(labels, series[0], p.unit, width, at) : wbBarSvg(labels, series, p.unit, width, at);
    const graphic = svg("svg", { viewBox: "0 0 " + width + " " + g.H, width, height: g.H, role: "img", "aria-label": wbClip(label, 1200) }, g.parts);
    if (figure.firstChild && figure.firstChild.nodeName === "svg") figure.firstChild.replaceWith(graphic); else figure.prepend(graphic);
  };
  draw(640);
  if (window.ResizeObserver) new ResizeObserver((entries) => { const w = entries[0].contentRect.width; if (w > 0) draw(w); }).observe(figure);
  if (whole && series.length) {
    const values = labels.map((_, i) => Math.max(0, at(series[0], i) || 0));
    const total = values.reduce((a, b) => a + b, 0);
    figure.append(el("figcaption", { class: "wb-legend" }, labels.map((l, i) => el("span", { class: "wb-key", style: "--c:" + wbColor(i) },
      l + ": " + wbFormat(values[i], p.unit) + " (" + (total ? Math.round(values[i] / total * 100) : 0) + "%)")).concat([el("b", null, "Total " + wbFormat(total, p.unit))])));
  } else if (series.length > 1) {
    figure.append(el("figcaption", { class: "wb-legend" }, series.map((s, k) => el("span", { class: "wb-key", style: "--c:" + wbColor(k) }, s.props.name))));
  }
  const shown = whole ? series.slice(0, 1) : series;
  figure.append(el("details", { class: "wb-numbers" }, [el("summary", null, "Numbers"), el("div", { class: "md-table" }, el("table", null, [
    el("thead", null, el("tr", null, [el("th", null, "")].concat(shown.map((s) => el("th", { scope: "col" }, s.props.name))))),
    el("tbody", null, labels.map((l, i) => el("tr", null, [el("th", { scope: "row" }, l)].concat(shown.map((s) => el("td", { class: "num" }, wbFormat(at(s, i), p.unit))))))),
  ]))]));
  return figure;
}

// ---- One board: rendered program, errors, and the answer bar (or the answer, once given).
// Writeback (as in OpenChamber): a board with fields gets Send and a note; a Button press sends the
// whole form with its label and value. Buttons whose values name two or more items are decisions:
// they collect per item and send once every item has one, or early with Send now.
function wbBoardNode(board) {
  const ctx = {
    errors: [], fields: [], buttons: [], items: [], controls: [],
    field(name, line, controls, get, set) {
      this.controls.push(...controls);
      if (this.fields.some((f) => f.name === name)) { this.errors.push("line " + line + ": two fields are named " + JSON.stringify(name) + "; only the first is sent (drawn anyway)"); return; }
      this.fields.push({ name, get, set });
    },
  };
  let body = null;
  try {
    const program = wbProgram(board.program);
    ctx.errors.push(...program.errors);
    if (program.root) body = wbRender(program.root, [], ctx);
  } catch (error) {
    ctx.errors.push("could not draw the board: " + error.message);
  }
  const answered = board.status === "answered";
  const node = el("article", { class: "wb-board" + (answered ? " answered" : ""), "data-board": board.id }, [
    el("header", { class: "wb-head" }, [el("span", { class: "wb-note" }, board.note || "Whiteboard"), answered ? badge("answered", "stopped") : null, timeNode(board.at)]),
    ctx.errors.length ? el("div", { class: "wb-callout tone-error wb-errors", role: "note" }, [
      el("strong", null, body ? "Part of this board could not be drawn" : "This board could not be drawn"),
      el("ul", null, ctx.errors.slice(0, 12).map((e) => el("li", null, e))),
      el("p", { class: "small" }, "The walker has been told and may draw it again."),
    ]) : null,
    body,
  ]);

  const chosen = new Map(); // item -> the label of the button chosen for it
  const decisions = ctx.items.length >= 2;
  const markChoices = () => {
    for (const b of ctx.buttons) {
      if (b.value == null || !chosen.has(b.value)) continue;
      const mine = chosen.get(b.value) === b.label;
      b.node.setAttribute("aria-pressed", String(mine));
      b.node.classList.toggle("dim", !mine);
      const card = b.node.closest(".wb-card");
      const tag = card && card.querySelector(":scope > .wb-card-head > .wb-decision");
      if (mine && tag) { tag.textContent = b.label; tag.hidden = false; card.dataset.decision = b.label; }
    }
  };
  if (answered) {
    const answer = board.answer || {};
    const values = answer.values && typeof answer.values === "object" ? answer.values : {};
    for (const f of ctx.fields) if (Object.prototype.hasOwnProperty.call(values, f.name)) f.set(values[f.name]);
    for (const d of Array.isArray(answer.decisions) ? answer.decisions : []) if (d && d.item != null) chosen.set(String(d.item), String(d.choice));
    if (answer.button != null && answer.value != null) chosen.set(String(answer.value), String(answer.button));
    markChoices();
    if (answer.button != null && answer.value == null) {
      const pressed = ctx.buttons.find((b) => b.label === answer.button && b.value == null);
      if (pressed) pressed.node.setAttribute("aria-pressed", "true");
    }
    for (const c of ctx.controls.concat(ctx.buttons.map((b) => b.node))) c.disabled = true;
    node.append(wbAnswerNode(board.answer));
    return { node, errors: ctx.errors };
  }
  if (!ctx.fields.length && !ctx.buttons.length) return { node, errors: ctx.errors };

  let sent = false;
  const note = el("textarea", { class: "wb-input", rows: 2, placeholder: "Anything the walker should know" });
  const noteField = el("label", { class: "wb-field", hidden: true }, [el("span", null, "Note (optional)"), note]);
  const noteToggle = el("button", { type: "button", class: "link small", onclick: () => { noteField.hidden = false; noteToggle.hidden = true; note.focus(); } }, "Add a note");
  const tally = el("span", { class: "small muted" });
  const status = el("span", { class: "small", role: "status" });
  const sendButton = ctx.fields.length ? el("button", { type: "button", class: "primary", onclick: () => submit(null) }, "Send") : null;
  const sendNow = decisions && !ctx.fields.length ? el("button", { type: "button", disabled: true, onclick: () => submit(null) }, "Send now") : null;
  const decided = () => ctx.items.filter((i) => chosen.has(i)).length;
  const refresh = () => {
    markChoices();
    if (decisions) tally.textContent = decided() + " of " + ctx.items.length + " decided";
    if (sendNow) sendNow.disabled = !decided();
  };
  function submit(pressed) {
    if (sent) return;
    const values = {};
    for (const f of ctx.fields) values[f.name] = f.get();
    const answer = {
      button: pressed ? pressed.label : null,
      value: pressed ? pressed.value : null,
      values,
      decisions: ctx.items.filter((i) => chosen.has(i)).map((item) => ({ item, choice: chosen.get(item) })),
      note: note.value.trim(),
    };
    if (!send({ type: "board_answer", id: board.id, answer })) { status.textContent = "Not connected. Try again in a moment."; return; }
    sent = true;
    log("whiteboard: answered " + board.id);
    wbOnBoard(Object.assign({}, board, { status: "answered", answer }));
  }
  for (const b of ctx.buttons) {
    b.node.addEventListener("click", () => {
      if (sent) return;
      if (b.value != null) chosen.set(b.value, b.label);
      if (decisions && b.value != null) {
        refresh();
        if (!ctx.fields.length && decided() === ctx.items.length) submit(null);
        return;
      }
      submit(b);
    });
  }
  refresh();
  node.append(el("div", { class: "wb-actions" }, [noteField, noteToggle, tally, status, el("span", { class: "spacer" }), sendNow, sendButton]));
  return { node, errors: ctx.errors };
}

function wbAnswerNode(answer) {
  const rows = [];
  const shown = (v) => v == null || v === "" ? "(blank)" : Array.isArray(v) ? (v.join(", ") || "(none)") : typeof v === "object" ? JSON.stringify(v) : String(v);
  if (answer && typeof answer === "object") {
    for (const d of Array.isArray(answer.decisions) ? answer.decisions : []) if (d) rows.push([String(d.item), shown(d.choice)]);
    if (answer.values && typeof answer.values === "object") for (const key of Object.keys(answer.values)) rows.push([key, shown(answer.values[key])]);
    if (answer.button) rows.push(["Pressed", String(answer.button)]);
    if (answer.note) rows.push(["Note", String(answer.note)]);
  }
  return el("div", { class: "wb-answered" }, [el("strong", null, "Answered"),
    rows.length ? el("dl", { class: "wb-kv" }, [].concat(...rows.map((r) => [el("dt", null, r[0]), el("dd", null, r[1])]))) : null]);
}

// ---- The boards on this page: id -> { board, key, node, fold, errors }
const wbBoards = new Map();
const wbErrorsSent = new Set();
let wbUnseen = false;   // an open board arrived while the whiteboard was out of sight
let wbOnScreen = false; // the whiteboard is in this page's viewport, for the viewing report
// Held by reference, not looked up by id: the section moves into the whiteboard's own tab and back.
const WB = { root: $("whiteboard"), empty: $("wbEmpty"), current: $("wbCurrent"), older: $("wbOlder"), olderCount: $("wbOlderCount"), olderList: $("wbOlderList") };

function wbNormalize(raw) {
  if (!raw || typeof raw !== "object" || raw.id == null) return null;
  return {
    id: String(raw.id), at: Number(raw.at) || Date.now(), program: String(raw.program || ""), note: String(raw.note || "").slice(0, 300),
    status: raw.status === "answered" ? "answered" : "open", answer: raw.answer && typeof raw.answer === "object" ? raw.answer : null,
  };
}
// Rebuilds a board only when what it shows changed, so an update never wipes half-entered input.
function wbUpsert(board) {
  const key = [board.status, board.note, board.program, JSON.stringify(board.answer)].join("\u0001");
  const known = wbBoards.get(board.id);
  if (known && known.key === key) { known.board = board; return false; }
  const built = wbBoardNode(board);
  if (known) known.node.remove();
  const entry = { board, key, node: built.node, fold: known ? known.fold : null, errors: built.errors };
  wbBoards.set(board.id, entry);
  wbReportErrors(entry);
  return !known;
}
function wbReportErrors(entry) {
  const b = entry.board;
  if (!entry.errors.length || b.status !== "open" || wbErrorsSent.has(b.id)) return;
  if (send({ type: "board_error", id: b.id, error: entry.errors.slice(0, 12).join("\n").slice(0, 2000) })) wbErrorsSent.add(b.id);
}
// In sight: the whiteboard's own tab is visible, or this page is visible on the Hands tab.
const wbInSight = () => wbTabVisible() || (currentTab === "hands" && !document.hidden && !wbTabOpen());
function wbOnBoard(raw) {
  const board = wbNormalize(raw);
  if (!board) return;
  const fresh = wbUpsert(board);
  wbLayout();
  if (!fresh || board.status !== "open") return;
  log("whiteboard: " + (board.note || board.id));
  if (wbInSight()) return;
  wbUnseen = true;
  renderBoardCount();
  notifyHidden("The walker drew on the whiteboard", board.note);
}
// After the feed snapshot: the server's boards replace this page's.
function wbOnBoards(list) {
  const boards = (Array.isArray(list) ? list : []).map(wbNormalize).filter(Boolean);
  const ids = new Set(boards.map((b) => b.id));
  for (const id of [...wbBoards.keys()]) if (!ids.has(id)) { wbBoards.get(id).node.remove(); wbBoards.delete(id); }
  for (const b of boards) {
    if (wbUpsert(b) && b.status === "open" && !wbInSight()) wbUnseen = true;
    else wbReportErrors(wbBoards.get(b.id)); // a report that could not go out before the socket dropped
  }
  renderBoardCount();
  wbLayout();
}
// The newest board on top; older ones, newest first, each folded beneath it.
function wbLayout() {
  const entries = [...wbBoards.values()].sort((a, b) => a.board.at - b.board.at);
  while (entries.length > WB_KEEP) { const old = entries.shift(); old.node.remove(); wbBoards.delete(old.board.id); }
  WB.empty.hidden = entries.length > 0;
  const latest = entries.pop();
  WB.current.replaceChildren(...(latest ? [latest.node] : []));
  const older = entries.reverse().map((e) => {
    if (!e.fold) e.fold = el("details", { class: "wb-past" });
    e.fold.replaceChildren(el("summary", null, [e.board.note || "Whiteboard", e.board.status === "answered" ? badge("answered", "stopped") : badge("open"), timeNode(e.board.at)]), e.node);
    return e.fold;
  });
  WB.older.hidden = !older.length;
  WB.olderCount.textContent = String(older.length);
  WB.olderList.replaceChildren(...older);
}
function wbClear() {
  wbBoards.clear();
  wbErrorsSent.clear();
  wbUnseen = false;
  renderBoardCount();
  wbLayout();
  wbCloseTab();
}
function renderBoardCount() { $("boardCount").hidden = !wbUnseen; }
function wbSeen() {
  if (!wbUnseen || !wbInSight()) return;
  wbUnseen = false;
  renderBoardCount();
}
// Flashes the newest board and brings the whiteboard into view, here or in its own tab.
function wbReveal() {
  const board = WB.current.firstElementChild;
  if (board) {
    board.classList.remove("flash");
    void board.offsetWidth; // restart the animation
    board.classList.add("flash");
  }
  if (wbTabOpen()) { wbTab.focus(); return; }
  showTab("hands");
  WB.root.scrollIntoView({ block: "start", behavior: REDUCED_MOTION.matches ? "auto" : "smooth" });
}
if (window.IntersectionObserver) {
  new IntersectionObserver((entries) => {
    const e = entries[entries.length - 1];
    wbOnScreen = e.isIntersecting && (e.intersectionRatio >= 0.25 || e.intersectionRect.height >= innerHeight * 0.4);
    reportViewing();
  }, { threshold: [0, 0.25, 0.5, 1] }).observe(WB.root);
}

// ---- The whiteboard in its own tab: a blank tab this page fills and keeps driving, so the one paired
// socket stays here and the server sees nothing new. The section's nodes move there with their listeners;
// closing that tab (or Bring it back) moves them home to Hands, and leaving this page closes it.
let wbTab = null;
let wbTabTimer = null;
const wbTabOpen = () => !!wbTab && !wbTab.closed;
const wbTabVisible = () => wbTabOpen() && wbTab.document.visibilityState === "visible";
// Must run inside the click: browsers open new tabs only from one.
function wbOpenTab() {
  if (wbTabOpen()) { wbTab.focus(); return; }
  const tab = window.open("", "_blank");
  if (!tab) { $("handsNote").textContent = "The browser blocked the new tab. Allow pop-ups for this site and try again."; return; }
  const doc = tab.document;
  doc.open();
  doc.write("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Dogwalk: Whiteboard</title></head><body></body></html>");
  doc.close();
  doc.head.append(document.querySelector("style").cloneNode(true));
  doc.body.append(el("div", { class: "page" }, [
    el("header", { class: "top" }, [el("span", { class: "mark" }, [el("b", null, "dog"), "walk.tools"]), el("span", { class: "crumb" }, "Whiteboard")]),
    el("p", { class: "small muted" }, "Drawn by the walker on your call. This tab follows your Paired Browser tab, so keep that one open; answer here or aloud."),
    WB.root,
  ]));
  wbTab = tab;
  $("wbAway").hidden = false;
  $("handsNote").textContent = "";
  tab.addEventListener("pagehide", wbTabClosed);
  doc.addEventListener("visibilitychange", () => { wbSeen(); reportViewing(true); });
  clearInterval(wbTabTimer);
  wbTabTimer = setInterval(() => { if (!wbTabOpen()) wbTabClosed(); }, 1000);
  wbSeen();
  reportViewing(true);
}
function wbTabClosed() {
  if (!wbTab) return;
  wbTab = null;
  clearInterval(wbTabTimer);
  $("wbHome").append(WB.root);
  $("wbAway").hidden = true;
  reportViewing(true);
}
function wbCloseTab() {
  const tab = wbTab;
  wbTabClosed();
  if (tab && !tab.closed) tab.close();
}
$("handsWhiteboard").addEventListener("click", wbOpenTab);
$("wbBack").addEventListener("click", wbCloseTab);
addEventListener("pagehide", wbCloseTab);
// ==== WHITEBOARD END ====

// ---- The walker shows the user something: {"type":"show","uri":"...","note":"..."}.
// dogwalk:file/<path>, dogwalk:dir/<path>, dogwalk:session/<Alias>, dogwalk:timeline and dogwalk:whiteboard navigate
// this page at once, with a toast. dogwalk:terminal, dogwalk:desktop and http(s) URLs need a new tab,
// which a browser opens only from a click, so they raise a banner with a button instead.
const BANNER_MS = 120000;
let bannerAction = null;
let bannerTimer = null;
let toastTimer = null;

function onShow(m) {
  const uri = String(m.uri || "");
  const note = String(m.note || "").slice(0, 300);
  const path = /^dogwalk:(file|dir)\/(.*)$/.exec(uri);
  if (path) {
    // Paths are absolute under the sandbox home; anything else is ignored.
    const full = resolvePath("", path[2]);
    if (path[2].charAt(0) !== "/" || (full !== HOME && full.indexOf(HOME + "/") !== 0)) {
      console.warn("show: ignoring a path outside " + HOME + ": " + uri);
      return;
    }
    if (path[1] === "file") openFile(full);
    else { showTab("files"); listDir(full); }
    return walkerShowed("The walker opened " + (path[1] === "dir" ? "the folder " : "") + basename(full), note);
  }
  const session = /^dogwalk:session\/(.+)$/.exec(uri);
  if (session) {
    const alias = session[1];
    showLive("sessions");
    const card = [...document.querySelectorAll(".card[data-alias]")].find((node) => node.dataset.alias === alias);
    if (card) {
      const group = card.closest("details");
      if (group && !group.open) { group.open = true; closedOpen = true; }
      card.classList.remove("flash");
      void card.offsetWidth; // restart the animation
      card.classList.add("flash");
      card.scrollIntoView({ block: "center", behavior: REDUCED_MOTION.matches ? "auto" : "smooth" });
    }
    return walkerShowed("The walker pointed you to " + alias, note);
  }
  if (uri === "dogwalk:timeline") {
    showLive("timeline");
    feedStick = true;
    scrollFeedToEnd();
    const last = $("feedList").lastElementChild;
    if (last) last.scrollIntoView({ block: "nearest", behavior: REDUCED_MOTION.matches ? "auto" : "smooth" });
    return walkerShowed("The walker opened the call timeline", note);
  }
  if (uri === "dogwalk:whiteboard") {
    wbReveal();
    return walkerShowed(wbBoards.size ? "The walker pointed you to the whiteboard" : "The walker opened the whiteboard, but nothing is drawn on it yet", note);
  }
  if (uri === "dogwalk:terminal" || uri === "dogwalk:desktop") {
    const kind = uri.slice("dogwalk:".length);
    return showBanner("The walker wants to show you the " + kind, "", note, "Open " + kind, () => launch(kind));
  }
  if (/^https?:/i.test(uri)) {
    let url = null;
    try { url = new URL(uri); } catch (error) { /* ignored below */ }
    if (!url || (url.protocol !== "http:" && url.protocol !== "https:")) {
      console.warn("show: ignoring a malformed web address: " + uri);
      return;
    }
    const href = url.href;
    return showBanner("The walker wants to show you a web page", url.hostname, note, "Open page",
      () => window.open(href, "_blank", "noopener,noreferrer"));
  }
  console.warn("show: ignoring an unknown URI: " + uri);
}

// A navigation already happened: say so briefly, and notify if the tab is hidden.
function walkerShowed(text, note) {
  log("show: " + text + (note ? " (" + note + ")" : ""));
  const toast = $("toast");
  toast.replaceChildren(...[text, note ? el("span", { class: "note" }, note) : null].filter(Boolean));
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, note ? 8000 : 5000);
  notifyHidden(text, note);
}
function notifyHidden(title, body) {
  if (alertsOn && document.hidden) raiseAlert(title, body || "Open the Paired Browser to see it.");
}

function showBanner(text, host, note, button, action) {
  log("show: " + text + (host ? " " + host : "") + (note ? " (" + note + ")" : ""));
  $("bannerText").replaceChildren(...[text, host ? el("span", { class: "host" }, host) : null].filter(Boolean));
  $("bannerNote").textContent = note;
  $("bannerNote").hidden = !note;
  $("bannerOpen").textContent = button;
  bannerAction = action;
  $("showBanner").hidden = false;
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(hideBanner, BANNER_MS);
  notifyHidden(text, [host, note].filter(Boolean).join(": "));
}
function hideBanner() {
  clearTimeout(bannerTimer);
  bannerAction = null;
  $("showBanner").hidden = true;
}
// Runs inside the click, so the new tab is allowed.
function openBanner() {
  const action = bannerAction;
  hideBanner();
  if (action) action();
}

// ---- Start
$("listen").addEventListener("click", startListening);
$("stop").addEventListener("click", () => {
  stopListening();
  claimState = "idle";
  setStatus("Stopped. Press Listen to start again.");
});
$("unpair").addEventListener("click", unpair);
$("closeViewer").addEventListener("click", closeViewer);
$("copyPath").addEventListener("click", (event) => copyText(viewer.path, event.currentTarget));
$("downloadFile").addEventListener("click", () => downloadFile(viewer.path));
for (const tab of TABS) $("tab-" + tab).addEventListener("click", () => showTab(tab));
$("seg-sessions").addEventListener("click", () => showLiveView("sessions"));
$("seg-timeline").addEventListener("click", () => showLiveView("timeline"));
$("bannerDismiss").addEventListener("click", hideBanner);
$("bannerOpen").addEventListener("click", openBanner);
$("tabs").addEventListener("keydown", (event) => {
  // Arrow keys move between tabs, as in the ARIA tabs pattern.
  const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
  if (!step) return;
  const next = TABS[(TABS.indexOf(currentTab) + step + TABS.length) % TABS.length];
  showTab(next);
  $("tab-" + next).focus();
});
$("feedList").addEventListener("scroll", () => {
  const list = $("feedList");
  feedStick = list.scrollHeight - list.scrollTop - list.clientHeight < 48;
  if (feedStick) $("feedPill").hidden = true;
}, { passive: true });
$("feedPill").addEventListener("click", () => { feedStick = true; scrollFeedToEnd(); });
$("wakeToggle").checked = wakeWanted;
$("wakeToggle").disabled = !("wakeLock" in navigator);
if (!("wakeLock" in navigator)) $("wakeToggle").parentNode.title = "This browser cannot keep the screen on.";
$("wakeToggle").addEventListener("change", toggleWake);
$("alertToggle").checked = alertsOn && (!("Notification" in window) || Notification.permission !== "denied");
$("alertToggle").addEventListener("change", toggleAlerts);
WIDE.addEventListener("change", () => showTab(currentTab));
document.addEventListener("keydown", (event) => {
  if (event.altKey || event.ctrlKey || event.metaKey) return;
  const target = event.target;
  if (target && (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable)) return;
  if (event.key === "Escape" && !$("showBanner").hidden) { hideBanner(); return; }
  if (event.key === "Escape" && !$("viewer").hidden) { closeViewer(); return; }
  const index = "123".indexOf(event.key);
  if (index >= 0 && event.key.length === 1 && !$("pairedScreen").hidden) showTab(TABS[index]);
});
document.addEventListener("visibilitychange", () => {
  reportViewing(true); // "hidden" on the way out, the current view on the way back
  if (document.visibilityState !== "visible") return;
  wbSeen();
  acquireWake();
  // Backgrounded tabs lose their sockets: reconnect at once on return instead of waiting out the backoff.
  if (!socket && sessionStorage.getItem(TOKEN_KEY)) { backoff = 1000; connect(); }
});
// Relative timestamps and the expiry countdown tick together.
function tick() {
  for (const node of document.querySelectorAll("time[data-at]")) node.textContent = ago(Number(node.dataset.at));
  renderPairing();
}
setInterval(tick, 15000);
renderCrumbs(HOME);
renderFilters();
applyFilters();
showLiveView(liveView);
showTab(currentTab);
acquireWake();
if (sessionStorage.getItem(TOKEN_KEY)) connect();
else { showScreen("pair"); setStatus("Press Listen to start"); }
</script>
</body>
</html>
`;
