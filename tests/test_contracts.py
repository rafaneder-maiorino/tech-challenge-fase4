"""Tests for the two data contracts and their blocker/warning classification.

Every frame here is built by hand, a dozen rows at a time, by the factory in
``conftest.py``, and none of them reads ``data/raw/gmsc.parquet``. The
reference numbers live in the schema docstrings, traced to
``reports/inspection.md``; the tests here prove that each rule fires on exactly
the violation it describes.

The shape of a rule test is always the same: start from a frame that passes
both schemas, break one rule, assert that the failure report names that rule and
nothing else. Where "nothing else" is structurally impossible the test says so
and explains why.
"""

import pandas as pd
import pandera.pandas as pa
import pytest
from conftest import make_frame

from credit_monitor.constants import DELINQUENCY_COLUMNS, TARGET_COLUMN
from credit_monitor.contracts import ModelInputSchema, RawCreditSchema
from credit_monitor.contracts.raw import (
    EXPECTED_COLUMNS,
    FEATURE_COLUMNS,
    POSITIVE_LABEL,
)
from credit_monitor.contracts.severity import (
    BLOCKER_RULES,
    RULE_SEVERITY,
    WARNING_RULES,
    Rule,
    Severity,
    severity_for,
)

# Rules enforced by a schema declaration (strict/ordered/dtype) rather than by a
# named Check, so they never appear in `failure_cases["check"]` under their own
# name. Kept explicit so the coverage test below stays honest about the gap.
STRUCTURAL_RULES: frozenset[str] = frozenset({Rule.SCHEMA_COLUMNS_AND_DTYPES.value})


def failed_checks(schema: type[pa.DataFrameModel], frame: pd.DataFrame) -> set[str]:
    """Validate lazily and return the names of every check that failed.

    Normalised to plain strings: the schemas register their checks under
    `Rule` members, and a test that compared enum members to whatever pandera
    stored would be asserting an enum implementation detail.
    """
    try:
        schema.validate(frame, lazy=True)
    except pa.errors.SchemaErrors as errors:
        return {str(name) for name in errors.failure_cases["check"]}
    return set()


# --------------------------------------------------------------------------
# The valid frame
# --------------------------------------------------------------------------


def test_valid_frame_passes_both_schemas() -> None:
    frame = make_frame()

    # Without this the rest of the file proves nothing: every rule test below
    # reads "this frame, minus one rule" and needs the baseline to be clean.
    assert failed_checks(RawCreditSchema, frame) == set()
    assert failed_checks(ModelInputSchema, frame) == set()


def test_valid_frame_positive_rate_matches_the_reference() -> None:
    frame = make_frame()

    rate = (frame[TARGET_COLUMN] == POSITIVE_LABEL).mean()

    # 6.67% against the 6.68% of inspection §2 — the fixture is the population
    # the contract describes, not an arbitrary frame that happens to validate.
    assert rate == pytest.approx(0.0668, abs=0.001)


# --------------------------------------------------------------------------
# Rule: schema_columns_and_dtypes (BLOCKER)
#
# Structural, so pandera reports it under its own check names rather than the
# rule name. These four tests pin those names and assert that severity_for
# classifies them as blockers through its fail-closed path.
# --------------------------------------------------------------------------


def test_missing_column_is_a_blocking_failure() -> None:
    frame = make_frame().drop(columns=["MonthlyIncome"])

    failures = failed_checks(RawCreditSchema, frame)

    assert "column_in_dataframe" in failures
    assert all(severity_for(name) is Severity.BLOCKER for name in failures)


def test_unexpected_extra_column_is_a_blocking_failure() -> None:
    # strict=True: a column nobody declared is a source change, even a harmless
    # looking one. The 11 columns of inspection §1 are the whole contract.
    frame = make_frame().assign(CustomerSegment="premium")

    failures = failed_checks(RawCreditSchema, frame)

    assert "column_in_schema" in failures
    assert all(severity_for(name) is Severity.BLOCKER for name in failures)


def test_reordered_columns_are_a_blocking_failure() -> None:
    # ordered=True. Position matters because downstream code that reads by
    # position (a numpy conversion, a model.predict on .values) would silently
    # read the wrong feature.
    columns = list(EXPECTED_COLUMNS)
    columns[1], columns[2] = columns[2], columns[1]
    frame = make_frame()[columns]

    failures = failed_checks(RawCreditSchema, frame)

    assert "column_ordered" in failures
    assert all(severity_for(name) is Severity.BLOCKER for name in failures)


def test_wrong_dtype_is_a_blocking_failure_and_is_not_coerced() -> None:
    # coerce=False is the point: age arriving as int64 is reported, not cast.
    # The values are all valid, so a coercing schema would pass this frame.
    frame = make_frame()
    frame["age"] = frame["age"].astype("int64")

    failures = failed_checks(RawCreditSchema, frame)

    assert failures == {"dtype('uint8')"}
    assert severity_for("dtype('uint8')") is Severity.BLOCKER


# --------------------------------------------------------------------------
# Rule: age_range (BLOCKER) — inspection §5
# --------------------------------------------------------------------------


@pytest.mark.parametrize("age", [0, 17, 111])
def test_age_outside_the_plausible_range_fails(age: int) -> None:
    # 0 is the single implausible row inspection §5 found; 17 is below legal
    # capacity; 111 is above the 109 maximum the report observed.
    frame = make_frame()
    frame.loc[0, "age"] = age

    assert failed_checks(RawCreditSchema, frame) == {Rule.AGE_RANGE.value}


@pytest.mark.parametrize("age", [18, 109, 110])
def test_age_at_the_range_boundaries_passes(age: int) -> None:
    # The bounds are inclusive on both sides: 18 is the floor and 109 is the
    # real maximum of inspection §5, which must not be rejected.
    frame = make_frame()
    frame.loc[0, "age"] = age

    assert failed_checks(RawCreditSchema, frame) == set()


# --------------------------------------------------------------------------
# Rule: delinquency_sentinels (BLOCKER) — inspection §6
# --------------------------------------------------------------------------


@pytest.mark.parametrize("column", DELINQUENCY_COLUMNS)
@pytest.mark.parametrize("sentinel", [96, 98])
def test_delinquency_sentinel_in_any_counter_fails(column: str, sentinel: int) -> None:
    # In the real data the codes appear in all three counters at once (269 rows,
    # §6). The rule is per-column on purpose: one counter carrying a code is
    # already a corrupted count, and a source that emits them one at a time
    # must not slip through.
    frame = make_frame()
    frame.loc[0, column] = sentinel

    assert failed_checks(RawCreditSchema, frame) == {Rule.DELINQUENCY_SENTINELS.value}


@pytest.mark.parametrize("value", [13, 17])
def test_high_but_legitimate_delinquency_counts_pass(value: int) -> None:
    # 13 and 17 are the largest values below the cut in inspection §6. The rule
    # rejects the two codes, not high counts, so these must survive.
    frame = make_frame()
    for column in DELINQUENCY_COLUMNS:
        frame.loc[0, column] = value

    assert failed_checks(RawCreditSchema, frame) == set()


def test_sentinels_are_still_rejected_at_the_model_boundary() -> None:
    # "No sentinel values remaining" at the model boundary is satisfied by
    # inheritance rather than by a second rule — this is the test that says so.
    frame = make_frame()
    frame.loc[0, "NumberOfTimes90DaysLate"] = 98

    assert failed_checks(ModelInputSchema, frame) == {Rule.DELINQUENCY_SENTINELS.value}


# --------------------------------------------------------------------------
# Rules: monthly_income_non_negative, dependents_non_negative,
#        target_binary, revolving_utilization_at_most_one
# --------------------------------------------------------------------------


def test_negative_monthly_income_fails() -> None:
    # Inspection §4: the observed minimum over 120,269 non-null rows is 0.
    frame = make_frame()
    frame.loc[0, "MonthlyIncome"] = -1.0

    assert failed_checks(RawCreditSchema, frame) == {
        Rule.MONTHLY_INCOME_NON_NEGATIVE.value
    }


def test_zero_monthly_income_passes_the_raw_contract() -> None:
    # Allowed at ingestion: 0 is the observed minimum (§4), and an applicant
    # with no income is a real applicant. The model boundary is where it stops.
    frame = make_frame()
    frame.loc[0, "MonthlyIncome"] = 0.0

    assert failed_checks(RawCreditSchema, frame) == set()


def test_negative_dependents_fails() -> None:
    # Inspection §4: the range over 146,076 non-null rows is 0 to 20.
    frame = make_frame()
    frame.loc[0, "NumberOfDependents"] = -1.0

    assert failed_checks(RawCreditSchema, frame) == {Rule.DEPENDENTS_NON_NEGATIVE.value}


def test_target_label_outside_the_binary_domain_fails() -> None:
    # Inspection §2 observed 139,974 zeros and 10,026 ones and nothing else.
    frame = make_frame()
    target = frame[TARGET_COLUMN].cat.add_categories(["2"])
    target.iloc[0] = "2"
    frame[TARGET_COLUMN] = target

    assert Rule.TARGET_BINARY.value in failed_checks(RawCreditSchema, frame)


def test_revolving_utilization_above_one_fails() -> None:
    # Inspection §8: 3,321 rows exceed 1 in the real data, which is why this is
    # classified as a warning — but the rule still has to fire.
    frame = make_frame()
    frame.loc[0, "RevolvingUtilizationOfUnsecuredLines"] = 1.5

    assert failed_checks(RawCreditSchema, frame) == {
        Rule.REVOLVING_UTILIZATION_AT_MOST_ONE.value
    }
    assert severity_for(Rule.REVOLVING_UTILIZATION_AT_MOST_ONE) is Severity.WARNING


# --------------------------------------------------------------------------
# Rule: nested_missingness (BLOCKER) — inspection §3
# --------------------------------------------------------------------------


def test_null_dependents_with_income_present_fails_nested_missingness() -> None:
    # The relationship, not the values: dependents may be missing only where
    # income is also missing. True for 3,924 of 3,924 rows today (§3).
    frame = make_frame()
    frame.loc[0, "NumberOfDependents"] = None

    assert failed_checks(RawCreditSchema, frame) == {Rule.NESTED_MISSINGNESS.value}


def test_null_dependents_and_null_income_together_pass_the_raw_contract() -> None:
    # The permitted pattern: both columns go missing from one upstream cause.
    # 19.82% of the real rows look exactly like this (§3).
    frame = make_frame()
    frame.loc[0, ["MonthlyIncome", "NumberOfDependents"]] = None

    assert failed_checks(RawCreditSchema, frame) == set()


def test_null_income_alone_passes_the_raw_contract() -> None:
    # The containment is one-directional: income may be missing on its own
    # (29,731 rows against 3,924 dependent-nulls, §3).
    frame = make_frame()
    frame.loc[0, "MonthlyIncome"] = None

    assert failed_checks(RawCreditSchema, frame) == set()


# --------------------------------------------------------------------------
# Rule: no_duplicate_rows (WARNING) — inspection §7
# --------------------------------------------------------------------------


def test_exact_duplicate_row_fails() -> None:
    # Row 1 is a negative, so repeating it leaves the positive rate at 1/16 =
    # 6.25% and keeps this test about duplicates alone.
    frame = make_frame()
    duplicated = pd.concat([frame, frame.iloc[[1]]], ignore_index=True)

    failures = failed_checks(RawCreditSchema, duplicated)

    assert failures == {Rule.NO_DUPLICATE_ROWS.value}
    assert severity_for(Rule.NO_DUPLICATE_ROWS) is Severity.WARNING


def test_duplicate_rule_reports_only_the_repeated_copy() -> None:
    # keep="first" — the count has to match the method behind the 609 of
    # inspection §7, so the original row is not itself a failure. The report
    # melts a frame-level check across columns, so failures are counted in
    # cells: 11 here, one row's worth.
    frame = make_frame()
    duplicated = pd.concat([frame, frame.iloc[[1]]], ignore_index=True)

    with pytest.raises(pa.errors.SchemaErrors) as caught:
        RawCreditSchema.validate(duplicated, lazy=True)

    failure_cases = caught.value.failure_cases
    assert set(failure_cases["index"]) == {len(frame)}
    assert len(failure_cases) == len(EXPECTED_COLUMNS)


# --------------------------------------------------------------------------
# Rule: positive_rate_band (WARNING) — inspection §2
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n_rows", "n_positive", "rate"),
    [(15, 0, 0.0), (15, 3, 0.20)],
)
def test_positive_rate_outside_the_band_fails(
    n_rows: int, n_positive: int, rate: float
) -> None:
    # 0% and 20% against a 6.68% reference (§2) and a [5%, 9%] band.
    frame = make_frame(n_rows=n_rows, n_positive=n_positive)
    assert (frame[TARGET_COLUMN] == POSITIVE_LABEL).mean() == pytest.approx(rate)

    failures = failed_checks(RawCreditSchema, frame)

    assert failures == {Rule.POSITIVE_RATE_BAND.value}
    assert severity_for(Rule.POSITIVE_RATE_BAND) is Severity.WARNING


# --------------------------------------------------------------------------
# Rule: monthly_income_present (BLOCKER) — inspection §3
# --------------------------------------------------------------------------


def test_null_income_fails_the_model_schema_but_not_the_raw_one() -> None:
    # The whole reason there are two schemas, in one test. 19.82% of the real
    # rows are on the left-hand side of this assertion (§3).
    frame = make_frame()
    frame.loc[0, ["MonthlyIncome", "NumberOfDependents"]] = None

    assert failed_checks(RawCreditSchema, frame) == set()

    model_failures = failed_checks(ModelInputSchema, frame)
    assert Rule.MONTHLY_INCOME_PRESENT.value in model_failures


def test_zero_income_fails_the_model_schema_but_not_the_raw_one() -> None:
    # DebtRatio is debt over income, so a zero denominator makes the feature
    # the model consumes undefined. Tolerated on arrival (§4 minimum is 0).
    frame = make_frame()
    frame.loc[0, "MonthlyIncome"] = 0.0

    assert failed_checks(RawCreditSchema, frame) == set()
    assert failed_checks(ModelInputSchema, frame) == {Rule.MONTHLY_INCOME_PRESENT.value}


# --------------------------------------------------------------------------
# Rule: debt_ratio_unit_guard (BLOCKER) — inspection §9
# --------------------------------------------------------------------------


@pytest.mark.parametrize("debt_ratio", [2.5, 1159.0])
def test_debt_ratio_above_the_ceiling_fails_the_model_schema(
    debt_ratio: float,
) -> None:
    # 1,159 is the median DebtRatio of the missing-income group (§9): the value
    # this rule exists to keep out of a row that claims to have income.
    frame = make_frame()
    frame.loc[0, "DebtRatio"] = debt_ratio

    assert failed_checks(ModelInputSchema, frame) == {Rule.DEBT_RATIO_UNIT_GUARD.value}


def test_debt_ratio_at_the_ceiling_passes() -> None:
    # Inclusive bound, above the 1.1288 that is the 95th percentile of the
    # with-income group (§9), so a genuinely over-indebted borrower survives.
    frame = make_frame()
    frame.loc[0, "DebtRatio"] = 2.0

    assert failed_checks(ModelInputSchema, frame) == set()


def test_large_debt_ratio_passes_the_raw_schema() -> None:
    # No DebtRatio range rule exists in the raw contract, deliberately: §9
    # shows the column carrying a different unit for the 19.82% of rows with no
    # income, where a median of 1,159 is normal rather than broken.
    frame = make_frame()
    frame.loc[0, ["MonthlyIncome", "NumberOfDependents"]] = None
    frame.loc[0, "DebtRatio"] = 329664.0

    assert failed_checks(RawCreditSchema, frame) == set()


# --------------------------------------------------------------------------
# Rule: no_nulls_in_features (BLOCKER) — inspection §3
# --------------------------------------------------------------------------


def test_null_feature_fails_the_model_schema() -> None:
    # This rule cannot be isolated, and the reason is the dataset: the only two
    # feature columns that can hold a null are MonthlyIncome and
    # NumberOfDependents (§3), and each of those nulls necessarily trips a
    # second rule — income-null hits monthly_income_present, dependents-null
    # alone hits nested_missingness. Every other column is uint8, where a null
    # would widen the dtype and be caught structurally instead. So the
    # assertion is membership, and the companion assertion below pins the
    # column set the rule actually covers.
    frame = make_frame()
    frame.loc[0, ["MonthlyIncome", "NumberOfDependents"]] = None

    failures = failed_checks(ModelInputSchema, frame)

    assert Rule.NO_NULLS_IN_FEATURES.value in failures
    assert all(severity_for(name) is Severity.BLOCKER for name in failures)


def test_feature_columns_are_every_column_except_the_target() -> None:
    # What no_nulls_in_features iterates over. Derived from the schema rather
    # than restated, so the rule widens automatically if a column is added.
    assert set(FEATURE_COLUMNS) == set(EXPECTED_COLUMNS) - {TARGET_COLUMN}
    assert len(FEATURE_COLUMNS) == 10


# --------------------------------------------------------------------------
# Lazy validation
# --------------------------------------------------------------------------


def test_lazy_validation_collects_every_failure_at_once() -> None:
    # Six rules broken in six different ways, in one frame. Without lazy=True
    # pandera raises on the first one and an operator fixes a batch six times
    # to learn what is wrong with it once.
    frame = make_frame(n_rows=15, n_positive=3)  # positive_rate_band: 20%
    frame.loc[0, "age"] = 0  # age_range
    frame.loc[1, "NumberOfTimes90DaysLate"] = 98  # delinquency_sentinels
    frame.loc[2, "MonthlyIncome"] = -10.0  # monthly_income_non_negative
    frame.loc[3, "NumberOfDependents"] = -2.0  # dependents_non_negative
    frame.loc[4, "RevolvingUtilizationOfUnsecuredLines"] = 9.9  # revolving_util
    frame.loc[5, "NumberOfDependents"] = None  # nested_missingness

    failures = failed_checks(RawCreditSchema, frame)

    assert failures == {
        Rule.AGE_RANGE.value,
        Rule.DELINQUENCY_SENTINELS.value,
        Rule.MONTHLY_INCOME_NON_NEGATIVE.value,
        Rule.DEPENDENTS_NON_NEGATIVE.value,
        Rule.REVOLVING_UTILIZATION_AT_MOST_ONE.value,
        Rule.NESTED_MISSINGNESS.value,
        Rule.POSITIVE_RATE_BAND.value,
    }


def test_lazy_validation_separates_blockers_from_warnings_in_one_report() -> None:
    # The payload stage 2 actually consumes: one report, partitioned by policy.
    # A batch with only warnings is shipped with an alert; this one is refused.
    frame = make_frame()
    frame.loc[0, "age"] = 0  # blocker
    frame.loc[1, "RevolvingUtilizationOfUnsecuredLines"] = 50708.0  # warning

    failures = failed_checks(RawCreditSchema, frame)
    by_severity = {name: severity_for(name) for name in failures}

    assert by_severity == {
        Rule.AGE_RANGE.value: Severity.BLOCKER,
        Rule.REVOLVING_UTILIZATION_AT_MOST_ONE.value: Severity.WARNING,
    }


def test_eager_validation_stops_at_the_first_failure() -> None:
    # The contrast that justifies lazy=True everywhere else.
    frame = make_frame()
    frame.loc[0, "age"] = 0
    frame.loc[1, "MonthlyIncome"] = -10.0

    with pytest.raises(pa.errors.SchemaError) as caught:
        RawCreditSchema.validate(frame, lazy=False)

    assert not isinstance(caught.value, pa.errors.SchemaErrors)


# --------------------------------------------------------------------------
# The severity mapping
# --------------------------------------------------------------------------


def implemented_rules(schema: type[pa.DataFrameModel]) -> set[str]:
    """Return the name of every named check registered by a schema."""
    built = schema.to_schema()
    names = {
        str(check.name) for column in built.columns.values() for check in column.checks
    }
    return names | {str(check.name) for check in built.checks}


def test_severity_mapping_covers_every_implemented_rule_and_nothing_else() -> None:
    # The orphan test, in both directions. A rule added to a schema without a
    # severity would be silently escalated to BLOCKER by severity_for's
    # fail-closed default; a severity entry with no rule behind it is a policy
    # decision about something that is not being checked. Both are drift.
    implemented = (
        implemented_rules(RawCreditSchema)
        | implemented_rules(ModelInputSchema)
        | STRUCTURAL_RULES
    )
    classified = {rule.value for rule in RULE_SEVERITY}

    assert implemented - classified == set(), "implemented rules with no severity"
    assert classified - implemented == set(), "severities for unimplemented rules"


def test_every_rule_in_the_registry_is_classified() -> None:
    assert set(Rule) == set(RULE_SEVERITY)


def test_blockers_and_warnings_partition_the_rules() -> None:
    blockers, warnings_ = BLOCKER_RULES, WARNING_RULES

    assert blockers | warnings_ == set(Rule)
    assert blockers & warnings_ == frozenset()


def test_the_declared_classification_is_what_the_plan_specified() -> None:
    # Pinned explicitly rather than derived, so moving a rule between the two
    # buckets is a deliberate edit to this test and not a silent policy change.
    blockers, warnings_ = BLOCKER_RULES, WARNING_RULES

    assert blockers == {
        Rule.SCHEMA_COLUMNS_AND_DTYPES,
        Rule.AGE_RANGE,
        Rule.DELINQUENCY_SENTINELS,
        Rule.MONTHLY_INCOME_NON_NEGATIVE,
        Rule.DEPENDENTS_NON_NEGATIVE,
        Rule.TARGET_BINARY,
        Rule.NESTED_MISSINGNESS,
        Rule.MONTHLY_INCOME_PRESENT,
        Rule.DEBT_RATIO_UNIT_GUARD,
        Rule.NO_NULLS_IN_FEATURES,
    }
    assert warnings_ == {
        Rule.NO_DUPLICATE_ROWS,
        Rule.REVOLVING_UTILIZATION_AT_MOST_ONE,
        Rule.POSITIVE_RATE_BAND,
    }


@pytest.mark.parametrize(
    "check_name",
    ["not_nullable", "column_in_schema", "dtype('uint8')", "a_rule_from_the_future"],
)
def test_severity_for_unknown_check_names_fails_closed(check_name: str) -> None:
    # pandera's own structural check names arrive here verbatim, and so would a
    # rule someone adds to a schema and forgets to classify. Guessing WARNING
    # would ship unexamined data to the model.
    assert severity_for(check_name) is Severity.BLOCKER


def test_model_schema_inherits_every_raw_rule() -> None:
    # The tightening must be additive. If the model schema ever stopped
    # inheriting a raw rule, the boundary meant to be stricter would be looser.
    assert implemented_rules(RawCreditSchema) <= implemented_rules(ModelInputSchema)
