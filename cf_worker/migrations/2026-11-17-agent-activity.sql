-- OpenCode's native API replaced ACP (issue #28): call_activity's acp source becomes agent, and
-- acp.* event names become agent.*, both on the row and inside voice.timing details, with
-- acp_session_id renamed to agent_session_id. SQLite cannot alter a CHECK constraint, so the
-- table is rebuilt with its indexes. ACP-era rows keep their other fields (rpc_id, stop_reason):
-- the projections leave them as unlinked logs. acp.turn.cancelled, an end the operator timing page
-- once counted, becomes agent.turn.stopped; acp.turn.interrupted stays a detection, never an end,
-- as recorded (it named a turn found cut short, not when the work stopped). The Managed Sessions
-- listed in voice.live.attached rows get the same ID rename.
CREATE TABLE call_activity_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_sid TEXT NOT NULL,
  ts INTEGER NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('voice', 'access', 'hosting', 'menu', 'agent')),
  direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound', 'internal')),
  event TEXT NOT NULL,
  detail TEXT
);

INSERT INTO call_activity_new (id, call_sid, ts, source, direction, event, detail)
SELECT id, call_sid, ts,
  CASE source WHEN 'acp' THEN 'agent' ELSE source END,
  direction,
  CASE WHEN event = 'acp.turn.cancelled' THEN 'agent.turn.stopped'
    WHEN event LIKE 'acp.%' THEN 'agent.' || substr(event, 5) ELSE event END,
  CASE WHEN NOT json_valid(detail) THEN detail
    WHEN json_extract(detail, '$.acp_session_id') IS NOT NULL THEN json_remove(
      json_set(detail, '$.agent_session_id', json_extract(detail, '$.acp_session_id')), '$.acp_session_id')
    WHEN json_extract(detail, '$.event') LIKE 'acp.%' THEN
      json_set(detail, '$.event', 'agent.' || substr(json_extract(detail, '$.event'), 5))
    ELSE detail END
FROM call_activity;

UPDATE call_activity_new SET detail = json_set(detail, '$.managed_sessions', (
  SELECT json_group_array(CASE WHEN json_extract(value, '$.acp_session_id') IS NULL THEN json(value)
    ELSE json(json_remove(json_set(value, '$.agent_session_id', json_extract(value, '$.acp_session_id')), '$.acp_session_id')) END)
  FROM json_each(call_activity_new.detail, '$.managed_sessions')))
WHERE event = 'voice.live.attached' AND json_valid(detail) AND json_type(detail, '$.managed_sessions') = 'array';

DROP TABLE call_activity;
ALTER TABLE call_activity_new RENAME TO call_activity;

CREATE INDEX IF NOT EXISTS idx_call_activity_call
  ON call_activity(call_sid, id);
CREATE INDEX IF NOT EXISTS idx_call_activity_span
  ON call_activity(json_extract(detail, '$.span_id'))
  WHERE json_extract(detail, '$.span_id') IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_call_activity_twilio_event_key
  ON call_activity(json_extract(detail, '$.provider_event_key'))
  WHERE event LIKE 'provider.twilio.%';
