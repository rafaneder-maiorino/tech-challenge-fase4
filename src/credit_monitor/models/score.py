"""Score a batch with the registered champion, and measure what came out.

The signature is the design. :func:`predict_proba` takes a feature frame and
nothing else, and it refuses a frame that carries the target column at all.
That is not defensiveness for its own sake: a monitor that *can* touch the
label at scoring time is a monitor that will, in some future revision, measure
its own degradation with information it would not have had. The only way to
guarantee that never happens is for the function to be unable to accept it.

The same reasoning is why the simulator writes ``features.parquet``,
``predictions.parquet`` and ``labels.parquet`` as three files. In production
the third one arrives months after the first two, and any pipeline that reads
them from one frame has quietly assumed otherwise.
"""

import logging
from dataclasses import asdict, dataclass
from typing import Any, Final, Protocol, runtime_checkable

import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
from mlflow.exceptions import MlflowException

from credit_monitor.constants import (
    CHAMPION_ALIAS,
    MLFLOW_TRACKING_URI,
    REGISTERED_MODEL_NAME,
    TARGET_COLUMN,
)
from credit_monitor.data.preprocess import MODEL_FEATURES
from credit_monitor.models.metrics import evaluate

log = logging.getLogger(__name__)

CHAMPION_URI: Final[str] = f"models:/{REGISTERED_MODEL_NAME}@{CHAMPION_ALIAS}"


@runtime_checkable
class ProbabilisticClassifier(Protocol):
    """What the scorer needs from a model: calibrated probabilities.

    Stated as a protocol rather than as the concrete estimator class because
    the requirement really is just this one method — and because writing it
    down is what makes the pyfunc mistake impossible to repeat. A wrapper that
    only offers ``predict`` does not satisfy this type.
    """

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:  # noqa: N803
        """Return per-class probabilities, positives in column 1."""
        ...


class LabelLeakageError(ValueError):
    """A frame handed to the scorer contained the target column."""


MISSING_CHAMPION_MESSAGE: Final[str] = (
    "campeão não encontrado em {uri} — rode `make train` antes.\n"
    "O registry do MLflow (mlruns/ e mlflow.db) é gitignored, então um clone "
    "novo não traz modelo nenhum. A ordem é: make prepare -> make train.\n"
    "Ou rode `make all`, que faz a cadeia inteira na ordem certa."
)


def load_champion(uri: str = CHAMPION_URI) -> ProbabilisticClassifier:
    """Load the model behind the ``champion`` alias, as a sklearn estimator.

    By alias, never by version: the alias is the indirection that lets stage 3
    promote a challenger without anything here changing.

    The **sklearn** flavour, not pyfunc, and that is not a preference. A pyfunc
    wrapper exposes only ``predict``, which for a classifier returns the
    predicted class — so every probability came back as a 0 or a 1, the mean
    prediction read 2.1% because that was the share predicted positive, and the
    AUC collapsed to 0.586 for a model that scores 0.856. Everything looked
    plausible enough to miss: a degraded-looking model in a drift simulation is
    exactly what one expects to see. Loading the sklearn flavour gives back
    ``predict_proba``, and calibrated probabilities are the entire point of the
    Brier score and the calibration gap.

    ``mlruns/`` and ``mlflow.db`` are gitignored, so a fresh clone has no
    champion and every caller of this function fails. Raw, the failure is an
    MLflow stack trace ending in "Registered Model with name=... not found",
    which tells a first-time reader nothing about what to run. The message
    below names the command instead.

    Args:
        uri: The model URI; defaults to the champion alias.

    Returns:
        The loaded estimator, with ``predict_proba``.

    Raises:
        SystemExit: No champion is registered yet.
    """
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    log.info("score.loading_champion", extra={"uri": uri})
    try:
        return mlflow.sklearn.load_model(uri)
    except MlflowException as error:
        raise SystemExit(MISSING_CHAMPION_MESSAGE.format(uri=uri)) from error


def predict_proba(model: ProbabilisticClassifier, features: pd.DataFrame) -> np.ndarray:
    """Probability of default for each row. Features only — labels rejected.

    Args:
        model: The loaded champion.
        features: A frame containing the model's feature columns.

    Returns:
        One probability per row.

    Raises:
        LabelLeakageError: ``features`` contains the target column.
        KeyError: A model feature is missing from ``features``.
    """
    if TARGET_COLUMN in features.columns:
        raise LabelLeakageError(
            f"the scoring input carries {TARGET_COLUMN!r}. Scoring happens "
            "before the outcome exists; a frame that has both is a frame "
            "assembled by code that forgot the label arrives months later."
        )
    missing = set(MODEL_FEATURES) - set(features.columns)
    if missing:
        raise KeyError(f"missing model features: {sorted(missing)}")
    matrix = features[list(MODEL_FEATURES)].astype("float64")
    return np.asarray(model.predict_proba(matrix)[:, 1], dtype="float64")


@dataclass(frozen=True, slots=True)
class BatchMetrics:
    """One batch, scored and measured."""

    month: int
    rows: int
    auc_roc: float
    ks_statistic: float
    brier: float
    observed_default_rate: float
    mean_predicted: float
    calibration_gap: float
    """Mean predicted minus observed. **The silent-degradation signal.**

    AUC and KS answer "does the model still rank correctly", and under drift by
    measurement they hold up for a long time — the ordering between customers
    survives a shift that moves everybody. This one answers "does the model
    still know the level", and it is the first to break: the model keeps
    predicting ~6% while the observed rate climbs, so the gap goes negative
    and grows. It needs the label, so it can only be computed once the outcome
    arrives — which is exactly why the batches are written as three files.
    """

    def to_dict(self) -> dict[str, Any]:
        """Render as a plain dict for the report tables."""
        return asdict(self)


def measure(month: int, y_true: np.ndarray, y_prob: np.ndarray) -> BatchMetrics:
    """Compute the six per-batch numbers.

    Args:
        month: Which batch this is.
        y_true: Observed labels, as arriving late.
        y_prob: Champion probabilities from scoring time.

    Returns:
        The metrics.
    """
    core = evaluate(y_true, y_prob)
    observed = float(np.mean(y_true))
    predicted = float(np.mean(y_prob))
    return BatchMetrics(
        month=month,
        rows=len(y_true),
        auc_roc=core.auc_roc,
        ks_statistic=core.ks_statistic,
        brier=core.brier,
        observed_default_rate=observed,
        mean_predicted=predicted,
        calibration_gap=predicted - observed,
    )
