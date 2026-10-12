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

``--validate`` runs the ingestion gate over the batch just written and exits
with the gate's code. It is what the two ``validate-*`` targets call. The gate
used to be chained in the Makefile with ``status=$$?`` and ``exit $$status``,
which is POSIX shell and fails under ``cmd.exe``; here the batch path also comes
from the code that wrote it, instead of a date the Makefile rebuilt on its own.

``--if-missing`` writes the dirty batch only when ``data/dirty`` has none. It
is the ``make dirty-batch`` step of ``make all``, and runs the same function
``make monitor-all`` falls back on.

Run as ``uv run python scripts/make_dirty_batch.py [--mode clean] [--validate]``.
"""

import argparse
import subprocess
import sys
from pathlib import Path

from credit_monitor.constants import (
    DIRTY_DATA_DIR,
    PROJECT_ROOT,
    QUARANTINE_DIR,
    RAW_DATA_PATH,
    VALIDATION_REPORTS_DIR,
)
from credit_monitor.logging_config import configure_logging
from credit_monitor.pipeline.dirty_batch import (
    DEMO_BASE_ROWS,
    DEMO_SEED,
    DefectManifest,
    ensure_dirty_batch,
    write_demo_batch,
)


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
        "--rows", type=int, default=DEMO_BASE_ROWS, help="linhas da base limpa"
    )
    parser.add_argument(
        "--seed", type=int, default=DEMO_SEED, help="semente do amostrador"
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
    parser.add_argument(
        "--validate",
        action="store_true",
        help="roda o portão de ingestão sobre o lote e sai com o código dele",
    )
    parser.add_argument(
        "--if-missing",
        action="store_true",
        help="só gera o lote sujo se data/dirty não tiver nenhum",
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


def _relative(path: Path) -> Path:
    """``path`` relative to the repository when it is inside it."""
    try:
        return path.resolve().relative_to(PROJECT_ROOT)
    except ValueError:
        return path


def run_gate(batch_id: str, batch_path: Path, blocked_expected: bool) -> int:
    """Run the ingestion gate in its own process and report what it left behind.

    A subprocess rather than a call to ``ingest.main``: the gate configures JSON
    logging for itself, and running it in this process would change the format
    of everything printed after it.
    """
    status = subprocess.call(
        [sys.executable, "-m", "credit_monitor.pipeline.ingest", str(batch_path)]
    )
    print()
    if blocked_expected:
        print(
            f">>> ingestão saiu com código {status} — pipeline interrompido, "
            "como esperado"
        )
        print(f">>> quarentena: {_relative(QUARANTINE_DIR / batch_id)}")
    else:
        print(
            f">>> ingestão saiu com código {status} — lote aceito, alertas registrados"
        )
    print(f">>> relatório : {_relative(VALIDATION_REPORTS_DIR / f'{batch_id}.html')}")
    return status


def main() -> int:
    configure_logging()
    args = build_parser().parse_args()

    if args.if_missing:
        path, written = ensure_dirty_batch(
            args.out_dir or DIRTY_DATA_DIR,
            source=args.source,
            rows=args.rows,
        )
        verb = "gerado" if written else "já existe, mantido"
        print(f"Lote sujo {verb}: {_relative(path)}")
        return 0

    batch = write_demo_batch(
        args.mode,
        source=args.source,
        out_dir=args.out_dir,
        rows=args.rows,
        seed=args.seed,
    )
    print_manifest(batch.batch_id, batch.path, batch.manifest)
    print(f"Manifesto: {batch.manifest_path}")
    print()

    if not args.validate:
        return 0
    return run_gate(batch.batch_id, batch.path, blocked_expected=args.mode == "dirty")


if __name__ == "__main__":
    sys.exit(main())
