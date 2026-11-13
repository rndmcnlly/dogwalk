// Spend ledger pricing and the Opening's budget clause: node --experimental-strip-types cf_worker/spend_test.ts
import assert from "node:assert/strict";
import { budgetClause, liveCost, monthStart, responseUsage, tokenCost } from "./src/spend.ts";

// The prompt-caching guide's own example, at gpt-6.1-sol's prices: 1,000 ordinary, 4,000 cached,
// 3,000 written for 30 minutes and 4,000 for 12 hours, plus 500 output.
const usage = responseUsage({
  input_tokens: 12_000, output_tokens: 500, output_tokens_details: { reasoning_tokens: 200 },
  input_tokens_details: { cached_tokens: 4_000, cache_write_tokens: 7_000,
    cache_write_tokens_details: { ttl_30m_tokens: 3_000, ttl_12h_tokens: 4_000 } },
});
const expected = (1_000 * 2 + 4_000 * 0.1 + 3_000 * 1.25 * 2 + 4_000 * 2.5 * 2 + 500 * 10) / 1_000_000;
assert.ok(Math.abs(tokenCost("gpt-6.1-sol", usage)! - expected) < 1e-12);

// Writes without a lifetime split are priced as 30-minute writes; content never survives.
const plain = responseUsage({ input_tokens: 100, output_tokens: 0, input_tokens_details: { cache_write_tokens: 100 }, text: "secret" })!;
assert.equal(tokenCost("gpt-6.1-sol", plain), 100 * 1.25 * 2 / 1_000_000);
assert.ok(!("text" in plain));
// Before #54 the details were dropped, so cached tokens read as 0.
assert.equal((plain.input_tokens_details as Record<string, number>).cached_tokens, 0);

assert.equal(tokenCost("unknown-model", usage), null);
assert.equal(responseUsage({ input_tokens: 0, output_tokens: 0 }), null);
assert.equal(liveCost(60), 0.05);
assert.equal(liveCost(-5), 0);

assert.equal(budgetClause({ budget_usd: 80, month_usd: 59 }), null);
assert.equal(budgetClause({ budget_usd: 80, month_usd: 61.4 }), "Dogwalk has used about 61 of its 80 dollar budget this month.");
assert.match(budgetClause({ budget_usd: 80, month_usd: 80.2 })!, /used its 80 dollar budget/);
assert.equal(budgetClause({ budget_usd: null, month_usd: 500 }), null);

assert.equal(monthStart(new Date(Date.UTC(2026, 10, 13, 18))), Date.UTC(2026, 10, 1) / 1000);
console.log("spend_test ok");
