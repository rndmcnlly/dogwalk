-- Dogwalk phone registration schema.

-- NULL expires_at means the invite never expires. NULL max_uses means it may
-- register any number of phones; otherwise max_uses must be positive.
CREATE TABLE IF NOT EXISTS invite_codes (
  code_words TEXT PRIMARY KEY,
  minted_at  INTEGER NOT NULL DEFAULT (unixepoch()),
  expires_at INTEGER,
  max_uses   INTEGER CHECK (max_uses IS NULL OR max_uses > 0)
);

-- A phone number is the User's identity at the PSTN boundary. Each number can
-- register once, while one invite may authorize multiple registrations.
CREATE TABLE IF NOT EXISTS registrations (
  phone_number TEXT PRIMARY KEY,
  invite_code  TEXT NOT NULL REFERENCES invite_codes(code_words),
  registered_at INTEGER NOT NULL,
  last_seen_at  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_registrations_invite ON registrations(invite_code);

-- Agent Hosting projection, separate from access Registration. provider_id is
-- NULL while one caller owns provisioning; identity_hash is an HMAC-derived,
-- non-reversible provider label.
CREATE TABLE IF NOT EXISTS sandbox_assignments (
  phone_number TEXT PRIMARY KEY REFERENCES registrations(phone_number),
  provider TEXT NOT NULL DEFAULT 'daytona' CHECK (provider = 'daytona'),
  provider_id TEXT UNIQUE,
  identity_hash TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL DEFAULT 'provisioning',
  error TEXT,
  provisioning_started_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  last_checked_at INTEGER
);

-- Transient state between speech recognition and confirmation.
CREATE TABLE IF NOT EXISTS claim_attempt (
  call_sid     TEXT PRIMARY KEY,
  phone_number TEXT NOT NULL,
  code_words   TEXT NOT NULL,
  ts           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           INTEGER NOT NULL,
  event        TEXT NOT NULL,
  phone_number TEXT,
  call_sid     TEXT,
  detail       TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_phone ON audit_log(phone_number);

CREATE TABLE IF NOT EXISTS voice_calls (
  call_sid TEXT PRIMARY KEY,
  phone_number TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  last_activity_at INTEGER NOT NULL,
  ended_at INTEGER,
  duration_seconds INTEGER
);

CREATE INDEX IF NOT EXISTS idx_voice_calls_live
  ON voice_calls(ended_at, last_activity_at);

CREATE TABLE IF NOT EXISTS call_activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_sid TEXT NOT NULL,
  ts INTEGER NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('voice', 'access', 'hosting', 'menu', 'acp')),
  direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound', 'internal')),
  event TEXT NOT NULL,
  detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_call_activity_call
  ON call_activity(call_sid, id);
