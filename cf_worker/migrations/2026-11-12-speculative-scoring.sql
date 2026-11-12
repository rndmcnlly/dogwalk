-- Challenge Practice speculative scoring: each round's row keeps the latest score started at
-- the end of the caller's answer (spec_*), when the matched phrase arrived (heard_ms), and
-- whether its score was the speculative one. Rows live only for a round, so the table is
-- recreated empty.
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
  heard_ms INTEGER,
  spec_seq INTEGER,
  spec_started_ms INTEGER,
  spec_cosine REAL,
  spec_speech_seconds REAL,
  spec_error TEXT,
  speculative INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (call_sid, round)
);
