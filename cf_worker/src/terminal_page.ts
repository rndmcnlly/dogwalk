// Human Takeover terminal page (DOMAIN.md), served at GET /pair/terminal and opened by a Paired
// Browser in a new full-screen tab. It is an xterm.js client for the TerminalRelay Durable Object,
// which relays bytes to a sandbox PTY running "tmux new -A -s human", so a reconnect redraws the
// screen. Wire protocol, over GET /pair/terminal/ws?for=<identity>:
//
//   client text   {"type":"auth","ticket"|"token":"<identity>.<secret>","cols":c,"rows":r}, then
//                 {"type":"resize","cols":c,"rows":r} (debounced) and {"type":"ping"} every 20 s
//   client binary keystrokes (term.onData as UTF-8, term.onBinary as raw bytes)
//   server text   {"type":"ready","token":...,"expires_at":ms} | {"type":"error","error":...}
//                 | {"type":"exit","code":n,"reason":...} | {"type":"notice","text":...} | {"type":"pong"}
//   server binary raw PTY output (UTF-8 may split across frames; xterm reassembles)
//   close 4001    ticket or token expired or invalid; 4003 the browser was unpaired; else reconnect
//
// The launch ticket arrives once in the URL fragment (#t=...) and is stripped at once; the token
// from "ready" lives in sessionStorage so a reload of this tab reconnects without a ticket.
//
// Vendored xterm.js builds and this page's script are served by handleTerminalAssets from
// /pair/assets/<name>.<hash>.<ext>, where the hash covers the file's contents, so any change busts
// caches and every asset can be immutable. Keeping the script out of the HTML lets the page run
// under a CSP without 'unsafe-inline' for scripts. Suggested headers for GET /pair/terminal:
//
//   Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline';
//     connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none';
//     form-action 'none'; frame-ancestors 'none'
//   Cache-Control: no-store
//   Referrer-Policy: no-referrer
//   X-Content-Type-Options: nosniff
//
// style-src needs 'unsafe-inline' because xterm.js injects <style> elements for its theme and
// renderer. connect-src 'self' covers the same-origin wss: URL in current browsers.
//
// String.raw keeps backslashes literal (the script's "\x1b" escapes reach the browser as written),
// so neither the page nor the script may contain a backtick or a dollar-brace sequence.

import xtermJs from "../vendor/xterm/xterm.js.txt";
import xtermCss from "../vendor/xterm/xterm.css.txt";
import fitJs from "../vendor/xterm/addon-fit.js.txt";
import webglJs from "../vendor/xterm/addon-webgl.js.txt";
import unicode11Js from "../vendor/xterm/addon-unicode11.js.txt";
import webLinksJs from "../vendor/xterm/addon-web-links.js.txt";
import clipboardJs from "../vendor/xterm/addon-clipboard.js.txt";
import xtermLicense from "../vendor/xterm/LICENSE.txt";

const ASSET_PREFIX = "/pair/assets/";
const JS_TYPE = "text/javascript; charset=utf-8";
const CSS_TYPE = "text/css; charset=utf-8";
const TEXT_TYPE = "text/plain; charset=utf-8";

const TERMINAL_JS = String.raw`"use strict";
(function () {
  var TOKEN_KEY = "dogwalk.terminal.token";
  var FONT_KEY = "dogwalk.terminal.fontSize";
  var KEYS_KEY = "dogwalk.terminal.keys";
  var AUTOCOPY_KEY = "dogwalk.terminal.copyOnSelect";
  var CRED = /^[0-9a-f]+\.[0-9a-f]+$/;
  var FONT_MIN = 8, FONT_MAX = 32;

  function $(id) { return document.getElementById(id); }
  function sget(store, key) { try { return store.getItem(key); } catch (e) { return null; } }
  function sset(store, key, value) { try { store.setItem(key, value); } catch (e) {} }
  function sdel(store, key) { try { store.removeItem(key); } catch (e) {} }

  // Credentials: a one-time launch ticket from the fragment, else the token from "ready".
  var ticket = null;
  var frag = /(?:^#|&)t=([^&]*)/.exec(location.hash);
  if (frag) {
    var cand = decodeURIComponent(frag[1]).toLowerCase();
    if (CRED.test(cand)) ticket = cand;
  }
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  // A new ticket sent to this same tab changes only the fragment, which does not reload the page.
  window.addEventListener("hashchange", function () {
    if (/(?:^#|&)t=/.test(location.hash)) location.reload();
  });
  function savedToken() {
    var v = sget(sessionStorage, TOKEN_KEY);
    return v && CRED.test(v) ? v : null;
  }

  // Terminal
  var coarse = matchMedia("(pointer: coarse)").matches;
  var shortSide = Math.min(screen.width, screen.height);
  var defaultFont = coarse ? (shortSide >= 600 ? 16 : 13) : 14;
  function clampFont(n) { return Math.max(FONT_MIN, Math.min(FONT_MAX, n)); }
  var fontSize = clampFont(parseInt(sget(localStorage, FONT_KEY), 10) || defaultFont);

  var term = new Terminal({
    allowProposedApi: true,
    fontFamily: 'ui-monospace, Menlo, "DejaVu Sans Mono", monospace',
    fontSize: fontSize,
    cursorBlink: true,
    macOptionIsMeta: true,
    scrollback: 5000,
    theme: {
      background: "#111815", foreground: "#dcdcd0",
      cursor: "#ec8a62", cursorAccent: "#111815",
      selectionBackground: "rgba(236,138,98,0.32)", selectionInactiveBackground: "rgba(236,138,98,0.18)",
      scrollbarSliderBackground: "rgba(163,173,163,0.22)",
      scrollbarSliderHoverBackground: "rgba(163,173,163,0.38)",
      scrollbarSliderActiveBackground: "rgba(163,173,163,0.5)",
      black: "#26322d", red: "#ff8f85", green: "#74c69d", yellow: "#e3b45a",
      blue: "#8cb9e6", magenta: "#bba6e8", cyan: "#8cc2a6", white: "#cfd3c8",
      brightBlack: "#6f7a72", brightRed: "#ffb3ab", brightGreen: "#9ee0bd", brightYellow: "#f2cd85",
      brightBlue: "#b4d3f2", brightMagenta: "#d6c8f5", brightCyan: "#b4dcc7", brightWhite: "#f4efe3"
    }
  });
  var fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.loadAddon(new Unicode11Addon.Unicode11Addon());
  term.unicode.activeVersion = "11";
  term.loadAddon(new WebLinksAddon.WebLinksAddon(function (ev, uri) {
    window.open(uri, "_blank", "noopener,noreferrer");
  }));
  // OSC 52 may write the clipboard; reads are refused so sandbox programs cannot see it.
  term.loadAddon(new ClipboardAddon.ClipboardAddon(undefined, {
    readText: function () { return ""; },
    writeText: function (sel, text) { return copyText(text, "Copied from terminal"); }
  }));
  term.open($("term"));

  function setRenderer(name) { document.body.dataset.renderer = name; }
  setRenderer("dom");
  try {
    var webgl = new WebglAddon.WebglAddon();
    webgl.onContextLoss(function () { webgl.dispose(); setRenderer("dom"); });
    term.loadAddon(webgl);
    setRenderer("webgl");
  } catch (e) {
    setRenderer("dom");
  }

  // Layout: follow the visual viewport so the key bar rides above a soft keyboard.
  var root = document.documentElement;
  function syncViewport() {
    var vv = window.visualViewport;
    if (!vv) return;
    root.style.setProperty("--vvh", vv.height + "px");
    root.style.setProperty("--vvt", vv.offsetTop + "px");
  }
  if (window.visualViewport) {
    visualViewport.addEventListener("resize", syncViewport);
    visualViewport.addEventListener("scroll", syncViewport);
    syncViewport();
  }
  var fitFrame = 0;
  function refit() {
    cancelAnimationFrame(fitFrame);
    fitFrame = requestAnimationFrame(function () { try { fit.fit(); } catch (e) {} });
  }
  try { fit.fit(); } catch (e) {}
  new ResizeObserver(refit).observe($("term"));
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(refit);

  var lastSize = "";
  var resizeTimer = 0;
  function sendResize() {
    var size = term.cols + "x" + term.rows;
    if (!ready || size === lastSize) return;
    lastSize = size;
    sendJson({ type: "resize", cols: term.cols, rows: term.rows });
  }
  term.onResize(function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(sendResize, 150);
  });

  // Status, top bar, banner, overlay
  var barTimer = 0;
  function setState(state, label) {
    document.body.dataset.state = state;
    $("status").textContent = label;
    if (state === "live") hideBarSoon(); else showBar(false);
  }
  function showBar(autoHide) {
    document.body.classList.add("bar");
    clearTimeout(barTimer);
    if (autoHide) hideBarSoon();
  }
  function hideBarSoon() {
    clearTimeout(barTimer);
    barTimer = setTimeout(function () {
      if (document.body.dataset.state === "live" && !$("bar").matches(":hover")) {
        document.body.classList.remove("bar");
      } else if (document.body.dataset.state === "live") {
        hideBarSoon();
      }
    }, 2500);
  }
  window.addEventListener("mousemove", function (ev) { if (ev.clientY < 24) showBar(true); });
  $("pill").addEventListener("click", function () { showBar(true); });
  $("bar").addEventListener("pointerdown", function () { showBar(true); });

  var bannerTimer = 0;
  function banner(text, ms) {
    var b = $("banner");
    b.textContent = text;
    b.hidden = false;
    clearTimeout(bannerTimer);
    if (ms) bannerTimer = setTimeout(function () { b.hidden = true; }, ms);
  }
  function hideBanner() { clearTimeout(bannerTimer); $("banner").hidden = true; }

  function overlay(kind, title, text, button) {
    var o = $("overlay");
    o.dataset.kind = kind;
    $("ovTitle").textContent = title;
    $("ovText").textContent = text;
    $("ovBtn").hidden = !button;
    if (button) $("ovBtn").textContent = button;
    $("ovLink").hidden = kind !== "fatal";
    o.hidden = false;
  }
  function hideOverlay() { $("overlay").hidden = true; }

  // Connection
  var ws = null, ready = false, exited = false, finished = false;
  var pendingError = null, backoff = 500, retryTimer = 0, pingTimer = 0, lastPong = 0;
  var enc = new TextEncoder();

  function sendJson(msg) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg)); }
  function sendBytes(bytes) { if (ws && ready && ws.readyState === 1) ws.send(bytes); }

  function startPing() {
    clearInterval(pingTimer);
    lastPong = Date.now();
    pingTimer = setInterval(function () {
      if (Date.now() - lastPong > 50000) { drop(); return; }
      sendJson({ type: "ping" });
    }, 20000);
  }
  function stopPing() { clearInterval(pingTimer); }

  // Abandon the current socket without waiting for its close event (a dead network may never send one).
  function drop() {
    var sock = ws;
    ws = null;
    ready = false;
    stopPing();
    if (sock) try { sock.close(); } catch (e) {}
    scheduleReconnect();
  }

  function connect() {
    clearTimeout(retryTimer);
    retryTimer = 0;
    if (finished) return;
    var cred = ticket || savedToken();
    if (!cred) { fatal("expired"); return; }
    var useTicket = !!ticket;
    var identity = cred.split(".")[0];
    var old = ws;
    ws = null;
    if (old) try { old.close(); } catch (e) {}
    ready = false;
    exited = false;
    pendingError = null;
    if (document.body.dataset.state !== "retry") setState("connecting", "Connecting");
    var url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host +
      "/pair/terminal/ws?for=" + encodeURIComponent(identity);
    var sock;
    try { sock = new WebSocket(url); } catch (e) { scheduleReconnect(); return; }
    sock.binaryType = "arraybuffer";
    ws = sock;
    sock.onopen = function () {
      if (ws !== sock) return;
      try { fit.fit(); } catch (e) {}
      var auth = { type: "auth", cols: term.cols, rows: term.rows };
      if (useTicket) { auth.ticket = cred; ticket = null; } else { auth.token = cred; }
      sock.send(JSON.stringify(auth));
      lastSize = term.cols + "x" + term.rows;
    };
    sock.onmessage = function (ev) {
      if (ws !== sock) return;
      if (typeof ev.data !== "string") { term.write(new Uint8Array(ev.data)); return; }
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === "ready") {
        if (typeof msg.token === "string" && CRED.test(msg.token)) sset(sessionStorage, TOKEN_KEY, msg.token);
        ready = true;
        backoff = 500;
        hideOverlay();
        hideBanner();
        setState("live", "Connected");
        startPing();
        sendResize();
        term.focus();
      } else if (msg.type === "error") {
        pendingError = String(msg.error || "error");
      } else if (msg.type === "exit") {
        sessionEnded(msg);
      } else if (msg.type === "notice") {
        banner(String(msg.text || ""), 4000);
      } else if (msg.type === "pong") {
        lastPong = Date.now();
      }
    };
    sock.onclose = function (ev) {
      if (ws !== sock) return;
      ws = null;
      ready = false;
      stopPing();
      if (ev.code === 4001) { fatal("expired"); return; }
      if (ev.code === 4003) { fatal("unpaired"); return; }
      if (exited) return;
      scheduleReconnect();
    };
  }

  function scheduleReconnect() {
    if (finished || exited || retryTimer) return;
    if (!ticket && !savedToken()) { fatal("expired"); return; }
    setState("retry", "Reconnecting...");
    banner("Reconnecting...");
    retryTimer = setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 10000);
  }
  function reconnectNow() {
    if (retryTimer) { backoff = 500; connect(); }
  }
  window.addEventListener("online", reconnectNow);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") reconnectNow();
  });

  function sessionEnded(msg) {
    exited = true;
    ready = false;
    stopPing();
    setState("ended", "Session ended");
    var detail = typeof msg.code === "number" ? "Exit code " + msg.code : "";
    if (msg.reason) detail += (detail ? ": " : "") + msg.reason;
    overlay("exit", "Session ended. Press Enter for a new shell", detail, "New shell");
  }
  function newShell() {
    if (!exited || finished) return;
    hideOverlay();
    term.reset();
    backoff = 500;
    connect();
    term.focus();
  }

  function fatal(kind) {
    finished = true;
    sdel(sessionStorage, TOKEN_KEY);
    ticket = null;
    clearTimeout(retryTimer);
    stopPing();
    var sock = ws;
    ws = null;
    ready = false;
    if (sock) try { sock.close(); } catch (e) {}
    term.options.disableStdin = true;
    term.blur();
    hideBanner();
    if (kind === "unpaired") {
      setState("fatal", "Pairing ended");
      overlay("fatal", "This pairing was ended.", "Pair this browser again from the pairing page.", "");
    } else {
      setState("fatal", "Link expired");
      overlay("fatal", "This terminal link has expired.", "Open a new one from the pairing page.", "");
    }
    document.body.classList.add("finished");
  }

  // Input, with one-shot Ctrl and Alt latches from the key bar
  var ctrlLatch = false, altLatch = false;
  function setLatches(ctrl, alt) {
    ctrlLatch = ctrl;
    altLatch = alt;
    $("kCtrl").setAttribute("aria-pressed", String(ctrl));
    $("kAlt").setAttribute("aria-pressed", String(alt));
  }
  var CTRL_PUNCT = { " ": "\x00", "2": "\x00", "?": "\x7f", "/": "\x1f", "-": "\x1f", "~": "\x1e", "|": "\x1c" };
  function ctrlChar(c) {
    var lower = c.toLowerCase();
    if (lower >= "a" && lower <= "z") return String.fromCharCode(lower.charCodeAt(0) - 96);
    if (c >= "@" && c <= "_") return String.fromCharCode(c.charCodeAt(0) - 64);
    return CTRL_PUNCT[c] || c;
  }
  function applyLatches(data) {
    if (!ctrlLatch && !altLatch) return data;
    var out = data;
    if (ctrlLatch && data.length === 1) out = ctrlChar(data);
    if (altLatch) out = "\x1b" + out;
    setLatches(false, false);
    return out;
  }

  term.onData(function (data) {
    if (exited) {
      if (data === "\r" || data === "\n") newShell();
      return;
    }
    sendBytes(enc.encode(applyLatches(data)));
  });
  term.onBinary(function (data) {
    var bytes = new Uint8Array(data.length);
    for (var i = 0; i < data.length; i++) bytes[i] = data.charCodeAt(i) & 255;
    sendBytes(bytes);
  });
  window.addEventListener("keydown", function (ev) {
    if (exited && ev.key === "Enter" && !$("overlay").hidden && document.activeElement !== $("ovBtn")) {
      ev.preventDefault();
      newShell();
    }
  });
  $("ovBtn").addEventListener("click", newShell);

  // Key bar: cursor-style keys honor application cursor mode; modifiers use xterm's CSI 1;m form.
  function cursorKey(final) {
    var mod = 1 + (altLatch ? 2 : 0) + (ctrlLatch ? 4 : 0);
    setLatches(false, false);
    if (mod > 1) return "\x1b[1;" + mod + final;
    return (term.modes.applicationCursorKeysMode ? "\x1bO" : "\x1b[") + final;
  }
  function tildeKey(n) {
    var mod = 1 + (altLatch ? 2 : 0) + (ctrlLatch ? 4 : 0);
    setLatches(false, false);
    return "\x1b[" + n + (mod > 1 ? ";" + mod : "") + "~";
  }
  var KEYS = {
    esc: function () { var s = altLatch ? "\x1b\x1b" : "\x1b"; setLatches(false, false); return s; },
    tab: function () { var s = altLatch ? "\x1b\t" : "\t"; setLatches(false, false); return s; },
    up: function () { return cursorKey("A"); },
    down: function () { return cursorKey("B"); },
    right: function () { return cursorKey("C"); },
    left: function () { return cursorKey("D"); },
    home: function () { return cursorKey("H"); },
    end: function () { return cursorKey("F"); },
    pgup: function () { return tildeKey(5); },
    pgdn: function () { return tildeKey(6); }
  };
  var repeatDelay = 0, repeatTimer = 0;
  function stopRepeat() { clearTimeout(repeatDelay); clearInterval(repeatTimer); }
  function pressKey(btn) {
    var k = btn.dataset.k;
    if (k === "ctrl") { setLatches(!ctrlLatch, altLatch); return; }
    if (k === "alt") { setLatches(ctrlLatch, !altLatch); return; }
    if (k === "paste") { pasteClipboard(); return; }
    if (exited) return;
    var data = KEYS[k] ? KEYS[k]() : applyLatches(btn.dataset.ch);
    sendBytes(enc.encode(data));
  }
  $("keys").addEventListener("pointerdown", function (ev) {
    var btn = ev.target.closest("button");
    if (!btn) return;
    ev.preventDefault();
    stopRepeat();
    // Clipboard reads need user activation, which a touch grants only on pointerup.
    if (btn.dataset.k === "paste") return;
    pressKey(btn);
    if (btn.dataset.repeat) {
      repeatDelay = setTimeout(function () {
        repeatTimer = setInterval(function () { pressKey(btn); }, 70);
      }, 400);
    }
  });
  $("keys").addEventListener("pointerup", function (ev) {
    var btn = ev.target.closest("button");
    if (btn && btn.dataset.k === "paste") pressKey(btn);
  });
  ["pointerup", "pointercancel", "pointerleave"].forEach(function (t) { $("keys").addEventListener(t, stopRepeat); });
  $("keys").addEventListener("touchstart", function (ev) { ev.preventDefault(); }, { passive: false });

  var keysOn = sget(localStorage, KEYS_KEY);
  function setKeys(on) {
    $("keys").hidden = !on;
    $("keysBtn").setAttribute("aria-pressed", String(on));
    refit();
  }
  setKeys(keysOn === null ? coarse : keysOn === "1");
  $("keysBtn").addEventListener("click", function () {
    var on = $("keys").hidden;
    sset(localStorage, KEYS_KEY, on ? "1" : "0");
    setKeys(on);
    term.focus();
  });

  // Font size
  function setFont(n) {
    fontSize = clampFont(n);
    term.options.fontSize = fontSize;
    sset(localStorage, FONT_KEY, String(fontSize));
    $("fontVal").textContent = String(fontSize);
    refit();
  }
  $("fontVal").textContent = String(fontSize);
  $("fontDown").addEventListener("click", function () { setFont(fontSize - 1); });
  $("fontUp").addEventListener("click", function () { setFont(fontSize + 1); });

  // Clipboard
  function copyText(text, note) {
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) {}
      ta.remove();
      banner(ok ? note : "Copy was blocked by the browser", 1800);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () { banner(note, 1500); }, fallback);
    }
    fallback();
    return Promise.resolve();
  }
  function screenText() {
    var buf = term.buffer.active, lines = [];
    for (var y = buf.viewportY; y < buf.viewportY + term.rows; y++) {
      var line = buf.getLine(y);
      lines.push(line ? line.translateToString(true) : "");
    }
    while (lines.length && !lines[lines.length - 1]) lines.pop();
    return lines.join("\n");
  }
  $("copy").addEventListener("click", function () {
    if (term.hasSelection()) copyText(term.getSelection(), "Copied selection");
    else copyText(screenText(), "Copied screen");
    term.focus();
  });
  var autoCopy = sget(localStorage, AUTOCOPY_KEY) === "1";
  $("autocopy").setAttribute("aria-pressed", String(autoCopy));
  $("autocopy").addEventListener("click", function () {
    autoCopy = !autoCopy;
    sset(localStorage, AUTOCOPY_KEY, autoCopy ? "1" : "0");
    $("autocopy").setAttribute("aria-pressed", String(autoCopy));
    term.focus();
  });
  var selTimer = 0;
  term.onSelectionChange(function () {
    if (!autoCopy) return;
    clearTimeout(selTimer);
    selTimer = setTimeout(function () {
      if (term.hasSelection()) copyText(term.getSelection(), "Copied selection");
    }, 300);
  });
  function pasteClipboard() {
    if (!navigator.clipboard || !navigator.clipboard.readText) { banner("Paste with the keyboard instead", 2000); return; }
    navigator.clipboard.readText().then(function (text) {
      if (text && !exited) term.paste(text);
    }, function () { banner("Paste was blocked by the browser", 2000); });
  }

  // Debug handle for the diagnostic harness; carries no credentials.
  window.dogwalkTerminal = {
    term: term,
    get state() { return { state: document.body.dataset.state, ready: ready, exited: exited, renderer: document.body.dataset.renderer, backoff: backoff }; }
  };

  showBar(false);
  connect();
  term.focus();
})();
`;

// FNV-1a over UTF-16 code units: a cheap synchronous content hash for cache-busting names.
function contentHash(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, "0");
}

interface TerminalAsset {
  name: string;
  body: string;
  type: string;
}

function asset(base: string, ext: string, body: string, type: string): TerminalAsset {
  return { name: `${base}.${contentHash(body)}${ext}`, body, type };
}

const ASSETS = {
  xtermCss: asset("xterm", ".css", xtermCss, CSS_TYPE),
  xterm: asset("xterm", ".js", xtermJs, JS_TYPE),
  fit: asset("addon-fit", ".js", fitJs, JS_TYPE),
  webgl: asset("addon-webgl", ".js", webglJs, JS_TYPE),
  unicode11: asset("addon-unicode11", ".js", unicode11Js, JS_TYPE),
  webLinks: asset("addon-web-links", ".js", webLinksJs, JS_TYPE),
  clipboard: asset("addon-clipboard", ".js", clipboardJs, JS_TYPE),
  page: asset("terminal", ".js", TERMINAL_JS, JS_TYPE),
  license: asset("xterm-LICENSE", ".txt", xtermLicense, TEXT_TYPE),
};

const ASSETS_BY_NAME = new Map(Object.values(ASSETS).map((a) => [a.name, a]));

const href = (a: TerminalAsset) => ASSET_PREFIX + a.name;

/** Serves GET /pair/assets/<name>; 404 for unknown names there; null for any other path. */
export function handleTerminalAssets(url: URL): Response | null {
  if (!url.pathname.startsWith(ASSET_PREFIX)) return null;
  const found = ASSETS_BY_NAME.get(url.pathname.slice(ASSET_PREFIX.length));
  if (!found) {
    return new Response("Not found\n", {
      status: 404,
      headers: { "content-type": TEXT_TYPE, "cache-control": "no-store", "x-content-type-options": "nosniff" },
    });
  }
  return new Response(found.body, {
    headers: {
      "content-type": found.type,
      "cache-control": "public, max-age=31536000, immutable",
      "x-content-type-options": "nosniff",
    },
  });
}

const PAGE_HEAD = String.raw`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,interactive-widget=resizes-content">
<meta name="referrer" content="no-referrer">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#111815">
<title>Dogwalk terminal</title>
`;

// Palette from the dark scheme in src/landing.ts and src/pair_page.ts.
const PAGE_STYLE = String.raw`<style>
:root{
  --paper:#111815;--card:#18211d;--ink:#ece6d7;--body:#cfd3c8;--muted:#a3ada3;--faint:#6f7a72;
  --line:#26322d;--rule:#34423b;--leash:#ec8a62;--leash-soft:#3a2219;--grass:#8cc2a6;
  --accent-ink:#1b120d;--ok:#74c69d;--bad:#ff8f85;--warn:#e3b45a;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  --dot:var(--warn);
}
html,body{margin:0;height:100%;overflow:hidden;background:var(--paper);color:var(--body)}
body{position:fixed;left:0;top:var(--vvt,0px);width:100%;height:var(--vvh,100dvh);display:flex;flex-direction:column;
  font:14px/1.35 var(--sans);-webkit-text-size-adjust:100%;overscroll-behavior:none}
body[data-state=live]{--dot:var(--ok)}
body[data-state=fatal]{--dot:var(--bad)}
body[data-state=ended]{--dot:var(--faint)}
#term{flex:1;min-height:0;padding:4px 0 0 max(6px,env(safe-area-inset-left));padding-right:env(safe-area-inset-right);box-sizing:border-box}
#term .xterm{height:100%}
#term .xterm-viewport{background:var(--paper)!important}
button,a.btn{font:600 13px/1 var(--sans);color:var(--body);background:transparent;border:1px solid var(--rule);
  border-radius:6px;min-height:30px;padding:0 10px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;
  -webkit-tap-highlight-color:transparent;touch-action:manipulation}
button:hover,a.btn:hover{border-color:var(--leash);color:var(--ink)}
button[aria-pressed=true]{background:var(--leash);border-color:var(--leash);color:var(--accent-ink)}
:focus-visible{outline:2px solid var(--leash);outline-offset:1px}

#bar{position:absolute;left:0;right:0;top:0;z-index:20;display:flex;flex-wrap:wrap;align-items:center;gap:6px;
  padding:6px max(10px,env(safe-area-inset-right)) 6px max(10px,env(safe-area-inset-left));padding-top:max(6px,env(safe-area-inset-top));
  background:rgba(24,33,29,.94);border-bottom:1px solid var(--line);box-shadow:0 4px 16px rgba(0,0,0,.35);
  transform:translateY(-110%);transition:transform .18s ease}
body.bar #bar{transform:none}
.dot{width:9px;height:9px;border-radius:50%;background:var(--dot);flex:none}
#status{font-weight:600;color:var(--ink);margin-right:auto;white-space:nowrap}
.group{display:inline-flex;align-items:center;gap:4px}
#fontVal{min-width:2ch;text-align:center;font-variant-numeric:tabular-nums;color:var(--muted)}
#pill{position:absolute;z-index:19;top:0;left:50%;transform:translateX(-50%);min-height:0;height:14px;width:48px;padding:0;
  border:1px solid var(--line);border-top:0;border-radius:0 0 8px 8px;background:rgba(24,33,29,.85);opacity:.6;
  display:flex;align-items:center;justify-content:center}
#pill:hover{opacity:1}
body.bar #pill{display:none}

#keys{display:flex;gap:5px;overflow-x:auto;padding:6px max(6px,env(safe-area-inset-left)) max(6px,env(safe-area-inset-bottom));
  background:var(--card);border-top:1px solid var(--line);scrollbar-width:none;flex:none}
#keys[hidden]{display:none}
#keys::-webkit-scrollbar{display:none}
#keys button{min-height:40px;min-width:44px;justify-content:center;font:600 14px/1 var(--sans);flex:none;user-select:none;-webkit-user-select:none}
#keys .mono{font-family:ui-monospace,Menlo,monospace;font-size:16px}
#keys .sep{width:1px;background:var(--line);flex:none;margin:4px 2px}

#banner{position:absolute;z-index:25;top:22px;left:50%;transform:translateX(-50%);max-width:min(90vw,520px);
  padding:7px 14px;border-radius:999px;background:var(--card);border:1px solid var(--rule);color:var(--ink);
  font-weight:600;box-shadow:0 6px 20px rgba(0,0,0,.4);pointer-events:none;text-align:center}
body[data-state=retry] #banner{border-color:var(--warn)}

#overlay{position:absolute;inset:0;z-index:30;display:flex;align-items:center;justify-content:center;padding:24px;
  background:rgba(17,24,21,.72);backdrop-filter:blur(2px)}
#overlay[hidden]{display:none}
#overlay[data-kind=fatal]{background:var(--paper);backdrop-filter:none}
.card{max-width:440px;background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:22px 24px;text-align:center;
  box-shadow:0 10px 40px rgba(0,0,0,.45)}
#overlay[data-kind=fatal] .card{box-shadow:none}
#ovTitle{font:600 21px/1.3 var(--serif);color:var(--ink);margin:0 0 8px}
#ovText{margin:0 0 16px;color:var(--muted);overflow-wrap:anywhere}
#ovText:empty{display:none}
.card .row{display:flex;gap:8px;justify-content:center}
#ovBtn{background:var(--leash);border-color:var(--leash);color:var(--accent-ink);min-height:38px;padding:0 16px}
#ovLink{min-height:38px;padding:0 16px}
#ovLink[hidden],#ovBtn[hidden]{display:none}
body.finished #keys,body.finished #pill{display:none}
@media (pointer:coarse){#bar button,#bar a.btn{min-height:36px}}
</style>
`;

const PAGE_BODY = String.raw`</head>
<body data-state="connecting">
<header id="bar">
  <span class="dot" aria-hidden="true"></span><span id="status" role="status" aria-live="polite">Connecting</span>
  <span class="group">
    <button id="fontDown" type="button" aria-label="Smaller text">A-</button>
    <span id="fontVal" aria-label="Font size">14</span>
    <button id="fontUp" type="button" aria-label="Larger text">A+</button>
  </span>
  <button id="copy" type="button" title="Copy the selection, or the whole screen when nothing is selected">Copy</button>
  <button id="autocopy" type="button" aria-pressed="false" title="Copy text as soon as it is selected">Copy on select</button>
  <button id="keysBtn" type="button" aria-pressed="false" title="On-screen keys">Keys</button>
  <a class="btn" href="/pair">Pairing</a>
</header>
<button id="pill" type="button" aria-label="Show toolbar"><span class="dot"></span></button>
<main id="term" aria-label="Terminal"></main>
<nav id="keys" aria-label="Terminal keys" hidden>
  <button type="button" data-k="esc">Esc</button>
  <button type="button" data-k="tab">Tab</button>
  <button type="button" data-k="ctrl" aria-pressed="false" id="kCtrl">Ctrl</button>
  <button type="button" data-k="alt" aria-pressed="false" id="kAlt">Alt</button>
  <span class="sep"></span>
  <button type="button" data-k="left" data-repeat="1" aria-label="Left">&larr;</button>
  <button type="button" data-k="up" data-repeat="1" aria-label="Up">&uarr;</button>
  <button type="button" data-k="down" data-repeat="1" aria-label="Down">&darr;</button>
  <button type="button" data-k="right" data-repeat="1" aria-label="Right">&rarr;</button>
  <span class="sep"></span>
  <button type="button" data-k="home">Home</button>
  <button type="button" data-k="end">End</button>
  <button type="button" data-k="pgup" data-repeat="1">PgUp</button>
  <button type="button" data-k="pgdn" data-repeat="1">PgDn</button>
  <span class="sep"></span>
  <button type="button" class="mono" data-ch="|">|</button>
  <button type="button" class="mono" data-ch="~">~</button>
  <button type="button" class="mono" data-ch="/">/</button>
  <button type="button" class="mono" data-ch="-">-</button>
  <span class="sep"></span>
  <button type="button" data-k="paste">Paste</button>
</nav>
<div id="banner" role="status" aria-live="polite" hidden></div>
<div id="overlay" hidden>
  <div class="card" role="dialog" aria-labelledby="ovTitle">
    <h1 id="ovTitle"></h1>
    <p id="ovText"></p>
    <div class="row">
      <button id="ovBtn" type="button"></button>
      <a id="ovLink" class="btn" href="/pair">Open the pairing page</a>
    </div>
  </div>
</div>
`;

export const TERMINAL_HTML =
  PAGE_HEAD +
  `<link rel="stylesheet" href="${href(ASSETS.xtermCss)}">\n` +
  PAGE_STYLE +
  PAGE_BODY +
  [ASSETS.xterm, ASSETS.fit, ASSETS.webgl, ASSETS.unicode11, ASSETS.webLinks, ASSETS.clipboard, ASSETS.page]
    .map((a) => `<script src="${href(a)}"></script>\n`)
    .join("") +
  "</body>\n</html>\n";
