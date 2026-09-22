"""Structured JSON logs for the pipeline, written where the log shipper reads.

One JSON object per line in ``logs/``, which Alloy tails and forwards to Loki.
``batch_id``, ``scenario`` and ``stage`` are promoted to Loki labels, so those
are the dimensions a query filters on.

**No raw feature values, ever.** A log line is the least access-controlled
artefact a pipeline produces: it is tailed, forwarded, indexed, and retained
somewhere nobody is thinking about. The features here are a person's income,
age, dependants and debt — writing them to a log would be exporting the
dataset through the back door, one line at a time. Row identity travels as a
**salted hash of the row id**, which is enough to correlate two log lines about
the same row and not enough to reconstruct anything about the person.
"""

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from credit_monitor.constants import PROJECT_ROOT
from credit_monitor.logging_config import JsonFormatter

LOG_DIR: Final[Path] = PROJECT_ROOT / "logs"

# The salt has to be stable across runs, or a Loki query cannot follow one row
# through the pipeline. The default below is committed and therefore **public**:
# it protects against the likely accident of readable personal data sitting in a
# log index, and not at all against someone holding this repository. With a row
# id space of 150,000 the mapping inverts by enumeration in under a second.
#
# A deployment that handles real people overrides it via the environment, so the
# salt lives in the secret store and rotates without a code change. See
# `docs/governanca.md` §5 for what the hash is and is not under the LGPD.
ROW_SALT_ENV: Final[str] = "CREDIT_MONITOR_ROW_SALT"
DEFAULT_ROW_ID_SALT: Final[str] = "credit-monitor-stage-3"

# Fields that must never appear in a log record, checked in tests. These are
# the feature columns; a stage that wanted to log one is a stage that should be
# writing a parquet instead.
FORBIDDEN_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "MonthlyIncome",
        "DebtRatio",
        "age",
        "RevolvingUtilizationOfUnsecuredLines",
        "NumberOfDependents",
        "NumberOfOpenCreditLinesAndLoans",
        "NumberRealEstateLoansOrLines",
        "NumberOfTime30-59DaysPastDueNotWorse",
        "NumberOfTimes90DaysLate",
        "NumberOfTime60-89DaysPastDueNotWorse",
    }
)


def row_salt() -> str:
    """The salt in force, from the environment or the committed default.

    Read per call rather than at import: a process that sets the variable after
    importing this module would otherwise keep hashing with the public default
    and never know it.
    """
    return os.environ.get(ROW_SALT_ENV, DEFAULT_ROW_ID_SALT)


def hash_row_id(row_id: int | str) -> str:
    """Salted, truncated SHA-256 of a row id.

    Sixteen hex characters: enough that a collision across a 44,000-row batch
    is not a practical concern, short enough to read in a log line.
    """
    digest = hashlib.sha256(f"{row_salt()}:{row_id}".encode()).hexdigest()
    return digest[:16]


@dataclass(frozen=True, slots=True)
class PipelineLogger:
    """A logger bound to one batch, writing JSONL where Alloy will find it."""

    batch_id: str
    scenario: str
    path: Path
    logger: logging.Logger

    @classmethod
    def create(
        cls, batch_id: str, scenario: str, directory: Path = LOG_DIR
    ) -> "PipelineLogger":
        """Open a per-scenario log file and attach a JSON handler to it."""
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{scenario}.jsonl"
        logger = logging.getLogger(f"credit_monitor.pipeline.{scenario}")
        logger.setLevel(logging.INFO)
        # Replace rather than append: a second run in the same process would
        # otherwise write every line twice.
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
        return cls(batch_id=batch_id, scenario=scenario, path=path, logger=logger)

    def emit(
        self,
        event: str,
        stage: str,
        status: str = "ok",
        level: int = logging.INFO,
        **fields: object,
    ) -> None:
        """Write one structured line.

        Raises:
            ValueError: a field name is a feature column. Failing loudly beats
                writing personal data to a log index and discovering it later.
        """
        leaked = FORBIDDEN_FIELDS & set(fields)
        if leaked:
            raise ValueError(
                "tentativa de registrar valores de feature em log: "
                f"{sorted(leaked)}. Logs levam contagens, durações e ids com "
                "hash — nunca o dado da pessoa."
            )
        self.logger.log(
            level,
            event,
            extra={
                "batch_id": self.batch_id,
                "scenario": self.scenario,
                "stage": stage,
                "status": status,
                **fields,
            },
        )

    def read_records(self) -> list[dict[str, Any]]:
        """Parse back what was written, for the tests and the Loki check."""
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
