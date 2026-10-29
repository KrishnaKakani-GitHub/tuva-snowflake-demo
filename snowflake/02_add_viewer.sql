-- =============================================================================
-- Tuva Snowflake demo: add a read-only human viewer
-- =============================================================================
-- Gives one person a Snowflake login that can query TUVA_DEMO and nothing else.
-- Before running: replace DEMO_VIEWER_NAME (3 places), <their email> (2 places)
-- and <temporary password> (1 place). Then Run All (Cmd+Shift+Enter).
-- Share the temporary password separately from the login details.
-- Remove later with:  USE ROLE USERADMIN; DROP USER DEMO_VIEWER_NAME;
-- =============================================================================

-- 1. The login (a human user; must change the password on first sign-in)
USE ROLE USERADMIN;
CREATE USER IF NOT EXISTS DEMO_VIEWER_NAME
  TYPE                 = PERSON
  LOGIN_NAME           = '<their email>'
  EMAIL                = '<their email>'
  PASSWORD             = '<temporary password>'
  MUST_CHANGE_PASSWORD = TRUE
  DEFAULT_ROLE         = TUVA_DEMO_READER
  DEFAULT_WAREHOUSE    = TUVA_DEMO_WH
  DEFAULT_NAMESPACE    = TUVA_DEMO.CORE
  COMMENT              = 'Tuva demo: read-only viewer';

-- 2. Read-only access to the demo database only (no other databases, no writes)
USE ROLE SECURITYADMIN;
GRANT ROLE TUVA_DEMO_READER TO USER DEMO_VIEWER_NAME;

-- 3. Verify: exactly one role, TUVA_DEMO_READER
SHOW GRANTS TO USER DEMO_VIEWER_NAME;
