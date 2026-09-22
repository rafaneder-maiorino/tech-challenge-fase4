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
from credit_monitor.monitoring import blind_window
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
    # Per scenario and month, for the blind-window measurement at the end.
    gaps: dict[str, dict[int, float]] = {}
    alarms: dict[str, dict[int, bool]] = {}

    def score_month(
        batch_id: str, scenario: str, directory: Path, labels_pending: bool = True
    ) -> str:
        """Scoring-time metrics: available the day the batch is scored."""
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
            labels_pending=labels_pending,
        )
        pushed.append((scenario, batch_id, outcome.verdict_label))
        worst = (
            max(outcome.metrics.psi_by_feature.values())
            if outcome.metrics.psi_by_feature
            else float("nan")
        )
        print(
            f"  scoring  {scenario:18} {batch_id:14} "
            f"veredito={outcome.verdict_label:20} psi_max={worst:.3f} "
            f"quarentena={outcome.metrics.rows_quarantined}"
        )
        return outcome.verdict_label

    def deliver_labels(batch_id: str, scenario: str, directory: Path, lag: int) -> None:
        """Label-time metrics: only published once the outcome would exist.

        A blocked batch never gets here. Its rows were rejected, so measuring
        the model's performance on them would be scoring a decision the
        pipeline refused to make.
        """
        features = pd.read_parquet(directory / "features.parquet")
        label_metrics = run_label_arrival(
            batch_id=batch_id,
            scenario=scenario,
            features=features,
            labels=pd.read_parquet(directory / "labels.parquet"),
            predictions=pd.read_parquet(directory / "predictions.parquet"),
            model=model,
            label_lag_months=lag,
            gateway=args.gateway,
        )
        print(
            f"  rótulos  {scenario:18} {batch_id:14} "
            f"auc={label_metrics.auc:.4f} gap={label_metrics.calibration_gap:+.4f} "
            f"atraso={lag}m"
        )

    print()
    print("EMPURRANDO METRICAS")
    print("===================")
    simulation = SimulationConfig.load()
    lag = config.label_lag_months
    directories = {
        scenario: ensure_scenario(scenario, args.batches_dir, args.data_dir, model)
        for scenario in SCENARIOS
    }

    # The replay walks STEPS, not months. At step t the monitor sees the
    # scoring metrics for month t and the labels for month t - lag. That is
    # what the Pushgateway's refusal of client timestamps forces anyway, and
    # it is also simply true: the outcome has not happened yet.
    for step in range(simulation.n_months + 1):
        print(f"\n-- passo {step} " + "-" * 50)
        for scenario in SCENARIOS:
            verdict = score_month(
                f"month_{step:02d}",
                scenario,
                directories[scenario] / f"month_{step:02d}",
            )
            alarms.setdefault(scenario, {})[step] = verdict in {"warning", "critical"}
            labelled = step - lag
            if labelled >= 0:
                deliver_labels(
                    f"month_{labelled:02d}",
                    scenario,
                    directories[scenario] / f"month_{labelled:02d}",
                    lag,
                )
        if args.pause:
            time.sleep(args.pause)

    # Gaps for every month, computed whether or not the labels were published.
    # The blind window is about what the MONITOR could see; the degradation is
    # a fact about the data either way.
    for scenario in SCENARIOS:
        gaps[scenario] = {}
        for month in range(simulation.n_months + 1):
            directory = directories[scenario] / f"month_{month:02d}"
            labels = pd.read_parquet(directory / "labels.parquet")
            predictions = pd.read_parquet(directory / "predictions.parquet")
            truth = (labels[TARGET_COLUMN].astype(str) == "1").to_numpy(dtype=float)
            gaps[scenario][month] = float(
                predictions[PREDICTION_COLUMN].mean() - truth.mean()
            )

    # --- the two edge cases ----------------------------------------------
    print()
    small = make_small_demo(
        args.data_dir, args.batches_dir, args.small_demo_rows, model
    )
    score_month(SMALL_DEMO, "full", small, labels_pending=False)
    deliver_labels(SMALL_DEMO, "full", small, lag=0)

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
    pushed.append(("full", DIRTY, outcome.verdict_label))
    print(
        f"  scoring  {'full':18} {DIRTY:14} veredito={outcome.verdict_label:20} "
        f"estagios={outcome.metrics.stage_status} "
        f"quarentena={outcome.metrics.rows_quarantined}"
    )
    # No label metrics for a blocked batch, deliberately: the rows were
    # rejected, so measuring the model's performance on them would be grading
    # a decision the pipeline refused to make.
    if outcome.verdict_label == "blocked":
        print(f"  rótulos  {'full':18} {DIRTY:14} NÃO publicados (lote bloqueado)")

    # --- the blind window -------------------------------------------------
    print()
    print("LEAD TIME POR CENARIO (positivo = aviso antecipado, negativo = cego)")
    print("=" * 68)
    print(
        f"atraso de rótulo = {lag} meses | "
        f"degradação real = gap <= {config.degradation_gap_threshold}"
    )
    print()
    print(
        f"{'cenário':18} {'1º degradado':>13} {'1º alarme drift':>16} "
        f"{'1º sinal':>9} {'lead (meses)':>13}"
    )
    windows = []
    for scenario in SCENARIOS:
        window = blind_window.compute(
            scenario,
            gaps[scenario],
            alarms.get(scenario, {}),
            lag,
            config.degradation_gap_threshold,
        )
        windows.append(window)
        lead = window.lead_time_months
        mark = (
            "  aviso antecipado"
            if lead is not None and lead > 0
            else ("  CEGO" if lead is not None and lead < 0 else "")
        )
        rendered = f"{lead:+d}" if lead is not None else "n/a"
        print(
            f"{scenario:18} {window.first_degraded_month!s:>13} "
            f"{window.first_drift_alarm_month!s:>16} "
            f"{window.first_signal_month!s:>9} {rendered:>13}{mark}"
        )
    measured = [w for w in windows if w.lead_time_months is not None]
    if measured:
        worst = min(measured, key=lambda w: w.lead_time_months or 0)
        print()
        print(
            f"Pior caso: `{worst.scenario}` com lead de "
            f"{worst.lead_time_months:+d} meses — degradando desde o mês "
            f"{worst.first_degraded_month} e sem sinal nenhum até o "
            f"{worst.first_signal_month}."
        )

    print()
    print(f"{len(pushed)} grupos empurrados para {args.gateway}")
    print("Chave de agrupamento: {job, scenario, batch_id}")
    print()


if __name__ == "__main__":
    main()
