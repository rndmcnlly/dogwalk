// Public earcon soundboard and cheat sheet, served at GET /sounds: every earcon grouped by
// what it means in a call, playable in each Earcon Style, then the System Menu prompts.
// WAVs come straight from the bundled clips in audio.ts, so the page plays exactly what a
// call would (before the phone line narrows it; the page's phone filter approximates that).
//
//   GET /sounds                        the page
//   GET /sounds/<tonal|spoken>/<earcon>.wav
//   GET /sounds/prompt/<prompt>.wav
//
// The CSS and page script are String.raw constants with no backtick and no dollar-brace, so
// they reach the browser as written; only the markup is built with interpolation, and every
// interpolated string goes through esc().

import {
  base64ToBytes, earcon, EARCON_NAMES, EARCON_STYLES, pcmToWav, prompt, PROMPT_NAMES,
  type Clip, type EarconName, type EarconStyle, type PromptName,
} from "./audio";

interface EventInfo {
  title: string;
  meaning: string;
  // The spoken style's label, rendered by make_earcons.py (presentation, may use the fiction).
  label: string;
}

// Neutral titles and plain-language meanings; Record keeps the table complete as earcons change.
const EVENTS: Record<EarconName, EventInfo> = {
  call_started: {
    title: "Call connected, sandbox waking",
    meaning: "The very first sound of a call when your Sandbox was asleep. It ends unresolved on purpose: the Voice Agent can talk at once, but the coding agents are not reachable yet.",
    label: "Line open.",
  },
  dogs_ready: {
    title: "Sandbox ready",
    meaning: "The Sandbox finished waking during the call and the ACP Agents are reachable. It completes the phrase the opening sound left hanging.",
    label: "Dogs awake.",
  },
  call_started_ready: {
    title: "Call connected, sandbox already awake",
    meaning: "The first sound of a call when the Sandbox was already running: the whole opening phrase at once, since no ready sound will follow. Also the preview when you change sound style.",
    label: "Line open. Dogs awake.",
  },
  session_created: {
    title: "Session opened",
    meaning: "A new Managed Session started, or a saved one was resumed, under a pronounceable Alias.",
    label: "New dog.",
  },
  session_closed: {
    title: "Session closed",
    meaning: "A Managed Session was closed. Its history is kept, so it can be reopened on a later call.",
    label: "Dog home.",
  },
  turn_started: {
    title: "Prompt Turn started",
    meaning: "A session's Agent took on new work. Starting a turn also follows that session, so this sound stands in for the following sound.",
    label: "Dog sent.",
  },
  turn_stopped: {
    title: "Prompt Turn finished",
    meaning: "The Agent finished its turn normally and has a result to report. The session stays available for more work.",
    label: "Dog back.",
  },
  turn_cancelled: {
    title: "Prompt Turn cancelled",
    meaning: "A turn was cancelled before it finished, usually because you asked. The session itself stays open.",
    label: "Dog recalled.",
  },
  turn_truncated: {
    title: "Prompt Turn cut short",
    meaning: "The turn hit a token or request limit and stopped partway. Whatever it did may be incomplete.",
    label: "Dog ran out of room.",
  },
  turn_refused: {
    title: "Prompt Turn refused",
    meaning: "The Agent declined the request and ended its turn without doing the work.",
    label: "Dog refused.",
  },
  turn_failed: {
    title: "Prompt Turn failed",
    meaning: "The turn broke on an error rather than ending with a stop reason. The Voice Agent will say what went wrong.",
    label: "Errand failed.",
  },
  follow_started: {
    title: "Following a session",
    meaning: "The Voice Agent began keeping up with one session's activity. Progress sounds and short updates about it follow.",
    label: "Following.",
  },
  follow_stopped: {
    title: "Stopped following",
    meaning: "The Voice Agent stopped keeping up with a session. It keeps working, unobserved, until it finishes or asks for attention.",
    label: "Ranging.",
  },
  progress_looking: {
    title: "Looking around",
    meaning: "The followed session moved on to reading: opening files, searching, fetching.",
    label: "Sniffing.",
  },
  progress_changing: {
    title: "Changing files",
    meaning: "The followed session moved on to editing, including the first change of a turn.",
    label: "Digging.",
  },
  progress_running: {
    title: "Running something",
    meaning: "The followed session moved on to running a command: tests, a build, a script.",
    label: "Running.",
  },
  progress_still_running: {
    title: "Still running",
    meaning: "A command in the followed session has been going for a while (about 25 seconds or more).",
    label: "Still running.",
  },
  progress_remark: {
    title: "Interim remark",
    meaning: "The Agent said something between tool calls. The Voice Agent may pass it on in a quiet moment.",
    label: "Woof.",
  },
  progress_step: {
    title: "Next plan step",
    meaning: "The Agent moved on to a new step of its plan.",
    label: "Next step.",
  },
  progress_plan_done: {
    title: "Plan complete",
    meaning: "Every step of the Agent's plan is checked off. The turn itself may still be wrapping up.",
    label: "Plan done.",
  },
  progress_failed: {
    title: "A step failed",
    meaning: "A tool call failed in the followed session. One is routine; several in a row usually mean trouble.",
    label: "Stumbled.",
  },
  progress_tiring: {
    title: "Context nearly full",
    meaning: "The followed session has used most of its context window (about 80 percent). A fresh session may soon serve better.",
    label: "Tiring.",
  },
  permission_requested: {
    title: "Permission Request",
    meaning: "An Agent wants to do something that needs your approval. Nothing is approved on your behalf: the Voice Agent reads you the Agent's own options and waits for your answer.",
    label: "Permission needed.",
  },
  pull: {
    title: "Pull",
    meaning: "A session you are not following wants attention, for example after several failed tool calls in a row. The Voice Agent mentions it and offers to follow; it never switches on its own.",
    label: "Tug.",
  },
  recording_started: {
    title: "Recording started",
    meaning: "A voice note began recording into the Workspace. Press # to save it or * to discard it.",
    label: "Recording.",
  },
  recording_saved: {
    title: "Recording saved",
    meaning: "The voice note was saved as a Workspace file an Agent can work with.",
    label: "Recording saved.",
  },
  recording_discarded: {
    title: "Recording discarded",
    meaning: "The voice note was thrown away; nothing was written.",
    label: "Recording discarded.",
  },
};

interface Group {
  id: string;
  title: string;
  intro: string;
  names: EarconName[];
}

const GROUPS: Group[] = [
  {
    id: "call",
    title: "Call and readiness",
    intro: "How a call opens. The opening is one musical phrase split across two events, so you can hear whether the Sandbox was already awake.",
    names: ["call_started", "dogs_ready", "call_started_ready"],
  },
  {
    id: "turns",
    title: "Sessions and Prompt Turn outcomes",
    intro: "Sessions opening and closing, and each way a Prompt Turn can end. How a turn ended matters more than that it ended, so every outcome has its own sound.",
    names: ["session_created", "session_closed", "turn_started", "turn_stopped", "turn_cancelled", "turn_truncated", "turn_refused", "turn_failed"],
  },
  {
    id: "following",
    title: "Following and Progress Beats",
    intro: "While the Voice Agent follows one session, short quiet beats mark changes in what it is doing. They are meant for the edge of your attention.",
    names: ["follow_started", "follow_stopped", "progress_looking", "progress_changing", "progress_running", "progress_still_running", "progress_remark", "progress_step", "progress_plan_done", "progress_failed", "progress_tiring"],
  },
  {
    id: "attention",
    title: "Attention and Pulls",
    intro: "Sounds that ask something of you. The more repeats, the more it needs an answer.",
    names: ["permission_requested", "pull"],
  },
  {
    id: "recording",
    title: "Recording",
    intro: "Voice notes, recorded like voicemail into the Workspace.",
    names: ["recording_started", "recording_saved", "recording_discarded"],
  },
];

// Anything audio.ts gains before this page is updated still shows up, untitled but playable.
const ungrouped = EARCON_NAMES.filter((name) => !GROUPS.some((group) => group.names.includes(name)));
if (ungrouped.length) {
  GROUPS.push({ id: "other", title: "Other", intro: "Earcons not yet placed on this sheet.", names: ungrouped });
}

const STYLE_INFO: Record<EarconStyle, { label: string; long: string }> = {
  tonal: { label: "Tonal", long: "the tonal style" },
  spoken: { label: "Spoken", long: "the spoken style" },
};

// The prompts' text, as rendered by make_earcons.py.
const PROMPTS: Record<PromptName, { title: string; text: string }> = {
  menu: { title: "Menu opened", text: "Menu. One, restart the sandbox. Two, recovery options. Star, back." },
  menu_restart_confirm: { title: "Confirm restart", text: "Restarting stops running work. Press one again to confirm." },
  menu_restarting: { title: "Restarting", text: "Restarting the sandbox." },
  menu_back: { title: "Back to the conversation", text: "Back." },
  pairing_cue: { title: "Pairing tones next", text: "Pairing tones." },
};

const HTML_CACHE = "public, max-age=300";
const WAV_CACHE = "public, max-age=86400";

export async function handleSounds(request: Request, url: URL): Promise<Response | null> {
  const path = url.pathname;
  if (path !== "/sounds" && !path.startsWith("/sounds/")) return null;
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("method not allowed\n", { status: 405, headers: { allow: "GET, HEAD" } });
  }
  const head = request.method === "HEAD";
  if (path === "/sounds" || path === "/sounds/") {
    return new Response(head ? null : page(), {
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": HTML_CACHE,
        "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; media-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'",
        "x-content-type-options": "nosniff",
      },
    });
  }
  const match = /^\/sounds\/([a-z]+)\/([a-z_]+)\.wav$/.exec(path);
  const clip = match ? findClip(match[1], match[2]) : null;
  if (!match || !clip) return new Response("no such sound\n", { status: 404, headers: { "content-type": "text/plain; charset=utf-8" } });
  const wav = wavFor(`${match[1]}/${match[2]}`, clip);
  return new Response(head ? null : wav, {
    headers: {
      "content-type": "audio/wav",
      "content-length": String(wav.byteLength),
      "cache-control": WAV_CACHE,
      "x-content-type-options": "nosniff",
    },
  });
}

function findClip(kind: string, name: string): Clip | null {
  if (kind === "prompt") return (PROMPT_NAMES as string[]).includes(name) ? prompt(name as PromptName) : null;
  if (!(EARCON_STYLES as string[]).includes(kind) || !(EARCON_NAMES as string[]).includes(name)) return null;
  return earcon(name as EarconName, kind as EarconStyle);
}

const wavs = new Map<string, Uint8Array<ArrayBuffer>>();
function wavFor(key: string, clip: Clip): Uint8Array<ArrayBuffer> {
  let wav = wavs.get(key);
  if (!wav) wavs.set(key, (wav = pcmToWav([base64ToBytes(clip.base64)])));
  return wav;
}

// --- Page -----------------------------------------------------------------------

function esc(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

function seconds(clip: Clip): string {
  return clip.seconds < 10 ? `${clip.seconds.toFixed(2)} s` : `${clip.seconds.toFixed(1)} s`;
}

function eventRow(name: EarconName): string {
  const info: EventInfo = EVENTS[name] ?? { title: name, meaning: "", label: "" };
  const buttons = EARCON_STYLES.map((style) => {
    const clip = earcon(name, style);
    const { label, long } = STYLE_INFO[style];
    return `<button type="button" class="play" data-src="/sounds/${style}/${name}.wav" data-style="${style}" aria-label="Play ${esc(info.title)} in ${long}, ${seconds(clip)}">`
      + `<span class="tri" aria-hidden="true"></span><span class="sl">${label}${style === "tonal" ? `<em>usual</em>` : ""}</span><span class="dur">${seconds(clip)}</span></button>`;
  }).join("");
  return `<li class="ev" id="${name}">
  <div class="what"><h3>${esc(info.title)}</h3>${info.meaning ? `<p>${esc(info.meaning)}</p>` : ""}
  <p class="meta"><code>${name}</code>${info.label ? `<span>spoken label: &ldquo;${esc(info.label)}&rdquo;</span>` : ""}</p></div>
  <div class="plays" role="group" aria-label="${esc(info.title)}">${buttons}</div>
</li>`;
}

function groupSection(group: Group): string {
  const all = EARCON_STYLES.map((style) =>
    `<button type="button" class="all" data-group="${group.id}" data-style="${style}" aria-label="Play every ${esc(group.title)} sound in ${STYLE_INFO[style].long}">${STYLE_INFO[style].label}</button>`).join("");
  return `<section class="group" id="g-${group.id}" aria-labelledby="h-${group.id}" data-group="${group.id}">
<div class="ghead"><div><h2 id="h-${group.id}">${esc(group.title)}</h2><p class="intro">${esc(group.intro)}</p></div>
<div class="allrow"><span class="eyebrow">Play all</span><span class="seg">${all}</span></div></div>
<ol class="events">${group.names.map(eventRow).join("\n")}</ol>
</section>`;
}

function promptRow(name: PromptName): string {
  const clip = prompt(name);
  const info = PROMPTS[name] ?? { title: name, text: "" };
  return `<li class="ev" id="prompt-${name}">
  <div class="what"><h3>${esc(info.title)}</h3><p class="quote">&ldquo;${esc(info.text)}&rdquo;</p><p class="meta"><code>${name}</code></p></div>
  <div class="plays one" role="group" aria-label="${esc(info.title)}"><button type="button" class="play" data-src="/sounds/prompt/${name}.wav" aria-label="Play the ${esc(info.title)} prompt, ${seconds(clip)}"><span class="tri" aria-hidden="true"></span><span class="sl">Play</span><span class="dur">${seconds(clip)}</span></button></div>
</li>`;
}

let cachedPage: string | null = null;
function page(): string {
  return (cachedPage ??= `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Every Dogwalk earcon, what it means in a call, and how it sounds in the tonal and spoken sound styles.">
<meta name="color-scheme" content="light dark">
<title>Dogwalk: what the call sounds like</title>
<style>${CSS}</style>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<div class="wrap">

<header class="top">
  <a class="mark" href="/" aria-label="Dogwalk home">
    <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true"><rect x="1" y="1" width="30" height="30" rx="9" fill="none" stroke="currentColor" stroke-width="2"/><ellipse class="accent-fill" cx="16" cy="20" rx="5.2" ry="4.2"/><circle cx="9.6" cy="14" r="2" fill="currentColor"/><circle cx="13.6" cy="10.4" r="2" fill="currentColor"/><circle cx="18.4" cy="10.4" r="2" fill="currentColor"/><circle cx="22.4" cy="14" r="2" fill="currentColor"/></svg>
    <span>dogwalk<small>.tools</small></span>
  </a>
  <nav aria-label="Site">
    <a href="/">Home</a>
    <a href="https://github.com/rndmcnlly/dogwalk">Source</a>
  </nav>
</header>

<main id="main">
<div class="hero">
  <p class="kicker">Sounds</p>
  <h1>What the call <em>sounds like.</em></h1>
  <p class="lede">Dogwalk marks events with earcons: short sounds that wait for a lull rather than talk over anyone. There are three sound styles. <b>Tonal</b>, the usual one, is a set of chimes built from a small code. <b>Spoken</b> marks the same events with short spoken labels. <b>None</b> plays no event sounds at all; the recording beep still plays. To change the style, just ask the walker during a call; your choice carries over to later calls.</p>
</div>

<div class="bar" role="region" aria-label="Playback">
  <label class="switch"><input type="checkbox" id="phone"><span>Phone filter</span></label>
  <span class="bar-note">Hear it as a caller does: telephone audio keeps roughly 300&ndash;3400 Hz.</span>
  <button type="button" id="stop" class="stop">Stop</button>
  <p id="now" class="now" role="status" aria-live="polite"></p>
</div>

<section class="key" aria-labelledby="h-key">
  <h2 id="h-key">How to hear the tonal set</h2>
  <ul class="legend">
    <li><b>Contour.</b> Rising means something began, falling means it ended, level means a state.</li>
    <li><b>Harmony.</b> Clean intervals are routine. A rough, sour sound means something went wrong.</li>
    <li><b>Repeats.</b> Once is news. Twice is an optional ask (a Pull). Three times needs your answer.</li>
    <li><b>Loudness.</b> Foreground events are fuller; Progress Beats are short and quiet on purpose.</li>
    <li><b>Timbre.</b> A bright chirp for the call and Sandbox, a glassy beep for a session's Agent, a soft hollow tone for the Voice Agent following, a near-pure tone for your own recordings.</li>
  </ul>
</section>

${GROUPS.map(groupSection).join("\n\n")}

<section class="group" id="g-menu" aria-labelledby="h-menu">
<div class="ghead"><div><h2 id="h-menu">System Menu prompts</h2><p class="intro">Press star during a call for a menu no model listens to. Its prompts are always spoken, whatever the sound style, because they are instructions to follow rather than events to notice.</p></div></div>
<ol class="events">${PROMPT_NAMES.map(promptRow).join("\n")}</ol>
</section>
</main>

<footer class="site">
  <span>Every clip here is the same audio a call plays, served as 24 kHz mono WAV.<br>The phone filter band-limits playback; it does not simulate the codec.</span>
  <span><a href="https://github.com/rndmcnlly/dogwalk/blob/main/SOUND_DESIGN_NOTES.md">Sound design notes</a><br><a href="/">dogwalk.tools</a></span>
</footer>
</div>
<script>${SCRIPT}</script>
</body></html>`);
}

const CSS = String.raw`
:root{
  --paper:#f4efe3;--card:#fbf8f1;--ink:#1b2a24;--body:#33413a;--muted:#56625a;--faint:#8a9188;
  --line:#ddd4c3;--rule:#c6bca7;--leash:#b2441f;--leash-soft:#f1dccf;--grass:#2f5d4a;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,"Cascadia Code",monospace;
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#111815;--card:#18211d;--ink:#ece6d7;--body:#cfd3c8;--muted:#a3ada3;--faint:#6f7a72;
    --line:#26322d;--rule:#34423b;--leash:#ec8a62;--leash-soft:#3a2219;--grass:#8cc2a6;
  }
}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 var(--sans);-webkit-font-smoothing:antialiased}
a{color:inherit}
a:focus-visible,button:focus-visible,input:focus-visible+span{outline:3px solid var(--leash);outline-offset:3px;border-radius:6px}
.wrap{max-width:1040px;margin:0 auto;padding:0 clamp(16px,5vw,48px)}
.skip{position:absolute;left:-999px}
.skip:focus{left:12px;top:12px;background:var(--card);padding:8px 12px}

.top{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:22px 0;flex-wrap:wrap}
.mark{display:inline-flex;align-items:center;gap:10px;font-weight:700;letter-spacing:-.02em;text-decoration:none;font-size:19px}
.mark svg{flex:none}
.accent-fill{fill:var(--leash)}
.mark small{color:var(--muted);font-weight:500}
.top nav{display:flex;gap:18px;font:500 13px/1 var(--mono);flex-wrap:wrap}
.top nav a{text-decoration:none;color:var(--muted);padding:6px 0;border-bottom:1px solid transparent}
.top nav a:hover{color:var(--ink);border-color:var(--rule)}

.hero{padding:clamp(24px,5vw,52px) 0 clamp(20px,4vw,36px);border-top:1px solid var(--rule)}
.kicker,.eyebrow{margin:0 0 14px;font:600 12px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--leash)}
h1{margin:0;font:600 clamp(32px,5.4vw,58px)/1.06 var(--serif);letter-spacing:-.02em;text-wrap:balance}
h1 em{font-style:italic;font-weight:500;color:var(--leash)}
.lede{margin:20px 0 0;max-width:40em;font-size:clamp(16.5px,1.7vw,19px);color:var(--body)}
.lede b{color:var(--ink);font-weight:650}

.bar{position:sticky;top:0;z-index:2;display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;margin:0 0 8px;padding:12px 14px;background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:0 10px 24px -22px rgba(20,30,25,.6)}
.bar-note{flex:1 1 16em;font-size:13.5px;color:var(--muted)}
.now{flex-basis:100%;margin:0;min-height:1.4em;font:500 12.5px/1.4 var(--mono);color:var(--muted)}
.now:empty{display:none}
.switch{display:inline-flex;align-items:center;gap:10px;font:600 14px/1 var(--sans);cursor:pointer}
.switch input{position:absolute;opacity:0;width:1px;height:1px}
.switch span::before{content:"";display:inline-block;vertical-align:middle;width:36px;height:20px;margin-right:10px;border-radius:999px;background:var(--line);box-shadow:inset 0 0 0 1.5px var(--rule);transition:background .15s}
.switch span{position:relative}
.switch span::after{content:"";position:absolute;left:3px;top:50%;width:14px;height:14px;margin-top:-7px;border-radius:50%;background:var(--paper);box-shadow:0 0 0 1.5px var(--rule);transition:transform .15s}
.switch input:checked+span::before{background:var(--leash)}
.switch input:checked+span::after{transform:translateX(16px);box-shadow:none}
button{font:inherit;color:inherit;cursor:pointer}
.stop{padding:8px 16px;border-radius:999px;border:1.5px solid var(--ink);background:transparent;font:600 14px/1 var(--sans)}
.stop:hover{background:var(--ink);color:var(--paper)}

section{padding:clamp(28px,5vw,52px) 0 8px;border-top:1px solid var(--rule)}
.bar+section{border-top:0}
h2{margin:0 0 8px;font:600 clamp(23px,3vw,32px)/1.15 var(--serif);letter-spacing:-.015em;text-wrap:balance}
.intro{margin:0;max-width:40em;color:var(--body);font-size:15.5px}
.legend{list-style:none;margin:14px 0 0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:0 36px}
.legend li{padding:11px 0;border-top:1px solid var(--line);font-size:14.5px;color:var(--muted)}
.legend b{color:var(--ink)}

.ghead{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:14px 28px;margin-bottom:12px}
.allrow{display:flex;flex-direction:column;gap:6px}
.allrow .eyebrow{margin:0;color:var(--muted);font-size:11px}
.seg{display:inline-flex;border:1.5px solid var(--rule);border-radius:999px;overflow:hidden}
.seg button{border:0;background:transparent;padding:8px 14px;font:600 13px/1 var(--mono)}
.seg button+button{border-left:1.5px solid var(--rule)}
.seg button:hover{background:var(--leash-soft)}
.seg button.playing{background:var(--leash);color:var(--paper)}

.events{list-style:none;margin:0;padding:0}
.ev{display:grid;grid-template-columns:1fr;gap:10px 28px;padding:16px 0 18px;border-top:1px solid var(--line);scroll-margin-top:90px}
@media(min-width:760px){.ev{grid-template-columns:1fr auto;align-items:center}}
.what h3{margin:0 0 2px;font:650 16.5px/1.35 var(--sans)}
.what p{margin:0;font-size:14.5px;line-height:1.55;color:var(--muted);max-width:44em}
.what p.quote{color:var(--body);font-family:var(--serif);font-size:16px}
.what .meta{margin-top:6px;display:flex;flex-wrap:wrap;gap:4px 14px;font:500 12px/1.5 var(--mono);color:var(--faint)}
.what code{font:inherit;color:var(--muted)}
.plays{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.plays.one{grid-template-columns:minmax(0,1fr)}
@media(min-width:760px){.plays{grid-template-columns:repeat(2,108px)}.plays.one{grid-template-columns:108px}}
.play{position:relative;display:grid;grid-template-columns:auto 1fr;grid-template-rows:auto auto;column-gap:8px;align-items:center;min-height:52px;padding:8px 10px;border-radius:12px;border:1.5px solid var(--rule);background:var(--card);text-align:left}
.play:hover{border-color:var(--ink)}
.play .tri{grid-row:1/3;width:0;height:0;border-left:10px solid var(--leash);border-top:6.5px solid transparent;border-bottom:6.5px solid transparent}
.play .sl{font:600 14px/1.2 var(--sans)}
.play .sl em{display:block;font:600 9.5px/1.3 var(--mono);font-style:normal;letter-spacing:.1em;text-transform:uppercase;color:var(--leash)}
.play .dur{font:500 11px/1.3 var(--mono);color:var(--faint)}
.play.playing{background:var(--leash-soft);border-color:var(--leash)}
.play.playing .tri{border:0;width:10px;height:13px;background:linear-gradient(90deg,var(--leash) 0 3.5px,transparent 3.5px 6.5px,var(--leash) 6.5px)}
.play.failed{border-style:dashed}

footer.site{display:flex;justify-content:space-between;gap:16px 32px;flex-wrap:wrap;margin-top:40px;padding:28px 0 56px;border-top:1px solid var(--rule);font:500 12.5px/1.7 var(--mono);color:var(--muted)}
footer.site a{text-underline-offset:3px;text-decoration-color:var(--rule)}
@media(max-width:520px){.top nav{width:100%}.bar{border-radius:12px}}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
`;

// Web Audio when available (so the phone filter works), an <audio> element otherwise.
// Decoded buffers are cached per URL; one play or one play-all sequence runs at a time.
const SCRIPT = String.raw`
(function(){
  var ctx = null, cache = {}, live = [], timers = [];
  var phone = document.getElementById("phone"), now = document.getElementById("now");
  var fallback = null;

  function audio(){
    if (!ctx){
      var C = window.AudioContext || window.webkitAudioContext;
      if (!C) return null;
      ctx = new C();
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }
  function decode(src){
    if (!cache[src]){
      cache[src] = fetch(src).then(function(r){
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.arrayBuffer();
      }).then(function(bytes){
        return new Promise(function(resolve, reject){ ctx.decodeAudioData(bytes, resolve, reject); });
      });
      cache[src].catch(function(){ delete cache[src]; });
    }
    return cache[src];
  }
  // The PSTN keeps about 300-3400 Hz: two high-pass and two low-pass sections.
  function output(){
    var node = ctx.destination;
    if (!phone.checked) return node;
    var chain = [["lowpass", 3400], ["lowpass", 3400], ["highpass", 300], ["highpass", 300]];
    for (var i = 0; i < chain.length; i++){
      var f = ctx.createBiquadFilter();
      f.type = chain[i][0]; f.frequency.value = chain[i][1]; f.Q.value = 0.707;
      f.connect(node); node = f;
    }
    return node;
  }
  function mark(button, on){ if (button) button.classList.toggle("playing", on); }
  function say(text){ now.textContent = text; }
  function stop(){
    timers.forEach(clearTimeout); timers = [];
    live.forEach(function(item){ try { item.source.stop(); } catch (e) {} mark(item.button, false); });
    live = [];
    if (fallback){ fallback.pause(); fallback = null; }
    document.querySelectorAll(".playing").forEach(function(el){ el.classList.remove("playing"); });
  }
  function title(button){
    var row = button.closest(".ev");
    var h = row && row.querySelector("h3");
    var style = button.querySelector(".sl");
    return (h ? h.textContent : "") + (style && button.dataset.style ? ", " + style.firstChild.textContent : "");
  }

  // Play buttons in order, each after the last ends plus a gap; owner lights up for the whole run.
  function run(buttons, owner, gap){
    stop();
    var ac = audio();
    if (!ac){ return fallbackRun(buttons, owner, gap); }
    mark(owner, true);
    Promise.all(buttons.map(function(b){ return decode(b.dataset.src); })).then(function(buffers){
      var out = output(), at = ac.currentTime + 0.05, start = at;
      buffers.forEach(function(buffer, i){
        var source = ac.createBufferSource(), button = buttons[i];
        source.buffer = buffer; source.connect(out); source.start(at);
        var item = { source: source, button: button };
        live.push(item);
        timers.push(setTimeout(function(){ mark(button, true); say("Playing: " + title(button)); }, (at - ac.currentTime) * 1000));
        source.onended = function(){
          if (live.indexOf(item) < 0) return;
          mark(button, false);
          live = live.filter(function(x){ return x !== item; });
          if (!live.length){ mark(owner, false); say(""); }
        };
        at += buffer.duration + gap;
      });
    }).catch(function(error){
      mark(owner, false);
      buttons.forEach(function(b){ b.classList.add("failed"); });
      say("Could not play: " + error.message);
    });
  }
  function fallbackRun(buttons, owner, gap){
    var i = 0;
    mark(owner, true);
    function next(){
      if (i > 0) mark(buttons[i - 1], false);
      if (i >= buttons.length){ mark(owner, false); say(""); fallback = null; return; }
      var button = buttons[i++];
      fallback = new Audio(button.dataset.src);
      mark(button, true); say("Playing: " + title(button));
      fallback.onended = function(){ timers.push(setTimeout(next, gap * 1000)); };
      fallback.play().catch(function(error){ say("Could not play: " + error.message); stop(); });
    }
    next();
  }

  document.addEventListener("click", function(event){
    var button = event.target.closest("button");
    if (!button) return;
    if (button.id === "stop"){ stop(); say(""); return; }
    if (button.classList.contains("play")){ run([button], null, 0); return; }
    if (button.classList.contains("all")){
      var group = document.querySelector('section[data-group="' + button.dataset.group + '"]');
      var list = Array.prototype.slice.call(group.querySelectorAll('.play[data-style="' + button.dataset.style + '"]'));
      run(list, button, 0.7);
    }
  });
  document.addEventListener("keydown", function(event){
    if (event.key === "Escape"){ stop(); say(""); }
  });
  // The phone filter applies from the next play; restart nothing mid-sound.
  phone.addEventListener("change", function(){ say(phone.checked ? "Phone filter on." : "Phone filter off."); });
})();
`;
