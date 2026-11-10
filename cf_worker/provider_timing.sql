-- Provider enrichment is diagnostic only. All times in this queue are milliseconds.
CREATE TABLE IF NOT EXISTS provider_call_jobs (
  account_sid TEXT NOT NULL,
  provider_call_sid TEXT NOT NULL,
  root_call_sid TEXT NOT NULL,
  parent_call_sid TEXT,
  leg TEXT NOT NULL CHECK (leg IN ('pstn', 'sip')),
  next_metadata_at INTEGER,
  metadata_generation INTEGER NOT NULL DEFAULT 0,
  events_after INTEGER,
  events_done INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  lease_until INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (account_sid, provider_call_sid)
);
CREATE INDEX IF NOT EXISTS idx_provider_call_jobs_due
  ON provider_call_jobs(lease_until, next_metadata_at, events_after);
CREATE UNIQUE INDEX IF NOT EXISTS idx_call_activity_twilio_event_key
  ON call_activity(json_extract(detail, '$.provider_event_key'))
  WHERE event LIKE 'provider.twilio.%';
