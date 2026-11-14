-- User reaping and the Registration cooldown (issue #50, index.ts reapUsers and bindClaim).
-- registered_from is the number a User registered from: the Registration cooldown counts it, and
-- the Diagnostics Surface lists numbers that registered many Users. forgotten_at is set once a
-- reaped User's Voice Session storage is wiped and their call text stripped; until then the
-- daily pass retries that cleanup.
ALTER TABLE users ADD COLUMN registered_from TEXT;
ALTER TABLE users ADD COLUMN forgotten_at INTEGER;

-- Back-patch: an Invitation recorded the redeeming number; an operator Invite Code left none,
-- so the Home stands in where it is still the User's (a displaced Home stays unknown).
UPDATE users SET registered_from = COALESCE(
  (SELECT v.redeemed_from FROM invitations v WHERE v.redeemed_by_user_id = users.id),
  home_phone);

CREATE INDEX IF NOT EXISTS idx_users_registered_from ON users(registered_from, registered_at);
