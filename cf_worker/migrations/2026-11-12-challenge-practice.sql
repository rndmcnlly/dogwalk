-- Challenge Practice: a 'practice' handoff sends the Dial action to /voice/practice. SQLite
-- cannot alter a CHECK constraint, and handoffs live only for seconds, so the table is
-- recreated empty.
DROP TABLE IF EXISTS call_handoffs;
CREATE TABLE call_handoffs (
  call_sid TEXT PRIMARY KEY,
  phone_number TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('hangup', 'recovery', 'rejoin', 'donation', 'practice')),
  created_at INTEGER NOT NULL
);

-- Transient state of one Challenge Practice round, shared by the round's webhooks and its
-- Media Stream: the phrase, the current take (each replay of the phrase on request gets a new
-- stream), whether its transcript matched, and the verifier's result. Never
-- audio or an embedding. A round's row is deleted once its result is spoken, and every row of
-- a call when practice ends.
DROP TABLE IF EXISTS practice_rounds;
CREATE TABLE practice_rounds (
  call_sid TEXT NOT NULL,
  round INTEGER NOT NULL,
  phrase TEXT NOT NULL,
  take INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL CHECK (state IN ('listening', 'scoring', 'scored', 'skipped')),
  streamed INTEGER NOT NULL DEFAULT 0,
  cosine REAL,
  speech_seconds REAL,
  error TEXT,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (call_sid, round)
);
