-- How a deterministic flow the Voice Agent handed the call to (Re-enrollment, Challenge Practice)
-- ended, as JSON { flow, outcome }: written by the flow as it returns the call to /voice, and
-- read and cleared by the Voice Session when the Voice Agent reconnects, so call_started tells
-- the Session Orchestrator (voice_session.ts, returnedFrom). Never a score, level, or phrase.
ALTER TABLE voice_calls ADD COLUMN returned_from TEXT;
