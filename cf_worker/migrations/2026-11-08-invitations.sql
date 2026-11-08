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
