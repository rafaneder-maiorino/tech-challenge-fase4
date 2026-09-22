"""The fairness arithmetic, pinned where it would fail silently.

Every test here guards a number that would otherwise be wrong in a direction
nobody notices: an interval that does not contain its own estimate, a band that
swallows an age it should not, a criterion computed over a cell too small to
support it.
"""

import math
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest

from credit_monitor.fairness.bands import (
    AGE_BANDS,
    MIN_CELL_FOR_COMPARISON,
    assign_bands,
    band_metrics,
    band_of,
    common_comparable_bands,
    comparable_bands,
    fairness_criteria,
    wilson_interval,
)


def test_the_bands_tile_the_adult_age_range_without_gap_or_overlap() -> None:
    # A gap would drop people silently; an overlap would count them twice. The
    # contract rejects ages outside [18, 110], so 18 upward is what must tile.
    assert all(band_of(age) != "fora" for age in range(18, 111))

    # Each band starts exactly where the previous one ended, and the labels come
    # out in age order — the document reads them as an ordered axis.
    for (_, _, high), (_, low, _) in pairwise(AGE_BANDS):
        assert low == high + 1

    seen = [band_of(age) for age in range(18, 111)]
    assert seen == sorted(seen, key=[label for label, _, _ in AGE_BANDS].index)


def test_wilson_stays_inside_the_unit_interval_where_the_normal_would_not() -> None:
    # 2 of 80 with the normal approximation gives a lower bound below zero.
    # That is the whole reason Wilson is here: the 18-25 band is this small.
    low, high = wilson_interval(2, 80)

    assert low > 0.0
    assert high < 1.0
    assert low < 2 / 80 < high


def test_a_wilson_interval_always_contains_its_own_estimate() -> None:
    for successes, total in ((0, 10), (1, 10), (5, 10), (9, 10), (10, 10), (80, 781)):
        low, high = wilson_interval(successes, total)
        assert low <= successes / total <= high


def test_wilson_on_an_empty_cell_is_nan_rather_than_a_confident_zero() -> None:
    low, high = wilson_interval(0, 0)

    assert math.isnan(low)
    assert math.isnan(high)


def _population(n_per_band: int, positive_rate: float) -> tuple[pd.Series, np.ndarray]:
    ages = []
    for _, low, _ in AGE_BANDS:
        ages.extend([low] * n_per_band)
    rng = np.random.default_rng(20260922)
    target = (rng.random(len(ages)) < positive_rate).astype("int8")
    return pd.Series(assign_bands(pd.Series(ages))), target


def test_a_perfectly_equal_population_has_zero_disparity_on_every_criterion() -> None:
    # The null case. If this drifts off zero, every non-zero number the document
    # reports is measuring the estimator, not the model.
    bands, _ = _population(2000, 0.0)
    target = np.zeros(len(bands), dtype="int8")
    target[::10] = 1  # the same 10% in every band, by construction
    scores = np.where(target == 1, 0.9, 0.1)

    rows = band_metrics(bands, target, scores, threshold=0.5)
    criteria = fairness_criteria(rows)

    assert criteria.demographic_parity_difference == pytest.approx(0.0)
    assert criteria.equalised_odds_difference == pytest.approx(0.0)
    assert criteria.calibration_difference == pytest.approx(0.0)
    assert criteria.base_rate_difference == pytest.approx(0.0)


def test_a_band_below_the_comparison_floor_is_reported_but_never_compared() -> None:
    # The 18-25 band in the real holdout has 80 positives. It must appear in the
    # table with its interval and stay out of the criteria, or a rate that
    # swings on a handful of rows would set the reported maximum.
    ages = [20] * 40 + [30] * 4000
    target = np.array([1] * 20 + [0] * 20 + [1] * 400 + [0] * 3600, dtype="int8")
    scores = np.where(target == 1, 0.9, 0.1)
    rows = band_metrics(pd.Series(assign_bands(pd.Series(ages))), target, scores, 0.5)

    small = next(row for row in rows if row.band == "18-25")
    assert small.tpr_n < MIN_CELL_FOR_COMPARISON
    assert not small.comparable
    assert not math.isnan(small.tpr)  # reported, with its interval

    assert "18-25" not in fairness_criteria(rows).bands_compared


def test_the_criteria_refuse_to_report_when_no_band_is_large_enough() -> None:
    ages = [20] * 30 + [30] * 30
    target = np.array([1, 0] * 30, dtype="int8")
    rows = band_metrics(
        pd.Series(assign_bands(pd.Series(ages))), target, np.full(60, 0.5), 0.5
    )

    with pytest.raises(ValueError, match="comparar"):
        fairness_criteria(rows)


def test_the_calibration_gap_is_predicted_minus_observed_and_signed() -> None:
    # Sign convention, pinned: negative means the model under-predicts risk,
    # which is the direction that hurts. Flipping it would invert every reading
    # in docs/vies.md without any test failing.
    ages = [30] * 1000
    target = np.zeros(1000, dtype="int8")
    target[:200] = 1  # observed 20%
    rows = band_metrics(
        pd.Series(assign_bands(pd.Series(ages))), target, np.full(1000, 0.05), 0.5
    )

    assert rows[0].calibration_gap == pytest.approx(0.05 - 0.20)
    assert rows[0].calibration_gap < 0


def test_a_threshold_above_every_score_approves_everyone() -> None:
    ages = [30] * 500 + [70] * 500
    target = np.zeros(1000, dtype="int8")
    target[::5] = 1
    rows = band_metrics(
        pd.Series(assign_bands(pd.Series(ages))), target, np.full(1000, 0.1), 0.99
    )

    assert all(row.approval_rate == 1.0 for row in rows)
    assert all(row.tpr == 0.0 for row in rows)
    assert all(row.fpr == 0.0 for row in rows)


def _table(band_sizes: dict[str, int], positive_rate: float) -> list:
    ages = []
    starts = {label: low for label, low, _ in AGE_BANDS}
    for label, size in band_sizes.items():
        ages.extend([starts[label]] * size)
    target = np.zeros(len(ages), dtype="int8")
    step = max(1, round(1 / positive_rate))
    target[::step] = 1
    scores = np.where(target == 1, 0.9, 0.1)
    return band_metrics(pd.Series(assign_bands(pd.Series(ages))), target, scores, 0.5)


def test_the_common_comparable_set_is_the_intersection_in_band_order() -> None:
    # Month 0 has three comparable bands and month 6 has four. Subtracting a
    # criterion computed over different sets subtracts answers to different
    # questions — this is the guard that stops it.
    small = _table({"26-35": 900, "36-45": 900, "46-55": 150}, 0.5)
    large = _table({"26-35": 900, "36-45": 900, "46-55": 900}, 0.5)

    assert common_comparable_bands(small, large) == ("26-35", "36-45")
    assert comparable_bands(large) == ("26-35", "36-45", "46-55")


def test_restricting_the_band_set_changes_the_criterion_it_reports() -> None:
    # The whole point: a band left in or out moves the number, so the set has
    # to be declared rather than implied.
    ages = [30] * 2000 + [40] * 2000 + [50] * 2000
    target = np.zeros(6000, dtype="int8")
    target[:600] = 1  # 26-35 defaults at 30%
    target[2000:2200] = 1  # 36-45 at 10%
    target[4000:4200] = 1  # 46-55 at 10%
    rows = band_metrics(
        pd.Series(assign_bands(pd.Series(ages))), target, np.full(6000, 0.10), 0.5
    )

    everything = fairness_criteria(rows)
    without_the_outlier = fairness_criteria(rows, restrict_to=("36-45", "46-55"))

    assert everything.base_rate_difference == pytest.approx(0.20)
    assert without_the_outlier.base_rate_difference == pytest.approx(0.0)
    assert without_the_outlier.bands_compared == ("36-45", "46-55")
