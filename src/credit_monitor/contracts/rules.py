"""The rule predicates, defined once and attached to several contracts.

Pandera expresses a rule as a decorated method on a schema class, and a rule
that belongs to more than one contract would therefore be written more than
once. The predicates live here instead: each schema keeps the decorator and the
method name — which **is** the rule name, and what the severity mapping keys on
— while the logic exists in exactly one place.

That matters because the contracts are now split by **when the data exists**.
At scoring time there is no outcome: a features-only batch has no target
column, so a contract that required one would reject every batch the monitor
actually sees. The label-dependent rules move to a separate contract that only
applies once the outcome arrives, months later.

Every threshold still comes from ``reports/inspection.md``; the citations stay
with the schema docstrings, where a reviewer reading the contract finds them.
"""

from typing import Final

import pandas as pd

from credit_monitor.constants import TARGET_COLUMN

# --------------------------------------------------------------------------
# Contract parameters
#
# Thresholds live here as named constants so the schema reads as prose and the
# tests can assert against the same numbers the rules use.
# --------------------------------------------------------------------------

# Inspection §5: 1 row has age 0 and 1 row is below 18, while the maximum
# observed is 109 with 13 rows above 100. 109 is an old but real borrower, 0 is
# not a borrower at all, so the floor is the legal-capacity age and the ceiling
# sits just above the observed maximum rather than at it.
AGE_MIN: Final[int] = 18
AGE_MAX: Final[int] = 110

# Inspection §6: 96 and 98 appear in all three delinquency counters, 5 and 264
# rows respectively, and always in the three columns at once (269 rows, 0.18%).
# Those rows default at 54.65% against 6.60% elsewhere, and the largest value
# below the cut is 13 / 17 / 11 depending on the column. A jump from 17 to 96
# with nothing in between, carrying its own default rate, is a code — an
# administrative status recorded in a count column — not a count.
DELINQUENCY_SENTINELS: Final[frozenset[int]] = frozenset({96, 98})

# Inspection §8: 3,321 rows exceed 1 (2.21%) but only 241 exceed 10 (0.16%),
# with a maximum of 50,708. The 99th percentile is 1.093 (§4), so the mass just
# above the line is plausible over-limit usage; the tail is not.
REVOLVING_UTILIZATION_MAX: Final[float] = 1.0

# Inspection §2: the observed positive rate is 6.68% (10,026 of 150,000). The
# band is asymmetric-tolerant around it rather than tight: the point is to catch
# a batch that is not this population, not to re-detect sampling noise.
POSITIVE_RATE_MIN: Final[float] = 0.05
POSITIVE_RATE_MAX: Final[float] = 0.09

# Inspection §2 reports the target dtype as `category`. The parquet written by
# credit_monitor.data.download round-trips it as an *ordered categorical of the
# strings* "0"/"1" — the report's descriptive statistics converted it to int for
# display, the file on disk did not change. The contract describes the file, so
# the labels are strings here; a schema written against integers would fail on
# the very artefact it is meant to guard.
TARGET_LABELS: Final[frozenset[str]] = frozenset({"0", "1"})
POSITIVE_LABEL: Final[str] = "1"


def age_in_range(age: pd.Series) -> pd.Series:
    """Age within [18, 110] — inspection §5 found 1 row at 0, maximum 109."""
    return (age >= AGE_MIN) & (age <= AGE_MAX)


def no_delinquency_sentinel(counter: pd.Series) -> pd.Series:
    """No administrative code 96/98 — inspection §6, 269 rows, 54.65% default."""
    return ~counter.isin(DELINQUENCY_SENTINELS)


def income_non_negative(income: pd.Series) -> pd.Series:
    """Income at or above zero — inspection §4 observed minimum is 0."""
    return income >= 0


def dependents_non_negative(dependents: pd.Series) -> pd.Series:
    """Dependants at or above zero — inspection §4 range is 0 to 20."""
    return dependents >= 0


def utilization_at_most_one(utilization: pd.Series) -> pd.Series:
    """Utilisation at or below 1 — inspection §8, a warning-level rule."""
    return utilization <= REVOLVING_UTILIZATION_MAX


def target_is_binary(target: pd.Series) -> pd.Series:
    """Only the labels 0 and 1 — inspection §2 observed nothing else."""
    return target.isin(TARGET_LABELS)


def nested_missingness_holds(frame: pd.DataFrame) -> pd.Series:
    """Dependants-null implies income-null — inspection §3, exact containment."""
    return ~(frame["NumberOfDependents"].isna() & frame["MonthlyIncome"].notna())


def rows_are_unique(frame: pd.DataFrame) -> pd.Series:
    """No exact duplicate row — inspection §7 found 609, keep="first"."""
    return ~frame.duplicated(keep="first")


def positive_rate_in_band(frame: pd.DataFrame) -> bool:
    """Positive rate within [5%, 9%] — inspection §2 measured 6.68%.

    A batch statistic, and one that needs the outcome, which is why it belongs
    to the label-arrival contract and not to the scoring-time one.
    """
    rate = (frame[TARGET_COLUMN] == POSITIVE_LABEL).mean()
    return bool(POSITIVE_RATE_MIN <= rate <= POSITIVE_RATE_MAX)
