"""The monitoring job: validate, measure, push, log, record.

One function per moment data exists. ``run_scoring`` is what happens the day a
batch is scored, with no outcome available; ``run_label_arrival`` is what
happens months later when the outcome lands. They push under different
Prometheus jobs because in production they *are* different jobs.

Prometheus is operations — what is happening now, and what should page someone.
MLflow is the audit trail — what was decided, on which data, by which model
version. The same run writes to both, deliberately: an operator needs the first
and an auditor needs the second, and reconstructing either from the other is
guesswork.
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import mlflow
import numpy as np
import pandas as pd
import pandera.pandas as pa

from credit_monitor.constants import (
    CHAMPION_ALIAS,
    MLFLOW_TRACKING_URI,
    REGISTERED_MODEL_NAME,
    TARGET_COLUMN,
)
from credit_monitor.contracts.failures import blocking_failures, resolve_rule
from credit_monitor.contracts.scoring import LabelArrivalSchema, ScoringInputSchema
from credit_monitor.contracts.severity import RULE_SEVERITY, Rule
from credit_monitor.data.preprocess import MODEL_FEATURES
from credit_monitor.models.metrics import evaluate
from credit_monitor.models.score import ProbabilisticClassifier, predict_proba
from credit_monitor.monitoring import metrics as m
from credit_monitor.monitoring.config import MonitoringConfig
from credit_monitor.monitoring.logs import PipelineLogger, hash_row_id
from credit_monitor.reporting.drift import PREDICTION_COLUMN, champion_gain_shares
from credit_monitor.simulation.psi import psi

log = logging.getLogger(__name__)

MONITORING_EXPERIMENT: Final[str] = "credit-monitor-monitoring"

# How many rows to name in a log line when a rule fires. Enough to chase a few
# by hash, not enough to turn the log into a copy of the quarantine.
LOGGED_ROW_SAMPLE: Final[int] = 5


@dataclass(frozen=True, slots=True)
class ScoringOutcome:
    """Everything one scoring run produced."""

    batch_id: str
    scenario: str
    metrics: m.ScoringMetrics
    verdict_label: str
    mlflow_run_id: str = ""
    stage_failures: list[str] = field(default_factory=list)


def verdict_code(
    psi_values: dict[str, float], batch_size: int, config: MonitoringConfig
) -> tuple[int, str]:
    """Numeric verdict and its label, enforcing the minimum batch size.

    The minimum is enforced **here**, in the pipeline, and not only inside
    ``drift.verdict``: the pipeline is what pushes the metric, so a job that
    computed a colour and then pushed it anyway would put a number on a
    dashboard that the stage-2 A/A says means nothing. Below the floor the
    verdict is ``-1`` and ``sample_sufficient`` is 0 — never green, never red.
    """
    if batch_size < config.min_batch_size:
        return m.VERDICT_INSUFFICIENT_SAMPLE, "insufficient_sample"
    if not psi_values:
        return m.VERDICT_OK, "ok"
    worst = max(psi_values.values())
    if worst >= config.psi_alert_threshold:
        return m.VERDICT_CRITICAL, "critical"
    if worst >= config.psi_warn_threshold:
        return m.VERDICT_WARNING, "warning"
    return m.VERDICT_OK, "ok"


def contract_violations(
    frame: pd.DataFrame, schema: type[pa.DataFrameModel]
) -> tuple[dict[tuple[str, str], int], int, list[str]]:
    """Validate and count rows per (rule, severity), plus quarantined rows.

    Returns the counts, the number of rows failing a blocker, and the blocking
    rule names. Blockers do not raise here — the monitoring job's purpose is to
    *report* that a batch is bad, so it has to survive a bad batch.
    """
    try:
        schema.validate(frame, lazy=True)
        return {}, 0, []
    except pa.errors.SchemaErrors as errors:
        cases = errors.failure_cases.copy()
        cases["rule"] = [resolve_rule(str(name)).value for name in cases["check"]]
        cases["severity"] = [RULE_SEVERITY[Rule(rule)].value for rule in cases["rule"]]
        counts: dict[tuple[str, str], int] = {}
        for (rule, severity), group in cases.groupby(["rule", "severity"]):
            # Distinct rows, not failure cases: pandera melts a frame-level
            # check across every column, so one duplicated row arrives as
            # eleven cases and the count would be eleven times too large.
            distinct = group["index"].nunique(dropna=True)
            counts[(str(rule), str(severity))] = int(distinct) or len(group)
        blockers = blocking_failures(errors)
        quarantined = int(blockers["index"].nunique(dropna=True))
        return (
            counts,
            quarantined,
            sorted(set(blockers["rule"])) if len(blockers) else [],
        )


def run_scoring(
    batch_id: str,
    scenario: str,
    features: pd.DataFrame,
    reference: pd.DataFrame,
    model: ProbabilisticClassifier,
    config: MonitoringConfig,
    gain_shares: dict[str, float],
    champion_version: str,
    gateway: str | None = m.DEFAULT_GATEWAY,
    log_dir: Path | None = None,
) -> ScoringOutcome:
    """The scoring-time monitoring run. Never sees a label.

    Args:
        batch_id: Identifies the batch; part of the Pushgateway grouping key.
        scenario: One of the stage-2 ablation arms; the other part of the key.
        features: The batch as monitoring sees it at scoring time.
        reference: The stage-1 baseline.
        model: The champion.
        config: The measured drift policy.
        gain_shares: Champion gain share per feature, for the weighted PSI.
        champion_version: Recorded as an MLflow tag.
        gateway: Pushgateway address, or ``None`` to skip pushing.
        log_dir: Where the JSONL goes; ``None`` uses the default.

    Returns:
        The outcome, including the metrics that were pushed.
    """
    pipeline_log = (
        PipelineLogger.create(batch_id, scenario, log_dir)
        if log_dir
        else PipelineLogger.create(batch_id, scenario)
    )
    durations: dict[str, float] = {}
    status: dict[str, int] = {}
    failures: list[str] = []
    pipeline_log.emit(
        "scoring.started", stage="start", rows=len(features), batch_size=len(features)
    )

    # --- contract ---------------------------------------------------------
    started = time.perf_counter()
    violations, quarantined, blocking = contract_violations(
        features, ScoringInputSchema
    )
    durations["contract"] = time.perf_counter() - started
    status["contract"] = 0 if blocking else 1
    if blocking:
        failures.append("contract")
        pipeline_log.emit(
            "contract.blocked",
            stage="contract",
            status="failed",
            level=logging.ERROR,
            rules=blocking,
            rows_quarantined=quarantined,
            # Hashed row ids only. A log line is the least access-controlled
            # artefact a pipeline makes; the person's data does not go in it.
            sample_rows=[
                hash_row_id(row) for row in features.index[:LOGGED_ROW_SAMPLE].tolist()
            ],
        )
    else:
        pipeline_log.emit(
            "contract.passed",
            stage="contract",
            violations=len(violations),
            rows_quarantined=quarantined,
        )

    # --- drift ------------------------------------------------------------
    started = time.perf_counter()
    psi_values = {
        feature: psi(reference[feature], features[feature], 10)
        for feature in MODEL_FEATURES
        if feature in features.columns
    }
    weighted = {
        feature: value * gain_shares.get(feature, 0.0)
        for feature, value in psi_values.items()
    }
    durations["drift"] = time.perf_counter() - started
    status["drift"] = 1

    code, verdict_name = verdict_code(psi_values, len(features), config)
    sufficient = len(features) >= config.min_batch_size
    if not sufficient:
        pipeline_log.emit(
            "drift.insufficient_sample",
            stage="drift",
            status="insufficient_sample",
            level=logging.WARNING,
            batch_size=len(features),
            min_batch_size=config.min_batch_size,
        )

    # --- scoring ----------------------------------------------------------
    started = time.perf_counter()
    probabilities = predict_proba(model, features)
    reference_probabilities = predict_proba(model, reference)
    prediction_psi = psi(
        pd.Series(reference_probabilities, name=PREDICTION_COLUMN),
        pd.Series(probabilities, name=PREDICTION_COLUMN),
        10,
    )
    durations["scoring"] = time.perf_counter() - started
    status["scoring"] = 1

    scoring_metrics = m.ScoringMetrics(
        rows_in=len(features),
        rows_quarantined=quarantined,
        batch_size=len(features),
        sample_sufficient=sufficient,
        drift_verdict=code,
        prediction_mean=float(np.mean(probabilities)),
        prediction_psi=prediction_psi,
        drifted_warning=sum(
            1
            for value in psi_values.values()
            if config.psi_warn_threshold <= value < config.psi_alert_threshold
        ),
        drifted_critical=sum(
            1 for value in psi_values.values() if value >= config.psi_alert_threshold
        ),
        last_success_timestamp_seconds=time.time(),
        psi_by_feature=psi_values,
        psi_weighted_by_feature=weighted,
        violations=violations,
        stage_durations_seconds=durations,
        stage_status=status,
    )

    if gateway:
        m.delete_group(m.SCORING_JOB, scenario, batch_id, gateway)
        m.push(
            m.build_scoring_registry(scoring_metrics),
            m.SCORING_JOB,
            scenario,
            batch_id,
            gateway,
        )

    run_id = _record_mlflow(
        batch_id, scenario, scoring_metrics, verdict_name, champion_version
    )
    pipeline_log.emit(
        "scoring.finished",
        stage="finish",
        status="failed" if failures else "ok",
        verdict=verdict_name,
        drift_verdict=code,
        drifted_critical=scoring_metrics.drifted_critical,
        prediction_mean=round(scoring_metrics.prediction_mean, 6),
        duration_seconds=round(sum(durations.values()), 4),
        mlflow_run_id=run_id,
    )
    return ScoringOutcome(
        batch_id=batch_id,
        scenario=scenario,
        metrics=scoring_metrics,
        verdict_label=verdict_name,
        mlflow_run_id=run_id,
        stage_failures=failures,
    )


def run_label_arrival(
    batch_id: str,
    scenario: str,
    features: pd.DataFrame,
    labels: pd.DataFrame,
    predictions: pd.DataFrame,
    model: ProbabilisticClassifier,
    label_lag_months: int,
    gateway: str | None = m.DEFAULT_GATEWAY,
    log_dir: Path | None = None,
) -> m.LabelMetrics:
    """The label-arrival run, months after scoring. A separate Prometheus job.

    Uses the predictions **as recorded at scoring time**, not fresh ones: the
    question is how the model did on the day it decided, and re-scoring now
    would quietly answer a different question with a possibly different model.
    """
    pipeline_log = (
        PipelineLogger.create(batch_id, scenario, log_dir)
        if log_dir
        else PipelineLogger.create(batch_id, scenario)
    )
    target = pd.Categorical(
        labels[TARGET_COLUMN].astype(str), categories=["0", "1"], ordered=True
    )
    validated = labels.assign(**{TARGET_COLUMN: target})
    try:
        LabelArrivalSchema.validate(validated, lazy=True)
        label_status = "ok"
    except pa.errors.SchemaErrors as errors:
        label_status = "warning"
        pipeline_log.emit(
            "labels.contract_warnings",
            stage="labels",
            status="warning",
            level=logging.WARNING,
            rules=sorted(
                {resolve_rule(str(c)).value for c in errors.failure_cases["check"]}
            ),
        )

    truth = (labels[TARGET_COLUMN].astype(str) == "1").to_numpy(dtype=int)
    probabilities = predictions[PREDICTION_COLUMN].to_numpy(dtype=float)
    core = evaluate(truth, probabilities)
    label_metrics = m.LabelMetrics(
        auc=core.auc_roc,
        brier=core.brier,
        calibration_gap=float(np.mean(probabilities)) - float(np.mean(truth)),
        observed_default_rate=float(np.mean(truth)),
        label_lag_months=label_lag_months,
    )

    if gateway:
        m.delete_group(m.LABEL_JOB, scenario, batch_id, gateway)
        m.push(
            m.build_label_registry(label_metrics),
            m.LABEL_JOB,
            scenario,
            batch_id,
            gateway,
        )

    pipeline_log.emit(
        "labels.finished",
        stage="labels",
        status=label_status,
        rows=len(labels),
        auc=round(label_metrics.auc, 6),
        brier=round(label_metrics.brier, 6),
        calibration_gap=round(label_metrics.calibration_gap, 6),
        observed_default_rate=round(label_metrics.observed_default_rate, 6),
        label_lag_months=label_lag_months,
        features_seen=len(features.columns),
    )
    return label_metrics


def _record_mlflow(
    batch_id: str,
    scenario: str,
    scoring: m.ScoringMetrics,
    verdict_name: str,
    champion_version: str,
) -> str:
    """Log the run to the monitoring experiment on the day-4 SQLite store."""
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    client = mlflow.tracking.MlflowClient()
    if client.get_experiment_by_name(MONITORING_EXPERIMENT) is None:
        client.create_experiment(MONITORING_EXPERIMENT)
    mlflow.set_experiment(MONITORING_EXPERIMENT)

    with mlflow.start_run(run_name=f"{scenario}/{batch_id}") as run:
        mlflow.log_params(
            {
                "batch_id": batch_id,
                "scenario": scenario,
                "batch_size": scoring.batch_size,
            }
        )
        mlflow.log_metrics(
            {
                "drift_verdict": float(scoring.drift_verdict),
                "sample_sufficient": float(scoring.sample_sufficient),
                "rows_in": float(scoring.rows_in),
                "rows_quarantined": float(scoring.rows_quarantined),
                "prediction_mean": scoring.prediction_mean,
                "prediction_psi": scoring.prediction_psi,
                "drifted_warning": float(scoring.drifted_warning),
                "drifted_critical": float(scoring.drifted_critical),
                **{f"psi_{k}": v for k, v in scoring.psi_by_feature.items()},
            }
        )
        mlflow.set_tags(
            {
                "verdict": verdict_name,
                "champion_model": REGISTERED_MODEL_NAME,
                "champion_alias": CHAMPION_ALIAS,
                "champion_version": champion_version,
            }
        )
        return run.info.run_id


def champion_context(model: ProbabilisticClassifier) -> tuple[dict[str, float], str]:
    """Gain shares and the registered version behind the ``champion`` alias."""
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    try:
        version = (
            mlflow.tracking.MlflowClient()
            .get_model_version_by_alias(REGISTERED_MODEL_NAME, CHAMPION_ALIAS)
            .version
        )
    except Exception as error:
        log.warning("monitor.champion_version_unavailable", extra={"error": str(error)})
        version = "unknown"
    return champion_gain_shares(model), str(version)


def label_metrics_as_dict(label_metrics: m.LabelMetrics) -> dict[str, Any]:
    """Flatten label metrics, for printing and for MLflow."""
    return {
        "auc": label_metrics.auc,
        "brier": label_metrics.brier,
        "calibration_gap": label_metrics.calibration_gap,
        "observed_default_rate": label_metrics.observed_default_rate,
        "label_lag_months": float(label_metrics.label_lag_months),
    }
