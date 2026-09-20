"""Shared scaffolding for the test suite.

One place for the hand-built batch factory, because both the contract tests and
the ingestion tests need a frame that satisfies the contract and neither may
read ``data/raw/gmsc.parquet``. The real file is the thing the contract is
*about*: a test that validated it would assert today's data rather than the
rule, would fail the moment the source legitimately changed, and would need the
dataset present to run at all.
"""

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
