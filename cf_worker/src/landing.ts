export const LANDING_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Dogwalk is an eyes-free ACP client for supervising coding agents through an ordinary phone call.">
<title>Dogwalk: take your coding agents for a walk</title>
<style>
:root{color-scheme:light;--paper:#f2ead8;--paper-deep:#e5d8bd;--ink:#18332c;--muted:#59665d;--leash:#e45d32;--acid:#d9ee83;--white:#fffdf7}
*{box-sizing:border-box}html{background:var(--ink)}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;overflow-x:hidden}
body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.36;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 180 180' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.8' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.11'/%3E%3C/svg%3E")}
a{color:inherit}.shell{position:relative;min-height:100svh;max-width:1440px;margin:auto;padding:26px clamp(22px,5vw,76px) 42px;display:flex;flex-direction:column}
nav{position:relative;z-index:3;display:flex;align-items:center;justify-content:space-between}.mark{font:900 21px/1 Georgia,serif;letter-spacing:-.035em;text-decoration:none}.source{display:inline-flex;align-items:center;gap:8px;border-bottom:1px solid currentColor;padding:5px 0;text-decoration:none;font-size:13px;font-weight:750}.source:after{content:"↗";font-size:15px}
.hero{position:relative;z-index:1;flex:1;display:grid;grid-template-columns:minmax(0,1.04fr) minmax(400px,.96fr);align-items:center;gap:20px;min-width:0;padding:7vh 0 5vh}.copy{position:relative;z-index:2;width:100%;max-width:730px;min-width:0}.eyebrow{display:inline-block;margin:0 0 24px;padding:7px 11px;background:var(--acid);font:750 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.12em;text-transform:uppercase;transform:rotate(-1deg)}
h1{max-width:760px;margin:0;font:900 clamp(58px,8.6vw,132px)/.8 Georgia,"Times New Roman",serif;letter-spacing:-.075em;text-wrap:balance}.walk{display:block;color:var(--leash);font-style:italic;font-weight:500}.dek{max-width:620px;margin:34px 0 0;font-size:clamp(18px,2vw,25px);line-height:1.42;letter-spacing:-.02em}.dek strong{font-weight:800}
.actions{display:flex;align-items:center;gap:18px;margin-top:32px;flex-wrap:wrap}.button{display:inline-flex;align-items:center;gap:10px;padding:13px 17px;background:var(--ink);color:var(--white);text-decoration:none;font-weight:800;box-shadow:5px 5px 0 var(--leash);transition:transform .16s,box-shadow .16s}.button:hover{transform:translate(2px,2px);box-shadow:3px 3px 0 var(--leash)}.note{color:var(--muted);font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.06em}
.trail{position:relative;min-height:610px;align-self:stretch}.trail svg{position:absolute;width:min(68vw,850px);height:auto;right:clamp(-250px,-13vw,-100px);top:50%;transform:translateY(-50%);overflow:visible}.leash{fill:none;stroke:var(--leash);stroke-width:10;stroke-linecap:round;stroke-dasharray:18 15;animation:lead 22s linear infinite}.leash-shadow{fill:none;stroke:var(--ink);stroke-width:16;stroke-linecap:round;opacity:.12}.paw{fill:var(--ink)}.boot{fill:var(--ink);opacity:.78}.print{animation:step 5s ease-in-out infinite}.p2{animation-delay:.5s}.p3{animation-delay:1s}.p4{animation-delay:1.5s}.p5{animation-delay:2s}.p6{animation-delay:2.5s}.p7{animation-delay:3s}.p8{animation-delay:3.5s}
.how{position:relative;z-index:2;border-top:1px solid rgba(24,51,44,.28);padding-top:24px;display:grid;grid-template-columns:.8fr 2.2fr;gap:28px}.how h2{margin:0;font:800 12px/1 ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;letter-spacing:.12em}.how p{max-width:900px;margin:0;color:var(--muted);font-size:14px}.how a{text-decoration-color:var(--leash);text-decoration-thickness:2px;text-underline-offset:3px}.how strong{color:var(--ink)}
@keyframes lead{to{stroke-dashoffset:-330}}@keyframes step{0%,100%{opacity:.42}45%,60%{opacity:1}}
@media(max-width:900px){.shell{padding-top:20px}.hero{grid-template-columns:minmax(0,1fr);padding-top:11vh}.copy{max-width:760px}.trail{position:absolute;inset:70px -180px auto 15%;height:620px;opacity:.14;z-index:-1}.trail svg{width:820px;right:0;top:45%;transform:translateY(-50%) rotate(-8deg)}.how{grid-template-columns:1fr;padding-top:19px}.how p{max-width:680px}}
@media(max-width:560px){h1{font-size:clamp(53px,18vw,82px)}.eyebrow{margin-bottom:18px}.hero{padding-top:9vh}.dek{margin-top:27px}.source span{display:none}.actions{align-items:flex-start;flex-direction:column}.trail{left:-5%;right:-290px;top:40px}.how{margin-top:46px}.how p{font-size:13px}}
@media(prefers-reduced-motion:reduce){.leash,.print{animation:none}.button{transition:none}}
</style>
</head>
<body><main class="shell">
<nav><a class="mark" href="/">dogwalk.tools</a><a class="source" href="https://github.com/rndmcnlly/dogwalk"><span>View source</span> GitHub</a></nav>
<section class="hero">
  <div class="copy">
    <p class="eyebrow">An eyes-free ACP client</p>
    <h1>Take your coding agents <span class="walk">for a walk.</span></h1>
    <p class="dek">Supervise multiple coding sessions through an ordinary phone call: start work, check progress, answer permission requests, and redirect agents <strong>without looking at a screen.</strong></p>
    <div class="actions"><a class="button" href="https://github.com/rndmcnlly/dogwalk">Explore the project <span aria-hidden="true">↗</span></a><span class="note">Open-source proof of concept<br>rough edges included</span></div>
  </div>
  <div class="trail" aria-hidden="true">
    <svg viewBox="0 0 900 760" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <g id="paw"><ellipse cx="0" cy="11" rx="22" ry="18" transform="rotate(-8)"/><ellipse cx="-24" cy="-10" rx="8" ry="11" transform="rotate(-26 -24 -10)"/><ellipse cx="-8" cy="-21" rx="8" ry="12" transform="rotate(-8 -8 -21)"/><ellipse cx="11" cy="-20" rx="8" ry="12" transform="rotate(10 11 -20)"/><ellipse cx="28" cy="-7" rx="8" ry="11" transform="rotate(28 28 -7)"/></g>
        <g id="boot"><path d="M-19-43C-4-48 11-40 17-26L20-4C23 8 31 20 34 33C36 44 27 51 14 52L-7 50C-22 48-27 38-23 26L-17 10C-13-1-17-18-24-29C-29-37-26-41-19-43Z"/><path d="M-19 20L23 15M-22 34L30 29M-16-6L18-10" fill="none" stroke="var(--paper)" stroke-width="5" opacity=".65"/></g>
      </defs>
      <path class="leash-shadow" d="M70 690C205 598 69 485 245 420C430 350 305 192 492 159C660 129 663 294 826 75"/>
      <path class="leash" d="M70 690C205 598 69 485 245 420C430 350 305 192 492 159C660 129 663 294 826 75"/>
      <g class="print p1 boot" transform="translate(116 630) rotate(-28) scale(.82)"><use href="#boot"/></g>
      <g class="print p2 paw" transform="translate(210 568) rotate(20) scale(.72)"><use href="#paw"/></g>
      <g class="print p3 boot" transform="translate(181 466) rotate(17) scale(.82)"><use href="#boot"/></g>
      <g class="print p4 paw" transform="translate(300 394) rotate(-18) scale(.72)"><use href="#paw"/></g>
      <g class="print p5 boot" transform="translate(381 295) rotate(-21) scale(.82)"><use href="#boot"/></g>
      <g class="print p6 paw" transform="translate(489 226) rotate(25) scale(.72)"><use href="#paw"/></g>
      <g class="print p7 boot" transform="translate(622 227) rotate(30) scale(.82)"><use href="#boot"/></g>
      <g class="print p8 paw" transform="translate(760 135) rotate(-20) scale(.72)"><use href="#paw"/></g>
    </svg>
  </div>
</section>
<section class="how"><h2>Under the hood</h2><p>Calls arrive through <a href="https://www.twilio.com/voice">Twilio</a> and become a live conversation through <a href="https://platform.openai.com/docs/guides/realtime">OpenAI Realtime</a>. A lightweight Voice Agent translates speech into neutral <a href="https://agentclientprotocol.com/">ACP</a> operations that direct <a href="https://opencode.ai/">OpenCode</a> sessions inside isolated <a href="https://www.daytona.io/">Daytona</a> sandboxes. <strong>The Voice Agent does not code; it coordinates stronger coding agents and human attention.</strong></p></section>
</main></body></html>`;
