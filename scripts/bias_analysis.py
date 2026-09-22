"""Measure group fairness by age band, on the holdout and on a drifted batch.

Writes the three charts and a JSON of every number the narrative in
``docs/vies.md`` cites, so the document can be checked against a rerun instead
of trusted.

The operating threshold is **not** chosen here. It is read from the champion's
KS threshold, fitted on the validation partition during training — a cutoff
picked on the evaluation data would be a cutoff picked knowing the answer.
"""

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Final

import pandas as pd

from credit_monitor.constants import (
    PROCESSED_DATA_DIR,
    PRODUCTION_DATA_DIR,
    REPORTS_DIR,
)
from credit_monitor.data.preprocess import MODEL_FEATURES, TARGET_COLUMN
from credit_monitor.fairness.bands import (
    MIN_CELL_FOR_COMPARISON,
    BandMetrics,
    assign_bands,
    band_metrics,
    common_comparable_bands,
    fairness_criteria,
    wilson_interval,
)
from credit_monitor.models.score import load_champion, predict_proba
from credit_monitor.reporting.fairness_charts import (
    render_approval_chart,
    render_calibration_chart,
    render_odds_chart,
)
from credit_monitor.simulation.arms import ensure_scenario

FAIRNESS_REPORTS_DIR: Final[Path] = REPORTS_DIR / "fairness"
POSITIVE_LABEL: Final[str] = "1"


def operating_threshold(summary_path: Path) -> tuple[float, str]:
    """The champion's KS threshold, and the name it was fitted under.

    Args:
        summary_path: ``training_summary.json`` from stage 1.

    Returns:
        ``(threshold, champion_name)``.
    """
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    champion = next(
        model for model in summary["models"] if model["name"] == summary["champion"]
    )
    return float(champion["calibrated"]["ks_threshold"]), str(summary["champion"])


def _scored(
    frame: pd.DataFrame, model: object
) -> tuple[pd.Series, pd.Series, pd.Series]:
    features = frame[list(MODEL_FEATURES)].astype("float64")
    target = (frame[TARGET_COLUMN].astype(str) == POSITIVE_LABEL).astype("int8")
    scores = pd.Series(predict_proba(model, features), index=frame.index)
    return pd.Series(assign_bands(frame["age"]), index=frame.index), target, scores


def load_batch(directory: Path) -> pd.DataFrame:
    """Join a production batch to its labels on ``row_id``."""
    features = pd.read_parquet(directory / "features.parquet")
    labels = pd.read_parquet(directory / "labels.parquet")
    return features.merge(labels, on="row_id")


def measure(frame: pd.DataFrame, model: object, threshold: float) -> list[BandMetrics]:
    """Per-band metrics for one population at the operating threshold."""
    bands, target, scores = _scored(frame, model)
    return band_metrics(bands, target.to_numpy(), scores.to_numpy(), threshold)


def _serialise(rows: list[BandMetrics]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in rows:
        record = asdict(row)
        positives = round(row.base_rate * row.n)
        low, high = wilson_interval(positives, row.n)
        # The calibration gap's uncertainty is the observed rate's: the
        # predicted mean over hundreds of rows barely moves by comparison.
        record["calibration_gap_ci"] = (
            row.mean_predicted - high,
            row.mean_predicted - low,
        )
        record["comparable"] = row.comparable
        out.append(record)
    return out


def build_parser() -> argparse.ArgumentParser:
    """CLI surface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=PROCESSED_DATA_DIR)
    parser.add_argument("--production-dir", type=Path, default=PRODUCTION_DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=FAIRNESS_REPORTS_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Measure, render and record. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    threshold, champion = operating_threshold(args.data_dir / "training_summary.json")
    model = load_champion()

    # `make simulate` writes only the `full` arm; the ablation arms used below
    # are materialised on demand, deterministically from the same seed. Before
    # this, `make bias` failed on a fresh clone because the arms happened to
    # exist only after `make monitor-all` — which needs a Docker stack up.
    arms = {
        scenario: ensure_scenario(scenario, model, args.production_dir, args.data_dir)
        for scenario in ("full", "composition_only", "stress_only")
    }

    populations: dict[str, pd.DataFrame] = {
        "holdout": pd.read_parquet(args.data_dir / "holdout.parquet"),
        "full/month_00": load_batch(arms["full"] / "month_00"),
        "full/month_06": load_batch(arms["full"] / "month_06"),
        "composition_only/month_06": load_batch(arms["composition_only"] / "month_06"),
        "stress_only/month_06": load_batch(arms["stress_only"] / "month_06"),
    }

    measured = {
        name: measure(frame, model, threshold) for name, frame in populations.items()
    }
    # Every cross-population comparison in docs/vies.md goes through the bands
    # comparable in BOTH, named. Month 0 has three comparable bands and month 6
    # has four; subtracting criteria computed over different sets would be
    # subtracting answers to different questions.
    drift_pair = ("full/month_00", "full/month_06")
    common = common_comparable_bands(*(measured[name] for name in drift_pair))

    record: dict[str, object] = {
        "champion": champion,
        "operating_threshold": threshold,
        "threshold_source": "ks_threshold da partição de validação (etapa 1)",
        "min_cell_for_comparison": MIN_CELL_FOR_COMPARISON,
        "populations": {
            name: {
                "n": len(populations[name]),
                "bands": _serialise(rows),
                "criteria": asdict(fairness_criteria(rows)),
                "bands_excluded": [row.band for row in rows if not row.comparable],
            }
            for name, rows in measured.items()
        },
        "drift_comparison": {
            "populations": list(drift_pair),
            "common_comparable_bands": list(common),
            "criteria": {
                name: asdict(fairness_criteria(measured[name], restrict_to=common))
                for name in drift_pair
            },
        },
    }
    (args.out_dir / "bands.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    render_approval_chart(measured["holdout"], args.out_dir / "approval_by_band.png")
    render_odds_chart(measured["holdout"], args.out_dir / "odds_by_band.png")
    render_calibration_chart(
        measured["holdout"],
        measured["full/month_06"],
        args.out_dir / "calibration_by_band.png",
        "holdout contra mês 6 do cenário `full` "
        "· diferença entre faixas 0,0035 no mês 0 e 0,0264 no mês 6",
    )

    for name, rows in measured.items():
        criteria = fairness_criteria(rows)
        print(f"\n{name}  (n={len(populations[name]):,})".replace(",", "."))
        excluded = [row.band for row in rows if not row.comparable] or ["nenhuma"]
        print(f"  fora do critério (< {MIN_CELL_FOR_COMPARISON} positivos): {excluded}")
        for row in rows:
            flag = "" if row.comparable else "  (célula pequena)"
            print(
                f"  {row.band:7} n={row.n:>6} base={row.base_rate:.4f} "
                f"aprov={row.approval_rate:.4f} TPR={row.tpr:.4f} FPR={row.fpr:.4f} "
                f"gap={row.calibration_gap:+.4f}{flag}"
            )
        print(f"  critérios: {criteria.summary}")
    print(f"\nmês 0 → mês 6 sobre as faixas comparáveis nos DOIS: {list(common)}")
    for name in drift_pair:
        summary = fairness_criteria(measured[name], restrict_to=common).summary
        print(f"  {name:16} " + "  ".join(f"{k}={v:.4f}" for k, v in summary.items()))

    print(f"\nLimiar de operação: {threshold:.6f} (KS do campeão {champion})")
    print(f"Escrito em {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
