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

-- Invitations: a registered User invites another person by voice. Each Invitation extends one
-- single-use Invite Code row (same words, so the registration ritual is unchanged) with its
-- inviter, an optional bound recipient number, revocation, and hiding. Rows are never deleted:
-- the redeeming Registration (registrations.invite_code = code_words, and redeemed_by) is the
-- inviter -> invitation -> invitee edge.
CREATE TABLE IF NOT EXISTS invitations (
  code_words TEXT PRIMARY KEY REFERENCES invite_codes(code_words),
  inviter_phone TEXT NOT NULL REFERENCES registrations(phone_number),
  recipient_phone TEXT,
  recipient_confirmed_at INTEGER,
  revoked_at INTEGER,
  hidden_at INTEGER,
  redeemed_by TEXT UNIQUE REFERENCES registrations(phone_number),
  redeemed_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_invitations_inviter ON invitations(inviter_phone);

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

-- Kept in provider_timing.sql as an idempotent addition for deployed databases.
-- This queue enriches diagnostics only, never operational call state.
CREATE TABLE IF NOT EXISTS provider_call_jobs (
  account_sid TEXT NOT NULL,
  provider_call_sid TEXT NOT NULL,
  root_call_sid TEXT NOT NULL,
  parent_call_sid TEXT,
  leg TEXT NOT NULL CHECK (leg IN ('pstn', 'sip')),
  next_metadata_at INTEGER,
  metadata_generation INTEGER NOT NULL DEFAULT 0,
  events_after INTEGER,
  events_done INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  lease_until INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (account_sid, provider_call_sid)
);
CREATE INDEX IF NOT EXISTS idx_provider_call_jobs_due
  ON provider_call_jobs(lease_until, next_metadata_at, events_after);
CREATE UNIQUE INDEX IF NOT EXISTS idx_call_activity_twilio_event_key
  ON call_activity(json_extract(detail, '$.provider_event_key'))
  WHERE event LIKE 'provider.twilio.%';

-- One-shot instruction consumed when Twilio resumes after the GPT-Live SIP leg.
CREATE TABLE IF NOT EXISTS call_handoffs (
  call_sid TEXT PRIMARY KEY,
  phone_number TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('hangup', 'recovery', 'rejoin')),
  created_at INTEGER NOT NULL
);

-- Paired Browser: a Pairing Code played into a call, awaiting a browser's claim.
-- Only the code's hash is stored; a claim deletes its row, so each code works once.
CREATE TABLE IF NOT EXISTS pairing_codes (
  code_hash TEXT PRIMARY KEY,
  identity_hash TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);

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
