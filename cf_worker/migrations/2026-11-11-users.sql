-- Users with Handles and Homes (DOMAIN.md, Access Control). A phone number stops being the
-- User's identity: Registration becomes provenance columns on users, the number becomes the
-- User's Home, and invitations and Sandbox Assignments refer to the User. identity_hash is
-- copied from the existing Sandbox Assignment, so each VoiceSession and TerminalRelay Durable
-- Object, its Sandbox, and its pairing tokens keep their keys; a User without one gets a
-- random value. Handles are assigned afterwards by a one-time operator command (README), so
-- no Handle appears in this public file. Also relabel each existing Daytona Sandbox from
-- phone-hmac-v1:<hash> to identity-v1:<hash>, which findDaytonaSandboxes now looks for.
PRAGMA defer_foreign_keys = true;

CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  handle TEXT UNIQUE,
  identity_hash TEXT NOT NULL UNIQUE,
  invite_code TEXT NOT NULL REFERENCES invite_codes(code_words),
  registered_at INTEGER NOT NULL,
  last_seen_at INTEGER,
  home_phone TEXT UNIQUE,
  home_used_at INTEGER,
  reaped_at INTEGER
);

INSERT INTO users (identity_hash, invite_code, registered_at, last_seen_at, home_phone, home_used_at)
SELECT COALESCE(s.identity_hash, lower(hex(randomblob(32)))), r.invite_code, r.registered_at,
       r.last_seen_at, r.phone_number, COALESCE(r.last_seen_at, r.registered_at)
  FROM registrations r LEFT JOIN sandbox_assignments s ON s.phone_number = r.phone_number
 ORDER BY r.registered_at;

CREATE TABLE invitations_new (
  code_words TEXT PRIMARY KEY REFERENCES invite_codes(code_words),
  inviter_user_id INTEGER NOT NULL REFERENCES users(id),
  recipient_phone TEXT,
  recipient_confirmed_at INTEGER,
  revoked_at INTEGER,
  hidden_at INTEGER,
  redeemed_by_user_id INTEGER UNIQUE REFERENCES users(id),
  redeemed_from TEXT,
  redeemed_at INTEGER
);

INSERT INTO invitations_new
SELECT v.code_words, inviter.id, v.recipient_phone, v.recipient_confirmed_at, v.revoked_at, v.hidden_at,
       invitee.id, v.redeemed_by, v.redeemed_at
  FROM invitations v
  JOIN users inviter ON inviter.home_phone = v.inviter_phone
  LEFT JOIN users invitee ON invitee.home_phone = v.redeemed_by;

DROP TABLE invitations;
ALTER TABLE invitations_new RENAME TO invitations;
CREATE INDEX idx_invitations_inviter ON invitations(inviter_user_id);

CREATE TABLE sandbox_assignments_new (
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  provider TEXT NOT NULL DEFAULT 'daytona' CHECK (provider = 'daytona'),
  provider_id TEXT UNIQUE,
  state TEXT NOT NULL DEFAULT 'provisioning',
  error TEXT,
  provisioning_started_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  last_checked_at INTEGER
);

INSERT INTO sandbox_assignments_new
SELECT u.id, s.provider, s.provider_id, s.state, s.error, s.provisioning_started_at, s.created_at, s.last_checked_at
  FROM sandbox_assignments s JOIN users u ON u.home_phone = s.phone_number;

DROP TABLE sandbox_assignments;
ALTER TABLE sandbox_assignments_new RENAME TO sandbox_assignments;

DROP TABLE registrations;

-- A Voice Call's User, once known; a roaming call's number will not identify it.
ALTER TABLE voice_calls ADD COLUMN user_id INTEGER REFERENCES users(id);
UPDATE voice_calls SET user_id = (SELECT id FROM users WHERE home_phone = voice_calls.phone_number);
CREATE INDEX idx_voice_calls_user ON voice_calls(user_id, started_at);

CREATE TABLE voice_references (
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  embedding_space TEXT NOT NULL,
  embedding TEXT NOT NULL,
  enrolled_at INTEGER NOT NULL
);
