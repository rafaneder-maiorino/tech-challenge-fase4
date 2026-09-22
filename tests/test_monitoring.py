"""Tests for the observability layer. No Docker, no network, no MLflow.

Metrics are built into an in-process ``CollectorRegistry`` and inspected there.
That is not only about CI having no daemon: a test that needed a Pushgateway
would be testing the gateway, and what needs testing is that the right numbers
carry the right labels.
"""

import json
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
import pytest
from conftest import make_random_frame

from credit_monitor.constants import TARGET_COLUMN
from credit_monitor.contracts.scoring import LabelArrivalSchema, ScoringInputSchema
from credit_monitor.data.preprocess import (
    MODEL_FEATURES,
    clean,
    fit_imputation,
    impute,
)
from credit_monitor.monitoring import metrics as m
from credit_monitor.monitoring.config import MonitoringConfig
from credit_monitor.monitoring.logs import (
    FORBIDDEN_FIELDS,
    PipelineLogger,
    hash_row_id,
)
from credit_monitor.monitoring.run import verdict_code


@pytest.fixture
def config() -> MonitoringConfig:
    return MonitoringConfig.load()


def sample_scoring(batch_size: int = 7368, verdict: int = 0) -> m.ScoringMetrics:
    return m.ScoringMetrics(
        rows_in=batch_size,
        rows_quarantined=12,
        batch_size=batch_size,
        sample_sufficient=batch_size >= 1000,
        drift_verdict=verdict,
        prediction_mean=0.0681,
        prediction_psi=0.0018,
        drifted_warning=2,
        drifted_critical=3,
        last_success_timestamp_seconds=1_790_000_000.0,
        psi_by_feature={"age": 0.314, "DebtRatio": 0.024},
        psi_weighted_by_feature={"age": 0.0103, "DebtRatio": 0.0006},
        violations={("age_range", "blocker"): 3},
        stage_durations_seconds={"contract": 0.03, "drift": 0.12},
        stage_status={"contract": 1, "drift": 1},
    )


# --------------------------------------------------------------------------
# The grouping key
# --------------------------------------------------------------------------


def test_the_grouping_key_carries_scenario_and_batch_id() -> None:
    # The Pushgateway rejects client timestamps, so months pushed back to back
    # would collapse into one instant. The batch id in the key is what makes
    # each month its own persistent series, and the scenario is what lets the
    # 2x2 sit on one screen.
    key = m.grouping_key("stress_only", "month_06")

    assert key == {"scenario": "stress_only", "batch_id": "month_06"}


def test_different_batches_produce_different_groups() -> None:
    assert m.grouping_key("full", "month_00") != m.grouping_key("full", "month_06")
    assert m.grouping_key("full", "month_06") != m.grouping_key(
        "stress_only", "month_06"
    )


def test_scoring_and_label_jobs_are_distinct() -> None:
    # In production they are different jobs, running months apart.
    assert m.SCORING_JOB != m.LABEL_JOB


# --------------------------------------------------------------------------
# Metric construction, in process
# --------------------------------------------------------------------------


def test_every_scoring_metric_is_registered_with_its_labels() -> None:
    samples = m.registry_samples(m.build_scoring_registry(sample_scoring()))

    assert samples["rows_in"] == 7368
    assert samples["rows_quarantined"] == 12
    assert samples["sample_sufficient"] == 1
    assert samples["drift_verdict"] == 0
    assert samples['drift_psi{feature="age"}'] == pytest.approx(0.314)
    assert samples['drift_psi_weighted{feature="age"}'] == pytest.approx(0.0103)
    assert samples['drifted_features{level="critical"}'] == 3
    assert samples['contract_violations{rule="age_range",severity="blocker"}'] == 3
    assert samples['stage_duration_seconds{stage="contract"}'] == pytest.approx(0.03)
    assert samples['stage_status{stage="drift"}'] == 1


def test_label_metrics_are_registered() -> None:
    label_metrics = m.LabelMetrics(
        auc=0.7779,
        brier=0.1529,
        calibration_gap=-0.0396,
        observed_default_rate=0.2446,
        label_lag_months=6,
    )

    samples = m.registry_samples(m.build_label_registry(label_metrics))

    assert samples["auc"] == pytest.approx(0.7779)
    assert samples["calibration_gap"] == pytest.approx(-0.0396)
    assert samples["label_lag_months"] == 6


def test_each_registry_is_independent() -> None:
    # A fresh registry per push, never the process-wide default: otherwise a
    # job reporting two batches would carry the first batch's label values
    # into the second push.
    first = m.build_scoring_registry(sample_scoring(batch_size=7368))
    second = m.build_scoring_registry(sample_scoring(batch_size=400))

    assert m.registry_samples(first)["batch_size"] == 7368
    assert m.registry_samples(second)["batch_size"] == 400


# --------------------------------------------------------------------------
# The minimum batch size
# --------------------------------------------------------------------------


def test_a_small_batch_yields_minus_one_and_insufficient_sample(
    config: MonitoringConfig,
) -> None:
    # Enforced in the pipeline, not only in drift.verdict: the pipeline is what
    # pushes, so a job that computed a colour and pushed it anyway would put a
    # number on a dashboard that the stage-2 A/A says means nothing.
    code, label = verdict_code({"age": 0.02}, config.min_batch_size - 1, config)

    assert code == m.VERDICT_INSUFFICIENT_SAMPLE == -1
    assert label == "insufficient_sample"

    samples = m.registry_samples(
        m.build_scoring_registry(sample_scoring(batch_size=400, verdict=code))
    )
    assert samples["drift_verdict"] == -1
    assert samples["sample_sufficient"] == 0


def test_a_small_batch_is_insufficient_even_with_extreme_drift(
    config: MonitoringConfig,
) -> None:
    # Never red either. The measurement cannot separate drift from its own
    # bias at that size, and a red would report that inability as knowledge.
    code, label = verdict_code({"age": 0.95}, 250, config)

    assert code == m.VERDICT_INSUFFICIENT_SAMPLE
    assert label == "insufficient_sample"


@pytest.mark.parametrize(
    ("worst_psi", "expected"),
    [
        (0.05, m.VERDICT_OK),
        (0.15, m.VERDICT_WARNING),
        (0.40, m.VERDICT_CRITICAL),
    ],
)
def test_above_the_floor_the_psi_bands_decide(
    worst_psi: float, expected: int, config: MonitoringConfig
) -> None:
    code, _ = verdict_code({"age": worst_psi}, config.min_batch_size, config)

    assert code == expected


def test_the_insufficient_code_sits_outside_the_severity_ordering() -> None:
    # -1, not 3: a dashboard sorting by the code must not read "insufficient
    # sample" as "less drift than ok".
    assert m.VERDICT_INSUFFICIENT_SAMPLE < m.VERDICT_OK


# --------------------------------------------------------------------------
# The contracts, split by when data exists
# --------------------------------------------------------------------------


def test_the_scoring_contract_accepts_a_target_free_frame() -> None:
    frame = make_random_frame(n_rows=300, n_positive=20).drop(columns=[TARGET_COLUMN])

    ScoringInputSchema.validate(frame, lazy=True)  # raises if it rejects


def test_the_scoring_contract_still_catches_a_feature_violation() -> None:
    frame = make_random_frame(n_rows=300, n_positive=20).drop(columns=[TARGET_COLUMN])
    frame.loc[0, "age"] = 0

    with pytest.raises(pa.errors.SchemaErrors) as caught:
        ScoringInputSchema.validate(frame, lazy=True)

    assert "age_range" in set(caught.value.failure_cases["check"].astype(str))


def test_the_label_contract_rejects_a_non_binary_target() -> None:
    labels = pd.DataFrame(
        {
            "row_id": range(300),
            TARGET_COLUMN: pd.Categorical(
                ["2"] * 300, categories=["0", "1", "2"], ordered=True
            ),
        }
    )

    with pytest.raises(pa.errors.SchemaErrors) as caught:
        LabelArrivalSchema.validate(labels, lazy=True)

    assert "target_binary" in set(caught.value.failure_cases["check"].astype(str))


def test_the_scoring_contract_owns_no_label_rule() -> None:
    # The whole point of the split: the rules that need an outcome are not in
    # the contract that runs before the outcome exists.
    built = ScoringInputSchema.to_schema()
    names = {str(c.name) for col in built.columns.values() for c in col.checks}
    names |= {str(c.name) for c in built.checks}

    assert "target_binary" not in names
    assert "positive_rate_band" not in names


def test_a_real_features_file_validates_against_the_scoring_contract() -> None:
    raw = make_random_frame(n_rows=1200, n_positive=80)
    raw.loc[0:239, "MonthlyIncome"] = None
    raw.loc[0:239, "NumberOfDependents"] = None
    cleaned, _ = clean(raw)
    prepared = impute(cleaned, fit_imputation(cleaned))
    # Shaped like what the pipeline writes: no target, plus its own plumbing.
    features = prepared[list(MODEL_FEATURES)].assign(row_id=range(len(prepared)))

    ScoringInputSchema.validate(features, lazy=True)


# --------------------------------------------------------------------------
# Logs carry no personal data
# --------------------------------------------------------------------------


def test_log_records_never_contain_raw_feature_values(tmp_path: Path) -> None:
    # A log line is the least access-controlled artefact a pipeline makes. The
    # features here are a person's income, age and debt; writing them to a log
    # would export the dataset one line at a time.
    logger = PipelineLogger.create("month_06", "full", tmp_path)
    logger.emit("scoring.finished", stage="finish", rows=7368, verdict="critical")

    records = logger.read_records()

    assert len(records) == 1
    assert not FORBIDDEN_FIELDS & set(records[0])
    written = json.dumps(records[0])
    assert "MonthlyIncome" not in written


def test_attempting_to_log_a_feature_value_raises(tmp_path: Path) -> None:
    logger = PipelineLogger.create("month_06", "full", tmp_path)

    with pytest.raises(ValueError, match="MonthlyIncome"):
        logger.emit("oops", stage="finish", MonthlyIncome=5461.0)


def test_row_identity_travels_as_a_hash(tmp_path: Path) -> None:
    logger = PipelineLogger.create("dirty_batch", "full", tmp_path)
    logger.emit(
        "contract.blocked",
        stage="contract",
        status="failed",
        sample_rows=[hash_row_id(0), hash_row_id(1)],
    )

    record = logger.read_records()[0]

    assert record["sample_rows"] == [hash_row_id(0), hash_row_id(1)]
    assert all(len(h) == 16 for h in record["sample_rows"])
    # Stable across runs, so a Loki query can follow one row; and not the id.
    assert hash_row_id(0) == hash_row_id(0)
    assert hash_row_id(0) != "0"


def test_logs_carry_the_loki_filter_labels(tmp_path: Path) -> None:
    logger = PipelineLogger.create("month_03", "stress_only", tmp_path)
    logger.emit("drift.done", stage="drift")

    record = logger.read_records()[0]

    assert record["batch_id"] == "month_03"
    assert record["scenario"] == "stress_only"
    assert record["stage"] == "drift"
    assert record["status"] == "ok"
