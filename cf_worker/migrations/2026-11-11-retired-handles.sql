-- Handles a User left behind by renaming (or, later, by being reaped). A retired Handle is
-- not offered again until HANDLE_QUARANTINE_SECONDS (handles.ts) have passed, so a friend
-- saying someone's old name reaches nobody for a while.
CREATE TABLE IF NOT EXISTS retired_handles (
  handle TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  retired_at INTEGER NOT NULL
);
