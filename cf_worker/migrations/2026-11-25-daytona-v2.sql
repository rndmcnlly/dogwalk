-- Daytona v2: every Sandbox before it is abandoned, not migrated, so each User's next call
-- provisions a fresh one. Apply with the Worker deploy that speaks v2; apply it again if a call
-- reached the old Worker in between (a v0 id names nothing on v2).
--
-- The old Sandboxes stay on v0 for the operator to delete there. Deletions still pending name v0
-- Sandboxes this Worker can no longer reach, so they are closed.
UPDATE sandbox_assignments
   SET provider_id = NULL, state = 'missing', error = NULL, provisioning_started_at = 0, last_checked_at = unixepoch()
 WHERE provider_id IS NOT NULL AND provider_id NOT LIKE 'sbx_%';
UPDATE sandbox_deletions
   SET deleted_at = unixepoch(), last_error = 'abandoned on Daytona v0'
 WHERE deleted_at IS NULL AND provider_id NOT LIKE 'sbx_%';
