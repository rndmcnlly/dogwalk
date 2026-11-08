// Human Takeover terminal (DOMAIN.md): relays a full-screen browser terminal to a Daytona PTY.
//
// One object per Sandbox Assignment identity, separate from VoiceSession: an open terminal
// holds an outbound WebSocket, which keeps an object in memory, and the VoiceSession should
// keep hibernating meanwhile. The browser authenticates with a one-time launch ticket (or, on
// reload, the terminal token it was given), which VoiceSession redeems; no Daytona credential
// ever reaches the browser. The PTY runs `tmux new -A -s human`, so reconnecting redraws the
// screen and a TUI such as OpenCode survives a closed tab.

import type { Env } from "./types";

const PTY_ID = "human";
const PTY_PROTOCOL = "X-Daytona-Pty-Exit-Control";

interface Relay {
  browserHash: string | null;
  upstream: WebSocket | null;
  toolbox: string | null;
}

export class TerminalRelay {
  private relays = new Map<WebSocket, Relay>();

  constructor(private state: DurableObjectState, private env: Env) {}

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/ws") {
      const identity = request.headers.get("x-dogwalk-identity") ?? "";
      const pair = new WebSocketPair();
      const browser = pair[1];
      browser.accept();
      this.relays.set(browser, { browserHash: null, upstream: null, toolbox: null });
      browser.addEventListener("message", (event) => {
        void this.fromBrowser(browser, identity, event.data).catch((error) => this.fail(browser, String(error?.message ?? error)));
      });
      browser.addEventListener("close", () => this.drop(browser));
      browser.addEventListener("error", () => this.drop(browser));
      return new Response(null, { status: 101, webSocket: pair[0] });
    }
    if (url.pathname === "/revoke" && request.method === "POST") {
      // VoiceSession: these browsers were unpaired or expired; end their terminals now.
      const { browserHashes } = await request.json<{ browserHashes: string[] }>();
      for (const [browser, relay] of this.relays) {
        if (relay.browserHash && browserHashes.includes(relay.browserHash)) {
          browser.close(4003, "pairing ended");
          this.drop(browser);
        }
      }
      return new Response(null, { status: 204 });
    }
    return new Response("not found", { status: 404 });
  }

  private async fromBrowser(browser: WebSocket, identity: string, data: string | ArrayBuffer): Promise<void> {
    const relay = this.relays.get(browser);
    if (!relay) return;
    if (typeof data !== "string") {
      // Keystrokes.
      if (relay.upstream?.readyState === WebSocket.OPEN) relay.upstream.send(data);
      return;
    }
    const message = JSON.parse(data) as Record<string, unknown>;
    if (!relay.browserHash) {
      if (message.type !== "auth") return this.refuse(browser, "authenticate first");
      return this.authenticate(browser, relay, identity, message);
    }
    if (message.type === "resize" && relay.toolbox) {
      await this.resize(relay.toolbox, Number(message.cols), Number(message.rows));
    } else if (message.type === "ping") {
      browser.send(JSON.stringify({ type: "pong" }));
    }
  }

  private async authenticate(browser: WebSocket, relay: Relay, identity: string, message: Record<string, unknown>): Promise<void> {
    const session = this.env.VOICE_SESSIONS.get(this.env.VOICE_SESSIONS.idFromName(identity));
    const redeemed = await session.fetch("https://voice-session/pair/takeover/redeem", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ticket: message.ticket ?? null, token: message.token ?? null }),
    });
    if (!redeemed.ok) return this.refuse(browser, "this terminal link has expired");
    const grant = await redeemed.json<{ token: string; browserHash: string; sandboxId: string; expiresAt: number }>();
    relay.browserHash = grant.browserHash;
    browser.send(JSON.stringify({ type: "ready", token: grant.token, expires_at: grant.expiresAt }));
    const cols = clamp(Number(message.cols), 20, 400, 120);
    const rows = clamp(Number(message.rows), 5, 200, 32);
    relay.toolbox = await this.toolbox(grant.sandboxId);
    const created = await this.ensurePty(relay.toolbox, cols, rows);
    relay.upstream = await this.connect(browser, relay.toolbox);
    // A fresh PTY starts a plain shell; hand it to tmux so later connections reattach.
    if (created) relay.upstream.send(" command -v tmux >/dev/null && exec tmux new-session -A -s human\r");
    else await this.resize(relay.toolbox, cols, rows);
  }

  private async toolbox(sandboxId: string): Promise<string> {
    const base = (this.env.DAYTONA_API_BASE ?? "https://app.daytona.io/api").replace(/\/$/, "");
    const response = await fetch(`${base}/sandbox/${encodeURIComponent(sandboxId)}`, {
      headers: this.auth(), signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) throw new Error(`the sandbox is unavailable (${response.status}); a call wakes it`);
    const sandbox = await response.json<{ toolboxProxyUrl?: string; state?: string }>();
    if (sandbox.state && sandbox.state !== "started") throw new Error(`the sandbox is ${sandbox.state}; a call wakes it`);
    return `${(sandbox.toolboxProxyUrl ?? "https://proxy.app.daytona.io/toolbox").replace(/\/$/, "")}/${encodeURIComponent(sandboxId)}`;
  }

  // Reuse the running PTY when there is one; returns whether a new one was created.
  private async ensurePty(toolbox: string, cols: number, rows: number): Promise<boolean> {
    const listed = await fetch(`${toolbox}/process/pty`, { headers: this.auth(), signal: AbortSignal.timeout(10_000) });
    if (listed.ok) {
      const body = await listed.json<unknown>();
      const sessions = (Array.isArray(body) ? body : (body as { sessions?: unknown[] }).sessions ?? []) as Array<{ id?: string; active?: boolean }>;
      const existing = sessions.find((item) => item.id === PTY_ID);
      if (existing && existing.active !== false) return false;
      if (existing) await fetch(`${toolbox}/process/pty/${PTY_ID}`, { method: "DELETE", headers: this.auth() });
    }
    const created = await fetch(`${toolbox}/process/pty`, {
      method: "POST",
      headers: { ...this.auth(), "content-type": "application/json" },
      body: JSON.stringify({
        id: PTY_ID, cwd: "/home/daytona", cols, rows, lazyStart: false,
        envs: { TERM: "xterm-256color", COLORTERM: "truecolor", LANG: "C.UTF-8" },
      }),
      signal: AbortSignal.timeout(15_000),
    });
    if (!created.ok) throw new Error(`the terminal could not start (${created.status})`);
    return true;
  }

  private async connect(browser: WebSocket, toolbox: string): Promise<WebSocket> {
    const response = await fetch(`${toolbox}/process/pty/${PTY_ID}/connect`, {
      headers: { ...this.auth(), Upgrade: "websocket", "Sec-WebSocket-Protocol": PTY_PROTOCOL },
    });
    if (!response.webSocket) throw new Error(`the terminal could not connect (${response.status})`);
    const upstream = response.webSocket;
    upstream.accept();
    upstream.addEventListener("message", (event) => {
      if (browser.readyState !== WebSocket.OPEN) return;
      if (typeof event.data !== "string") return browser.send(event.data);
      // Text frames are Daytona control messages: connected, or the shell exiting.
      try {
        const control = JSON.parse(event.data) as { type?: string; status?: string; exitCode?: number; exitReason?: string };
        if (control.status === "exited") browser.send(JSON.stringify({ type: "exit", code: control.exitCode ?? 0, reason: control.exitReason ?? "" }));
      } catch {
        browser.send(new TextEncoder().encode(event.data));
      }
    });
    upstream.addEventListener("close", (event) => {
      if (browser.readyState !== WebSocket.OPEN) return;
      let code = 0;
      try {
        code = Number((JSON.parse(event.reason || "{}") as { exitCode?: number }).exitCode ?? 0);
      } catch {
        // a plain reason
      }
      browser.send(JSON.stringify({ type: "exit", code, reason: event.reason || "the terminal closed" }));
    });
    return upstream;
  }

  private async resize(toolbox: string, cols: number, rows: number): Promise<void> {
    if (!Number.isFinite(cols) || !Number.isFinite(rows)) return;
    await fetch(`${toolbox}/process/pty/${PTY_ID}/resize`, {
      method: "POST",
      headers: { ...this.auth(), "content-type": "application/json" },
      body: JSON.stringify({ cols: clamp(cols, 20, 400, 120), rows: clamp(rows, 5, 200, 32) }),
    }).catch(() => undefined);
  }

  private auth(): Record<string, string> {
    return { authorization: `Bearer ${this.env.DAYTONA_API_KEY}` };
  }

  private refuse(browser: WebSocket, error: string): void {
    browser.send(JSON.stringify({ type: "error", error }));
    browser.close(4001, error.slice(0, 120));
    this.drop(browser);
  }

  private fail(browser: WebSocket, error: string): void {
    if (browser.readyState !== WebSocket.OPEN) return this.drop(browser);
    browser.send(JSON.stringify({ type: "notice", text: error }));
    browser.close(1011, error.slice(0, 120));
    this.drop(browser);
  }

  // The tmux session (and the PTY) outlive the tab; only this connection ends.
  private drop(browser: WebSocket): void {
    const relay = this.relays.get(browser);
    if (!relay) return;
    this.relays.delete(browser);
    if (relay.upstream?.readyState === WebSocket.OPEN) relay.upstream.close(1000, "browser closed");
  }
}

function clamp(value: number, low: number, high: number, fallback: number): number {
  return Number.isFinite(value) ? Math.max(low, Math.min(high, Math.round(value))) : fallback;
}
