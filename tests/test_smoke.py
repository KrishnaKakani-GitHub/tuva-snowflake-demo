from __future__ import annotations

from tuva_demo import smoke

MANIFEST = smoke.Manifest.model_validate(
    {
        "nodes": {
            "model.the_tuva_project.core__patient": {
                "resource_type": "model",
                "package_name": "the_tuva_project",
                "name": "core__patient",
                "relation_name": "TUVA_DEMO.core.patient",
            },
            "model.the_tuva_project.core__medical_claim": {
                "resource_type": "model",
                "package_name": "the_tuva_project",
                "name": "core__medical_claim",
                "relation_name": "TUVA_DEMO.core.medical_claim",
            },
            "model.integration_tests.core__patient": {
                "resource_type": "model",
                "package_name": "integration_tests",
                "name": "core__patient",
                "relation_name": "WRONG.package.patient",
            },
            "test.the_tuva_project.t": {
                "resource_type": "test",
                "package_name": "the_tuva_project",
                "name": "core__condition",
            },
        }
    }
)


class FakeCursor:
    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts
        self.sql: list[str] = []
        self._last: str = ""

    def execute(self, sql: str) -> object:
        self.sql.append(sql)
        self._last = sql.removeprefix("select count(*) from ")
        if self._last not in self.counts:
            raise RuntimeError(f"Object '{self._last}' does not exist")
        return self

    def fetchone(self) -> tuple[object, ...] | None:
        return (self.counts[self._last],)


def test_resolve_only_tuva_package_models() -> None:
    rel = smoke.resolve_relations(MANIFEST, ["core__patient", "core__medical_claim", "core__condition"])
    assert rel == {
        "core__patient": "TUVA_DEMO.core.patient",
        "core__medical_claim": "TUVA_DEMO.core.medical_claim",
        "core__condition": None,  # only a test node exists with that name
    }


def test_all_present_and_populated() -> None:
    cur = FakeCursor({"TUVA_DEMO.core.patient": 10, "TUVA_DEMO.core.medical_claim": 500})
    rel = smoke.resolve_relations(MANIFEST, ["core__patient", "core__medical_claim"])
    report = smoke.run_checks(rel, smoke.cursor_count_fn(cur))
    assert report.ok
    assert all(s.startswith("select count(*)") for s in cur.sql)  # aggregate-only reads


def test_empty_table_fails() -> None:
    cur = FakeCursor({"TUVA_DEMO.core.patient": 0, "TUVA_DEMO.core.medical_claim": 5})
    rel = smoke.resolve_relations(MANIFEST, ["core__patient", "core__medical_claim"])
    report = smoke.run_checks(rel, smoke.cursor_count_fn(cur))
    assert not report.ok
    assert [c.model for c in report.checks if not c.ok] == ["core__patient"]


def test_query_error_is_reported_and_others_still_checked() -> None:
    cur = FakeCursor({"TUVA_DEMO.core.medical_claim": 5})
    rel = smoke.resolve_relations(MANIFEST, ["core__patient", "core__medical_claim"])
    report = smoke.run_checks(rel, smoke.cursor_count_fn(cur))
    by_model = {c.model: c for c in report.checks}
    assert not report.ok
    assert "does not exist" in (by_model["core__patient"].error or "")
    assert by_model["core__medical_claim"].ok


def test_missing_model_fails() -> None:
    report = smoke.run_checks({"core__condition": None}, lambda _r: 1)
    assert not report.ok
    assert report.checks[0].error == "not in manifest"


def test_no_checks_is_not_ok() -> None:
    assert not smoke.run_checks({}, lambda _r: 1).ok
