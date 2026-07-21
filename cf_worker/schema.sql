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

-- A provider-neutral, create-only capability delivered to one Sandbox
-- incarnation. Only the token hash is retained by Dogwalk.
CREATE TABLE IF NOT EXISTS sandbox_capabilities (
  phone_number TEXT PRIMARY KEY REFERENCES sandbox_assignments(phone_number),
  provider_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  issued_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS review_bundles (
  id TEXT PRIMARY KEY,
  phone_number TEXT NOT NULL REFERENCES sandbox_assignments(phone_number),
  public_token_hash TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  session_id TEXT NOT NULL,
  default_path TEXT NOT NULL,
  file_count INTEGER NOT NULL,
  byte_count INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_bundles_phone
  ON review_bundles(phone_number, created_at DESC);

CREATE TABLE IF NOT EXISTS review_bundle_files (
  bundle_id TEXT NOT NULL REFERENCES review_bundles(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  media_type TEXT NOT NULL,
  byte_count INTEGER NOT NULL,
  content BLOB NOT NULL,
  PRIMARY KEY (bundle_id, path)
);

CREATE TABLE IF NOT EXISTS ephemeral_services (
  id TEXT PRIMARY KEY,
  phone_number TEXT NOT NULL REFERENCES sandbox_assignments(phone_number),
  provider_id TEXT NOT NULL,
  name TEXT NOT NULL,
  port INTEGER NOT NULL,
  session_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_ephemeral_services_phone
  ON ephemeral_services(phone_number, active, updated_at DESC);

-- One-shot instruction consumed when Twilio resumes after a Media Stream.
CREATE TABLE IF NOT EXISTS call_handoffs (
  call_sid TEXT PRIMARY KEY,
  phone_number TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('hangup', 'recovery')),
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sms_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone_number TEXT NOT NULL,
  kind TEXT NOT NULL,
  provider_id TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  error_code TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL DEFAULT 0
);
