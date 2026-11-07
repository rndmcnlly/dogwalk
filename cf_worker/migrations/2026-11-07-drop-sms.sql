-- Retire SMS and every feature whose only delivery path was SMS (Review
-- Bundles, Ephemeral Services, Scoped Diagnostic Views, and the sandbox
-- capability that served the publication APIs). Dogwalk is voice-only.
DROP TABLE IF EXISTS review_bundle_files;
DROP TABLE IF EXISTS review_bundles;
DROP TABLE IF EXISTS ephemeral_services;
DROP TABLE IF EXISTS diagnostic_views;
DROP TABLE IF EXISTS sms_log;
DROP TABLE IF EXISTS sandbox_capabilities;
