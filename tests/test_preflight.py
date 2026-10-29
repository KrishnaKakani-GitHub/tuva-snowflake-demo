from __future__ import annotations

from collections.abc import Sequence

from tuva_demo import preflight

COPY_COLS = ["file", "status", "rows_parsed", "rows_loaded", "error_limit", "errors_seen"]


class FakeCursor:
    def __init__(
        self,
        copy_rows: list[tuple[object, ...]] | None = None,
        copy_cols: list[str] | None = None,
        fail_with: str | None = None,
    ) -> None:
        self.copy_rows = copy_rows or []
        self.copy_cols = copy_cols if copy_cols is not None else COPY_COLS
        self.fail_with = fail_with
        self.sql: list[str] = []
        self.description: Sequence[Sequence[object]] | None = None

    def execute(self, sql: str) -> object:
        self.sql.append(sql)
        if sql.lstrip().lower().startswith("copy into"):
            if self.fail_with:
                raise RuntimeError(self.fail_with)
            self.description = [(c,) for c in self.copy_cols]
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.copy_rows


URI = preflight.probe_uri("small")


def test_probe_uri_matches_tuva_layout() -> None:
    assert URI == "tuva-public-resources/tuva-core/1.0.0/synthetic-data/small"


def test_copy_uses_inline_s3_url_into_temp_table_only() -> None:
    stmts = preflight.build_statements(URI, "TUVA_DEMO")
    assert "temporary table TUVA_DEMO.TUVA_PREFLIGHT.S3_ACCESS_CHECK" in stmts[1]
    assert f"'s3://{URI}/'" in stmts[2]
    assert "synthetic_data__appointment" in stmts[2]
    assert all("TUVA_DEMO." in s for s in stmts)  # never touches another database


def test_success() -> None:
    cur = FakeCursor(copy_rows=[(f"s3://{URI}/synthetic_data__appointment.csv.gz", "LOADED", 120, 120, 1, 0)])
    r = preflight.run_preflight(cur, URI, "TUVA_DEMO")
    assert r.ok and r.files_loaded == 1 and r.rows_loaded == 120


def test_policy_block_gives_remediation() -> None:
    cur = FakeCursor(
        fail_with="SQL compilation error: Cannot perform operation. "
        "This account requires a storage integration for stage operations."
    )
    r = preflight.run_preflight(cur, URI, "TUVA_DEMO")
    assert not r.ok
    assert r.remediation == preflight.REMEDIATION


def test_unrelated_error_has_no_policy_remediation() -> None:
    r = preflight.run_preflight(FakeCursor(fail_with="Warehouse suspended"), URI, "TUVA_DEMO")
    assert not r.ok and r.remediation is None and r.error == "Warehouse suspended"


def test_zero_files_processed_fails() -> None:
    cur = FakeCursor(copy_rows=[("Copy executed with 0 files processed.",)], copy_cols=["status_message"])
    r = preflight.run_preflight(cur, URI, "TUVA_DEMO")
    assert not r.ok and r.files_loaded == 0


def test_summarize_counts_only_loaded_files() -> None:
    rows: list[tuple[object, ...]] = [("a", "LOADED", 5, 5, 1, 0), ("b", "LOAD_FAILED", 3, 0, 1, 3)]
    assert preflight.summarize_copy(COPY_COLS, rows) == (1, 5)
