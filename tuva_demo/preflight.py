"""Preflight: prove this Snowflake account can load Tuva's public S3 seed files.

Tuva Core loads every seed (synthetic data and terminology) with
`COPY INTO <table> FROM s3://tuva-public-resources/...` and no stage or
credentials. Hardened accounts can block that (for example
REQUIRE_STORAGE_INTEGRATION_FOR_STAGE_OPERATION = TRUE). Rather than guess from
parameters, this runs the same kind of COPY on one small synthetic file into a
temporary table and fails within seconds, before a long dbt build starts.

PHI: none. Synthetic file, temporary table, aggregate result only.

Usage (CI):
    python -m tuva_demo.preflight --out preflight.json
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from tuva_demo.log import get_logger
from tuva_demo.snowflake_conn import connect_from_env

log = get_logger("tuva_demo.preflight")

DEFAULT_BUCKET = "tuva-public-resources"
DEFAULT_ASSET_VERSION = "1.0.0"
PROBE_SEED = "synthetic_data__appointment"
PREFLIGHT_SCHEMA = "TUVA_PREFLIGHT"

REMEDIATION = (
    "This Snowflake account blocks COPY INTO from an inline s3:// URL, which Tuva Core "
    "uses for every seed. Fix (admin decision): (1) recommended: run the demo in a "
    "dedicated sandbox/trial account where this is allowed, keeping your production "
    "account's exfiltration controls intact; or (2) have an ACCOUNTADMIN review and set "
    "REQUIRE_STORAGE_INTEGRATION_FOR_STAGE_OPERATION = FALSE. Do not do (2) on an "
    "account that holds PHI without your security team's sign-off. See README."
)


class Cursor(Protocol):
    description: Sequence[Sequence[object]] | None

    def execute(self, sql: str) -> object: ...
    def fetchall(self) -> list[tuple[object, ...]]: ...


class PreflightResult(BaseModel):
    ok: bool
    probe_uri: str
    files_loaded: int = 0
    rows_loaded: int = 0
    error: str | None = None
    remediation: str | None = None


def probe_uri(size: str, bucket: str = DEFAULT_BUCKET, version: str = DEFAULT_ASSET_VERSION) -> str:
    return f"{bucket}/tuva-core/{version}/synthetic-data/{size}"


def build_statements(uri: str, database: str) -> list[str]:
    schema = f"{database}.{PREFLIGHT_SCHEMA}"
    return [
        f"create schema if not exists {schema} comment = 'Tuva demo preflight scratch space'",
        f"create or replace temporary table {schema}.S3_ACCESS_CHECK (c1 varchar)",
        # Same shape as the_tuva_project.snowflake__load_seed: inline s3 URL, no credentials.
        f"""copy into {schema}.S3_ACCESS_CHECK (c1)
from (select $1 from 's3://{uri}/')
file_format = (type = csv compression = gzip skip_header = 1
               field_optionally_enclosed_by = '"' error_on_column_count_mismatch = false)
pattern = '.*/{PROBE_SEED}[.]csv[.]gz'
on_error = abort_statement""",
    ]


def summarize_copy(columns: list[str], rows: list[tuple[object, ...]]) -> tuple[int, int]:
    """Return (files_loaded, rows_loaded) from a COPY INTO result set."""
    cols = [c.lower() for c in columns]
    if "status" not in cols or "rows_loaded" not in cols:
        return 0, 0  # e.g. "Copy executed with 0 files processed."
    si, ri = cols.index("status"), cols.index("rows_loaded")
    files = sum(1 for r in rows if str(r[si]).upper() == "LOADED")
    loaded = sum(int(r[ri] or 0) for r in rows)  # type: ignore[call-overload]
    return files, loaded


def run_preflight(cursor: Cursor, uri: str, database: str) -> PreflightResult:
    *setup, copy_sql = build_statements(uri, database)
    try:
        for sql in setup:
            cursor.execute(sql)
        cursor.execute(copy_sql)
        columns = [str(d[0]) for d in (cursor.description or [])]
        files, rows = summarize_copy(columns, cursor.fetchall())
    except Exception as exc:
        msg = str(exc)
        blocked = "integration" in msg.lower() or "inline" in msg.lower()
        log.error("preflight_copy_failed", extra={"uri": uri, "error": msg, "blocked_by_policy": blocked})
        return PreflightResult(
            ok=False, probe_uri=uri, error=msg, remediation=REMEDIATION if blocked else None
        )

    if files == 0 or rows == 0:
        log.error("preflight_no_rows", extra={"uri": uri, "files": files, "rows": rows})
        return PreflightResult(
            ok=False,
            probe_uri=uri,
            files_loaded=files,
            rows_loaded=rows,
            error="COPY ran but loaded nothing; the S3 path or network egress may be blocked.",
            remediation=REMEDIATION,
        )

    log.info("preflight_ok", extra={"uri": uri, "files": files, "rows": rows})
    return PreflightResult(ok=True, probe_uri=uri, files_loaded=files, rows_loaded=rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--size", choices=["small", "large"], default="small")
    parser.add_argument("--database", default="TUVA_DEMO")
    args = parser.parse_args(argv)

    uri = probe_uri(args.size)
    try:
        conn = connect_from_env(default_role="TUVA_DEMO_TRANSFORMER", tag_suffix="preflight")
    except Exception:
        log.exception("snowflake_connect_failed")
        return 2
    try:
        result = run_preflight(conn.cursor(), uri, args.database)
    finally:
        conn.close()

    args.out.write_text(result.model_dump_json(indent=2))
    if not result.ok and result.remediation:
        print(f"::error title=Snowflake cannot load Tuva seeds::{result.remediation}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
