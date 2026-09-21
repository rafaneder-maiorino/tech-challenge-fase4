"""Run the MMD tests: the four batches, MMD's own A/A, and localization.

Run as ``uv run python scripts/mmd_test.py`` (or ``make mmd``).
"""

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from credit_monitor.constants import (
    PROCESSED_DATA_DIR,
    PRODUCTION_DATA_DIR,
    REPORTS_DIR,
)
from credit_monitor.data.preprocess import MODEL_FEATURES
from credit_monitor.drift_tests.mmd import (
    DEFAULT_N_PER_SIDE,
    DEFAULT_PERMUTATIONS,
    build_context,
    localize,
    mmd_test,
    run_self_aa,
)
from credit_monitor.logging_config import configure_logging

log = logging.getLogger(__name__)

CACHE_DIR = Path("data/drift_tests")
SEED = 20260922

BATCHES = {
    "month_00 (controle)": "month_00",
    "multivariado primário (open_lines / real_estate)": (
        "multivariate_primary_by_dependence"
    ),
    "multivariado aperto de crédito (utilização / idade)": (
        "multivariate_credit_tightening_channel"
    ),
    "month_06 (sanidade)": "month_06",
}

LOCALIZATION_TARGETS = {
    "primário": (
        "multivariate_primary_by_dependence",
        ("NumberOfOpenCreditLinesAndLoans", "NumberRealEstateLoansOrLines"),
    ),
    "aperto de crédito": (
        "multivariate_credit_tightening_channel",
        ("RevolvingUtilizationOfUnsecuredLines", "age"),
    ),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python scripts/mmd_test.py")
    parser.add_argument("--data-dir", type=Path, default=PROCESSED_DATA_DIR)
    parser.add_argument("--batches-dir", type=Path, default=PRODUCTION_DATA_DIR)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--seed", type=int, default=SEED)
    return parser


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    reference = pd.read_parquet(args.data_dir / "reference.parquet")
    holdout = pd.read_parquet(args.data_dir / "holdout.parquet")
    rng = np.random.default_rng(args.seed)
    context, reference_scores = build_context(reference, rng)

    rows = []
    for label, folder in BATCHES.items():
        frame = pd.read_parquet(args.batches_dir / folder / "features.parquet")
        sample = frame.iloc[
            rng.choice(len(frame), size=DEFAULT_N_PER_SIDE, replace=False)
        ]
        result = mmd_test(
            reference_scores,
            context.transform(sample, MODEL_FEATURES),
            context.bandwidth,
            rng,
            label,
            DEFAULT_PERMUTATIONS,
        )
        rows.append(result.to_dict())
    pd.DataFrame(rows).to_parquet(args.cache_dir / "mmd_batches.parquet", index=False)

    pvalues = run_self_aa(reference, holdout, context, rng)
    np.save(args.cache_dir / "mmd_self_aa.npy", pvalues)

    localization = {}
    for label, (folder, expected) in LOCALIZATION_TARGETS.items():
        frame = pd.read_parquet(args.batches_dir / folder / "features.parquet")
        pairs = localize(reference, frame, context, rng)
        localization[label] = {
            "expected": list(expected),
            "pairs": [
                {
                    "column_x": p.column_x,
                    "column_y": p.column_y,
                    "mmd2": p.mmd2,
                    "pvalue": p.pvalue,
                }
                for p in pairs[:6]
            ],
            "rank_of_expected": next(
                i
                for i, p in enumerate(pairs, 1)
                if {p.column_x, p.column_y} == set(expected)
            ),
        }
    (args.cache_dir / "mmd_localization.json").write_text(
        json.dumps(
            {"bandwidth": context.bandwidth, "localization": localization},
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print("MMD POR LOTE")
    print("============")
    print(f"{'lote':52} {'MMD2':>12} {'p':>8} {'detecta':>9}")
    for row in rows:
        print(
            f"{row['label']:52} {row['mmd2']:12.6f} {row['pvalue']:8.4f} "
            f"{'SIM' if row['detected'] else 'nao':>9}"
        )
    print()
    print(
        f"A/A do proprio MMD: falso alarme {float((pvalues < 0.05).mean()):.1%} "
        f"em {len(pvalues)} repeticoes | mediana do p {float(np.median(pvalues)):.3f}"
    )
    print()
    print("LOCALIZACAO (top 3 pares)")
    print("=========================")
    for label, payload in localization.items():
        print(f"\n  {label} — par invertido posicao #{payload['rank_of_expected']}")
        for index, pair in enumerate(payload["pairs"][:3], 1):
            hit = (
                "  <<< PAR INVERTIDO"
                if {pair["column_x"], pair["column_y"]} == set(payload["expected"])
                else ""
            )
            print(
                f"    {index}. {pair['column_x'][:30]:30} / "
                f"{pair['column_y'][:26]:26} MMD2={pair['mmd2']:9.6f} "
                f"p={pair['pvalue']:.4f}{hit}"
            )
    print()
    print(f"Cache em {args.cache_dir} | relatorio: {REPORTS_DIR / 'drift_tests'}")
    print()


if __name__ == "__main__":
    main()
