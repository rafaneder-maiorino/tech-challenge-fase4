"""Generate the Evidently drift reports for every simulated month.

Two reports per month — scoring-time (features + score, no labels) and
label-time (labels joined back, classification quality) — plus the pair over
the ``stress_only`` ablation arm at month 6, which is the 2x2 of stage 2 turned
into two files you can open side by side.

Also writes ``reports/evidently/summary.md`` with the feature x month table,
the cross-check against our own PSI, the KS statistics and the ranking by
drift x importance.

Run as ``uv run python scripts/drift_reports.py`` (or ``make drift-reports``).
"""

import argparse
import datetime as dt
import logging
from pathlib import Path

import pandas as pd

from credit_monitor.constants import (
    PROCESSED_DATA_DIR,
    PRODUCTION_DATA_DIR,
    REPORTS_DIR,
    TARGET_COLUMN,
)
from credit_monitor.data.preprocess import MODEL_FEATURES
from credit_monitor.logging_config import configure_logging
from credit_monitor.models.score import load_champion, predict_proba
from credit_monitor.reporting import drift, drift_summary
from credit_monitor.reporting.drift_summary import CURATED_REPORTS, ReportLink
from credit_monitor.simulation.config import SimulationConfig
from credit_monitor.simulation.simulate import run_simulation

log = logging.getLogger(__name__)

EVIDENTLY_DIR = REPORTS_DIR / "evidently"

# Generation writes HERE, never into the tracked directory. Evidently names the
# report's JavaScript variable with a fresh random UUID on every run, so two
# generations of identical data differ in ~2,300 byte positions across a 4 MB
# file. Writing straight into the tracked path would mean every routine
# regeneration dirties the tree with six 4 MB files that contain the same
# numbers, and one careless `git commit -a` puts that in history forever.
# `make publish-reports` is the only command that touches tracked HTML.
BUILD_DIR = EVIDENTLY_DIR / "_build"

STRESS_DIR = PRODUCTION_DATA_DIR / "ablation_stress_only"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/drift_reports.py",
        description="Gera os relatórios de drift do Evidently para cada mês.",
    )
    parser.add_argument("--data-dir", type=Path, default=PROCESSED_DATA_DIR)
    parser.add_argument("--batches-dir", type=Path, default=PRODUCTION_DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=BUILD_DIR)
    return parser


def load_batch(directory: Path) -> pd.DataFrame:
    """Rejoin the three files a real pipeline would receive separately."""
    features = pd.read_parquet(directory / "features.parquet")
    predictions = pd.read_parquet(directory / "predictions.parquet")
    labels = pd.read_parquet(directory / "labels.parquet")
    if not (
        features["row_id"].equals(predictions["row_id"])
        and features["row_id"].equals(labels["row_id"])
    ):
        raise ValueError(f"row_id mismatch across the three files in {directory}")
    return drift.attach_scores(
        features, predictions["probability"].to_numpy(), labels[TARGET_COLUMN]
    )


def print_table(title: str, table: str) -> None:
    print()
    print(title)
    print("=" * len(title))
    print(table)


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    config = SimulationConfig.load()
    reference = pd.read_parquet(args.data_dir / "reference.parquet")
    holdout = pd.read_parquet(args.data_dir / "holdout.parquet")
    model = load_champion()
    gain = drift.champion_gain_shares(model)

    reference_scored = drift.attach_scores(
        reference,
        predict_proba(model, reference[list(MODEL_FEATURES)]),
        reference[TARGET_COLUMN],
    )

    rows: list[drift.ColumnDrift] = []
    for month in range(config.n_months + 1):
        batch = load_batch(args.batches_dir / f"month_{month:02d}")
        drift.save_html(
            drift.scoring_report(reference_scored, batch),
            args.out_dir / f"scoring_month_{month:02d}.html",
        )
        drift.save_html(
            drift.label_report(reference_scored, batch),
            args.out_dir / f"label_month_{month:02d}.html",
        )
        rows.extend(drift.drift_table(month, reference_scored, batch, gain))
        log.info("drift.month_done", extra={"month": month})

    # The stress_only arm: same seed, only mechanism 3 active. Regenerated
    # here because the ablation of stage 2 measures without writing batches.
    run_simulation(
        holdout,
        reference,
        config,
        model,
        toggles=config.mechanisms.only("stress"),
        label="stress_only",
        write_dir=STRESS_DIR,
    )
    stress_batch = load_batch(STRESS_DIR / f"month_{config.n_months:02d}")
    drift.save_html(
        drift.scoring_report(reference_scored, stress_batch),
        args.out_dir / f"stress_only_scoring_month_{config.n_months:02d}.html",
    )
    drift.save_html(
        drift.label_report(reference_scored, stress_batch),
        args.out_dir / f"stress_only_label_month_{config.n_months:02d}.html",
    )
    stress_rows = drift.drift_table(
        config.n_months, reference_scored, stress_batch, gain
    )

    content = drift_summary.render(
        rows=rows,
        stress_rows=stress_rows,
        gain=gain,
        months=list(range(config.n_months + 1)),
        reference_rows=len(reference),
        generated=dt.date.today().isoformat(),
        committed=CURATED_REPORTS,
        out_dir=args.out_dir,
    )
    (args.out_dir / "summary.md").write_text(content, encoding="utf-8")

    links = [
        ReportLink(
            path=name,
            title=drift_summary.TITLES[name],
            description=drift_summary.DESCRIPTIONS[name],
            size_mb=(args.out_dir / name).stat().st_size / 1_048_576,
        )
        for name in CURATED_REPORTS
    ]
    (args.out_dir / "index.html").write_text(
        drift_summary.index_html(links, dt.date.today().isoformat()), encoding="utf-8"
    )

    print_table(
        "CROSS-CHECK DE PSI (mes 6): nosso vs nativo do Evidently",
        drift_summary.cross_check_table(rows, config.n_months),
    )
    print_table("PSI POR FEATURE E MES", drift_summary.psi_matrix(rows, gain))
    print_table(
        "RANKING POR PSI x GANHO - MES 3", drift_summary.impact_ranking(rows, 3)
    )
    print_table(
        f"RANKING POR PSI x GANHO - MES {config.n_months}",
        drift_summary.impact_ranking(rows, config.n_months),
    )
    print_table(
        f"KS - MES {config.n_months}", drift_summary.ks_table(rows, config.n_months)
    )
    print()
    print("CONJUNTO CURADO (em _build, ainda nao publicado)")
    print("================================================")
    total = 0.0
    for name in (*CURATED_REPORTS, "index.html", "summary.md"):
        size = (args.out_dir / name).stat().st_size / 1_048_576
        total += size
        print(f"  {name:42} {size:7.2f} MB")
    print(f"  {'TOTAL':42} {total:7.2f} MB")
    print()
    print(f"Gerado em {args.out_dir} (ignorado pelo git).")
    print("Para versionar o conjunto curado: make publish-reports")
    print()


if __name__ == "__main__":
    main()
