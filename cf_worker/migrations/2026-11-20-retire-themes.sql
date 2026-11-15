-- Themes are retired: nothing sends a 'rejoin' handoff (a Theme switch's bounce to a fresh
-- voice session). SQLite cannot alter a CHECK constraint, so the table is rebuilt, copying
-- any other handoff still pending; a pending rejoin, which lives only for seconds, is dropped.
CREATE TABLE call_handoffs_next (
  call_sid TEXT PRIMARY KEY,
  phone_number TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('hangup', 'recovery', 'donation', 'practice', 'reenrollment')),
  created_at INTEGER NOT NULL
);
INSERT INTO call_handoffs_next (call_sid, phone_number, action, created_at)
  SELECT call_sid, phone_number, action, created_at FROM call_handoffs WHERE action != 'rejoin';
DROP TABLE call_handoffs;
ALTER TABLE call_handoffs_next RENAME TO call_handoffs;
