# Tuva on Snowflake: production-style demo

Runs [Tuva Core v1.0.0](https://github.com/tuva-health/tuva-core/releases/tag/v1.0.0) against an
isolated Snowflake footprint, with the controls a real deployment would have.

> **Data:** Tuva's bundled **synthetic** claims and clinical data only. No PHI.
> Never point this pipeline at a database holding real patient data.

## What "production-style" means here

| Control | How |
|---|---|
| Isolation | Dedicated `TUVA_DEMO` database, `TUVA_DEMO_WH` warehouse, `TUVA_DEMO_*` roles. No grants on any other database. The workflow refuses to run if the target isn't `TUVA_DEMO`. |
| Least privilege | `TUVA_DEMO_TRANSFORMER` (build) owns only `TUVA_DEMO`. `TUVA_DEMO_READER` is read-only and is what the smoke check and humans use. |
| No passwords | `TUVA_DEMO_SVC` is a `TYPE = SERVICE` user with key-pair auth only. |
| Human approval gate | Builds run only by manual dispatch, inside the `production` GitHub environment with a required reviewer. |
| Audit (who/what/when/why) | Each run requires a written reason. An audit record (actor, commit, Tuva ref, scope, node statuses, reason) goes to the job summary and a 90-day artifact. Every Snowflake statement carries a `QUERY_TAG` with run id, actor and commit. |
| Reproducibility | Tuva pinned to the `v1.0.0` commit SHA. dbt pinned to the exact versions Tuva's own Snowflake CI uses. GitHub Actions pinned by SHA. |
| Cost | X-Small warehouse, 60s auto-suspend, 1-hour statement timeout, 20-credit monthly resource monitor that suspends at 100%. |
| Fail fast | Before dbt starts, a preflight loads one small Tuva synthetic file from S3 into a temporary table, using the same `COPY INTO` pattern Tuva uses. If the account blocks it, the run stops in seconds with the fix, not an hour into the build. |
| Verification | After the build, a read-only smoke check confirms core tables exist and are non-empty. It runs `COUNT(*)` only, with no row-level reads. |

## Layout

```
snowflake/01_setup.sql        one-time admin setup (roles, warehouse, DB, monitor, service user)
snowflake/99_teardown.sql     removes every TUVA_DEMO_* object
profiles/profiles.yml         env-var-only dbt profile (key-pair auth, query tag)
.github/workflows/
  tuva-build.yml              manual, approval-gated production build
  ci.yml                      unit tests + offline dbt parse on every PR (no Snowflake access)
tuva_demo/audit.py            run_results.json -> audit record + job summary
tuva_demo/preflight.py        pre-build check that the account can load Tuva's S3 seeds
tuva_demo/smoke.py            post-build read-only row-count check
tuva_demo/snowflake_conn.py   shared key-pair connection
tests/                        pytest for audit, preflight and smoke
requirements.txt              dbt-core 1.11.14, dbt-snowflake 1.11.6 (Tuva v1.0.0's CI pins)
```

## One-time setup (about 15 minutes)

### 0. Pick the Snowflake account

**Recommended: a dedicated Snowflake trial or sandbox account used only for this demo.**
You're its `ACCOUNTADMIN`, it holds no real data, the settings that can block Tuva's S3 seed loads are off by default,
and deleting it later removes everything. Use a shared company account only if the setup
step 8 load test passes there.

### 1. Generate the service key pair (locally; never commit it)

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -v2 aes-256-cbc -inform PEM -out tuva_demo_svc.p8   # prompts for a passphrase
openssl rsa -in tuva_demo_svc.p8 -pubout -out tuva_demo_svc.pub
grep -v -- '-----' tuva_demo_svc.pub | tr -d '\n'   # public key body for step 2
```

### 2. Run `snowflake/01_setup.sql`

Run it in a Snowflake worksheet as an admin who can use `ACCOUNTADMIN`, `SECURITYADMIN` and `SYSADMIN`.
Paste the public key body into `RSA_PUBLIC_KEY` first.

The last block (step 8) is a real load test. It must return `LOADED` with `rows_loaded > 0`
before you continue. See "S3 seed loading" below if it doesn't.

### 3. Configure GitHub

1. **Settings → Environments → New environment → `production`**
   - Required reviewers: add the approver(s).
   - Deployment branches: `main` only.
2. Add **environment secrets** (in `production`, not repo-level):

   | Secret | Value |
   |---|---|
   | `SNOWFLAKE_ACCOUNT` | account identifier, e.g. `myorg-myaccount` |
   | `SNOWFLAKE_PRIVATE_KEY` | full contents of `tuva_demo_svc.p8`, including the BEGIN/END lines |
   | `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` | the passphrase from step 1 |

3. Optional **environment variables**: `SNOWFLAKE_USER` (default `TUVA_DEMO_SVC`) and
   `SNOWFLAKE_WAREHOUSE` (default `TUVA_DEMO_WH`).

### 4. Run it

**Actions → "Tuva build -- Snowflake (production)" → Run workflow**, then enter a reason and choose:

- `scope`: `core` (synthetic Input Layer + Tuva Core) or `all` (+ all eight data marts)
- `synthetic_data_size`: `small` (default) or `large`
- `data_quality`: build Structural and Logical Data Quality result tables

A reviewer approves the run, and then it builds. The audit summary appears on the run page.

## S3 seed loading

Tuva Core v1.0.0 loads every seed, both synthetic data and terminology, with
`COPY INTO <table> FROM 's3://tuva-public-resources/...'`, with no stage and no credentials.
Some hardened accounts block that pattern, for example with
`REQUIRE_STORAGE_INTEGRATION_FOR_STAGE_OPERATION = TRUE`.
Snowflake's docs don't clearly say whether that parameter covers inline-URL loads, so this repo
tests real behavior instead of reading parameters: setup step 8 and the workflow preflight both
run the same kind of `COPY`.

If the test fails, it's an admin decision:

1. **Recommended:** run the demo in a dedicated sandbox or trial Snowflake account where the
   load is allowed. That keeps your production account's exfiltration controls untouched.
2. Have an `ACCOUNTADMIN` review and relax the blocking setting. Don't do this on an account that
   holds PHI without security sign-off; it weakens a data-exfiltration control account-wide.

## Explore

As `TUVA_DEMO_READER`:

```sql
use role TUVA_DEMO_READER;
use warehouse TUVA_DEMO_WH;
use database TUVA_DEMO;

show schemas;
select count(*) from core.patient;
select count(*) from core.medical_claim;
select count(*) from core.condition;
```

Audit trail inside Snowflake:

```sql
select start_time, user_name, role_name, query_tag, query_type, execution_status
from snowflake.account_usage.query_history
where query_tag like 'tuva-demo|%'
order by start_time desc;
```

## Local development

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

## Upgrading Tuva

Change `TUVA_CORE_REF` in **both** workflows to the new tag's commit SHA. CI fails if they drift.
Then update `requirements.txt` to the dbt versions in that tag's `.github/workflows/ci.yml`.

## Teardown

Run `snowflake/99_teardown.sql` as an admin, then delete the `production` environment secrets.
