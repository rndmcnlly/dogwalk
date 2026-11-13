-- Dogwalk phone registration schema.

-- NULL expires_at means the invite never expires. NULL max_uses means it may
-- register any number of phones; otherwise max_uses must be positive.
CREATE TABLE IF NOT EXISTS invite_codes (
  code_words TEXT PRIMARY KEY,
  minted_at  INTEGER NOT NULL DEFAULT (unixepoch()),
  expires_at INTEGER,
  max_uses   INTEGER CHECK (max_uses IS NULL OR max_uses > 0)
);

-- A User (DOMAIN.md, Access Control): a spoken Handle, an opaque identity_hash that keys the
-- User's Durable Objects and provider label, Registration provenance (invite_code,
-- registered_at), and at most one Home number, which is Home to at most one User. A reaped
-- User keeps its row as provenance with handle and home_phone cleared.
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  handle TEXT UNIQUE,
  identity_hash TEXT NOT NULL UNIQUE,
  invite_code TEXT NOT NULL REFERENCES invite_codes(code_words),
  registered_at INTEGER NOT NULL,
  last_seen_at INTEGER,
  home_phone TEXT UNIQUE,
  home_used_at INTEGER,
  reaped_at INTEGER,
  -- Scored Home Identity Challenges, so the ritual is taught in full only the first few times.
  challenges_completed INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_users_invite ON users(invite_code);

-- Invitations: a User invites another person by voice. Each Invitation extends one
-- single-use Invite Code row (same words, so the registration ritual is unchanged) with its
-- inviter, an optional bound recipient number, revocation, and hiding. Rows are never deleted:
-- redeemed_by_user_id is the inviter -> invitation -> invitee edge, and redeemed_from the
-- number that redeemed it.
CREATE TABLE IF NOT EXISTS invitations (
  code_words TEXT PRIMARY KEY REFERENCES invite_codes(code_words),
  inviter_user_id INTEGER NOT NULL REFERENCES users(id),
  recipient_phone TEXT,
  recipient_confirmed_at INTEGER,
  revoked_at INTEGER,
  hidden_at INTEGER,
  redeemed_by_user_id INTEGER UNIQUE REFERENCES users(id),
  redeemed_from TEXT,
  redeemed_at INTEGER,
  -- How the invitee's Registration went, for the inviter's next call (invitations.ts, inviteeNews).
  onboarding_call_sid TEXT,
  enrollment_answers INTEGER NOT NULL DEFAULT 0,
  enrollment_outcome TEXT,
  onboarding_noticed TEXT
);

CREATE INDEX IF NOT EXISTS idx_invitations_inviter ON invitations(inviter_user_id);

-- Agent Hosting projection, separate from Registration. provider_id is NULL while one caller
-- owns provisioning; the provider label derives from users.identity_hash.
CREATE TABLE IF NOT EXISTS sandbox_assignments (
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  provider TEXT NOT NULL DEFAULT 'daytona' CHECK (provider = 'daytona'),
  provider_id TEXT UNIQUE,
  state TEXT NOT NULL DEFAULT 'provisioning',
  error TEXT,
  provisioning_started_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  last_checked_at INTEGER
);

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

-- Handles a User left behind by renaming (or, later, by being reaped). A retired Handle is
-- not offered again until HANDLE_QUARANTINE_SECONDS (handles.ts) have passed.
CREATE TABLE IF NOT EXISTS retired_handles (
  handle TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  retired_at INTEGER NOT NULL
);

-- A User's enrolled speaker embedding (JSON array), never audio, tagged with the embedding
-- space (weights, preprocessing, normalization) that produced it.
CREATE TABLE IF NOT EXISTS voice_references (
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  embedding_space TEXT NOT NULL,
  embedding TEXT NOT NULL,
  enrolled_at INTEGER NOT NULL
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
  duration_seconds INTEGER,
  -- W3C trace context minted when the call begins; see migrations/2026-11-10-voice-call-trace.sql.
  trace_id TEXT,
  span_id TEXT,
  -- The call's User once known; a roaming call's number will not identify it.
  user_id INTEGER REFERENCES users(id),
  -- How the caller got in (JSON), written once when the Identity Check concludes; the
  -- Opening tells the Session Orchestrator. See identity_check.ts.
  identity TEXT,
  -- How a flow the call was handed to ended (JSON { flow, outcome }), until the Voice Agent
  -- reconnects and call_started reports it. See migrations/2026-11-15-returned-from.sql.
  returned_from TEXT
);

CREATE INDEX IF NOT EXISTS idx_voice_calls_user ON voice_calls(user_id, started_at);
CREATE INDEX IF NOT EXISTS idx_voice_calls_live
  ON voice_calls(ended_at, last_activity_at);

CREATE TABLE IF NOT EXISTS call_activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_sid TEXT NOT NULL,
  ts INTEGER NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('voice', 'access', 'hosting', 'menu', 'agent')),
  direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound', 'internal')),
  event TEXT NOT NULL,
  detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_call_activity_call
  ON call_activity(call_sid, id);
-- Lets the OTLP export complete spans whose end was observed under a later call.
CREATE INDEX IF NOT EXISTS idx_call_activity_span
  ON call_activity(json_extract(detail, '$.span_id'))
  WHERE json_extract(detail, '$.span_id') IS NOT NULL;

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
  action TEXT NOT NULL CHECK (action IN ('hangup', 'recovery', 'rejoin', 'donation', 'practice', 'reenrollment')),
  created_at INTEGER NOT NULL
);

-- Transient state of one challenge round (challenge_rounds.ts) of Challenge Practice, the
-- Identity Check, or enrollment, shared by the round's webhooks and its Media Stream: the
-- phrase, the User it is scored against or enrolls, the current take (each replay of the
-- phrase on request gets a new stream), whether its transcript matched, and the verifier's
-- result. The spec_ columns hold the latest speculative score, started at the end of the answer
-- before the transcript arrived (discarded unused unless the phrase matched); heard_ms and
-- speculative record whether it was used and its head start. Never audio. An enrollment round
-- keeps its answer's embedding until the Voice Reference is computed; every row of a ritual is
-- deleted when the ritual ends.
CREATE TABLE IF NOT EXISTS challenge_rounds (
  call_sid TEXT NOT NULL,
  round INTEGER NOT NULL,
  ritual TEXT NOT NULL CHECK (ritual IN ('practice', 'identity', 'enrollment')),
  user_id INTEGER NOT NULL,
  phrase TEXT NOT NULL,
  take INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL CHECK (state IN ('listening', 'scoring', 'scored', 'skipped')),
  streamed INTEGER NOT NULL DEFAULT 0,
  cosine REAL,
  speech_seconds REAL,
  error TEXT,
  cold INTEGER,
  embedding_space TEXT,
  embedding TEXT,
  heard_ms INTEGER,
  spec_seq INTEGER,
  spec_started_ms INTEGER,
  spec_cosine REAL,
  spec_speech_seconds REAL,
  spec_error TEXT,
  spec_cold INTEGER,
  spec_embedding_space TEXT,
  spec_embedding TEXT,
  speculative INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (call_sid, round)
);

-- Roaming attempts while Roaming admission is on (identity_check.ts): one per Voice Call and
-- named User, written before the first challenge. failures counts unmatched voices on matched
-- phrases (never wrong phrases or thin speech); recent failures pause Roaming for the Handle
-- and from the number. The User's next Home Opening reports them (noticed_at). Rows older than
-- 90 days are pruned.
CREATE TABLE IF NOT EXISTS roaming_attempts (
  call_sid TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id),
  phone_number TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  failures INTEGER NOT NULL DEFAULT 0,
  admitted_at INTEGER,
  noticed_at INTEGER,
  PRIMARY KEY (call_sid, user_id)
);

CREATE INDEX IF NOT EXISTS idx_roaming_attempts_user ON roaming_attempts(user_id, started_at);
CREATE INDEX IF NOT EXISTS idx_roaming_attempts_phone ON roaming_attempts(phone_number, started_at);

-- Spoken Pairing Code: public rendezvous, hashed secret, bounded online guesses.
CREATE TABLE IF NOT EXISTS pairing_codes (
  code_hash TEXT PRIMARY KEY,
  nameplate TEXT NOT NULL UNIQUE,
  identity_hash TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0
);

-- Global fixed-minute budget, shared across Worker isolates and source addresses.
CREATE TABLE IF NOT EXISTS pairing_claim_budget (
  minute INTEGER PRIMARY KEY,
  attempts INTEGER NOT NULL
);

-- Spend accounting (issue #57, spend.ts): an estimate per architectural component, priced by the
-- Worker from what it sees. call_sid is null for Agent work while no call is attached.
CREATE TABLE IF NOT EXISTS spend (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  component TEXT NOT NULL CHECK (component IN ('voice_frontend', 'orchestrator', 'sandbox_agents', 'baseline')),
  usd REAL NOT NULL,
  sandbox_id TEXT,
  call_sid TEXT,
  detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_spend_ts ON spend(ts);
CREATE INDEX IF NOT EXISTS idx_spend_call ON spend(call_sid) WHERE call_sid IS NOT NULL;
