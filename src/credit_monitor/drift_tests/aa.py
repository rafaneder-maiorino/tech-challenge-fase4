"""A/A test: how often each rule cries drift when there is none.

An A/A test feeds the monitor two samples from the **same** population and
counts the alarms. Everything it reports is a false alarm by construction, so
it is the only way to learn what a rule's threshold actually costs before
trusting it on data where the answer is unknown.

Both sides come from data the simulator never touched: the reference is the
stage-1 baseline and the draws come from the **holdout**, which was sealed at
the split precisely for this. The two are disjoint by construction, and that is
asserted rather than assumed.

The hypothesis under test is not the one this project started with. "KS raises
false alarms with large samples" is wrong as stated: under a true null, KS
p-values are uniform, so the per-feature rate sits at alpha for **any** n, and
on tie-dominated count columns it sits *below* alpha because ties make KS
conservative. The two real problems are expected to be:

1. **Significance is not magnitude.** KS flags real but irrelevant shifts —
   measured separately in :mod:`credit_monitor.drift_tests.sensitivity`.
2. **Multiple comparisons.** Eleven features at alpha 0.05 give a batch-level
   rate far above alpha with no drift at all.

PSI is expected to fail in the opposite regime: it is biased upward at small n,
so it should false-alarm on small batches. Whether the data agree is what this
module measures.
"""

import logging
from dataclasses import dataclass
from typing import Final

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

from credit_monitor.data.preprocess import MODEL_FEATURES
from credit_monitor.simulation.psi import bin_edges, psi

log = logging.getLogger(__name__)

# Sample sizes swept. 7,000 is the production batch size (44,208 / 6 = 7,368),
# so the recommendation has to be read at that point on the curve.
SAMPLE_SIZES: Final[tuple[int, ...]] = (250, 500, 1000, 2000, 5000, 7000, 20000)
REPEATS: Final[int] = 200
ALPHA: Final[float] = 0.05
PSI_WARN: Final[float] = 0.10
PSI_ALERT: Final[float] = 0.25


@dataclass(frozen=True, slots=True)
class AAResult:
    """Every draw x feature measurement, tidy."""

    frame: pd.DataFrame
    """Columns: n, repeat, feature, ks_statistic, ks_pvalue, psi."""

    reference_rows: int
    repeats: int

    def per_feature_rates(self) -> pd.DataFrame:
        """False-alarm rate per feature and n, for each rule."""
        grouped = self.frame.groupby(["n", "feature"], observed=True)
        return grouped.agg(
            ks_far=("ks_pvalue", lambda s: float((s < ALPHA).mean())),
            psi_warn_far=("psi", lambda s: float((s > PSI_WARN).mean())),
            psi_alert_far=("psi", lambda s: float((s > PSI_ALERT).mean())),
            psi_mean=("psi", "mean"),
        ).reset_index()

    def family_wise_rates(self) -> pd.DataFrame:
        """Share of draws where **any** feature fires, per rule.

        This is the number an operator actually experiences: a dashboard turns
        red if any tile turns red, so the per-feature rate is not the alarm
        rate of the dashboard.
        """
        rows = []
        for n, draw in self.frame.groupby("n", observed=True):
            by_repeat = draw.groupby("repeat", observed=True)
            rows.append(
                {
                    "n": n,
                    "ks_any": float(by_repeat["ks_pvalue"].min().lt(ALPHA).mean()),
                    "ks_bonferroni": float(
                        by_repeat["ks_pvalue"]
                        .min()
                        .lt(ALPHA / len(MODEL_FEATURES))
                        .mean()
                    ),
                    "ks_benjamini_hochberg": float(
                        by_repeat["ks_pvalue"]
                        .apply(lambda s: _any_bh_rejection(s.to_numpy()))
                        .mean()
                    ),
                    "psi_warn_any": float(by_repeat["psi"].max().gt(PSI_WARN).mean()),
                    "psi_alert_any": float(by_repeat["psi"].max().gt(PSI_ALERT).mean()),
                }
            )
        return pd.DataFrame(rows)


def _any_bh_rejection(pvalues: np.ndarray, alpha: float = ALPHA) -> bool:
    """Whether Benjamini-Hochberg rejects at least one hypothesis.

    BH controls the false **discovery** rate rather than the family-wise error
    rate, so it is expected to sit between the uncorrected rate and
    Bonferroni's: more alarms than Bonferroni, fewer than none at all.
    """
    ordered = np.sort(pvalues)
    ranks = np.arange(1, len(ordered) + 1)
    return bool(np.any(ordered <= alpha * ranks / len(ordered)))


def theoretical_psi_bias(n: int, m: int, bins: int) -> float:
    """Expected PSI under the null, from the chi-square approximation.

    ``2 n PSI`` is asymptotically chi-square with ``bins - 1`` degrees of
    freedom when the two samples come from the same distribution, which gives
    ``E[PSI] ~ (bins - 1) * (1/n + 1/m)``. The point of plotting it next to the
    measured mean is that the small-sample bias of PSI is **predictable**, not
    mysterious: it is a property of the bin count and the sample size, and it
    says a threshold tuned at one batch size is wrong at another.
    """
    return (bins - 1) * (1.0 / n + 1.0 / m)


def effective_bins(reference: pd.DataFrame) -> dict[str, int]:
    """Bins each feature actually gets under the adaptive rule."""
    return {
        column: len(bin_edges(reference[column], 10)) - 1 for column in MODEL_FEATURES
    }


def run_aa(
    reference: pd.DataFrame,
    holdout: pd.DataFrame,
    seed: int,
    sizes: tuple[int, ...] = SAMPLE_SIZES,
    repeats: int = REPEATS,
) -> AAResult:
    """Draw from the holdout, compare against the reference, record everything.

    Args:
        reference: The stage-1 baseline, used whole on every comparison.
        holdout: The sealed set; every draw comes from here.
        seed: Fixed, so two runs give identical results.
        sizes: Sample sizes to sweep.
        repeats: Draws per size.

    Returns:
        Every measurement, tidy.
    """
    rng = np.random.default_rng(seed)
    reference_arrays = {
        column: reference[column].to_numpy(dtype="float64") for column in MODEL_FEATURES
    }
    records: list[dict[str, object]] = []

    for size in sizes:
        for repeat in range(repeats):
            draw = holdout.iloc[rng.choice(len(holdout), size=size, replace=False)]
            for column in MODEL_FEATURES:
                current = draw[column]
                result = ks_2samp(
                    reference_arrays[column], current.to_numpy(dtype="float64")
                )
                records.append(
                    {
                        "n": size,
                        "repeat": repeat,
                        "feature": column,
                        "ks_statistic": float(result.statistic),
                        "ks_pvalue": float(result.pvalue),
                        "psi": psi(reference[column], current, 10),
                    }
                )
        log.info("aa.size_done", extra={"n": size, "repeats": repeats})

    return AAResult(
        frame=pd.DataFrame(records),
        reference_rows=len(reference),
        repeats=repeats,
    )
