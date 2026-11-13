// The public landing page. Its hero is an animated scene that shows the Park conceit and the
// deployed path at once: the User in a far-off phone booth (Twilio's phone network), the walker
// speaking on a phone (GPT-Live), the walker's head (the Session Orchestrator on Cloudflare),
// leashes (Agent Connections), and dogs (Managed Sessions of OpenCode) loose in the park (the Daytona Sandbox).
// The script loops one grounded call and the quiet between calls: tool chips are Agent tool
// kinds, the cloud shows real Orchestrator tool names, the gate offers OpenCode's own permission
// options, a request that arrives between calls waits for the next Opening, following ends with
// the call, and a closed session goes home with its history (DOMAIN.md).

// Scene coordinates the script relies on: the route runs booth -> wire -> tower -> phone, the
// walker stands at (340, 480), and dogs are drawn facing right with their feet at y = 0.
const WALK_SVG = String.raw`
<svg id="walk" viewBox="0 54 960 452" role="group" aria-label="A dog walker takes a phone call from someone in a farm phone booth far away, while three dogs work in a park on long leashes. Tab through its parts; Enter pins one.">
<g id="sunny" data-tip="sun"><circle class="halo" cx="862" cy="96" r="60"/>
<g transform="translate(862 96)"><g id="rays" class="rays"><path d="M0 -46 V-58" transform="rotate(0)"/><path d="M0 -46 V-58" transform="rotate(30)"/><path d="M0 -46 V-58" transform="rotate(60)"/><path d="M0 -46 V-58" transform="rotate(90)"/><path d="M0 -46 V-58" transform="rotate(120)"/><path d="M0 -46 V-58" transform="rotate(150)"/><path d="M0 -46 V-58" transform="rotate(180)"/><path d="M0 -46 V-58" transform="rotate(210)"/><path d="M0 -46 V-58" transform="rotate(240)"/><path d="M0 -46 V-58" transform="rotate(270)"/><path d="M0 -46 V-58" transform="rotate(300)"/><path d="M0 -46 V-58" transform="rotate(330)"/></g></g>
<circle class="sun" cx="862" cy="96" r="36"/></g>
<g class="town" data-tip="web">
  <rect x="872" y="214" width="16" height="34"/><rect x="890" y="200" width="20" height="48"/><rect x="912" y="222" width="14" height="26"/>
  <rect x="928" y="206" width="18" height="42"/><path d="M890 200 L900 190 L910 200 Z"/>
  <g class="win"><rect x="894" y="206" width="4" height="4"/><rect x="902" y="206" width="4" height="4"/><rect x="894" y="216" width="4" height="4"/><rect x="932" y="212" width="4" height="4"/><rect x="938" y="222" width="4" height="4"/><rect x="876" y="220" width="4" height="4"/></g>
</g>
<path class="hill" d="M0 262 C120 236 200 240 300 258 C420 280 520 238 640 246 C760 254 860 230 960 244 L960 350 L0 350 Z"/>
<g class="farm" data-tip="farm">
  <path class="farmrow" d="M0 262 L96 254 M0 270 L100 262 M0 278 L104 270"/>
  <g transform="translate(62 252) scale(1.35) translate(-62 -252)">
  <rect x="36" y="228" width="34" height="24" fill="#b23a2a"/>
  <path d="M33 229 L53 213 L73 229 Z" fill="#7d261b"/>
  <rect x="48" y="238" width="10" height="14" fill="#f4e8d4"/>
  <path d="M48 238 L58 252 M58 238 L48 252" stroke="#b23a2a" stroke-width="1.4"/>
  <rect x="76" y="215" width="12" height="37" fill="#cfc6b3"/>
  <path d="M76 216 A6 6 0 0 1 88 216 Z" fill="#8a9188"/>
  </g>
  <g id="booth" data-tip="you" transform="translate(113 252) scale(1.3) translate(-113 -252)">
    <rect x="104.5" y="218.5" width="17" height="4" rx="1.5" fill="#8f2a1f"/>
    <rect x="106" y="222" width="14" height="30" rx="1.5" fill="#c0392b"/>
    <rect id="booth-glass" x="108.5" y="226.5" width="9" height="19" rx="1"/>
    <g id="you-in"><circle cx="113" cy="232" r="2.4" fill="#1b2a24"/>
    <path d="M110.2 245.5 Q110.2 235.4 113 235.4 Q115.8 235.4 115.8 245.5 Z" fill="#1b2a24"/></g>
  </g>
  <g id="you-walk" data-tip="you" fill="#1b2a24"><circle cx="0" cy="-16" r="3.2"/><path d="M-3.8 -1 Q-3.8 -12.4 0 -12.4 Q3.8 -12.4 3.8 -1 Z"/></g>
</g>
<g data-tip="twilio">
  <path class="pad" d="M104 214 L232 226 L276 186 L276 300 L232 308 L104 262 Z"/>
  <path class="pole" d="M150 228 V250 M145 231 H155 M186 236 V270 M180 240 H192 M222 254 V302 M215 258 H229"/>
  <path class="wire" d="M114 209 Q132 226 150 231 Q168 241 186 240 Q204 256 222 258 Q242 276 259 292"/>
  <g class="tower" transform="translate(262 305) scale(.62) translate(-262 -372)">
    <ellipse class="tower-shadow" cx="262" cy="373" rx="30" ry="4"/>
  <line x1="246" y1="372" x2="259" y2="202"/><line x1="278" y1="372" x2="265" y2="202"/>
  <polyline points="248,352 274,306 252,262 270,222 261,204"/>
  <rect class="tower-fill" x="251" y="196" width="5" height="16" rx="1"/>
  <rect class="tower-fill" x="268" y="196" width="5" height="16" rx="1"/>
  <line x1="262" y1="200" x2="262" y2="182"/>
  <circle class="beacon" cx="262" cy="181" r="2.8"/>
  <rect class="tower-fill" x="244" y="352" width="32" height="20" rx="2"/>
  </g>
</g>
<path class="ground" data-tip="park" d="M0 342 C200 330 400 340 560 334 C720 328 860 338 960 332 L960 540 L0 540 Z"/>
<path class="bank" d="M0 343 C200 331 400 341 560 335 C720 329 860 339 960 333"/>
<path class="stream" data-tip="stream" d="M0 340 C200 328 400 338 560 332 C720 326 860 336 960 330"/>
<g id="trees" aria-hidden="true"></g>
<path id="air" class="air" d="M263 190 Q332 188 372 278"/>
<path id="route" fill="none" stroke="none" d="M114 209 Q132 226 150 231 Q168 241 186 240 Q204 256 222 258 Q242 276 259 292 L262 190 Q332 188 375 284"/>
<g id="mailbox" transform="translate(84 -60)" data-tip="mailbox">
  <rect class="pad" x="750" y="278" width="40" height="46"/>
  <rect x="768.5" y="300" width="3" height="20" fill="#7a5a3a"/>
  <path d="M758 302 V292 Q758 286 764 286 H776 Q782 286 782 292 V302 Z" fill="#3d5a80"/>
  <rect id="flag" x="781" y="290" width="2.5" height="11" fill="#c0392b"/>
</g>
<path class="concrete" data-tip="path" d="M0 452 C300 446 600 451 960 442 L960 540 L0 540 Z"/>
<path class="curb" d="M0 452 C300 446 600 451 960 442"/>
<g id="tufts" aria-hidden="true"></g>
<ellipse id="spot" class="spot" cx="652" cy="432" rx="92" ry="11"/>
<g class="patch" data-tip="patch" transform="translate(652 432)"><ellipse rx="72" ry="8"/><path class="sprout" d="M-50 -2 l-3 -7 M-50 -2 l3 -7 M-14 -3 l-3 -7 M-14 -3 l3 -7 M30 -2 l-3 -7 M30 -2 l3 -7 M56 -1 l-3 -6 M56 -1 l3 -6"/></g>
<g class="patch" id="patch-j" data-tip="patch" transform="translate(836 394)"><ellipse rx="50" ry="6.5"/><path class="sprout" d="M-32 -2 l-3 -7 M-32 -2 l3 -7 M0 -3 l-3 -7 M0 -3 l3 -7 M30 -2 l-3 -7 M30 -2 l3 -7"/></g>
<g id="frisbee" data-tip="artifact" transform="translate(748 424)">
  <ellipse class="pad" rx="22" ry="14"/>
  <g id="frisbee-disc"><ellipse rx="14" ry="5.5" fill="#c0392b"/><ellipse rx="8" ry="3" fill="none" stroke="#f4e8d4" stroke-width="1.6"/></g>
  <ellipse id="frisbee-glow" class="fglow" rx="20" ry="8" opacity="0"/>
</g>
<g id="basket" data-tip="notes" transform="translate(546 446)">
  <rect class="pad" x="-20" y="-26" width="40" height="30"/>
  <g id="pouch-in"><path d="M-7 -10 Q-9 -20 -2 -21 L2 -21 Q9 -20 7 -10 Z" fill="#efe3c6"/><path d="M-3 -21 L-5 -26 M3 -21 L5 -26" stroke="#9a3818" stroke-width="1.8" stroke-linecap="round"/></g>
  <path d="M-15 -12 L15 -12 L11 0 L-11 0 Z" fill="#b98a55"/>
  <path d="M-13 -7 H13 M-12 -3 H12" stroke="#8a6238" stroke-width="1.2"/>
  <ellipse id="basket-glow" class="fglow" cx="0" cy="-8" rx="22" ry="11" opacity="0"/>
</g>
<g id="kennel" data-tip="home">
  <g id="wheels" fill="#3a2a22"><circle cx="-18" cy="-1" r="5"/><circle cx="18" cy="-1" r="5"/></g>
  <rect x="-28" y="-36" width="56" height="36" rx="3" fill="#c96b3c"/>
  <path d="M-35 -33 L0 -62 L35 -33 Z" fill="#7d3a22"/>
  <path d="M-12 0 V-15 A12 12 0 0 1 12 -15 V0 Z" fill="#3a2a22"/>
  <g id="kennel-biscuit">
    <path d="M-9 -12 L-10 -22 L-3 -15 Z" fill="#c97530"/><path d="M3 -15 L9 -23 L9 -12 Z" fill="#e2893b"/>
    <circle cx="0" cy="-8" r="8" fill="#e2893b"/><ellipse cx="5" cy="-5" rx="4.5" ry="3.4" fill="#fbf1e0"/>
    <path d="M-4 -9 Q-2 -7 0 -9" stroke="#1b2a24" stroke-width="1.3" fill="none"/>
  </g>
  <text id="zzz" class="zzz" x="16" y="-44">z</text>
</g>
<g class="shadow" aria-hidden="true">
  <ellipse id="sh-walker" rx="38" ry="6"/><ellipse id="sh-biscuit" rx="36" ry="5"/>
  <ellipse id="sh-pepper" rx="54" ry="5"/><ellipse id="sh-juniper" rx="27" ry="3.6"/>
</g>

<g id="logs" aria-hidden="true"></g>
<g id="juniper" data-tip="juniper">
  <g id="juniper-body">
    <rect data-leg="juniper" data-ph="0" x="-34" y="-30" width="13" height="30" rx="6" fill="#e3d8c0"/>
    <rect data-leg="juniper" data-ph="3.1" x="-18" y="-30" width="13" height="30" rx="6" fill="#d2c6aa"/>
    <rect data-leg="juniper" data-ph="3.1" x="12" y="-30" width="13" height="30" rx="6" fill="#e3d8c0"/>
    <rect data-leg="juniper" data-ph="0" x="26" y="-30" width="13" height="30" rx="6" fill="#d2c6aa"/>
    <path d="M-46 -58 Q-64 -70 -60 -90 Q-54 -78 -50 -84 Q-50 -72 -42 -66 Z" fill="#b5aca0"/>
    <g fill="#b5aca0"><circle cx="-24" cy="-46" r="27.5"/><circle cx="2" cy="-55" r="30.5"/><circle cx="24" cy="-45" r="25.5"/></g>
    <g fill="#f7f1e2"><circle cx="-24" cy="-46" r="25"/><circle cx="2" cy="-55" r="28"/><circle cx="24" cy="-45" r="23"/></g>
    <ellipse cx="-6" cy="-66" rx="16" ry="11" fill="#a9b0b2"/>
    <g id="juniper-head">
      <path d="M30 -58 Q30 -94 52 -92 Q70 -90 72 -76 L86 -72 Q94 -70 92 -60 Q90 -52 78 -52 L50 -50 Q32 -48 30 -58 Z" fill="#b5aca0"/>
      <path d="M32.5 -58 Q33 -91 52 -89.5 Q68 -88 69.5 -74 L85 -70 Q91 -68 89.5 -61 Q88 -54.5 78 -54.5 L50 -52.5 Q35 -51 32.5 -58 Z" fill="#f7f1e2"/>
      <ellipse cx="54" cy="-76" rx="9.5" ry="8.5" fill="#7d868a"/>
      <circle cx="56" cy="-76" r="2.8" fill="#1b2a24"/>
      <circle cx="90" cy="-64" r="4.6" fill="#1b2a24"/>
      <path d="M36 -86 Q46 -98 60 -88 Q50 -90 44 -82 Z" fill="#e8dfcb"/>
      <ellipse cx="38" cy="-66" rx="7" ry="15" fill="#c4bba9" transform="rotate(18 38 -66)"/>
      <path d="M74 -54 Q78 -46 82 -54" fill="#c2574a"/>
      <path d="M29 -56 Q35 -64 33 -76" stroke="#2f5d4a" stroke-width="6" fill="none" stroke-linecap="round"/>
    </g>
  </g>
</g>
<g id="gate" opacity="0" data-tip="gate">
  <rect x="-3" y="-38" width="6" height="38" rx="2" fill="#7a5a3a"/>
  <rect x="47" y="-38" width="6" height="38" rx="2" fill="#7a5a3a"/>
  <g id="gate-panel">
    <rect x="3" y="-33" width="44" height="5" rx="2" fill="#b98a55"/>
    <rect x="3" y="-16" width="44" height="5" rx="2" fill="#b98a55"/>
    <rect x="9" y="-35" width="5" height="26" rx="2" fill="#b98a55"/>
    <rect x="22" y="-35" width="5" height="26" rx="2" fill="#b98a55"/>
    <rect x="35" y="-35" width="5" height="26" rx="2" fill="#b98a55"/>
  </g>
</g>

<g id="pepper" data-tip="pepper">
  <rect data-leg="pepper" data-ph="0" x="-40" y="-16" width="9" height="16" rx="4" fill="#6e3720"/>
  <rect data-leg="pepper" data-ph="3.1" x="-28" y="-16" width="9" height="16" rx="4" fill="#8a4a2b"/>
  <rect data-leg="pepper" data-ph="3.1" x="28" y="-16" width="9" height="16" rx="4" fill="#6e3720"/>
  <rect data-leg="pepper" data-ph="0" x="40" y="-16" width="9" height="16" rx="4" fill="#8a4a2b"/>
  <path id="pepper-tail" d="M-46 -30 Q-62 -42 -66 -56" stroke="#8a4a2b" stroke-width="6" stroke-linecap="round" fill="none"/>
  <rect x="-50" y="-40" width="102" height="28" rx="14" fill="#8a4a2b"/>
  <g id="pepper-head">
    <ellipse cx="58" cy="-34" rx="17" ry="12" fill="#8a4a2b"/>
    <ellipse cx="74" cy="-28" rx="12" ry="7.5" fill="#8a4a2b"/>
    <circle cx="85" cy="-29" r="3.4" fill="#1b2a24"/>
    <circle cx="61" cy="-38" r="2.2" fill="#1b2a24"/>
    <ellipse cx="50" cy="-28" rx="6.5" ry="13" fill="#5e2f19" transform="rotate(14 50 -28)"/>
    <g id="bone" opacity="0" transform="rotate(-10 84 -20) translate(-6 -2)">
      <rect x="78" y="-22" width="26" height="6" rx="3" fill="#f6efdc"/>
      <circle cx="78" cy="-23" r="4" fill="#f6efdc"/><circle cx="78" cy="-15" r="4" fill="#f6efdc"/>
      <circle cx="104" cy="-23" r="4" fill="#f6efdc"/><circle cx="104" cy="-15" r="4" fill="#f6efdc"/>
    </g>
    <path d="M42 -28 L46 -42" stroke="#b2441f" stroke-width="5"/>
  </g>
</g>

<g id="biscuit" data-tip="biscuit">
  <rect data-leg="biscuit" data-ph="0" x="-24" y="-17" width="9" height="17" rx="4" fill="#c97530"/>
  <rect data-leg="biscuit" data-ph="3.1" x="-12" y="-17" width="9" height="17" rx="4" fill="#e2893b"/>
  <rect data-leg="biscuit" data-ph="3.1" x="14" y="-17" width="9" height="17" rx="4" fill="#c97530"/>
  <rect data-leg="biscuit" data-ph="0" x="26" y="-17" width="9" height="17" rx="4" fill="#e2893b"/>
  <circle cx="-32" cy="-33" r="5.5" fill="#e2893b"/>
  <ellipse cx="2" cy="-28" rx="34" ry="16" fill="#e2893b"/>
  <ellipse cx="6" cy="-19" rx="24" ry="7" fill="#fbf1e0"/>
  <g id="biscuit-head">
    <path d="M24 -54 L21 -74 L34 -58 Z" fill="#c97530"/><path d="M34 -57 L40 -76 L44 -55 Z" fill="#e2893b"/>
    <circle cx="33" cy="-46" r="14" fill="#e2893b"/>
    <ellipse cx="44" cy="-41" rx="8.5" ry="6.5" fill="#fbf1e0"/>
    <circle cx="51" cy="-43" r="2.8" fill="#1b2a24"/>
    <circle cx="36" cy="-49" r="2.1" fill="#1b2a24"/>
    <path d="M22 -38 L27 -54" stroke="#2f5d4a" stroke-width="5"/>
  </g>
</g>

<path id="l-juniper" class="leash far"/>
<path id="l-pepper" class="leash far"/>
<path id="l-biscuit" class="leash"/>
<path id="l-juniper-hit" class="hit" data-tip="leash"/><path id="l-pepper-hit" class="hit" data-tip="leash"/><path id="l-biscuit-hit" class="hit" data-tip="leash"/>
<g id="walker" data-tip="walker">
  <g data-leg="walker" data-ph="3.1"><rect x="-8" y="-100" width="16" height="54" rx="8" fill="#1d3344"/><g data-knee="3.1"><rect x="-7.5" y="-56" width="15" height="52" rx="7.5" fill="#1d3344"/><ellipse cx="7" cy="-5" rx="14" ry="6.5" fill="#1b2a24"/></g></g>
  <g id="walker-arm"><path d="M-8 -170 Q-14 -146 -10 -122" stroke="#a84b2a" stroke-width="12" stroke-linecap="round" fill="none"/><circle cx="-10" cy="-119" r="5.5" fill="#7a4d32"/></g>
  <g data-leg="walker" data-ph="0"><rect x="-8" y="-100" width="16" height="54" rx="8" fill="#27485f"/><g data-knee="0"><rect x="-7.5" y="-56" width="15" height="52" rx="7.5" fill="#27485f"/><ellipse cx="7" cy="-5" rx="14" ry="6.5" fill="#1b2a24"/></g></g>
  <path d="M-21 -96 L-25 -164 Q-24 -182 -8 -188 L8 -188 Q24 -182 23 -164 L21 -96 Z" fill="#d0643b"/>
  <rect x="-5" y="-197" width="10" height="13" fill="#8d5a3b"/>
  <g id="walker-head">
    <circle cx="2" cy="-209" r="17" fill="#8d5a3b"/>
    <circle cx="18.5" cy="-207" r="3.4" fill="#8d5a3b"/>
    <path d="M-15 -209 Q-15 -229 4 -228 Q19 -227 20 -214 Q9 -221 -1 -215 Q-6 -206 -15 -203 Z" fill="#1b2a24"/>
    <circle cx="-1" cy="-207" r="3.8" fill="#7a4d32"/>
  </g>
  <g id="walker-leash">
    <path d="M12 -170 Q42 -150 64 -138" stroke="#e2733f" stroke-width="12" stroke-linecap="round" fill="none"/>
    <circle cx="66" cy="-136" r="6" fill="#8d5a3b"/>
  </g>
  <g id="arm-up" data-tip="voice">
    <circle class="pad" cx="14" cy="-112" r="16"/>
    <path d="M14 -176 L14 -146" stroke="#e2733f" stroke-width="12" stroke-linecap="round"/>
    <g id="arm-fore">
      <path d="M14 -146 L14 -113" stroke="#e2733f" stroke-width="11" stroke-linecap="round"/>
      <rect id="phone" x="9" y="-119" width="10" height="18" rx="2.5" fill="#1b2a24"/>
      <rect id="phone-screen" x="10.8" y="-117" width="6.4" height="13" rx="1" fill="#9fd6b8" opacity="0"/>
      <circle cx="14" cy="-112" r="5.5" fill="#8d5a3b"/>
    </g>
  </g>
  <path id="voice" d="M44 -210 Q48 -204 44 -198 M49 -214 Q55 -204 49 -194" stroke="#2f5d4a" stroke-width="2" stroke-linecap="round" fill="none" opacity="0"/>
</g>

<g id="cloud" data-tip="head">
  <g class="cloud-edge"><path d="M322 172 C294 173 286 146 306 135 C297 111 326 96 349 106 C357 80 397 72 417 91 C434 72 478 77 483 101 C508 96 532 118 522 138 C544 148 536 173 512 172 Z"/><circle cx="392" cy="196" r="8"/><circle cx="378" cy="218" r="5.5"/><circle cx="366" cy="236" r="3.5"/></g>
  <g class="cloud-fill"><path d="M322 172 C294 173 286 146 306 135 C297 111 326 96 349 106 C357 80 397 72 417 91 C434 72 478 77 483 101 C508 96 532 118 522 138 C544 148 536 173 512 172 Z"/><circle cx="392" cy="196" r="8"/><circle cx="378" cy="218" r="5.5"/><circle cx="366" cy="236" r="3.5"/></g>
  <text id="c1" class="think" x="420" y="139" text-anchor="middle"></text>
  <text id="c2" class="think2" x="420" y="159" text-anchor="middle"></text>
</g>

<g id="card" opacity="0" data-tip="gate">
  <rect class="card-bg" x="712" y="250" width="236" height="68" rx="12"/>
  <path class="card-bg" d="M860 317 L870 333 L882 317"/>
  <text class="card-t" id="card-t" x="726" y="273">Run migrate.py?</text>
  <g class="opt" id="opt-once"><rect x="724" y="284" width="76" height="22" rx="11"/><text x="762" y="299" text-anchor="middle">Allow once</text></g>
  <g class="opt"><rect x="805" y="284" width="84" height="22" rx="11"/><text x="847" y="299" text-anchor="middle">Always allow</text></g>
  <g class="opt"><rect x="894" y="284" width="48" height="22" rx="11"/><text x="918" y="299" text-anchor="middle">Reject</text></g>
</g>

<g class="labels" aria-hidden="true">
  <text class="lbl you-lbl" x="113" y="272" text-anchor="middle">you</text>
  <text class="lbl" x="420" y="66" text-anchor="middle"><tspan class="b">Cloudflare</tspan><tspan class="r"> · the walker's head</tspan></text>
  <text class="lbl" x="246" y="322" text-anchor="end"><tspan class="b">Twilio</tspan><tspan class="r"> · phone network</tspan></text>
  <path class="leader" d="M398 302 L384 290"/>
  <text class="lbl" x="398" y="310"><tspan class="b">OpenAI GPT-Live</tspan><tspan class="r"> · the walker's voice</tspan></text>
  <text class="lbl" x="512" y="388" text-anchor="middle"><tspan class="b">Agent API</tspan><tspan class="r"> · leashes</tspan></text>
  <text class="lbl" x="16" y="428"><tspan class="b">Daytona</tspan><tspan class="r"> · the park, your sandbox</tspan></text>
  <text class="lbl" x="16" y="496"><tspan class="b">the path</tspan><tspan class="r"> · outside the sandbox</tspan></text>
  <text class="lbl" x="910" y="182" text-anchor="middle"><tspan class="b">the open web</tspan></text>
  <text class="lbl" x="944" y="494" text-anchor="end"><tspan class="b">OpenCode</tspan><tspan class="r"> · the dogs, one per session</tspan></text>
  <text class="tag" id="biscuit-tag" x="472" y="470" text-anchor="middle">Biscuit <tspan class="st" dx="5" id="biscuit-st">at heel</tspan></text>
  <text class="tag" x="652" y="462" text-anchor="middle">Pepper <tspan class="st" dx="5" id="pepper-st">ranging</tspan></text>
  <text class="tag" x="846" y="412" text-anchor="middle">Juniper <tspan class="st" dx="5" id="juniper-st">ranging</tspan></text>
</g>
<g id="fx" aria-hidden="true"></g>
</svg>`;

// A seamless 72-second cycle: deterministic, so ?t=<seconds> freezes any moment for review and
// ?tip=<key> opens a tooltip; reduced motion shows a still frame. Plain ES5 with no backticks,
// inside a template literal.
const WALK_SCRIPT = String.raw`
(function(){
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var svg = document.getElementById("walk");
  if (!svg || !svg.createSVGPoint) return;
  function $(id){ return document.getElementById(id); }

  // A clockwork of one 72-second cycle. Everything that persists (you in the booth, the phone
  // raised, following, the gate, Biscuit at home) is a pure function of the time within the
  // cycle; events only spawn transient things (bubbles, chips, pulses). Every continuous motion
  // completes a whole number of cycles per loop, f(k) being k cycles, so the loop has no seam:
  // time runs on, and the scene at 72 seconds is the scene at 0.
  var P = 72, STEP = 1 / 30, TAU = Math.PI * 2;
  function f(k){ return TAU * k / P; }
  var WX = 340, WY = 480, PX = 375, PY = 288, V = 4800 / P;
  var fx = $("fx"), route = $("route"), routeLen = route.getTotalLength();
  var capWho = $("cap-who"), capText = $("cap-text");

  // Labels sit on badges so scrolling scenery never runs behind their text.
  Array.prototype.forEach.call(svg.querySelectorAll(".lbl"), function(t){
    var b = t.getBBox(), r = document.createElementNS(NS, "rect");
    r.setAttribute("class", "lbl-bg");
    r.setAttribute("x", b.x - 7); r.setAttribute("y", b.y - 3); r.setAttribute("width", b.width + 14);
    r.setAttribute("height", b.height + 6); r.setAttribute("rx", (b.height + 6) / 2);
    t.parentNode.insertBefore(r, t);
  });

  function el(tag, attrs, parent, text){
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    (parent || fx).appendChild(n);
    return n;
  }
  function ease(p){ p = p < 0 ? 0 : p > 1 ? 1 : p; return p < .5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2; }
  function lerp(a, b, p){ return a + (b - a) * p; }
  function mod(t){ return ((t % P) + P) % P; }
  // Seconds since a moment in the cycle, and windows that may wrap past the loop's end.
  function since(t, a){ return mod(t - a); }
  function within(t, a, b){ return since(t, a) < since(b, a); }
  function level(t, a, b, d){
    if (within(t, a, b)) return ease(Math.min(1, since(t, a) / d));
    var s = since(t, b);
    return s < d ? 1 - ease(s / d) : 0;
  }

  // Scenery: trees once around per loop, the curbside five times, the kennel four.
  (function(){
    var trees = $("trees"), tufts = $("tufts");
    var xs = [40, 150, 330, 470, 600, 700, 820, 920];
    for (var tile = 0; tile < 2; tile++) xs.forEach(function(x, i){
      x += tile * 960;
      if (i % 3 === 0){
        el("rect", {x: x - 3, y: 312, width: 6, height: 32, "class": "trunk"}, trees);
        el("circle", {cx: x, cy: 302, r: 21, "class": "tree-a"}, trees);
      } else if (i % 3 === 1){
        el("rect", {x: x - 2, y: 326, width: 4, height: 18, "class": "trunk"}, trees);
        el("ellipse", {cx: x, cy: 304, rx: 11, ry: 30, "class": "tree-b"}, trees);
      } else el("ellipse", {cx: x, cy: 338, rx: 28, ry: 14, "class": "tree-b"}, trees);
    });
    for (var tile2 = 0; tile2 < 2; tile2++) for (var j = 0; j < 14; j++){
      var tx = tile2 * 960 + j * 69 + (j % 3) * 11;
      el("path", {d: "M" + (tx + 30) + " 451 l-16 89", "class": "joint"}, tufts);
      el("path", {d: "M" + tx + " 446 l4 -10 l3 10 l4 -7 l3 7", "class": "tuft"}, tufts);
    }
  })();

  var legs = Array.prototype.map.call(svg.querySelectorAll("[data-leg]"), function(n){
    var b = n.getBBox ? n.getBBox() : {x: 0, y: 0, width: 0};
    return {n: n, dog: n.getAttribute("data-leg"), ph: +n.getAttribute("data-ph"), px: b.x + b.width / 2, py: b.y + 2};
  });
  var knees = Array.prototype.map.call(svg.querySelectorAll("[data-knee]"), function(n){ return {n: n, ph: +n.getAttribute("data-knee")}; });
  var GAIT = {walker: [68, 22], biscuit: [158, 30], pepper: [101, 22], juniper: [108, 20]};

  // --- The cycle's fixed moments -------------------------------------------------------------
  var YOU_OUT = 3.0, YOU_IN = 6.8, YOU_LEAVE = 69.6, YOU_HOME = 73.4;   // barn <-> booth
  var LIT = 7.2, DARK = 69.2;                                           // booth lamp
  var RAISE = 9.6, LOWER = 69.4;                                        // the walker's phone
  var FOLLOW = 32.6, UNFOLLOW = 69.2;                                   // following Pepper
  var BACK = 46.2, OFF_AGAIN = 62.4;                                    // Pepper's bone
  var BID = 70.0, OPEN = 26.2;                                          // Juniper at the gate
  var LEAVE = 27.6, EMERGE = 45.6;                                      // Biscuit and the kennel

  // --- Transient things ----------------------------------------------------------------------
  var sprites = [], state = {}, lastT = 0;
  function sprite(t0, dur, fn, done){ sprites.push({t0: t0, dur: dur, fn: fn, done: done}); }
  function clear(){
    while (fx.firstChild) fx.removeChild(fx.firstChild);
    while ($("logs").firstChild) $("logs").removeChild($("logs").firstChild);
    sprites = []; state = {thinking: false, talking: false};
    idle(); capWho.textContent = ""; capText.textContent = "";
  }
  function pill(x, y, parts, cls, anchor, h){
    h = h || 24;
    var g = el("g", {"class": cls, opacity: 0});
    var r = el("rect", {rx: h / 2, height: h, y: y}, g);
    var t = el("text", {y: y + h / 2 + 5}, g);
    parts.forEach(function(p){ el("tspan", p[1] ? {"class": p[1]} : {}, t, p[0]); });
    var w = (t.getComputedTextLength() || t.textContent.length * 7.4) + h - 2;
    var x0 = anchor === "middle" ? x - w / 2 : x;
    r.setAttribute("x", x0); r.setAttribute("width", w); t.setAttribute("x", x0 + (h - 2) / 2);
    return g;
  }
  function show(node, t0, dur){
    sprite(t0, dur, function(p, now){ node.setAttribute("opacity", Math.min(1, (now - t0) / .25, (t0 + dur - now) / .4)); },
      function(){ node.remove(); });
  }
  // An earcon, drawn as rings at the phone.
  function cue(t0){
    var c = el("circle", {cx: PX, cy: PY, r: 6, "class": "ripple"}), c2 = el("circle", {cx: PX, cy: PY, r: 6, "class": "ripple"});
    sprite(t0, 1.2, function(p){
      c.setAttribute("r", 6 + 30 * p); c.setAttribute("opacity", 1 - p);
      var q = Math.max(0, p - .25) / .75; c2.setAttribute("r", 6 + 30 * q); c2.setAttribute("opacity", p < .25 ? 0 : 1 - q);
    }, function(){ c.remove(); c2.remove(); });
  }
  // A dot on the phone line: from the booth to the walker's phone, or back.
  function travel(t0, cls, outward){
    var dot = el("circle", {r: 6, "class": "dot " + cls, opacity: 0});
    sprite(t0, 2.0, function(p){
      var pt = route.getPointAtLength(routeLen * (outward ? ease(p) : 1 - ease(p)));
      dot.setAttribute("cx", pt.x); dot.setAttribute("cy", pt.y); dot.setAttribute("opacity", 1);
    }, function(){ dot.remove(); });
  }
  function say(t0, who, text){
    capWho.textContent = who; capText.textContent = text;
    var g, tail;
    if (who === "you"){
      g = pill(16, 182, [[text]], "bubble you");
      tail = el("path", {d: "M98 205 L112 207 L108 205 Z", "class": "tail you", opacity: 0});
      sprite(t0, 1.2, function(){ state.talking = true; }, function(){ state.talking = false; });
    } else {
      g = pill(392, 212, [[text]], "bubble walker");
      tail = el("path", {d: "M399 235 L381 256 L414 235 Z", "class": "tail walker", opacity: 0});
      var v = $("voice");
      sprite(t0, 1.6, function(p){ v.setAttribute("opacity", Math.abs(Math.sin(p * 9)) * .9); }, function(){ v.setAttribute("opacity", 0); });
    }
    show(g, t0, 3.8); show(tail, t0, 3.8);
    sprite(t0, 3.8, function(){}, function(){ if (capText.textContent === text){ capWho.textContent = ""; capText.textContent = ""; } });
    travel(t0 + .3, who, who === "you");
  }
  function idle(){ var c1 = $("c1"); c1.textContent = "· · ·"; c1.setAttribute("class", "think idle"); $("c2").textContent = ""; }
  // The Orchestrator thinks it over, then calls one of its neutral tools.
  function think(t0, gloss, call, quiet, dur){
    var c1 = $("c1"), c2 = $("c2");
    sprite(t0, dur || (quiet ? 2.6 : 4.4), function(p, now){
      var e = now - t0;
      state.thinking = quiet ? .55 : e < .8 ? .7 : 1;
      if (e < .8){ c1.setAttribute("class", "think"); c1.textContent = ["·", "· ·", "· · ·"][Math.floor(e * 4) % 3]; c2.textContent = ""; }
      else {
        c1.setAttribute("class", "think gloss"); c1.textContent = gloss;
        c2.textContent = call.slice(0, Math.floor((e - .8) * 34));
      }
    }, function(){ state.thinking = false; idle(); });
  }
  // Agent tool calls rise off a dog; an unfollowed dog's are faint, out of earshot.
  function chip(t0, dog, kind, arg){
    if (kind === "execute"){ state["squat-" + dog] = mod(t0 + .1); }
    if (kind === "execute") poop(t0 + .6, dog === "pepper" ? 594 : 808, dog === "pepper" ? 432 : 394, dog === "pepper" ? 59 : 40);
    var faint = dog === "juniper" || !within(mod(t0), FOLLOW, UNFOLLOW);
    var o = dog === "pepper" ? [660, 364, 62] : [772, 300, 40];
    var g = pill(o[0], o[1], [[kind, "k"], [arg ? "  " + arg : ""]], "chip" + (faint ? " faint" : ""), "middle", 22);
    sprite(t0, 2.8, function(p){
      g.setAttribute("transform", "translate(0," + (-o[2] * p).toFixed(1) + ")");
      g.setAttribute("opacity", (faint ? .94 : 1) * Math.min(1, p * 6, (1 - p) * 4));
    }, function(){ g.remove(); });
  }
  function remark(t0, text){
    var g = pill(672, 350, [[text]], "remark", "middle");
    var tail = el("path", {d: "M686 373 L704 394 L698 373 Z", "class": "tail remark", opacity: 0});
    show(g, t0, 2.8); show(tail, t0, 2.8);
  }
  function quad(a, c, b, p){
    var q = 1 - p;
    return [q * q * a[0] + 2 * q * p * c[0] + p * p * b[0], q * q * a[1] + 2 * q * p * c[1] + p * p * b[1]];
  }
  function flight(t0, dur, a, c, b){
    var path = el("path", {d: "M" + a.join(" ") + " Q" + c.join(" ") + " " + b.join(" "), "class": "flight", opacity: 0});
    sprite(t0, dur, function(p){ path.setAttribute("opacity", Math.min(1, p * 4, (1 - p) * 3)); }, function(){ path.remove(); });
  }
  // fetch: a stick out over the stream, a page back to Juniper's mouth.
  function fetchPage(t0){
    var stick = el("path", {d: "M-13 0 L13 0", "class": "stick", opacity: 0});
    var page = el("g", {"class": "page", opacity: 0});
    el("rect", {x: -10, y: -13, width: 20, height: 26, rx: 2}, page);
    el("path", {d: "M-5 -6 H5 M-5 -1 H5 M-5 4 H3"}, page);
    var a = [862, 356], c = [900, 160], b = [906, 226];
    flight(t0, 2.8, a, c, b);
    sprite(t0, 2.8, function(p){
      var out = p < .45, e = out ? ease(p / .45) : ease((p - .55) / .45);
      var xy = out ? quad(a, c, b, e) : quad(b, c, a, e);
      var n = out ? stick : page, other = out ? page : stick;
      n.setAttribute("transform", "translate(" + xy[0].toFixed(1) + " " + xy[1].toFixed(1) + ") rotate(" + (out ? p * 900 : (1 - e) * 20).toFixed(0) + ")");
      n.setAttribute("opacity", p > .45 && p < .55 ? 0 : 1); other.setAttribute("opacity", 0);
    }, function(){ stick.remove(); page.remove(); });
  }
  // git push: Pepper rears and tosses a parcel over the stream into the mailbox by the town.
  function push(t0){
    var box = el("g", {"class": "parcel", opacity: 0});
    el("rect", {x: -11, y: -8, width: 22, height: 16, rx: 2}, box);
    el("path", {d: "M0 -8 V8 M-11 0 H11"}, box);
    var a = [712, 404], c = [800, 110], b = [854, 236];
    flight(t0, 1.9, a, c, b);
    sprite(t0 + .2, 1.4, function(p){
      var xy = quad(a, c, b, ease(p));
      box.setAttribute("transform", "translate(" + xy[0].toFixed(1) + " " + xy[1].toFixed(1) + ") rotate(" + (p * 240).toFixed(0) + ") scale(" + (1 - .3 * p).toFixed(3) + ")");
      box.setAttribute("opacity", p > .92 ? (1 - p) / .08 : 1);
    }, function(){ box.remove(); });
  }
  function digBone(t0){
    var b = el("g", {"class": "dropped", opacity: 0});
    el("rect", {x: -13, y: -3, width: 26, height: 6, rx: 3}, b);
    sprite(t0, .5, function(p){
      b.setAttribute("transform", "translate(716 " + (432 - 26 * ease(p)).toFixed(1) + ") rotate(" + (30 - 30 * p).toFixed(0) + ")");
      b.setAttribute("opacity", 1);
    }, function(){ b.remove(); });
  }
  function dropBone(t0){
    var b = el("g", {"class": "dropped", opacity: 0});
    el("rect", {x: -13, y: -3, width: 26, height: 6, rx: 3}, b);
    sprite(t0, 2.4, function(p){
      b.setAttribute("transform", "translate(" + (714 - 30 * p).toFixed(1) + " " + (404 + 28 * ease(Math.min(1, p * 4))).toFixed(1) + ") rotate(" + (Math.min(1, p * 4) * 40).toFixed(0) + ")");
      b.setAttribute("opacity", 1 - ease(Math.max(0, p - .7) / .3));
    }, function(){ b.remove(); });
  }
  // An Agent message riding a leash: down to the dog, or up from it to the walker's hand.
  function pulse(t0, id, up){
    var dot = el("circle", {r: 5.5, "class": "dot msg", opacity: 0}), path = $(id);
    sprite(t0, .9, function(p){
      var len = path.getTotalLength();
      if (!len) return;
      var pt = path.getPointAtLength(len * (up ? 1 - ease(p) : ease(p)));
      dot.setAttribute("cx", pt.x); dot.setAttribute("cy", pt.y); dot.setAttribute("opacity", 1);
    }, function(){ dot.remove(); });
  }
  // An artifact goes to a dog's mouth and comes back: picked up and put down again, whether
  // the dog wrote to the file or only read it. A write leaves it glowing for a moment.
  function frisbeeShape(g){
    el("ellipse", {rx: 14, ry: 5.5, fill: "#c0392b"}, g);
    el("ellipse", {rx: 8, ry: 3, fill: "none", stroke: "#f4e8d4", "stroke-width": 1.6}, g);
  }
  function pouchShape(g){
    el("path", {d: "M-7 6 Q-9 -4 -2 -5 L2 -5 Q9 -4 7 6 Z", fill: "#efe3c6"}, g);
    el("path", {d: "M-3 -5 L-5 -10 M3 -5 L5 -10", stroke: "#9a3818", "stroke-width": 1.8, "stroke-linecap": "round"}, g);
  }
  function carry(t0, restId, glowId, shape, home, mouth, wrote, spin){
    var rest = $(restId), glow = $(glowId), fly = el("g", {opacity: 0});
    shape(fly);
    var c = [(home[0] + mouth[0]) / 2, Math.min(home[1], mouth[1]) - 60];
    sprite(t0, 2.4, function(p){
      rest.setAttribute("opacity", 0);
      var e = p < .4 ? ease(p / .4) : ease((p - .6) / .4);
      var xy = p < .4 ? quad(home, c, mouth, e) : p < .6 ? mouth : quad(mouth, c, home, e);
      fly.setAttribute("transform", "translate(" + xy[0].toFixed(1) + " " + xy[1].toFixed(1) + ") rotate(" + (spin ? p * 720 : 0).toFixed(0) + ")");
      fly.setAttribute("opacity", 1);
    }, function(){ fly.remove(); rest.setAttribute("opacity", 1); });
    if (wrote) sprite(t0 + 2.4, 1.6, function(p){ glow.setAttribute("opacity", (1 - p).toFixed(2)); }, function(){ glow.setAttribute("opacity", 0); });
  }
  function frisbee(t0, mouth, wrote){ carry(t0, "frisbee-disc", "frisbee-glow", frisbeeShape, [748, 424], mouth, wrote, true); }
  // start_recording: your words become a tied pouch the walker drops into the basket.
  function dropPouch(t0){
    var fly = el("g", {opacity: 0});
    pouchShape(fly);
    var a = [380, 300], c = [470, 250], b = [546, 432];
    sprite(t0, 1.2, function(p){
      var xy = quad(a, c, b, ease(p));
      fly.setAttribute("transform", "translate(" + xy[0].toFixed(1) + " " + xy[1].toFixed(1) + ")");
      fly.setAttribute("opacity", 1);
    }, function(){ fly.remove(); });
    sprite(t0 + 1.2, 1.6, function(p){ $("basket-glow").setAttribute("opacity", (1 - p).toFixed(2)); }, function(){ $("basket-glow").setAttribute("opacity", 0); });
  }
  // Logs: a dog that runs something leaves a little heap behind, and the walk leaves it
  // behind in turn. Nothing loops it back: it drifts off with the grass and is gone.
  function poop(t0, x, y, speed){
    var g = el("g", {"class": "log", "data-tip": "log", opacity: 0}, $("logs"));
    el("ellipse", {"class": "pad", rx: 12, ry: 10, cy: -5}, g);
    el("ellipse", {cx: 0, cy: -2.5, rx: 6, ry: 3.2, fill: "#6b4a2e"}, g);
    el("ellipse", {cx: .5, cy: -6, rx: 4.4, ry: 2.8, fill: "#7d5636"}, g);
    el("ellipse", {cx: 1, cy: -9, rx: 2.6, ry: 2.1, fill: "#6b4a2e"}, g);
    var dur = (x + 40) / speed;
    sprite(t0, dur, function(p, now){
      g.setAttribute("transform", "translate(" + (x - speed * (now - t0)).toFixed(1) + " " + y + ")");
      g.setAttribute("opacity", Math.min(1, (now - t0) / .3));
    }, function(){ g.remove(); });
  }
  function at(t, fn){ return {t: t, run: fn}; }

  var events = [
    // Hello: you reach the booth and dial; the walker's phone buzzes in a pocket and comes up.
    // The Opening is fixed text: the caller-ID clause, the name, what happened since, a question.
    at(7.4, function(t){ travel(t, "you", true); }),
    at(10.2, function(t){ cue(t); }),
    at(10.4, function(t){ say(t, "walker", "Verified. Dogwalk. Since then, Juniper asked for permission. Where to?"); }),
    at(13.4, function(t){ say(t, "you", "What does Juniper want?"); }),
    at(15.8, function(t){ think(t, "Look at Juniper's request.", "inspect_managed_session(Juniper)", false, 2.4); }),
    at(17.8, function(t){ say(t, "walker", "To run the migration. Allow once, always allow, or reject?"); }),
    at(20.6, function(t){ say(t, "you", "Allow once. And let Biscuit go home."); }),
    at(23.0, function(t){ think(t, "Allow Juniper, once.", "resolve_permission(Juniper, ‹Allow once›)", false, 2.3); }),
    at(25.0, function(t){ pulse(t, "l-juniper"); }),
    at(25.4, function(t){ think(t, "Let Biscuit go home.", "close_managed_session(Biscuit)", false, 2.2); }),
    at(LEAVE - .5, function(t){ pulse(t, "l-biscuit"); }),
    at(28.4, function(t){ say(t, "you", "What's Pepper up to?"); }),
    at(30.8, function(t){ think(t, "Follow Pepper.", "follow_managed_session(Pepper)", false, 2.6); }),
    at(FOLLOW, function(t){ cue(t); }),
    at(33.0, function(t){ chip(t, "pepper", "read", "parser.py"); }),
    at(33.6, function(t){ say(t, "walker", "Pepper's reading the parser."); }),
    at(35.6, function(t){ remark(t, "“Found it: an off-by-one.”"); cue(t); }),
    at(38.0, function(t){ say(t, "you", "Bring Biscuit back."); }),
    at(38.4, function(t){ chip(t, "pepper", "edit", "tokenizer.py"); }),
    at(40.4, function(t){ think(t, "Find Biscuit's old session.", "discover_persisted_sessions()", false, 2.2); }),
    at(41.0, function(t){ chip(t, "pepper", "execute", "pytest"); cue(t); }),
    at(42.7, function(t){ think(t, "Bring Biscuit back.", "load_persisted_session(r1, Biscuit)", false, 2.4); }),
    at(43.2, function(t){ chip(t, "pepper", "execute", "git push"); }),
    at(43.5, function(t){ push(t); }),
    at(EMERGE - .5, function(t){ pulse(t, "l-biscuit"); }),
    at(45.4, function(t){ digBone(t); }),
    at(BACK, function(t){ chip(t, "pepper", "done", "end_turn"); cue(t); }),
    at(46.8, function(t){ say(t, "walker", "Biscuit's back. Pepper reports tests pass, fix pushed."); }),
    at(50.0, function(t){ say(t, "you", "Good dog. I'll describe the next one."); }),
    at(52.4, function(t){ think(t, "Record a voice note.", "start_recording(300)", false, 2.0); }),
    at(54.0, function(t){ say(t, "walker", "Recording after the beep. Pound when you're done."); }),
    at(56.4, function(t){ cue(t); }),
    at(56.6, function(t){ say(t, "you", "It fails near midnight, every time. #"); }),
    at(59.2, function(t){ dropPouch(t); cue(t + 1.2); }),
    at(59.8, function(t){ think(t, "Send Pepper off with it.", "begin_prompt_turn(Pepper, \"…\")", false, 2.4); }),
    at(61.5, function(t){ pulse(t, "l-pepper"); }),
    at(OFF_AGAIN, function(t){ dropBone(t); cue(t); chip(t, "pepper", "read", "recording-….wav"); carry(t + .2, "pouch-in", "basket-glow", pouchShape, [546, 430], [708, 406], false, false); }),
    at(62.8, function(t){ say(t, "walker", "Saved, and Pepper's on it."); }),
    at(64.6, function(t){ chip(t, "pepper", "read", "NOTES.md"); frisbee(t + .2, [708, 406], false); }),
    at(65.4, function(t){ say(t, "you", "Bye!"); }),
    at(66.6, function(t){ chip(t, "pepper", "search", "parse_date"); }),
    // Goodbye: the walker answers and you hang up; following ends with the call. No earcon
    // marks a call's end.
    at(66.8, function(t){ say(t, "walker", "Bye."); }),
    at(68.4, function(t){ chip(t, "pepper", "edit", "dates.py"); }),
    at(30.6, function(t){ chip(t, "juniper", "fetch", "alembic docs"); fetchPage(t + .3); }),
    // A file outlives the sessions around it: Juniper writes the shared notes, Pepper's next
    // turn reads them, and the frisbee stays in the park through every call.
    at(37.4, function(t){ chip(t, "juniper", "edit", "NOTES.md"); frisbee(t + .2, [856, 368], true); }),
    // Between calls: Juniper reaches a gate; the request rides up the leash, and the walker's
    // head only notes it for the next call's Opening.
    at(BID + .3, function(t){ pulse(t, "l-juniper", true); }),
    at(BID + 1.3, function(t){ think(t, "Noted for the next call.", "permission request · Juniper", true); })
  ];
  var JUN = [["execute", "migrate.py"], ["read", "migrate.log"], ["read", "alembic.ini"], ["edit", "models.py"], ["search", "user_id"],
    ["read", "schema.sql"], ["edit", "migrate.py"], ["execute", "pytest"], ["read", "README.md"], ["edit", "models.py"], ["search", "alembic"]];
  for (var i = 0; i < 21; i++) (function(i){
    var t = 26.9 + i * 2.1, c = t > 67.4 ? ["execute", "migrate.py"] : JUN[i % JUN.length];
    if (t > 69 || within(t, 29.6, 32.4) || within(t, 36.4, 39.6)) return;
    events.push(at(t, function(t0){ chip(t0, "juniper", c[0], c[1]); if (c[0] === "fetch") fetchPage(t0 + .3); }));
  })(i);
  var PEP = [["read", "test_dates.py"], ["execute", "pytest"], ["search", "tokenize"], ["read", "tokenizer.py"], ["read", "lexer.py"]];
  for (var k = 0; k < 16; k++) (function(k){
    var t = mod(69.9 + k * 2.0);
    if (within(t, 31.6, 69.6)) return;
    events.push(at(t, function(t0){ chip(t0, "pepper", PEP[k % PEP.length][0], PEP[k % PEP.length][1]); }));
  })(k);

  function leash(id, ax, ay, bx, by, sag){
    var d = "M" + ax.toFixed(1) + " " + ay.toFixed(1) + " Q" + ((ax + bx) / 2).toFixed(1) + " " +
      ((ay + by) / 2 + sag).toFixed(1) + " " + bx.toFixed(1) + " " + by.toFixed(1);
    $(id).setAttribute("d", d); $(id + "-hit").setAttribute("d", d);
  }
  function shadow(id, x, y, lift, show){
    var k = 1 - Math.min(.25, lift / 24);
    $(id).setAttribute("transform", "translate(" + x.toFixed(1) + " " + y + ") scale(" + k.toFixed(3) + ")");
    $(id).setAttribute("opacity", show === false ? 0 : 1);
  }
  function rot(id, a, x, y){ $(id).setAttribute("transform", "rotate(" + a.toFixed(2) + " " + x + " " + y + ")"); }
  function squatting(dog, t){ var q = state["squat-" + dog]; return q !== undefined && since(t, q) < 1.1; }
  function kennelX(t){ var x = 452 - (V * since(t, EMERGE)) % 1200; return x < -148 ? x + 1200 : x; }

  // Everything persistent, as a pure function of the time within the cycle.
  function pose(t){
    $("trees").setAttribute("transform", "translate(" + (-(t * 960 / P) % 960).toFixed(1) + " 0)");
    $("rays").setAttribute("transform", "rotate(" + (t * 360 / P).toFixed(2) + ")");
    $("tufts").setAttribute("transform", "translate(" + (-(t * V) % 960).toFixed(1) + " 0)");

    // You: barn, walk, booth, walk, barn.
    var walkIn = within(t, YOU_OUT, YOU_IN), walkOut = within(t, YOU_LEAVE, YOU_HOME), inBooth = within(t, YOU_IN, YOU_LEAVE);
    var yx = walkIn ? lerp(60, 113, since(t, YOU_OUT) / (YOU_IN - YOU_OUT)) : lerp(113, 60, since(t, YOU_LEAVE) / (YOU_HOME - YOU_LEAVE));
    $("you-walk").setAttribute("transform", "translate(" + yx.toFixed(1) + " " + (253 - Math.abs(Math.sin(f(160) * t)) * 1.5).toFixed(1) + ")");
    $("you-walk").setAttribute("opacity", walkIn || walkOut ? 1 : 0);
    $("you-in").setAttribute("opacity", inBooth ? 1 : 0);
    $("you-in").setAttribute("transform", "translate(" + (.8 * Math.sin(f(9) * t)).toFixed(2) + " 0)");
    $("booth-glass").setAttribute("class", state.talking ? "talk" : within(t, LIT, DARK) ? "lit" : "");

    // The walker: stride, a phone that comes up for the call, and the following gesture.
    legs.forEach(function(l){
      var g = GAIT[l.dog], amp = g[1];
      if (l.dog === "juniper" && within(t, BID, OPEN + .5)) amp = 0;
      if (squatting(l.dog, t)) amp = 0;
      if (l.dog === "pepper" && within(t, FOLLOW, UNFOLLOW) && !within(t, BACK, OFF_AGAIN)) amp = 12;
      l.n.setAttribute("transform", "rotate(" + (amp * Math.sin(f(g[0]) * t + l.ph)).toFixed(2) + " " + l.px + " " + l.py + ")");
    });
    knees.forEach(function(k){
      k.n.setAttribute("transform", "rotate(" + (34 * Math.max(0, -Math.cos(f(68) * t + k.ph))).toFixed(2) + " 0 -52)");
    });
    var wy = WY - Math.abs(Math.sin(f(68) * t)) * 3;
    rot("walker-arm", 18 * Math.sin(f(68) * t), -8, -170);
    $("walker").setAttribute("transform", "translate(" + WX + " " + wy.toFixed(1) + ")");
    shadow("sh-walker", WX + 2, WY + 1, WY - wy);
    var up = level(t, RAISE, LOWER, .6), buzz = within(t, 9.0, RAISE);
    rot("arm-up", lerp(-4 + 7 * Math.sin(f(68) * t + 3.1), -35, up), 14, -176);
    rot("arm-fore", lerp(-8, -138.5, up) + (buzz ? 6 * Math.sin(t * 90) : 0), 14, -146);
    $("phone").setAttribute("opacity", up > .02 || buzz ? 1 : 0);
    if (buzz) $("phone-screen").setAttribute("opacity", .9);
    else if (!within(t, RAISE, LOWER)) $("phone-screen").setAttribute("opacity", 0);
    var fo = level(t, FOLLOW, UNFOLLOW, .6), th = -15 * fo * Math.PI / 180;
    rot("walker-head", 15 * fo, 2, -196);
    rot("walker-leash", -15 * fo, 12, -170);
    var hx = WX + 12 + 54 * Math.cos(th) - 34 * Math.sin(th), hy = wy - 170 + 54 * Math.sin(th) + 34 * Math.cos(th);

    var cloud = $("cloud");
    cloud.setAttribute("opacity", state.thinking || (within(t, 10.2, DARK) ? .5 : .22));

    // Biscuit: home in the kennel, out on a fresh leash, home again.
    var kx = kennelX(t), out = within(t, EMERGE, LEAVE + .6);
    $("kennel").setAttribute("transform", "translate(" + kx.toFixed(1) + " 441) scale(.78)");
    var home = !out;
    $("kennel-biscuit").setAttribute("opacity", home ? 1 : 0);
    var zp = (t * 0.7) % 1;
    $("zzz").setAttribute("opacity", home ? Math.sin(zp * Math.PI) : 0);
    $("zzz").setAttribute("transform", "translate(" + (zp * 6).toFixed(1) + " " + (-zp * 10).toFixed(1) + ")");
    var heelX = 452 + 3 * Math.sin(f(15) * t), heelY = 440 - Math.abs(Math.sin(f(158) * t)) * 2;
    var bx = heelX, by = heelY, bs = .9, bo = 1;
    if (within(t, EMERGE, EMERGE + .8)){ var e1 = ease(since(t, EMERGE) / .8); bx = lerp(kx, heelX, e1); by = lerp(437, heelY, e1); bs = lerp(.5, .9, e1); }
    else if (within(t, LEAVE, LEAVE + .6)){ var e2 = ease(since(t, LEAVE) / .6); bx = lerp(heelX, kx, e2); by = lerp(heelY, 437, e2) - Math.sin(e2 * Math.PI) * 14; bs = lerp(.9, .5, e2); bo = 1 - Math.max(0, e2 - .55) / .45; }
    else if (home) bo = 0;
    $("biscuit").setAttribute("transform", "translate(" + bx.toFixed(1) + " " + by.toFixed(1) + ") scale(" + bs.toFixed(3) + ")");
    $("biscuit").setAttribute("opacity", bo);
    shadow("sh-biscuit", bx + 5, 441, 440 - by, !home);
    rot("biscuit-head", -3 * Math.sin(f(19) * t), 30, -40);
    var cxB = bx + 22 * bs / .9, cyB = by - 40 * bs / .9, lb = $("l-biscuit");
    if (within(t, EMERGE - .5, LEAVE + .3)){
      if (within(t, EMERGE - .5, EMERGE)){ cxB = kx; cyB = 425; }
      var grow = within(t, EMERGE - .5, EMERGE - .1) ? ease(since(t, EMERGE - .5) / .4) : within(t, LEAVE, LEAVE + .3) ? 1 - ease(since(t, LEAVE) / .3) : 1;
      leash("l-biscuit", hx, hy, lerp(hx, cxB, grow), lerp(hy, cyB, grow), 18 * grow);
    } else { lb.setAttribute("d", ""); $("l-biscuit-hit").setAttribute("d", ""); }
    $("biscuit-st").textContent = home || within(t, LEAVE, LEAVE + .6) ? "home" : "at heel";
    $("biscuit-tag").setAttribute("x", (home ? kx : bx + 20).toFixed(1));
    $("biscuit-tag").setAttribute("y", home ? 456 : 470);
    $("biscuit-tag").setAttribute("opacity", home && (kx < 10 || kx > 950) ? 0 : 1);

    // Pepper: ranging, followed, back with a bone, off again, ranging once the call ends.
    var followed = within(t, FOLLOW, UNFOLLOW), back = within(t, BACK, OFF_AGAIN);
    var hs = since(t, 52.4), hop = hs < 1.2 ? Math.abs(Math.sin(hs * Math.PI * 2.5)) * 14 : 0;
    var px = 640 + 6 * Math.sin(f(8) * t), py = 431 - Math.abs(Math.sin(f(101) * t)) * 1.5 - hop;
    $("pepper").setAttribute("transform", "translate(" + px.toFixed(1) + " " + py.toFixed(1) + ") scale(.86)" + (squatting("pepper", t) ? " rotate(-9 40 0)" : ""));
    shadow("sh-pepper", px + 9, 432, 431 - py);
    var rear = within(t, 43.3, 44.2);
    rot("pepper-head", rear ? -28 : back ? -8 : 12 + 9 * Math.sin(f(80) * t), 44, -32);
    rot("pepper-tail", back ? (hop ? 32 : 22) * Math.sin(f(hop ? 298 : 183) * t) : 6 * Math.sin(f(34) * t), -46, -30);
    $("bone").setAttribute("opacity", back ? 1 : 0);
    $("spot").setAttribute("opacity", fo);
    $("l-pepper").setAttribute("class", followed ? "leash hot" : "leash far");
    leash("l-pepper", hx, hy, px + 38, py - 37, lerp(10, -14, fo));
    $("pepper-st").textContent = followed ? (back ? "followed, back" : "followed") : "ranging";

    // Juniper: waits at a gate from between calls until you allow it, then ranges on.
    var waiting = within(t, BID, OPEN), sitting = within(t, BID, OPEN + .5);
    var base = 830 + 18 * Math.sin(f(5) * t), sx = 830 + 18 * Math.sin(f(5) * BID), jx = base, jy = 393;
    var so = since(t, OPEN);
    if (sitting) jx = sx;
    else if (so < 2.3) jx = sx + 46 * ease((so - .5) / 1.8);
    else if (so < 5.3) jx = lerp(sx + 46, base, ease((so - 2.3) / 3));
    $("juniper").setAttribute("transform", "translate(" + jx.toFixed(1) + " " + jy + ") scale(.6)" + (squatting("juniper", t) ? " rotate(-8 30 0)" : ""));
    $("juniper-body").setAttribute("transform", sitting ? "translate(0 6)" : "translate(0 " + (-Math.abs(Math.sin(f(108) * t)) * 3).toFixed(1) + ")");
    shadow("sh-juniper", jx + 4, 394, sitting ? 0 : 3);
    rot("juniper-head", sitting ? -6 : 7 * Math.sin(f(37) * t), 36, -56);
    var tug = since(t, BID) < 1.6;
    $("l-juniper").setAttribute("class", waiting ? "leash far pull" : "leash far");
    leash("l-juniper", hx, hy, jx + 19, jy - 35, tug ? -4 + 5 * Math.sin(t * 38) : 62);
    $("juniper-st").textContent = sitting ? "at a gate" : "ranging";

    var gate = $("gate"), card = $("card"), gx = sx + 60, sb = since(t, BID);
    var gOp = 0, panel = 1, cOp = 0;
    if (waiting){ gOp = 1; cOp = ease((sb - .2) / .4); }
    else if (within(t, BID - .7, BID)){ gOp = ease(since(t, BID - .7) / .5); }
    else if (so < 2.4){ gOp = 1 - ease((so - 1.6) / .8); panel = 1 - .9 * ease(so / .8); cOp = 1 - ease((so - .4) / .4); }
    gate.setAttribute("transform", "translate(" + gx.toFixed(1) + " 395) scale(1 " + (within(t, BID - .7, BID) ? gOp : 1).toFixed(3) + ")");
    gate.setAttribute("opacity", gOp.toFixed(3));
    gate.style.pointerEvents = gOp > .2 ? "" : "none";
    $("gate-panel").setAttribute("transform", "scale(" + panel.toFixed(3) + " 1)");
    card.setAttribute("opacity", cOp.toFixed(3));
    card.style.pointerEvents = cOp > .2 ? "" : "none";
    var allowed = within(t, 25.9, BID);
    $("card-t").textContent = allowed ? "Allowed once." : "Run migrate.py?";
    $("opt-once").setAttribute("class", allowed ? "opt on" : "opt");
  }

  // Time runs on without resetting: an event fires at each of its moments in every cycle.
  function step(T){
    for (var i = 0; i < events.length; i++){
      var e = events[i], o = e.t + Math.floor((T - e.t) / P) * P;
      if (o > lastT && o <= T) e.run(o);
    }
    sprites = sprites.filter(function(s){
      if (T >= s.t0 + s.dur){ if (s.done) s.done(); return false; }
      if (T >= s.t0) s.fn((T - s.t0) / s.dur, T);
      return true;
    });
    pose(mod(T));
    lastT = T;
  }
  // Jump to a moment: warm up the few seconds before it so transient things are in flight.
  function seek(T){
    clear(); lastT = T - 22;
    for (var s = T - 22 + STEP; s < T; s += STEP) step(s);
    step(T);
  }

  // --- Tooltips: a friendly line by the element, the technical story in the panel below ------
  // Hover or focus previews; a click or Enter pins, so the pointer is free to select text and
  // follow links in the panel; Escape, or a click on empty ground, unpins.
  var TIPS = window.WALK_TIPS || {}, TECH = window.WALK_TECH || {};
  var ORDER = (window.WALK_ORDER || []).filter(function(k){ return k !== "log"; });
  var tip = $("tip"), tipT = $("tip-t"), tipP = $("tip-p"), fig = svg.parentNode, hot = null, pinned = null;
  var tech = $("tech"), techH = $("tech-h"), techB = $("tech-b"), techIntro = techB.innerHTML;
  function showTip(node, x, y){
    var key = node.getAttribute("data-tip"), d = TIPS[key];
    if (!d) return hideTip();
    if (hot !== node){ if (hot) hot.classList.remove("hot"); hot = node; node.classList.add("hot"); }
    var fr = fig.getBoundingClientRect(), sr = svg.getBoundingClientRect(), narrow = fr.width < 640;
    tipT.textContent = d[0]; tipP.textContent = d[1]; tip.classList.toggle("on", !narrow);
    fig.classList.toggle("tipping", !narrow);
    unlit();
    if (node.classList.contains("hit")) $(node.id.replace("-hit", "")).classList.add("lit");
    techH.textContent = "Under the hood · " + d[0];
    techB.innerHTML = (narrow ? '<span class="friendly">' + d[1] + '</span> ' : "") + (TECH[key] || "");
    Array.prototype.forEach.call(techB.querySelectorAll("a"), function(a){ a.target = "_blank"; a.rel = "noopener"; });
    if (narrow) return;
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var left = x - fr.left + 16, top = y - fr.top + 18;
    if (left + w > fr.width) left = x - fr.left - w - 16;
    if (left < 0) left = 4;
    if (top + h > sr.bottom - fr.top) top = y - fr.top - h - 14;
    if (top < 0) top = 4;
    tip.style.left = left + "px"; tip.style.top = top + "px";
  }
  function unlit(){ Array.prototype.forEach.call(svg.querySelectorAll(".leash.lit"), function(l){ l.classList.remove("lit"); }); }
  function hideTip(){
    if (pinned) return;
    if (hot) hot.classList.remove("hot");
    hot = null; tip.classList.remove("on");
    techH.textContent = "Under the hood"; techB.innerHTML = techIntro;
    fig.classList.remove("tipping"); unlit();
  }
  function anchor(n){ var r = n.getBoundingClientRect(); return [r.left + r.width / 2, r.bottom - Math.min(10, r.height / 4)]; }
  function pin(n, x, y){
    if (pinned) pinned.setAttribute("aria-pressed", "false");
    pinned = null;
    if (n){ showTip(n, x, y); pinned = n; n.setAttribute("aria-pressed", "true"); }
    tech.classList.toggle("pinned", !!pinned);
    $("unpin").hidden = !pinned;
    if (!n) hideTip();
  }
  svg.addEventListener("pointermove", function(e){
    if (pinned) return;
    var n = e.target && e.target.closest ? e.target.closest("[data-tip]") : null;
    if (n && svg.contains(n)) showTip(n, e.clientX, e.clientY); else hideTip();
  });
  svg.addEventListener("pointerleave", hideTip);
  svg.addEventListener("click", function(e){
    var n = e.target && e.target.closest ? e.target.closest("[data-tip]") : null;
    pin(n && n !== pinned ? n : null, e.clientX, e.clientY);
  });

  // Keyboard: the scene's parts join the tab order in narrative order (you first, the
  // mailbox last), one stop each; arrows move too, and Tab past the end leaves the scene.
  var stops = ORDER.map(function(key){
    return svg.querySelector(key === "gate" ? "#card" : '[data-tip="' + key + '"]');
  }).filter(Boolean);
  stops.forEach(function(n, i){
    n.setAttribute("tabindex", i === 0 ? "0" : "-1");
    n.setAttribute("role", "button");
    n.setAttribute("aria-label", (TIPS[n.getAttribute("data-tip")] || [""])[0]);
    n.setAttribute("aria-describedby", "tip-p");
    n.setAttribute("aria-pressed", "false");
  });
  function shown(n){
    var g = n.id === "card" ? n : n.closest("#biscuit") || n;
    return g.getAttribute("opacity") === null || parseFloat(g.getAttribute("opacity")) > .1;
  }
  // The next part on show from i in direction d, or -1 past either end.
  function next(i, d){
    for (var j = i + d; j >= 0 && j < stops.length; j += d) if (shown(stops[j])) return j;
    return -1;
  }
  function go(i){
    stops.forEach(function(n, j){ n.setAttribute("tabindex", j === i ? "0" : "-1"); });
    stops[i].focus();
  }
  stops.forEach(function(n, i){
    n.addEventListener("focus", function(){ if (!pinned || pinned === n){ var a = anchor(n); showTip(n, a[0], a[1]); } });
    n.addEventListener("blur", function(){ if (!pinned) hideTip(); });
    n.addEventListener("keydown", function(e){
      if (e.currentTarget !== e.target) return;
      e.stopPropagation();
      var k = e.key, j;
      if ((k === "Tab" && !e.shiftKey) || k === "ArrowRight" || k === "ArrowDown"){
        j = next(i, 1);
        if (j >= 0){ e.preventDefault(); if (pinned) pin(null); go(j); }
      } else if ((k === "Tab" && e.shiftKey) || k === "ArrowLeft" || k === "ArrowUp"){
        j = next(i, -1);
        if (j >= 0){ e.preventDefault(); if (pinned) pin(null); go(j); }
      } else if (k === "Home" || k === "End"){
        e.preventDefault(); j = k === "Home" ? next(-1, 1) : next(stops.length, -1);
        if (j >= 0){ if (pinned) pin(null); go(j); }
      } else if (k === "Enter" || k === " "){
        e.preventDefault(); var a = anchor(n); pin(pinned === n ? null : n, a[0], a[1]);
      } else if (k === "Escape"){ pin(null); }
    });
  });
  document.addEventListener("keydown", function(e){ if (e.key === "Escape" && pinned) pin(null); });
  $("unpin").addEventListener("click", function(){ pin(null); });
  function tipAt(key){
    var n = key === "gate" ? $("card") : svg.querySelector('[data-tip="' + key + '"]');
    if (n){ var a = anchor(n); showTip(n, a[0], a[1]); }
  }

  // --- Clock -----------------------------------------------------------------------------------
  var params = new URLSearchParams(location.search);
  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var T = P + (params.has("t") ? mod(parseFloat(params.get("t")) || 0) : reduce ? 23.9 : 0);
  var playing = !params.has("t") && !reduce, visible = true, prev = null;
  seek(T);
  if (params.has("tip")){ tip.style.transition = "none"; tipAt(params.get("tip")); }
  // Pausing has its own small button (WCAG 2.2.2), since a click on the scene pins.
  var pauseBtn = $("walk-pause");
  function label(){ pauseBtn.textContent = playing ? "Pause" : "Play"; }
  pauseBtn.addEventListener("click", function(){ playing = !playing; label(); });
  label();
  if ("IntersectionObserver" in window){
    new IntersectionObserver(function(es){ visible = es[0].isIntersecting; }).observe(svg);
  }
  function tick(ts){
    if (prev !== null && playing && visible){ T += Math.min(.1, (ts - prev) / 1000); step(T); }
    prev = ts;
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
})();
`;

// What each element of the scene is, in the fiction and in fact: the hover tooltips and the
// "What's what" glossary below the scene read from this one table, so keyboard, touch, and
// screen reader users get the same story as a mouse.
const WALK_TIPS: Record<string, [string, string]> = {
  you: ["You", "On an ordinary phone, even a borrowed one: here, a payphone on a farm. No app, internet connection, or screen needed at your end."],
  farm: ["The farm", "Far from any fibre line, and it does not matter. A voice call is enough to take part on the web: to build things and set agents to work, with no internet connection of your own."],
  twilio: ["Twilio · the phone network", "Carries your call to the walker. It is a plain voice call: no smartphone, app, or mobile data at your end."],
  voice: ["OpenAI GPT-Live · the walker's voice", "The voice you talk with. It listens, answers, and copes with interruptions, and passes questions about your projects to the walker's head."],
  head: ["Cloudflare · the walker's head", "The coordinator: it turns your requests into actions and keeps notes across calls. When you hang up, the dogs keep working, and keep using paid models."],
  walker: ["The walker", "Your go-between. It carries your instructions to the right dog and brings back what it hears. You choose; the dogs do the programming, and the walker reports it as theirs."],
  leash: ["The Agent API · a leash", "One connection to one coding agent. It carries your instructions to the dog and brings back updates, results, and questions."],
  pepper: ["Pepper · a coding agent", "Ask to follow it and you hear short updates as it reads, edits, and runs things, until you hang up. Following changes what you hear, not what it does."],
  juniper: ["Juniper · a coding agent", "Working without spoken updates. When it needs your permission, the walker brings the question to you."],
  gate: ["A permission request", "The walker reads you the agent's own choices, and you decide. Hanging up approves nothing; the request waits with the agent until someone answers it, here or from another screen."],
  biscuit: ["Biscuit · a coding agent", "Waiting for its next task. Send it home and its conversation is kept; ask for it back and it carries on where it left off."],
  home: ["Home", "Where a closed session rests. Its history stays in the park, though its turn has ended, so you can pick it up later, even on another call."],
  park: ["Daytona · the park", "A separate computer in the cloud where the agents work and your projects stay between calls. Their thinking happens at a model provider, so prompts do leave the park."],
  patch: ["A garden patch", "A project's files on the agents' computer (a Workspace). Dogs may share one; a patch is a place, not a fence."],
  notes: ["Your voice notes", "Recordings you left during a call, kept in a basket on the grass: tied pouches any dog can open. They stay in the park after you hang up."],
  log: ["What a dog leaves behind", "Yes, we are stretching the metaphor. Logs and output from running things: nobody keeps them, and the walk moves on."],
  artifact: ["A frisbee in the park", "A file one dog left for others: here, shared notes. It stays in the park after the dog that threw it goes home, and between calls."],
  path: ["The path", "The walker works from outside the agents' computer and never sets foot on the grass. It reaches in only by leash."],
  stream: ["The edge of the park", "The edge of the agents' computer. Your working files stay inside; websites, remote repositories, the phone network, and you are outside."],
  web: ["The open web", "Agents can read websites and send work to outside services. Some actions run without asking you; what succeeds depends on the credentials the park holds."],
  sun: ["The sun", "We're not sure what this represents. Try exploring other parts of the diagram."],
  mailbox: ["The mailbox", "Where work leaves the park: changes sent to the online home of your project (a git push). Sending it is not proof that it works."],
};

// The technical layer under each friendly tooltip, shown in the panel below the scene. The
// keys run in narrative order, which is also the keyboard's order through the scene.
const DOC = "https://github.com/rndmcnlly/dogwalk/blob/main/";
const WALK_TECH: Record<string, string> = {
  you: `The Opening begins with whether your carrier verified the caller ID. A first call registers with a speakable Invite Code; today your registered phone number is your identity, and calling back from a different handset is an open problem. Keypad keys work with no model at all: <code>1</code> status, <code>0</code> repeat, <code>#</code> stop talking, <code>*</code> system menu. <a href="${DOC}AGENTS.md">Accessibility ethos</a>`,
  farm: `The ethos the whole design answers to: put the complexity in the infrastructure, not on the caller. The minimum equipment is a voice line, from a payphone, a borrowed handset, or a satellite phone; richer clients are welcome but never required. <a href="${DOC}AGENTS.md">Accessibility ethos</a>`,
  twilio: `An ordinary PSTN call into <a href="https://www.twilio.com/voice">Twilio</a>. A Cloudflare Worker validates the signed webhook, answers with TwiML, and starts the sandbox without waiting for it: the conversation begins while the sandbox wakes. <a href="${DOC}PSTN_CLOUDFLARE.md">Deployed architecture</a>`,
  voice: `OpenAI GPT-Live, reached over secure SIP: the <em>Voice Frontend</em>. It has no session tools; questions about work become a <em>Handoff</em> by client delegation, and it speaks the answer in its own words. <a href="${DOC}DOMAIN.md">Domain language</a>`,
  head: `The <em>Session Orchestrator</em>: a Responses API tool loop in a <a href="https://developers.cloudflare.com/durable-objects/">Durable Object</a> keyed by your sandbox, calling neutral tools such as <code>follow_managed_session</code> and <code>resolve_permission</code> and rewriting its own <em>Memory</em> to fit its context. Between calls it records incoming events and acts on none.`,
  walker: `The <em>Voice Agent</em>: one character performed by the Voice Frontend and the Session Orchestrator. What happened is read from recorded Managed Session and Prompt Turn state, not from the model's recollection.`,
  leash: `<a href="https://opencode.ai/docs/api/">OpenCode's native API</a>, through Daytona's preview proxy, to a narrow gateway that keeps the sandbox's shared OpenCode service running. Down the leash: plain HTTP requests (prompts, steering, permission answers). Up: the service's live events over a WebSocket. Agent work lives in the service, so it outlives the leash. <a href="${DOC}SANDBOX_AGENT_GATEWAY.md">Agent Gateway</a>`,
  pepper: `A <em>Managed Session</em> (Alias "Pepper") wrapping one Agent Session in <a href="https://opencode.ai/">OpenCode</a>. As the <em>Followed Session</em>, its tool calls, plan steps, and remarks become spoken <em>Progress Beats</em>, built without a model: a beat never contains command text or tool names, and file names are dropped before it is spoken. The agents are asked to narrate themselves in one plain sentence per step; that is where Pepper's quotes come from.`,
  juniper: `An unfollowed session keeps working. During a call, a <em>Bid</em> (a permission request, a turn ending, a streak of failed tool calls) reaches the Voice Agent, which mentions it and offers to follow, but never switches on its own.`,
  gate: `<code>permission.asked</code>. Dogwalk presents the agent's own decisions (allow once, always allow what it would save, reject) and returns the one chosen. Pending requests live with the agent, not with Dogwalk: after a dropped connection they are read back, and one answered from another client (an OpenCode terminal, say) is dropped from what the walker presents.`,
  biscuit: `<em>Ready</em>: no Prompt Turn in progress. <code>close_managed_session</code> releases the Agent Session without deleting its history; <code>load_persisted_session</code> restores that history as a new Managed Session under a fresh Alias, which may be its old one.`,
  home: `Two stores, two lifecycles: OpenCode keeps Agent Session history in the durable sandbox filesystem, while the Durable Object keeps the Managed Session record. Closing is not deleting.`,
  park: `A durable <a href="https://www.daytona.io/">Daytona</a> Sandbox from an immutable snapshot, running the Agent Gateway and OpenCode. Dogwalk's policy: it auto-stops when idle and archives after long inactivity, with no automatic deletion. Model keys arrive as Daytona Secret placeholders.`,
  patch: `A <em>Workspace</em>: a directory in the sandbox where an Agent Session is rooted (its working directory). Sessions sharing one can edit the same files, so they can collide.`,
  notes: `<code>start_recording</code> captures your voice into <code>recordings/recording-&lt;time&gt;.wav</code> in the Workspace, voicemail style: <code>#</code> saves, <code>*</code> discards, and a long silence or the length limit also ends it. After that it is an ordinary file; what a dog does with it is an Assignment like any other.`,
  log: `Tool events stream up the leash (<code>session.tool.*</code>). Dogwalk keeps only the last few tool calls per session (kind, status, paths), never their output, and spoken beats never read it out. On disk, a command's output is whatever the agent chose to keep.`,
  artifact: `Files in the durable sandbox filesystem outlive Agent Sessions, turns, and calls: one session's output becomes another's input. Dogwalk manages sessions, not files; the walker knows about an artifact only through what an agent reports.`,
  path: `The Worker and Durable Object run on Cloudflare, outside the sandbox. They keep no copy of your project files and reach the agents only through the Agent Gateway.`,
  stream: `The sandbox separates its files, processes, and credentials from everything else, the walker included. It is not a network firewall: agents have outbound access, and model inference happens at the provider.`,
  web: `Network access is not write access: tools such as <code>fetch</code> and <code>execute</code> contact outside services, and credentials and service permissions decide what succeeds. Which actions ask first is the agent's configured policy, not something Dogwalk can guarantee.`,
  mailbox: `A push is an ordinary <code>execute</code> tool call (<code>git push</code>) using whatever credentials the sandbox holds. The walker reports what the agent says about it; it does not verify the result.`,
  sun: `Not every part of a diagram has to carry meaning. This one is here for warmth.`,
};
const WALK_ORDER = Object.keys(WALK_TECH);


export const LANDING_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Dogwalk is an eyes-free client for coding agents. Phone it from any phone and supervise several coding agents by voice.">
<meta name="color-scheme" content="light dark">
<title>Dogwalk: take your coding agents for a walk</title>
<style>
:root{
  --paper:#f4efe3;--card:#fbf8f1;--ink:#1b2a24;--body:#33413a;--muted:#56625a;--faint:#8a9188;
  --line:#ddd4c3;--rule:#c6bca7;--leash:#b2441f;--leash-soft:#f1dccf;--grass:#2f5d4a;
  --hill:#e4e3c8;--ground:#d3dcb8;--tree-a:#5f8f63;--tree-b:#86a96e;--trunk:#7a5a3a;
  --pole:#6f6a5c;--sun:#efc760;--cloud:#ffffff;
  --water:#9cc3c9;--concrete:#dedad0;--curb:#c2bcad;--joint:#cbc5b7;--soil:#b98e62;--town:#b9c4c6;--win:#f4efe3;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,"Cascadia Code",monospace;
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#111815;--card:#18211d;--ink:#ece6d7;--body:#cfd3c8;--muted:#a3ada3;--faint:#6f7a72;
    --line:#26322d;--rule:#34423b;--leash:#ec8a62;--leash-soft:#3a2219;--grass:#8cc2a6;
    --hill:#1a2420;--ground:#1f2d26;--tree-a:#2f5d4a;--tree-b:#3c6b50;--trunk:#5a4430;
    --pole:#8a9188;--sun:#d9d4c0;--cloud:#243038;
    --water:#2f5560;--concrete:#2e3330;--curb:#454c47;--joint:#3a403c;--soil:#5c4630;--town:#34423f;--win:#7d8b84;
  }
}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 var(--sans);-webkit-font-smoothing:antialiased}
a{color:inherit}
a:focus-visible,button:focus-visible{outline:3px solid var(--leash);outline-offset:3px;border-radius:4px}
.wrap{max-width:1080px;margin:0 auto;padding:0 clamp(18px,5vw,48px)}
.skip{position:absolute;left:-999px}
.skip:focus{left:12px;top:12px;background:var(--card);padding:8px 12px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}

/* masthead */
.top{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:22px 0;flex-wrap:wrap}
.mark{display:inline-flex;align-items:center;gap:10px;font-weight:700;letter-spacing:-.02em;text-decoration:none;font-size:19px}
.mark svg{flex:none}
.accent-fill{fill:var(--leash)}
.mark small{color:var(--muted);font-weight:500}
.top nav{display:flex;gap:18px;font:500 13px/1 var(--mono)}
.top nav a{text-decoration:none;color:var(--muted);padding:6px 0;border-bottom:1px solid transparent}
.top nav a:hover{color:var(--ink);border-color:var(--rule)}

/* hero */
.hero{padding:clamp(22px,4vw,40px) 0 clamp(34px,5vw,56px);border-top:1px solid var(--rule)}
h1{margin:0;font:600 clamp(32px,5vw,56px)/1.06 var(--serif);letter-spacing:-.02em;text-wrap:balance}
h1 em{font-style:italic;font-weight:500;color:var(--leash)}
.scene{margin:10px 0 0;font-size:clamp(16px,1.7vw,19px);color:var(--body)}

/* the walk */
.walk{margin:clamp(14px,3vw,28px) 0 0}
.walk svg{display:block;width:100%;height:auto}
[data-tip]{cursor:pointer}
.walk svg:focus{outline:none}
.walk svg:focus-visible{outline:3px solid var(--leash);outline-offset:4px;border-radius:6px}
.walk text{font-family:var(--sans)}
.sun{fill:var(--sun);opacity:.55}
.hill{fill:var(--hill)}
.ground{fill:var(--ground)}
.concrete{fill:var(--concrete)}
.halo{fill:var(--sun);opacity:0}
.rays path{stroke:var(--sun);stroke-width:5;stroke-linecap:round;opacity:.75}
@media (prefers-color-scheme:dark){.rays path{opacity:.25}}
.zzz{font:italic 700 13px var(--serif);fill:var(--muted)}
.tower-shadow{fill:var(--ink);opacity:.1}
.hit{fill:none;stroke:transparent;stroke-width:16;pointer-events:stroke}
.pad{fill:transparent}
.fglow{fill:none;stroke:var(--leash);stroke-width:2}
#fx,.labels,.shadow,#trees,#tufts,.bank,.air,#spot{pointer-events:none}
#logs .log{pointer-events:auto}
[data-tip].hot{filter:drop-shadow(0 0 .8px var(--leash)) drop-shadow(0 0 .8px var(--leash)) drop-shadow(0 0 3px var(--leash))}
.ground.hot{filter:none;fill:color-mix(in srgb,var(--ground) 84%,var(--leash))}
.concrete.hot{filter:none;fill:color-mix(in srgb,var(--concrete) 86%,var(--leash))}
.stream.hot{filter:none;stroke:color-mix(in srgb,var(--water) 65%,var(--leash))}
.leash.lit{stroke:var(--leash);stroke-width:3.6;stroke-dasharray:none}
.walk .labels{transition:opacity .15s}
.walk.tipping .labels{opacity:.35}
.hit.hot{filter:none}
.walk{position:relative}
.tip{position:absolute;z-index:2;max-width:290px;padding:11px 14px 12px;border-radius:12px;background:var(--card);border:1px solid var(--rule);box-shadow:0 10px 30px -12px rgba(20,30,25,.45);pointer-events:none;opacity:0;transform:translateY(4px);transition:opacity .15s,transform .15s}
.tip.on{opacity:1;transform:none}
.tip b{display:block;font:700 14px/1.3 var(--sans);color:var(--ink);margin-bottom:4px}
.tip p{margin:0;font:400 14px/1.45 var(--sans);color:var(--body)}
.stream{fill:none;stroke:var(--water);stroke-width:9;stroke-linecap:round}
.bank{fill:none;stroke:var(--tree-a);stroke-width:2;opacity:.5}
.curb{fill:none;stroke:var(--curb);stroke-width:4}
.joint{fill:none;stroke:var(--joint);stroke-width:2}
.patch ellipse{fill:var(--soil);opacity:.55}
.sprout{fill:none;stroke:var(--tree-a);stroke-width:1.8;stroke-linecap:round}
.town rect,.town path{fill:var(--town)}
.town .win rect{fill:var(--win)}
#cloud{transition:opacity .4s}
.flight{fill:none;stroke:var(--grass);stroke-width:2;stroke-dasharray:1 7;stroke-linecap:round}
.stick{stroke:#7a5a3a;stroke-width:5;stroke-linecap:round}
.page rect{fill:#fffaf0;stroke:var(--rule)}
.page path{stroke:var(--faint);stroke-width:1}
.parcel rect{fill:#c9a46a}
.dropped rect{fill:#f6efdc}
.parcel path{stroke:#7a5a3a;stroke-width:1.2}
.tree-a{fill:var(--tree-a)}
.tree-b{fill:var(--tree-b)}
.trunk{fill:var(--trunk)}
.tuft{fill:none;stroke:var(--tree-a);stroke-width:1.6;stroke-linejoin:round}
.farmrow{fill:none;stroke:var(--tree-b);stroke-width:2;opacity:.6}
.pole,.wire{fill:none;stroke:var(--pole);stroke-width:2}
.wire{stroke-width:1.2}
.tower line,.tower polyline{fill:none;stroke:var(--pole);stroke-width:3.4;stroke-linejoin:round;stroke-linecap:round}
.tower-fill{fill:var(--pole)}
.beacon{fill:var(--leash)}
.air{fill:none;stroke:var(--grass);stroke-width:2.2;stroke-dasharray:1 7;stroke-linecap:round}
#booth-glass{fill:#d9c27a}
#booth-glass.lit{fill:#f7d774}
#booth-glass.talk{fill:#fff3b8}
.leash{fill:none;stroke:var(--ink);stroke-width:2.2;stroke-linecap:round}
.leash.far{stroke:var(--muted);stroke-dasharray:4 6}
.leash.hot{stroke:var(--leash);stroke-width:3}
.leash.pull{stroke:var(--leash);stroke-width:2.6}
.spot{fill:var(--leash-soft)}
.shadow ellipse{fill:var(--ink);opacity:.13}
@media (prefers-color-scheme:dark){.shadow ellipse{fill:#000;opacity:.28}}
.cloud-edge *{fill:var(--line);stroke:var(--line);stroke-width:5}
@media (prefers-color-scheme:dark){.cloud-edge *{fill:#4d606e;stroke:#4d606e;stroke-width:3}.sun{opacity:.7}.halo{opacity:.12}.spot{fill:rgba(236,138,98,.18)}}
.cloud-fill *{fill:var(--cloud)}
.think{font:700 14px var(--mono);fill:var(--ink)}
.think.idle{fill:var(--faint);font-weight:400}
.think.gloss{font:650 16px var(--sans)}
.think2{font:500 11px var(--mono);fill:var(--leash)}
.lbl{font:500 12px var(--mono);fill:var(--muted)}
.lbl-bg{fill:var(--paper);stroke:var(--rule);stroke-dasharray:2 3;opacity:.9}
.lbl .b{fill:var(--ink);font-weight:700}
.you-lbl{fill:var(--leash);font-weight:700}
.leader{fill:none;stroke:var(--faint);stroke-width:1}
.tag{font:700 13px var(--sans);fill:var(--ink);paint-order:stroke;stroke:var(--concrete);stroke-width:4px;stroke-linejoin:round}
.tag .st{font:500 11.5px var(--mono);fill:#9a3818}
@media (prefers-color-scheme:dark){.tag .st{fill:var(--leash)}}
.bubble text{font:600 15px var(--sans);fill:#fffaf0}
.bubble.you rect,.tail.you,.dot.you{fill:var(--leash)}
.bubble.walker rect,.tail.walker,.dot.walker{fill:var(--grass)}
.dot.msg{fill:var(--leash);stroke:var(--card);stroke-width:2.5}
.dot.you,.dot.walker{stroke:var(--card);stroke-width:2}
@media (prefers-color-scheme:dark){.bubble text{fill:#111815}}
.chip rect{fill:var(--card);stroke:var(--rule)}
.chip text{font:500 12.5px var(--mono);fill:var(--ink)}
.chip .k{font-weight:700;fill:var(--leash)}
.chip.faint .k,.chip.faint text{fill:var(--muted)}
.chip.faint rect{stroke:var(--line)}
.remark rect{fill:var(--card);stroke:var(--leash);stroke-width:1.5}
.remark text{font:italic 500 15px var(--serif);fill:var(--ink)}
.tail.remark{fill:var(--card);stroke:var(--leash);stroke-width:1.5}
.ripple{fill:none;stroke:var(--grass);stroke-width:2}
.card-bg{fill:var(--card);stroke:var(--leash);stroke-width:1.5}
.card-t{font:700 14px var(--sans);fill:var(--ink)}
.opt rect{fill:var(--paper);stroke:var(--rule)}
.opt text{font:500 11.5px var(--mono);fill:var(--ink)}
.opt.on rect{fill:var(--leash);stroke:var(--leash)}
.opt.on text{fill:var(--paper);font-weight:700}
.walk-bar{display:flex;align-items:center;justify-content:space-between;gap:10px 16px;flex-wrap:wrap;margin-top:8px;font:500 13px/1.4 var(--mono);color:var(--muted)}
#walk-pause{flex:none;min-width:5.4rem;text-align:center;font:500 12.5px/1 var(--mono);background:none;border:1px solid var(--rule);border-radius:999px;padding:6px 12px;color:var(--muted);cursor:pointer}
#walk-pause:hover{color:var(--ink);border-color:var(--ink)}
.tech{margin:12px 0 0;padding:14px 18px;min-height:7.4em;border-radius:14px;background:var(--card);border:1px solid var(--line);font:400 14px/1.55 var(--sans);color:var(--body)}
.tech.pinned{border-color:var(--leash)}
.tech-top{display:flex;justify-content:space-between;align-items:center;gap:12px}
#unpin{font:600 11px/1 var(--mono);color:var(--paper);background:var(--leash);border:0;border-radius:999px;padding:4px 9px;cursor:pointer}
.tech.pinned{box-shadow:inset 3px 0 0 var(--leash)}
.tech-h{margin:0 0 4px;font:600 12px/1.4 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.tech.pinned .tech-h{color:var(--leash)}
.tech-b{margin:0}
.tech .friendly{display:block;margin-bottom:6px;color:var(--ink)}
.tech code{font:500 12.5px var(--mono);background:var(--paper);border:1px solid var(--line);border-radius:5px;padding:1px 5px}
.tech a{color:var(--ink);text-underline-offset:3px;text-decoration-color:var(--leash)}
.tech em{font-style:normal;font-weight:650;color:var(--ink)}
[data-tip]:focus{outline:none}
.walk-cap{margin:0;min-height:1.4em;font:600 15px/1.4 var(--sans);color:var(--ink)}
.walk-cap b{font:700 11px/1 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--leash);margin-right:8px}
@media(max-width:640px){.labels .lbl:not(.you-lbl),.lbl-bg{display:none}.tag{font-size:19px}.tag .st{font-size:16px}.you-lbl{font-size:19px}}
@media (prefers-color-scheme:dark){#walker,#biscuit,#juniper{filter:drop-shadow(0 0 1px rgba(236,230,215,.3))}#walker,#pepper{filter:brightness(1.22) drop-shadow(0 0 1px rgba(236,230,215,.3))}.chip.faint .k,.chip.faint text{fill:var(--muted)}.leash{stroke:#8f9a92}.farm,#mailbox{filter:brightness(.62) saturate(.8)}#walker.hot,#pepper.hot,#juniper.hot,#biscuit.hot{filter:drop-shadow(0 0 1px var(--leash)) drop-shadow(0 0 1px var(--leash)) drop-shadow(0 0 4px var(--leash))}.patch ellipse{opacity:.95}}

footer.site{display:flex;justify-content:space-between;gap:16px 32px;flex-wrap:wrap;padding:28px 0 56px;border-top:1px solid var(--rule);font:500 12.5px/1.7 var(--mono);color:var(--muted)}
footer.site a{text-underline-offset:3px;text-decoration-color:var(--rule)}


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
    <a href="https://github.com/rndmcnlly/dogwalk">Source</a>
  </nav>
</header>

<main id="main">

<div class="hero">
  <h1>Take your coding agents <em>for a walk.</em></h1>
  <p class="scene">Call from any phone. Ask a pack of AI coding agents to build and fix your software, and hear how they're doing.</p>

  <figure class="walk">
    ${WALK_SVG}
    <div class="walk-bar">
      <p class="walk-cap" aria-hidden="true"><b id="cap-who"></b><span id="cap-text"></span></p>
      <button type="button" id="walk-pause">Pause</button>
    </div>
        <section class="tech" id="tech">
      <div class="tech-top"><p class="tech-h" id="tech-h">Under the hood</p><button type="button" id="unpin" hidden>Pinned · unpin</button></div>
      <p class="tech-b" id="tech-b">Point at anything in the scene, or tab to it, for the technical story. Click or press Enter to pin it, so you can copy terms and follow links.</p>
    </section>
    <div class="tip" id="tip" role="tooltip"><b id="tip-t"></b><p id="tip-p"></p></div>
    <figcaption class="sr">
      <p>An illustrated cycle that repeats. You walk from a barn to a phone booth on a distant farm, far from any internet connection, and call; the call crosses the phone network (Twilio) to a dog walker, whose voice is OpenAI GPT-Live and whose thinking (the Session Orchestrator) runs on Cloudflare. The walker keeps to a concrete path outside a park, your Daytona sandbox, holding leashes (connections to the coding agents) to three dogs, each an OpenCode coding session, on the grass. Across a stream at the park's edge lie the open web and a mailbox.</p>
      <ol>
        <li>Throughout, whenever a dog runs something it leaves a small heap of logs behind, which drifts off with the grass as the walk moves on.</li>
        <li>Between calls, Juniper reached a gate: it wants permission to run a migration. The walker only noted it.</li>
        <li>You dial; the walker takes the phone from a pocket. Walker: Verified. Dogwalk. Since then, Juniper asked for permission. Where to?</li>
        <li>You: What does Juniper want? Walker: To run the migration. Allow once, always allow, or reject?</li>
        <li>You: Allow once. And let Biscuit go home. A message runs down Juniper's leash, the gate opens, and Juniper carries on. Biscuit, a corgi at heel, has its leash unclipped and hops into a passing kennel, which rolls away with it asleep inside, to come round again.</li>
        <li>You: What's Pepper up to? The walker follows Pepper, a dachshund, turning toward it. Walker: Pepper's reading the parser. Pepper remarks that it found an off-by-one.</li>
        <li>You: Bring Biscuit back. The walker finds Biscuit's old session; as the kennel comes round, a leash reaches its door and Biscuit steps out on it.</li>
        <li>Pepper runs the tests, tosses a parcel over the stream into the mailbox (a git push), and digs up a bone. Walker: Biscuit's back. Pepper reports tests pass, fix pushed.</li>
        <li>You: Good dog. I'll describe the next one. Walker: Recording after the beep. Pound when you're done. You: It fails near midnight, every time, and press pound. Your note drops into a basket on the grass as a tied pouch. Pepper drops its bone, opens the pouch, then picks up a frisbee Juniper left in the park earlier: shared notes, files that outlive the sessions around them. Walker: Saved, and Pepper's on it.</li>
        <li>You: Bye! Walker: Bye. You hang up and walk home; the walker pockets the phone and stops following. Juniper works on until it reaches the next gate.</li>
      </ol>
    </figcaption>
  </figure>

</div>

</main>

<footer class="site">
  <span>An open-source proof of concept, run by one operator.<br>Access is invite-only: a first call registers with an Invite Code.</span>
  <span><a href="https://github.com/rndmcnlly/dogwalk">github.com/rndmcnlly/dogwalk</a><br><a href="/pair">Pair a browser</a></span>
</footer>

</div>
<script>window.WALK_TIPS = ${JSON.stringify(WALK_TIPS)};window.WALK_TECH = ${JSON.stringify(WALK_TECH)};window.WALK_ORDER = ${JSON.stringify(WALK_ORDER)};${WALK_SCRIPT}</script>
</body></html>`;
