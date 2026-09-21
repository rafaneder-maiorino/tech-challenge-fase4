"""Tests for the ingestion gate: quarantine, exit codes and the report.

The batches here are the same ones the demo uses, only smaller: a hand-built
clean frame from ``conftest`` run through the real defect injector, so the
tests assert against the generator's own manifest rather than against numbers
copied out of it. If the injector and the report ever disagree about how many
duplicates a batch has, one of them is wrong and these tests say so.

Nothing reads ``data/raw/gmsc.parquet`` and nothing writes outside ``tmp_path``.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pandera.pandas as pa
import pytest
from conftest import make_frame
from pandera.typing import Series, UInt8

from credit_monitor.contracts.failures import (
    UnknownRuleError,
    assert_rules_are_classified,
    resolve_rule,
)
from credit_monitor.contracts.severity import RULE_SEVERITY, Rule, Severity
from credit_monitor.pipeline.dirty_batch import (
    AGE_DEFECT_ROWS,
    DUPLICATE_DEFECT_ROWS,
    NEGATIVE_INCOME_DEFECT_ROWS,
    NESTED_MISSING_DEFECT_ROWS,
    SENTINEL_DEFECT_ROWS,
    UTILIZATION_DEFECT_ROWS,
    DefectManifest,
    inject_defects,
)
from credit_monitor.pipeline.ingest import EXIT_BLOCKED, EXIT_OK, main

# 26 rows with 2 positives is 7.69%, and 2 of 30 once the four duplicates are
# appended: 6.67%, inside the contract's [5%, 9%] band at both ends. Getting
# this wrong would trip `positive_rate_band` by accident and add a rule to the
# report that the manifest never mentions.
BASE_ROWS: int = 26
BASE_POSITIVES: int = 2

# Rows the blocker defects land on, by construction of inject_defects: three
# ages, two sentinels, five nested-missingness, two negative incomes, on
# disjoint positions starting at 0.
EXPECTED_QUARANTINED_ROWS: int = (
    AGE_DEFECT_ROWS
    + SENTINEL_DEFECT_ROWS
    + NESTED_MISSING_DEFECT_ROWS
    + NEGATIVE_INCOME_DEFECT_ROWS
)


@dataclass(frozen=True, slots=True)
class GateRun:
    """One end-to-end run of the gate, and everything it left behind."""

    exit_code: int
    report: dict[str, Any]
    manifest: DefectManifest
    quarantine_dir: Path
    batch: pd.DataFrame

    @property
    def rejections_jsonl(self) -> list[dict[str, Any]]:
        """Parse the quarantine JSONL, or return nothing if none was written."""
        path = self.quarantine_dir / "rejections.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line) for line in path.read_text().splitlines() if line.strip()
        ]

    @property
    def quarantined_frame(self) -> pd.DataFrame:
        """The quarantined rows, or an empty frame if none were written."""
        path = self.quarantine_dir / "rows.parquet"
        return pd.read_parquet(path) if path.exists() else pd.DataFrame()

    @property
    def rule_rows(self) -> dict[str, int]:
        """Per-rule row counts from the JSON report, shaped like the manifest."""
        return {rule["rule"]: rule["rows"] for rule in self.report["rules"]}


def run_gate(
    tmp_path: Path,
    include_blockers: bool,
    batch_id: str = "batch_test",
) -> GateRun:
    """Generate a batch, run the CLI over it, and collect what came out."""
    base = make_frame(n_rows=BASE_ROWS, n_positive=BASE_POSITIVES)
    batch, manifest = inject_defects(base, include_blockers=include_blockers)

    batch_path = tmp_path / f"{batch_id}.parquet"
    batch.to_parquet(batch_path, index=False)
    quarantine = tmp_path / "quarantine"
    reports = tmp_path / "reports"

    exit_code = main(
        [
            str(batch_path),
            "--quarantine-dir",
            str(quarantine),
            "--report-dir",
            str(reports),
        ]
    )
    report = json.loads((reports / f"{batch_id}.json").read_text())
    return GateRun(
        exit_code=exit_code,
        report=report,
        manifest=manifest,
        quarantine_dir=quarantine / batch_id,
        batch=batch,
    )


# --------------------------------------------------------------------------
# The two verdicts the demo turns on
# --------------------------------------------------------------------------


def test_blocker_batch_is_blocked_with_a_non_zero_exit(tmp_path: Path) -> None:
    run = run_gate(tmp_path, include_blockers=True)

    assert run.report["verdict"] == "BLOCKED"
    assert run.exit_code == EXIT_BLOCKED
    assert run.exit_code != EXIT_OK


def test_blocked_batch_populates_the_quarantine(tmp_path: Path) -> None:
    run = run_gate(tmp_path, include_blockers=True)

    assert run.quarantine_dir.is_dir()
    assert len(run.quarantined_frame) == EXPECTED_QUARANTINED_ROWS
    assert run.report["rows"]["quarantined"] == EXPECTED_QUARANTINED_ROWS

    # The JSONL is the part a human reads without rerunning anything, so it has
    # to carry every blocking rejection, including the batch-scoped one that
    # names no row.
    jsonl = run.rejections_jsonl
    assert len(jsonl) == EXPECTED_QUARANTINED_ROWS + 1
    assert all(record["severity"] == Severity.BLOCKER.value for record in jsonl)
    assert {record["rule"] for record in jsonl} == {
        Rule.AGE_RANGE.value,
        Rule.DELINQUENCY_SENTINELS.value,
        Rule.NESTED_MISSINGNESS.value,
        Rule.MONTHLY_INCOME_NON_NEGATIVE.value,
        Rule.SCHEMA_COLUMNS_AND_DTYPES.value,
    }


def test_warning_only_batch_is_accepted_with_exit_zero(tmp_path: Path) -> None:
    # The run that proves the gate discriminates. Same generator, same
    # contract, same script — only the defects change, and the pipeline lives.
    run = run_gate(tmp_path, include_blockers=False)

    assert run.report["verdict"] == "ACCEPTED_WITH_WARNINGS"
    assert run.exit_code == EXIT_OK
    assert run.report["rows"]["quarantined"] == 0


def test_warning_only_batch_quarantines_nothing_at_all(tmp_path: Path) -> None:
    # Not "an empty quarantine directory" — no directory. A warning that left a
    # quarantine behind would be indistinguishable from a blocker at a glance.
    run = run_gate(tmp_path, include_blockers=False)

    assert not run.quarantine_dir.exists()
    assert run.rejections_jsonl == []


def test_a_batch_with_no_defects_is_accepted_outright(tmp_path: Path) -> None:
    # The third verdict. Without it, ACCEPTED_WITH_WARNINGS could be the
    # best outcome the gate is capable of and nobody would notice.
    batch_path = tmp_path / "pristine.parquet"
    make_frame(n_rows=BASE_ROWS, n_positive=BASE_POSITIVES).to_parquet(
        batch_path, index=False
    )
    reports = tmp_path / "reports"

    exit_code = main(
        [
            str(batch_path),
            "--quarantine-dir",
            str(tmp_path / "quarantine"),
            "--report-dir",
            str(reports),
        ]
    )
    report = json.loads((reports / "pristine.json").read_text())

    assert report["verdict"] == "ACCEPTED"
    assert exit_code == EXIT_OK
    assert report["rules"] == []


# --------------------------------------------------------------------------
# Nothing is silently lost
# --------------------------------------------------------------------------


@pytest.mark.parametrize("include_blockers", [True, False])
def test_passed_plus_quarantined_equals_rows_in(
    tmp_path: Path, include_blockers: bool
) -> None:
    # The accounting invariant. A gate that drops a row it cannot classify is
    # worse than no gate: the loss is silent and the model still gets fed.
    run = run_gate(tmp_path, include_blockers=include_blockers)
    rows = run.report["rows"]

    assert rows["passed"] + rows["quarantined"] == rows["in"]
    assert rows["in"] == len(run.batch) == run.manifest.rows_out


def test_quarantined_rows_are_the_blocker_rows_and_only_those(
    tmp_path: Path,
) -> None:
    # inject_defects puts the four blocker defects on disjoint positions
    # starting at 0, so the quarantine is exactly that prefix. The warning
    # rows that follow must still be in the passed count.
    run = run_gate(tmp_path, include_blockers=True)

    quarantined = set(run.quarantined_frame.index)
    assert quarantined == set(range(EXPECTED_QUARANTINED_ROWS))

    # The seven warning rows are not in the quarantine and are not missing
    # either: a warning means "pass it through and tell somebody", and a gate
    # that quietly held those rows back would be enforcing a policy nobody
    # wrote down.
    warning_rows = sum(
        count["rows"]
        for count in run.report["rules"]
        if count["severity"] == Severity.WARNING.value
    )
    assert warning_rows == UTILIZATION_DEFECT_ROWS + DUPLICATE_DEFECT_ROWS
    assert not quarantined & set(
        range(EXPECTED_QUARANTINED_ROWS, run.report["rows"]["in"])
    )


# --------------------------------------------------------------------------
# The report is checkable, not merely plausible
# --------------------------------------------------------------------------


@pytest.mark.parametrize("include_blockers", [True, False])
def test_report_rule_counts_match_the_injected_manifest(
    tmp_path: Path, include_blockers: bool
) -> None:
    # The assertion the whole generator exists for. Equality, not containment:
    # a rule in the report that the manifest never mentions is the report
    # inventing a defect, and a rule in the manifest missing from the report is
    # the gate failing to notice one.
    run = run_gate(tmp_path, include_blockers=include_blockers)

    assert run.rule_rows == run.manifest.injected


@pytest.mark.parametrize("include_blockers", [True, False])
def test_every_rejection_names_a_rule_in_the_severity_mapping(
    tmp_path: Path, include_blockers: bool
) -> None:
    # No rejection may carry a name the policy cannot resolve. This is the
    # assertion that would have caught the pandera trap of stage 1's first
    # half, where inherited checks reported under a different name than the one
    # they were registered with.
    run = run_gate(tmp_path, include_blockers=include_blockers)
    known = {rule.value for rule in RULE_SEVERITY}

    reported = {rejection["rule"] for rejection in run.report["rejections"]}
    counted = {count["rule"] for count in run.report["rules"]}
    quarantined = {record["rule"] for record in run.rejections_jsonl}

    assert reported <= known
    assert counted <= known
    assert quarantined <= known
    assert reported, "a defective batch produced no rejections at all"


def test_report_severities_agree_with_the_mapping(tmp_path: Path) -> None:
    run = run_gate(tmp_path, include_blockers=True)

    for count in run.report["rules"]:
        expected = RULE_SEVERITY[Rule(count["rule"])]
        assert count["severity"] == expected.value


# --------------------------------------------------------------------------
# The severity lookup refuses to guess
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "check_name",
    ["column_in_schema", "column_in_dataframe", "column_ordered", "not_nullable"],
)
def test_pandera_structural_check_names_resolve_to_the_structural_rule(
    check_name: str,
) -> None:
    assert resolve_rule(check_name) is Rule.SCHEMA_COLUMNS_AND_DTYPES


@pytest.mark.parametrize("check_name", ["dtype('uint8')", "coerce_dtype('float64')"])
def test_parameterised_dtype_check_names_resolve_to_the_structural_rule(
    check_name: str,
) -> None:
    # Pandera renders the dtype into the name, so these cannot be matched by
    # equality against a fixed list.
    assert resolve_rule(check_name) is Rule.SCHEMA_COLUMNS_AND_DTYPES


@pytest.mark.parametrize(
    "check_name",
    ["a_rule_from_the_future", "check_age_range", "greater_than_or_equal_to(0)"],
)
def test_unknown_check_names_raise_instead_of_defaulting(check_name: str) -> None:
    # `check_age_range` is not hypothetical: it is the method name pandera
    # falls back to when a registered check loses its name, which is exactly
    # the silent failure this lookup exists to make loud.
    with pytest.raises(UnknownRuleError, match=check_name.split("(")[0]):
        resolve_rule(check_name)


class UnclassifiedSchema(pa.DataFrameModel):
    """A contract carrying a rule that no severity mapping claims."""

    age: Series[UInt8]

    @pa.check("age")
    def a_rule_nobody_classified(cls, age: Series[UInt8]) -> Series[bool]:
        """Stand in for a rule added to a schema and never classified."""
        return age >= 0


def test_preflight_rejects_a_schema_with_an_unclassified_rule() -> None:
    # The check runs before any data is read, which is the difference between
    # finding the drift now and finding it the first time a batch happens to
    # violate the rule nobody classified.
    with pytest.raises(UnknownRuleError, match="a_rule_nobody_classified"):
        assert_rules_are_classified(UnclassifiedSchema)
