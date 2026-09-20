"""Data contracts for the credit dataset.

Two schemas, deliberately not one:

``RawCreditSchema``
    What the ingestion stage is allowed to accept. It describes the dataset as
    it actually arrives, including its documented gaps.

``ModelInputSchema``
    What the model is allowed to be given. Same rules, tightened where the raw
    data is merely *survivable* rather than *usable*.

The split exists because a single schema would have to choose one answer for a
question that has two. The clearest case is ``MonthlyIncome``: it is null in
29,731 of 150,000 rows (19.82%, inspection report §3). A raw schema that
rejected null income would refuse a fifth of the real source on arrival, which
is not a data-quality failure — it is the source. A model schema that accepted
it would hand the estimator a null and get a crash, a silent zero, or an
imputed value that nobody declared. So null income is *valid raw data* and
*invalid model input*, and those are two contracts.

``DebtRatio`` is the same argument with a sharper edge: its median is 1,159 when
income is missing and 0.296 when income is present (§9). The column is not
noisy, it is carrying a different unit in the two groups, so a range rule on it
is only meaningful once income is known to exist — i.e. only at the model
boundary.

Severity (blocker vs warning) is not a property of either schema; it lives in
:mod:`credit_monitor.contracts.severity` and is applied by the caller.
"""

from credit_monitor.contracts.model import ModelInputSchema
from credit_monitor.contracts.raw import RawCreditSchema
from credit_monitor.contracts.severity import (
    BLOCKER_RULES,
    RULE_SEVERITY,
    WARNING_RULES,
    Rule,
    Severity,
    severity_for,
)

__all__ = [
    "BLOCKER_RULES",
    "RULE_SEVERITY",
    "WARNING_RULES",
    "ModelInputSchema",
    "RawCreditSchema",
    "Rule",
    "Severity",
    "severity_for",
]
