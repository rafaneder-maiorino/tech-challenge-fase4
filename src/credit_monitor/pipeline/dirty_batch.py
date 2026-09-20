"""Build a batch with a known, counted set of defects in it.

A validation gate that has never been shown failing is a gate nobody trusts.
This module manufactures the failure on purpose: it takes a batch that is clean
under the raw contract and injects a fixed number of violations of each rule,
returning a manifest that says exactly how many of each it put in. The
validation report is then checkable against that manifest — if the report says
four duplicates and the manifest says four, the counting is right; if they
disagree, one of the two is lying and the test says which.

The injection is deliberately separate from the sampling.
``scripts/make_dirty_batch.py`` does the sampling, from the real parquet,
seeded. :func:`inject_defects` works on any clean frame, which is what lets the
tests drive it with a hand-built dozen rows and never touch
``data/raw/gmsc.parquet``.

The defect that matters most here is ``nested_missingness``: five rows whose
``NumberOfDependents`` is null while ``MonthlyIncome`` is present. The
inspection report found that pattern in zero of 150,000 rows, which is exactly
why it is worth injecting — it is the one rule whose failure has never been
observed, no column-wise validator would catch it, and an untested rule is a
comment with a function signature.
"""

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from credit_monitor.constants import DELINQUENCY_COLUMNS, TARGET_COLUMN
from credit_monitor.contracts.raw import (
    AGE_MAX,
    AGE_MIN,
    DELINQUENCY_SENTINELS,
    POSITIVE_LABEL,
    REVOLVING_UTILIZATION_MAX,
)
from credit_monitor.contracts.severity import Rule

# --------------------------------------------------------------------------
# How many rows carry each defect, and what is written into them.
#
# One defect per rule, disjoint rows, so every count in the manifest is a count
# of rows that violate exactly one thing. A row carrying two defects would make
# the per-rule totals add up to more than the number of bad rows and the
# manifest would stop being a statement anyone could check.
# --------------------------------------------------------------------------
AGE_DEFECT_ROWS: int = 3
SENTINEL_DEFECT_ROWS: int = 2
NESTED_MISSING_DEFECT_ROWS: int = 5
NEGATIVE_INCOME_DEFECT_ROWS: int = 2
UTILIZATION_DEFECT_ROWS: int = 3
DUPLICATE_DEFECT_ROWS: int = 4

# Values chosen to be unmistakably wrong rather than borderline: a demo that
# hinges on whether 17.5 rounds the right way proves nothing.
INJECTED_AGE: int = 12
INJECTED_SENTINEL: int = 98
INJECTED_NEGATIVE_INCOME: float = -1500.0
INJECTED_UTILIZATION: tuple[float, ...] = (7.8, 8.0, 8.2)

# The sentinel goes into one counter, not all three. The real data carries the
# codes in the three columns at once (inspection §6), so injecting into one is
# the harder case: it proves the rule is per-column and does not need the
# triple co-occurrence to notice.
SENTINEL_COLUMN: str = "NumberOfTimes90DaysLate"

# Rows needed before any duplication. The duplicate sources are taken from
# rows that carry no other defect, so their copies violate the duplicate rule
# and nothing else.
MIN_BASE_ROWS: int = (
    AGE_DEFECT_ROWS
    + SENTINEL_DEFECT_ROWS
    + NESTED_MISSING_DEFECT_ROWS
    + NEGATIVE_INCOME_DEFECT_ROWS
    + UTILIZATION_DEFECT_ROWS
    + DUPLICATE_DEFECT_ROWS
)


@dataclass(frozen=True, slots=True)
class DefectManifest:
    """What was injected, so the validation report can be checked against it."""

    rows_in_base: int
    rows_out: int
    include_blockers: bool
    injected: dict[str, int] = field(default_factory=dict)
    """Rule name -> number of rows expected to violate it.

    Keyed by ``Rule`` values, the same strings the validation report uses, so
    comparing the two is a dict comparison and not a translation exercise.
    """

    def to_dict(self) -> dict[str, Any]:
        """Render the manifest as the JSON document written beside the batch."""
        return {
            "rows_in_base": self.rows_in_base,
            "rows_out": self.rows_out,
            "include_blockers": self.include_blockers,
            "injected": dict(sorted(self.injected.items())),
        }


def is_clean_under_contract(frame: pd.DataFrame) -> pd.Series:
    """Return a mask of rows that violate no row-level rule of the raw contract.

    The thresholds come from :mod:`credit_monitor.contracts.raw` rather than
    being restated, so a contract that moves takes this sampler with it. Both
    nullable columns are required to be present: the base has to be clean
    *before* injection, and a row that already has a null income would make the
    nested-missingness defect uncountable.
    """
    sentinels = frame[list(DELINQUENCY_COLUMNS)].isin(DELINQUENCY_SENTINELS)
    return (
        frame["age"].between(AGE_MIN, AGE_MAX)
        & ~sentinels.any(axis="columns")
        & frame["MonthlyIncome"].notna()
        & (frame["MonthlyIncome"] >= 0)
        & frame["NumberOfDependents"].notna()
        & (frame["NumberOfDependents"] >= 0)
        & (frame["RevolvingUtilizationOfUnsecuredLines"] <= REVOLVING_UTILIZATION_MAX)
    )


def sample_clean_base(
    frame: pd.DataFrame,
    n_rows: int,
    seed: int,
    positive_rate: float = 0.0668,
) -> pd.DataFrame:
    """Draw a seeded, stratified sample of rows that pass the raw contract.

    Stratified rather than uniform, and that is not fussiness. At 200 rows a
    uniform draw from a 6.68% positive population has a standard deviation of
    1.8 points, so roughly one seed in three lands outside the contract's
    [5%, 9%] band and the demo batch trips ``positive_rate_band`` by accident —
    an unplanned defect that the manifest does not mention and the report then
    appears to invent. Fixing the positive count removes the coincidence.

    Args:
        frame: Source data, typically the raw parquet.
        n_rows: Size of the sample.
        seed: Fixed for reproducibility; the same seed gives the same batch.
        positive_rate: Target share of the positive class (inspection §2).

    Returns:
        A positionally indexed frame of ``n_rows`` clean rows.

    Raises:
        ValueError: Not enough clean rows of one class to fill the sample.
    """
    clean = frame[is_clean_under_contract(frame)].drop_duplicates()
    n_positive = round(n_rows * positive_rate)
    n_negative = n_rows - n_positive

    positives = clean[clean[TARGET_COLUMN] == POSITIVE_LABEL]
    negatives = clean[clean[TARGET_COLUMN] != POSITIVE_LABEL]
    if len(positives) < n_positive or len(negatives) < n_negative:
        raise ValueError(
            f"not enough clean rows to sample {n_rows}: need {n_positive} "
            f"positive and {n_negative} negative, have {len(positives)} and "
            f"{len(negatives)}"
        )

    sample = pd.concat(
        [
            positives.sample(n=n_positive, random_state=seed),
            negatives.sample(n=n_negative, random_state=seed),
        ]
    )
    # Shuffled so the positives are not all at the front, which would make the
    # defect rows below systematically one class.
    return sample.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def inject_defects(
    base: pd.DataFrame,
    include_blockers: bool = True,
) -> tuple[pd.DataFrame, DefectManifest]:
    """Inject a counted set of contract violations into a clean batch.

    Args:
        base: A frame that passes the raw contract, positionally indexed.
        include_blockers: When false, only the two warning-level defects are
            injected. That is the clean-batch demo: a batch that trips alerts
            and is still accepted, which is what shows the gate discriminating
            rather than simply always failing.

    Returns:
        The defective batch and the manifest describing it.

    Raises:
        ValueError: ``base`` is too small to hold disjoint defect rows.
    """
    if len(base) < MIN_BASE_ROWS:
        raise ValueError(
            f"base needs at least {MIN_BASE_ROWS} rows to carry disjoint "
            f"defects, got {len(base)}"
        )

    frame = base.copy()
    injected: dict[str, int] = {}
    cursor = 0

    def take(count: int) -> list[int]:
        """Reserve the next `count` row positions, so no row gets two defects."""
        nonlocal cursor
        reserved = list(range(cursor, cursor + count))
        cursor += count
        return reserved

    if include_blockers:
        # age_range — inspection §5 found exactly one implausible row in the
        # real data; here there are three and we know which.
        frame.loc[take(AGE_DEFECT_ROWS), "age"] = INJECTED_AGE
        injected[Rule.AGE_RANGE.value] = AGE_DEFECT_ROWS

        # delinquency_sentinels — the 98 code (inspection §6).
        frame.loc[take(SENTINEL_DEFECT_ROWS), SENTINEL_COLUMN] = INJECTED_SENTINEL
        injected[Rule.DELINQUENCY_SENTINELS.value] = SENTINEL_DEFECT_ROWS

        # nested_missingness — the one rule with no observed failure anywhere
        # in the 150,000 rows, and the only one here that no per-column
        # validator could ever catch: every value in these rows is individually
        # legal, and it is the pairing that is wrong.
        frame.loc[take(NESTED_MISSING_DEFECT_ROWS), "NumberOfDependents"] = None
        injected[Rule.NESTED_MISSINGNESS.value] = NESTED_MISSING_DEFECT_ROWS

        # monthly_income_non_negative — inspection §4 puts the observed
        # minimum at 0, so a negative income is impossible rather than rare.
        frame.loc[take(NEGATIVE_INCOME_DEFECT_ROWS), "MonthlyIncome"] = (
            INJECTED_NEGATIVE_INCOME
        )
        injected[Rule.MONTHLY_INCOME_NON_NEGATIVE.value] = NEGATIVE_INCOME_DEFECT_ROWS

    # revolving_utilization_at_most_one — a warning: inspection §8 shows 2.21%
    # of the real rows above 1, so this must not stop a pipeline.
    utilization_rows = take(UTILIZATION_DEFECT_ROWS)
    frame.loc[utilization_rows, "RevolvingUtilizationOfUnsecuredLines"] = list(
        INJECTED_UTILIZATION[: len(utilization_rows)]
    )
    injected[Rule.REVOLVING_UTILIZATION_AT_MOST_ONE.value] = UTILIZATION_DEFECT_ROWS

    # no_duplicate_rows — also a warning, for the reason inspection §7 gives:
    # with no identifier column, a repeated row could be a repeated customer or
    # two customers who match on all 11 attributes. The copies are taken from
    # rows carrying no other defect, and appended, so `keep="first"` flags
    # exactly the copies.
    duplicate_sources = take(DUPLICATE_DEFECT_ROWS)
    frame = pd.concat([frame, frame.iloc[duplicate_sources]], ignore_index=True)
    injected[Rule.NO_DUPLICATE_ROWS.value] = DUPLICATE_DEFECT_ROWS

    if include_blockers:
        # schema_columns_and_dtypes — one structural defect, and the one that
        # proves `coerce` is still off in the contract: every age value stays
        # legal, only the storage type changes. A coercing schema would accept
        # this frame without a word.
        frame["age"] = frame["age"].astype("int64")
        injected[Rule.SCHEMA_COLUMNS_AND_DTYPES.value] = 1

    return frame, DefectManifest(
        rows_in_base=len(base),
        rows_out=len(frame),
        include_blockers=include_blockers,
        injected=injected,
    )
