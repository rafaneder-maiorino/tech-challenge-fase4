"""Maximum Mean Discrepancy: detecting drift that no marginal reveals.

Every univariate test — PSI, KS, chi-square — asks the same question eleven
times: *did this column's distribution move?* A batch can answer "no" eleven
times and still be drawn from a different joint distribution, because
dependence between columns is not a property of any column.

The stage-2 simulator produces exactly that batch on purpose: two columns have
their dependence inverted by **permutation**, so every marginal is identical by
construction and the univariate PSI comes out at 0.0041 — green with two orders
of magnitude to spare. MMD is the test that can see it.

Three implementation choices, each of which would break the test if made the
obvious way:

**Rank-based normal scores, not raw values.** The kernel is a function of
distance, so a column with standard deviation 249.76 and a maximum of 50,708
would dominate every pairwise distance and the test would measure that column
alone. This is the lesson of ``docs/findings.md`` §4, §6 and §8 applied to
kernels: the same transform the simulator uses, fitted on the reference.

**Median heuristic for the bandwidth, computed on the reference.** A bandwidth
much smaller than typical distances makes the kernel matrix approximately the
identity and MMD approximately constant; much larger and every point looks
alike. The median pairwise distance puts the kernel in the regime where it
discriminates, and computing it on the reference keeps it a fixed yardstick
rather than something each batch renegotiates.

**Permutation test, not an asymptotic null.** The null distribution of MMD has
no convenient closed form, and the permutation test is exact under exchange-
ability, which is precisely the null being tested.
"""

import logging
from dataclasses import asdict, dataclass
from typing import Any, Final

import numpy as np
import pandas as pd

from credit_monitor.data.preprocess import MODEL_FEATURES
from credit_monitor.simulation.scores import RankNormalScorer

log = logging.getLogger(__name__)

# Per side. The kernel matrix is (2n)^2 float64 = 128 MB at n = 2000, which is
# the largest that stays comfortable in memory while giving the permutation
# test enough resolution.
DEFAULT_N_PER_SIDE: Final[int] = 2000
DEFAULT_PERMUTATIONS: Final[int] = 1000

# Rows used to estimate the median pairwise distance. The median is stable well
# before this; more would only cost time.
BANDWIDTH_SAMPLE: Final[int] = 2000


def median_bandwidth(scores: np.ndarray, rng: np.random.Generator) -> float:
    """Median pairwise Euclidean distance, the classic kernel bandwidth.

    Args:
        scores: Reference rows in rank-normal-score space.
        rng: For subsampling when the reference is large.

    Returns:
        The median distance; never zero, so the kernel stays defined.
    """
    size = min(len(scores), BANDWIDTH_SAMPLE)
    sample = scores[rng.choice(len(scores), size=size, replace=False)]
    squared = np.maximum(
        (sample**2).sum(1)[:, None]
        + (sample**2).sum(1)[None, :]
        - 2 * sample @ sample.T,
        0.0,
    )
    upper = squared[np.triu_indices_from(squared, k=1)]
    return float(max(np.sqrt(np.median(upper)), 1e-12))


def rbf_kernel(joint: np.ndarray, bandwidth: float) -> np.ndarray:
    """Gaussian kernel matrix over the stacked sample."""
    squared = np.maximum(
        (joint**2).sum(1)[:, None] + (joint**2).sum(1)[None, :] - 2 * joint @ joint.T,
        0.0,
    )
    return np.exp(-squared / (2 * bandwidth**2))


def _mmd2_from_sums(
    sum_xx: np.ndarray, sum_x_all: np.ndarray, total: float, n: int, m: int
) -> np.ndarray:
    """Unbiased MMD^2 from block sums, vectorised over permutations.

    The identity that makes 1000 permutations cheap. With ``a`` the 0/1
    indicator of the first sample, ``a' K a`` gives the within-first block sum
    and ``a' K 1`` gives that plus the cross block, so one matrix product
    against all indicator vectors at once yields every permutation's statistic.
    Recomputing the kernel per permutation would be a thousand times the work
    for the same numbers.

    The diagonal is excluded (k(x, x) = 1 for the RBF), which is what makes the
    estimator unbiased rather than inflated by each point's self-similarity.
    """
    sum_xy = sum_x_all - sum_xx
    sum_yy = total - 2 * sum_x_all + sum_xx
    return (
        (sum_xx - n) / (n * (n - 1))
        + (sum_yy - m) / (m * (m - 1))
        - 2 * sum_xy / (n * m)
    )


@dataclass(frozen=True, slots=True)
class MMDResult:
    """One MMD test."""

    label: str
    mmd2: float
    pvalue: float
    n_per_side: int
    permutations: int
    bandwidth: float
    null_mean: float
    null_std: float

    @property
    def detected(self) -> bool:
        """Whether the permutation test rejects at 5%."""
        return self.pvalue < 0.05

    def to_dict(self) -> dict[str, Any]:
        """Render for the report tables."""
        return {**asdict(self), "detected": self.detected}


def mmd_test(
    reference_scores: np.ndarray,
    current_scores: np.ndarray,
    bandwidth: float,
    rng: np.random.Generator,
    label: str = "",
    permutations: int = DEFAULT_PERMUTATIONS,
) -> MMDResult:
    """Permutation test for equality of joint distributions.

    Args:
        reference_scores: Reference side, already rank-transformed.
        current_scores: Batch side, same transform.
        bandwidth: From :func:`median_bandwidth`, fitted on the reference.
        rng: The generator.
        label: Name for the report.
        permutations: Null draws.

    Returns:
        The statistic, its permutation p-value and the null's shape.
    """
    n, m = len(reference_scores), len(current_scores)
    joint = np.vstack([reference_scores, current_scores])
    kernel = rbf_kernel(joint, bandwidth)
    total = float(kernel.sum())
    row_sums = kernel.sum(1)

    observed_indicator = np.zeros(n + m)
    observed_indicator[:n] = 1.0
    observed = float(
        _mmd2_from_sums(
            observed_indicator @ kernel @ observed_indicator,
            observed_indicator @ row_sums,
            total,
            n,
            m,
        )
    )

    # All permutations at once: one (n+m) x permutations matrix product.
    indicators = np.zeros((n + m, permutations))
    for column in range(permutations):
        indicators[rng.permutation(n + m)[:n], column] = 1.0
    projected = kernel @ indicators
    null = _mmd2_from_sums(
        np.einsum("ij,ij->j", indicators, projected),
        indicators.T @ row_sums,
        total,
        n,
        m,
    )

    # +1 in numerator and denominator: the observed value is itself one draw
    # from the null under the hypothesis, so a p-value of exactly zero is not
    # attainable and 1/(permutations+1) is the floor.
    pvalue = float((np.sum(null >= observed) + 1) / (permutations + 1))
    return MMDResult(
        label=label,
        mmd2=observed,
        pvalue=pvalue,
        n_per_side=n,
        permutations=permutations,
        bandwidth=bandwidth,
        null_mean=float(null.mean()),
        null_std=float(null.std(ddof=1)),
    )


@dataclass(frozen=True, slots=True)
class MMDContext:
    """The reference side, transformed once and reused by every test."""

    scorer: RankNormalScorer
    reference_scores: np.ndarray
    bandwidth: float

    def transform(self, frame: pd.DataFrame, columns: tuple[str, ...]) -> np.ndarray:
        """Rank-transform a batch with the reference-fitted scorer."""
        return np.column_stack(
            [self.scorer.normal(frame[column]) for column in columns]
        )


def build_context(
    reference: pd.DataFrame,
    rng: np.random.Generator,
    columns: tuple[str, ...] = MODEL_FEATURES,
    n_per_side: int = DEFAULT_N_PER_SIDE,
) -> tuple[MMDContext, np.ndarray]:
    """Fit the scorer and the bandwidth on the reference, once.

    Returns:
        The context and a reference subsample of ``n_per_side`` rows, already
        transformed, for use as the left-hand side of every test.
    """
    scorer = RankNormalScorer.fit(reference, columns)
    full = np.column_stack([scorer.normal(reference[column]) for column in columns])
    bandwidth = median_bandwidth(full, rng)
    sample = full[rng.choice(len(full), size=n_per_side, replace=False)]
    context = MMDContext(scorer=scorer, reference_scores=sample, bandwidth=bandwidth)
    log.info(
        "mmd.context_built",
        extra={
            "columns": len(columns),
            "bandwidth": bandwidth,
            "n_per_side": n_per_side,
        },
    )
    return context, sample


@dataclass(frozen=True, slots=True)
class PairMMD:
    """MMD for one feature pair, for localization."""

    column_x: str
    column_y: str
    mmd2: float
    pvalue: float


def localize(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    context: MMDContext,
    rng: np.random.Generator,
    n_per_side: int = 1000,
    permutations: int = 200,
    columns: tuple[str, ...] = MODEL_FEATURES,
) -> list[PairMMD]:
    """Run MMD on every feature pair, to say *where* the joint moved.

    MMD answers "did the joint distribution change" and nothing else — it is a
    single number over eleven dimensions. Running it per pair recovers the
    missing half of the answer, at the cost of 55 tests instead of one. Fewer
    permutations are enough here because the question is a ranking, not a
    p-value: the pair the simulator inverted should come out on top.
    """
    import itertools

    reference_rows = rng.choice(len(reference), size=n_per_side, replace=False)
    current_rows = rng.choice(len(current), size=n_per_side, replace=False)
    results: list[PairMMD] = []
    for column_x, column_y in itertools.combinations(columns, 2):
        left = np.column_stack(
            [
                context.scorer.normal(reference[column].iloc[reference_rows])
                for column in (column_x, column_y)
            ]
        )
        right = np.column_stack(
            [
                context.scorer.normal(current[column].iloc[current_rows])
                for column in (column_x, column_y)
            ]
        )
        bandwidth = median_bandwidth(left, np.random.default_rng(0))
        result = mmd_test(left, right, bandwidth, rng, permutations=permutations)
        results.append(PairMMD(column_x, column_y, result.mmd2, result.pvalue))
    return sorted(results, key=lambda pair: -pair.mmd2)


def run_self_aa(
    reference: pd.DataFrame,
    holdout: pd.DataFrame,
    context: MMDContext,
    rng: np.random.Generator,
    repeats: int = 100,
    n_per_side: int = DEFAULT_N_PER_SIDE,
    permutations: int = 200,
    columns: tuple[str, ...] = MODEL_FEATURES,
) -> np.ndarray:
    """MMD's own A/A: reference against holdout, where nothing drifted.

    A test that detects drift between two samples of the same population is
    worthless no matter how sensitive it is elsewhere, so MMD gets the same
    treatment as PSI and KS before being trusted.
    """
    left_all = np.column_stack(
        [context.scorer.normal(reference[column]) for column in columns]
    )
    right_all = np.column_stack(
        [context.scorer.normal(holdout[column]) for column in columns]
    )
    pvalues = []
    for _ in range(repeats):
        left = left_all[rng.choice(len(left_all), n_per_side, replace=False)]
        right = right_all[rng.choice(len(right_all), n_per_side, replace=False)]
        pvalues.append(
            mmd_test(
                left, right, context.bandwidth, rng, permutations=permutations
            ).pvalue
        )
    return np.array(pvalues)
