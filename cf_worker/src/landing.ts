export const LANDING_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Dogwalk is an eyes-free ACP client. Phone it and supervise several coding agents by voice, hands and eyes free, while you walk the dog.">
<meta name="color-scheme" content="light dark">
<title>Dogwalk: an eyes-free ACP client</title>
<style>
:root{
  --paper:#f4efe3;--card:#fbf8f1;--ink:#1b2a24;--body:#33413a;--muted:#56625a;--faint:#8a9188;
  --line:#ddd4c3;--rule:#c6bca7;--leash:#b2441f;--leash-soft:#f1dccf;--grass:#2f5d4a;
  --night:#17211d;--night-ink:#ece6d7;--night-muted:#a9b3a9;--night-line:#2d3a34;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,"Cascadia Code",monospace;
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#111815;--card:#18211d;--ink:#ece6d7;--body:#cfd3c8;--muted:#a3ada3;--faint:#6f7a72;
    --line:#26322d;--rule:#34423b;--leash:#ec8a62;--leash-soft:#3a2219;--grass:#8cc2a6;
    --night:#0b100e;--night-line:#26322d;
  }
  .call{border:1px solid var(--rule)}
}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 var(--sans);-webkit-font-smoothing:antialiased}
a{color:inherit}
a:focus-visible,.btn:focus-visible{outline:3px solid var(--leash);outline-offset:3px;border-radius:4px}
.wrap{max-width:1040px;margin:0 auto;padding:0 clamp(18px,5vw,48px)}
.skip{position:absolute;left:-999px}
.skip:focus{left:12px;top:12px;background:var(--card);padding:8px 12px}

/* masthead */
.top{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:22px 0;flex-wrap:wrap}
.mark{display:inline-flex;align-items:center;gap:10px;font-weight:700;letter-spacing:-.02em;text-decoration:none;font-size:19px}
.mark svg{flex:none}
.accent{stroke:var(--leash)}
.accent-fill{fill:var(--leash)}
.mark small{color:var(--muted);font-weight:500}
.top nav{display:flex;gap:18px;font:500 13px/1 var(--mono)}
.top nav a{text-decoration:none;color:var(--muted);padding:6px 0;border-bottom:1px solid transparent}
.top nav a:hover{color:var(--ink);border-color:var(--rule)}

/* hero */
.hero{display:grid;grid-template-columns:1fr;gap:clamp(28px,5vw,56px);padding:clamp(26px,6vw,64px) 0 clamp(40px,6vw,72px);border-top:1px solid var(--rule)}
@media(min-width:880px){.hero{grid-template-columns:1.15fr .85fr;align-items:center}}
.kicker{margin:0 0 16px;font:600 12px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--leash)}
h1{margin:0;font:600 clamp(34px,5.6vw,62px)/1.06 var(--serif);letter-spacing:-.02em;text-wrap:balance}
h1 em{font-style:italic;font-weight:500;color:var(--leash)}
.scene{margin:22px 0 0;max-width:36em;font-size:clamp(17px,1.8vw,20px);color:var(--body)}
.scene b{color:var(--ink);font-weight:650}
.actions{display:flex;flex-wrap:wrap;align-items:center;gap:12px 18px;margin-top:28px}
.btn{display:inline-flex;align-items:center;gap:8px;padding:11px 18px;border-radius:999px;font:600 15px/1 var(--sans);text-decoration:none;border:1.5px solid var(--ink)}
.btn:hover{background:var(--ink);color:var(--paper)}
.btn svg{flex:none}
.invite{font:500 13px/1.5 var(--mono);color:var(--muted)}
.invite a{text-underline-offset:3px;text-decoration-color:var(--rule)}

/* call transcript */
.call{background:var(--night);color:var(--night-ink);border-radius:22px;padding:22px 22px 18px;box-shadow:0 1px 0 rgba(0,0,0,.04),0 18px 40px -24px rgba(20,30,25,.55)}
.call header{display:flex;justify-content:space-between;align-items:center;font:500 12px/1 var(--mono);color:var(--night-muted);letter-spacing:.04em;padding-bottom:14px;border-bottom:1px solid var(--night-line)}
.call header .live{display:inline-flex;align-items:center;gap:8px;color:#9fd6b8}
.call header .live i{width:8px;height:8px;border-radius:50%;background:#5fc28e;box-shadow:0 0 0 4px rgba(95,194,142,.18)}
.call ol{list-style:none;margin:0;padding:6px 0 0}
.call li{padding:9px 0;font-size:14.5px;line-height:1.5}
.call .who{display:block;font:600 11px/1.6 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--night-muted)}
.call .you .who{color:#f0a27f}
.call .cue{font:500 12.5px/1.5 var(--mono);color:#9fd6b8}
.call .cue::before{content:"";display:inline-block;width:18px;height:9px;margin-right:8px;vertical-align:0;background:linear-gradient(90deg,currentColor 0 2px,transparent 2px 5px,currentColor 5px 7px,transparent 7px 10px,currentColor 10px 12px,transparent 12px 15px,currentColor 15px 17px,transparent 17px);opacity:.9}
.call kbd{font:600 12px/1 var(--mono);padding:2px 6px;border:1px solid #4a5a52;border-radius:5px;color:var(--night-ink)}
.call footer{margin-top:8px;padding-top:12px;border-top:1px solid var(--night-line);font:500 11.5px/1.5 var(--mono);color:var(--night-muted)}

/* sections */
section{padding:clamp(40px,6vw,72px) 0;border-top:1px solid var(--rule)}
.eyebrow{margin:0 0 10px;font:600 12px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
h2{margin:0 0 14px;font:600 clamp(26px,3.4vw,38px)/1.12 var(--serif);letter-spacing:-.015em;text-wrap:balance;max-width:24ch}
.intro{margin:0 0 30px;max-width:40em;color:var(--body);font-size:17px}

/* capabilities */
.caps{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,290px),1fr));gap:0 clamp(24px,4vw,48px);margin:0;padding:0;list-style:none}
.caps li{padding:18px 0 20px;border-top:1px solid var(--line)}
.caps h3{margin:0 0 4px;font:650 16px/1.35 var(--sans);color:var(--ink)}
.caps p{margin:0;font-size:14.5px;line-height:1.55;color:var(--muted)}

/* keypad */
.keys{display:grid;grid-template-columns:1fr;gap:clamp(28px,5vw,56px);align-items:start}
@media(min-width:820px){.keys{grid-template-columns:minmax(300px,380px) 1fr}}
.pad{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:0;padding:18px;list-style:none;background:var(--card);border:1px solid var(--line);border-radius:26px}
.pad li{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:78px;padding:8px 4px;border-radius:16px;text-align:center;background:var(--paper);border:1px solid var(--line)}
.pad .d{font:500 26px/1 var(--serif);color:var(--faint)}
.pad .l{font:600 10px/1.2 var(--mono);letter-spacing:.1em;color:var(--faint);margin-top:4px}
.pad .on{background:var(--leash-soft);border-color:transparent}
.pad .on .d{color:var(--leash);font-weight:600}
.pad .on .l{color:var(--ink);letter-spacing:.02em;text-transform:none;font-size:11.5px}
.padnote{margin:12px 4px 0;font-size:13.5px;color:var(--muted)}
.menu h3,.rec h3{margin:0 0 6px;font:650 17px/1.3 var(--sans)}
.menu p,.rec p{margin:0 0 14px;color:var(--muted);font-size:14.5px;max-width:38em}
.menu ol{list-style:none;margin:0 0 30px;padding:0}
.menu li{display:grid;grid-template-columns:52px 1fr;gap:12px;align-items:baseline;padding:9px 0;border-top:1px solid var(--line);font-size:15px}
.menu li:last-child{border-bottom:1px solid var(--line)}
.k{display:inline-block;min-width:34px;padding:4px 7px;border:1.5px solid var(--rule);border-bottom-width:3px;border-radius:8px;font:600 14px/1 var(--mono);text-align:center;color:var(--ink);background:var(--card)}
.menu .new{display:inline-block;margin-left:8px;padding:2px 7px;border-radius:999px;font:600 10.5px/1.4 var(--mono);letter-spacing:.06em;text-transform:uppercase;background:var(--leash);color:var(--paper)}
.rec p .k{min-width:0;font-size:12.5px;padding:2px 6px;border-bottom-width:2px}

/* pairing */
.pair{background:var(--card);border-radius:28px;border:1px solid var(--line);padding:clamp(24px,5vw,52px);display:grid;grid-template-columns:1fr;gap:clamp(24px,4vw,44px)}
@media(min-width:880px){.pair{grid-template-columns:1fr 1fr}}
.pair .tag{display:inline-block;margin:0 0 14px;padding:4px 10px;border-radius:999px;border:1px solid var(--rule);font:600 11px/1.3 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.pair h2{max-width:18ch}
.pair .intro{margin-bottom:22px;font-size:16px}
.pair figure{margin:0 0 6px}
.pair svg.ill{width:100%;height:auto;max-width:440px;display:block;color:var(--ink)}
.steps{list-style:none;margin:0;padding:0;counter-reset:s}
.steps li{counter-increment:s;position:relative;padding:0 0 22px 50px;font-size:15.5px;color:var(--body)}
.steps li::before{content:counter(s);position:absolute;left:0;top:-2px;width:34px;height:34px;border-radius:50%;display:grid;place-items:center;font:600 15px/1 var(--mono);color:var(--paper);background:var(--ink)}
.steps li::after{content:"";position:absolute;left:16.5px;top:36px;bottom:4px;width:1px;background:var(--rule)}
.steps li:last-child::after{display:none}
.steps b{color:var(--ink)}
.steps .k{font-size:12.5px;min-width:0;padding:2px 6px;border-bottom-width:2px}
.fine{margin:4px 0 0;padding:14px 16px;border-radius:14px;background:var(--paper);font-size:14px;color:var(--muted)}
.fine b{color:var(--ink)}

/* pipeline */
.flow{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:1fr;gap:0}
@media(min-width:860px){.flow{grid-template-columns:repeat(5,1fr)}}
.flow li{position:relative;padding:16px 18px 18px 0;border-top:3px solid var(--ink)}
.flow li:nth-child(2),.flow li:nth-child(3){border-color:var(--leash)}
.flow .stage{display:block;font:650 15px/1.3 var(--sans)}
.flow .ctx{display:block;margin:3px 0 8px;font:500 11px/1.4 var(--mono);letter-spacing:.05em;color:var(--muted)}
.flow p{margin:0;font-size:14px;line-height:1.55;color:var(--muted)}
.flow a{text-underline-offset:3px;text-decoration-color:var(--leash)}

footer.site{display:flex;justify-content:space-between;gap:16px 32px;flex-wrap:wrap;padding:28px 0 56px;border-top:1px solid var(--rule);font:500 12.5px/1.7 var(--mono);color:var(--muted)}
footer.site a{text-underline-offset:3px;text-decoration-color:var(--rule)}

@media(max-width:520px){
  .top nav{width:100%}
  .pad{padding:12px;gap:8px}
  .pad li{min-height:70px}
  .call{padding:18px 16px 14px;border-radius:18px}
}
@media(prefers-reduced-motion:no-preference){
  .call header .live i{animation:pulse 2.4s ease-in-out infinite}
  @keyframes pulse{50%{box-shadow:0 0 0 7px rgba(95,194,142,.05)}}
}
</style>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<div class="wrap">

<header class="top">
  <a class="mark" href="/" aria-label="Dogwalk home">
    <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true"><rect x="1" y="1" width="30" height="30" rx="9" fill="none" stroke="currentColor" stroke-width="2"/><ellipse class="accent-fill" cx="16" cy="20" rx="5.2" ry="4.2"/><circle cx="9.6" cy="14" r="2" fill="currentColor"/><circle cx="13.6" cy="10.4" r="2" fill="currentColor"/><circle cx="18.4" cy="10.4" r="2" fill="currentColor"/><circle cx="22.4" cy="14" r="2" fill="currentColor"/></svg>
    <span>dogwalk<small>.tools</small></span>
  </a>
  <nav aria-label="Page">
    <a href="#keypad">Keypad</a>
    <a href="#pair">Pairing</a>
    <a href="https://github.com/rndmcnlly/dogwalk">Source</a>
  </nav>
</header>

<main id="main">

<div class="hero">
  <div>
    <p class="kicker">An eyes-free ACP client</p>
    <h1>Take your coding agents <em>for a walk.</em></h1>
    <p class="scene">Phone in your pocket, earbuds in, leash in hand. You call Dogwalk and <b>start work, hear progress, answer permission requests, and redirect several coding agents at once</b>, all by talking. No screen required, ever.</p>
    <div class="actions">
      <a class="btn" href="/pair">
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="3" y="5" width="18" height="11" rx="1.5"/><path d="M1.5 19h21"/><path d="M8.5 10.5h1M11.5 9v3M14.5 8v5"/></svg>
        Pair a browser
      </a>
      <span class="invite">Optional companion. Calling is invite-only.</span>
    </div>
  </div>

  <aside class="call" aria-label="An illustrative call">
    <header><span class="live"><i aria-hidden="true"></i>On a call</span><span>illustrative</span></header>
    <ol>
      <li class="cue">call-start earcon</li>
      <li><span class="who">Dogwalk</span>Verified. Dogwalk. Morning. Since then, Juniper finished its turn and Pepper is waiting on a permission. Where to?</li>
      <li class="you"><span class="who">You</span>What does Pepper want?</li>
      <li><span class="who">Dogwalk</span>Pepper wants to run the database migration on your dev copy. Allow it?</li>
      <li class="you"><span class="who">You</span>Yes, once. And follow it.</li>
      <li class="cue">progress earcon</li>
      <li><span class="who">Dogwalk</span>Pepper is running the migration now. Juniper wrote a report on the flaky test, if you want it read.</li>
      <li class="cue">you press <kbd>#</kbd>: it stops talking and listens</li>
    </ol>
    <footer>Aliases like Pepper are short spoken names for Managed Sessions.</footer>
  </aside>
</div>

<section aria-labelledby="what">
  <p class="eyebrow">What a call can do</p>
  <h2 id="what">One voice, a whole pack of sessions.</h2>
  <p class="intro">You talk to a single Voice Agent. It is deliberately modest about engineering: the coding is done by stronger ACP Agents such as OpenCode, each working in its own Managed Session inside your sandbox. The Voice Agent keeps track of them and of your attention.</p>
  <ul class="caps">
    <li><h3>Talk first, never wait</h3><p>The call answers at once while your sandbox wakes in the background. The opening tells you what changed since your last call: turns that finished or failed, permissions waiting, recordings saved.</p></li>
    <li><h3>Sessions that outlast the call</h3><p>Each Managed Session gets a pronounceable Alias. Hang up and the agents keep working; call back hours or days later and pick up where they are.</p></li>
    <li><h3>Follow one, let the rest range</h3><p>Starting a Prompt Turn follows that session: short progress beats as it explores, edits, and runs things. The others work quietly until one pulls for your attention.</p></li>
    <li><h3>Decide out loud</h3><p>Permission requests and questions from an agent come to your ear with the agent's own options. Nothing is approved on your behalf.</p></li>
    <li><h3>Hear events, not just words</h3><p>Short earcons mark a call starting, a turn ending, a session pulling. Ask for a different sound style any time: the default set, plain tones, or spoken words. <a href="/sounds">Hear them all</a>.</p></li>
    <li><h3>Leave a voice note</h3><p>Record voicemail-style into the workspace: a spec, a hummed melody, a sound to process. It becomes an ordinary file an agent can work with.</p></li>
    <li><h3>Reports, not logs</h3><p>Ask how something went and get a short spoken result that keeps the stop reason honest, never a wall of tool output. Agents can also play you audio they made.</p></li>
    <li><h3>Redirect mid-stride</h3><p>Cancel a turn, give a session a new assignment, rename it, or close it. Closing keeps its history, so it can be reopened later.</p></li>
  </ul>
</section>

<section id="keypad" aria-labelledby="keypad-h">
  <p class="eyebrow">The keypad</p>
  <h2 id="keypad-h">Four keys you can press without looking.</h2>
  <p class="intro">A few keys have fixed meanings that skip the model entirely, so they work the same every time, even in wind or a noisy street. Other digits go to the Voice Agent as ordinary input.</p>
  <div class="keys">
    <div>
      <ul class="pad" aria-label="Keypad during conversation">
        <li class="on"><span class="d">1</span><span class="l">instant status</span></li>
        <li><span class="d">2</span><span class="l">ABC</span></li>
        <li><span class="d">3</span><span class="l">DEF</span></li>
        <li><span class="d">4</span><span class="l">GHI</span></li>
        <li><span class="d">5</span><span class="l">JKL</span></li>
        <li><span class="d">6</span><span class="l">MNO</span></li>
        <li><span class="d">7</span><span class="l">PQRS</span></li>
        <li><span class="d">8</span><span class="l">TUV</span></li>
        <li><span class="d">9</span><span class="l">WXYZ</span></li>
        <li class="on"><span class="d">&#42;</span><span class="l">system menu</span></li>
        <li class="on"><span class="d">0</span><span class="l">repeat that</span></li>
        <li class="on"><span class="d">#</span><span class="l">stop talking</span></li>
      </ul>
      <p class="padnote">During conversation. Status reads every session from saved state, instantly.</p>
    </div>
    <div>
      <div class="menu">
        <h3>The star menu</h3>
        <p>For when talking is not working. Pre-recorded prompts, no model listening; it returns to the conversation on star or after fifteen quiet seconds. Everything else (status, sound style, pairing a browser, hanging up) you just ask for.</p>
        <ol>
          <li><span class="k">1</span><span>Restart the sandbox (press one again to confirm; running work stops)</span></li>
          <li><span class="k">2</span><span>Recovery options</span></li>
          <li><span class="k">&#42;</span><span>Back to the conversation</span></li>
        </ol>
      </div>
      <div class="rec">
        <h3>While recording</h3>
        <p>Like voicemail: <span class="k">#</span> saves the recording to the workspace, <span class="k">&#42;</span> discards it. A long silence or the length limit also ends it.</p>
      </div>
    </div>
  </div>
</section>

<section id="pair" aria-labelledby="pair-h">
  <div class="pair">
    <div>
      <p class="tag">Optional, eyes-full</p>
      <h2 id="pair-h">Pair a browser when you get back to a desk.</h2>
      <p class="intro">The call is the product. But some things are better read than heard: a long report, or any other file an agent wrote. A Paired Browser is a read-only companion page, introduced by sound.</p>
      <figure aria-hidden="true">
        <svg class="ill" viewBox="0 0 440 150" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="18" y="22" width="62" height="112" rx="12"/>
          <path d="M40 32h18"/>
          <g fill="currentColor" stroke="none" opacity=".75">
            <circle cx="36" cy="62" r="4"/><circle cx="49" cy="62" r="4"/><circle cx="62" cy="62" r="4"/>
            <circle cx="36" cy="76" r="4"/><circle cx="49" cy="76" r="4"/><circle cx="62" cy="76" r="4"/>
            <circle cx="36" cy="90" r="4"/><circle cx="49" cy="90" r="4"/><circle cx="62" cy="90" r="4"/>
            <circle cx="36" cy="104" r="4"/><circle cx="62" cy="104" r="4"/>
          </g>
          <circle class="accent-fill" cx="49" cy="104" r="5" stroke="none"/>
          <g class="accent" stroke-width="3">
            <path d="M108 70v12"/><path d="M118 60v32"/><path d="M128 66v20"/><path d="M138 52v48"/><path d="M148 62v28"/><path d="M158 56v40"/><path d="M168 68v16"/><path d="M178 58v36"/><path d="M188 64v24"/><path d="M198 54v44"/><path d="M208 66v20"/><path d="M218 60v32"/><path d="M228 70v12"/>
          </g>
          <rect x="262" y="34" width="150" height="88" rx="6"/>
          <path d="M246 132h182"/>
          <path d="M278 52h60" opacity=".8"/><path d="M278 66h100" opacity=".45"/><path d="M278 78h86" opacity=".45"/><path d="M278 90h108" opacity=".45"/><path d="M278 102h70" opacity=".45"/>
          <circle cx="392" cy="52" r="5" fill="#5fc28e" stroke="none"/>
        </svg>
      </figure>
      <p class="actions" style="margin-top:18px"><a class="btn" href="/pair">Open dogwalk.tools/pair</a></p>
    </div>
    <div>
      <ol class="steps">
        <li><b>Open <a href="/pair">dogwalk.tools/pair</a> on a laptop or tablet</b> and press Listen. Use a different device from the phone in the call: phones give browsers only silence while a call is active.</li>
        <li><b>In the call, ask to pair a browser.</b> When you say it is listening, the call plays a two-second burst of keypad tones.</li>
        <li><b>The browser hears the tones.</b> The Voice Agent names the browser and its rough location, so you know it is the one in front of you. Press <span class="k">1</span> to accept.</li>
        <li><b>Read along.</b> The page shows your sessions' live status and lets you open files the agents wrote, such as reports. It is read-only: every decision still happens on the call.</li>
      </ol>
      <p class="fine"><b>Pairing is ephemeral.</b> It lives in that browser tab and outlasts the call, so you can read a report, then call back with feedback. It expires after an hour idle or twelve hours total.</p>
    </div>
  </div>
</section>

<section aria-labelledby="how">
  <p class="eyebrow">Under the hood</p>
  <h2 id="how">How a call becomes code.</h2>
  <p class="intro">The Voice Agent is one character played by two parts: a fast conversational frontend that holds the call, and a slower Session Orchestrator that thinks, remembers across calls, and operates the sessions.</p>
  <ol class="flow">
    <li><span class="stage">You speak</span><span class="ctx">Voice Transport</span><p>An ordinary phone call reaches <a href="https://www.twilio.com/voice">Twilio</a> and is handed over secure SIP to OpenAI GPT-Live.</p></li>
    <li><span class="stage">Voice Frontend</span><span class="ctx">Voice Interaction</span><p>Converses, takes turns, handles interruptions. It has no session tools; it hands requests to its other half.</p></li>
    <li><span class="stage">Session Orchestrator</span><span class="ctx">Session Management</span><p>Resolves what you meant, briefs agents, and tracks every Managed Session and Prompt Turn on Cloudflare.</p></li>
    <li><span class="stage">ACP</span><span class="ctx">ACP Integration</span><p>Speaks the <a href="https://agentclientprotocol.com/">Agent Client Protocol</a>: prompts, updates, stop reasons, permissions, elicitations.</p></li>
    <li><span class="stage">ACP Agent</span><span class="ctx">Agent Hosting</span><p><a href="https://opencode.ai/">OpenCode</a> does the engineering in your own <a href="https://www.daytona.io/">Daytona</a> sandbox, which keeps its files between calls.</p></li>
  </ol>
</section>

</main>

<footer class="site">
  <span>An open-source proof of concept, run by one operator.<br>Access is invite-only: a first call registers with an Invite Code.</span>
  <span><a href="https://github.com/rndmcnlly/dogwalk">github.com/rndmcnlly/dogwalk</a><br><a href="/pair">Pair a browser</a></span>
</footer>

</div>
</body></html>`;
