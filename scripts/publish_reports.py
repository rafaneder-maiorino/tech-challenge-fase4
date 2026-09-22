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
import re
import shutil
from pathlib import Path

from credit_monitor.constants import REPORTS_DIR
from credit_monitor.reporting.drift_summary import CURATED_REPORTS

EVIDENTLY_DIR = REPORTS_DIR / "evidently"
BUILD_DIR = EVIDENTLY_DIR / "_build"

# Published alongside the HTML: the index that links them and the numbers.
EXTRA_FILES = ("index.html", "summary.md")

# The two id formats Evidently regenerates on every run: a 32-hex hash used as
# the report's JavaScript variable name, and UUIDv7 widget ids — which embed
# the generation timestamp, so they change even when nothing else does.
#
# Normalising them is what makes "did the report actually change?" answerable.
# On day 8 a republish produced six files that were byte-different and
# content-identical; committing them would have added ~7 MB of pure churn. That
# was caught by hand. This catches it in code, which is the difference between
# a rule and a habit.
GENERATED_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32}"
)


def normalized(path: Path) -> str:
    """File contents with every generated id replaced by a fixed token."""
    return GENERATED_ID.sub("<id>", path.read_text(encoding="utf-8", errors="ignore"))


def content_changed(source: Path, destination: Path) -> bool:
    """Whether the two files differ in anything but generated ids.

    A missing destination counts as changed: the first publish has to happen.
    """
    if not destination.exists():
        return True
    return normalized(source) != normalized(destination)


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
    copied, skipped, total = [], [], 0.0
    for name in names:
        source, destination = args.build_dir / name, args.out_dir / name
        if not content_changed(source, destination):
            skipped.append(name)
            print(f"  {name:42} {'ignorado':>10}  conteúdo idêntico (só ids)")
            continue
        shutil.copy2(source, destination)
        size = destination.stat().st_size / 1_048_576
        total += size
        copied.append(name)
        print(f"  {name:42} {size:7.2f} MB  copiado")

    print()
    if skipped:
        print(
            f"{len(skipped)} arquivo(s) ignorado(s): idênticos aos versionados "
            "depois de normalizar o nome da variável JavaScript e os ids de "
            "widget (UUIDv7, que embutem o instante de geração). Copiá-los "
            "produziria um diff de megabytes com zero informação nova."
        )
    if copied:
        print(f"{len(copied)} arquivo(s) copiado(s), {total:.2f} MB no total.")
        print()
        print("Confira o diff antes de commitar:")
        print("  git status reports/evidently/")
    else:
        print("Nada a publicar: o conjunto versionado já está atualizado.")
    print()


if __name__ == "__main__":
    main()
