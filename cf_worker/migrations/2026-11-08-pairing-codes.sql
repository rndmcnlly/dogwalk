-- Paired Browser: a Pairing Code played into a call, awaiting a browser's claim.
-- Only the code's hash is stored; a claim deletes its row, so each code works once.
CREATE TABLE IF NOT EXISTS pairing_codes (
  code_hash TEXT PRIMARY KEY,
  identity_hash TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);
