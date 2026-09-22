"""Where an ablation arm's batches live, and how to produce them on demand.

`make simulate` writes only the ``full`` arm to disk. The other arms are
computed in memory for the ablation tables of `reports/simulation/summary.md`
and then thrown away, because the tables are all stage 2 needed.

Stages 3 and 4 need the arms **as files**: the monitoring replay pushes one
scenario per arm, and the fairness analysis reads month 6 of two of them. This
module is the single place that knows how to materialise an arm, so a fresh
clone does not need a Docker stack up just to run `make bias` — which is what
happened before this existed.
"""

import logging
from pathlib import Path
from typing import Final

import pandas as pd

from credit_monitor.constants import PRODUCTION_DATA_DIR
from credit_monitor.models.score import ProbabilisticClassifier
from credit_monitor.simulation.config import SimulationConfig
from credit_monitor.simulation.simulate import run_simulation

log = logging.getLogger(__name__)

MISSING_REFERENCE_MESSAGE: Final[str] = (
    "{path} não existe — rode `make prepare` antes.\n"
    "A cadeia é: make download -> make prepare -> make train -> make simulate.\n"
    "Ou rode `make all`, que faz tudo na ordem certa."
)


def scenario_dir(scenario: str, batches_dir: Path = PRODUCTION_DATA_DIR) -> Path:
    """Where a scenario's monthly batches live.

    ``full`` sits at the root because it is the scenario the simulation writes
    by default; the ablation arms get a prefixed subdirectory each.
    """
    if scenario == "full":
        return batches_dir
    return batches_dir / f"ablation_{scenario}"


def ensure_scenario(
    scenario: str,
    model: ProbabilisticClassifier,
    batches_dir: Path = PRODUCTION_DATA_DIR,
    data_dir: Path | None = None,
) -> Path:
    """Return an arm's directory, generating its batches if they are absent.

    Generation is deterministic from the simulation seed, so an arm produced
    here is byte-identical to one produced by any other caller. That is what
    makes it safe to generate lazily instead of demanding a prior command.

    Args:
        scenario: ``full`` or an ablation arm name such as ``stress_only``.
        model: The champion, needed to score the generated months.
        batches_dir: Root of the production batches.
        data_dir: Where ``reference.parquet`` and ``holdout.parquet`` live.

    Returns:
        The directory holding ``month_00`` .. ``month_06``.

    Raises:
        SystemExit: The processed split is missing.
    """
    from credit_monitor.constants import PROCESSED_DATA_DIR

    directory = scenario_dir(scenario, batches_dir)
    if (directory / "month_00" / "features.parquet").exists():
        return directory

    data_dir = data_dir or PROCESSED_DATA_DIR
    reference_path = data_dir / "reference.parquet"
    if not reference_path.exists():
        raise SystemExit(MISSING_REFERENCE_MESSAGE.format(path=reference_path))

    log.info("simulation.materialising_arm", extra={"scenario": scenario})
    config = SimulationConfig.load()
    run_simulation(
        pd.read_parquet(data_dir / "holdout.parquet"),
        pd.read_parquet(reference_path),
        config,
        model,
        toggles=config.mechanisms.only(scenario.removesuffix("_only")),
        label=scenario,
        write_dir=directory,
    )
    return directory
