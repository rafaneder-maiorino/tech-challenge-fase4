"""Stratified reference/holdout split, and the sealed control set.

Two sets with two different jobs, and the second one only works if nobody
touches it:

``reference``
    Training data *and* the drift baseline of stage 2. Every distribution the
    monitor compares a future batch against is computed from this set.

``holdout``
    The control group for the day-8 A/A test. An A/A test feeds the monitor
    two samples drawn from the same population and checks how often it cries
    drift anyway — the false-alarm rate. That measurement is only meaningful
    if the holdout really is the same population as the reference and has had
    no chance to be selected for.

So the holdout is used for exactly nothing until day 8. Model comparison runs
inside the reference set, on its own train/calibration/validation partition
(see :mod:`credit_monitor.models.train`). If the holdout were used to pick a
model, the A/A test would be measuring a set that had been chosen *because* it
made some model look good, and its false-alarm rate would be an underestimate
of the real one.

Stratification is on the target. At a 6.68% positive rate (inspection §2) a
uniform split leaves the two sets with visibly different base rates, and the
drift monitor would then report a difference that the splitter created.
"""

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

import pandas as pd
from sklearn.model_selection import train_test_split

from credit_monitor.constants import PROCESSED_DATA_DIR, TARGET_COLUMN
from credit_monitor.contracts.raw import POSITIVE_LABEL

log = logging.getLogger(__name__)

# Fixed so the reference distribution is the same artefact on every machine.
# Stage 2 compares batches against it; a baseline that moved between runs
# would make every drift number unreproducible.
DEFAULT_SEED: Final[int] = 20260920

# 30% held back. Large enough that the A/A test has power — at ~44k rows a
# KS test resolves differences far smaller than anything worth alerting on —
# while leaving ~103k rows to train and to build the reference distribution.
DEFAULT_HOLDOUT_FRACTION: Final[float] = 0.30

REFERENCE_NAME: Final[str] = "reference"
HOLDOUT_NAME: Final[str] = "holdout"


@dataclass(frozen=True, slots=True)
class SplitMetadata:
    """Everything needed to reproduce or audit a split, persisted beside it."""

    seed: int
    holdout_fraction: float
    stratified_on: str
    rows_total: int
    rows_reference: int
    rows_holdout: int
    positive_rate_total: float
    positive_rate_reference: float
    positive_rate_holdout: float
    source_sha256: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """Render as a plain dict for JSON and for MLflow params."""
        return asdict(self)


def stratified_split(
    frame: pd.DataFrame,
    seed: int = DEFAULT_SEED,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split into reference and holdout, stratified on the target.

    Args:
        frame: The cleaned frame, before imputation.
        seed: Fixed for reproducibility.
        holdout_fraction: Share of rows sealed for the A/A test.

    Returns:
        ``(reference, holdout)``, both positionally re-indexed.
    """
    reference, holdout = train_test_split(
        frame,
        test_size=holdout_fraction,
        random_state=seed,
        stratify=frame[TARGET_COLUMN],
    )
    return (
        reference.reset_index(drop=True),
        holdout.reset_index(drop=True),
    )


def _positive_rate(frame: pd.DataFrame) -> float:
    return float((frame[TARGET_COLUMN] == POSITIVE_LABEL).mean())


def build_metadata(
    frame: pd.DataFrame,
    reference: pd.DataFrame,
    holdout: pd.DataFrame,
    seed: int,
    holdout_fraction: float,
    source_sha256: str,
    created_at: str,
) -> SplitMetadata:
    """Assemble the metadata record persisted next to the two parquets."""
    return SplitMetadata(
        seed=seed,
        holdout_fraction=holdout_fraction,
        stratified_on=TARGET_COLUMN,
        rows_total=len(frame),
        rows_reference=len(reference),
        rows_holdout=len(holdout),
        positive_rate_total=_positive_rate(frame),
        positive_rate_reference=_positive_rate(reference),
        positive_rate_holdout=_positive_rate(holdout),
        source_sha256=source_sha256,
        created_at=created_at,
    )


def persist(
    reference: pd.DataFrame,
    holdout: pd.DataFrame,
    metadata: SplitMetadata,
    directory: Path = PROCESSED_DATA_DIR,
) -> dict[str, Path]:
    """Write both splits and the metadata, returning the paths written.

    Args:
        reference: The reference split, imputed and validated.
        holdout: The holdout split, imputed and validated.
        metadata: The record of how the split was produced.
        directory: Destination, created if absent.

    Returns:
        A mapping of name to path for the three files written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    paths = {
        REFERENCE_NAME: directory / f"{REFERENCE_NAME}.parquet",
        HOLDOUT_NAME: directory / f"{HOLDOUT_NAME}.parquet",
        "metadata": directory / "split_metadata.json",
    }
    reference.to_parquet(paths[REFERENCE_NAME], index=False)
    holdout.to_parquet(paths[HOLDOUT_NAME], index=False)
    paths["metadata"].write_text(
        json.dumps(metadata.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    log.info(
        "split.persisted",
        extra={**metadata.to_dict(), **{k: str(v) for k, v in paths.items()}},
    )
    return paths


def load_split(name: str, directory: Path = PROCESSED_DATA_DIR) -> pd.DataFrame:
    """Read a persisted split by name.

    Args:
        name: ``"reference"`` or ``"holdout"``.
        directory: Where the splits were persisted.

    Returns:
        The split, as written.
    """
    return pd.read_parquet(directory / f"{name}.parquet")
