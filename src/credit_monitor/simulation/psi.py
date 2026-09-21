"""Population Stability Index, on bins cut from the reference.

A quick, hand-rolled PSI, used for exactly one job: calibrating the simulator
so the dashboard shows a ramp from green to red instead of a jump. The real
drift detection of stage 2 is Evidently's, and this is not a replacement for
it — it is the ruler used while building the thing being measured.

The bins are cut on the **reference** and then held fixed, which is the whole
point of the statistic: a batch is compared against the baseline, not against
its own quantiles. Cutting bins per batch would make every batch look stable.
"""

from typing import Final

import numpy as np
import pandas as pd

# Floor on a bin's share, so an empty production bin contributes a large but
# finite amount instead of infinity. 1e-6 on ten bins is far below any
# threshold anyone acts on.
MIN_SHARE: Final[float] = 1e-6

# Conventional reading of the statistic, and the bands the calibration targets.
WARN_THRESHOLD: Final[float] = 0.10
ALERT_THRESHOLD: Final[float] = 0.25

# Above this many distinct values a tie-dominated column is still binned by
# quantiles: one bin per value would be a histogram, not a summary.
MAX_DISCRETE_BINS: Final[int] = 30


def bin_edges(reference: pd.Series, bins: int = 10) -> np.ndarray:
    """Bin edges from the reference: quantiles, or one bin per value if tied.

    Quantile binning silently fails on this dataset, and the failure is total
    rather than approximate. ``NumberOfTimes90DaysLate`` is zero in 94.56% of
    reference rows, so every decile from the 10th to the 90th lands on 0 and
    ``np.unique`` collapses the eleven requested edges to two. Bracketed with
    -inf and +inf that is a **single bin**, and a single bin makes PSI
    identically zero — the statistic cannot report drift in that column no
    matter what happens to it. Three of the eleven features were in that state
    (the 90+ and 60-89 day counters and ``income_missing``, the last being
    binary and therefore hopeless for quantiles by construction).

    So the rule is adaptive, and the trigger is the degeneracy itself: if the
    requested quantiles do not yield ``bins + 1`` distinct edges, the column is
    tie-dominated and gets **one bin per observed value** instead, with edges
    at the midpoints. For a count column that is the honest binning anyway —
    "how many rows moved from zero to one" is the question, and no quantile
    grid asks it.

    Args:
        reference: The baseline column.
        bins: Requested number of quantile bins.

    Returns:
        Monotone edges, with -inf and +inf at the ends. The ends are
        *appended*, never written over an interior edge — overwriting is what
        destroyed the 0/non-zero boundary in the first place.
    """
    values = reference.to_numpy(dtype="float64")
    quantile_edges = np.unique(np.quantile(values, np.linspace(0, 1, bins + 1)))

    if len(quantile_edges) < bins + 1:
        distinct = np.unique(values)
        if len(distinct) <= MAX_DISCRETE_BINS:
            midpoints = (distinct[:-1] + distinct[1:]) / 2
            return np.concatenate(([-np.inf], midpoints, [np.inf]))

    interior = quantile_edges[1:-1] if len(quantile_edges) > 2 else quantile_edges
    return np.concatenate(([-np.inf], interior, [np.inf]))


def _shares(values: pd.Series, edges: np.ndarray) -> np.ndarray:
    counts = np.histogram(values.to_numpy(dtype="float64"), bins=edges)[0]
    shares = counts / max(counts.sum(), 1)
    return np.clip(shares, MIN_SHARE, None)


def psi(reference: pd.Series, production: pd.Series, bins: int = 10) -> float:
    """Population Stability Index of a production column against the reference.

    Args:
        reference: The baseline column.
        production: The batch column.
        bins: Quantile bins cut on the reference.

    Returns:
        The PSI. Conventionally: below 0.10 stable, 0.10-0.25 moderate shift,
        above 0.25 significant shift.
    """
    edges = bin_edges(reference, bins)
    reference_shares = _shares(reference, edges)
    production_shares = _shares(production, edges)
    delta = production_shares - reference_shares
    return float(np.sum(delta * np.log(production_shares / reference_shares)))


def psi_table(
    reference: pd.DataFrame,
    production: pd.DataFrame,
    columns: tuple[str, ...],
    bins: int = 10,
) -> dict[str, float]:
    """PSI for several columns at once."""
    return {
        column: psi(reference[column], production[column], bins) for column in columns
    }
