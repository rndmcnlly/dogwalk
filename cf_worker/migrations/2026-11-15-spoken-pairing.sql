-- Spoken Pairing Code replaces Pairing Tones (README: Paired Browser). Pairing codes live
-- ninety seconds, so the table is recreated rather than altered: any code pending at
-- migration time simply expires early. Decode-failure reports belonged to the tones.
DROP TABLE IF EXISTS pairing_codes;
CREATE TABLE pairing_codes (
  code_hash TEXT PRIMARY KEY,
  nameplate TEXT NOT NULL UNIQUE,
  identity_hash TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0
);

DROP INDEX IF EXISTS idx_pairing_decode_failures_ts;
DROP TABLE IF EXISTS pairing_decode_failures;

CREATE TABLE IF NOT EXISTS pairing_claim_budget (
  minute INTEGER PRIMARY KEY,
  attempts INTEGER NOT NULL
);
