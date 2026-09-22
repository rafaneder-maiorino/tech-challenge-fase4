"""Push monitoring metrics for every batch, scenario and the two edge cases.

Two modes:

``--pause 20`` (``make monitor-replay``)
    Month by month with a pause, so a dashboard can be watched turning from
    green to red while it happens.

``--pause 0`` (``make monitor-all``)
    Everything at once.

Stale groups are deleted before each push: the Pushgateway keeps whatever it
was last told until something removes it, so a rerun with one fewer violated
rule would leave the old label behind and the dashboard would show a violation
that no longer exists.

Run as ``uv run python scripts/monitor_push.py`` (or the make targets).
"""

import argparse
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from credit_monitor.constants import (
    DIRTY_DATA_DIR,
    PROCESSED_DATA_DIR,
    PRODUCTION_DATA_DIR,
    TARGET_COLUMN,
)
from credit_monitor.data.preprocess import INCOME_MISSING_COLUMN, MODEL_FEATURES
from credit_monitor.logging_config import configure_logging
from credit_monitor.models.score import load_champion
from credit_monitor.monitoring import metrics as m
from credit_monitor.monitoring.config import MonitoringConfig
from credit_monitor.monitoring.run import (
    champion_context,
    run_label_arrival,
    run_scoring,
)
from credit_monitor.reporting.drift import PREDICTION_COLUMN
from credit_monitor.simulation.config import SimulationConfig
from credit_monitor.simulation.simulate import run_simulation

log = logging.getLogger(__name__)

# The stage-2 ablation arms, so day 10 can put the 2x2 on one screen.
SCENARIOS = ("full", "composition_only", "stress_only")
SMALL_DEMO = "small_demo"
DIRTY = "dirty_batch"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python scripts/monitor_push.py")
    parser.add_argument("--gateway", default=m.DEFAULT_GATEWAY)
    parser.add_argument("--pause", type=float, default=0.0)
    parser.add_argument("--data-dir", type=Path, default=PROCESSED_DATA_DIR)
    parser.add_argument("--batches-dir", type=Path, default=PRODUCTION_DATA_DIR)
    parser.add_argument("--small-demo-rows", type=int, default=400)
    return parser


def scenario_dir(batches_dir: Path, scenario: str) -> Path:
    """Where a scenario's monthly batches live."""
    if scenario == "full":
        return batches_dir
    return batches_dir / f"ablation_{scenario}"


def ensure_scenario(
    scenario: str, batches_dir: Path, data_dir: Path, model: object
) -> Path:
    """Generate an ablation arm's batches if they are not on disk yet."""
    directory = scenario_dir(batches_dir, scenario)
    if (directory / "month_00" / "features.parquet").exists():
        return directory
    config = SimulationConfig.load()
    reference = pd.read_parquet(data_dir / "reference.parquet")
    holdout = pd.read_parquet(data_dir / "holdout.parquet")
    arm = scenario.removesuffix("_only")
    run_simulation(
        holdout,
        reference,
        config,
        model,  # type: ignore[arg-type]
        toggles=config.mechanisms.only(arm),
        label=scenario,
        write_dir=directory,
    )
    return directory


def make_small_demo(
    data_dir: Path, batches_dir: Path, rows: int, model: object
) -> Path:
    """A deliberately small batch, to exercise INSUFFICIENT_SAMPLE end to end.

    The stage-2 A/A measured PSI's small-sample bias reaching the warning
    threshold at n = 250, which is why the floor exists. A state that is never
    exercised is a state nobody knows works, so the demo batch is below it on
    purpose.
    """
    directory = batches_dir / SMALL_DEMO
    if (directory / "features.parquet").exists():
        return directory
    from credit_monitor.simulation.simulate import write_batch

    holdout = pd.read_parquet(data_dir / "holdout.parquet")
    rng = np.random.default_rng(20260922)
    batch = holdout.iloc[
        rng.choice(len(holdout), size=rows, replace=False)
    ].reset_index(drop=True)
    from credit_monitor.models.score import predict_proba

    write_batch(batch, predict_proba(model, batch[list(MODEL_FEATURES)]), directory)  # type: ignore[arg-type]
    return directory


def prepare_dirty(model: object) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """The day-3 dirty batch, made scoreable without hiding its defects.

    The batch is raw: it never went through preprocessing, so it has no
    ``income_missing`` column and the model cannot consume it. The flag is
    derived and the *null* incomes are filled so scoring can run — but the
    **negative** incomes, the 96/98 codes, the age of 12, the orphan
    dependants-nulls and the int64 age dtype are all left exactly as delivered.
    Those are what the contract has to catch, and a pipeline that cleaned them
    to make its own life easier would be reporting a batch that never existed.
    """
    from credit_monitor.models.score import predict_proba

    candidates = sorted(DIRTY_DATA_DIR.glob("batch_*.parquet"))
    if not candidates:
        raise SystemExit(
            "nenhum lote sujo em data/dirty — rode `make validate-bad-batch`."
        )
    raw = pd.read_parquet(candidates[-1])
    income = raw["MonthlyIncome"]
    frame = raw.copy()
    frame[INCOME_MISSING_COLUMN] = (
        (income.isna() | (income == 0)).fillna(False).astype("int8")
    )
    frame["MonthlyIncome"] = income.fillna(float(income[income > 0].median()))
    labels = pd.DataFrame(
        {
            "row_id": range(len(frame)),
            TARGET_COLUMN: frame[TARGET_COLUMN].astype(str).to_numpy(),
        }
    )
    probabilities = predict_proba(model, frame[list(MODEL_FEATURES)])  # type: ignore[arg-type]
    predictions = pd.DataFrame(
        {"row_id": range(len(frame)), PREDICTION_COLUMN: probabilities}
    )
    return frame.drop(columns=[TARGET_COLUMN]), labels, predictions


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()

    config = MonitoringConfig.load()
    reference = pd.read_parquet(args.data_dir / "reference.parquet")
    model = load_champion()
    gain_shares, champion_version = champion_context(model)
    reference_features = reference[list(MODEL_FEATURES)]

    pushed: list[tuple[str, str, str]] = []

    def scoring_and_labels(
        batch_id: str, scenario: str, directory: Path, lag: int
    ) -> None:
        features = pd.read_parquet(directory / "features.parquet")
        outcome = run_scoring(
            batch_id=batch_id,
            scenario=scenario,
            features=features,
            reference=reference_features,
            model=model,
            config=config,
            gain_shares=gain_shares,
            champion_version=champion_version,
            gateway=args.gateway,
        )
        labels = pd.read_parquet(directory / "labels.parquet")
        predictions = pd.read_parquet(directory / "predictions.parquet")
        run_label_arrival(
            batch_id=batch_id,
            scenario=scenario,
            features=features,
            labels=labels,
            predictions=predictions,
            model=model,
            label_lag_months=lag,
            gateway=args.gateway,
        )
        pushed.append((scenario, batch_id, outcome.verdict_label))
        print(
            f"  {scenario:18} {batch_id:14} veredito={outcome.verdict_label:20} "
            f"psi_max={max(outcome.metrics.psi_by_feature.values()):.3f} "
            f"quarentena={outcome.metrics.rows_quarantined}"
        )
        if args.pause:
            time.sleep(args.pause)

    print()
    print("EMPURRANDO METRICAS")
    print("===================")
    simulation = SimulationConfig.load()
    for scenario in SCENARIOS:
        directory = ensure_scenario(scenario, args.batches_dir, args.data_dir, model)
        for month in range(simulation.n_months + 1):
            scoring_and_labels(
                f"month_{month:02d}",
                scenario,
                directory / f"month_{month:02d}",
                lag=simulation.n_months - month,
            )

    # --- the two edge cases ----------------------------------------------
    small = make_small_demo(
        args.data_dir, args.batches_dir, args.small_demo_rows, model
    )
    scoring_and_labels(SMALL_DEMO, "full", small, lag=0)

    features, labels, predictions = prepare_dirty(model)
    outcome = run_scoring(
        batch_id=DIRTY,
        scenario="full",
        features=features,
        reference=reference_features,
        model=model,
        config=config,
        gain_shares=gain_shares,
        champion_version=champion_version,
        gateway=args.gateway,
    )
    run_label_arrival(
        batch_id=DIRTY,
        scenario="full",
        features=features,
        labels=labels,
        predictions=predictions,
        model=model,
        label_lag_months=0,
        gateway=args.gateway,
    )
    pushed.append(("full", DIRTY, outcome.verdict_label))
    print(
        f"  {'full':18} {DIRTY:14} veredito={outcome.verdict_label:20} "
        f"estagios_falhos={outcome.stage_failures} "
        f"quarentena={outcome.metrics.rows_quarantined}"
    )

    print()
    print(f"{len(pushed)} grupos empurrados para {args.gateway}")
    print("Chave de agrupamento: {job, scenario, batch_id}")
    print()


if __name__ == "__main__":
    main()
