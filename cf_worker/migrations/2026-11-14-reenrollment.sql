-- Re-enrollment: a 'reenrollment' handoff sends the Dial action to /voice/reenrollment. SQLite
-- cannot alter a CHECK constraint, so the table is rebuilt, copying any handoff still pending
-- (they live only for seconds, but a call mid-handoff keeps its flow).
CREATE TABLE call_handoffs_next (
  call_sid TEXT PRIMARY KEY,
  phone_number TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('hangup', 'recovery', 'rejoin', 'donation', 'practice', 'reenrollment')),
  created_at INTEGER NOT NULL
);
INSERT INTO call_handoffs_next (call_sid, phone_number, action, created_at)
  SELECT call_sid, phone_number, action, created_at FROM call_handoffs;
DROP TABLE call_handoffs;
ALTER TABLE call_handoffs_next RENAME TO call_handoffs;
