"""Train the two baseline candidates, compare them, register a champion.

Logistic regression against XGBoost on **identical features** — the comparison
is about model family, so anything else that differed would confound it.

Class imbalance (6.68% positives, 13.96:1, inspection §2) is handled
explicitly, in four places, and they are not interchangeable:

1. ``class_weight="balanced"`` / ``scale_pos_weight = neg/pos`` during fitting,
   so the minority class is not simply averaged away by the loss.
2. Stratification on every split, so the base rate is the same everywhere and
   a metric difference is never the splitter's doing.
3. Threshold-free metrics — AUC-ROC and KS — because accuracy at a 0.5 cutoff
   on a 93/7 problem is a statement about the majority class.
4. **A calibration step, because of what step 1 costs.** Reweighting is a
   deliberate lie about the prior: it tells the model positives are ~14 times
   more common than they are, and the probabilities come out inflated by
   roughly that factor. It barely moves ranking — AUC and KS are invariant to
   any monotone transform — but it wrecks the Brier score, which stage 3
   tracks per batch as a degradation signal. Measured on this data, the
   weighted XGBoost scores Brier 0.1356 while *always predicting the base
   rate* scores 0.0623: on the one metric that reads probabilities, the
   weighted model is worse than a constant. Isotonic calibration brings it to
   0.0496 at a cost of 0.0005 AUC.

So the reference set is partitioned three ways rather than two: **train** fits
the weighted model, **calibration** fits the isotonic map on top of it, and
**validation** is what the reported numbers come from. Fitting the calibrator
on the same rows it is scored on would report the calibrator's memory instead
of its behaviour.

The day-8 holdout is not touched anywhere in this module. Selecting on it
would make it a set chosen *because* some model looked good on it, and the A/A
false-alarm rate it is meant to measure would come out too low.

MLflow tracking is local and SQLite-backed; see
:mod:`credit_monitor.constants` for the URI and the UI port.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import mlflow
import mlflow.sklearn
import pandas as pd
from sklearn.base import BaseEstimator
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from credit_monitor.constants import (
    CHAMPION_ALIAS,
    MLFLOW_ARTIFACT_ROOT,
    MLFLOW_EXPERIMENT,
    MLFLOW_TRACKING_URI,
    REGISTERED_MODEL_NAME,
)
from credit_monitor.data.preprocess import MODEL_FEATURES
from credit_monitor.models.metrics import ClassificationMetrics, evaluate

log = logging.getLogger(__name__)

# The metric the champion is chosen on. AUC-ROC because the operational
# question is ranking — who gets reviewed first — and because it is the metric
# least sensitive to the calibration step that follows fitting. Brier is
# reported and tracked but is not the selector: it is a property of the
# calibrator as much as of the model.
SELECTION_METRIC: Final[str] = "auc_roc"

# Reference is split 60/20/20 into train / calibration / validation.
CALIBRATION_AND_VALIDATION_FRACTION: Final[float] = 0.40

# MLflow rejects a dataset digest longer than this.
MLFLOW_DIGEST_MAX_CHARS: Final[int] = 32

# MLflow 3 serialises sklearn models with skops instead of pickle, which
# refuses to reconstruct any class not on an allow-list — the point being that
# loading a model file should not be able to execute arbitrary code. These
# three are not on the default list and have to be named: the calibration
# wrapper, and XGBoost's estimator and booster. Every one is a class from
# sklearn or xgboost itself, which is why trusting them here is a statement
# about our own dependencies rather than a way around the check.
SKOPS_TRUSTED_TYPES: Final[list[str]] = [
    "sklearn.calibration._CalibratedClassifier",
    "xgboost.sklearn.XGBClassifier",
    "xgboost.core.Booster",
]


@dataclass(frozen=True, slots=True)
class ModelResult:
    """One candidate, evaluated both before and after calibration."""

    name: str
    params: dict[str, Any]
    calibrated: ClassificationMetrics
    uncalibrated: ClassificationMetrics
    run_id: str = ""
    estimator: BaseEstimator | None = field(default=None, repr=False)

    def to_row(self) -> dict[str, Any]:
        """Flatten into one row of the comparison table."""
        return {
            "model": self.name,
            "auc_roc": self.calibrated.auc_roc,
            "ks": self.calibrated.ks_statistic,
            "brier": self.calibrated.brier,
            "brier_uncalibrated": self.uncalibrated.brier,
            "run_id": self.run_id,
        }


@dataclass(frozen=True, slots=True)
class TrainingOutcome:
    """Everything the training run produced."""

    results: list[ModelResult]
    champion: str
    champion_run_id: str
    registered_version: str
    rows_train: int
    rows_calibration: int
    rows_validation: int
    scale_pos_weight: float

    def comparison_table(self) -> pd.DataFrame:
        """The metric comparison, sorted by the selection metric."""
        frame = pd.DataFrame([result.to_row() for result in self.results])
        return frame.sort_values(SELECTION_METRIC, ascending=False).reset_index(
            drop=True
        )

    def to_dict(self) -> dict[str, Any]:
        """Render as a plain dict for the run summary on disk."""
        return {
            "champion": self.champion,
            "champion_run_id": self.champion_run_id,
            "registered_version": self.registered_version,
            "selection_metric": SELECTION_METRIC,
            "rows": {
                "train": self.rows_train,
                "calibration": self.rows_calibration,
                "validation": self.rows_validation,
            },
            "scale_pos_weight": self.scale_pos_weight,
            "models": [
                {
                    "name": result.name,
                    "params": result.params,
                    "calibrated": result.calibrated.to_dict(),
                    "uncalibrated": result.uncalibrated.to_dict(),
                    "run_id": result.run_id,
                }
                for result in self.results
            ],
        }


def build_candidates(
    scale_pos_weight: float, seed: int
) -> dict[str, tuple[BaseEstimator, dict[str, Any]]]:
    """Build the two candidates and the params to log for each.

    Both see the same eleven features. The logistic regression is wrapped in a
    scaler because it is the only one of the two that cares: ``MonthlyIncome``
    runs to six figures while ``income_missing`` is 0/1, and an unscaled
    gradient would spend its time on the units. Trees are invariant to
    monotone rescaling, so XGBoost gets the columns as they are.

    Args:
        scale_pos_weight: Negative-to-positive ratio from the training split.
        seed: Fixed for reproducibility.

    Returns:
        Mapping of model name to ``(estimator, params to log)``.
    """
    logistic_params: dict[str, Any] = {
        "max_iter": 1000,
        "class_weight": "balanced",
        "C": 1.0,
        "solver": "lbfgs",
        "random_state": seed,
    }
    xgboost_params: dict[str, Any] = {
        # Shallow and many, with subsampling: the minority class is 6.68% of
        # rows, so a deep tree finds a leaf per defaulter and calls it a rule.
        "n_estimators": 400,
        "max_depth": 4,
        "learning_rate": 0.05,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_lambda": 1.0,
        "scale_pos_weight": scale_pos_weight,
        "eval_metric": "logloss",
        "random_state": seed,
        "n_jobs": 4,
    }
    return {
        "logistic_regression": (
            Pipeline(
                [
                    ("scaler", StandardScaler()),
                    ("model", LogisticRegression(**logistic_params)),
                ]
            ),
            logistic_params,
        ),
        "xgboost": (XGBClassifier(**xgboost_params), xgboost_params),
    }


def configure_mlflow() -> None:
    """Point MLflow at the local SQLite store and make sure it exists."""
    MLFLOW_ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    client = mlflow.tracking.MlflowClient()
    if client.get_experiment_by_name(MLFLOW_EXPERIMENT) is None:
        client.create_experiment(
            MLFLOW_EXPERIMENT, artifact_location=MLFLOW_ARTIFACT_ROOT.as_uri()
        )
    mlflow.set_experiment(MLFLOW_EXPERIMENT)


def _log_dataset(
    features: pd.DataFrame,
    target: pd.Series,
    source_path: Path,
    digest: str,
) -> None:
    """Record which data a run was fitted on, by content hash.

    The digest is the SHA-256 of the reference parquet, the same kind of
    checksum ``credit_monitor.data.download`` pins the raw artefact with. A
    run that records only a path records nothing: the path will still resolve
    next month, pointing at a different file.

    MLflow caps ``digest`` at 36 characters, so the field carries the first 32
    hex digits — 128 bits, which is not a collision risk for a handful of
    datasets — and the full 64-character hash goes on a tag beside it, so the
    value can still be compared against the checksum in ``constants.py``
    without recomputing anything.
    """
    # resolve() before as_uri(): a file URI has to be absolute, and the path
    # arrives from a CLI flag that may well be relative.
    dataset = mlflow.data.from_pandas(
        pd.concat([features, target.rename("target")], axis=1),
        source=source_path.resolve().as_uri(),
        name=source_path.stem,
        digest=digest[:MLFLOW_DIGEST_MAX_CHARS],
        targets="target",
    )
    mlflow.log_input(dataset, context="training")
    mlflow.set_tag("dataset_sha256", digest)


def train(
    features: pd.DataFrame,
    target: pd.Series,
    seed: int,
    preprocessing_params: dict[str, Any],
    source_path: Path,
    dataset_digest: str,
) -> TrainingOutcome:
    """Fit, calibrate, evaluate and register. One MLflow run per candidate.

    Args:
        features: The reference feature matrix.
        target: The reference target.
        seed: Fixed for reproducibility.
        preprocessing_params: Logged with every run, so a model is always
            traceable to the cleaning that produced its inputs.
        source_path: The reference parquet, for the dataset record.
        dataset_digest: SHA-256 of that file.

    Returns:
        The outcome, including the comparison table and the champion.
    """
    configure_mlflow()

    train_x, rest_x, train_y, rest_y = train_test_split(
        features,
        target,
        test_size=CALIBRATION_AND_VALIDATION_FRACTION,
        random_state=seed,
        stratify=target,
    )
    calib_x, valid_x, calib_y, valid_y = train_test_split(
        rest_x, rest_y, test_size=0.5, random_state=seed, stratify=rest_y
    )
    scale_pos_weight = float((train_y == 0).sum() / (train_y == 1).sum())
    log.info(
        "train.split",
        extra={
            "rows_train": len(train_x),
            "rows_calibration": len(calib_x),
            "rows_validation": len(valid_x),
            "scale_pos_weight": scale_pos_weight,
        },
    )

    results: list[ModelResult] = []
    for name, (estimator, params) in build_candidates(scale_pos_weight, seed).items():
        with mlflow.start_run(run_name=name) as run:
            estimator.fit(train_x, train_y)
            uncalibrated = evaluate(
                valid_y.to_numpy(), estimator.predict_proba(valid_x)[:, 1]
            )

            # Isotonic rather than sigmoid: the distortion introduced by
            # reweighting is a near-constant odds shift, which a sigmoid can
            # undo, but isotonic also fixes the shape and there are 20k rows
            # to fit it on — well past where isotonic starts overfitting.
            calibrated_estimator = CalibratedClassifierCV(
                FrozenEstimator(estimator), method="isotonic"
            )
            calibrated_estimator.fit(calib_x, calib_y)
            calibrated = evaluate(
                valid_y.to_numpy(), calibrated_estimator.predict_proba(valid_x)[:, 1]
            )

            mlflow.log_params({f"model__{k}": v for k, v in params.items()})
            mlflow.log_params(
                {f"prep__{k}": v for k, v in preprocessing_params.items()}
            )
            mlflow.log_params(
                {
                    "model_family": name,
                    "seed": seed,
                    "calibration": "isotonic",
                    "imbalance_handling": "class_weight/scale_pos_weight + isotonic",
                    "scale_pos_weight": scale_pos_weight,
                    "rows_train": len(train_x),
                    "rows_calibration": len(calib_x),
                    "rows_validation": len(valid_x),
                    "features": ",".join(MODEL_FEATURES),
                }
            )
            mlflow.log_metrics(
                {
                    "auc_roc": calibrated.auc_roc,
                    "ks_statistic": calibrated.ks_statistic,
                    "ks_threshold": calibrated.ks_threshold,
                    "brier": calibrated.brier,
                    "auc_roc_uncalibrated": uncalibrated.auc_roc,
                    "ks_statistic_uncalibrated": uncalibrated.ks_statistic,
                    "brier_uncalibrated": uncalibrated.brier,
                    "validation_positive_rate": calibrated.positive_rate,
                }
            )
            _log_dataset(features, target, source_path, dataset_digest)
            mlflow.sklearn.log_model(
                calibrated_estimator,
                name="model",
                input_example=valid_x.head(5),
                skops_trusted_types=SKOPS_TRUSTED_TYPES,
            )
            results.append(
                ModelResult(
                    name=name,
                    params=params,
                    calibrated=calibrated,
                    uncalibrated=uncalibrated,
                    run_id=run.info.run_id,
                    estimator=calibrated_estimator,
                )
            )
            log.info(
                "train.candidate_finished",
                extra={
                    "model": name,
                    "run_id": run.info.run_id,
                    **calibrated.to_dict(),
                },
            )

    champion = max(results, key=lambda r: getattr(r.calibrated, SELECTION_METRIC))
    version = register_champion(champion)
    return TrainingOutcome(
        results=results,
        champion=champion.name,
        champion_run_id=champion.run_id,
        registered_version=version,
        rows_train=len(train_x),
        rows_calibration=len(calib_x),
        rows_validation=len(valid_x),
        scale_pos_weight=scale_pos_weight,
    )


def register_champion(champion: ModelResult) -> str:
    """Register the winning model and move the ``champion`` alias onto it.

    An alias rather than a stage: the serving side resolves
    ``models:/<name>@champion`` and never learns a version number, so
    promoting a challenger later is one API call and no redeploy.

    Args:
        champion: The winning candidate, already logged to its run.

    Returns:
        The registered version number, as a string.
    """
    client = mlflow.tracking.MlflowClient()
    result = mlflow.register_model(
        model_uri=f"runs:/{champion.run_id}/model",
        name=REGISTERED_MODEL_NAME,
    )
    client.set_registered_model_alias(
        REGISTERED_MODEL_NAME, CHAMPION_ALIAS, result.version
    )
    log.info(
        "train.champion_registered",
        extra={
            "model": champion.name,
            "registered_model": REGISTERED_MODEL_NAME,
            "version": result.version,
            "alias": CHAMPION_ALIAS,
            "run_id": champion.run_id,
        },
    )
    return str(result.version)


def write_summary(outcome: TrainingOutcome, path: Path) -> Path:
    """Write the training summary as JSON, for the report and the tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = outcome.to_dict()
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return path
