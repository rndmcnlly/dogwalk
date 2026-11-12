-- Pending acoustic codes are ephemeral. Existing paired-browser credentials,
-- stored in Durable Objects, remain valid. Apply before deploying the Worker.
DROP TABLE IF EXISTS pairing_codes;
DROP TABLE IF EXISTS pairing_decode_failures;
CREATE TABLE pairing_codes (
  code_hash TEXT PRIMARY KEY,
  nameplate TEXT NOT NULL UNIQUE,
  identity_hash TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pairing_claim_budget (
  minute INTEGER PRIMARY KEY,
  attempts INTEGER NOT NULL
);
