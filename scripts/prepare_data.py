"""Clean the raw parquet, split it, and persist the reference and holdout sets.

The full stage-1 data path in one command: drop what the inspection report
says cannot be a borrower, build the missingness indicator before imputing,
separate the two units hiding in DebtRatio, split stratified, fit the
imputation on the reference alone, and validate both sides against
``ModelInputSchema`` — the validation runs here, in the pipeline, not only in
a test.

Run as ``uv run python scripts/prepare_data.py`` (or ``make prepare``).
"""

import argparse
import datetime as dt
import logging
from pathlib import Path

import pandas as pd

from credit_monitor.constants import PROCESSED_DATA_DIR, RAW_DATA_PATH
from credit_monitor.data.download import compute_sha256
from credit_monitor.data.preprocess import (
    PreprocessingParams,
    assert_nested_missingness_subsumed,
    clean,
    fit_imputation,
    prepare_split,
)
from credit_monitor.data.split import (
    DEFAULT_HOLDOUT_FRACTION,
    DEFAULT_SEED,
    HOLDOUT_NAME,
    REFERENCE_NAME,
    build_metadata,
    persist,
    stratified_split,
)
from credit_monitor.logging_config import configure_logging

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/prepare_data.py",
        description="Limpa o dado bruto e grava os conjuntos reference e holdout.",
    )
    parser.add_argument("--source", type=Path, default=RAW_DATA_PATH)
    parser.add_argument("--out-dir", type=Path, default=PROCESSED_DATA_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--holdout-fraction", type=float, default=DEFAULT_HOLDOUT_FRACTION
    )
    parser.add_argument(
        "--utilization-cap",
        type=float,
        default=PreprocessingParams().utilization_cap,
        help="teto de RevolvingUtilizationOfUnsecuredLines (padrão 1.0; ver §8)",
    )
    return parser


def print_cleaning_report(report: object, title: str) -> None:
    print()
    print(title)
    print("-" * len(title))
    for key, value in report.to_dict().items():  # type: ignore[attr-defined]
        shown = f"{value:.4%}" if isinstance(value, float) else f"{value:,}"
        print(f"  {key:30} {shown:>14}")


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()
    params = PreprocessingParams(utilization_cap=args.utilization_cap)

    log.info("Lendo %s", args.source)
    raw = pd.read_parquet(args.source)

    cleaned, report = clean(raw, params)
    # Verifies the §3 containment still holds on THIS delivery, which is what
    # licenses a single income_missing flag standing in for both columns.
    assert_nested_missingness_subsumed(cleaned)

    reference_raw, holdout_raw = stratified_split(
        cleaned, seed=args.seed, holdout_fraction=args.holdout_fraction
    )
    # Fitted on the reference only: the holdout is the day-8 control group and
    # must not influence the values the reference is filled with.
    fit = fit_imputation(reference_raw)
    reference = prepare_split(REFERENCE_NAME, reference_raw, fit)
    holdout = prepare_split(HOLDOUT_NAME, holdout_raw, fit)

    metadata = build_metadata(
        frame=cleaned,
        reference=reference.frame,
        holdout=holdout.frame,
        seed=args.seed,
        holdout_fraction=args.holdout_fraction,
        source_sha256=compute_sha256(args.source),
        created_at=dt.datetime.now(tz=dt.UTC).replace(microsecond=0).isoformat(),
    )
    paths = persist(reference.frame, holdout.frame, metadata, args.out_dir)

    print_cleaning_report(report, "Limpeza")
    print()
    print("Divisão estratificada")
    print("---------------------")
    print(f"  semente                        {args.seed:>14}")
    print(f"  reference (treino + baseline)  {reference.rows:>14,}")
    print(f"    taxa de positivos            {reference.positive_rate:>13.4%}")
    print(f"  holdout (controle do A/A)      {holdout.rows:>14,}")
    print(f"    taxa de positivos            {holdout.positive_rate:>13.4%}")
    print()
    print("Imputação (ajustada só no reference)")
    print("------------------------------------")
    for key, value in fit.to_dict().items():
        print(f"  {key:30} {value:>14,.4f}")
    print()
    print("Contrato: ModelInputSchema validado nos dois conjuntos. OK")
    print()
    for name, path in paths.items():
        print(f"  {name:10} {path}")
    print()


if __name__ == "__main__":
    main()
