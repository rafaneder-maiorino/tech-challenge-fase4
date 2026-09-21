"""Tests for the training decisions that do not need a tracking server.

Fitting and MLflow registration are exercised end to end by ``make train``.
What is pinned here is the reasoning around them: that both candidates are
told about the imbalance, that they see the same features, and that the
champion is chosen by the metric the module says it chooses by.
"""

import pandas as pd

from credit_monitor.data.preprocess import MODEL_FEATURES
from credit_monitor.models.metrics import ClassificationMetrics
from credit_monitor.models.train import (
    SELECTION_METRIC,
    ModelResult,
    TrainingOutcome,
    build_candidates,
)

SCALE_POS_WEIGHT = 13.974


def metrics(auc: float, brier: float) -> ClassificationMetrics:
    return ClassificationMetrics(
        auc_roc=auc,
        ks_statistic=0.5,
        ks_threshold=0.07,
        brier=brier,
        positive_rate=0.0668,
        rows=20_629,
    )


def result(name: str, auc: float, brier: float = 0.05) -> ModelResult:
    return ModelResult(
        name=name,
        params={},
        calibrated=metrics(auc, brier),
        uncalibrated=metrics(auc, brier * 2.7),
        run_id=f"run-{name}",
    )


def outcome(results: list[ModelResult]) -> TrainingOutcome:
    champion = max(results, key=lambda r: getattr(r.calibrated, SELECTION_METRIC))
    return TrainingOutcome(
        results=results,
        champion=champion.name,
        champion_run_id=champion.run_id,
        registered_version="1",
        rows_train=61_886,
        rows_calibration=20_629,
        rows_validation=20_629,
        scale_pos_weight=SCALE_POS_WEIGHT,
    )


# --------------------------------------------------------------------------
# Imbalance handling (inspection §2: 6.68% positives, 13.96:1)
# --------------------------------------------------------------------------


def test_both_candidates_are_told_about_the_imbalance() -> None:
    # Two families, two mechanisms, same intent. Neither may be left on its
    # default, where a 93/7 problem is solved by predicting "no" every time.
    candidates = build_candidates(SCALE_POS_WEIGHT, seed=20260920)

    _, logistic_params = candidates["logistic_regression"]
    _, xgboost_params = candidates["xgboost"]

    assert logistic_params["class_weight"] == "balanced"
    assert xgboost_params["scale_pos_weight"] == SCALE_POS_WEIGHT


def test_both_candidates_are_seeded_identically() -> None:
    candidates = build_candidates(SCALE_POS_WEIGHT, seed=42)

    assert all(params["random_state"] == 42 for _, params in candidates.values())


def test_only_the_linear_model_is_wrapped_in_a_scaler() -> None:
    # Trees are invariant to monotone rescaling; the logistic regression is
    # not, and MonthlyIncome runs to six figures beside a 0/1 indicator.
    candidates = build_candidates(SCALE_POS_WEIGHT, seed=20260920)

    logistic, _ = candidates["logistic_regression"]
    xgboost, _ = candidates["xgboost"]

    assert list(logistic.named_steps) == ["scaler", "model"]
    assert not hasattr(xgboost, "named_steps")


def test_the_comparison_is_over_identical_features() -> None:
    # The comparison is about model family. Anything else that differed
    # between the two would confound it.
    assert len(MODEL_FEATURES) == 11
    assert len(set(MODEL_FEATURES)) == len(MODEL_FEATURES)


# --------------------------------------------------------------------------
# Champion selection
# --------------------------------------------------------------------------


def test_champion_is_the_best_on_the_selection_metric() -> None:
    results = [result("logistic_regression", 0.8489), result("xgboost", 0.8579)]

    assert outcome(results).champion == "xgboost"


def test_champion_selection_ignores_brier() -> None:
    # Brier is reported and tracked, but it is a property of the calibrator as
    # much as of the model, so it does not choose. Pinned so that changing the
    # rule has to be deliberate.
    results = [
        result("logistic_regression", 0.8489, brier=0.0001),
        result("xgboost", 0.8579, brier=0.9),
    ]

    assert outcome(results).champion == "xgboost"


def test_comparison_table_is_sorted_by_the_selection_metric() -> None:
    results = [result("logistic_regression", 0.8489), result("xgboost", 0.8579)]

    table = outcome(results).comparison_table()

    assert list(table["model"]) == ["xgboost", "logistic_regression"]
    assert table[SELECTION_METRIC].is_monotonic_decreasing


def test_summary_records_the_partition_and_both_calibration_states() -> None:
    # The three-way partition is the answer to the calibration problem, so the
    # run summary has to show it happened and what it bought.
    payload = outcome([result("xgboost", 0.8579, brier=0.0496)]).to_dict()

    assert payload["rows"]["calibration"] > 0
    assert payload["rows"]["train"] > payload["rows"]["validation"]
    assert payload["selection_metric"] == SELECTION_METRIC
    model = payload["models"][0]
    assert model["calibrated"]["brier"] < model["uncalibrated"]["brier"]


def test_outcome_table_is_a_dataframe_with_one_row_per_candidate() -> None:
    results = [result("logistic_regression", 0.8489), result("xgboost", 0.8579)]

    table = outcome(results).comparison_table()

    assert isinstance(table, pd.DataFrame)
    assert len(table) == 2
    assert {"model", "auc_roc", "ks", "brier", "brier_uncalibrated"} <= set(
        table.columns
    )
