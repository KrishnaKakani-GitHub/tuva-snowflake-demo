-- =============================================================================
-- Tuva Snowflake demo: isolated, least-privilege footprint (production-style)
-- =============================================================================
-- Run once, top to bottom, in a Snowflake worksheet by a human admin.
-- Creates ONLY objects prefixed TUVA_DEMO_* and grants nothing on any other
-- database, so the demo cannot read or write existing (e.g. PHI) schemas.
--
-- Data: Tuva's bundled SYNTHETIC claims/clinical data only. No PHI.
--
-- Objects
--   TUVA_DEMO                 database (owned by the transformer role)
--   TUVA_DEMO_WH              X-Small warehouse, 60s auto-suspend
--   TUVA_DEMO_RM              resource monitor (monthly credit cap)
--   TUVA_DEMO_TRANSFORMER     dbt build role (owns TUVA_DEMO only)
--   TUVA_DEMO_READER          read-only role for humans/BI
--   TUVA_DEMO_SVC             key-pair-only service user for GitHub Actions
-- =============================================================================

-- 0. Pre-flight --------------------------------------------------------------
-- Informational. Tuva loads seeds with COPY INTO from a public S3 URL; if either
-- parameter below is TRUE, that MAY be blocked. Step 8 is the real test.
USE ROLE ACCOUNTADMIN;
SHOW PARAMETERS LIKE 'REQUIRE_STORAGE_INTEGRATION_FOR_STAGE_%' IN ACCOUNT;

-- 1. Roles -------------------------------------------------------------------
USE ROLE SECURITYADMIN;

CREATE ROLE IF NOT EXISTS TUVA_DEMO_READER
  COMMENT = 'Tuva demo: read-only access to TUVA_DEMO (synthetic data only)';

CREATE ROLE IF NOT EXISTS TUVA_DEMO_TRANSFORMER
  COMMENT = 'Tuva demo: dbt build role; owns TUVA_DEMO, no other database access';

-- Role hierarchy: transformer inherits reader; SYSADMIN can administer both.
GRANT ROLE TUVA_DEMO_READER      TO ROLE TUVA_DEMO_TRANSFORMER;
GRANT ROLE TUVA_DEMO_TRANSFORMER TO ROLE SYSADMIN;

-- 2. Warehouse + database ----------------------------------------------------
USE ROLE SYSADMIN;

CREATE WAREHOUSE IF NOT EXISTS TUVA_DEMO_WH
  WAREHOUSE_SIZE               = 'XSMALL'
  AUTO_SUSPEND                 = 60
  AUTO_RESUME                  = TRUE
  INITIALLY_SUSPENDED          = TRUE
  STATEMENT_TIMEOUT_IN_SECONDS = 3600
  COMMENT = 'Tuva demo: dedicated compute, isolated from other workloads';

CREATE DATABASE IF NOT EXISTS TUVA_DEMO
  DATA_RETENTION_TIME_IN_DAYS = 1
  COMMENT = 'Tuva demo: synthetic data only. Never load real patient data here.';

GRANT OWNERSHIP ON DATABASE TUVA_DEMO TO ROLE TUVA_DEMO_TRANSFORMER COPY CURRENT GRANTS;
GRANT USAGE, OPERATE ON WAREHOUSE TUVA_DEMO_WH TO ROLE TUVA_DEMO_TRANSFORMER;
GRANT USAGE          ON WAREHOUSE TUVA_DEMO_WH TO ROLE TUVA_DEMO_READER;

-- 3. Reader grants (applies to every schema/table dbt creates later) ---------
USE ROLE SECURITYADMIN;

GRANT USAGE  ON DATABASE TUVA_DEMO                       TO ROLE TUVA_DEMO_READER;
GRANT USAGE  ON FUTURE SCHEMAS IN DATABASE TUVA_DEMO     TO ROLE TUVA_DEMO_READER;
GRANT SELECT ON FUTURE TABLES  IN DATABASE TUVA_DEMO     TO ROLE TUVA_DEMO_READER;
GRANT SELECT ON FUTURE VIEWS   IN DATABASE TUVA_DEMO     TO ROLE TUVA_DEMO_READER;

-- 4. Cost guardrail ----------------------------------------------------------
USE ROLE ACCOUNTADMIN;

CREATE RESOURCE MONITOR IF NOT EXISTS TUVA_DEMO_RM
  WITH CREDIT_QUOTA = 20
       FREQUENCY = MONTHLY
       START_TIMESTAMP = IMMEDIATELY
  TRIGGERS ON 80  PERCENT DO NOTIFY
           ON 100 PERCENT DO SUSPEND
           ON 110 PERCENT DO SUSPEND_IMMEDIATE;

ALTER WAREHOUSE TUVA_DEMO_WH SET RESOURCE_MONITOR = TUVA_DEMO_RM;

-- 5. Service user (key-pair only; TYPE=SERVICE cannot log in with a password)
-- Generate the key pair locally (see README), then paste the PUBLIC key body
-- (no BEGIN/END lines, no newlines) below. The private key goes ONLY into the
-- GitHub environment secret SNOWFLAKE_PRIVATE_KEY.
USE ROLE SECURITYADMIN;

CREATE USER IF NOT EXISTS TUVA_DEMO_SVC
  TYPE              = SERVICE
  DEFAULT_ROLE      = TUVA_DEMO_TRANSFORMER
  DEFAULT_WAREHOUSE = TUVA_DEMO_WH
  RSA_PUBLIC_KEY    = '<PASTE_PUBLIC_KEY_BODY_HERE>'
  COMMENT           = 'Tuva demo: GitHub Actions dbt runner';

GRANT ROLE TUVA_DEMO_TRANSFORMER TO USER TUVA_DEMO_SVC;
-- The post-build smoke check verifies with the read-only role.
GRANT ROLE TUVA_DEMO_READER      TO USER TUVA_DEMO_SVC;

-- 6. Let the admin running this script use the demo roles ---------------------
-- USE ROLE needs the role granted to your user; inheriting it through SYSADMIN
-- is not enough. Step 8 runs as TUVA_DEMO_TRANSFORMER, and you'll explore the
-- results as TUVA_DEMO_READER. (To add another person: same GRANT, their user.)
USE ROLE SECURITYADMIN;
SET me = '"' || CURRENT_USER() || '"';
GRANT ROLE TUVA_DEMO_TRANSFORMER TO USER IDENTIFIER($me);
GRANT ROLE TUVA_DEMO_READER      TO USER IDENTIFIER($me);

-- 7. Verify grants -------------------------------------------------------------
SHOW GRANTS TO ROLE TUVA_DEMO_TRANSFORMER;
SHOW GRANTS TO ROLE TUVA_DEMO_READER;
DESC USER TUVA_DEMO_SVC;

-- 8. Verify Tuva can load its seed data (MUST pass before you set up GitHub) ---
-- Same COPY pattern Tuva Core uses for every seed: inline s3:// URL, no stage,
-- no credentials. Expect one row with status = LOADED and rows_loaded > 0.
-- If this errors mentioning a storage integration, see README > "S3 seed loading".
USE ROLE TUVA_DEMO_TRANSFORMER;
USE WAREHOUSE TUVA_DEMO_WH;
CREATE SCHEMA IF NOT EXISTS TUVA_DEMO.TUVA_PREFLIGHT;
CREATE OR REPLACE TEMPORARY TABLE TUVA_DEMO.TUVA_PREFLIGHT.S3_ACCESS_CHECK (c1 VARCHAR);
-- Mirrors the_tuva_project.snowflake__load_seed: no transformation subquery,
-- unquoted s3:// folder + PATTERN. The 1-column table keeps only the first
-- field of each row (ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE).
COPY INTO TUVA_DEMO.TUVA_PREFLIGHT.S3_ACCESS_CHECK
  FROM s3://tuva-public-resources/tuva-core/1.0.0/synthetic-data/small
  FILE_FORMAT = (TYPE = CSV COMPRESSION = GZIP SKIP_HEADER = 1
                 EMPTY_FIELD_AS_NULL = TRUE
                 FIELD_OPTIONALLY_ENCLOSED_BY = '"'
                 ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE)
  PATTERN = '.*/synthetic_data__appointment[.]csv[.]gz'
  ON_ERROR = ABORT_STATEMENT;
