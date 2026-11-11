-- W3C trace context for each Voice Call, minted at the front door when the call begins
-- (issue #8, B1). Calls from before this migration keep NULL and export derived IDs.
ALTER TABLE voice_calls ADD COLUMN trace_id TEXT;
ALTER TABLE voice_calls ADD COLUMN span_id TEXT;

-- Lets the OTLP export complete spans whose end was observed under a later call.
CREATE INDEX IF NOT EXISTS idx_call_activity_span
  ON call_activity(json_extract(detail, '$.span_id'))
  WHERE json_extract(detail, '$.span_id') IS NOT NULL;
