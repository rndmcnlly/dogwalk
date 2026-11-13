// Spend accounting (issue #57): an estimate of what each architectural component costs, kept
// in D1 so playtest.py and the Diagnostics Surface can total it without the OpenAI Platform,
// and so the Opening can warn before the provider's hard budget refuses calls.
//
// Each component bills to its own OpenAI key in the Dogwalk project, which carries the hard
// monthly limit (DOGWALK_MONTHLY_BUDGET_USD mirrors it). The ledger is an estimate: it prices
// what the Worker sees, so the Platform remains the authority, and laptop probes (their own
// key, same project) count against the limit without appearing here.

import type { Env } from "./types";

export type SpendComponent = "voice_frontend" | "orchestrator" | "sandbox_agents" | "baseline";

// USD per 1M tokens, Standard tier, short context, from openai.com/api/pricing (2026-11-13).
// A 30-minute cache write costs 1.25x input and a 12-hour write 2.5x; cached_tokens excludes writes.
const TOKEN_PRICES: Record<string, { input: number; cached: number; output: number }> = {
  "gpt-6.1-sol": { input: 2.0, cached: 0.1, output: 10.0 },
  "gpt-6.1-terra": { input: 1.0, cached: 0.05, output: 5.0 },
  "gpt-6-luna": { input: 0.1, cached: 0.01, output: 0.5 },
};
// gpt-live-1 bills per second of session, without rounding to a minute.
const LIVE_USD_PER_SECOND = 0.05 / 60;

// The Opening mentions the budget once the month's estimate passes this share of it.
export const BUDGET_NOTICE_RATIO = 0.75;

const count = (value: unknown): number => (Number.isSafeInteger(value) && (value as number) >= 0 ? value as number : 0);

// The Responses API usage, numbers only: the token counts and their details, never content.
export function responseUsage(usage: unknown): Record<string, unknown> | null {
  if (!usage || typeof usage !== "object") return null;
  const raw = usage as Record<string, any>;
  const input = raw.input_tokens_details ?? {};
  const writes = input.cache_write_tokens_details ?? {};
  const kept = {
    input_tokens: count(raw.input_tokens),
    output_tokens: count(raw.output_tokens),
    input_tokens_details: {
      cached_tokens: count(input.cached_tokens),
      cache_write_tokens: count(input.cache_write_tokens),
      ...(input.cache_write_tokens_details ? { cache_write_tokens_details: {
        ttl_30m_tokens: count(writes.ttl_30m_tokens), ttl_12h_tokens: count(writes.ttl_12h_tokens) } } : {}),
    },
    output_tokens_details: { reasoning_tokens: count(raw.output_tokens_details?.reasoning_tokens) },
  };
  return kept.input_tokens || kept.output_tokens ? kept : null;
}

// What one Responses API call cost, or null for a model with no known price.
export function tokenCost(model: string, usage: Record<string, any> | null): number | null {
  const price = TOKEN_PRICES[model];
  if (!price || !usage) return null;
  const details = usage.input_tokens_details ?? {};
  const cached = count(details.cached_tokens), writes = count(details.cache_write_tokens);
  const ttl = details.cache_write_tokens_details;
  // Without the lifetime split every write is priced as 30-minute, the default lifetime.
  const writeWeight = ttl ? 1.25 * count(ttl.ttl_30m_tokens) + 2.5 * count(ttl.ttl_12h_tokens) : 1.25 * writes;
  const ordinary = Math.max(0, count(usage.input_tokens) - cached - writes);
  return (ordinary * price.input + cached * price.cached + writeWeight * price.input
    + count(usage.output_tokens) * price.output) / 1_000_000;
}

export function liveCost(seconds: number): number {
  return Math.max(0, seconds) * LIVE_USD_PER_SECOND;
}

export async function recordSpend(env: Env, entry: {
  component: SpendComponent; usd: number; sandboxId: string | null; callSid: string | null;
  detail?: Record<string, unknown>;
}): Promise<void> {
  if (!(entry.usd > 0)) return;
  try {
    await env.DB.prepare(
      "INSERT INTO spend (ts, component, usd, sandbox_id, call_sid, detail) VALUES (?, ?, ?, ?, ?, ?)",
    ).bind(Math.floor(Date.now() / 1000), entry.component, entry.usd, entry.sandboxId, entry.callSid,
      entry.detail ? JSON.stringify(entry.detail) : null).run();
  } catch (error) {
    // Accounting is best effort: a lost row must never stall a call.
    console.error("spend record failed", error instanceof Error ? error.message : String(error));
  }
}

export function monthlyBudget(env: Env): number | null {
  const budget = Number(env.DOGWALK_MONTHLY_BUDGET_USD);
  return Number.isFinite(budget) && budget > 0 ? budget : null;
}

// The provider's limit resets at the start of each UTC calendar month.
export function monthStart(now = new Date()): number {
  return Math.floor(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1) / 1000);
}

export interface SpendSummary {
  budget_usd: number | null;
  month_usd: number;
  days: { day: string; total_usd: number; by_component: Record<string, number> }[];
}

// This month's estimate and the last few UTC days, each split by component.
export async function spendSummary(env: Env, days = 7): Promise<SpendSummary> {
  const since = Math.min(monthStart(), Math.floor(Date.now() / 1000 / 86400 - (days - 1)) * 86400);
  const rows = await env.DB.prepare(
    `SELECT date(ts, 'unixepoch') AS day, component, SUM(usd) AS usd, MIN(ts) AS first_ts
     FROM spend WHERE ts >= ? GROUP BY day, component ORDER BY day DESC`,
  ).bind(since).all<{ day: string; component: string; usd: number; first_ts: number }>();
  const start = monthStart();
  const recent = new Set(Array.from({ length: days }, (_, i) =>
    new Date(Date.now() - i * 86_400_000).toISOString().slice(0, 10)));
  const byDay = new Map<string, Record<string, number>>();
  let month = 0;
  for (const row of rows.results) {
    if (row.first_ts >= start) month += row.usd;
    if (!recent.has(row.day)) continue;
    byDay.set(row.day, { ...byDay.get(row.day), [row.component]: round(row.usd) });
  }
  return {
    budget_usd: monthlyBudget(env),
    month_usd: round(month),
    days: [...byDay].map(([day, by]) => ({ day, total_usd: round(Object.values(by).reduce((a, b) => a + b, 0)), by_component: by })),
  };
}

// One call's spend by component, for playtest.py.
export async function callSpend(env: Env, callSid: string): Promise<Record<string, number>> {
  const rows = await env.DB.prepare("SELECT component, SUM(usd) AS usd FROM spend WHERE call_sid = ? GROUP BY component")
    .bind(callSid).all<{ component: string; usd: number }>();
  return Object.fromEntries(rows.results.map((row) => [row.component, round(row.usd)]));
}

const round = (usd: number): number => Math.round(usd * 10_000) / 10_000;

// Spoken at the Opening once the month's estimate nears the budget, or null.
export function budgetClause(summary: Pick<SpendSummary, "budget_usd" | "month_usd">): string | null {
  const { budget_usd: budget, month_usd: spent } = summary;
  if (!budget || spent < budget * BUDGET_NOTICE_RATIO) return null;
  if (spent >= budget) return `Dogwalk has used its ${budget} dollar budget for this month, so calls may stop working until it resets or is raised.`;
  return `Dogwalk has used about ${Math.round(spent)} of its ${budget} dollar budget this month.`;
}
