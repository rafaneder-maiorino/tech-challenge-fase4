"""Tests for the Evidently drift configuration.

What is pinned here is that nothing is left to Evidently's defaults, that the
custom stattest really is the one running, and that the two report types differ
in the one way that matters: only one of them can see a label.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from conftest import make_random_frame

from credit_monitor.constants import TARGET_COLUMN
from credit_monitor.data.preprocess import (
    MODEL_FEATURES,
    clean,
    fit_imputation,
    impute,
)
from credit_monitor.reporting import drift
from credit_monitor.simulation.psi import psi as our_psi

ROWS = 900
POSITIVES = 60


class StubClassifier:
    """Deterministic stand-in, same protocol the scorer requires."""

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:  # noqa: N803
        """Logistic in utilisation, so scores vary."""
        logit = 4.0 * X["RevolvingUtilizationOfUnsecuredLines"].to_numpy() - 2.0
        positive = 1.0 / (1.0 + np.exp(-logit))
        return np.column_stack([1.0 - positive, positive])


@pytest.fixture
def scored() -> pd.DataFrame:
    """A model-ready frame with score and label attached."""
    raw = make_random_frame(n_rows=ROWS, n_positive=POSITIVES)
    raw.loc[0:179, "MonthlyIncome"] = None
    raw.loc[0:179, "NumberOfDependents"] = None
    cleaned, _ = clean(raw)
    frame = impute(cleaned, fit_imputation(cleaned))
    probabilities = StubClassifier().predict_proba(frame[list(MODEL_FEATURES)])[:, 1]
    return drift.attach_scores(frame, probabilities, frame[TARGET_COLUMN])


# --------------------------------------------------------------------------
# The custom stattest
# --------------------------------------------------------------------------


def test_the_custom_stattest_is_registered_under_its_name() -> None:
    from evidently.legacy.calculations.stattests.registry import get_stattest
    from evidently.legacy.core import ColumnType

    resolved = get_stattest(
        pd.Series([1.0, 2.0]),
        pd.Series([1.0, 2.0]),
        ColumnType.Numerical,
        drift.REFERENCE_PSI,
    )

    assert resolved.name == drift.REFERENCE_PSI


def test_the_custom_stattest_delegates_to_our_psi(scored: pd.DataFrame) -> None:
    # Delegation, not reimplementation: the number in Evidently's HTML and the
    # number in our summary must be the same number, not two estimates that
    # happen to agree.
    reference = scored.iloc[:600]
    current = scored.iloc[600:].copy()
    current["RevolvingUtilizationOfUnsecuredLines"] *= 1.5

    score, drifted = drift._reference_psi_impl(
        reference["RevolvingUtilizationOfUnsecuredLines"],
        current["RevolvingUtilizationOfUnsecuredLines"],
        None,
        drift.WARN_THRESHOLD,
    )
    direct = our_psi(
        reference["RevolvingUtilizationOfUnsecuredLines"],
        current["RevolvingUtilizationOfUnsecuredLines"],
        drift.PSI_BINS,
    )

    assert score == pytest.approx(direct, abs=1e-12)
    assert drifted == (score >= drift.WARN_THRESHOLD)


# --------------------------------------------------------------------------
# Nothing is left to defaults
# --------------------------------------------------------------------------


def test_every_feature_gets_an_explicit_psi_method() -> None:
    # With a ~103k reference, auto-selection may pick another test and omit
    # exactly what the stage asks for. Every column names its method.
    metrics = drift.scoring_metrics()
    psi_columns = {m.column for m in metrics if m.method == drift.REFERENCE_PSI}

    assert set(MODEL_FEATURES) <= psi_columns
    assert all(m.method is not None for m in metrics)


def test_the_continuous_features_also_get_ks() -> None:
    ks_columns = {m.column for m in drift.scoring_metrics() if m.method == "ks"}

    assert ks_columns == set(drift.CONTINUOUS_FEATURES)


def test_the_prediction_column_is_monitored_at_scoring_time() -> None:
    # The score distribution aggregates every feature and is available with no
    # label at all, which makes it the earliest signal the monitor has.
    columns = {m.column for m in drift.scoring_metrics()}

    assert drift.PREDICTION_COLUMN in columns


def test_thresholds_are_the_declared_bands() -> None:
    assert drift.WARN_THRESHOLD == 0.10
    assert drift.ALERT_THRESHOLD == 0.25


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, drift.VERDICT_STABLE),
        (0.099, drift.VERDICT_STABLE),
        (0.10, drift.VERDICT_MODERATE),
        (0.249, drift.VERDICT_MODERATE),
        (0.25, drift.VERDICT_SIGNIFICANT),
        (1.0, drift.VERDICT_SIGNIFICANT),
    ],
)
def test_verdict_bands(value: float, expected: str) -> None:
    assert drift.verdict(value) == expected


# --------------------------------------------------------------------------
# The two report types differ in exactly one way
# --------------------------------------------------------------------------


def test_the_scoring_dataset_carries_no_label(scored: pd.DataFrame) -> None:
    # The scoring-time report is what exists in production today. The outcome
    # does not exist yet, so the dataset handed to it must not contain one.
    dataset = drift.feature_dataset(scored, with_prediction=True)
    columns = set(dataset.as_dataframe().columns)

    assert drift.LABEL_COLUMN not in columns
    assert TARGET_COLUMN not in columns
    assert drift.PREDICTION_COLUMN in columns


def test_the_label_dataset_carries_the_label(scored: pd.DataFrame) -> None:
    dataset = drift.scored_dataset(scored)
    columns = set(dataset.as_dataframe().columns)

    assert drift.LABEL_COLUMN in columns
    assert drift.PREDICTION_COLUMN in columns


def test_a_scoring_report_runs_and_reports_every_feature(
    scored: pd.DataFrame,
) -> None:
    reference = scored.iloc[:600]
    current = scored.iloc[600:]

    values = drift.metric_values(drift.scoring_report(reference, current))

    assert len(values) >= len(MODEL_FEATURES)
    assert all(np.isfinite(v) for v in values.values())


def test_a_label_report_reports_classification_quality(
    scored: pd.DataFrame,
) -> None:
    reference = scored.iloc[:600]
    current = scored.iloc[600:]

    values = drift.metric_values(drift.label_report(reference, current))

    assert any("RocAuc" in name for name in values)


# --------------------------------------------------------------------------
# The drift table
# --------------------------------------------------------------------------


def test_drift_table_covers_every_feature_with_ks_only_on_continuous(
    scored: pd.DataFrame,
) -> None:
    gain = dict.fromkeys(MODEL_FEATURES, 1.0 / len(MODEL_FEATURES))

    rows = drift.drift_table(3, scored.iloc[:600], scored.iloc[600:], gain)

    assert {row.column for row in rows} == set(MODEL_FEATURES)
    for row in rows:
        if row.column in drift.CONTINUOUS_FEATURES:
            assert row.ks_statistic is not None and row.ks_pvalue is not None
        else:
            assert row.ks_statistic is None and row.ks_pvalue is None


def test_impact_weights_drift_by_how_much_the_model_uses_the_feature() -> None:
    # A PSI of 0.30 on a feature carrying 28% of the gain is not the same
    # event as the same PSI on one carrying 2%.
    heavy = drift.ColumnDrift(
        6,
        "a",
        psi=0.30,
        psi_evidently_native=0.30,
        ks_statistic=None,
        ks_pvalue=None,
        verdict=drift.VERDICT_SIGNIFICANT,
        gain_share=0.2847,
    )
    light = drift.ColumnDrift(
        6,
        "b",
        psi=0.30,
        psi_evidently_native=0.30,
        ks_statistic=None,
        ks_pvalue=None,
        verdict=drift.VERDICT_SIGNIFICANT,
        gain_share=0.0215,
    )

    assert heavy.impact > light.impact * 10


def test_gain_shares_fall_back_to_uniform_without_a_booster() -> None:
    # The ranking degrades to plain PSI order rather than failing the report.
    shares = drift.champion_gain_shares(StubClassifier())

    assert set(shares) == set(MODEL_FEATURES)
    assert sum(shares.values()) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Minimum batch size (docs/findings.md §11)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("psi_value", [0.0, 0.05, 0.15, 0.90])
def test_a_small_batch_gets_no_colour_at_all(psi_value: float) -> None:
    # Never green, never red. A small batch does not make drift less likely; it
    # makes the measurement unable to separate drift from PSI's own bias, which
    # reaches the warning threshold at n = 250 on the 26-bin column. Either
    # colour would report that inability as knowledge.
    assert (
        drift.verdict(psi_value, rows=drift.MIN_BATCH_SIZE - 1)
        == drift.VERDICT_INSUFFICIENT_SAMPLE
    )


def test_at_the_minimum_the_normal_bands_apply_again() -> None:
    assert drift.verdict(0.05, rows=drift.MIN_BATCH_SIZE) == drift.VERDICT_STABLE
    assert drift.verdict(0.30, rows=drift.MIN_BATCH_SIZE) == drift.VERDICT_SIGNIFICANT


def test_omitting_the_row_count_keeps_the_old_behaviour() -> None:
    # The guard is opt-in: callers that do not know the batch size still get a
    # verdict, so adding it did not silently change every existing call site.
    assert drift.verdict(0.30) == drift.VERDICT_SIGNIFICANT


def test_the_minimum_matches_the_monitoring_config() -> None:
    # The config is what stage 3 will read; the code must not disagree with it.
    import yaml

    from credit_monitor.constants import PROJECT_ROOT

    config = yaml.safe_load(
        (PROJECT_ROOT / "configs" / "monitoring.yaml").read_text(encoding="utf-8")
    )

    assert config["min_batch_size"] == drift.MIN_BATCH_SIZE
    assert config["psi_warn_threshold"] == drift.WARN_THRESHOLD
    assert config["psi_alert_threshold"] == drift.ALERT_THRESHOLD
    assert config["ks_decides_verdict"] is False


# --------------------------------------------------------------------------
# publish-reports skips id-only differences (day 8, caught by hand)
# --------------------------------------------------------------------------


def test_publish_skips_a_file_that_differs_only_in_generated_ids(
    tmp_path: Path,
) -> None:
    # The day-8 no-op: six files byte-different and content-identical, because
    # Evidently regenerates the JS variable hash and the UUIDv7 widget ids on
    # every run. Committing them would have added ~7 MB of pure churn.
    import sys

    sys.path.insert(0, "scripts")
    from publish_reports import content_changed

    body = (
        '<script>var metric_{hash} = {{...}}</script><div id="{uuid}">PSI 0.9420</div>'
    )
    source = tmp_path / "new.html"
    destination = tmp_path / "old.html"
    source.write_text(
        body.format(hash="a" * 32, uuid="01a0c625-8513-7d5d-aeca-86ac2e8ceca6"),
        encoding="utf-8",
    )
    destination.write_text(
        body.format(hash="b" * 32, uuid="01a0c654-359f-7b37-9296-89271a9e2416"),
        encoding="utf-8",
    )

    assert source.read_bytes() != destination.read_bytes()
    assert content_changed(source, destination) is False


def test_publish_copies_a_file_whose_numbers_changed(tmp_path: Path) -> None:
    # The skip must not be a blanket "never copy html".
    import sys

    sys.path.insert(0, "scripts")
    from publish_reports import content_changed

    source = tmp_path / "new.html"
    destination = tmp_path / "old.html"
    source.write_text(f"<p>metric_{'a' * 32}</p><p>PSI 0.9420</p>", encoding="utf-8")
    destination.write_text(
        f"<p>metric_{'a' * 32}</p><p>PSI 0.6486</p>", encoding="utf-8"
    )

    assert content_changed(source, destination) is True


def test_publish_treats_a_missing_destination_as_changed(tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, "scripts")
    from publish_reports import content_changed

    source = tmp_path / "new.html"
    source.write_text("<p>anything</p>", encoding="utf-8")

    assert content_changed(source, tmp_path / "absent.html") is True
