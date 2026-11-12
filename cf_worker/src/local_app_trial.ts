import type { Env } from "./types";

/** Explicit development adapter, never a production provider fallback. */
export function localAppTrial(env: Env): { acpUrl: string; workspace: string } | null {
  const configured = [env.LOCAL_APP_TRIAL_ENABLED, env.LOCAL_APP_TRIAL_ACP_URL, env.LOCAL_APP_TRIAL_WORKSPACE].some(v => v !== undefined);
  if (!configured) return null;
  if (env.DEPLOYMENT_ENVIRONMENT !== "local-app-trial" || env.LOCAL_APP_TRIAL_ENABLED !== "true") throw new Error("Local app hosting is forbidden outside the explicit local trial");
  const url = new URL(env.LOCAL_APP_TRIAL_ACP_URL ?? "invalid:");
  const workspace = env.LOCAL_APP_TRIAL_WORKSPACE ?? "";
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port || url.pathname !== "/acp" || url.search || url.hash || url.username || url.password
    || !workspace.startsWith("/private/var/folders/") || !workspace.includes("/dogwalk-local-trial-") || !workspace.endsWith("/workspace") || workspace.split("/").includes("..")) {
    throw new Error("Local trial requires a fixed loopback ACP endpoint and disposable workspace");
  }
  return { acpUrl: url.toString(), workspace };
}
