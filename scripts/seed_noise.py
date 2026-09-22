"""The noise floor of AUC and KS across five seeds, same data, same protocol.

A margin reported without the noise of the procedure that produced it is a
margin without an interpretation. Stage 1 measured the floor for **AUC** while
deciding the utilization cap (findings §7) and never measured it for **KS** —
which left the Model Card quoting a KS margin against the AUC's ruler.

This reruns the exact training protocol with the five seeds of that study,
changing only the internal partition. It touches **no** MLflow run and **no**
registry: the champion stays version 1 with its alias where it is. The point is
the spread, not a new model.
"""

import argparse
import json
import statistics
from pathlib import Path
from typing import Final

import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.model_selection import train_test_split

from credit_monitor.constants import PROCESSED_DATA_DIR, REPORTS_DIR
from credit_monitor.data.preprocess import build_feature_matrix
from credit_monitor.data.split import REFERENCE_NAME
from credit_monitor.models.metrics import evaluate
from credit_monitor.models.train import (
    CALIBRATION_AND_VALIDATION_FRACTION,
    build_candidates,
)

# The same five of the stage-1 cap study, so the AUC column reproduces it.
SEEDS: Final[tuple[int, ...]] = (20260920, 7, 1234, 99, 555)


def fit_one(
    features: pd.DataFrame, target: pd.Series, seed: int
) -> dict[str, dict[str, float]]:
    """Run the training protocol once and return calibrated metrics per model."""
    train_x, rest_x, train_y, rest_y = train_test_split(
        features,
        target,
        test_size=CALIBRATION_AND_VALIDATION_FRACTION,
        random_state=seed,
        stratify=target,
    )
    calib_x, valid_x, calib_y, valid_y = train_test_split(
        rest_x, rest_y, test_size=0.5, random_state=seed, stratify=rest_y
    )
    scale_pos_weight = float((train_y == 0).sum() / (train_y == 1).sum())

    out: dict[str, dict[str, float]] = {}
    for name, (estimator, _) in build_candidates(scale_pos_weight, seed).items():
        estimator.fit(train_x, train_y)
        calibrated = CalibratedClassifierCV(
            FrozenEstimator(estimator), method="isotonic"
        )
        calibrated.fit(calib_x, calib_y)
        scores = calibrated.predict_proba(valid_x)[:, 1]
        metrics = evaluate(valid_y.to_numpy(), scores)
        out[name] = {"auc_roc": metrics.auc_roc, "ks": metrics.ks_statistic}
    return out


def _spread(values: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.fmean(values),
        "stdev": statistics.stdev(values),
        "range": max(values) - min(values),
    }


def build_parser() -> argparse.ArgumentParser:
    """CLI surface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=PROCESSED_DATA_DIR)
    parser.add_argument("--out", type=Path, default=REPORTS_DIR / "seed_noise.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Measure, print and record. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    reference = pd.read_parquet(args.data_dir / f"{REFERENCE_NAME}.parquet")
    features, target = build_feature_matrix(reference)

    per_seed = {seed: fit_one(features, target, seed) for seed in SEEDS}
    models = sorted(next(iter(per_seed.values())))

    print(f"{'semente':>10} " + "".join(f"{m[:12]:>26}" for m in models))
    print(f"{'':>10} " + "".join(f"{'AUC':>13}{'KS':>13}" for _ in models))
    for seed in SEEDS:
        row = "".join(
            f"{per_seed[seed][m]['auc_roc']:>13.4f}{per_seed[seed][m]['ks']:>13.4f}"
            for m in models
        )
        print(f"{seed:>10} {row}")

    summary: dict[str, dict[str, dict[str, float]]] = {}
    for model in models:
        summary[model] = {
            metric: _spread([per_seed[seed][model][metric] for seed in SEEDS])
            for metric in ("auc_roc", "ks")
        }

    print()
    for model in models:
        for metric in ("auc_roc", "ks"):
            stats = summary[model][metric]
            print(
                f"{model:22} {metric:8} média {stats['mean']:.4f}  "
                f"desvio {stats['stdev']:.4f}  amplitude {stats['range']:.4f}"
            )

    # The question limitation #1 actually asks: does the champion win on every
    # seed, or only on the one that was run? A margin that survives five out of
    # five is a different claim from a margin larger than a standard deviation.
    print()
    deltas = {
        metric: [
            per_seed[seed]["xgboost"][metric]
            - per_seed[seed]["logistic_regression"][metric]
            for seed in SEEDS
        ]
        for metric in ("auc_roc", "ks")
    }
    for metric, values in deltas.items():
        wins = sum(1 for value in values if value > 0)
        stats = _spread(values)
        print(
            f"delta xgboost menos logística, {metric:8} "
            f"média {stats['mean']:+.4f}  desvio {stats['stdev']:.4f}  "
            f"vitórias {wins}/{len(SEEDS)}  valores "
            + " ".join(f"{value:+.4f}" for value in values)
        )
        summary.setdefault("delta", {})[metric] = {**stats, "wins": float(wins)}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "seeds": list(SEEDS),
                "per_seed": {str(k): v for k, v in per_seed.items()},
                "summary": summary,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nEscrito em {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
