"""Train the baseline candidates, compare them and register a champion.

Reads the reference set produced by ``scripts/prepare_data.py`` and never
opens the holdout: that set is the control group of the day-8 A/A test and
selecting on it would bias the false-alarm rate it exists to measure.

Run as ``uv run python scripts/train_baseline.py`` (or ``make train``).
The MLflow UI is ``make mlflow-ui`` — port 5001, because macOS binds 5000 to
the AirPlay Receiver.
"""

import argparse
import json
import logging
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from credit_monitor.constants import (
    CHAMPION_ALIAS,
    MLFLOW_EXPERIMENT,
    MLFLOW_TRACKING_URI,
    MLFLOW_UI_PORT,
    PROCESSED_DATA_DIR,
    REGISTERED_MODEL_NAME,
)
from credit_monitor.data.download import compute_sha256
from credit_monitor.data.preprocess import PreprocessingParams, build_feature_matrix
from credit_monitor.data.split import DEFAULT_SEED, REFERENCE_NAME
from credit_monitor.logging_config import configure_logging
from credit_monitor.models.train import SELECTION_METRIC, train, write_summary

log = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/train_baseline.py",
        description="Treina os modelos baseline e registra o campeão no MLflow.",
    )
    parser.add_argument("--data-dir", type=Path, default=PROCESSED_DATA_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--summary",
        type=Path,
        default=PROCESSED_DATA_DIR / "training_summary.json",
    )
    return parser


def print_comparison(table: pd.DataFrame) -> None:
    print()
    print("Comparação dos modelos (conjunto de validação, dentro do reference)")
    print("-" * 84)
    print(
        f"{'modelo':22} {'AUC-ROC':>9} {'KS':>9} {'Brier':>9} "
        f"{'Brier s/ calib.':>16} {'run_id':>10}"
    )
    print("-" * 84)
    for row in table.itertuples():
        print(
            f"{row.model:22} {row.auc_roc:9.4f} {row.ks:9.4f} {row.brier:9.4f} "
            f"{row.brier_uncalibrated:16.4f} {row.run_id[:8]:>10}"
        )
    print("-" * 84)
    print(f"Critério de seleção: {SELECTION_METRIC} (maior é melhor)")
    print()


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()

    reference_path = args.data_dir / f"{REFERENCE_NAME}.parquet"
    metadata_path = args.data_dir / "split_metadata.json"
    if not reference_path.exists():
        raise SystemExit(f"{reference_path} não existe — rode `make prepare` primeiro.")

    reference = pd.read_parquet(reference_path)
    features, target = build_feature_matrix(reference)
    split_metadata = json.loads(metadata_path.read_text())

    log.info("Treinando com %s linhas de referência", f"{len(features):,}")
    outcome = train(
        features=features,
        target=target,
        seed=args.seed,
        preprocessing_params={
            **asdict(PreprocessingParams()),
            "split_seed": split_metadata["seed"],
            "holdout_fraction": split_metadata["holdout_fraction"],
            "rows_reference": split_metadata["rows_reference"],
            "raw_source_sha256": split_metadata["source_sha256"],
        },
        source_path=reference_path,
        dataset_digest=compute_sha256(reference_path),
    )
    summary_path = write_summary(outcome, args.summary)

    print_comparison(outcome.comparison_table())
    print(f"Campeão              : {outcome.champion}")
    print(f"  run id             : {outcome.champion_run_id}")
    registered = f"{REGISTERED_MODEL_NAME} v{outcome.registered_version}"
    print(f"  modelo registrado  : {registered}")
    print(f"  alias              : {CHAMPION_ALIAS}")
    print(f"  URI de serving     : models:/{REGISTERED_MODEL_NAME}@{CHAMPION_ALIAS}")
    print()
    print(f"Desbalanceamento     : scale_pos_weight = {outcome.scale_pos_weight:.3f}")
    print(
        f"Partição do reference: treino {outcome.rows_train:,} / "
        f"calibração {outcome.rows_calibration:,} / "
        f"validação {outcome.rows_validation:,}"
    )
    print(f"Resumo               : {summary_path}")
    print(f"MLflow               : {MLFLOW_TRACKING_URI}")
    print(f"  experimento        : {MLFLOW_EXPERIMENT}")
    print(f"  UI                 : make mlflow-ui  (porta {MLFLOW_UI_PORT})")
    print()


if __name__ == "__main__":
    main()
