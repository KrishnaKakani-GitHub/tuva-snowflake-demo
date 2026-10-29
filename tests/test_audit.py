from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from tuva_demo import audit

RUN_RESULTS = {
    "metadata": {
        "dbt_version": "1.11.14",
        "generated_at": "2026-10-29T02:00:00Z",
        "invocation_id": "inv-123",
        "env": {},
    },
    "results": [
        {"unique_id": "model.the_tuva_project.core__patient", "status": "success", "execution_time": 1.2},
        {"unique_id": "test.the_tuva_project.not_null_x", "status": "pass", "execution_time": 0.1},
        {"unique_id": "test.the_tuva_project.unique_y", "status": "fail", "execution_time": 0.1},
        {"unique_id": "model.the_tuva_project.core__cost", "status": "error", "execution_time": 0.3},
        {"unique_id": "model.the_tuva_project.core__member_month", "status": "skipped"},
    ],
    "elapsed_time": 123.456,
    "args": {},
}

ENV = {
    "GITHUB_ACTOR": "alice",
    "GITHUB_TRIGGERING_ACTOR": "alice",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "o/r",
    "GITHUB_RUN_ID": "42",
    "GITHUB_SHA": "abc",
    "TUVA_CORE_REF": "131d12a",
    "BUILD_SCOPE": "core",
    "SYNTHETIC_DATA_SIZE": "small",
    "SNOWFLAKE_DATABASE": "TUVA_DEMO",
    "RUN_REASON": "  demo for leadership  ",
}


@pytest.fixture
def results_path(tmp_path: Path) -> Path:
    p = tmp_path / "run_results.json"
    p.write_text(json.dumps(RUN_RESULTS))
    return p


def test_build_record_captures_who_what_when_why(results_path: Path) -> None:
    now = datetime(2026, 10, 29, 3, tzinfo=timezone.utc)
    rec = audit.build_record(audit.load_run_results(results_path), ENV, now=now)

    assert rec.who.actor == "alice"
    assert rec.why == "demo for leadership"
    assert rec.when.recorded_at == now
    assert rec.when.elapsed_seconds == 123.46
    assert rec.run_url == "https://github.com/o/r/actions/runs/42"
    assert rec.what.status_counts == {"error": 1, "fail": 1, "pass": 1, "skipped": 1, "success": 1}
    assert rec.what.failed_nodes == [
        "model.the_tuva_project.core__cost",
        "test.the_tuva_project.unique_y",
    ]
    assert rec.outcome == "failure"


def test_success_when_nothing_failed(tmp_path: Path) -> None:
    ok = {**RUN_RESULTS, "results": RUN_RESULTS["results"][:2]}
    p = tmp_path / "rr.json"
    p.write_text(json.dumps(ok))
    rec = audit.build_record(audit.load_run_results(p), ENV)
    assert rec.outcome == "success"
    assert rec.what.failed_nodes == []


def test_missing_reason_is_explicit() -> None:
    rr = audit.RunResults.model_validate(RUN_RESULTS)
    rec = audit.build_record(rr, {k: v for k, v in ENV.items() if k != "RUN_REASON"})
    assert rec.why == "not provided"


def test_invalid_run_results_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"results": []}))
    with pytest.raises(ValidationError):
        audit.load_run_results(p)


def test_markdown_truncates_failures() -> None:
    rr = audit.RunResults.model_validate(RUN_RESULTS)
    rec = audit.build_record(rr, ENV)
    md = audit.to_markdown(rec, max_failed=1)
    assert "**failure**" in md
    assert "and 1 more" in md


def test_main_writes_record_and_summary(
    results_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    out = tmp_path / "audit.json"

    assert audit.main(["--run-results", str(results_path), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["who"]["actor"] == "alice"
    assert "Tuva build audit" in summary.read_text()


def test_main_missing_file_returns_2(tmp_path: Path) -> None:
    assert audit.main(["--run-results", str(tmp_path / "nope.json"), "--out", str(tmp_path / "o.json")]) == 2
