"""Generate a demo batch with a known set of contract violations in it.

Samples a small, seeded, stratified base from the real parquet — rows that
already pass the raw contract — then injects a fixed number of violations of
each rule and writes the result next to a manifest saying exactly what went in.
The manifest is printed before validation ever runs, which is the whole point:
the expected counts are known in advance, so the validation report can be
checked rather than believed.

Two modes:

``--mode dirty`` (default)
    Blockers and warnings. Feeds ``make validate-bad-batch``, which must end
    BLOCKED with a non-zero exit.

``--mode clean``
    Warnings only. Feeds ``make validate-clean-batch``, which must end
    ACCEPTED_WITH_WARNINGS with exit 0 — the run that shows the gate
    discriminating instead of just always failing.

Run as ``uv run python scripts/make_dirty_batch.py [--mode clean]``.
"""

import argparse
import datetime as dt
import json
import logging
from pathlib import Path

import pandas as pd

from credit_monitor.constants import (
    CLEAN_DATA_DIR,
    DIRTY_DATA_DIR,
    RAW_DATA_PATH,
)
from credit_monitor.logging_config import configure_logging
from credit_monitor.pipeline.dirty_batch import (
    DefectManifest,
    inject_defects,
    sample_clean_base,
)

log = logging.getLogger(__name__)

# 200 rows: big enough that the stratified positive count is a whole number
# close to the 6.68% reference, small enough that the HTML report and the
# quarantine can be read on a projector without scrolling for a minute.
DEFAULT_BASE_ROWS: int = 200

# Fixed, not configurable by accident: "deterministic" means the demo produces
# the same batch on the presenter's laptop and on the grader's.
DEFAULT_SEED: int = 20260920


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/make_dirty_batch.py",
        description="Gera um lote de demonstração com defeitos conhecidos.",
    )
    parser.add_argument(
        "--mode",
        choices=("dirty", "clean"),
        default="dirty",
        help="dirty injeta bloqueantes e alertas; clean injeta só alertas",
    )
    parser.add_argument(
        "--rows", type=int, default=DEFAULT_BASE_ROWS, help="linhas da base limpa"
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="semente do amostrador"
    )
    parser.add_argument(
        "--source", type=Path, default=RAW_DATA_PATH, help="parquet bruto de origem"
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="destino (padrão: data/dirty ou data/clean conforme o modo)",
    )
    return parser


def print_manifest(batch_id: str, path: Path, manifest: DefectManifest) -> None:
    """Print the injected-defect manifest as a table."""
    print()
    print(f"Lote gerado: {batch_id}")
    print(f"  arquivo   : {path}")
    print(f"  base limpa: {manifest.rows_in_base} linhas")
    print(f"  entregue  : {manifest.rows_out} linhas")
    print()

    width = max(len(rule) for rule in manifest.injected)
    print(f"{'REGRA VIOLADA':<{width}}  {'LINHAS':>6}")
    print(f"{'-' * width}  {'-' * 6}")
    for rule, rows in sorted(manifest.injected.items()):
        print(f"{rule:<{width}}  {rows:>6}")
    print(f"{'-' * width}  {'-' * 6}")
    print(f"{'total de defeitos':<{width}}  {sum(manifest.injected.values()):>6}")
    print()


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()

    include_blockers = args.mode == "dirty"
    out_dir = args.out_dir or (DIRTY_DATA_DIR if include_blockers else CLEAN_DATA_DIR)
    stamp = dt.date.today().strftime("%Y%m%d")
    batch_id = f"batch_{stamp}" if include_blockers else f"clean_batch_{stamp}"

    log.info("Lendo %s", args.source)
    source = pd.read_parquet(args.source)
    base = sample_clean_base(source, n_rows=args.rows, seed=args.seed)
    batch, manifest = inject_defects(base, include_blockers=include_blockers)

    out_dir.mkdir(parents=True, exist_ok=True)
    batch_path = out_dir / f"{batch_id}.parquet"
    manifest_path = out_dir / f"{batch_id}.manifest.json"
    batch.to_parquet(batch_path, index=False)
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), indent=2) + "\n", encoding="utf-8"
    )

    print_manifest(batch_id, batch_path, manifest)
    print(f"Manifesto: {manifest_path}")
    print()


if __name__ == "__main__":
    main()
