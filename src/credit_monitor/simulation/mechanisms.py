"""The three drift mechanisms, plus the dependence-only permutation.

Each function is pure: frame in, frame out, no files, no global state, and the
random generator is always passed in. That is what makes the ablation possible
— turning a mechanism off is not calling one of these — and what makes the
same seed produce byte-identical batches.

See ``docs/simulation.md`` for the causal story each mechanism implements.
"""

import logging
from dataclasses import dataclass
from typing import Final

import numpy as np
import pandas as pd
from scipy.stats import norm, spearmanr

from credit_monitor.constants import TARGET_COLUMN
from credit_monitor.contracts.raw import POSITIVE_LABEL
from credit_monitor.simulation.config import CompositionParams, SimulationConfig
from credit_monitor.simulation.scores import RankNormalScorer

log = logging.getLogger(__name__)

UTILIZATION: Final[str] = "RevolvingUtilizationOfUnsecuredLines"
AGE: Final[str] = "age"
LATE_30_59: Final[str] = "NumberOfTime30-59DaysPastDueNotWorse"
LATE_60_89: Final[str] = "NumberOfTime60-89DaysPastDueNotWorse"
LATE_90: Final[str] = "NumberOfTimes90DaysLate"
INCOME: Final[str] = "MonthlyIncome"
DEBT_RATIO: Final[str] = "DebtRatio"
INCOME_MISSING: Final[str] = "income_missing"

# Columns the composition mechanism weights on, and therefore the ones the
# rank scorer has to be fitted for.
SCORED_COLUMNS: Final[tuple[str, ...]] = (
    UTILIZATION,
    AGE,
    LATE_30_59,
    LATE_60_89,
    LATE_90,
)

# Bisection bounds and budget for hitting a target Spearman.
_RHO_BOUNDS: Final[tuple[float, float]] = (-0.999, 0.999)
_BISECTION_STEPS: Final[int] = 40


# --------------------------------------------------------------------------
# Mechanism 1 — portfolio composition
# --------------------------------------------------------------------------


def composition_log_weights(
    frame: pd.DataFrame,
    scorer: RankNormalScorer,
    params: CompositionParams,
    intensity: float,
    lag_d60: float,
    lag_d90: float,
) -> np.ndarray:
    """Log sampling weights: the exponent of ``w_i`` from the design document.

    Returned as logs rather than weights because the sampler works in log
    space anyway, and because ``exp`` of a large exponent overflows while its
    log does not.

    Args:
        frame: The pool being sampled (the holdout).
        scorer: Rank scorer fitted on the reference.
        params: Exponent weights and lags.
        intensity: ``s`` for this month.
        lag_d60: Ramp for the 60-89 day bucket, 0 before its lag month.
        lag_d90: Ramp for the 90+ day bucket.

    Returns:
        One log weight per row.
    """
    exponent = (
        params.b_util * scorer.normal(frame[UTILIZATION])
        # Minus: the shock brings in YOUNGER applicants, so a low age gets the
        # high weight. The sign lives here rather than in the config so that a
        # positive b_age in the YAML reads as "age matters this much".
        + params.b_age * (-scorer.normal(frame[AGE]))
        + params.b_d30 * scorer.normal(frame[LATE_30_59])
        + params.b_d60 * lag_d60 * scorer.normal(frame[LATE_60_89])
        + params.b_d90 * lag_d90 * scorer.normal(frame[LATE_90])
    )
    return intensity * exponent


def sample_without_replacement(
    log_weights: np.ndarray, size: int, rng: np.random.Generator
) -> np.ndarray:
    """Weighted sample without replacement, via the Gumbel top-k trick.

    Adding Gumbel noise to log weights and taking the top ``k`` is exactly
    weighted sampling without replacement (the Plackett-Luce / Gumbel top-k
    identity). It is used instead of ``Generator.choice(replace=False, p=...)``
    because it is one vectorised pass and because it stays numerically sane
    when the weights span several orders of magnitude.

    Without replacement is not a detail: drawing the same customer twice would
    put a duplicate row in the batch, the contract would raise its duplicates
    warning, and the monitor would be reporting a simulator artefact as drift.

    Args:
        log_weights: Log weight per candidate row.
        size: Rows to draw.
        rng: The generator; the only source of randomness.

    Returns:
        Positional indices of the sampled rows, sorted ascending so the batch
        keeps the pool's row order and the output is order-stable.
    """
    gumbel = rng.gumbel(size=len(log_weights))
    keys = log_weights + gumbel
    chosen = np.argpartition(-keys, size - 1)[:size]
    return np.sort(chosen)


def draw_batch(
    pool: pd.DataFrame,
    scorer: RankNormalScorer,
    config: SimulationConfig,
    month: int,
    rng: np.random.Generator,
    weighted: bool,
) -> pd.DataFrame:
    """Draw one month's batch from the pool.

    Args:
        pool: The holdout.
        scorer: Rank scorer fitted on the reference.
        config: The frozen parameters.
        month: 0 for the control batch.
        rng: The generator.
        weighted: False draws a uniform sample, which is what month 0 and the
            multivariate base need, and what the composition mechanism being
            off means.

    Returns:
        A batch of ``config.batch_size`` rows, positionally re-indexed.
    """
    if weighted and month > 0:
        log_weights = composition_log_weights(
            pool,
            scorer,
            config.composition,
            config.intensity(month),
            config.lag_ramp(month, config.composition.lag_d60_months),
            config.lag_ramp(month, config.composition.lag_d90_months),
        )
    else:
        log_weights = np.zeros(len(pool))
    indices = sample_without_replacement(log_weights, config.batch_size, rng)
    return pool.iloc[indices].reset_index(drop=True)


# --------------------------------------------------------------------------
# Mechanism 2 — nominal inflation
# --------------------------------------------------------------------------


def apply_inflation(
    frame: pd.DataFrame, pi_total: float, intensity: float
) -> tuple[pd.DataFrame, int]:
    """Inflate nominal income and deflate DebtRatio, where income exists.

    The labels are deliberately untouched. The person's real risk has not
    changed; what changed is the number the model reads. That is concept drift
    by measurement, and it is the mechanism that makes the degradation silent —
    both of the features the model most relies on to say "safe" move the
    reassuring way at exactly the wrong moment.

    Rows with income missing are skipped, and not for convenience: inspection
    §9 showed their ``DebtRatio`` is a different quantity (median 1,159 against
    0.296), which preprocessing replaced with the reference median. Dividing
    that by an income they do not have would be arithmetic on a unit error.

    Args:
        frame: The batch.
        pi_total: Total inflation at full intensity.
        intensity: ``s`` for this month.

    Returns:
        The batch and the number of rows actually touched.
    """
    out = frame.copy()
    factor = 1 + pi_total * intensity
    touched = out[INCOME_MISSING] == 0
    out.loc[touched, INCOME] = out.loc[touched, INCOME] * factor
    out.loc[touched, DEBT_RATIO] = out.loc[touched, DEBT_RATIO] / factor
    return out, int(touched.sum())


# --------------------------------------------------------------------------
# Mechanism 3 — macroeconomic stress
# --------------------------------------------------------------------------


def stress_flip_scale(
    frame: pd.DataFrame,
    scorer: RankNormalScorer,
    extra_default_rate: float,
) -> float:
    """Solve the flip scale analytically for a target extra default rate.

    We want the expected number of flips at full intensity to be
    ``extra_default_rate * n``, with each non-defaulter's flip probability
    proportional to its utilisation rank. That is one equation in one unknown:

        sum over y=0 of (c * u_i) = extra * n     =>     c = extra * n / sum(u_i)

    Solving it rather than tuning a constant by hand is what makes the extra
    default rate a *parameter* instead of an outcome — at intensity ``s`` the
    expected extra rate is exactly ``s * extra``, linear in the month.

    Args:
        frame: The batch, before flipping.
        scorer: Rank scorer fitted on the reference.
        extra_default_rate: Target extra rate at ``s = 1``.

    Returns:
        The scale ``c``.
    """
    eligible = frame[TARGET_COLUMN] != POSITIVE_LABEL
    ranks = scorer.uniform(frame[UTILIZATION])
    total = float(ranks[eligible.to_numpy()].sum())
    if total <= 0:
        return 0.0
    return extra_default_rate * len(frame) / total


def apply_stress(
    frame: pd.DataFrame,
    scorer: RankNormalScorer,
    extra_default_rate: float,
    intensity: float,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, int]:
    """Flip labels 0 -> 1, never 1 -> 0, with probability set by utilisation.

    One-directional on purpose: a recession makes good borrowers default. It
    does not retroactively make a defaulter creditworthy, and allowing the
    reverse flip would let the mechanism cancel itself out and quietly reduce
    the drift it is supposed to introduce.

    Args:
        frame: The batch.
        scorer: Rank scorer fitted on the reference.
        extra_default_rate: Target extra rate at ``s = 1``.
        intensity: ``s`` for this month.
        rng: The generator.

    Returns:
        The batch and the realised number of flips.
    """
    out = frame.copy()
    scale = stress_flip_scale(out, scorer, extra_default_rate)
    ranks = scorer.uniform(out[UTILIZATION])
    eligible = (out[TARGET_COLUMN] != POSITIVE_LABEL).to_numpy()
    probability = np.clip(scale * intensity * ranks, 0.0, 1.0) * eligible
    flipped = rng.random(len(out)) < probability

    labels = out[TARGET_COLUMN].astype(str).to_numpy().copy()
    labels[flipped] = POSITIVE_LABEL
    out[TARGET_COLUMN] = pd.Categorical(
        labels, categories=frame[TARGET_COLUMN].cat.categories, ordered=True
    )
    return out, int(flipped.sum())


# --------------------------------------------------------------------------
# The dependence-only scenario
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DependenceResult:
    """What the permutation achieved, for the report."""

    column_x: str
    column_y: str
    reference_spearman: float
    before_spearman: float
    after_spearman: float
    target_spearman: float
    rho_used: float


def _reorder_to_latent(values: np.ndarray, latent: np.ndarray) -> np.ndarray:
    """Reassign ``values`` among rows so their order follows ``latent``.

    A pure permutation: the returned array is ``values`` sorted and then dealt
    out by the ranks of ``latent``, so the multiset is identical by
    construction. That is what lets every marginal distribution stay exactly
    what it was while the dependence changes.
    """
    ranks = np.argsort(np.argsort(latent))
    return np.sort(values)[ranks]


def invert_dependence(
    frame: pd.DataFrame,
    column_x: str,
    column_y: str,
    target_spearman: float,
    rng: np.random.Generator,
    reference_spearman: float,
    tolerance: float = 0.02,
) -> tuple[pd.DataFrame, DependenceResult]:
    """Reassign ``column_y`` among rows to hit a target Spearman with ``column_x``.

    The mechanism is a Gaussian copula: build a latent variable correlated with
    ``column_x`` at ``rho``, then deal ``column_y``'s own values out in the
    latent's rank order. Spearman and the Pearson correlation of a Gaussian
    copula are related by ``rho_s = (6/pi) * arcsin(rho/2)``, which gives the
    starting guess; because the noise draw is fixed first, the achieved
    Spearman is monotone in ``rho`` and a bisection lands on the target exactly.

    Args:
        frame: The base batch (a month-0 style uniform sample).
        column_x: The column left alone.
        column_y: The column whose values are reassigned.
        target_spearman: Desired dependence, typically minus the reference's.
        rng: The generator.
        reference_spearman: The reference value, for the report.
        tolerance: Accepted absolute deviation from the target.

    Returns:
        The batch with ``column_y`` permuted, and what was achieved.
    """
    out = frame.copy()
    x = out[column_x].to_numpy(dtype="float64")
    y = out[column_y].to_numpy(dtype="float64")
    before = float(spearmanr(x, y).statistic)

    # Ranked and normalised once; the noise is drawn once too, so everything
    # below is a deterministic function of rho alone.
    x_latent = np.argsort(np.argsort(x)).astype("float64")
    x_latent = (x_latent + 0.5) / len(x_latent)
    x_normal = norm.ppf(x_latent)
    noise = rng.standard_normal(len(x))

    def achieved(rho: float) -> tuple[float, np.ndarray]:
        latent = rho * x_normal + np.sqrt(max(1 - rho**2, 0.0)) * noise
        candidate = _reorder_to_latent(y, latent)
        return float(spearmanr(x, candidate).statistic), candidate

    low, high = _RHO_BOUNDS
    rho = 2 * np.sin(np.pi * target_spearman / 6)
    value, permuted = achieved(rho)
    if abs(value - target_spearman) > tolerance:
        for _ in range(_BISECTION_STEPS):
            rho = (low + high) / 2
            value, permuted = achieved(rho)
            if abs(value - target_spearman) <= tolerance:
                break
            if value < target_spearman:
                low = rho
            else:
                high = rho

    out[column_y] = permuted.astype(frame[column_y].dtype)
    return out, DependenceResult(
        column_x=column_x,
        column_y=column_y,
        reference_spearman=reference_spearman,
        before_spearman=before,
        after_spearman=value,
        target_spearman=target_spearman,
        rho_used=float(rho),
    )
