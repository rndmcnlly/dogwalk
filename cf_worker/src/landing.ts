export const LANDING_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Dogwalk is an eyes-free ACP client. Supervise a pack of coding agents over an ordinary phone call, hands and eyes free, while you walk the dog.">
<title>Dogwalk: an eyes-free ACP client</title>
<style>
:root{
  color-scheme:light;
  --paper:#f5f1e8;--ink:#1c2b25;--muted:#5d685f;--faint:#8a9188;
  --line:#ddd5c6;--rule:#c9c0ad;--leash:#bf4d2b;--card:#fbf9f3;--mono:ui-monospace,SFMono-Regular,Menlo,"Cascadia Code",monospace;
}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
a{color:inherit}
.page{max-width:940px;margin:0 auto;padding:0 clamp(22px,5vw,48px) 64px}
header{display:flex;align-items:baseline;justify-content:space-between;gap:16px;padding:26px 0;border-bottom:1px solid var(--rule);flex-wrap:wrap}
.mark{font-weight:700;letter-spacing:-.02em;text-decoration:none;font-size:18px}
.mark b{color:var(--leash)}
.mark .dot{color:var(--faint);font-weight:500}
.masthead-meta{font:500 12px/1.5 var(--mono);color:var(--muted);letter-spacing:.02em;text-align:right}
.masthead-meta a{text-decoration:none;border-bottom:1px solid var(--line)}
.masthead-meta a:hover{border-color:var(--ink)}
.lede{padding:clamp(34px,6vw,60px) 0 clamp(28px,4vw,44px);border-bottom:1px solid var(--rule)}
.kicker{margin:0 0 18px;font:600 12px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--leash)}
h1{margin:0;font:600 clamp(30px,4.6vw,50px)/1.14 Georgia,"Times New Roman",serif;letter-spacing:-.015em;text-wrap:balance;max-width:20ch}
h1 em{color:var(--leash);font-style:italic;font-weight:500}
.scene{max-width:60ch;margin:24px 0 0;font-size:clamp(17px,1.7vw,20px);color:#33413a}
.scene b{font-weight:650;color:var(--ink)}
.def{max-width:66ch;margin:20px 0 0;font-size:15px;color:var(--muted)}
section{padding-top:clamp(30px,4vw,44px)}
h2{margin:0 0 20px;font:600 12px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.pipe{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:0}
.pipe li{display:grid;grid-template-columns:clamp(140px,22vw,190px) 1fr;gap:clamp(14px,3vw,34px);padding:16px 0;border-top:1px solid var(--line);align-items:baseline}
.pipe li:first-child{border-top:0}
.pipe .stage{font:600 14px/1.3 var(--mono);color:var(--ink)}
.pipe .stage span{display:block;font:500 11px/1.4 var(--mono);color:var(--faint);letter-spacing:.04em;margin-top:3px}
.pipe .what{font-size:15px;color:#3a463f;max-width:56ch}
.pipe .what a{text-decoration:underline;text-decoration-color:var(--leash);text-underline-offset:3px;text-decoration-thickness:1.5px}
.caps{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:0 clamp(24px,4vw,44px)}
.caps div{padding:15px 0;border-top:1px solid var(--line)}
.caps b{display:block;font-size:15px;color:var(--ink);margin-bottom:2px}
.caps p{margin:0;font-size:13.5px;color:var(--muted);line-height:1.5}
footer{margin-top:clamp(34px,5vw,52px);padding-top:22px;border-top:1px solid var(--rule);display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;font:500 12.5px/1.6 var(--mono);color:var(--muted)}
footer a{text-decoration:none;border-bottom:1px solid var(--line)}
footer a:hover{border-color:var(--ink)}
.status{color:var(--leash)}
@media(max-width:620px){
  .masthead-meta{text-align:left;width:100%}
  .pipe li{grid-template-columns:1fr;gap:6px}
  .pipe .stage{color:var(--leash)}
}
</style>
</head>
<body>
<div class="page">

<header>
  <a class="mark" href="/"><b>dogwalk</b><span class="dot">.tools</span></a>
  <div class="masthead-meta">eyes-free ACP client<br><a href="https://github.com/rndmcnlly/dogwalk">github.com/rndmcnlly/dogwalk</a></div>
</header>

<div class="lede">
  <p class="kicker">Hands free. Eyes free.</p>
  <h1>Take your coding agents <em>for a walk.</em></h1>
  <p class="scene">Phone in your pocket, Bluetooth in your ears, dog on the leash. You <b>start work, check progress, answer permission requests, and redirect a pack of agents</b> by talking, and never once look at a screen.</p>
  <p class="def">Dogwalk is an eyes-free ACP client and multi-session coding manager. A deliberately engineering-weak Voice Agent turns what you say into precise session-manager operations. The coding is done by stronger ACP Agents such as OpenCode: the Voice Agent coordinates them and your attention.</p>
</div>

<section>
  <h2>How a call becomes code</h2>
  <ul class="pipe">
    <li>
      <div class="stage">You speak<span>Voice Transport</span></div>
      <div class="what">A phone call reaches <a href="https://www.twilio.com/voice">Twilio</a> and becomes a live conversation through <a href="https://platform.openai.com/docs/guides/realtime">OpenAI Realtime</a>.</div>
    </li>
    <li>
      <div class="stage">Voice Agent<span>Voice Interaction</span></div>
      <div class="what">Resolves what you mean, refers to each session by a short spoken Alias, and relays progress in plain language. It does not write code.</div>
    </li>
    <li>
      <div class="stage">Session Manager<span>Session Management</span></div>
      <div class="what">Keeps a pack of Managed Sessions, tracks each Prompt Turn, and routes anything needing your attention back to your ear.</div>
    </li>
    <li>
      <div class="stage">ACP Integration<span>Agent Client Protocol</span></div>
      <div class="what">Speaks <a href="https://agentclientprotocol.com/">ACP</a> exactly: prompts, updates, stop reasons, permissions, and elicitations.</div>
    </li>
    <li>
      <div class="stage">ACP Agent<span>Agent Hosting</span></div>
      <div class="what"><a href="https://opencode.ai/">OpenCode</a> does the engineering inside an isolated <a href="https://www.daytona.io/">Daytona</a> sandbox, one per caller.</div>
    </li>
  </ul>
</section>

<section>
  <h2>What you can do by voice</h2>
  <div class="caps">
    <div><b>Run a pack</b><p>Start and supervise several coding sessions at once, each with its own pronounceable Alias.</p></div>
    <div><b>Answer, don't watch</b><p>When an agent needs permission or asks a question, it comes to your ear and you decide out loud.</p></div>
    <div><b>Hear the work</b><p>Ask for progress or a result and get a short spoken Report, never a wall of tool output.</p></div>
    <div><b>Redirect mid-walk</b><p>Cancel a turn, hand over a new assignment, or close a session without breaking stride.</p></div>
  </div>
</section>

<footer>
  <span>An open-source proof of concept.<br><span class="status">Rough edges included.</span></span>
  <span>Twilio &middot; OpenAI Realtime &middot; ACP &middot; OpenCode &middot; Daytona<br><a href="https://github.com/rndmcnlly/dogwalk">Read the source &rarr;</a></span>
</footer>

</div>
</body></html>`;
