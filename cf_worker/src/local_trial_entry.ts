import worker from "./index";
import { localAppTrial } from "./local_app_trial";
import type { Env } from "./types";
export { VoiceSession, TerminalRelay } from "./index";

// Used only by the private, generated wrangler config. No PSTN/admin/browser routes,
// scheduled jobs, or public hosting lifecycle. Tailscale TLS terminates before loopback.
export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    try { if (!localAppTrial(env)) return new Response("local trial disabled", { status: 404 }); }
    catch { return new Response("invalid local trial configuration", { status: 503 }); }
    const url = new URL(request.url);
    if (!["/app/frontend", "/app/heartbeat"].includes(url.pathname)) return new Response("not found", { status: 404 });
    return worker.fetch(new Request(`https://local-app-trial.invalid${url.pathname}${url.search}`, request), env, ctx);
  },
};
