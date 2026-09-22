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
    ROW_SALT_ENV,
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


def test_the_salt_is_overridable_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pseudonymisation under LGPD Art. 13, not anonymisation: the committed
    # default is public, so a real deployment has to be able to supply its own
    # salt without touching the code. Same row id, different salt, different
    # hash is the whole property.
    default = hash_row_id(42)

    monkeypatch.setenv(ROW_SALT_ENV, "outro-sal-de-producao")

    assert hash_row_id(42) != default
    assert hash_row_id(42) == hash_row_id(42)


def test_logs_carry_the_loki_filter_labels(tmp_path: Path) -> None:
    logger = PipelineLogger.create("month_03", "stress_only", tmp_path)
    logger.emit("drift.done", stage="drift")

    record = logger.read_records()[0]

    assert record["batch_id"] == "month_03"
    assert record["scenario"] == "stress_only"
    assert record["stage"] == "drift"
    assert record["status"] == "ok"


# --------------------------------------------------------------------------
# A blocked batch stops the pipeline
# --------------------------------------------------------------------------


class StubClassifier:
    """Deterministic stand-in, so the test needs no MLflow model."""

    def predict_proba(self, X: pd.DataFrame) -> "object":  # noqa: N803
        """Constant 7% — the batch is blocked before this matters."""
        import numpy as np

        positive = np.full(len(X), 0.07)
        return np.column_stack([1.0 - positive, positive])


def blocked_batch(rows: int) -> pd.DataFrame:
    """A features frame above the sample floor with a blocker-level defect."""
    raw = make_random_frame(n_rows=rows, n_positive=int(rows * 0.0668))
    frame = raw.drop(columns=[TARGET_COLUMN])
    # The 96/98 administrative code: a blocker by the stage-1 severity mapping.
    frame.loc[0:9, "NumberOfTimes90DaysLate"] = 98
    frame["income_missing"] = 0
    return frame


def test_a_blocked_batch_above_the_floor_still_short_circuits(
    config: MonitoringConfig, tmp_path: Path
) -> None:
    # 5,000 rows, five times the floor. The 204-row demo batch was blocked AND
    # under the floor, so `insufficient_sample` masked the missing
    # short-circuit; at this size nothing hides it. A blocked batch of this
    # size would otherwise be handed a drift colour computed on rows the
    # contract had just rejected.
    from credit_monitor.monitoring.run import run_scoring

    rows = 5000
    assert rows > config.min_batch_size

    outcome = run_scoring(
        batch_id="blocked_big",
        scenario="test",
        features=blocked_batch(rows),
        reference=make_random_frame(n_rows=2000, n_positive=134).drop(
            columns=[TARGET_COLUMN]
        ),
        model=StubClassifier(),  # type: ignore[arg-type]
        config=config,
        gain_shares={},
        champion_version="test",
        gateway=None,
        log_dir=tmp_path,
    )

    assert outcome.verdict_label == "blocked"
    assert outcome.metrics.drift_verdict == m.VERDICT_BLOCKED == -2
    # Sufficient sample, and still blocked: the two states are independent.
    assert outcome.metrics.sample_sufficient is True


def test_skipped_stages_are_marked_skipped_never_ok(
    config: MonitoringConfig, tmp_path: Path
) -> None:
    from credit_monitor.monitoring.run import run_scoring

    outcome = run_scoring(
        batch_id="blocked_big",
        scenario="test",
        features=blocked_batch(5000),
        reference=make_random_frame(n_rows=2000, n_positive=134).drop(
            columns=[TARGET_COLUMN]
        ),
        model=StubClassifier(),  # type: ignore[arg-type]
        config=config,
        gain_shares={},
        champion_version="test",
        gateway=None,
        log_dir=tmp_path,
    )

    status = outcome.metrics.stage_status
    assert status["contract"] == m.STAGE_FAILED == 0
    # -1, not 0 and certainly not 1: the stage did not fail, it never ran.
    assert status["drift"] == m.STAGE_SKIPPED == -1
    assert status["scoring"] == m.STAGE_SKIPPED
    assert m.STAGE_SKIPPED not in {m.STAGE_OK, m.STAGE_FAILED}


def test_no_drift_or_prediction_metrics_are_published_for_a_blocked_batch(
    config: MonitoringConfig, tmp_path: Path
) -> None:
    from credit_monitor.monitoring.run import run_scoring

    outcome = run_scoring(
        batch_id="blocked_big",
        scenario="test",
        features=blocked_batch(5000),
        reference=make_random_frame(n_rows=2000, n_positive=134).drop(
            columns=[TARGET_COLUMN]
        ),
        model=StubClassifier(),  # type: ignore[arg-type]
        config=config,
        gain_shares={},
        champion_version="test",
        gateway=None,
        log_dir=tmp_path,
    )

    samples = m.registry_samples(m.build_scoring_registry(outcome.metrics))

    # Absent, not zero: a zero would be indistinguishable from a real reading.
    assert not [key for key in samples if key.startswith("drift_psi")]
    assert not [key for key in samples if key.startswith("drifted_features")]
    assert "prediction_mean" not in samples
    assert "prediction_psi" not in samples
    # What a blocked batch *does* report: why it was blocked.
    assert samples["drift_verdict"] == -2
    assert samples["rows_quarantined"] > 0
    assert [key for key in samples if key.startswith("contract_violations")]


def test_the_blocked_code_is_distinct_from_every_other_verdict() -> None:
    codes = {
        m.VERDICT_OK,
        m.VERDICT_WARNING,
        m.VERDICT_CRITICAL,
        m.VERDICT_INSUFFICIENT_SAMPLE,
        m.VERDICT_BLOCKED,
    }

    assert len(codes) == 5
    # Both sentinels sit below the drift ordering, so a dashboard sorting by
    # the code cannot read either as "less drift than ok".
    assert m.VERDICT_BLOCKED < m.VERDICT_INSUFFICIENT_SAMPLE < m.VERDICT_OK


# --------------------------------------------------------------------------
# The blind window
# --------------------------------------------------------------------------


def test_the_blind_window_is_zero_when_features_drift_too() -> None:
    # `full`: the degradation starts at month 3 and the drift rule is already
    # firing, so nothing is hidden.
    from credit_monitor.monitoring import blind_window

    window = blind_window.compute(
        "full",
        calibration_gap_by_month={0: -0.003, 3: -0.017, 4: -0.021},
        drift_alarm_by_month={0: False, 3: True, 4: True},
        label_lag_months=2,
        degradation_gap_threshold=-0.015,
    )

    assert window.degraded_months == (3, 4)
    assert window.months_blind == 0
    assert window.first_signal_month == 3


def test_the_blind_window_equals_the_label_lag_when_no_feature_drifts() -> None:
    # `stress_only`: P(y|X) moved and P(X) did not, so the only signal is the
    # outcome — and the outcome is late by construction.
    from credit_monitor.monitoring import blind_window

    window = blind_window.compute(
        "stress_only",
        calibration_gap_by_month={
            0: -0.003,
            1: -0.005,
            2: -0.006,
            3: -0.017,
            4: -0.018,
            5: -0.028,
            6: -0.034,
        },
        drift_alarm_by_month=dict.fromkeys(range(7), False),
        label_lag_months=2,
        degradation_gap_threshold=-0.015,
    )

    assert window.degraded_months == (3, 4, 5, 6)
    assert window.blind_months == (3, 4)
    assert window.months_blind == 2
    assert window.first_degraded_month == 3
    assert window.first_signal_month == 5


def test_a_scenario_that_never_degrades_has_no_blind_window() -> None:
    # `composition_only`: loud drift, calibration intact. Nothing to be blind
    # to, which is a different thing from being blind.
    from credit_monitor.monitoring import blind_window

    window = blind_window.compute(
        "composition_only",
        calibration_gap_by_month={0: -0.003, 3: -0.0001, 6: -0.003},
        drift_alarm_by_month={0: False, 3: True, 6: True},
        label_lag_months=2,
        degradation_gap_threshold=-0.015,
    )

    assert window.degraded_months == ()
    assert window.months_blind == 0
    assert window.first_degraded_month is None


def test_a_longer_lag_widens_the_blind_window() -> None:
    from credit_monitor.monitoring import blind_window

    gaps = {month: (-0.03 if month >= 2 else -0.001) for month in range(8)}
    alarms = dict.fromkeys(range(8), False)

    short = blind_window.compute("s", gaps, alarms, 1, -0.015)
    long = blind_window.compute("s", gaps, alarms, 4, -0.015)

    assert short.months_blind < long.months_blind


def test_the_label_lag_comes_from_the_config(config: MonitoringConfig) -> None:
    assert config.label_lag_months == 2
    # Calibrated on day 10; the provisional -0.015 had a measured false-alarm
    # rate of 0.0% — safe and blind, 6.5 null standard deviations out.
    assert config.degradation_gap_threshold == -0.0056


def test_the_lead_time_is_signed_and_positive_means_early_warning() -> None:
    # The unsigned count was clipped at zero and said "0 months blind" both for
    # a monitor that warns on time and for one that warns a month ahead. The
    # sign is the finding: the same monitor, on the same data, warns early or
    # late depending on the mechanism.
    from credit_monitor.monitoring import blind_window

    early = blind_window.compute(
        "full",
        calibration_gap_by_month={0: -0.003, 1: -0.004, 2: -0.006, 3: -0.017},
        # The drift alarm fires in month 2, one month before the degradation.
        drift_alarm_by_month={0: False, 1: False, 2: True, 3: True},
        label_lag_months=2,
        degradation_gap_threshold=-0.015,
    )

    assert early.first_degraded_month == 3
    assert early.first_drift_alarm_month == 2
    assert early.lead_time_months == 1  # positive: warned a month early


def test_a_negative_lead_time_is_blindness() -> None:
    from credit_monitor.monitoring import blind_window

    late = blind_window.compute(
        "stress_only",
        calibration_gap_by_month={0: -0.003, 3: -0.017, 4: -0.018},
        drift_alarm_by_month=dict.fromkeys(range(7), False),
        label_lag_months=2,
        degradation_gap_threshold=-0.015,
    )

    assert late.lead_time_months == -2


def test_an_alarm_before_the_degradation_still_counts_as_the_signal() -> None:
    # The correction: restricting the search for the first alarm to degraded
    # months threw away exactly the early-warning case.
    from credit_monitor.monitoring import blind_window

    window = blind_window.compute(
        "full",
        calibration_gap_by_month={0: -0.001, 5: -0.020},
        drift_alarm_by_month={0: True, 5: True},
        label_lag_months=2,
        degradation_gap_threshold=-0.015,
    )

    assert window.first_drift_alarm_month == 0
    assert window.lead_time_months == 5


def test_no_degradation_means_no_lead_time() -> None:
    from credit_monitor.monitoring import blind_window

    window = blind_window.compute(
        "composition_only",
        calibration_gap_by_month={0: -0.001, 6: -0.003},
        drift_alarm_by_month={6: True},
        label_lag_months=2,
        degradation_gap_threshold=-0.0056,
    )

    assert window.lead_time_months is None


def test_the_calibrated_thresholds_come_from_the_config(
    config: MonitoringConfig,
) -> None:
    # Measured, not chosen: percentile 0.5% and 1% of the null distributions
    # from 500 no-drift draws of 7,000 rows (docs/metrics.md §4.2).
    assert config.calibration_gap_critical == -0.0056
    assert config.auc_warning == 0.8372
    # The blind window uses the same critical threshold: two different numbers
    # for "the model got worse" would give two answers to one question.
    assert config.degradation_gap_threshold == config.calibration_gap_critical
