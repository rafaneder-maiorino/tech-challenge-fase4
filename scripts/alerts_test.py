"""Validate and unit-test the alert rules with promtool, from the pinned image.

The version is the point. Different Prometheus releases accept different rule
syntax, so a test that passes against another promtool proves nothing about the
stack that will load these rules. The image is read from
``docker-compose.monitoring.yml``, the same line the stack starts from, so there
is no second copy of the version to drift.

Two ways to run it, in this order:

1. **Docker**, ``promtool`` from ``prom/prometheus:<pinned>`` with the rules
   mounted read-only. Works the same on macOS, Linux, Windows and CI, and
   downloads nothing the stack does not already use.
2. **A native binary already in ``.tools/``** (``make promtool`` puts one there
   on Linux and macOS), accepted only if it reports the pinned version.

This replaced a Makefile recipe built from ``uname | sed``, ``tr``,
``curl | tar -C /tmp`` and ``chmod``: POSIX-only, and wrong even under Git Bash,
where ``uname -s`` is ``MINGW64_NT-…`` and the Windows release is a ``.zip``.

Run as ``uv run python scripts/alerts_test.py`` (or ``make alerts-test``).
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from credit_monitor.constants import PROJECT_ROOT

COMPOSE_FILE = PROJECT_ROOT / "docker-compose.monitoring.yml"
RULES_DIR = PROJECT_ROOT / "monitoring" / "rules"
RULE_TEST_FILE = "credit_monitor.rules.test.yml"
NATIVE_CANDIDATES = (
    PROJECT_ROOT / ".tools" / "promtool",
    PROJECT_ROOT / ".tools" / "promtool.exe",
)


def pinned_image(compose_file: Path = COMPOSE_FILE) -> str:
    """The Prometheus image the stack runs, e.g. ``prom/prometheus:v3.13.3``."""
    compose = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    return compose["services"]["prometheus"]["image"]


def docker_available() -> bool:
    """True when a Docker daemon answers, not merely when the CLI is installed."""
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


def native_promtool(version: str) -> Path | None:
    """A promtool in ``.tools/`` that reports ``version``, if there is one."""
    for candidate in NATIVE_CANDIDATES:
        if not candidate.exists():
            continue
        report = subprocess.run(
            [str(candidate), "--version"], capture_output=True, text=True, check=False
        )
        if re.search(rf"version {re.escape(version)}\b", report.stdout + report.stderr):
            return candidate
        print(f"{candidate} não é a versão {version}; ignorado.")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(prog="python scripts/alerts_test.py")
    parser.add_argument("--image", default=None, help="padrão: o do compose")
    args = parser.parse_args()

    image = args.image or pinned_image()
    version = re.sub(r"^.*:v?", "", image)
    rule_files = sorted(p.name for p in RULES_DIR.glob("*.rules.yml"))

    if docker_available():
        print(f"promtool de {image} (Docker)", flush=True)
        prefix = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{RULES_DIR}:/rules:ro",
            "-w",
            "/rules",
            "--entrypoint",
            "promtool",
            image,
        ]
    elif (native := native_promtool(version)) is not None:
        print(f"promtool nativo {version} em {native} (sem Docker)", flush=True)
        prefix = [str(native)]
    else:
        raise SystemExit(
            f"promtool {version} indisponível: suba o Docker (o alvo usa {image}) "
            "ou, em Linux/macOS, rode `make promtool` para baixar o binário."
        )

    # cwd=RULES_DIR serves the native binary; the container has -w /rules.
    # Both steps run with the same relative names either way.
    for step in (["check", "rules", *rule_files], ["test", "rules", RULE_TEST_FILE]):
        status = subprocess.call([*prefix, *step], cwd=RULES_DIR)
        if status != 0:
            return status
    return 0


if __name__ == "__main__":
    sys.exit(main())
