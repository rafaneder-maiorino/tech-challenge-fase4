"""Tests for the three baseline metrics, on cases whose answers are known.

Each metric is pinned against a situation where the correct value can be
derived by hand — a perfect separator, a coin flip, a constant — rather than
against a number produced by an earlier run of the same code.
"""

import numpy as np
import pytest

from credit_monitor.models.metrics import evaluate, ks_separation

PERFECT_LABELS = np.array([0] * 50 + [1] * 50)
PERFECT_SCORES = np.concatenate([np.linspace(0.0, 0.4, 50), np.linspace(0.6, 1.0, 50)])


def test_perfect_separation_scores_perfectly() -> None:
    metrics = evaluate(PERFECT_LABELS, PERFECT_SCORES)

    assert metrics.auc_roc == 1.0
    assert metrics.ks_statistic == 1.0


def test_ks_is_the_largest_gap_between_the_two_cumulative_distributions() -> None:
    # Half the positives score 0.9 and half score 0.1, all negatives 0.1: at a
    # threshold just below 0.9 the model has caught 50% of defaulters and 0%
    # of the rest, so KS is 0.5 exactly.
    labels = np.array([1] * 50 + [1] * 50 + [0] * 100)
    scores = np.array([0.9] * 50 + [0.1] * 50 + [0.1] * 100)

    ks, threshold = ks_separation(labels, scores)

    assert ks == pytest.approx(0.5)
    assert threshold == pytest.approx(0.9)


def test_a_coin_flip_separates_nothing() -> None:
    labels = np.array([0, 1] * 100)
    scores = np.full(200, 0.5)

    metrics = evaluate(labels, scores)

    assert metrics.auc_roc == pytest.approx(0.5)
    assert metrics.ks_statistic == pytest.approx(0.0)


def test_brier_is_zero_for_a_perfectly_confident_correct_model() -> None:
    labels = np.array([0, 0, 1, 1])
    scores = np.array([0.0, 0.0, 1.0, 1.0])

    assert evaluate(labels, scores).brier == pytest.approx(0.0)


def test_brier_is_one_for_a_perfectly_confident_wrong_model() -> None:
    labels = np.array([0, 0, 1, 1])
    scores = np.array([1.0, 1.0, 0.0, 0.0])

    assert evaluate(labels, scores).brier == pytest.approx(1.0)


def test_brier_of_a_constant_base_rate_prediction_is_p_times_one_minus_p() -> None:
    # The reference every calibrated model has to beat, and the one that shows
    # a reweighted model scoring worse than a constant. At the 6.68% rate of
    # inspection §2 this is 0.0623.
    rate = 0.0668
    labels = np.array([1] * 668 + [0] * 9332)
    scores = np.full(10000, rate)

    brier = evaluate(labels, scores).brier

    assert brier == pytest.approx(rate * (1 - rate), abs=1e-4)
    assert brier == pytest.approx(0.0623, abs=1e-4)


def test_auc_and_ks_are_blind_to_a_monotone_rescaling_but_brier_is_not() -> None:
    # The mechanism behind the calibration finding: reweighting inflates
    # probabilities, which is a monotone transform, so the two ranking metrics
    # cannot see it and the one calibration metric moves a lot.
    rng = np.random.default_rng(20260920)
    labels = (rng.random(4000) < 0.0668).astype(int)
    scores = np.clip(rng.beta(2, 20, 4000) + labels * 0.15, 1e-6, 1 - 1e-6)

    # Exactly what class weighting does to a probability: multiply the odds by
    # the weight. Strictly increasing, so the ranking is untouched by
    # construction — clipping would instead create ties and move the AUC for
    # a reason that has nothing to do with calibration.
    odds_multiplier = 14.0
    odds = scores / (1 - scores) * odds_multiplier
    inflated = odds / (1 + odds)

    honest, distorted = evaluate(labels, scores), evaluate(labels, inflated)

    assert distorted.auc_roc == pytest.approx(honest.auc_roc, abs=1e-9)
    assert distorted.ks_statistic == pytest.approx(honest.ks_statistic, abs=1e-9)
    assert distorted.brier > honest.brier * 2


def test_metrics_report_the_set_they_were_computed_on() -> None:
    labels = np.array([1] * 10 + [0] * 90)

    metrics = evaluate(labels, np.full(100, 0.1))

    assert metrics.rows == 100
    assert metrics.positive_rate == pytest.approx(0.10)
