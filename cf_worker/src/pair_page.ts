// Paired Browser page (DOMAIN.md), served at GET /pair: listens for the Pairing Code the call
// plays as keypad tones, claims it, waits for the caller to accept, then shows live Managed
// Session status, a live feed of the call, and a read-only Workspace file viewer over /pair/ws.
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

/* Tabs; on a wide screen the Sessions and Feed tabs both show the two panels side by side */
.tabs{position:sticky;top:0;z-index:5;display:flex;gap:2px;background:var(--paper);border-bottom:1px solid var(--rule);margin:14px 0 12px;overflow-x:auto}
.tab{border:0;border-bottom:3px solid transparent;border-radius:0;background:none;padding:10px 14px;min-height:46px;font-weight:600;color:var(--muted);white-space:nowrap}
.tab[aria-selected=true]{color:var(--ink);border-bottom-color:var(--accent)}
.tab .key{font:500 11px/1 var(--mono);color:var(--faint);margin-left:6px}
@media (hover:none){.tab .key{display:none}}
.count{display:inline-block;min-width:1.6em;margin-left:6px;padding:2px 6px;border-radius:999px;background:var(--accent);color:var(--accent-ink);font:700 11px/1.2 var(--sans);text-align:center}
.panel{display:none;min-width:0}
.panel>h2:first-child{margin-top:4px}
.panes[data-tab=sessions] #panelSessions,.panes[data-tab=feed] #panelFeed,.panes[data-tab=files] #panelFiles,.panes[data-tab=hands] #panelHands{display:block}
@media (min-width:1100px){
  .panes[data-tab=sessions],.panes[data-tab=feed]{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.1fr);gap:0 28px;align-items:start}
  .panes[data-tab=sessions] #panelFeed,.panes[data-tab=feed] #panelSessions{display:block}
  .panes #panelFeed{position:sticky;top:58px}
}

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

/* Feed: one color per role, always with a text label beside it */
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
.entries li{display:flex;justify-content:space-between;gap:10px;padding:5px 10px;border-top:1px solid var(--line);font:13px/1.5 var(--mono)}
.entries li:first-child{border-top:0}
.entries .meta{color:var(--faint);white-space:nowrap}
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
    <button id="tab-sessions" class="tab" role="tab" aria-controls="panelSessions">Sessions<span class="key">1</span></button>
    <button id="tab-feed" class="tab" role="tab" aria-controls="panelFeed">Feed<span id="feedCount" class="count" hidden></span><span class="key">2</span></button>
    <button id="tab-files" class="tab" role="tab" aria-controls="panelFiles">Files<span class="key">3</span></button>
    <button id="tab-hands" class="tab" role="tab" aria-controls="panelHands">Hands<span class="key">4</span></button>
  </nav>

  <div id="panes" class="panes" data-tab="feed">
    <section id="panelSessions" class="panel" role="tabpanel" aria-labelledby="tab-sessions">
      <h2>Sessions</h2>
      <p id="sandbox" class="small muted"></p>
      <div id="sessions"></div>
      <div id="closed"></div>
    </section>

    <section id="panelFeed" class="panel" role="tabpanel" aria-labelledby="tab-feed">
      <h2>Live feed</h2>
      <div id="feedFilters" class="feed-bar" role="group" aria-label="Show or hide"></div>
      <div class="feed-wrap">
        <ol id="feedList" class="feed-list" aria-live="off"></ol>
        <p id="feedEmpty" class="feed-empty muted small">Nothing yet. What is said and done in the call appears here as it happens.</p>
        <button id="feedPill" class="pill" hidden>New messages below</button>
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
      </div>
      <p id="handsNote" class="small muted" role="status"></p>
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
  }
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
    if (event.code === 4003) setStatus("Unpaired. Press Listen to pair again.");
    else setStatus("This pairing is no longer valid" + (serverError ? " (" + serverError + ")" : "") + ". Press Listen to pair again.", "bad");
    serverError = "";
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
      break;
    case "pong":
      if (pairing && m.idle_expires_at) { pairing.idle_expires_at = m.idle_expires_at; renderPairing(); }
      break;
    case "status": renderStatus(m); break;
    case "feed": onFeed(m); break;
    case "list": if (m.id === pendingList) renderListing(m); break;
    case "file": if (m.id === pendingRead) renderTextFile(m); break;
    case "launch": settleLaunch(m); break;
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
  renderChips();
  showScreen("pair");
}

function unpair() {
  if (!confirm("Unpair this browser? You will need to pair it again from the call.")) return;
  if (send({ type: "unpair" })) return; // the server replies, then closes with 4003
  clearTimeout(reconnectTimer);
  socket = null;
  forgetPairing();
  setStatus("Unpaired. Press Listen to pair again.");
}

// ---- Tabs: Sessions, Feed, Files, Hands. A wide screen shows Sessions and Feed side by side.
const TABS = ["sessions", "feed", "files", "hands"];
const TAB_KEY = "dogwalk.pair.tab";
let currentTab = TABS.indexOf(stored(TAB_KEY, "feed")) >= 0 ? stored(TAB_KEY, "feed") : "feed";

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
  if (feedVisible()) {
    feedUnseen = 0;
    renderFeedCount();
    if (feedStick) scrollFeedToEnd();
  }
}
const feedVisible = () => currentTab === "feed" || (WIDE.matches && currentTab === "sessions");

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
  const card = el("article", { class: "card turn-" + s.turn_state }, [
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

// ---- Live feed: what was said and done in the call, newest at the bottom
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
  $("feedCount").textContent = feedUnseen ? String(Math.min(feedUnseen, 99)) + (feedUnseen > 99 ? "+" : "") : "";
  $("feedCount").hidden = !feedUnseen;
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
  return bytes < 1024 ? bytes + " B" : bytes < 1048576 ? (bytes / 1024).toFixed(1) + " KB" : (bytes / 1048576).toFixed(1) + " MB";
}

function renderListing(m) {
  listedOnce = true;
  currentDir = m.path;
  renderCrumbs(m.path);
  $("fileNote").textContent = "";
  const entries = (m.entries || []).slice().sort((a, b) => (b.dir - a.dir) || String(a.name).localeCompare(String(b.name)));
  const items = entries.map((e) => {
    const target = resolvePath(m.path, e.name);
    return el("li", null, [
      el("button", { class: "link", onclick: () => (e.dir ? listDir(target) : openFile(target, e.size)) }, e.name + (e.dir ? "/" : "")),
      el("span", { class: "meta" }, (e.dir ? "" : size(e.size) + " ") + (e.modified || "")),
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
  json: "json geojson ipynb webmanifest",
  csv: "csv",
  tsv: "tsv tab",
  binary: "mid midi zip gz tgz bz2 xz zst tar 7z rar jar whl so dylib dll exe bin o a class pyc wasm sqlite sqlite3 db "
    + "woff woff2 ttf otf eot psd xcf blend fbx glb npy npz pt pth onnx safetensors parquet doc docx xls xlsx ppt pptx key pages numbers dmg iso",
};
for (const kind in kinds) for (const ext of kinds[kind].split(" ")) EXT_KIND[ext] = kind;
const TEXT_KINDS = new Set(["markdown", "json", "csv", "tsv", "text"]);
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
  if (kind === "json") {
    try { return setViewerBody(codeBlock(JSON.stringify(JSON.parse(text), null, 2))); }
    catch (error) { $("viewerNote").textContent = m.truncated ? "Too long to pretty-print; shown as is." : "Not valid JSON; shown as is."; }
  }
  if (kind === "csv" || kind === "tsv") return setViewerBody(tableBlock(text, kind === "tsv" ? "\t" : ","));
  setViewerBody(codeBlock(text));
}

// Source with line numbers (HTML and XML included, shown as text, never rendered).
function codeBlock(text) {
  const pre = el("pre", { class: "code" });
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  for (const line of lines) pre.append(el("span", { class: "ln" }, line));
  return pre;
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
const FENCE = /^\s*(\x60{3,}|~{3,})/;
function escapeHtml(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function inlineMarkdown(text) {
  const spans = [];
  text = text.replace(/\x60([^\x60]+)\x60/g, (_, code) => { spans.push(code); return "\u0000" + (spans.length - 1) + "\u0000"; });
  text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, href) =>
    /^(https?:\/\/|\/|#)/i.test(href) ? "<a href=\"" + href + "\" target=\"_blank\" rel=\"noopener noreferrer\">" + label + "</a>" : label);
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_]+)__/g, "<strong>$1</strong>");
  text = text.replace(/\*([^*\s][^*]*)\*/g, "<em>$1</em>").replace(/(^|[^\w])_([^_\s][^_]*)_(?=[^\w]|$)/g, "$1<em>$2</em>");
  return text.replace(/\u0000(\d+)\u0000/g, (_, i) => "<code>" + spans[+i] + "</code>");
}
function renderMarkdown(source) {
  const lines = escapeHtml(source).replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let paragraph = [], list = null;
  const flushParagraph = () => { if (paragraph.length) out.push("<p>" + inlineMarkdown(paragraph.join(" ")) + "</p>"); paragraph = []; };
  const flushList = () => {
    if (list) out.push("<" + list.tag + ">" + list.items.map((item) => "<li>" + inlineMarkdown(item) + "</li>").join("") + "</" + list.tag + ">");
    list = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = FENCE.exec(line);
    if (fence) {
      flushParagraph(); flushList();
      const code = [];
      for (i++; i < lines.length && lines[i].trim().indexOf(fence[1]) !== 0; i++) code.push(lines[i]);
      out.push("<pre><code>" + code.join("\n") + "</code></pre>");
      continue;
    }
    const heading = /^(#{1,6})\s+(.*?)[\s#]*$/.exec(line);
    if (heading) {
      flushParagraph(); flushList();
      out.push("<h" + heading[1].length + ">" + inlineMarkdown(heading[2]) + "</h" + heading[1].length + ">");
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flushParagraph(); flushList(); out.push("<hr>"); continue; }
    const item = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(line);
    if (item) {
      flushParagraph();
      const tag = /\d/.test(item[1]) ? "ol" : "ul";
      if (list && list.tag !== tag) flushList();
      if (!list) list = { tag, items: [] };
      list.items.push(item[2]);
      continue;
    }
    if (!line.trim()) { flushParagraph(); flushList(); continue; }
    if (list && /^\s{2,}/.test(line)) { list.items[list.items.length - 1] += " " + line.trim(); continue; }
    flushList();
    paragraph.push(line.trim());
  }
  flushParagraph(); flushList();
  return out.join("\n");
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
  if (event.key === "Escape" && !$("viewer").hidden) { closeViewer(); return; }
  const index = "1234".indexOf(event.key);
  if (index >= 0 && event.key.length === 1 && !$("pairedScreen").hidden) showTab(TABS[index]);
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
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
showTab(currentTab);
acquireWake();
if (sessionStorage.getItem(TOKEN_KEY)) connect();
else { showScreen("pair"); setStatus("Press Listen to start"); }
</script>
</body>
</html>
`;
