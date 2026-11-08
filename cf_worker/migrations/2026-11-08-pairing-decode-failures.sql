-- Paired Browser: anonymous reports from pairing pages that heard Pairing Tones but could
-- not decode them. Nothing ties a report to a call; ts is epoch milliseconds. The Worker
-- keeps only the newest rows (DECODE_FAILURE_KEEP_ROWS).
CREATE TABLE IF NOT EXISTS pairing_decode_failures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  stage TEXT NOT NULL CHECK (stage IN ('ecc_failed', 'ecc_unverified')),
  baud INTEGER,
  seconds REAL,
  symbols_heard INTEGER,
  erasures INTEGER
);

CREATE INDEX IF NOT EXISTS idx_pairing_decode_failures_ts ON pairing_decode_failures(ts);
