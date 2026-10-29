"""Turn a dbt run_results.json into an audit record (who / what / when / why).

Usage (CI):
    python -m tuva_demo.audit --run-results <path> --out audit.json

Reads actor/run metadata from the GitHub Actions environment. Writes the
record as JSON and appends a Markdown summary to $GITHUB_STEP_SUMMARY when set.
PHI: none. Inputs are dbt execution metadata only.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tuva_demo.log import get_logger

log = get_logger("tuva_demo.audit")

FAILING_STATUSES: frozenset[str] = frozenset({"error", "fail", "runtime error"})


class NodeResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    unique_id: str
    status: str
    execution_time: float = 0.0
    message: str | None = None


class RunMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dbt_version: str
    generated_at: datetime
    invocation_id: str | None = None


class RunResults(BaseModel):
    model_config = ConfigDict(extra="ignore")

    metadata: RunMetadata
    results: list[NodeResult]
    elapsed_time: float = 0.0


class Who(BaseModel):
    actor: str
    triggering_actor: str | None = None


class What(BaseModel):
    repository: str | None = None
    commit_sha: str | None = None
    tuva_core_ref: str | None = None
    scope: str | None = None
    synthetic_data_size: str | None = None
    target_database: str | None = None
    status_counts: dict[str, int]
    failed_nodes: list[str]


class When(BaseModel):
    dbt_generated_at: datetime
    recorded_at: datetime
    elapsed_seconds: float


class AuditRecord(BaseModel):
    """One record per agent/pipeline action, matching the who/what/when/why rule."""

    schema_version: Literal[1] = 1
    run_id: str | None = None
    run_url: str | None = None
    dbt_invocation_id: str | None = None
    who: Who
    what: What
    when: When
    why: str = Field(min_length=1)
    outcome: Literal["success", "failure"]


def load_run_results(path: Path) -> RunResults:
    try:
        return RunResults.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        log.error("run_results_missing", extra={"path": str(path)})
        raise
    except ValidationError:
        log.exception("run_results_invalid", extra={"path": str(path)})
        raise


def build_record(
    run_results: RunResults,
    env: dict[str, str],
    *,
    now: datetime | None = None,
) -> AuditRecord:
    counts = Counter(r.status for r in run_results.results)
    failed = sorted(r.unique_id for r in run_results.results if r.status in FAILING_STATUSES)
    server = env.get("GITHUB_SERVER_URL")
    repo = env.get("GITHUB_REPOSITORY")
    run_id = env.get("GITHUB_RUN_ID")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else None

    return AuditRecord(
        run_id=run_id,
        run_url=run_url,
        dbt_invocation_id=run_results.metadata.invocation_id,
        who=Who(
            actor=env.get("GITHUB_ACTOR", "unknown"),
            triggering_actor=env.get("GITHUB_TRIGGERING_ACTOR"),
        ),
        what=What(
            repository=repo,
            commit_sha=env.get("GITHUB_SHA"),
            tuva_core_ref=env.get("TUVA_CORE_REF"),
            scope=env.get("BUILD_SCOPE"),
            synthetic_data_size=env.get("SYNTHETIC_DATA_SIZE"),
            target_database=env.get("SNOWFLAKE_DATABASE"),
            status_counts=dict(sorted(counts.items())),
            failed_nodes=failed,
        ),
        when=When(
            dbt_generated_at=run_results.metadata.generated_at,
            recorded_at=now or datetime.now(timezone.utc),
            elapsed_seconds=round(run_results.elapsed_time, 2),
        ),
        why=env.get("RUN_REASON", "").strip() or "not provided",
        outcome="failure" if failed else "success",
    )


def to_markdown(record: AuditRecord, max_failed: int = 25) -> str:
    counts = ", ".join(f"{k}: {v}" for k, v in record.what.status_counts.items()) or "none"
    lines = [
        "## Tuva build audit",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Outcome | **{record.outcome}** |",
        f"| Who | `{record.who.actor}` |",
        f"| Why | {record.why} |",
        f"| When (UTC) | {record.when.dbt_generated_at.isoformat()} |",
        f"| Elapsed | {record.when.elapsed_seconds}s |",
        f"| Scope / size | {record.what.scope} / {record.what.synthetic_data_size} |",
        f"| Commit / Tuva ref | `{record.what.commit_sha}` / `{record.what.tuva_core_ref}` |",
        f"| Node statuses | {counts} |",
    ]
    if record.what.failed_nodes:
        lines += ["", "### Failed nodes", ""]
        lines += [f"- `{n}`" for n in record.what.failed_nodes[:max_failed]]
        extra = len(record.what.failed_nodes) - max_failed
        if extra > 0:
            lines.append(f"- … and {extra} more (see run_results.json artifact)")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        results = load_run_results(args.run_results)
    except (FileNotFoundError, ValidationError):
        return 2

    record = build_record(results, dict(os.environ))
    args.out.write_text(record.model_dump_json(indent=2))

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(to_markdown(record))

    log.info(
        "audit_recorded",
        extra={
            "outcome": record.outcome,
            "actor": record.who.actor,
            "run_id": record.run_id,
            "failed": len(record.what.failed_nodes),
            "out": str(args.out),
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
