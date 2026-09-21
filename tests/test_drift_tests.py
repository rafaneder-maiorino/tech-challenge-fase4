"""Tests for the A/A machinery and the MMD implementation."""

import numpy as np
import pandas as pd
import pytest
from conftest import make_random_frame

from credit_monitor.data.preprocess import (
    MODEL_FEATURES,
    clean,
    fit_imputation,
    impute,
)
from credit_monitor.drift_tests.aa import (
    _any_bh_rejection,
    run_aa,
    theoretical_psi_bias,
)
from credit_monitor.drift_tests.mmd import build_context, median_bandwidth, mmd_test


@pytest.fixture
def frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    """A disjoint reference/holdout pair, built by hand."""
    raw = make_random_frame(n_rows=1200, n_positive=80)
    raw.loc[0:239, "MonthlyIncome"] = None
    raw.loc[0:239, "NumberOfDependents"] = None
    cleaned, _ = clean(raw)
    full = impute(cleaned, fit_imputation(cleaned))
    return full.iloc[:800].reset_index(drop=True), full.iloc[800:].reset_index(
        drop=True
    )


# --------------------------------------------------------------------------
# MMD
# --------------------------------------------------------------------------


def test_mmd_of_two_halves_of_one_sample_is_essentially_zero(
    frames: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    # Two disjoint halves of the same population: MMD must sit at zero within
    # the null's own spread, and the permutation test must not reject.
    reference, _ = frames
    rng = np.random.default_rng(7)
    context, _ = build_context(reference, rng, n_per_side=300)
    scores = np.column_stack(
        [context.scorer.normal(reference[column]) for column in MODEL_FEATURES]
    )

    # Shuffled before splitting: the frame comes out of `clean` in its
    # original row order, so the income-missing rows sit at the front and two
    # positional halves are genuinely different populations. Taking them
    # positionally would make this test fail for a real reason that has
    # nothing to do with MMD.
    order = rng.permutation(len(scores))
    result = mmd_test(
        scores[order[:300]],
        scores[order[300:600]],
        context.bandwidth,
        rng,
        permutations=200,
    )

    assert abs(result.mmd2) < 5 * result.null_std
    assert result.pvalue > 0.05


def test_the_literal_self_comparison_returns_the_estimator_s_known_offset(
    frames: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    """Handing the *same rows* to both sides is not the zero case.

    The unbiased estimator drops the diagonal from each within-sample term but
    the cross term keeps every pair, so with X = Y the self-pairs survive only
    in the cross term and MMD^2 lands near -2/n rather than 0. Pinned because
    the obvious "MMD of a sample against itself is zero" test would fail here
    and look like a bug in the kernel.
    """
    reference, _ = frames
    rng = np.random.default_rng(7)
    context, scores = build_context(reference, rng, n_per_side=300)

    result = mmd_test(scores, scores.copy(), context.bandwidth, rng, permutations=50)

    assert result.mmd2 < 0
    assert abs(result.mmd2) < 4 / len(scores)
    assert result.pvalue > 0.5


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_permutation_pvalues_stay_in_the_unit_interval(
    frames: tuple[pd.DataFrame, pd.DataFrame], seed: int
) -> None:
    reference, holdout = frames
    rng = np.random.default_rng(seed)
    context, scores = build_context(reference, rng, n_per_side=300)
    other = context.transform(holdout.iloc[:300], MODEL_FEATURES)

    result = mmd_test(scores, other, context.bandwidth, rng, permutations=100)

    assert 0.0 < result.pvalue <= 1.0
    # The floor is 1/(permutations+1): the observed value is itself one draw
    # from the null, so exactly zero is unattainable.
    assert result.pvalue >= 1 / 101


def test_mmd_detects_a_shifted_sample(
    frames: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    # The test has to be capable of firing, or "did not detect" means nothing.
    reference, holdout = frames
    rng = np.random.default_rng(11)
    context, scores = build_context(reference, rng, n_per_side=400)
    shifted = holdout.iloc[:400].copy()
    shifted["RevolvingUtilizationOfUnsecuredLines"] *= 3.0

    result = mmd_test(
        scores,
        context.transform(shifted, MODEL_FEATURES),
        context.bandwidth,
        rng,
        permutations=200,
    )

    assert result.detected


def test_bandwidth_is_positive_and_stable(
    frames: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    reference, _ = frames
    scores = np.column_stack(
        [
            build_context(reference, np.random.default_rng(0), n_per_side=200)[
                0
            ].scorer.normal(reference[column])
            for column in MODEL_FEATURES
        ]
    )

    first = median_bandwidth(scores, np.random.default_rng(3))
    second = median_bandwidth(scores, np.random.default_rng(3))

    assert first > 0
    assert first == second


# --------------------------------------------------------------------------
# A/A
# --------------------------------------------------------------------------


def test_aa_draws_come_only_from_the_holdout(
    frames: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    # An A/A test whose "current" side contained reference rows would be
    # comparing a set against itself and would understate every rate.
    reference, holdout = frames
    result = run_aa(reference, holdout, seed=5, sizes=(200,), repeats=3)

    assert len(result.frame) == 3 * len(MODEL_FEATURES)
    assert result.reference_rows == len(reference)
    # The fixture splits one frame positionally, so the two sides share no row.
    merged = reference.merge(holdout, how="inner", on=list(reference.columns))
    assert len(merged) == 0


def test_aa_is_reproducible_for_a_given_seed(
    frames: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    reference, holdout = frames

    first = run_aa(reference, holdout, seed=42, sizes=(200,), repeats=3).frame
    second = run_aa(reference, holdout, seed=42, sizes=(200,), repeats=3).frame

    pd.testing.assert_frame_equal(first, second)


def test_a_different_seed_gives_different_draws(
    frames: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    reference, holdout = frames

    first = run_aa(reference, holdout, seed=1, sizes=(200,), repeats=3).frame
    second = run_aa(reference, holdout, seed=2, sizes=(200,), repeats=3).frame

    assert not first["psi"].equals(second["psi"])


def test_psi_bias_shrinks_with_n_and_grows_with_bins() -> None:
    # The prediction the report plots the measurement against.
    assert theoretical_psi_bias(250, 100_000, 10) > theoretical_psi_bias(
        7000, 100_000, 10
    )
    assert theoretical_psi_bias(250, 100_000, 26) > theoretical_psi_bias(
        250, 100_000, 10
    )


@pytest.mark.parametrize(
    ("pvalues", "expected"),
    [
        ([0.001] + [0.9] * 10, True),
        ([0.5] * 11, False),
        ([0.03] + [0.9] * 10, False),
    ],
)
def test_benjamini_hochberg_rejection(pvalues: list[float], expected: bool) -> None:
    # A single p of 0.03 does not survive BH across 11 hypotheses, which is why
    # BH and Bonferroni coincide under the null in this project's A/A.
    assert _any_bh_rejection(np.array(pvalues)) is expected


# --------------------------------------------------------------------------
# The permutation floor
# --------------------------------------------------------------------------


@pytest.mark.parametrize("permutations", [200, 1000])
def test_a_floored_pvalue_is_reported_as_an_upper_bound(permutations: int) -> None:
    # With B permutations the smallest attainable p is 1/(B+1), because the
    # observed statistic counts as one draw from the null. Printing that as an
    # exact value invents precision the test never had.
    from credit_monitor.reporting.drift_tests_report import format_pvalue

    floor = 1.0 / (permutations + 1)

    rendered = format_pvalue(floor, permutations)

    assert rendered.startswith("≤")
    assert "piso" in rendered
    assert format_pvalue(0.42, permutations) == "0.4200"
