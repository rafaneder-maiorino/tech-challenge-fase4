"""Shared scaffolding for the test suite.

One place for the hand-built batch factory, because both the contract tests and
the ingestion tests need a frame that satisfies the contract and neither may
read ``data/raw/gmsc.parquet``. The real file is the thing the contract is
*about*: a test that validated it would assert today's data rather than the
rule, would fail the moment the source legitimately changed, and would need the
dataset present to run at all.
"""

import numpy as np
import pandas as pd

from credit_monitor.constants import TARGET_COLUMN
from credit_monitor.contracts.raw import (
    EXPECTED_COLUMNS,
    POSITIVE_LABEL,
    TARGET_LABELS,
)

# The dtypes of data/raw/gmsc.parquet (inspection §1). Restated once, here,
# because a hand-built frame has to be given them explicitly: `coerce` is off in
# the contract, which is the behaviour these tests are partly checking.
_DTYPES: dict[str, str] = {
    "RevolvingUtilizationOfUnsecuredLines": "float64",
    "age": "uint8",
    "NumberOfTime30-59DaysPastDueNotWorse": "uint8",
    "DebtRatio": "float64",
    "MonthlyIncome": "float64",
    "NumberOfOpenCreditLinesAndLoans": "uint8",
    "NumberOfTimes90DaysLate": "uint8",
    "NumberRealEstateLoansOrLines": "uint8",
    "NumberOfTime60-89DaysPastDueNotWorse": "uint8",
    "NumberOfDependents": "float64",
}


def make_frame(n_rows: int = 15, n_positive: int = 1) -> pd.DataFrame:
    """Build a frame that passes both schemas.

    15 rows with 1 positive is 6.67%, which is both inside the [5%, 9%] band and
    within rounding distance of the 6.68% the inspection report measured. Every
    column varies with the row index so that no two rows coincide, which keeps
    the duplicate rule quiet until a test asks for it.
    """
    index = range(n_rows)
    frame = pd.DataFrame(
        {
            TARGET_COLUMN: pd.Categorical(
                [POSITIVE_LABEL] * n_positive + ["0"] * (n_rows - n_positive),
                categories=sorted(TARGET_LABELS),
                ordered=True,
            ),
            # <= 1 (§8) and distinct per row.
            "RevolvingUtilizationOfUnsecuredLines": [0.05 + i * 0.01 for i in index],
            # Inside [18, 110] (§5).
            "age": [25 + i for i in index],
            "NumberOfTime30-59DaysPastDueNotWorse": [i % 3 for i in index],
            # <= 2, the model-boundary ceiling (§9).
            "DebtRatio": [0.20 + i * 0.01 for i in index],
            # Present and > 0, so the frame clears the model boundary too (§3).
            "MonthlyIncome": [3000.0 + i * 100 for i in index],
            "NumberOfOpenCreditLinesAndLoans": [5 + i % 7 for i in index],
            "NumberOfTimes90DaysLate": [i % 2 for i in index],
            "NumberRealEstateLoansOrLines": [i % 3 for i in index],
            "NumberOfTime60-89DaysPastDueNotWorse": [(i + 1) % 2 for i in index],
            "NumberOfDependents": [float(i % 4) for i in index],
        }
    )
    return frame.astype(_DTYPES)[list(EXPECTED_COLUMNS)]


def make_random_frame(
    n_rows: int = 300,
    n_positive: int = 20,
    seed: int = 20260920,
) -> pd.DataFrame:
    """A larger contract-valid frame, drawn from seeded continuous ranges.

    :func:`make_frame` walks its values with the row index, which keeps small
    frames readable but stops being valid past ~90 rows — the age would leave
    the plausible range and the utilisation would cross 1. Anything that needs
    hundreds of rows (a stratified split, a preprocessing pass) uses this
    instead: every column is drawn inside the contract's bounds, and the
    continuous columns make exact duplicate rows effectively impossible, so a
    test can add one deliberately and know it is the only one.
    """
    rng = np.random.default_rng(seed)
    labels = np.array([POSITIVE_LABEL] * n_positive + ["0"] * (n_rows - n_positive))
    rng.shuffle(labels)
    frame = pd.DataFrame(
        {
            TARGET_COLUMN: pd.Categorical(
                labels, categories=sorted(TARGET_LABELS), ordered=True
            ),
            # Strictly inside [0, 1] (§8) and continuous, so rows stay distinct.
            "RevolvingUtilizationOfUnsecuredLines": rng.uniform(0.0, 0.99, n_rows),
            "age": rng.integers(21, 90, n_rows),
            "NumberOfTime30-59DaysPastDueNotWorse": rng.integers(0, 4, n_rows),
            # Strictly below the model contract's ceiling of 2 (§9).
            "DebtRatio": rng.uniform(0.01, 1.9, n_rows),
            # Strictly positive, so the raw frame has no missing-income rows
            # until a test creates them.
            "MonthlyIncome": rng.uniform(1200.0, 20000.0, n_rows),
            "NumberOfOpenCreditLinesAndLoans": rng.integers(1, 20, n_rows),
            "NumberOfTimes90DaysLate": rng.integers(0, 3, n_rows),
            "NumberRealEstateLoansOrLines": rng.integers(0, 4, n_rows),
            "NumberOfTime60-89DaysPastDueNotWorse": rng.integers(0, 3, n_rows),
            "NumberOfDependents": rng.integers(0, 5, n_rows).astype("float64"),
        }
    )
    return frame.astype(_DTYPES)[list(EXPECTED_COLUMNS)]
