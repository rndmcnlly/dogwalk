-- Sandbox deletions the reaping pass decided (index.ts, reapSandboxes) and Daytona has not yet
-- confirmed. A row outlives its Sandbox Assignment's reference: a call that could not finish the
-- deletion moves on to a fresh Sandbox, and the pass keeps retrying until Daytona accepts the
-- DELETE or answers 404. attempts counts the pass's failures; calls never count or give up.
CREATE TABLE IF NOT EXISTS sandbox_deletions (
  provider_id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  requested_at INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);
