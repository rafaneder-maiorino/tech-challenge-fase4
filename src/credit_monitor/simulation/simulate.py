"""Orchestration: six months of batches, scored, measured and written to disk.

The order the mechanisms are applied in follows the DAG of
``docs/simulation.md``: composition first (who is in the portfolio), then
inflation (what the model measures about them), then stress (what actually
happens to them). Reversing any pair would change the result — the stress
mechanism reads utilisation ranks of the *sampled* customers, and the inflation
mechanism must not see labels that the stress mechanism has already moved.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from credit_monitor.constants import PRODUCTION_DATA_DIR, TARGET_COLUMN
from credit_monitor.contracts.raw import POSITIVE_LABEL
from credit_monitor.data.preprocess import MODEL_FEATURES, assert_model_ready
from credit_monitor.models.score import (
    BatchMetrics,
    ProbabilisticClassifier,
    measure,
    predict_proba,
)
from credit_monitor.simulation.config import (
    MechanismToggles,
    PairSpec,
    SimulationConfig,
)
from credit_monitor.simulation.mechanisms import (
    SCORED_COLUMNS,
    DependenceResult,
    apply_inflation,
    apply_stress,
    draw_batch,
    invert_dependence,
)
from credit_monitor.simulation.psi import psi_table
from credit_monitor.simulation.scores import RankNormalScorer

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MonthResult:
    """One month: what was done to it, and what it measured."""

    month: int
    intensity: float
    rows: int
    inflated_rows: int
    label_flips: int
    metrics: BatchMetrics
    psi: dict[str, float] = field(default_factory=dict)
    contract_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Render for the report and the JSON summary."""
        return {
            "month": self.month,
            "intensity": round(self.intensity, 4),
            "rows": self.rows,
            "inflated_rows": self.inflated_rows,
            "label_flips": self.label_flips,
            **self.metrics.to_dict(),
            "psi": {k: round(v, 4) for k, v in self.psi.items()},
        }


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """The whole run."""

    label: str
    toggles: MechanismToggles
    months: list[MonthResult]

    def metrics_frame(self) -> pd.DataFrame:
        """Per-month metrics as a table."""
        return pd.DataFrame([month.metrics.to_dict() for month in self.months])

    def psi_frame(self) -> pd.DataFrame:
        """PSI per feature per month, months as rows."""
        return pd.DataFrame(
            {month.month: month.psi for month in self.months}
        ).T.rename_axis("month")


def write_batch(
    frame: pd.DataFrame, probabilities: np.ndarray, directory: Path
) -> None:
    """Write the three files a real pipeline would see at three different times.

    ``row_id`` is the join key and is written in all three, because the whole
    point of separating them is that they are reunited later — by a process
    that has to be able to fail if the keys do not line up.
    """
    directory.mkdir(parents=True, exist_ok=True)
    row_id = pd.RangeIndex(len(frame), name="row_id")

    features = frame[list(MODEL_FEATURES)].copy()
    features.insert(0, "row_id", row_id)
    features.to_parquet(directory / "features.parquet", index=False)

    pd.DataFrame({"row_id": row_id, "probability": probabilities}).to_parquet(
        directory / "predictions.parquet", index=False
    )

    pd.DataFrame(
        {
            "row_id": row_id,
            TARGET_COLUMN: frame[TARGET_COLUMN].astype(str).to_numpy(),
        }
    ).to_parquet(directory / "labels.parquet", index=False)


def simulate_month(
    month: int,
    pool: pd.DataFrame,
    reference: pd.DataFrame,
    scorer: RankNormalScorer,
    config: SimulationConfig,
    toggles: MechanismToggles,
    model: ProbabilisticClassifier,
    rng: np.random.Generator,
    write_dir: Path | None,
) -> MonthResult:
    """Generate, validate, score and measure one monthly batch.

    Args:
        month: 0 is the control batch, with every mechanism inert.
        pool: The holdout.
        reference: The baseline, for PSI and for the rank scorer.
        scorer: Rank scorer fitted on the reference.
        config: Frozen parameters.
        toggles: Which mechanisms are active.
        model: The champion.
        rng: The generator.
        write_dir: Where to write the three files, or ``None`` for the ablation.

    Returns:
        The month's result.
    """
    intensity = config.intensity(month)
    batch = draw_batch(pool, scorer, config, month, rng, toggles.composition)

    inflated_rows = 0
    if toggles.inflation and month > 0:
        batch, inflated_rows = apply_inflation(batch, config.pi_total, intensity)

    flips = 0
    if toggles.stress and month > 0:
        batch, flips = apply_stress(
            batch, scorer, config.extra_default_rate_at_full, intensity, rng
        )

    # Blockers stop the simulation; warnings are logged. A batch that violated
    # a blocker would be an impossible customer, which means the simulator is
    # broken rather than the population having drifted.
    assert_model_ready(batch)

    features = batch[list(MODEL_FEATURES)]
    probabilities = predict_proba(model, features)
    labels = (batch[TARGET_COLUMN].astype(str) == POSITIVE_LABEL).to_numpy(dtype=int)
    metrics = measure(month, labels, probabilities)

    if write_dir is not None:
        write_batch(batch, probabilities, write_dir / f"month_{month:02d}")

    result = MonthResult(
        month=month,
        intensity=intensity,
        rows=len(batch),
        inflated_rows=inflated_rows,
        label_flips=flips,
        metrics=metrics,
        psi=psi_table(reference, batch, MODEL_FEATURES, config.psi_bins),
    )
    log.info("simulate.month_done", extra=result.to_dict())
    return result


def run_simulation(
    pool: pd.DataFrame,
    reference: pd.DataFrame,
    config: SimulationConfig,
    model: ProbabilisticClassifier,
    toggles: MechanismToggles | None = None,
    label: str = "all",
    write_dir: Path | None = PRODUCTION_DATA_DIR,
) -> SimulationResult:
    """Run months 0..n with one mechanism configuration.

    The generator is seeded once per run rather than once per month, so the
    months are not independent draws of the same randomness — but the whole
    sequence is reproducible, which is what the determinism test checks. Every
    ablation arm gets the same seed, so arms differ only by which mechanisms
    are on.

    Args:
        pool: The holdout.
        reference: The baseline.
        config: Frozen parameters.
        model: The champion.
        toggles: Defaults to the config's.
        label: Name for this arm, used in the ablation table.
        write_dir: Where to write batches, or ``None`` to measure only.

    Returns:
        The run.
    """
    active = toggles or config.mechanisms
    scorer = RankNormalScorer.fit(reference, SCORED_COLUMNS)
    rng = np.random.default_rng(config.seed)
    months = [
        simulate_month(
            month,
            pool,
            reference,
            scorer,
            config,
            active,
            model,
            rng,
            write_dir,
        )
        for month in range(config.n_months + 1)
    ]
    return SimulationResult(label=label, toggles=active, months=months)


def run_ablation(
    pool: pd.DataFrame,
    reference: pd.DataFrame,
    config: SimulationConfig,
    model: ProbabilisticClassifier,
) -> list[SimulationResult]:
    """Rerun months 0..n once per mechanism configuration, same seed.

    This is the cheap-now-expensive-later artefact. Because we own the
    data-generating process, switching a mechanism off is an **intervention**,
    not a comparison of observed subgroups — see ``docs/simulation.md`` §5. In
    production the table cannot be built at all: only the total is observable,
    and attribution is argument rather than measurement.
    """
    arms = [
        ("all", config.mechanisms),
        ("composition_only", config.mechanisms.only("composition")),
        ("inflation_only", config.mechanisms.only("inflation")),
        ("stress_only", config.mechanisms.only("stress")),
    ]
    return [
        run_simulation(pool, reference, config, model, toggles, label, write_dir=None)
        for label, toggles in arms
    ]


@dataclass(frozen=True, slots=True)
class MultivariateResult:
    """The dependence-only batch: same marginals, inverted dependence."""

    label: str
    dependence: DependenceResult
    metrics_before: BatchMetrics
    metrics_after: BatchMetrics
    psi_before: dict[str, float]
    psi_after: dict[str, float]
    max_psi_after: float
    marginals_preserved: bool

    def to_dict(self) -> dict[str, Any]:
        """Render for the report."""
        return {
            "label": self.label,
            "dependence": asdict(self.dependence),
            "metrics_before": self.metrics_before.to_dict(),
            "metrics_after": self.metrics_after.to_dict(),
            "max_psi_after": round(self.max_psi_after, 5),
            "marginals_preserved": self.marginals_preserved,
        }


def multisets_match(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    """Whether every column holds exactly the same values, order aside.

    The assertion the whole multivariate scenario rests on. If a single
    marginal moved, a univariate test could in principle catch the drift and
    the batch would no longer prove that univariate monitoring is blind to it.
    """
    for column in left.columns:
        a = np.sort(left[column].astype(str).to_numpy())
        b = np.sort(right[column].astype(str).to_numpy())
        if not np.array_equal(a, b):
            return False
    return True


def run_multivariate(
    pool: pd.DataFrame,
    reference: pd.DataFrame,
    config: SimulationConfig,
    model: ProbabilisticClassifier,
    pair: PairSpec,
    reference_spearman: float,
    write_dir: Path | None = PRODUCTION_DATA_DIR,
) -> MultivariateResult:
    """Build and score the dependence-only batch.

    Starts from a month-0 style uniform sample, so the only difference from the
    control batch is the dependence between one pair of columns.
    """
    scorer = RankNormalScorer.fit(reference, SCORED_COLUMNS)
    rng = np.random.default_rng(config.seed)
    base = draw_batch(pool, scorer, config, 0, rng, weighted=False)

    permuted, dependence = invert_dependence(
        base,
        pair.column_x,
        pair.column_y,
        pair.target_spearman,
        rng,
        reference_spearman,
        config.multivariate.tolerance,
    )
    assert_model_ready(permuted)

    labels_before = (base[TARGET_COLUMN].astype(str) == POSITIVE_LABEL).to_numpy(int)
    labels_after = (permuted[TARGET_COLUMN].astype(str) == POSITIVE_LABEL).to_numpy(int)
    before = measure(0, labels_before, predict_proba(model, base[list(MODEL_FEATURES)]))
    after = measure(
        0, labels_after, predict_proba(model, permuted[list(MODEL_FEATURES)])
    )

    psi_after = psi_table(reference, permuted, MODEL_FEATURES, config.psi_bins)
    if write_dir is not None:
        write_batch(
            permuted,
            predict_proba(model, permuted[list(MODEL_FEATURES)]),
            write_dir / f"multivariate_{pair.label}",
        )

    return MultivariateResult(
        label=pair.label,
        dependence=dependence,
        metrics_before=before,
        metrics_after=after,
        psi_before=psi_table(reference, base, MODEL_FEATURES, config.psi_bins),
        psi_after=psi_after,
        max_psi_after=max(psi_after.values()),
        marginals_preserved=multisets_match(base, permuted),
    )


def write_run_metadata(
    config: SimulationConfig, results: list[SimulationResult], path: Path
) -> Path:
    """Persist the frozen parameters and every arm's numbers as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": config.to_dict(),
        "arms": {
            result.label: [month.to_dict() for month in result.months]
            for result in results
        },
    }
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return path
