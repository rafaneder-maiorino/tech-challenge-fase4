"""Copy the curated drift reports from the build directory into the repository.

**The only command that touches tracked HTML.** Generation
(`make drift-reports`) writes to `reports/evidently/_build/`, which is ignored;
this promotes the curated set into `reports/evidently/`, which is tracked.

The split exists because Evidently's HTML is not byte-deterministic: the
report's JavaScript variable is named with a fresh random UUID on every run, so
two generations of *identical* data differ in ~2,300 byte positions across a
4 MB file. Without the split, every routine regeneration would dirty the tree
with six 4 MB files carrying the same numbers, and one careless `git commit -a`
would put that in history permanently.

Publishing is therefore a deliberate act — end of a stage, final delivery — and
never a side effect of looking at a report.

Run as ``uv run python scripts/publish_reports.py`` (or ``make publish-reports``).
"""

import argparse
import shutil
from pathlib import Path

from credit_monitor.constants import REPORTS_DIR
from credit_monitor.reporting.drift_summary import CURATED_REPORTS

EVIDENTLY_DIR = REPORTS_DIR / "evidently"
BUILD_DIR = EVIDENTLY_DIR / "_build"

# Published alongside the HTML: the index that links them and the numbers.
EXTRA_FILES = ("index.html", "summary.md")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/publish_reports.py",
        description="Publica o conjunto curado de relatórios no repositório.",
    )
    parser.add_argument("--build-dir", type=Path, default=BUILD_DIR)
    parser.add_argument("--out-dir", type=Path, default=EVIDENTLY_DIR)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not args.build_dir.is_dir():
        raise SystemExit(
            f"{args.build_dir} não existe — rode `make drift-reports` primeiro."
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    names = (*CURATED_REPORTS, *EXTRA_FILES)
    missing = [n for n in names if not (args.build_dir / n).exists()]
    if missing:
        raise SystemExit(
            f"faltam no build: {', '.join(missing)} — rode `make drift-reports`."
        )

    print()
    print("PUBLICANDO O CONJUNTO CURADO")
    print("============================")
    total = 0.0
    for name in names:
        source, destination = args.build_dir / name, args.out_dir / name
        shutil.copy2(source, destination)
        size = destination.stat().st_size / 1_048_576
        total += size
        print(f"  {name:42} {size:7.2f} MB")
    print(f"  {'TOTAL':42} {total:7.2f} MB")
    print()
    print("Os arquivos acima são versionados. Confira o diff antes de commitar:")
    print("  git status reports/evidently/")
    print()


if __name__ == "__main__":
    main()
