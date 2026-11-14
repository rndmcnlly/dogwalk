-- Spend accounting (issue #57): an estimate per architectural component (voice_frontend,
-- orchestrator, sandbox_agents) that playtest.py, the Diagnostics Surface, and the Opening total
-- without the OpenAI Platform. call_sid is null for Agent work that runs while no call is attached.
CREATE TABLE IF NOT EXISTS spend (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  component TEXT NOT NULL CHECK (component IN ('voice_frontend', 'orchestrator', 'sandbox_agents', 'baseline')),
  usd REAL NOT NULL,
  sandbox_id TEXT,
  call_sid TEXT,
  detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_spend_ts ON spend(ts);
CREATE INDEX IF NOT EXISTS idx_spend_call ON spend(call_sid) WHERE call_sid IS NOT NULL;

-- The ledger starts mid-month: carry in what the Platform showed for the Dogwalk project and the
-- Default-project key the Sandboxes used (2026-11-01 to 2026-11-13), so this month's estimate
-- and the Opening's budget notice start from the real figure.
INSERT INTO spend (ts, component, usd, detail)
SELECT unixepoch('2026-11-13 18:00:00'), 'baseline', 58.45,
  '{"note":"OpenAI Dogwalk project spend 2026-11-01..13, before the ledger"}'
WHERE NOT EXISTS (SELECT 1 FROM spend WHERE component = 'baseline');
