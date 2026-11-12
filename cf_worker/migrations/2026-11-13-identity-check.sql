-- Shadow Identity Check (DOMAIN.md, Identity Check): Challenge Practice, the Identity Check,
-- and first-call enrollment share one round machinery, so practice_rounds becomes
-- challenge_rounds with the ritual and the User each round is scored against. Rows live for
-- one ritual, so the table is recreated empty.
DROP TABLE IF EXISTS practice_rounds;
DROP TABLE IF EXISTS challenge_rounds;
CREATE TABLE challenge_rounds (
  call_sid TEXT NOT NULL,
  round INTEGER NOT NULL,
  ritual TEXT NOT NULL CHECK (ritual IN ('practice', 'identity', 'enrollment')),
  user_id INTEGER NOT NULL,
  phrase TEXT NOT NULL,
  take INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL CHECK (state IN ('listening', 'scoring', 'scored', 'skipped')),
  streamed INTEGER NOT NULL DEFAULT 0,
  cosine REAL,
  speech_seconds REAL,
  error TEXT,
  cold INTEGER,
  embedding_space TEXT,
  embedding TEXT,
  heard_ms INTEGER,
  spec_seq INTEGER,
  spec_started_ms INTEGER,
  spec_cosine REAL,
  spec_speech_seconds REAL,
  spec_error TEXT,
  spec_cold INTEGER,
  spec_embedding_space TEXT,
  spec_embedding TEXT,
  speculative INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (call_sid, round)
);

-- Teach once, then go terse: scored Home Identity Challenges a User has completed.
ALTER TABLE users ADD COLUMN challenges_completed INTEGER NOT NULL DEFAULT 0;

-- How this Voice Call's caller got in (JSON), written once when the Identity Check concludes
-- and read by the Session Orchestrator's Opening.
ALTER TABLE voice_calls ADD COLUMN identity TEXT;
