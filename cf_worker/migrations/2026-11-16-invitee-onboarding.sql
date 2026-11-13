-- How an invitee's Registration went, for the inviter's next call (DOMAIN.md, Invitation;
-- invitations.ts, inviteeNews). Written by the invitee's own call: onboarding_call_sid when their
-- invite words are read back (and again when they register), enrollment_answers as Registration's
-- enrollment collects each answer, enrollment_outcome when it ends (enrolled, stopped, silent,
-- enrollment_failed, unavailable, ...; NULL when the caller hung up mid-enrollment).
-- onboarding_noticed is the sentence the inviter was last told, so each change is told once.
-- Never a phone number, invite words, score, or phrase.
ALTER TABLE invitations ADD COLUMN onboarding_call_sid TEXT;
ALTER TABLE invitations ADD COLUMN enrollment_answers INTEGER NOT NULL DEFAULT 0;
ALTER TABLE invitations ADD COLUMN enrollment_outcome TEXT;
ALTER TABLE invitations ADD COLUMN onboarding_noticed TEXT;
