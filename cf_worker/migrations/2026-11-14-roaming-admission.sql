-- Roaming admission (DOMAIN.md, Roaming; identity_check.ts): the attempts that pause Roaming
-- per Handle and per calling number, and that the User's next Home Opening reports. Written
-- only while ROAMING_ADMISSION is "on", so applying this changes nothing until then.
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
