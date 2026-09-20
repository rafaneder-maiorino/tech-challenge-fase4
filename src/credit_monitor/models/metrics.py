"""The three metrics the baseline is judged on, and why these three.

They answer different questions, and a model can be good at one while being
useless at another:

AUC-ROC
    Ranking. Given a defaulter and a non-defaulter, how often does the model
    score the defaulter higher? Threshold-free and invariant to any monotone
    transform of the score, which also means **it cannot see a calibration
    problem at all**.

KS separation
    The largest gap between the cumulative score distributions of the two
    classes. The standard credit-risk statistic, and the one that says where
    to put a cutoff: the threshold at which KS is attained is the point of
    maximum separation. Also threshold-free, also blind to calibration.

Brier score
    Mean squared error of the predicted probability. The only one of the three
    that is sensitive to calibration, and therefore the only one that notices
    if a model's probabilities stop meaning what they say. Stage 3 tracks it
    per batch as a degradation signal, which is precisely a bet that
    probabilities drift before ranking does.

Lower is better for Brier; higher is better for the other two.
"""

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from sklearn.metrics import brier_score_loss, roc_auc_score, roc_curve


@dataclass(frozen=True, slots=True)
class ClassificationMetrics:
    """One evaluation of one model on one set."""

    auc_roc: float
    ks_statistic: float
    ks_threshold: float
    """The score at which the KS gap is attained — the natural cutoff."""

    brier: float
    positive_rate: float
    rows: int

    def to_dict(self) -> dict[str, Any]:
        """Render as a plain dict for MLflow metrics and the report table."""
        return asdict(self)


def ks_separation(y_true: np.ndarray, y_score: np.ndarray) -> tuple[float, float]:
    """Return the KS statistic and the score threshold where it is attained.

    Computed from the ROC curve, where the KS statistic is exactly the largest
    vertical distance between the curve and the diagonal: at any threshold,
    ``tpr`` is the share of defaulters already caught and ``fpr`` the share of
    non-defaulters wrongly caught, so ``tpr - fpr`` is the separation there.

    Args:
        y_true: Binary labels.
        y_score: Predicted scores or probabilities.

    Returns:
        ``(ks, threshold)``.
    """
    fpr, tpr, thresholds = roc_curve(y_true, y_score)
    gaps = tpr - fpr
    best = int(np.argmax(gaps))
    return float(gaps[best]), float(thresholds[best])


def evaluate(y_true: np.ndarray, y_prob: np.ndarray) -> ClassificationMetrics:
    """Compute all three metrics for one set of predicted probabilities.

    Args:
        y_true: Binary labels.
        y_prob: Predicted probability of the positive class.

    Returns:
        The metrics.
    """
    ks, threshold = ks_separation(y_true, y_prob)
    return ClassificationMetrics(
        auc_roc=float(roc_auc_score(y_true, y_prob)),
        ks_statistic=ks,
        ks_threshold=threshold,
        brier=float(brier_score_loss(y_true, y_prob)),
        positive_rate=float(np.mean(y_true)),
        rows=len(y_true),
    )
