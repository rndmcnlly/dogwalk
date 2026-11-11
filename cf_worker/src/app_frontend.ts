import type { Env } from "./types";

export const APP_PROTOCOL = 1;
export const APP_LEASE_MS = 60_000;
const id = (v: unknown): v is string => typeof v === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(v);

/** App capability only: never operator auth, URL auth, or caller-selected identity. */
export async function appAuthorized(request: Request, env: Env): Promise<boolean> {
  if (env.APP_FRONTEND_ENABLED !== "true" || !/^\+[1-9]\d{7,14}$/.test(env.APP_FRONTEND_OWNER_PHONE ?? "")) return false;
  if (new URL(request.url).search || new URL(request.url).protocol !== "https:") return false;
  const token = /^Bearer ([A-Za-z0-9_-]{43,128})$/.exec(request.headers.get("authorization") ?? "")?.[1];
  if (!token || !/^[a-f0-9]{64}$/.test(env.APP_FRONTEND_TOKEN_SHA256 ?? "")) return false;
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token))))
    .map((b) => b.toString(16).padStart(2, "0")).join("");
  let difference = 0;
  for (let i = 0; i < 64; i++) difference |= digest.charCodeAt(i) ^ env.APP_FRONTEND_TOKEN_SHA256!.charCodeAt(i);
  return difference === 0;
}

export function parseAppFrame(raw: unknown, attachment: string): { id: string; event: Record<string, any> } {
  if (typeof raw !== "string" || new TextEncoder().encode(raw).length > 32_768) throw new Error("invalid frame");
  const m = JSON.parse(raw);
  if (m?.v !== APP_PROTOCOL || m.attachment !== attachment || !id(m.id) || !m.event || typeof m.event !== "object") throw new Error("invalid envelope");
  const e = m.event;
  const time = (v: unknown) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= Number.MAX_SAFE_INTEGER;
  if (e.type === "session.delegation.created") {
    if (!id(e.delegation?.id) || e.delegation.target !== "client" || !time(e.offset_ms)) throw new Error("invalid handoff");
  } else if (["session.input_transcript.delta", "session.output_transcript.delta"].includes(e.type)) {
    if (typeof e.delta !== "string" || e.delta.length > 16_384 || !time(e.start_ms) || !time(e.end_ms) || e.end_ms < e.start_ms) throw new Error("invalid transcript");
    const input = e.type === "session.input_transcript.delta";
    if (!(input ? ["text_typed", "speech_recognized"] : ["text_displayed", "speech_played"]).includes(e.delivery)) throw new Error("invalid provenance");
    if (["text_typed", "text_displayed"].includes(e.delivery) && e.start_ms !== e.end_ms) throw new Error("text delivery has no audio duration");
    if (!input && e.request_id != null && !id(e.request_id)) throw new Error("invalid receipt");
    if (!input && typeof e.complete !== "boolean") throw new Error("missing receipt completion");
  } else throw new Error("unsupported event");
  return { id: m.id, event: e };
}
