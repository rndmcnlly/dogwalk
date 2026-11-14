// Paired Browser page (DOMAIN.md), served at GET /pair: accepts the spoken Pairing Code as
// typed text, claims it, waits for the caller to accept, then shows the Whiteboards drawn on this
// browser's Display over /pair/ws. The page has no views of its own: anything the User should see
// is drawn as a board. Files a board shows (Image, Document) are read from GET /pair/file as blob
// URLs or, for text, over the socket; Access Elements open only on a click, under their Access Grant.
//
// String.raw keeps every backslash in the page script literal, so regexes and "\n" escapes
// reach the browser as written. The page must therefore contain no backtick and no dollar-brace
// sequence anywhere (a backtick is written "\x60" in the script).
//
// The page's build identity is a hash of this source, stamped into the page where it says
// __PAIR_BUILD__. The page sends it when it connects; a tab loaded before a deploy learns it is
// stale from the hello and reloads itself at a safe moment (its pairing lives in sessionStorage,
// so a reload keeps it).

const PAIR_TEMPLATE = String.raw`<!doctype html>
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
  --ok:#2e7d4f;--bad:#b3261e;--warn:#a86a00;--info:#2f6690;
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
    --ok:#74c69d;--bad:#ff8f85;--warn:#e3b45a;--info:#8cb9e6;
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
/* The paired screen's slim bar: the Display's name large enough to read across a room, then this
   browser, how long the pairing lasts, and the few things a person can do here. */
.display-bar{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--line)}
.display-id{min-width:0}
.display-name{margin:0;font:600 clamp(24px,4vw,40px)/1.15 var(--serif);color:var(--ink);overflow-wrap:anywhere}
#status:empty{display:none}
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
#pairForm{max-width:32em}
#pairCode{font-family:var(--mono);font-size:1.1em}
details summary{cursor:pointer}
#log{max-height:180px;overflow:auto;white-space:pre-wrap;margin-top:6px}
.diag{margin-top:28px}
.box{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px}
.prefs{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center;font-size:13px}
.prefs label{display:inline-flex;gap:6px;align-items:center;min-height:32px;cursor:pointer}
.prefs input{width:18px;height:18px;accent-color:var(--accent)}

/* Segmented buttons: a board's Tabs */
.segmented{display:inline-flex;margin:0 0 10px;border:1px solid var(--rule);border-radius:var(--radius);overflow:hidden}
.segmented button{border:0;border-radius:0;background:var(--card);padding:6px 14px;min-height:38px;font-weight:600;color:var(--muted)}
.segmented button+button{border-left:1px solid var(--rule)}
.segmented button[aria-pressed=true]{background:var(--accent);color:var(--accent-ink)}
.segmented button[aria-pressed=true] .count{background:var(--accent-ink);color:var(--accent)}
@keyframes flash{0%,40%{box-shadow:0 0 0 4px var(--accent);background:var(--highlight)}100%{box-shadow:0 0 0 0 transparent}}
/* Badges on boards */
.badge{font:600 11px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;padding:4px 7px;border-radius:999px;background:var(--line);color:var(--ink)}
.badge.in_progress,.badge.queued{background:var(--info);color:var(--paper)}
.badge.failed{background:var(--bad);color:var(--paper)}
.badge.stopped{background:var(--ok);color:var(--paper)}
/* Renderers for Documents */
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


/* Whiteboard (the paired screen, or a tab of its own): boards the walker draws in openui-lang. Tones and chart series take the page's palette. */
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
.wb-image{margin:0;min-width:0;display:flex;flex-direction:column;gap:4px;max-width:24em}
.wb-image.size-icon{max-width:3em}
.wb-image.size-small{max-width:12em}
.wb-image.size-large{max-width:40em}
.wb-image.size-full,.wb-image.zoomed{max-width:none}
.wb-img-frame{display:block;max-width:100%;padding:0;font:inherit;color:inherit;text-align:center;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;cursor:zoom-in;background:repeating-conic-gradient(var(--line) 0 25%,var(--card) 0 50%) 0 0/16px 16px}
div.wb-img-frame{cursor:default}
.size-icon .wb-img-frame{border:0;background:none}
.wb-img-frame img{display:block;max-width:100%;height:auto;margin:0 auto}
.wb-img-frame[aria-busy=true],.wb-image.failed .wb-img-frame{display:flex;align-items:center;justify-content:center;min-height:9em;padding:8px;cursor:default;background:var(--paper)}
.size-icon .wb-img-frame[aria-busy=true],.size-icon.failed .wb-img-frame{min-height:3em;padding:0}
.wb-image.failed .wb-img-frame{border-style:dashed}
.wb-image.zoomed .wb-img-frame{overflow:auto;max-height:80vh;cursor:zoom-out}
.wb-image.zoomed img{max-width:none}
.wb-image figcaption{color:var(--muted)}
.wb-retry{align-self:flex-start}
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
.wb-empty{margin:clamp(24px,8vh,96px) auto;max-width:36em;text-align:center;font:400 clamp(17px,2.4vw,24px)/1.45 var(--serif);color:var(--muted)}
.wb-empty b{color:var(--ink);font-weight:600}
@media (min-width:1400px){.whiteboard{font-size:17px}.wb-head .wb-note{font-size:22px}}

/* Documents: a Workspace file shown on a board */
.wb-doc{display:flex;flex-direction:column;gap:6px;min-width:0}
.wb-doc-head{display:flex;flex-wrap:wrap;gap:2px 10px;align-items:baseline}
.wb-doc-head strong{font-size:15.5px}
.wb-doc-head .mono{font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.wb-doc-note:empty{display:none}
.wb-doc-note{margin:0}
.wb-doc-body[aria-busy=true],.wb-doc.failed .wb-doc-body{display:flex;flex-direction:column;align-items:flex-start;justify-content:center;gap:6px;min-height:5em;padding:10px 12px;border:1px dashed var(--line);border-radius:var(--radius);background:var(--paper)}
.wb-doc .md{max-height:80vh}

/* Access Elements: a card per Terminal, Desktop, Preview, SSH Access or Download, opened only by a click */
.wb-access{display:flex;flex-direction:column;gap:8px;min-width:0;border:1px solid var(--line);border-left:4px solid var(--info);border-radius:var(--radius);padding:10px 12px;background:var(--paper)}
.wb-access.dead{border-left-color:var(--line)}
.wb-access-head{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline}
.wb-access-head strong{font-size:15.5px}
.wb-access-row{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center}
.wb-access-row button{min-height:40px}
.wb-access-say:empty{display:none}
.wb-ssh{display:flex;flex-direction:column;gap:6px}
.wb-ssh pre{margin:0;padding:8px 10px;border-radius:6px;background:var(--code-bg);font:13px/1.45 var(--mono);white-space:pre-wrap;overflow-wrap:anywhere;-webkit-user-select:all;user-select:all}
.wb-preview{display:flex;flex-direction:column;gap:6px}
.wb-preview iframe{display:block;width:100%;height:clamp(420px,75vh,1400px);border:1px solid var(--line);border-radius:var(--radius);background:#fff}

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
</header>
<main>
<p id="status" class="status" role="status" aria-live="polite"></p>

<section id="pairScreen" hidden>
  <ol class="steps">
    <li>In the call, ask the walker to pair a browser.</li>
    <li>Type the spoken code below: a number from 1 to 999, then two words.</li>
    <li>Select Pair browser. The call describes this browser: press 1 in the call to accept it, or star to refuse.</li>
  </ol>
  <form id="pairForm">
    <label class="wb-field" for="pairCode"><span>Pairing code</span>
      <input id="pairCode" name="code" class="wb-input" type="text" required autocomplete="off" autocapitalize="none" spellcheck="false" aria-describedby="pairHelp status" placeholder="123-adroitness-aardvark">
    </label>
    <p id="pairHelp" class="small muted">Use spaces or hyphens between the number and words. The code expires after 90 seconds. Ask the walker to repeat it or give you a new code if needed.</p>
    <div class="row"><button id="claimCode" type="submit" class="primary">Pair browser</button></div>
  </form>
</section>

<section id="pairedScreen" hidden>
  <div class="display-bar">
    <div class="display-id">
      <h1 id="displayName" class="display-name"></h1>
      <div class="small muted"><span id="who"></span><span id="conn"></span></div>
      <div class="small muted" id="expiry"></div>
    </div>
    <div class="prefs">
      <label><input type="checkbox" id="wakeToggle"> Keep screen on</label>
      <button id="wbPopOut">Own tab</button>
      <button id="unpair">Unpair</button>
    </div>
    <div id="barNote" class="small muted" role="status"></div>
  </div>

  <div id="wbHome">
    <section id="whiteboard" class="whiteboard" aria-labelledby="displayName">
      <p id="wbEmpty" class="wb-empty">Nothing on <b data-display-name></b> yet. Ask on the call to put something here.</p>
      <div id="wbCurrent"></div>
      <details id="wbOlder" class="wb-older" hidden><summary>Earlier boards (<span id="wbOlderCount">0</span>)</summary><div id="wbOlderList"></div></details>
    </section>
  </div>
  <p id="wbAway" class="box small" hidden>The whiteboard is open in its own tab. <button id="wbBack" class="mini">Bring it back here</button></p>
</section>

<details class="diag small"><summary class="muted">Diagnostics log</summary><div id="log" class="mono muted"></div></details>
</main>
</div>

<script>
"use strict";
const $ = (id) => document.getElementById(id);
const HOME = "/home/daytona";
const TOKEN_KEY = "dogwalk.pair.token";
const PAIR_BUILD = "__PAIR_BUILD__";
const RELOADED_KEY = "dogwalk.pair.reloadedFor"; // the server build this tab last reloaded to reach

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

// ---- Typed Pairing Code claim. Normalization and PGP word validation belong to the server.
let claimState = "idle"; // idle | claiming | waiting

function setClaimState(state) {
  claimState = state;
  const busy = state !== "idle";
  $("pairCode").disabled = busy;
  $("claimCode").disabled = busy;
  $("pairForm").setAttribute("aria-busy", String(busy));
}

async function claim(code) {
  if (claimState !== "idle") return;
  if (!code.trim()) {
    setStatus("Enter the pairing code spoken in the call.", "bad");
    $("pairCode").focus();
    return;
  }
  setClaimState("claiming");
  setStatus("Claiming the pairing code");
  let response, body;
  try {
    response = await fetch("/pair/claim", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ code }),
    });
    body = await response.json().catch(() => ({}));
  } catch (error) {
    return claimFailed("Could not reach dogwalk.tools (" + error.message + "). Try again.");
  }
  if (response.ok && typeof body.ticket === "string") {
    $("pairCode").value = "";
    setClaimState("waiting");
    return waitForAnswer(body.ticket);
  }
  if (response.status === 404) {
    return claimFailed("That code is unknown or expired. Ask the walker in the call for a new code.");
  }
  claimFailed("Claim failed (" + response.status + "): " + (body.error || response.statusText) + ". Check the code and try again.");
}

function claimFailed(message) {
  setClaimState("idle");
  setStatus(message, "bad");
  $("pairCode").focus();
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
      setClaimState("idle");
      return connect();
    }
    if (body.state === "refused") return pairingFailed("The pairing was refused in the call.");
    return pairingFailed("The pairing request expired before it was accepted.");
  }
  pairingFailed("No answer in the call within 45 seconds.");
}

function pairingFailed(message) {
  setClaimState("idle");
  showScreen("pair");
  setStatus(message + " Enter a new pairing code to try again.", "bad");
  $("pairCode").focus();
}

// ---- Small helpers shared by the paired screen
const stored = (key, fallback) => { try { const v = localStorage.getItem(key); return v == null ? fallback : v; } catch (error) { return fallback; } };
const store = (key, value) => { try { localStorage.setItem(key, value); } catch (error) { /* private mode */ } };
const basename = (path) => String(path).split("/").filter(Boolean).pop() || "/";
const ASLEEP = "The sandbox is asleep; a call wakes it.";
function badge(text, kind) { return el("span", { class: "badge " + (kind || "") }, text); }

// A path as a board writes it, absolute or relative to /home/daytona, made absolute.
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

// The button's own document, so a copy from the whiteboard's own tab works there.
function copyText(text, button) {
  const doc = (button && button.ownerDocument) || document;
  const done = () => {
    if (!button) return;
    const label = button.textContent;
    button.textContent = "Copied";
    setTimeout(() => { button.textContent = label; }, 1200);
  };
  const fallback = () => {
    const area = doc.createElement("textarea");
    area.className = "offscreen";
    area.value = text;
    doc.body.append(area);
    area.select();
    try { doc.execCommand("copy"); done(); } catch (error) { log("copy failed: " + error.message); }
    area.remove();
  };
  const view = doc.defaultView || window;
  if (view.navigator.clipboard && view.isSecureContext) view.navigator.clipboard.writeText(text).then(done, fallback);
  else fallback();
}

// ---- The paired connection
let socket = null;
let backoff = 1000;
let reconnectTimer = null;
let pingTimer = null;
let pairing = null;     // the hello message, with display renamed and idle_expires_at refreshed by pongs
let serverError = "";
let unpairRequested = false; // this page asked to unpair, so a 4003 close is expected

function connect() {
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (!token) return;
  clearTimeout(reconnectTimer);
  showScreen("paired");
  if (!pairing) setStatus("Connecting");
  setConnection("connecting");
  // The identity half routes the socket to its Durable Object; the secret only travels in the auth message.
  const identity = token.split(".")[0];
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host
    + "/pair/ws?for=" + encodeURIComponent(identity));
  socket = ws;
  ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token, build: PAIR_BUILD }));
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

// Requests that the server answers by id: open (opened) and read (file), or an error with the same id.
// handlers.done(reply) or handlers.fail({error, asleep}); a dropped socket fails them all.
let nextId = 1;
const pending = new Map();
function request(message, handlers) {
  const id = nextId++;
  if (!send(Object.assign({ id }, message))) return false;
  pending.set(id, handlers);
  return true;
}
function settle(m, ok) {
  const handlers = pending.get(m.id);
  if (!handlers) return;
  pending.delete(m.id);
  if (ok) handlers.done(m);
  else handlers.fail({ error: String(m.error || "it failed"), asleep: m.asleep === true });
}
function failPending(error) {
  const all = [...pending.values()];
  pending.clear();
  for (const handlers of all) handlers.fail({ error, asleep: false, offline: true });
}

function onClose(ws, event) {
  if (ws !== socket) return;
  socket = null;
  clearInterval(pingTimer);
  failPending("the connection dropped");
  log("socket closed " + event.code + (event.reason ? " " + event.reason : ""));
  if (event.code === 4001 || event.code === 4003) {
    forgetPairing();
    // 4003 without our own request: the server unpaired this browser, as when its Display is unpaired in the call.
    if (event.code === 4003 && unpairRequested) setStatus("Unpaired. Enter a pairing code to pair again.");
    else if (event.code === 4003) setStatus("This browser was unpaired (perhaps its display was unpaired in the call). Enter a pairing code to pair again.", "bad");
    else setStatus("This pairing is no longer valid" + (serverError ? " (" + serverError + ")" : "") + ". Enter a pairing code to pair again.", "bad");
    serverError = "";
    unpairRequested = false;
    return;
  }
  setConnection("disconnected, reconnecting in " + Math.round(backoff / 1000) + " s");
  reconnectTimer = setTimeout(connect, backoff);
  backoff = Math.min(backoff * 2, 30000);
}

function onMessage(m) {
  switch (m.type) {
    case "hello":
      pairing = m;
      backoff = 1000;
      setStatus("");
      setConnection("");
      renderPairing();
      renderDisplay();
      clearInterval(pingTimer);
      pingTimer = setInterval(() => send({ type: "ping" }), 30000);
      if (m.stale) staleBuild(String(m.build || ""));
      break;
    case "display":
      if (pairing) { pairing.display = String(m.name || ""); renderDisplay(); }
      break;
    case "pong":
      if (pairing && m.idle_expires_at) { pairing.idle_expires_at = m.idle_expires_at; renderPairing(); }
      break;
    case "board": wbOnBoard(m.board); break;
    case "boards": wbOnBoards(m.boards); break;
    case "opened": case "file": settle(m, true); break;
    case "error":
      if (m.id != null) settle(m, false);
      else { serverError = String(m.error || ""); log("server error: " + serverError); }
      break;
    case "unpaired": log("unpaired by request"); break;
  }
}

function setConnection(text) { $("conn").textContent = text ? " (" + text + ")" : ""; }

// A deploy since this tab loaded: reload when nothing would be lost (no field in use, no
// half-answered board, no open Access Element or whiteboard tab, which only a click reopens).
// Once per server build, so a page that still comes back stale asks the user instead of looping.
let staleTimer = null;
function staleBuild(build) {
  let reloaded = "";
  try { reloaded = sessionStorage.getItem(RELOADED_KEY) || ""; } catch (error) { /* storage off */ }
  if (reloaded === build) { setStatus("This page is out of date. Reload it to update.", "bad"); return; }
  setStatus("This page is out of date; it reloads itself when you are not using it.", "bad");
  clearInterval(staleTimer);
  const attempt = () => {
    const active = document.activeElement;
    const typing = active && (/^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName) || active.isContentEditable);
    if (typing || wbEdited || wbTabOpen() || wbAccessOpen()) return;
    clearInterval(staleTimer);
    try { sessionStorage.setItem(RELOADED_KEY, build); } catch (error) { /* storage off */ }
    location.reload();
  };
  staleTimer = setInterval(attempt, 2000);
  attempt();
}

function clock(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
// A clock time, with the day when it is not today (a grant can run past midnight).
function clockDay(ms) {
  const at = new Date(ms);
  return (at.toDateString() === new Date().toDateString() ? "" : at.toLocaleDateString([], { weekday: "short" }) + " ") + clock(ms);
}
function fromNow(ms) {
  const minutes = Math.max(0, Math.round((ms - Date.now()) / 60000));
  return minutes < 60 ? minutes + " min" : Math.floor(minutes / 60) + " h " + (minutes % 60) + " min";
}
function renderPairing() {
  if (!pairing) return;
  $("who").textContent = (pairing.device || "This browser") + (pairing.place ? ", " + pairing.place : "");
  // An open page pings, so the idle limit rarely arrives; the hard limit is the real end.
  const parts = [];
  if (pairing.expires_at) parts.push("Paired until " + clock(pairing.expires_at) + " (" + fromNow(pairing.expires_at) + ")");
  if (pairing.idle_expires_at && (!pairing.expires_at || pairing.idle_expires_at < pairing.expires_at)) {
    parts.push((parts.length ? "or " : "Ends ") + "at " + clock(pairing.idle_expires_at) + " if left unused");
  }
  $("expiry").textContent = parts.join(", ");
}

// The Display's name: in the bar, the empty whiteboard, the tab title, and the whiteboard's own tab.
const displayName = () => (pairing && pairing.display) || "this display";
function renderDisplay() {
  const name = displayName();
  $("displayName").textContent = (pairing && pairing.display) || "Paired display";
  document.title = pairing ? "Dogwalk: " + name : "Dogwalk: Paired Browser";
  for (const node of WB.root.querySelectorAll("[data-display-name]")) node.textContent = name;
  if (!wbTabOpen()) return;
  wbTab.document.title = "Dogwalk: " + name;
  for (const node of wbTab.document.querySelectorAll("[data-display-name]")) node.textContent = name;
}

function forgetPairing() {
  sessionStorage.removeItem(TOKEN_KEY);
  pairing = null;
  wbClear();
  renderDisplay();
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
  setStatus("Unpaired. Enter a pairing code to pair again.");
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
    $("barNote").textContent = "Could not keep the screen on: " + error.message;
  }
}
function toggleWake(event) {
  wakeWanted = event.target.checked;
  store(WAKE_KEY, wakeWanted ? "1" : "0");
  $("barNote").textContent = "";
  if (wakeWanted) acquireWake();
  else if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
}

// ---- File renderers for Documents: text arrives over the socket (read); media is fetched from
// /pair/file into a blob URL. note is the Document's status line, for sizes, durations and failures.
const EXT_KIND = {};
const kinds = {
  image: "png jpg jpeg gif webp svg avif bmp",
  pdf: "pdf",
  audio: "mp3 wav m4a aac ogg oga opus flac",
  video: "mp4 m4v webm mov ogv",
  markdown: "md markdown mdx",
  html: "html htm",
  json: "json geojson ipynb webmanifest",
  csv: "csv",
  tsv: "tsv tab",
};
for (const kind in kinds) for (const ext of kinds[kind].split(" ")) EXT_KIND[ext] = kind;
const MEDIA_KINDS = new Set(["image", "pdf", "audio", "video"]);
const WARN_BYTES = 10 * 1024 * 1024;
const TABLE_ROWS = 500;

function fileKind(path) {
  const ext = (/\.([^./]+)$/.exec(basename(path)) || [])[1];
  return EXT_KIND[String(ext || "").toLowerCase()] || "text";
}

function size(bytes) {
  if (bytes == null) return "";
  const scaled = (n, unit) => (n < 10 ? n.toFixed(1) : String(Math.round(n))) + " " + unit;
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return scaled(bytes / 1024, "KB");
  if (bytes < 1073741824) return scaled(bytes / 1048576, "MB");
  return scaled(bytes / 1073741824, "GB");
}

// GET /pair/file for a file a board on this Display names, exactly as the program writes it.
function fileUrl(boardId, path, extra) {
  return "/pair/file?path=" + encodeURIComponent(path) + "&board=" + encodeURIComponent(boardId) + (extra || "");
}
// Fetches with the pairing token in a header. limit > 0 stops before reading a larger body.
// A failure carries the server's reason, and asleep when the Sandbox is stopped.
async function fetchPaired(url, limit, controller) {
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (!token) throw new Error("This browser is not paired.");
  const response = await fetch(url, { headers: { authorization: "Bearer " + token }, signal: controller.signal });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(body.error ? String(body.error) : "The server answered " + response.status + ".");
    error.asleep = body.asleep === true;
    throw error;
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

function mediaNode(kind, url, name, note) {
  if (kind === "image") {
    // SVG too goes through <img>, where its scripts never run.
    const wrap = el("div", { class: "img-wrap", title: "Click for actual size" });
    const img = el("img", { src: url, alt: name });
    img.addEventListener("load", () => { note.textContent = img.naturalWidth + " by " + img.naturalHeight + " pixels. Click the image to toggle actual size."; });
    img.addEventListener("error", () => { note.textContent = "This browser could not decode the image."; });
    wrap.addEventListener("click", () => wrap.classList.toggle("actual"));
    wrap.append(img);
    return wrap;
  }
  if (kind === "pdf") {
    return el("div", null, [
      el("iframe", { class: "pdf", src: url, title: name }),
      el("p", { class: "small" }, el("a", { href: url, target: "_blank", rel: "noopener" }, "Open in a new tab")),
    ]);
  }
  const media = el(kind, { src: url, controls: true, preload: "metadata", playsinline: kind === "video" });
  media.addEventListener("loadedmetadata", () => {
    const d = media.duration;
    note.textContent = isFinite(d) ? "Duration " + Math.floor(d / 60) + ":" + String(Math.floor(d % 60)).padStart(2, "0")
      + (kind === "video" && media.videoWidth ? ", " + media.videoWidth + " by " + media.videoHeight : "") : "";
  });
  media.addEventListener("error", () => { note.textContent = "This browser cannot play this file."; });
  return media;
}

// Source with line numbers (code and plain text, and HTML under its Source toggle, shown as text, never rendered).
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
function htmlBlock(text, name, status) {
  const frame = el("iframe", { sandbox: "allow-scripts", referrerpolicy: "no-referrer", title: name });
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
    Promise.resolve(request.call(stage)).catch((error) => { status.textContent = "Full screen failed: " + error.message; });
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
function tableBlock(text, sep, note) {
  const parsed = parseDelimited(text, sep, TABLE_ROWS + 1);
  const rows = parsed.rows;
  if (!rows.length) return el("p", { class: "muted" }, "Empty file.");
  const head = rows[0], body = rows.slice(1, TABLE_ROWS + 1);
  note.textContent = body.length + " rows" + (parsed.more || rows.length > TABLE_ROWS + 1 ? ", the first " + TABLE_ROWS + " shown" : "")
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

// ==== WHITEBOARD START: boards the walker draws in openui-lang on this browser's Display, answered on this page ====
// Protocol: the server sends {"type":"board","board":{"id","at","display","program","note","status":"open"|"answered",
// "answer","failed"?,"access"?}} for a new or updated board, and {"type":"boards","boards":[...]} on connecting,
// both only for this Display. failed maps a path, as the program writes it, to why it failed the draw-time
// check; access maps "kind:target" to its Access Grant {kind, target, expires_at, revoked?}.
// The page answers {"type":"board_answer","id","answer":{"button","value","values","decisions","note"}}
// and reports, once per board, a program it could not fully draw: {"type":"board_error","id","error"}.
// A Document's text: {"type":"read","id","path","board_id"} -> {"type":"file","id","path","size","truncated","text"}.
// An Access Element: {"type":"open","id","board_id","key"} -> {"type":"opened","id","url"} or
// {"type":"opened","id","ssh":{"command","expires_at"}}. Either fails as {"type":"error","id","error","asleep"?}.
// Media and downloads come from GET /pair/file?path=&board=[&as=image][&download=1].
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
  Image: [["path", "imagepath", 1], ["alt", "str", 1], ["caption", "str"], ["size", "imagesize"]],
  Document: [["path", "docpath", 1], ["title", "str"]],
  // Access Elements: the first argument is the grant's target, or a label for untargeted ones.
  Terminal: [["label", "str"]],
  Desktop: [["label", "str"]],
  Preview: [["port", "port", 1], ["label", "str"], ["inline", "bool"]],
  SshAccess: [["label", "str"]],
  Download: [["path", "docpath", 1], ["label", "str"]],
};
// What may not sit anywhere inside each container; Tabs holds only Tab, and Series sits only in a chart.
const WB_FORBIDS = { Card: ["Card", "Tabs"], Row: ["Row", "Tabs", "Details"], Tab: ["Tabs"], Details: ["Card", "Tabs", "Details"] };
// args keeps the arguments as written (references resolved), for the targets the server keys on.
class WbNode {
  constructor(type, props, line, args) { this.type = type; this.props = props; this.line = line; this.args = args || []; }
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
  // A Workspace file, made absolute. Never a URL, data, or blob: only the Sandbox is ever read.
  docpath: (v, p) => {
    const s = (wbStr(v) || "").trim();
    if (!s) { p.push("must be a Workspace path"); return undefined; }
    if (/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(s)) { p.push("must be a Workspace path, never a URL"); return undefined; }
    const full = resolvePath("", s);
    if (full.indexOf(HOME + "/") !== 0) { p.push("must be a file under " + HOME); return undefined; }
    return full;
  },
  // A Workspace image file, likewise.
  imagepath: (v, p) => {
    const full = WB_KINDS.docpath(v, p);
    if (full === undefined) return undefined;
    if (!/\.(?:png|jpe?g|gif|webp|svg)$/i.test(full)) { p.push("must name a .png, .jpg, .gif, .webp, or .svg file"); return undefined; }
    return full;
  },
  port: (v, p) => { const n = wbNum(v); if (!(Number.isInteger(n) && n >= 1 && n <= 65535)) { p.push("must be a port number from 1 to 65535"); return undefined; } return n; },
  imagesize: wbEnum(["icon", "small", "medium", "large", "full"]),
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
  return new WbNode(name, props, line, args);
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
    case "Image": return wbImage(node, ctx);
    case "Document": return wbDocument(node, ctx);
    case "Terminal": case "Desktop": case "Preview": case "SshAccess": case "Download": return wbAccess(node, ctx);
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

// ---- Files a board shows (Image, Document). The program carries only the path; the page reads
// the file through its pairing while the board is shown, and only while the Sandbox runs. A path
// that failed the draw-time check (board.failed, keyed by the path as written) shows its reason
// instead, and the Orchestrator already knows. Images are fetched with the pairing token into blob
// URLs this page owns; SVG only ever sits in an <img>, where its scripts never run and it loads
// nothing else. Each image is fetched once per board and shared by that board's nodes (reference
// counted by board id and path), so a rebuilt board keeps what it loaded; a board releases its
// images when it leaves the page, and the last release aborts the fetch and revokes the URL. Boards
// moved into the whiteboard's own tab keep using this page's URLs, which stay valid because that tab
// closes with this page. A failed fetch is retried by the next acquire, as Try again does.
const WB_IMAGE_MAX = 8;   // images per board; the rest are left out with an error
const WB_DOC_MAX = 4;     // Documents per board, likewise
const WB_IMAGE_LOADS = 3; // fetches at once
const wbImages = new Map(); // board id + path -> { key, href, refs, url, error, asleep, waiters, controller }
const wbImageQueue = [];
let wbImageLoads = 0;

function wbImageAcquire(boardId, path, done) {
  const key = boardId + "\u0001" + path;
  let entry = wbImages.get(key);
  if (entry && entry.error) { wbImages.delete(key); entry = null; }
  if (!entry) {
    entry = { key, href: fileUrl(boardId, path, "&as=image"), refs: 0, url: "", error: "", asleep: false, waiters: [], controller: null };
    wbImages.set(key, entry);
    wbImageQueue.push(entry);
    wbImagePump();
  }
  entry.refs++;
  if (entry.url) done(entry); else entry.waiters.push(done);
  return entry;
}
// By entry, not key: a retry replaces a failed entry, and the old holder must not release the new one.
function wbImageRelease(entry) {
  if (--entry.refs > 0) return;
  if (wbImages.get(entry.key) === entry) wbImages.delete(entry.key);
  entry.waiters = [];
  if (entry.controller) entry.controller.abort();
  if (entry.url) URL.revokeObjectURL(entry.url);
}
function wbImagePump() {
  while (wbImageLoads < WB_IMAGE_LOADS && wbImageQueue.length) {
    const entry = wbImageQueue.shift();
    if (wbImages.get(entry.key) !== entry) continue; // released while it waited
    wbImageLoads++;
    entry.controller = new AbortController();
    fetchPaired(entry.href, 0, entry.controller)
      .then((blob) => { if (wbImages.get(entry.key) === entry) entry.url = URL.createObjectURL(blob); })
      .catch((error) => { entry.error = error.message || "it could not be fetched"; entry.asleep = error.asleep === true; })
      .finally(() => {
        wbImageLoads--;
        entry.controller = null;
        const waiters = entry.waiters;
        entry.waiters = [];
        if (wbImages.get(entry.key) === entry) for (const done of waiters) done(entry);
        wbImagePump();
      });
  }
}

// The first argument as the program writes it (references resolved, a number as its decimal string,
// trimmed as the server reads it): the path /pair/file and board.failed know, and an Access Grant's target.
function wbTarget(node) {
  const v = node.args[0];
  return typeof v === "number" ? String(v) : typeof v === "string" ? v.trim() : "";
}
// While the Sandbox sleeps a file is not shown, and that is no fault of the board: Try again reads it
// once a call has woken it. Other failures reach the Orchestrator once (see wbShownFailed).
function wbRetry(retry) {
  return el("button", { type: "button", class: "mini wb-retry", onclick: retry }, "Try again");
}

// A figure that holds its place while loading and, on failure, keeps its alt text and the reason
// so the rest of the board reads and answers as usual. alt "" marks a decorative image.
function wbImage(node, ctx) {
  const p = node.props, path = wbTarget(node);
  const size = p.size || "medium";
  const decorative = !p.alt;
  const zoomable = !decorative && size !== "icon";
  const status = el("span", { class: "small muted" }, decorative ? "" : "Loading " + basename(p.path));
  const frame = el(zoomable ? "button" : "div", { class: "wb-img-frame", type: zoomable ? "button" : null, "aria-busy": "true", title: p.path }, status);
  const figure = el("figure", { class: "wb-image size-" + size }, [frame, p.caption ? wbInline("figcaption", "small", p.caption) : null]);
  const fail = (reason, known, retry) => {
    figure.classList.add("failed");
    frame.removeAttribute("aria-busy");
    if (zoomable) frame.disabled = true;
    status.replaceChildren((p.alt ? p.alt + ". " : "") + (retry ? reason + " " : "Not shown: " + reason));
    if (retry && !decorative) figure.append(wbRetry(retry));
    if (!known && !retry) wbShownFailed(ctx, "image", path, reason);
  };
  if (ctx.imageCount >= WB_IMAGE_MAX) {
    ctx.errors.push("line " + node.line + ": a board shows at most " + WB_IMAGE_MAX + " images; " + path + " was left out");
    fail("too many images on this board", true);
    return figure;
  }
  ctx.imageCount++;
  if (Object.prototype.hasOwnProperty.call(ctx.failed, path)) { fail(String(ctx.failed[path]), true); return figure; }
  const load = () => {
    figure.classList.remove("failed");
    for (const button of figure.querySelectorAll(":scope > .wb-retry")) button.remove();
    frame.setAttribute("aria-busy", "true");
    if (zoomable) frame.disabled = false;
    status.textContent = decorative ? "" : "Loading " + basename(p.path);
    ctx.images.push(wbImageAcquire(ctx.boardId, path, (entry) => {
      if (entry.asleep) return fail(ASLEEP, true, load);
      if (entry.error) return fail(entry.error);
      const img = el("img", { alt: p.alt, decoding: "async" });
      img.src = entry.url;
      img.decode().then(() => {
        img.width = img.naturalWidth; // with height:auto, the box keeps the image's proportions
        img.height = img.naturalHeight;
        frame.removeAttribute("aria-busy");
        frame.replaceChildren(img);
      }, () => fail("this browser could not decode it"));
    }));
  };
  load();
  if (zoomable) frame.addEventListener("click", () => { if (!frame.hasAttribute("aria-busy")) figure.classList.toggle("zoomed"); });
  return figure;
}

// A Document: media is fetched as a blob this board owns (released with it); anything else is read
// over the socket and rendered as Markdown, a table, an isolated HTML report, or text with line numbers.
function wbDocument(node, ctx) {
  const p = node.props, path = wbTarget(node);
  const name = basename(p.path);
  const kind = fileKind(p.path);
  const sizeNote = el("span", { class: "small muted" });
  const note = el("p", { class: "small muted wb-doc-note", role: "status" });
  const body = el("div", { class: "wb-doc-body" });
  const box = el("section", { class: "wb-doc" }, [
    el("div", { class: "wb-doc-head" }, [el("strong", null, p.title || name), p.title ? el("span", { class: "mono", title: p.path }, name) : null, sizeNote]),
    note, body,
  ]);
  let url = "", controller = null, gone = false;
  ctx.cleanups.push(() => { gone = true; if (controller) controller.abort(); if (url) URL.revokeObjectURL(url); });
  const busy = (text) => { box.classList.remove("failed"); body.setAttribute("aria-busy", "true"); note.textContent = ""; body.replaceChildren(el("span", { class: "small muted" }, text)); };
  const show = (child) => { body.removeAttribute("aria-busy"); body.replaceChildren(child); };
  const fail = (reason, known, retry) => {
    if (gone) return;
    box.classList.add("failed");
    body.removeAttribute("aria-busy");
    body.replaceChildren(el("span", null, (retry ? "" : "Not shown: ") + reason), retry ? wbRetry(retry) : null);
    if (!known && !retry) wbShownFailed(ctx, "document", path, reason);
  };
  if (ctx.docCount >= WB_DOC_MAX) {
    ctx.errors.push("line " + node.line + ": a board shows at most " + WB_DOC_MAX + " documents; " + path + " was left out");
    fail("too many documents on this board", true);
    return box;
  }
  ctx.docCount++;
  if (Object.prototype.hasOwnProperty.call(ctx.failed, path)) { fail(String(ctx.failed[path]), true); return box; }
  const failed = (error, retry) => error.asleep ? fail(ASLEEP, true, retry) : error.offline ? fail("Not connected.", true, retry) : fail(error.error || error.message);
  async function loadMedia(force) {
    busy("Loading " + name);
    if (url) { URL.revokeObjectURL(url); url = ""; }
    controller = new AbortController();
    let blob;
    try {
      blob = await fetchPaired(fileUrl(ctx.boardId, path), force ? 0 : WARN_BYTES, controller);
    } catch (error) {
      if (gone) return;
      if (error.tooBig) {
        sizeNote.textContent = size(error.tooBig);
        return show(el("div", null, [
          el("p", null, "This file is " + size(error.tooBig) + ". Loading it may take a while and use mobile data."),
          el("button", { type: "button", class: "primary", onclick: () => loadMedia(true) }, "Load anyway"),
        ]));
      }
      return failed(error, () => loadMedia(force));
    }
    if (gone) return;
    controller = null;
    url = URL.createObjectURL(blob);
    sizeNote.textContent = size(blob.size);
    show(mediaNode(kind, url, name, note));
  }
  function loadText() {
    busy("Opening " + name);
    const sent = request({ type: "read", path, board_id: ctx.boardId }, {
      done(m) {
        if (gone) return;
        sizeNote.textContent = [size(m.size), m.truncated ? "truncated: only the first part is shown" : ""].filter(Boolean).join(", ");
        const text = String(m.text || "");
        if (kind === "markdown") return show(markdownBlock(text));
        if (kind === "html") return show(htmlBlock(text, name, note));
        if (kind === "json") {
          try { return show(codeBlock(JSON.stringify(JSON.parse(text), null, 2))); }
          catch (error) { note.textContent = m.truncated ? "Too long to pretty-print; shown as is." : "Not valid JSON; shown as is."; }
        }
        if (kind === "csv" || kind === "tsv") return show(tableBlock(text, kind === "tsv" ? "\t" : ",", note));
        show(codeBlock(text));
      },
      fail(error) { failed(error, loadText); },
    });
    if (!sent) fail("Not connected.", true, loadText);
  }
  if (MEDIA_KINDS.has(kind)) loadMedia(false); else loadText();
  return box;
}

// An open board's image or Document that did not show reaches the Orchestrator once, as a whiteboard_error.
const wbShownFailures = new Set();
function wbShownFailed(ctx, what, path, reason) {
  if (!ctx.open) return;
  const key = ctx.boardId + "\u0001" + path;
  if (wbShownFailures.has(key)) return;
  if (send({ type: "board_error", id: ctx.boardId, error: "the " + what + " " + path + " was not shown (" + reason + "); the rest of the board is drawn" })) wbShownFailures.add(key);
}
function wbRelease(entry) {
  for (const image of entry.images || []) wbImageRelease(image);
  for (const cleanup of entry.cleanups || []) cleanup();
  entry.images = [];
  entry.cleanups = [];
}

// ---- Access Elements (DOMAIN.md: Access Grant). The program names the element and its target; the
// grant behind it arrives in board.access, keyed "kind:target", and may change (expire, be revoked)
// without the board being redrawn, so each card reads it afresh from ctx.live and on a timer. A click
// sends open and the server mints a bearer credential for this open; nothing opens by itself.
const WB_ACCESS = {
  Terminal: { kind: "terminal", button: "Open terminal", tab: true, label: () => "Terminal" },
  Desktop: { kind: "desktop", button: "Open desktop", tab: true, label: () => "Desktop" },
  Preview: { kind: "preview", button: "Open preview", tab: true, label: (t) => "Preview of port " + t },
  SshAccess: { kind: "ssh", button: "Show SSH command", label: () => "SSH access" },
  Download: { kind: "download", button: "Download", label: (t) => basename(t) },
};
function wbGrant(board, key) {
  const grant = board.access[key];
  if (!grant || typeof grant !== "object") return { live: false, tone: "warning", text: "not available: ask on the call" };
  if (grant.revoked) return { live: false, tone: "neutral", text: "revoked" };
  const at = Number(grant.expires_at);
  if (!(at > Date.now())) return { live: false, tone: "neutral", text: "expired" };
  return { live: true, tone: "success", text: "open until " + clockDay(at) };
}
// Whether any card holds something open that only a click brings back (an inline preview, an SSH command).
function wbAccessOpen() {
  for (const entry of wbBoards.values()) if (entry.node.querySelector(".wb-access[data-open]")) return true;
  return false;
}

function wbAccess(node, ctx) {
  const spec = WB_ACCESS[node.type], p = node.props;
  const target = node.type === "Terminal" || node.type === "Desktop" || node.type === "SshAccess" ? "" : wbTarget(node);
  const key = spec.kind + ":" + target;
  const inline = node.type === "Preview" && p.inline === true;
  const grantBadge = el("span", { class: "wb-badge" });
  const say = el("span", { class: "small wb-access-say", role: "status" });
  const button = el("button", { type: "button", class: "primary" }, spec.button);
  const extra = el("div", { hidden: true });
  const card = el("section", { class: "wb-access", "data-access": key }, [
    el("div", { class: "wb-access-head" }, [el("strong", null, p.label || spec.label(target)), grantBadge]),
    el("div", { class: "wb-access-row" }, [button, say]),
    spec.kind === "terminal" ? el("p", { class: "small muted" }, "Each open starts a fresh shell. For persistent work, run tmux and reattach after reopening. Access may take about a minute to close after expiry or revocation.") : null,
    extra,
  ]);
  const doc = () => card.ownerDocument || document;
  const close = () => { extra.hidden = true; extra.replaceChildren(); card.removeAttribute("data-open"); };
  let busy = false;
  const refresh = () => {
    const grant = wbGrant(ctx.live.board, key);
    grantBadge.className = "wb-badge tone-" + grant.tone;
    grantBadge.textContent = grant.text;
    card.classList.toggle("dead", !grant.live);
    button.classList.toggle("primary", grant.live);
    button.disabled = busy || !grant.live;
    if (!grant.live && card.hasAttribute("data-open")) { close(); say.textContent = "Closed: the access " + (grant.text === "revoked" ? "was revoked." : "expired."); }
  };
  const failed = (error) => error.asleep ? ASLEEP : error.offline ? "Not connected." : "Could not open: " + error.error;
  const finish = (text) => { busy = false; say.textContent = text; refresh(); };
  const open = (then, abandon) => {
    busy = true;
    refresh();
    say.textContent = "Opening";
    const sent = request({ type: "open", board_id: ctx.boardId, key }, {
      done(m) { finish(""); then(m); },
      fail(error) { if (abandon) abandon(); finish(failed(error)); },
    });
    if (!sent) { if (abandon) abandon(); finish("Not connected."); }
  };
  button.addEventListener("click", () => {
    if (spec.kind === "download") return download();
    if (spec.tab && !inline) {
      // A blank tab opened inside the click is allowed by popup blockers, and gets its real address
      // when the reply arrives. It opens from the button's own window (this page or the whiteboard's tab).
      const tab = (doc().defaultView || window).open("about:blank", "_blank");
      if (!tab) { say.textContent = "The browser blocked the new tab. Allow pop-ups for this site and try again."; return; }
      return open((m) => {
        if (typeof m.url !== "string" || !/^https:\/\//.test(m.url)) { tab.close(); say.textContent = "Could not open: no address came back."; return; }
        tab.opener = null;
        tab.location.href = m.url;
      }, () => tab.close());
    }
    if (inline) {
      return open((m) => {
        if (typeof m.url !== "string" || !/^https:\/\//.test(m.url)) { say.textContent = "Could not open: no address came back."; return; }
        extra.replaceChildren(el("div", { class: "wb-preview" }, [
          el("iframe", { src: m.url, title: p.label || spec.label(target), sandbox: "allow-scripts allow-same-origin allow-forms allow-popups", referrerpolicy: "no-referrer" }),
          el("p", { class: "small" }, [
            el("a", { href: m.url, target: "_blank", rel: "noopener noreferrer" }, "Open in a new tab"), " ",
            el("button", { type: "button", class: "mini", onclick: close }, "Close"),
          ]),
        ]));
        extra.hidden = false;
        card.setAttribute("data-open", "");
        button.textContent = "Reload preview";
      });
    }
    open((m) => {
      const ssh = m.ssh && typeof m.ssh === "object" ? m.ssh : null;
      if (!ssh || typeof ssh.command !== "string") { say.textContent = "Could not open: no command came back."; return; }
      const copy = el("button", { type: "button", class: "mini", onclick: () => copyText(ssh.command, copy) }, "Copy");
      extra.replaceChildren(el("div", { class: "wb-ssh" }, [
        el("pre", null, ssh.command),
        el("div", { class: "wb-access-row" }, [copy,
          Number(ssh.expires_at) ? el("span", { class: "small muted" }, "Works until " + clockDay(Number(ssh.expires_at)) + ".") : null,
          el("button", { type: "button", class: "mini", onclick: close }, "Hide")]),
      ]));
      extra.hidden = false;
      card.setAttribute("data-open", "");
    });
  });
  // A Download is read when it is opened: fetched with the pairing token, then saved under its own name.
  async function download() {
    busy = true;
    refresh();
    say.textContent = "Preparing the download";
    let blob;
    try {
      blob = await fetchPaired(fileUrl(ctx.boardId, target, "&download=1"), 0, new AbortController());
    } catch (error) {
      return finish(error.asleep ? ASLEEP : "Download failed: " + error.message);
    }
    const url = URL.createObjectURL(blob);
    const link = doc().createElement("a");
    link.href = url;
    link.download = basename(target);
    doc().body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    finish("");
  }
  ctx.cards.push(refresh);
  refresh();
  return card;
}

// ---- One board: rendered program, errors, and the answer bar (or the answer, once given).
// Writeback (as in OpenChamber): a board with fields gets Send and a note; a Button press sends the
// whole form with its label and value. Buttons whose values name two or more items are decisions:
// they collect per item and send once every item has one, or early with Send now.
function wbBoardNode(board) {
  const ctx = {
    errors: [], fields: [], buttons: [], items: [], controls: [], boardId: board.id, open: board.status === "open", failed: board.failed,
    images: [], imageCount: 0, docCount: 0, cleanups: [], cards: [], live: { board },
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
    return wbBuilt(node, ctx);
  }
  if (!ctx.fields.length && !ctx.buttons.length) return wbBuilt(node, ctx);

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
  return wbBuilt(node, ctx);
}

function wbBuilt(node, ctx) {
  return { node, errors: ctx.errors, images: ctx.images, cleanups: ctx.cleanups, cards: ctx.cards, live: ctx.live };
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

// ---- The boards on this page: id -> { board, key, node, fold, errors, images, cleanups, cards, live }
const wbBoards = new Map();
const wbErrorsSent = new Set();
// Held by reference, not looked up by id: the section moves into the whiteboard's own tab and back.
const WB = { root: $("whiteboard"), empty: $("wbEmpty"), current: $("wbCurrent"), older: $("wbOlder"), olderCount: $("wbOlderCount"), olderList: $("wbOlderList") };
let wbEdited = false;   // a board has input not yet sent, so a stale page waits to reload
WB.root.addEventListener("input", () => { wbEdited = true; });
WB.root.addEventListener("change", () => { wbEdited = true; });

const wbObject = (v) => v && typeof v === "object" && !Array.isArray(v) ? v : {};
function wbNormalize(raw) {
  if (!raw || typeof raw !== "object" || raw.id == null) return null;
  return {
    id: String(raw.id), at: Number(raw.at) || Date.now(), program: String(raw.program || ""), note: String(raw.note || "").slice(0, 300),
    status: raw.status === "answered" ? "answered" : "open", answer: raw.answer && typeof raw.answer === "object" ? raw.answer : null,
    failed: wbObject(raw.failed), access: wbObject(raw.access),
  };
}
// Rebuilds a board only when what it shows changed, so an update never wipes half-entered input or
// closes an open Access Element; a grant that changes alone only refreshes the cards.
function wbUpsert(board) {
  const key = [board.status, board.note, board.program, JSON.stringify(board.answer), JSON.stringify(board.failed)].join("\u0001");
  const known = wbBoards.get(board.id);
  if (known && known.key === key) {
    known.board = board;
    known.live.board = board;
    for (const refresh of known.cards) refresh();
    return false;
  }
  const built = wbBoardNode(board); // acquires its images before the old node releases them, so shared ones stay loaded
  if (known) { known.node.remove(); wbRelease(known); }
  const entry = Object.assign({ board, key, fold: known ? known.fold : null }, built);
  wbBoards.set(board.id, entry);
  wbReportErrors(entry);
  return !known;
}
function wbReportErrors(entry) {
  const b = entry.board;
  if (!entry.errors.length || b.status !== "open" || wbErrorsSent.has(b.id)) return;
  if (send({ type: "board_error", id: b.id, error: entry.errors.slice(0, 12).join("\n").slice(0, 2000) })) wbErrorsSent.add(b.id);
}
function wbOnBoard(raw) {
  const board = wbNormalize(raw);
  if (!board) return;
  const fresh = wbUpsert(board);
  if (board.status !== "open") wbEdited = false;
  wbLayout();
  if (!fresh) return;
  log("whiteboard: " + (board.note || board.id));
  wbFlash(wbBoards.get(board.id).node);
}
// On connecting: the server's boards for this Display replace this page's.
function wbOnBoards(list) {
  const boards = (Array.isArray(list) ? list : []).map(wbNormalize).filter(Boolean);
  const ids = new Set(boards.map((b) => b.id));
  for (const id of [...wbBoards.keys()]) if (!ids.has(id)) { wbBoards.get(id).node.remove(); wbRelease(wbBoards.get(id)); wbBoards.delete(id); }
  for (const b of boards) {
    if (!wbUpsert(b)) wbReportErrors(wbBoards.get(b.id)); // a report that could not go out before the socket dropped
  }
  wbLayout();
}
// The newest board on top; older ones, newest first, each folded beneath it.
function wbLayout() {
  const entries = [...wbBoards.values()].sort((a, b) => a.board.at - b.board.at);
  while (entries.length > WB_KEEP) { const old = entries.shift(); old.node.remove(); wbRelease(old); wbBoards.delete(old.board.id); }
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
// A new board flashes once, so a glance across the room catches the change.
function wbFlash(node) {
  if (!node) return; // the reduced-motion rule turns the animation off
  node.classList.remove("flash");
  void node.offsetWidth; // restart the animation
  node.classList.add("flash");
}
// Pairing ends or the page goes away: every board's files are released, so every blob URL is revoked.
function wbClear() {
  for (const entry of wbBoards.values()) wbRelease(entry);
  wbBoards.clear();
  wbErrorsSent.clear();
  wbShownFailures.clear();
  wbLayout();
  wbCloseTab();
}
// Grants expire on the clock, not by message: re-read every card.
setInterval(() => { for (const entry of wbBoards.values()) for (const refresh of entry.cards) refresh(); }, 5000);


// ---- The whiteboard in its own tab: a blank tab this page fills and keeps driving, so the one paired
// socket stays here and the server sees nothing new. The section's nodes move there with their listeners;
// closing that tab (or Bring it back) moves them home, and leaving this page closes it.
let wbTab = null;
let wbTabTimer = null;
const wbTabOpen = () => !!wbTab && !wbTab.closed;
// Must run inside the click: browsers open new tabs only from one.
function wbOpenTab() {
  if (wbTabOpen()) { wbTab.focus(); return; }
  const tab = window.open("", "_blank");
  if (!tab) { $("barNote").textContent = "The browser blocked the new tab. Allow pop-ups for this site and try again."; return; }
  const doc = tab.document;
  doc.open();
  doc.write("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Dogwalk: Whiteboard</title></head><body></body></html>");
  doc.close();
  doc.head.append(document.querySelector("style").cloneNode(true));
  doc.body.append(el("div", { class: "page" }, [
    el("div", { class: "display-bar" }, el("div", { class: "display-id" }, [
      el("h1", { class: "display-name", "data-display-name": true }),
      el("div", { class: "small muted" }, "This tab follows your Paired Browser tab, so keep that one open; answer here or aloud."),
    ])),
    WB.root,
  ]));
  wbTab = tab;
  $("wbAway").hidden = false;
  $("barNote").textContent = "";
  renderDisplay();
  tab.addEventListener("pagehide", wbTabClosed);
  clearInterval(wbTabTimer);
  wbTabTimer = setInterval(() => { if (!wbTabOpen()) wbTabClosed(); }, 1000);
}
function wbTabClosed() {
  if (!wbTab) return;
  wbTab = null;
  clearInterval(wbTabTimer);
  $("wbHome").append(WB.root);
  $("wbAway").hidden = true;
}
function wbCloseTab() {
  const tab = wbTab;
  wbTabClosed();
  if (tab && !tab.closed) tab.close();
}
$("wbPopOut").addEventListener("click", wbOpenTab);
$("wbBack").addEventListener("click", wbCloseTab);
addEventListener("pagehide", wbCloseTab);
addEventListener("pagehide", (event) => { if (!event.persisted) for (const entry of wbBoards.values()) wbRelease(entry); });
// ==== WHITEBOARD END ====

// ---- Start
$("pairForm").addEventListener("submit", (event) => {
  event.preventDefault();
  claim($("pairCode").value);
});
$("unpair").addEventListener("click", unpair);
$("wakeToggle").checked = wakeWanted;
$("wakeToggle").disabled = !("wakeLock" in navigator);
if (!("wakeLock" in navigator)) $("wakeToggle").parentNode.title = "This browser cannot keep the screen on.";
$("wakeToggle").addEventListener("change", toggleWake);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  acquireWake();
  // Backgrounded tabs lose their sockets: reconnect at once on return instead of waiting out the backoff.
  if (!socket && sessionStorage.getItem(TOKEN_KEY)) { backoff = 1000; connect(); }
});
// Relative timestamps and the expiry countdown tick together.
function tick() {
  for (const node of document.querySelectorAll("time[data-at]")) node.textContent = ago(Number(node.dataset.at));
  if (wbTabOpen()) for (const node of wbTab.document.querySelectorAll("time[data-at]")) node.textContent = ago(Number(node.dataset.at));
  renderPairing();
}
setInterval(tick, 15000);
renderDisplay();
acquireWake();
if (sessionStorage.getItem(TOKEN_KEY)) connect();
else { showScreen("pair"); setStatus("Enter the pairing code spoken in the call"); }
</script>
</body>
</html>
`;

// FNV-1a, 32 bits: enough to tell one deployed page from another.
function fnv1a(text: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index++) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export const PAIR_BUILD = fnv1a(PAIR_TEMPLATE);
export const PAIR_HTML = PAIR_TEMPLATE.replace("__PAIR_BUILD__", PAIR_BUILD);
