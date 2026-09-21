"""Tests for the drift simulator.

No MLflow, no real parquet: the pool is a hand-built frame put through the real
preprocessing path, and the model is a stand-in that satisfies the scorer's
protocol. What is tested is the simulator's contract with itself — determinism,
which rows each mechanism is allowed to touch, and the invariants the day-7 and
day-8 analyses will rest on.
"""

import inspect
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from conftest import make_random_frame
from scipy.stats import spearmanr

from credit_monitor.constants import TARGET_COLUMN
from credit_monitor.data.preprocess import (
    INCOME_MISSING_COLUMN,
    MODEL_FEATURES,
    assert_model_ready,
    clean,
    fit_imputation,
    impute,
)
from credit_monitor.models.score import (
    LabelLeakageError,
    ProbabilisticClassifier,
    predict_proba,
)
from credit_monitor.simulation.config import (
    CompositionParams,
    MechanismToggles,
    MultivariateParams,
    PairSpec,
    SimulationConfig,
)
from credit_monitor.simulation.mechanisms import (
    SCORED_COLUMNS,
    apply_inflation,
    apply_stress,
    draw_batch,
)
from credit_monitor.simulation.scores import RankNormalScorer
from credit_monitor.simulation.simulate import (
    multisets_match,
    run_multivariate,
    run_simulation,
)

POOL_ROWS = 900
POOL_POSITIVES = 60  # 6.67%, the rate of inspection §2
BATCH_ROWS = 200


class StubClassifier:
    """A deterministic stand-in for the champion.

    Satisfies :class:`ProbabilisticClassifier` and nothing more, which is the
    point: the simulator must not depend on anything else about the model.
    """

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:  # noqa: N803
        """Logistic in utilisation, so scores vary and AUC is computable."""
        logit = 4.0 * X["RevolvingUtilizationOfUnsecuredLines"].to_numpy() - 2.0
        positive = 1.0 / (1.0 + np.exp(-logit))
        return np.column_stack([1.0 - positive, positive])


@pytest.fixture
def pool() -> pd.DataFrame:
    """A model-ready pool with missing-income rows and one dependent pair.

    ``make_random_frame`` draws every column independently, which is fine for
    contract tests and useless here: the multivariate scenario inverts a
    dependence, and there is nothing to invert in independent columns. So the
    real-estate count is made a noisy function of the open-credit-lines count,
    reproducing the ~0.46 Spearman the reference actually has between those two
    (``docs/simulation.md`` §4).
    """
    raw = make_random_frame(n_rows=POOL_ROWS, n_positive=POOL_POSITIVES)
    raw.loc[0:179, "MonthlyIncome"] = None  # ~20%, as inspection §3 reports
    raw.loc[0:179, "NumberOfDependents"] = None

    rng = np.random.default_rng(POOL_ROWS)
    linked = raw["NumberOfOpenCreditLinesAndLoans"].to_numpy() / 7.0 + rng.normal(
        0.0, 0.5, len(raw)
    )
    raw["NumberRealEstateLoansOrLines"] = np.clip(np.round(linked), 0, 4).astype(
        "uint8"
    )

    cleaned, _ = clean(raw)
    return impute(cleaned, fit_imputation(cleaned))


@pytest.fixture
def config() -> SimulationConfig:
    """A small config with the same structure as the frozen one."""
    return SimulationConfig(
        seed=20260921,
        n_months=6,
        batch_size=BATCH_ROWS,
        composition=CompositionParams(0.9, 0.45, 0.7, 0.6, 0.6, 2, 3),
        pi_total=0.10,
        extra_default_rate_at_full=0.03,
        multivariate=MultivariateParams(
            tolerance=0.05,
            pairs=(
                PairSpec(
                    "pair",
                    "NumberOfOpenCreditLinesAndLoans",
                    "NumberRealEstateLoansOrLines",
                    -0.40,
                ),
            ),
        ),
        mechanisms=MechanismToggles(True, True, True),
        psi_bins=10,
        psi_warn_threshold=0.10,
        psi_alert_threshold=0.25,
    )


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_same_seed_gives_byte_identical_batches(
    pool: pd.DataFrame, config: SimulationConfig, tmp_path: Path
) -> None:
    # Byte-identical, not merely equal: the parquets are the artefact the rest
    # of stage 2 reads, and "reproducible" has to mean the files, not just the
    # numbers computed from them.
    model = StubClassifier()
    first, second = tmp_path / "a", tmp_path / "b"
    run_simulation(pool, pool, config, model, label="a", write_dir=first)
    run_simulation(pool, pool, config, model, label="b", write_dir=second)

    written = sorted(p.relative_to(first) for p in first.rglob("*.parquet"))
    assert written, "the run wrote no batches"
    for relative in written:
        assert (first / relative).read_bytes() == (second / relative).read_bytes()


def test_a_different_seed_gives_different_batches(
    pool: pd.DataFrame, config: SimulationConfig
) -> None:
    from credit_monitor.simulation.config import asdict_shallow

    other = SimulationConfig(**{**asdict_shallow(config), "seed": config.seed + 1})
    model = StubClassifier()

    first = run_simulation(pool, pool, config, model, write_dir=None)
    second = run_simulation(pool, pool, other, model, write_dir=None)

    assert first.months[3].metrics.auc_roc != second.months[3].metrics.auc_roc


# --------------------------------------------------------------------------
# Month 0 is a control batch
# --------------------------------------------------------------------------


def test_month_zero_applies_no_mechanism(
    pool: pd.DataFrame, config: SimulationConfig
) -> None:
    # Without a true control batch there is no way to tell a month-1 signal
    # from sampling noise, so month 0 has to be untouched by construction.
    result = run_simulation(pool, pool, config, StubClassifier(), write_dir=None)
    month_zero = result.months[0]

    assert month_zero.intensity == 0.0
    assert month_zero.inflated_rows == 0
    assert month_zero.label_flips == 0
    assert max(month_zero.psi.values()) < 0.10


def test_month_zero_rows_are_unmodified_pool_rows(
    pool: pd.DataFrame, config: SimulationConfig
) -> None:
    scorer = RankNormalScorer.fit(pool, SCORED_COLUMNS)
    rng = np.random.default_rng(config.seed)

    batch = draw_batch(pool, scorer, config, 0, rng, weighted=True)

    # Every row must appear in the pool exactly as it is: month 0 samples, it
    # does not transform.
    merged = batch.merge(pool, how="left", on=list(batch.columns), indicator=True)
    assert (merged["_merge"] == "both").all()


# --------------------------------------------------------------------------
# Mechanism 2 touches only income-present rows
# --------------------------------------------------------------------------


def test_inflation_touches_only_income_present_rows(pool: pd.DataFrame) -> None:
    # The missing-income group's DebtRatio is in a different unit (inspection
    # §9) and was replaced with the reference median. Dividing that by an
    # income they do not have would be arithmetic on a unit error.
    missing = pool[INCOME_MISSING_COLUMN] == 1
    assert missing.sum() > 0, "the fixture must contain missing-income rows"

    inflated, touched = apply_inflation(pool, pi_total=0.10, intensity=1.0)

    assert touched == int((~missing).sum())
    pd.testing.assert_frame_equal(
        inflated[missing.to_numpy()], pool[missing.to_numpy()]
    )
    present = ~missing.to_numpy()
    assert np.allclose(
        inflated.loc[present, "MonthlyIncome"], pool.loc[present, "MonthlyIncome"] * 1.1
    )
    assert np.allclose(
        inflated.loc[present, "DebtRatio"], pool.loc[present, "DebtRatio"] / 1.1
    )


def test_inflation_leaves_labels_alone(pool: pd.DataFrame) -> None:
    # Concept drift by measurement: the person's real risk is unchanged, only
    # the number the model reads has moved.
    inflated, _ = apply_inflation(pool, pi_total=0.10, intensity=1.0)

    assert (
        inflated[TARGET_COLUMN].astype(str) == pool[TARGET_COLUMN].astype(str)
    ).all()


def test_inflation_is_inert_at_zero_intensity(pool: pd.DataFrame) -> None:
    inflated, _ = apply_inflation(pool, pi_total=0.10, intensity=0.0)

    pd.testing.assert_frame_equal(inflated, pool)


# --------------------------------------------------------------------------
# Mechanism 3 flips one way only
# --------------------------------------------------------------------------


@pytest.mark.parametrize("intensity", [0.5, 1.0])
def test_labels_only_ever_flip_zero_to_one(
    pool: pd.DataFrame, intensity: float
) -> None:
    # A recession makes good borrowers default. It does not retroactively make
    # a defaulter creditworthy, and allowing the reverse would let the
    # mechanism cancel itself out.
    scorer = RankNormalScorer.fit(pool, SCORED_COLUMNS)
    rng = np.random.default_rng(7)

    stressed, flips = apply_stress(pool, scorer, 0.03, intensity, rng)

    before = (pool[TARGET_COLUMN].astype(str) == "1").to_numpy()
    after = (stressed[TARGET_COLUMN].astype(str) == "1").to_numpy()
    assert not (before & ~after).any(), "a positive label was flipped back to 0"
    assert flips == int((~before & after).sum())
    assert flips > 0


def test_stress_hits_the_requested_extra_default_rate(pool: pd.DataFrame) -> None:
    # The scale is solved analytically rather than tuned, so the realised extra
    # rate has to land on the parameter, not near it by luck.
    scorer = RankNormalScorer.fit(pool, SCORED_COLUMNS)
    rng = np.random.default_rng(11)
    before = float((pool[TARGET_COLUMN].astype(str) == "1").mean())

    stressed, _ = apply_stress(pool, scorer, 0.03, 1.0, rng)
    after = float((stressed[TARGET_COLUMN].astype(str) == "1").mean())

    assert after - before == pytest.approx(0.03, abs=0.015)


def test_stress_is_inert_at_zero_intensity(pool: pd.DataFrame) -> None:
    scorer = RankNormalScorer.fit(pool, SCORED_COLUMNS)

    stressed, flips = apply_stress(pool, scorer, 0.03, 0.0, np.random.default_rng(1))

    assert flips == 0
    pd.testing.assert_frame_equal(stressed, pool)


# --------------------------------------------------------------------------
# The multivariate batch preserves every marginal
# --------------------------------------------------------------------------


def test_multivariate_batch_preserves_every_column_multiset(
    pool: pd.DataFrame, config: SimulationConfig
) -> None:
    # The assertion the whole dependence-only argument rests on. If one
    # marginal moved, a univariate test could in principle catch the drift and
    # the batch would stop proving that univariate monitoring is blind to it.
    pair = config.multivariate.primary
    reference_spearman = float(
        spearmanr(pool[pair.column_x], pool[pair.column_y]).statistic
    )

    result = run_multivariate(
        pool, pool, config, StubClassifier(), pair, reference_spearman, write_dir=None
    )

    assert result.marginals_preserved
    # Univariate monitoring stays green: every PSI below the 0.10 warning
    # band. The bound is the warning threshold rather than something tighter
    # because a 200-row batch carries real sampling noise -- the 7,368-row
    # batches of the actual run come in at 0.004.
    assert max(result.psi_after.values()) < 0.10


def test_multivariate_batch_actually_inverts_the_dependence(
    pool: pd.DataFrame, config: SimulationConfig
) -> None:
    pair = config.multivariate.primary
    reference_spearman = float(
        spearmanr(pool[pair.column_x], pool[pair.column_y]).statistic
    )

    result = run_multivariate(
        pool, pool, config, StubClassifier(), pair, reference_spearman, write_dir=None
    )

    assert result.dependence.before_spearman > 0.2
    assert result.dependence.after_spearman < 0
    assert result.dependence.after_spearman == pytest.approx(
        pair.target_spearman, abs=config.multivariate.tolerance
    )


def test_multisets_match_detects_a_changed_marginal(pool: pd.DataFrame) -> None:
    # The invariant checker has to be capable of failing, or it is decoration.
    altered = pool.copy()
    altered.loc[0, "age"] = altered.loc[0, "age"] + 1

    assert multisets_match(pool, pool)
    assert not multisets_match(pool, altered)


# --------------------------------------------------------------------------
# Every batch clears the contract's blockers
# --------------------------------------------------------------------------


def test_every_batch_passes_the_contract_blockers(
    pool: pd.DataFrame, config: SimulationConfig, tmp_path: Path
) -> None:
    # run_simulation calls assert_model_ready on every month, so completing is
    # itself the assertion; re-validating the written features is what proves
    # the files on disk inherited it. Warning-level rules are expected to fire
    # from month 1 on — the positive-rate band is 5-9% and the scenario drives
    # the default rate well past it, which is the scenario working.
    run_simulation(pool, pool, config, StubClassifier(), write_dir=tmp_path)

    for month in range(config.n_months + 1):
        directory = tmp_path / f"month_{month:02d}"
        features = pd.read_parquet(directory / "features.parquet")
        labels = pd.read_parquet(directory / "labels.parquet")
        rebuilt = features.drop(columns="row_id").assign(
            **{
                TARGET_COLUMN: pd.Categorical(
                    labels[TARGET_COLUMN].astype(str),
                    categories=["0", "1"],
                    ordered=True,
                )
            }
        )
        assert_model_ready(rebuilt)


def test_the_three_files_line_up_on_row_id(
    pool: pd.DataFrame, config: SimulationConfig, tmp_path: Path
) -> None:
    run_simulation(pool, pool, config, StubClassifier(), write_dir=tmp_path)
    directory = tmp_path / "month_03"

    features = pd.read_parquet(directory / "features.parquet")
    predictions = pd.read_parquet(directory / "predictions.parquet")
    labels = pd.read_parquet(directory / "labels.parquet")

    assert len(features) == len(predictions) == len(labels) == config.batch_size
    assert (
        list(features["row_id"])
        == list(predictions["row_id"])
        == list(labels["row_id"])
    )
    # The features file must not carry the outcome: it is what monitoring sees
    # at scoring time, and the outcome does not exist yet.
    assert TARGET_COLUMN not in features.columns
    assert set(features.columns) == {"row_id", *MODEL_FEATURES}


# --------------------------------------------------------------------------
# The scorer cannot be handed a label
# --------------------------------------------------------------------------


def test_the_scoring_signature_has_no_label_parameter() -> None:
    # Enforced on the signature, not by convention. A monitor that *can* touch
    # the label at scoring time will, in some future revision, measure its own
    # degradation with information it would not have had.
    parameters = set(inspect.signature(predict_proba).parameters)

    assert parameters == {"model", "features"}
    assert not parameters & {"labels", "y", "y_true", TARGET_COLUMN}


def test_scoring_rejects_a_frame_carrying_the_target(pool: pd.DataFrame) -> None:
    with pytest.raises(LabelLeakageError, match=TARGET_COLUMN):
        predict_proba(StubClassifier(), pool)


def test_scoring_rejects_a_frame_missing_a_feature(pool: pd.DataFrame) -> None:
    features = pool[list(MODEL_FEATURES)].drop(columns="DebtRatio")

    with pytest.raises(KeyError, match="DebtRatio"):
        predict_proba(StubClassifier(), features)


def test_the_stub_satisfies_the_scorer_protocol() -> None:
    # If this ever fails, the protocol has grown a requirement and the
    # simulator has quietly started depending on more than predict_proba.
    assert isinstance(StubClassifier(), ProbabilisticClassifier)
