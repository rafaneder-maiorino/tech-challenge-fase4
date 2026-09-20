"""Tests for the cleaning decisions and the contract assertion inside them.

Every frame is hand-built; none reads ``data/raw/gmsc.parquet``. The reference
numbers those decisions rest on live in the module docstrings, traced to
``reports/inspection.md`` — what is tested here is that the code does what
those docstrings claim, on data whose defects we placed ourselves and can
therefore count.
"""

import numpy as np
import pandas as pd
import pandera.pandas as pa
import pytest
from conftest import make_random_frame

from credit_monitor.constants import DELINQUENCY_COLUMNS, TARGET_COLUMN
from credit_monitor.data.preprocess import (
    INCOME_MISSING_COLUMN,
    MODEL_FEATURES,
    CleaningReport,
    ImputationFit,
    PreprocessingParams,
    assert_model_ready,
    assert_nested_missingness_subsumed,
    build_feature_matrix,
    clean,
    fit_imputation,
    impute,
    prepare_split,
    sentinel_mask,
)

SENTINEL_COLUMN = "NumberOfTimes90DaysLate"


def pipeline(
    frame: pd.DataFrame, params: PreprocessingParams | None = None
) -> tuple[pd.DataFrame, CleaningReport, ImputationFit]:
    """Run the whole stateless-then-fitted path, as the pipeline does."""
    cleaned, report = clean(frame, params)
    fit = fit_imputation(cleaned)
    return impute(cleaned, fit), report, fit


# --------------------------------------------------------------------------
# Sentinel rows (inspection §6)
# --------------------------------------------------------------------------


def test_sentinel_rows_are_dropped() -> None:
    frame = make_random_frame()
    frame.loc[0:2, list(DELINQUENCY_COLUMNS)] = 98

    cleaned, report = clean(frame)

    assert report.dropped_sentinel_rows == 3
    assert len(cleaned) == len(frame) - 3
    assert not sentinel_mask(cleaned).any()


@pytest.mark.parametrize("sentinel", [96, 98])
def test_a_sentinel_in_a_single_counter_still_drops_the_row(sentinel: int) -> None:
    # §6 found the codes in all three counters at once, but the marker is the
    # code itself, not the co-occurrence.
    frame = make_random_frame()
    frame.loc[0, SENTINEL_COLUMN] = sentinel

    _, report = clean(frame)

    assert report.dropped_sentinel_rows == 1


def test_sentinel_rows_can_be_kept_by_parameter() -> None:
    # The exclusion is a documented choice, so it has to be reversible without
    # editing the function.
    frame = make_random_frame()
    frame.loc[0:2, list(DELINQUENCY_COLUMNS)] = 98

    _, report = clean(frame, PreprocessingParams(drop_sentinel_rows=False))

    assert report.dropped_sentinel_rows == 0


# --------------------------------------------------------------------------
# The missingness indicator, before imputation (inspection §3, §9)
# --------------------------------------------------------------------------


def test_income_missing_is_flagged_before_the_value_is_imputed() -> None:
    # The whole point of the ordering. After imputation the null is gone and
    # is indistinguishable from a real 5,450 — the flag is the only surviving
    # evidence, and §9 shows it points the opposite way from the naive reading
    # (missing-income rows default at 5.61% against 6.95%).
    frame = make_random_frame()
    frame.loc[0:4, "MonthlyIncome"] = None
    frame.loc[0:4, "NumberOfDependents"] = None

    imputed, report, fit = pipeline(frame)

    assert report.income_missing_rows == 5
    flagged = imputed[imputed[INCOME_MISSING_COLUMN] == 1]
    assert len(flagged) == 5
    assert (flagged["MonthlyIncome"] == fit.monthly_income_median).all()
    assert imputed["MonthlyIncome"].notna().all()


def test_zero_income_is_folded_into_the_same_indicator() -> None:
    # §4 puts 1,634 rows at exactly zero income; they default at 4.04%, nearer
    # the missing group than the rest, and a zero denominator makes DebtRatio
    # undefined anyway.
    frame = make_random_frame()
    frame.loc[0:1, "MonthlyIncome"] = 0.0

    imputed, report, _ = pipeline(frame)

    assert report.income_zero_rows == 2
    assert report.income_missing_rows == 2
    assert (imputed.loc[imputed[INCOME_MISSING_COLUMN] == 1, "MonthlyIncome"] > 0).all()


def test_imputation_is_fitted_only_on_income_present_rows() -> None:
    # If the fill value were computed after imputing, or over the flagged
    # rows, it would drift toward itself on every rerun.
    frame = make_random_frame()
    frame.loc[0:9, "MonthlyIncome"] = None
    frame.loc[0:9, "NumberOfDependents"] = None

    cleaned, _ = clean(frame)
    fit = fit_imputation(cleaned)

    present = cleaned[cleaned[INCOME_MISSING_COLUMN] == 0]
    assert fit.fitted_on_rows == len(present)
    assert fit.monthly_income_median == pytest.approx(present["MonthlyIncome"].median())


# --------------------------------------------------------------------------
# DebtRatio: two units, separated (inspection §9)
# --------------------------------------------------------------------------


def test_debt_ratio_is_neutralised_where_income_is_missing() -> None:
    # §9: median 1,159 without income against 0.296 with it. The 1,159-style
    # value must not survive into a column that means "a ratio".
    frame = make_random_frame()
    frame.loc[0:2, "MonthlyIncome"] = None
    frame.loc[0:2, "NumberOfDependents"] = None
    frame.loc[0:2, "DebtRatio"] = 1159.0

    imputed, report, fit = pipeline(frame)

    assert report.debt_ratio_neutralised_rows == 3
    neutralised = imputed[imputed[INCOME_MISSING_COLUMN] == 1]
    assert (neutralised["DebtRatio"] == fit.debt_ratio_median).all()
    assert 1159.0 not in set(imputed["DebtRatio"])


def test_debt_ratio_is_capped_where_income_is_present() -> None:
    # §9 gives the with-income group a 95th percentile of 0.98, so the 1,786
    # rows above the contract ceiling of 2 are out of unit or unreliable.
    frame = make_random_frame()
    frame.loc[0:1, "DebtRatio"] = 61106.5

    imputed, report, _ = pipeline(frame)

    assert report.debt_ratio_capped_rows == 2
    assert imputed["DebtRatio"].max() <= PreprocessingParams().debt_ratio_cap


# --------------------------------------------------------------------------
# Utilisation cap (inspection §8) — a parameter, not a constant
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cap", [1.0, 10.0])
def test_utilization_cap_honours_the_parameter(cap: float) -> None:
    # §8: 3,321 rows above 1 but only 241 above 10. The two cuts disagree, so
    # the choice has to be visible and changeable from outside.
    frame = make_random_frame()
    frame.loc[0, "RevolvingUtilizationOfUnsecuredLines"] = 50708.0
    frame.loc[1, "RevolvingUtilizationOfUnsecuredLines"] = 8.0

    cleaned, report = clean(frame, PreprocessingParams(utilization_cap=cap))

    assert cleaned["RevolvingUtilizationOfUnsecuredLines"].max() <= cap
    expected_capped = 2 if cap == 1.0 else 1
    assert report.utilization_capped_rows == expected_capped


# --------------------------------------------------------------------------
# Nested missingness (inspection §3)
# --------------------------------------------------------------------------


def test_dependents_are_filled_and_the_single_indicator_still_covers_them() -> None:
    frame = make_random_frame()
    frame.loc[0:3, "MonthlyIncome"] = None
    frame.loc[0:3, "NumberOfDependents"] = None

    cleaned, report = clean(frame)

    assert report.dependents_filled_rows == 4
    assert_nested_missingness_subsumed(cleaned)
    assert cleaned["NumberOfDependents"].notna().all()


def test_dependents_null_without_missing_income_is_refused() -> None:
    # The containment is a property of the source, not of our code. If a
    # delivery breaks it, one flag stops representing two columns and the
    # pipeline must say so rather than quietly mislabel 3,924 rows.
    frame = make_random_frame()
    frame["income_missing"] = 0
    frame.loc[0, "NumberOfDependents"] = None

    with pytest.raises(ValueError, match="no longer subsumes"):
        assert_nested_missingness_subsumed(frame)


# --------------------------------------------------------------------------
# Duplicates (inspection §7)
# --------------------------------------------------------------------------


def test_exact_duplicates_are_dropped_before_the_split() -> None:
    # Deduplicating after splitting would leave one copy in the reference and
    # one in the holdout, and the day-8 control set would be holding rows the
    # model trained on.
    frame = make_random_frame()
    duplicated = pd.concat([frame, frame.iloc[[3, 7]]], ignore_index=True)

    cleaned, report = clean(duplicated)

    assert report.source_duplicate_rows == 2
    assert report.dropped_duplicate_rows == 2
    assert not cleaned.duplicated().any()


def test_transform_induced_duplicates_are_counted_separately() -> None:
    # Two rows differing only in a DebtRatio that both get neutralised become
    # the same record. That collapse is self-inflicted, so the report
    # distinguishes it from what the source delivered.
    frame = make_random_frame()
    frame.loc[1] = frame.loc[0]
    frame.loc[0:1, "MonthlyIncome"] = None
    frame.loc[0:1, "NumberOfDependents"] = None
    frame.loc[0, "DebtRatio"] = 800.0
    frame.loc[1, "DebtRatio"] = 1600.0

    _, report = clean(frame)

    assert report.source_duplicate_rows == 0
    assert report.dropped_duplicate_rows == 1


# --------------------------------------------------------------------------
# The contract assertion — the one that lives in the pipeline
# --------------------------------------------------------------------------


def test_cleaned_output_satisfies_the_model_contract() -> None:
    frame = make_random_frame()
    frame.loc[0:4, "MonthlyIncome"] = None
    frame.loc[0:4, "NumberOfDependents"] = None
    frame.loc[5, "MonthlyIncome"] = 0.0
    frame.loc[6, "age"] = 0
    frame.loc[7:8, list(DELINQUENCY_COLUMNS)] = 98
    frame.loc[9, "RevolvingUtilizationOfUnsecuredLines"] = 50708.0
    frame.loc[10, "DebtRatio"] = 61106.5

    imputed, _, _ = pipeline(frame)

    assert_model_ready(imputed)  # raises if anything is left over


def test_assert_model_ready_raises_on_a_frame_that_was_not_cleaned() -> None:
    # The assertion has to be capable of failing, or it is decoration.
    frame = make_random_frame()
    frame.loc[0, "age"] = 0
    frame[INCOME_MISSING_COLUMN] = 0

    with pytest.raises(pa.errors.SchemaErrors):
        assert_model_ready(frame)


def test_out_of_range_age_is_dropped() -> None:
    # §5: exactly one row in the real data, at age 0.
    frame = make_random_frame()
    frame.loc[0, "age"] = 0

    _, report = clean(frame)

    assert report.dropped_out_of_range_age == 1


# --------------------------------------------------------------------------
# The feature matrix
# --------------------------------------------------------------------------


def test_feature_matrix_is_the_ten_contract_features_plus_the_indicator() -> None:
    frame = make_random_frame()
    imputed, _, _ = pipeline(frame)

    features, _ = build_feature_matrix(imputed)

    assert list(features.columns) == list(MODEL_FEATURES)
    assert len(MODEL_FEATURES) == 11
    assert TARGET_COLUMN not in features.columns
    assert INCOME_MISSING_COLUMN in features.columns
    assert features.notna().all().all()


def test_feature_matrix_target_is_numeric_and_matches_the_labels() -> None:
    # The contract stores the target as a categorical of the strings "0"/"1";
    # no estimator wants that.
    frame = make_random_frame()
    imputed, _, _ = pipeline(frame)

    features, target = build_feature_matrix(imputed)

    assert target.dtype == np.int8
    assert set(target.unique()) <= {0, 1}
    assert target.sum() == (imputed[TARGET_COLUMN] == "1").sum()
    assert len(features) == len(target)


def test_prepare_split_validates_and_returns_matched_x_and_y() -> None:
    frame = make_random_frame()
    cleaned, _ = clean(frame)
    fit = fit_imputation(cleaned)

    split = prepare_split("reference", cleaned, fit)

    assert split.rows == len(split.features) == len(split.target)
    assert 0.05 <= split.positive_rate <= 0.09
