"""Evidently drift reports, configured explicitly rather than by default.

Version, API choice and the reasoning behind both are in ``docs/evidently.md``.
The short version, because it governs everything in this module:

* **evidently 0.7.23**, pinned exactly. The API changed incompatibly across the
  0.4/0.6/0.7 series and the HTML files are committed artefacts.
* The **current** API (``evidently.Report`` + ``evidently.metrics``), not the
  one the library itself moved into a module named ``legacy``.
* **No metric is left to auto-selection.** With a ~103k-row reference,
  Evidently's automatic choice may pick another test and silently omit the one
  the stage asks for, so every column names its method.

The one place ``evidently.legacy`` is unavoidable is the stattest registry,
which is where a custom drift method has to be registered; it is marked at the
import.
"""

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
from evidently import BinaryClassification, DataDefinition, Dataset, Report
from evidently.core.report import Snapshot
from evidently.legacy.calculations.stattests.registry import (  # legacy: see docstring
    StatTest,
    register_stattest,
)
from evidently.legacy.core import ColumnType
from evidently.metrics import ValueDrift
from scipy.stats import ks_2samp

from credit_monitor.data.preprocess import MODEL_FEATURES
from credit_monitor.simulation.psi import (
    ALERT_THRESHOLD,
    WARN_THRESHOLD,
)
from credit_monitor.simulation.psi import (
    psi as reference_quantile_psi,
)

log = logging.getLogger(__name__)

# Bins for the custom PSI. Ten quantile bins cut on the reference, the same
# grid the stage-2 calibration was tuned against.
PSI_BINS: Final[int] = 10

# Name the custom stattest is registered under and referenced by. `method=`
# accepts only a string in the current API — passing a callable is rejected
# with a validation error — so registration is the only route.
REFERENCE_PSI: Final[str] = "reference_psi"

# Columns that get a KS test in addition to PSI. The other seven features are
# tie-dominated counts (`NumberOfTimes90DaysLate` is zero in 94.56% of
# reference rows), where KS compares two step functions with one step and the
# statistic says nothing about distribution shape.
CONTINUOUS_FEATURES: Final[tuple[str, ...]] = (
    "RevolvingUtilizationOfUnsecuredLines",
    "DebtRatio",
    "MonthlyIncome",
    "age",
)

PREDICTION_COLUMN: Final[str] = "probability"
LABEL_COLUMN: Final[str] = "label"

VERDICT_STABLE: Final[str] = "estável"
VERDICT_MODERATE: Final[str] = "moderado"
VERDICT_SIGNIFICANT: Final[str] = "significativo"


def _reference_psi_impl(
    reference_data: pd.Series,
    current_data: pd.Series,
    feature_type: ColumnType,
    threshold: float,
) -> tuple[float, bool]:
    """Quantile-binned PSI, delegating to the stage-2 implementation.

    Delegation rather than reimplementation is the point: the number rendered
    in Evidently's HTML and the number in our own summary table are the same
    number by construction, not two estimates that happen to agree.
    """
    score = reference_quantile_psi(reference_data, current_data, PSI_BINS)
    return score, score >= threshold


reference_psi_stattest = StatTest(
    name=REFERENCE_PSI,
    display_name="PSI (bins de quantil do reference)",
    allowed_feature_types=[ColumnType.Categorical, ColumnType.Numerical],
    default_threshold=WARN_THRESHOLD,
)
register_stattest(reference_psi_stattest, _reference_psi_impl)


def verdict(psi_value: float) -> str:
    """Traffic light for a PSI value, on the 0.10 / 0.25 bands."""
    if psi_value < WARN_THRESHOLD:
        return VERDICT_STABLE
    if psi_value < ALERT_THRESHOLD:
        return VERDICT_MODERATE
    return VERDICT_SIGNIFICANT


# --------------------------------------------------------------------------
# Datasets
# --------------------------------------------------------------------------


def feature_dataset(frame: pd.DataFrame, with_prediction: bool) -> Dataset:
    """Wrap a feature frame (optionally with the score) for Evidently."""
    numerical = [*MODEL_FEATURES]
    if with_prediction:
        numerical.append(PREDICTION_COLUMN)
    columns = [c for c in numerical if c in frame.columns]
    definition = DataDefinition(numerical_columns=columns)
    return Dataset.from_pandas(frame[columns], data_definition=definition)


def scored_dataset(frame: pd.DataFrame) -> Dataset:
    """Wrap features + score + label, for the label-time report."""
    definition = DataDefinition(
        numerical_columns=[*MODEL_FEATURES, PREDICTION_COLUMN],
        categorical_columns=[LABEL_COLUMN],
        classification=[
            BinaryClassification(
                target=LABEL_COLUMN,
                prediction_probas=PREDICTION_COLUMN,
                pos_label="1",
            )
        ],
    )
    return Dataset.from_pandas(frame, data_definition=definition)


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


def scoring_metrics() -> list[ValueDrift]:
    """Every metric of the scoring-time report, named explicitly.

    PSI on all eleven features, KS on the four continuous ones, and PSI on the
    predicted score. No preset, no auto-selection.
    """
    metrics = [
        ValueDrift(column=column, method=REFERENCE_PSI, threshold=WARN_THRESHOLD)
        for column in MODEL_FEATURES
    ]
    metrics += [
        ValueDrift(column=column, method="ks", threshold=0.05)
        for column in CONTINUOUS_FEATURES
    ]
    # Prediction drift: the score distribution is the one "feature" that
    # aggregates all the others, and it moves at scoring time, with no label.
    metrics.append(
        ValueDrift(
            column=PREDICTION_COLUMN, method=REFERENCE_PSI, threshold=WARN_THRESHOLD
        )
    )
    return metrics


def scoring_report(reference: pd.DataFrame, current: pd.DataFrame) -> Snapshot:
    """Feature drift plus prediction drift, with no labels anywhere.

    Args:
        reference: Reference features with the champion's score attached.
        current: The batch, same columns.

    Returns:
        The rendered snapshot.
    """
    return Report(scoring_metrics(), include_tests=True).run(
        current_data=feature_dataset(current, with_prediction=True),
        reference_data=feature_dataset(reference, with_prediction=True),
    )


def label_report(reference: pd.DataFrame, current: pd.DataFrame) -> Snapshot:
    """Target drift and classification quality, once the labels arrive.

    The only report that can see the stress mechanism: it changes ``P(y|X)``
    without moving a single feature, so everything in the scoring-time report
    stays green by construction.

    Args:
        reference: Reference rows with score and label.
        current: The batch, same columns.

    Returns:
        The rendered snapshot.
    """
    from evidently.presets import ClassificationQuality

    metrics: list[Any] = [
        ValueDrift(column=LABEL_COLUMN, method=REFERENCE_PSI, threshold=WARN_THRESHOLD),
        ValueDrift(
            column=PREDICTION_COLUMN, method=REFERENCE_PSI, threshold=WARN_THRESHOLD
        ),
        ClassificationQuality(probas_threshold=0.5),
    ]
    return Report(metrics, include_tests=True).run(
        current_data=scored_dataset(current),
        reference_data=scored_dataset(reference),
    )


def save_html(snapshot: Snapshot, path: Path) -> Path:
    """Write a snapshot to a self-contained HTML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot.save_html(str(path))
    return path


def metric_values(snapshot: Snapshot) -> dict[str, float]:
    """Flatten a snapshot into ``{metric_name: value}``."""
    return {
        metric["metric_name"]: metric["value"]
        for metric in snapshot.dict()["metrics"]
        if isinstance(metric.get("value"), int | float)
    }


# --------------------------------------------------------------------------
# The per-column table behind the summary
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ColumnDrift:
    """One feature, one month, every number the summary needs."""

    month: int
    column: str
    psi: float
    """Our PSI: quantile bins cut on the reference (the configured method)."""

    psi_evidently_native: float
    """Evidently's built-in PSI, kept for the cross-check of findings §10."""

    ks_statistic: float | None
    ks_pvalue: float | None
    verdict: str
    gain_share: float
    """Share of the champion's XGBoost gain, from stage 2."""

    @property
    def impact(self) -> float:
        """PSI x importance: drift weighted by how much the model leans on it.

        A PSI of 0.30 on a feature carrying 28% of the gain and the same PSI on
        one carrying 2% are not the same event, and a table sorted by PSI alone
        presents them as if they were.
        """
        return self.psi * self.gain_share

    def to_dict(self) -> dict[str, Any]:
        """Render for the report table."""
        return {**asdict(self), "impact": self.impact}


def native_psi(reference: pd.Series, current: pd.Series) -> float:
    """Evidently's own PSI, called directly, for the cross-check."""
    from evidently.legacy.calculations.stattests import psi_stat_test

    score, _ = psi_stat_test.func(
        reference.astype(float), current.astype(float), ColumnType.Numerical, 0.1
    )
    return float(score)


def column_drift(
    month: int,
    column: str,
    reference: pd.Series,
    current: pd.Series,
    gain_share: float,
) -> ColumnDrift:
    """Compute every drift number for one column of one batch.

    The KS statistic is computed here with scipy rather than read from
    Evidently, because Evidently's ``ks`` returns the **p-value** as its drift
    score and the statistic is what describes the size of the difference.
    """
    psi_value = reference_quantile_psi(reference, current, PSI_BINS)
    statistic: float | None = None
    pvalue: float | None = None
    if column in CONTINUOUS_FEATURES:
        result = ks_2samp(
            reference.to_numpy(dtype="float64"), current.to_numpy(dtype="float64")
        )
        statistic, pvalue = float(result.statistic), float(result.pvalue)
    return ColumnDrift(
        month=month,
        column=column,
        psi=psi_value,
        psi_evidently_native=native_psi(reference, current),
        ks_statistic=statistic,
        ks_pvalue=pvalue,
        verdict=verdict(psi_value),
        gain_share=gain_share,
    )


def drift_table(
    month: int,
    reference: pd.DataFrame,
    current: pd.DataFrame,
    gain_shares: dict[str, float],
) -> list[ColumnDrift]:
    """Every feature of one batch, as rows."""
    return [
        column_drift(
            month,
            column,
            reference[column],
            current[column],
            gain_shares.get(column, 0.0),
        )
        for column in MODEL_FEATURES
    ]


def champion_gain_shares(model: object) -> dict[str, float]:
    """Share of total XGBoost gain per feature, from the registered champion.

    Read from the model rather than transcribed, so the summary cannot drift
    away from the model it describes. Falls back to equal shares if the inner
    booster is not reachable — the ranking then degrades to plain PSI order
    rather than failing the whole report.
    """
    try:
        inner = model.calibrated_classifiers_[0].estimator.estimator  # type: ignore[attr-defined]
        gain = inner.get_booster().get_score(importance_type="gain")
    except (AttributeError, IndexError, KeyError) as error:
        log.warning("drift.gain_unavailable", extra={"error": str(error)})
        return dict.fromkeys(MODEL_FEATURES, 1.0 / len(MODEL_FEATURES))
    total = sum(gain.values()) or 1.0
    return {column: gain.get(column, 0.0) / total for column in MODEL_FEATURES}


def attach_scores(
    features: pd.DataFrame, probabilities: np.ndarray, labels: pd.Series | None = None
) -> pd.DataFrame:
    """Assemble the frame Evidently sees: features, score and optionally label."""
    frame = features[list(MODEL_FEATURES)].copy()
    frame[PREDICTION_COLUMN] = probabilities
    if labels is not None:
        frame[LABEL_COLUMN] = labels.astype(str).to_numpy()
    return frame
