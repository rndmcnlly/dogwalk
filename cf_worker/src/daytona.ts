/**
 * Daytona v2, the Sandbox provider: the one place its HTTP shapes are known.
 *
 * Measured 2026-11-25 (API 2.0.0-alpha.6), where the published docs say otherwise or nothing:
 * - `persistent` Linux sandboxes stop, start, pause, resume, and restart. /start also resumes.
 * - A persistent sandbox gets a 300 s wall-clock timeout unless the create sets one.
 * - A deleted sandbox still answers 200, with status "deleted".
 * - Revoking signed links (sandbox-wide) refuses new opens but leaves open sockets connected.
 *   Unexposing the port does too; ending the server inside the Sandbox closes them at once.
 * - Just after a start, reads can briefly go back a step (running, then assigned).
 * - A start in the first moments after a stop is refused with 409.
 * - An SSH access token cannot be revoked; rotating the signing key does not end it.
 */

import type { Env } from "./types";

export interface DaytonaSandbox {
  id: string;
  status: string;
  error?: string | null;
  toolbox_proxy_url?: string | null;
  ssh_gateway_host?: string | null;
  ssh_gateway_port?: number | null;
}

export class DaytonaApiError extends Error {
  readonly status: number;
  constructor(status: number, detail: string) {
    super(`Daytona ${status}: ${detail}`);
    this.status = status;
  }
}

/** Where a running Sandbox's toolbox answers, and the credential it takes. */
export interface Toolbox { toolbox: string; headers: Record<string, string> }

export const DAYTONA_LABEL_MANAGED = "dogwalk.dev/managed-by";
export const DAYTONA_LABEL_IDENTITY = "dogwalk.dev/identity";

export const GONE_STATUSES = ["deleting", "deleted"];
export const BROKEN_STATUSES = ["failed", "lost"];
/** Statuses a start brings back to running. */
export const ASLEEP_STATUSES = ["stopped", "paused"];
/** Statuses on the way to one of those, which refuse a start until they arrive. */
export const FALLING_ASLEEP: Record<string, string> = { stopping: "stopped", pausing: "paused" };

const IDLE_SECONDS = 15 * 60;
// The longest the platform allows. It cannot be turned off, so each call restarts it.
const MAX_RUN_SECONDS = 86_400;

export async function daytonaFetch<T>(env: Env, path: string, init: RequestInit = {}): Promise<T> {
  const base = (env.DAYTONA_API_BASE ?? "https://api.daytona.com").replace(/\/$/, "");
  const response = await fetch(`${base}${path}`, {
    signal: AbortSignal.timeout(10_000),
    ...init,
    headers: {
      authorization: `Bearer ${env.DAYTONA_API_KEY}`,
      ...(init.body ? { "content-type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) throw new DaytonaApiError(response.status, (await response.text()).slice(0, 300));
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const sandboxPath = (id: string) => `/sandboxes/${encodeURIComponent(id)}`;

/** A Sandbox that exists. One deleted, or being deleted, is a 404 like one never made. */
export async function getSandbox(env: Env, id: string): Promise<DaytonaSandbox> {
  const sandbox = await daytonaFetch<DaytonaSandbox>(env, sandboxPath(id));
  if (GONE_STATUSES.includes(sandbox.status)) throw new DaytonaApiError(404, `sandbox ${sandbox.status}`);
  return sandbox;
}

/** The Sandboxes labelled for an identity. The index lags writes by a few seconds. */
export async function findSandboxes(env: Env, identity: string): Promise<DaytonaSandbox[]> {
  const labels = JSON.stringify({ [DAYTONA_LABEL_MANAGED]: "dogwalk", [DAYTONA_LABEL_IDENTITY]: identity });
  const query = new URLSearchParams({ labels, limit: "3" });
  const page = await daytonaFetch<{ sandboxes: DaytonaSandbox[] }>(env, `/sandboxes/search?${query}`);
  return page.sandboxes.filter((sandbox) => !GONE_STATUSES.includes(sandbox.status));
}

export async function createSandbox(env: Env, name: string, identity: string): Promise<DaytonaSandbox> {
  return daytonaFetch<DaytonaSandbox>(env, "/sandboxes", {
    method: "POST",
    body: JSON.stringify({
      name,
      image: env.DAYTONA_IMAGE,
      region_id: env.DAYTONA_REGION,
      persistent: true,
      cores_max: 1,  // 2 vCPUs
      memory_mib: 2048,
      memory_max_mib: 4096,
      disk_max_mib: 10_240,
      // A project secret's name, not a value: the egress proxy substitutes the key into HTTPS
      // headers to allowlisted hosts, so the Sandbox only ever holds a placeholder.
      ...(env.DAYTONA_AGENT_SECRET ? { secrets: [env.DAYTONA_AGENT_SECRET] } : {}),
      labels: { [DAYTONA_LABEL_MANAGED]: "dogwalk", [DAYTONA_LABEL_IDENTITY]: identity },
      // Pausing keeps memory, so the Agent's processes are still there on the next call. Only
      // the Worker wakes a Sandbox: a stray request to a link must not start one.
      lifecycle_policy: {
        idle_expiry_seconds: IDLE_SECONDS, on_idle_timeout: "pause",
        timeout_seconds: MAX_RUN_SECONDS, on_timeout: "pause",
        wake_on_request: false,
      },
      wait_seconds: 0,
    }),
  });
}

/** Start a sleeping Sandbox. Daytona answers 409 while it is falling asleep and for a moment after. */
export async function startSandbox(env: Env, id: string): Promise<DaytonaSandbox> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await daytonaFetch<DaytonaSandbox>(env, `${sandboxPath(id)}/start`, { method: "POST" });
    } catch (error) {
      if (!(error instanceof DaytonaApiError) || error.status !== 409 || attempt >= 12) throw error;
    }
    const sandbox = await getSandbox(env, id);
    // Another request started it, unless it has yet to finish falling asleep.
    if (!ASLEEP_STATUSES.includes(sandbox.status) && !(sandbox.status in FALLING_ASLEEP)) return sandbox;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
}

/** Start a Sandbox caught on its way to sleep, once it gets there. */
export async function startOnceAsleep(env: Env, id: string, asleep: string): Promise<DaytonaSandbox> {
  const query = new URLSearchParams({ wait: asleep, timeout_ms: "20000" });
  await daytonaFetch(env, `${sandboxPath(id)}?${query}`, { signal: AbortSignal.timeout(25_000) });
  return startSandbox(env, id);
}

export async function restartSandbox(env: Env, id: string): Promise<void> {
  await daytonaFetch(env, `${sandboxPath(id)}/restart`, { method: "POST" });
}

/** Restart the wall-clock limit, so a call never inherits the end of the last one's. */
export async function renewRunLimit(env: Env, id: string): Promise<void> {
  await daytonaFetch(env, `${sandboxPath(id)}/lifecycle-policy`, {
    method: "PATCH", body: JSON.stringify({ timeout_seconds: MAX_RUN_SECONDS }),
  });
}

export async function deleteSandbox(env: Env, id: string): Promise<void> {
  await daytonaFetch(env, sandboxPath(id), { method: "DELETE" });
}

/** The port's URL and a token for it that outlives pauses and restarts. */
export async function previewUrl(env: Env, id: string, port: number): Promise<{ url: string; token: string }> {
  return daytonaFetch(env, `${sandboxPath(id)}/ports/${port}/preview-url`, { method: "POST" });
}

/** A link anyone holding it can open, for that port, until it expires or every link is revoked. */
export async function signedPreviewUrl(env: Env, id: string, port: number, seconds: number): Promise<{ url: string }> {
  return daytonaFetch(env, `${sandboxPath(id)}/ports/${port}/signed-preview-urls`, {
    method: "POST", body: JSON.stringify({ expires_in_seconds: seconds }),
  });
}

/** Every signed link on the Sandbox, on every port: there is no revoking one. */
export async function revokeSignedPreviewUrls(env: Env, id: string): Promise<void> {
  await daytonaFetch(env, `${sandboxPath(id)}/signed-preview-urls/revoke`, { method: "POST" });
}

/** An SSH command carrying a token that opens nothing else and cannot be revoked. */
export async function sshCommand(env: Env, id: string, seconds: number): Promise<{ command: string; expiresAt: number }> {
  const sandbox = await getSandbox(env, id);
  if (!sandbox.ssh_gateway_host || !sandbox.ssh_gateway_port) throw new Error("the SSH gateway is unavailable");
  const access = await daytonaFetch<{ access_token: string; expires_at: string }>(env, `${sandboxPath(id)}/access-token`, {
    method: "POST", body: JSON.stringify({ operations: ["ssh"], ports: [], ttl_seconds: seconds }),
  });
  return {
    command: `ssh -p ${sandbox.ssh_gateway_port} '${id}#${access.access_token}@${sandbox.ssh_gateway_host}'`,
    expiresAt: Date.parse(access.expires_at),
  };
}

export function toolboxOf(env: Env, sandbox: DaytonaSandbox): Toolbox {
  if (!sandbox.toolbox_proxy_url) throw new Error("the sandbox has no toolbox");
  return {
    toolbox: `${sandbox.toolbox_proxy_url.replace(/\/$/, "")}/${encodeURIComponent(sandbox.id)}`,
    headers: { authorization: `Bearer ${env.DAYTONA_API_KEY}` },
  };
}

/** Run a bash script to completion. Output interleaves stdout and stderr, as one stream would. */
export async function execute(
  { toolbox, headers }: Toolbox, script: string, timeoutSeconds: number,
): Promise<{ result: string; exitCode: number }> {
  const milliseconds = timeoutSeconds * 1000;
  // Waits past the command's own timeout, so a timed-out command is reported rather than left running.
  const query = new URLSearchParams({ waitMs: String(milliseconds + 2000), logs: "text" });
  const response = await fetch(`${toolbox}/processes?${query}`, {
    method: "POST",
    headers: { ...headers, "content-type": "application/json" },
    body: JSON.stringify({ argv: ["bash", "-c", script], env: { LC_ALL: "C" }, timeoutMs: milliseconds }),
    signal: AbortSignal.timeout(milliseconds + 5000),
  });
  if (!response.ok) throw new Error(`the sandbox could not run a command (${response.status})`);
  const process = await response.json<{
    id: string; state: string; exitCode?: number | null; logs?: { frames: Array<{ data: string }>; nextCursor?: string; eof?: boolean };
  }>();
  if (process.state !== "terminal") throw new Error("the command did not finish in time");
  let result = "";
  let page = process.logs;
  for (let pages = 0; page && pages < 100; pages++) {
    result += page.frames.map((frame) => frame.data).join("");
    if (page.eof !== false || !page.nextCursor) break;
    const more = new URLSearchParams({ cursor: page.nextCursor, encoding: "text" });
    const next = await fetch(`${toolbox}/processes/${encodeURIComponent(process.id)}/logs?${more}`, { headers, signal: AbortSignal.timeout(10_000) });
    if (!next.ok) throw new Error(`the sandbox could not return a command's output (${next.status})`);
    page = await next.json<NonNullable<typeof process.logs>>();
  }
  return { result, exitCode: process.exitCode ?? -1 };
}

// The servers behind a Takeover Terminal and Desktop. Daytona has no page for either that can be
// shared, so the image carries ttyd and noVNC, run only while a grant is open. Ending one is also
// the only sure way to close a viewer's socket. Both listen on every interface because the preview
// proxy cannot reach loopback; the port stays private, reached only by its signed links.
export const TERMINAL_PORT = 7681;
export const DESKTOP_PORT = 6080;
// match is anchored so that it never names the shell running pgrep or pkill.
const TAKEOVER_SERVERS: Record<number, { command: string; match: string }> = {
  // Each viewer gets a login shell of its own, which ends with the page.
  [TERMINAL_PORT]: { command: `ttyd --writable --port ${TERMINAL_PORT} --cwd /home/daytona bash -l`, match: `^ttyd .*--port ${TERMINAL_PORT} ` },
  [DESKTOP_PORT]: { command: `websockify --web /usr/share/novnc ${DESKTOP_PORT} localhost:5900`, match: `^[^ ]*python[^ ]* [^ ]*websockify .* ${DESKTOP_PORT} localhost` },
};

export const isTakeoverPort = (port: number) => Object.hasOwn(TAKEOVER_SERVERS, port);

/** Daytona's own ports inside a Sandbox, which it refuses to preview. */
export const DAEMON_PORTS = [2280, 2281, 22220];

export async function startTakeoverServer(box: Toolbox, port: number): Promise<void> {
  const { command, match } = TAKEOVER_SERVERS[port];
  const listening = `(exec 3<>/dev/tcp/127.0.0.1/${port}) 2>/dev/null`;
  const { exitCode } = await execute(box, [
    `pgrep -f '${match}' >/dev/null || setsid ${command} </dev/null >/dev/null 2>&1 &`,
    `for i in $(seq 50); do ${listening} && exit 0; sleep 0.1; done; exit 1`,
  ].join("\n"), 10);
  if (exitCode !== 0) throw new Error("the takeover server did not start");
}

export async function stopTakeoverServer(box: Toolbox, port: number): Promise<void> {
  const { match } = TAKEOVER_SERVERS[port];
  // 0: ended; 1: none was running. TERM lets ttyd hang up its shells.
  const { exitCode } = await execute(box, `pkill -f '${match}'`, 10);
  if (exitCode > 1 || exitCode < 0) throw new Error("the takeover server did not stop");
}
