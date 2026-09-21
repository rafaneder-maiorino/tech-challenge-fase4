"""Tests for reading a pandera failure report through the severity policy."""

import pandera.pandas as pa
import pytest
from conftest import make_frame

from credit_monitor.contracts import ModelInputSchema, RawCreditSchema
from credit_monitor.contracts.failures import blocking_failures
from credit_monitor.contracts.severity import Rule, Severity


def errors_for(schema: type[pa.DataFrameModel], frame) -> pa.errors.SchemaErrors:  # noqa: ANN001
    with pytest.raises(pa.errors.SchemaErrors) as caught:
        schema.validate(frame, lazy=True)
    return caught.value


def test_a_warning_only_failure_report_has_no_blockers() -> None:
    # Utilisation above 1 is the case that matters in practice: the
    # preprocessing step keeps those rows on purpose (docs/findings.md §6) and
    # must not be stopped by a rule the contract classified as a warning.
    frame = make_frame()
    frame.loc[0, "RevolvingUtilizationOfUnsecuredLines"] = 8.0

    blockers = blocking_failures(errors_for(RawCreditSchema, frame))

    assert blockers.empty


def test_a_blocker_is_reported_as_one() -> None:
    frame = make_frame()
    frame.loc[0, "age"] = 0

    blockers = blocking_failures(errors_for(RawCreditSchema, frame))

    assert not blockers.empty
    assert set(blockers["rule"]) == {Rule.AGE_RANGE.value}
    assert set(blockers["severity"]) == {Severity.BLOCKER.value}


def test_a_warning_beside_a_blocker_does_not_hide_it() -> None:
    frame = make_frame()
    frame.loc[0, "RevolvingUtilizationOfUnsecuredLines"] = 8.0
    frame.loc[1, "age"] = 0

    blockers = blocking_failures(errors_for(RawCreditSchema, frame))

    assert set(blockers["rule"]) == {Rule.AGE_RANGE.value}
    assert Rule.REVOLVING_UTILIZATION_AT_MOST_ONE.value not in set(blockers["rule"])


def test_structural_failures_are_blockers() -> None:
    # A wrong dtype reports under pandera's own name and has to resolve to the
    # structural rule rather than fall through as unclassified.
    frame = make_frame()
    frame["age"] = frame["age"].astype("int64")

    blockers = blocking_failures(errors_for(ModelInputSchema, frame))

    assert set(blockers["rule"]) == {Rule.SCHEMA_COLUMNS_AND_DTYPES.value}
