"""Post-build smoke check: core Tuva tables exist and are non-empty.

Relations are resolved from dbt's manifest.json (no hard-coded schema names),
then checked with aggregate COUNT(*) queries only — no row-level reads.
PHI: none (synthetic data; aggregate counts only).

Usage (CI):
    python -m tuva_demo.smoke --manifest <path> --out smoke.json
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tuva_demo.log import get_logger
from tuva_demo.snowflake_conn import connect_from_env

log = get_logger("tuva_demo.smoke")

PACKAGE = "the_tuva_project"
DEFAULT_MODELS: tuple[str, ...] = (
    "core__patient",
    "core__eligibility",
    "core__medical_claim",
    "core__condition",
    "core__encounter",
)


class ManifestNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resource_type: str
    package_name: str
    name: str
    relation_name: str | None = None


class Manifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nodes: dict[str, ManifestNode]


class CheckResult(BaseModel):
    model: str
    relation: str | None
    row_count: int | None = None
    ok: bool
    error: str | None = None


class SmokeReport(BaseModel):
    ok: bool
    min_rows: int = Field(ge=0)
    checks: list[CheckResult]


class Cursor(Protocol):
    def execute(self, sql: str) -> object: ...
    def fetchone(self) -> tuple[object, ...] | None: ...


CountFn = Callable[[str], int]


def resolve_relations(manifest: Manifest, models: Iterable[str]) -> dict[str, str | None]:
    by_name = {
        n.name: n.relation_name
        for n in manifest.nodes.values()
        if n.resource_type == "model" and n.package_name == PACKAGE
    }
    return {m: by_name.get(m) for m in models}


def cursor_count_fn(cursor: Cursor) -> CountFn:
    def _count(relation: str) -> int:
        # relation comes from dbt's manifest (already quoted), never from user input.
        cursor.execute(f"select count(*) from {relation}")
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError(f"no result for count on {relation}")
        return int(row[0])  # type: ignore[arg-type]

    return _count


def run_checks(relations: dict[str, str | None], count: CountFn, min_rows: int = 1) -> SmokeReport:
    checks: list[CheckResult] = []
    for model, relation in relations.items():
        if relation is None:
            checks.append(CheckResult(model=model, relation=None, ok=False, error="not in manifest"))
            log.error("smoke_missing_model", extra={"model": model})
            continue
        try:
            n = count(relation)
        except Exception as exc:  # surface warehouse errors per table, keep checking the rest
            checks.append(CheckResult(model=model, relation=relation, ok=False, error=str(exc)))
            log.error("smoke_query_failed", extra={"model": model, "relation": relation, "error": str(exc)})
            continue
        ok = n >= min_rows
        checks.append(CheckResult(model=model, relation=relation, row_count=n, ok=ok))
        log.info("smoke_count", extra={"model": model, "relation": relation, "rows": n, "ok": ok})
    return SmokeReport(ok=all(c.ok for c in checks) and bool(checks), min_rows=min_rows, checks=checks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--min-rows", type=int, default=1)
    parser.add_argument("--model", action="append", dest="models", help="override model list")
    args = parser.parse_args(argv)

    try:
        manifest = Manifest.model_validate_json(args.manifest.read_bytes())
    except (FileNotFoundError, ValidationError):
        log.exception("manifest_unreadable", extra={"path": str(args.manifest)})
        return 2

    relations = resolve_relations(manifest, args.models or DEFAULT_MODELS)

    try:
        conn = connect_from_env(default_role="TUVA_DEMO_READER", tag_suffix="smoke")
    except Exception:
        log.exception("snowflake_connect_failed")
        return 2
    try:
        report = run_checks(relations, cursor_count_fn(conn.cursor()), args.min_rows)
    finally:
        conn.close()

    args.out.write_text(report.model_dump_json(indent=2))
    log.info("smoke_complete", extra={"ok": report.ok, "checks": len(report.checks)})
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
