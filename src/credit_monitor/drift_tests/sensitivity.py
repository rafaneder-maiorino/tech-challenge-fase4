"""Significance against magnitude: where KS screams and nothing is happening.

The A/A test shows KS is correctly calibrated under the null. That is not the
same as useful. This module measures the other failure mode: a **true** alarm
that is operationally worthless, because the shift it detects is real and too
small to matter.

The inflation mechanism is the right probe. It is the one mechanism the
stage-2 ablation showed to be nearly inert — it moves `MonthlyIncome` and
`DebtRatio`, which together carry 4.71% of the champion's gain, and the model's
mean prediction barely moves. So any alarm it raises is, by construction, an
alarm about something that does not change a decision.
"""

import logging
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

from credit_monitor.constants import TARGET_COLUMN
from credit_monitor.contracts.raw import POSITIVE_LABEL
from credit_monitor.data.preprocess import MODEL_FEATURES
from credit_monitor.models.metrics import evaluate
from credit_monitor.models.score import ProbabilisticClassifier, predict_proba
from credit_monitor.simulation.mechanisms import apply_inflation
from credit_monitor.simulation.psi import psi

log = logging.getLogger(__name__)

INFLATION_LEVELS: Final[tuple[float, ...]] = (0.01, 0.02, 0.05, 0.10)
BATCH_SIZES: Final[tuple[int, ...]] = (1000, 7000, 44000)

# The two columns the inflation mechanism touches.
PROBED_COLUMNS: Final[tuple[str, ...]] = ("DebtRatio", "MonthlyIncome")


@dataclass(frozen=True, slots=True)
class SensitivityRow:
    """One (pi, n) cell: what the tests say next to what the model does."""

    pi_total: float
    n: int
    column: str
    ks_pvalue: float
    ks_statistic: float
    psi: float
    mean_prediction_shift: float
    """Batch mean predicted probability minus the un-inflated batch's."""

    auc_delta: float
    """AUC on the inflated batch minus AUC on the same rows un-inflated."""

    def to_dict(self) -> dict[str, Any]:
        """Render for the report table."""
        return {
            "pi_total": self.pi_total,
            "n": self.n,
            "column": self.column,
            "ks_pvalue": self.ks_pvalue,
            "ks_statistic": self.ks_statistic,
            "psi": self.psi,
            "mean_prediction_shift": self.mean_prediction_shift,
            "auc_delta": self.auc_delta,
        }


def run_sweep(
    reference: pd.DataFrame,
    holdout: pd.DataFrame,
    model: ProbabilisticClassifier,
    seed: int,
    levels: tuple[float, ...] = INFLATION_LEVELS,
    sizes: tuple[int, ...] = BATCH_SIZES,
) -> pd.DataFrame:
    """Inflate at several intensities and sizes; record tests and behaviour.

    Args:
        reference: The baseline, for PSI and KS.
        holdout: Source of the batches.
        model: The champion.
        seed: Fixed for reproducibility.
        levels: Inflation intensities at full strength.
        sizes: Batch sizes.

    Returns:
        One row per (pi, n, column).
    """
    rng = np.random.default_rng(seed)
    rows: list[SensitivityRow] = []

    for size in sizes:
        draw = holdout.iloc[
            rng.choice(len(holdout), size=min(size, len(holdout)), replace=False)
        ].reset_index(drop=True)
        labels = (draw[TARGET_COLUMN].astype(str) == POSITIVE_LABEL).to_numpy(int)
        base_probabilities = predict_proba(model, draw[list(MODEL_FEATURES)])
        base_auc = evaluate(labels, base_probabilities).auc_roc
        base_mean = float(base_probabilities.mean())

        for level in levels:
            inflated, _ = apply_inflation(draw, level, 1.0)
            probabilities = predict_proba(model, inflated[list(MODEL_FEATURES)])
            metrics = evaluate(labels, probabilities)
            for column in PROBED_COLUMNS:
                result = ks_2samp(
                    reference[column].to_numpy(dtype="float64"),
                    inflated[column].to_numpy(dtype="float64"),
                )
                rows.append(
                    SensitivityRow(
                        pi_total=level,
                        n=len(draw),
                        column=column,
                        ks_pvalue=float(result.pvalue),
                        ks_statistic=float(result.statistic),
                        psi=psi(reference[column], inflated[column], 10),
                        mean_prediction_shift=float(probabilities.mean()) - base_mean,
                        auc_delta=metrics.auc_roc - base_auc,
                    )
                )
        log.info("sensitivity.size_done", extra={"n": len(draw)})

    return pd.DataFrame([row.to_dict() for row in rows])
