-- Sandboxes taken out of service by reaping (index.ts, reapSandboxes). The pass records a row
-- and clears the Sandbox Assignment in one transaction; only the pass ever deletes the Sandbox at
-- the provider, retrying failures with backoff (attempts, next_attempt_at). The row stays after
-- deleted_at as a tombstone, so a stale label listing is never adopted, and as the notice the
-- User's next Opening consumes (noticed_at).
CREATE TABLE IF NOT EXISTS sandbox_deletions (
  provider_id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  reaped_at INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL,
  last_error TEXT,
  deleted_at INTEGER,
  noticed_at INTEGER
);
