"""Generate, score and measure six months of simulated production drift.

Everything in one deterministic command: the six monthly batches plus the
month-0 control, the four-arm ablation, both dependence-only batches, and
``reports/simulation/summary.md``.

Run as ``uv run python scripts/simulate_production.py`` (or ``make simulate``).
"""

import argparse
import itertools
import logging
from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr

from credit_monitor.constants import (
    PROCESSED_DATA_DIR,
    PRODUCTION_DATA_DIR,
    SIMULATION_REPORTS_DIR,
)
from credit_monitor.data.preprocess import MODEL_FEATURES
from credit_monitor.logging_config import configure_logging
from credit_monitor.models.score import CHAMPION_URI, load_champion
from credit_monitor.reporting import simulation_report
from credit_monitor.simulation.config import DEFAULT_CONFIG_PATH, SimulationConfig
from credit_monitor.simulation.simulate import (
    run_ablation,
    run_multivariate,
    run_simulation,
    write_run_metadata,
)

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/simulate_production.py",
        description="Simula seis meses de drift em produção sobre o holdout.",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--data-dir", type=Path, default=PROCESSED_DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=PRODUCTION_DATA_DIR)
    parser.add_argument("--report-dir", type=Path, default=SIMULATION_REPORTS_DIR)
    return parser


def pair_ranking(
    reference: pd.DataFrame, top: int = 8
) -> list[tuple[str, str, float, float]]:
    """Rank feature pairs by absolute Spearman on the reference."""
    rows = []
    for column_x, column_y in itertools.combinations(MODEL_FEATURES, 2):
        spearman = float(spearmanr(reference[column_x], reference[column_y]).statistic)
        pearson = float(reference[column_x].corr(reference[column_y]))
        rows.append((abs(spearman), column_x, column_y, spearman, pearson))
    rows.sort(reverse=True)
    return [(x, y, s, p) for _, x, y, s, p in rows[:top]]


def print_table(title: str, table: str) -> None:
    print()
    print(title)
    print("=" * len(title))
    print(table)


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()

    config = SimulationConfig.load(args.config)
    reference = pd.read_parquet(args.data_dir / "reference.parquet")
    holdout = pd.read_parquet(args.data_dir / "holdout.parquet")
    model = load_champion()
    log.info(
        "simulate.starting",
        extra={
            "reference_rows": len(reference),
            "holdout_rows": len(holdout),
            "batch_size": config.batch_size,
            "months": config.n_months,
        },
    )

    main_run = run_simulation(holdout, reference, config, model, write_dir=args.out_dir)
    arms = run_ablation(holdout, reference, config, model)

    ranking = pair_ranking(reference)
    multivariate = []
    for pair in config.multivariate.pairs:
        reference_spearman = float(
            spearmanr(reference[pair.column_x], reference[pair.column_y]).statistic
        )
        multivariate.append(
            run_multivariate(
                holdout,
                reference,
                config,
                model,
                pair,
                reference_spearman,
                write_dir=args.out_dir,
            )
        )

    first, last = main_run.months[0].metrics, main_run.months[-1].metrics
    content = simulation_report.render(
        config=config,
        main=main_run,
        arms=arms,
        multivariate=multivariate,
        ranking=ranking,
        extra={
            "pool_rows": len(holdout),
            "champion_uri": CHAMPION_URI,
            "auc_drop": (first.auc_roc - last.auc_roc) / first.auc_roc,
        },
    )
    report_path = simulation_report.write(args.report_dir / "summary.md", content)
    metadata_path = write_run_metadata(
        config, [main_run, *arms], args.out_dir / "run_metadata.json"
    )

    print_table("RAMPA DE PSI", simulation_report.psi_ramp_table(main_run))
    print_table(
        "ALVOS DE CALIBRACAO",
        simulation_report.calibration_check(main_run, config.n_months),
    )
    print_table("METRICAS POR LOTE", simulation_report.metrics_table(main_run))
    print_table(
        "ABLACAO - AUC-ROC", simulation_report.ablation_table(arms, "auc_roc", ".4f")
    )
    print_table(
        "ABLACAO - GAP DE CALIBRACAO",
        simulation_report.ablation_table(arms, "calibration_gap", "+.4f"),
    )
    print_table(
        "ABLACAO - INADIMPLENCIA OBSERVADA",
        simulation_report.ablation_table(arms, "observed_default_rate", ".2%"),
    )
    print_table(
        "CENARIO SO-MULTIVARIADO",
        simulation_report.multivariate_section(multivariate),
    )
    print()
    print(f"Relatorio : {report_path}")
    print(f"Metadados : {metadata_path}")
    print(f"Lotes     : {args.out_dir}/month_XX/")
    print()


if __name__ == "__main__":
    main()
