-- User reaping and the Registration cooldown (issue #50, index.ts reapUsers and bindClaim).
-- registered_from is the number a User registered from: the Registration cooldown counts it, and
-- the Diagnostics Surface lists numbers that registered many Users. forgotten_at is set once a
-- reaped User's Voice Session storage is wiped and their call text stripped; until then the
-- daily pass retries that cleanup.
ALTER TABLE users ADD COLUMN registered_from TEXT;
ALTER TABLE users ADD COLUMN forgotten_at INTEGER;
-- The Registration call is never linked to the User it created (voice_calls.user_id), so it is
-- recorded here for forgetting to find.
ALTER TABLE users ADD COLUMN registered_call_sid TEXT;

-- Back-patch: an Invitation recorded the redeeming number; an operator Invite Code left none,
-- so the Home stands in where it is still the User's (a displaced Home stays unknown, and a
-- User who later moved their Home gets the new number: this only skews the busy-numbers view).
UPDATE users SET registered_from = COALESCE(
  (SELECT v.redeemed_from FROM invitations v WHERE v.redeemed_by_user_id = users.id),
  home_phone);
-- The Registration's own audit names its call and number, within moments of registered_at.
UPDATE users SET registered_call_sid = (
  SELECT a.call_sid FROM audit_log a
   WHERE a.event = 'registration.success' AND a.phone_number = users.registered_from
     AND a.ts BETWEEN users.registered_at AND users.registered_at + 60
   ORDER BY a.id LIMIT 1)
WHERE registered_from IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_users_registered_from ON users(registered_from, registered_at);
