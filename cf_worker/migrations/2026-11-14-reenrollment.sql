-- Re-enrollment: a 'reenrollment' handoff sends the Dial action to /voice/reenrollment. SQLite
-- cannot alter a CHECK constraint, and handoffs live only for seconds, so the table is
-- recreated empty.
DROP TABLE IF EXISTS call_handoffs;
CREATE TABLE call_handoffs (
  call_sid TEXT PRIMARY KEY,
  phone_number TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('hangup', 'recovery', 'rejoin', 'donation', 'practice', 'reenrollment')),
  created_at INTEGER NOT NULL
);
