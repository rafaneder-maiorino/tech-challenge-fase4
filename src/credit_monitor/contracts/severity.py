"""Rule registry and the blocker/warning classification of every contract rule.

Pandera itself has no notion of severity: a check either passes or it raises.
This module supplies the missing layer as plain data, so the ingestion script of
stage 2 can read a :class:`pandera.errors.SchemaErrors` report, group the
failures by rule and decide *per failure* whether the batch is refused
(``BLOCKER``) or accepted with an alert (``WARNING``). Nothing here imports
pandera, and nothing here validates: it is a lookup table on purpose, so the
policy can be reviewed without reading a schema.

The classification principle is the distinction between *impossible* and merely
*suspicious*:

* A ``BLOCKER`` is a value or structure that cannot be true of a credit
  applicant, or that would silently change the meaning of a feature. Letting it
  through corrupts the model input.
* A ``WARNING`` is a value that is unusual but may be genuine. Refusing the
  batch would throw away real customers; the right response is to record it and
  look.

Rule names double as the check names registered in the two schemas, which is
what makes the mapping usable against a pandera failure report: the ``check``
column of ``SchemaErrors.failure_cases`` carries exactly these strings.
"""

from enum import StrEnum
from types import MappingProxyType
from typing import Final


class Severity(StrEnum):
    """What the ingestion stage must do when a rule fails."""

    BLOCKER = "blocker"
    """Refuse the batch. The data cannot be trusted as model input."""

    WARNING = "warning"
    """Accept the batch and raise an alert. The data is odd, not impossible."""


class Rule(StrEnum):
    """Every rule implemented by the two schemas, by the name it reports under.

    A member's value is the name of the method that implements the rule in
    ``raw.py`` or ``model.py`` — pandera reports a failed check under that name
    — so a failure report maps back to a rule without string literals
    scattered across the codebase. The one exception is
    ``SCHEMA_COLUMNS_AND_DTYPES``, which no method implements: it is enforced
    by ``strict``/``ordered``/dtype declarations and reported by pandera under
    its own names (``column_in_schema``, ``column_ordered``, ``dtype(...)``),
    which :func:`severity_for` resolves through its fail-closed default.

    ``tests/test_contracts.py`` asserts that this registry and the rules the
    two schemas actually implement are the same set, in both directions.
    """

    # --- structural: enforced by schema declarations, not by a Check ---------
    SCHEMA_COLUMNS_AND_DTYPES = "schema_columns_and_dtypes"
    """``strict=True``, ``ordered=True`` and the per-column dtype annotations."""

    # --- raw-level value rules ----------------------------------------------
    AGE_RANGE = "age_range"
    DELINQUENCY_SENTINELS = "delinquency_sentinels"
    MONTHLY_INCOME_NON_NEGATIVE = "monthly_income_non_negative"
    DEPENDENTS_NON_NEGATIVE = "dependents_non_negative"
    TARGET_BINARY = "target_binary"
    REVOLVING_UTILIZATION_AT_MOST_ONE = "revolving_utilization_at_most_one"

    # --- raw-level batch rules (need the whole frame) ------------------------
    NESTED_MISSINGNESS = "nested_missingness"
    NO_DUPLICATE_ROWS = "no_duplicate_rows"
    POSITIVE_RATE_BAND = "positive_rate_band"

    # --- model-boundary rules ------------------------------------------------
    MONTHLY_INCOME_PRESENT = "monthly_income_present"
    DEBT_RATIO_UNIT_GUARD = "debt_ratio_unit_guard"
    NO_NULLS_IN_FEATURES = "no_nulls_in_features"


# --------------------------------------------------------------------------
# The policy
#
# Read-only (MappingProxyType) because this is a contract, not configuration:
# a caller that wants a different policy should change this file in a commit
# that a reviewer sees, not mutate the dict at runtime.
# --------------------------------------------------------------------------
RULE_SEVERITY: Final[MappingProxyType[Rule, Severity]] = MappingProxyType(
    {
        # Structure first: if the columns, their order or their dtypes moved,
        # every rule below is being evaluated against something other than the
        # dataset the model was trained on.
        Rule.SCHEMA_COLUMNS_AND_DTYPES: Severity.BLOCKER,
        # 1 row has age 0 and 1 row is under 18 (inspection §5): not a plausible
        # credit applicant, and age correlates with the target (-0.12, §10).
        Rule.AGE_RANGE: Severity.BLOCKER,
        # 96/98 are codes, not counts (inspection §6). Read as counts they
        # inflate a delinquency counter by ~7x its real maximum (98 vs 13).
        Rule.DELINQUENCY_SENTINELS: Severity.BLOCKER,
        # Negative income or a negative dependent count is impossible, not rare:
        # the observed minimum of both is 0 (inspection §4).
        Rule.MONTHLY_INCOME_NON_NEGATIVE: Severity.BLOCKER,
        Rule.DEPENDENTS_NON_NEGATIVE: Severity.BLOCKER,
        # A third label in a binary target means the label definition changed;
        # only 0 and 1 are observed, 93.32%/6.68% (inspection §2).
        Rule.TARGET_BINARY: Severity.BLOCKER,
        # Dependents-null is a strict subset of income-null for 100% of rows
        # today (3,924 of 29,731, inspection §3). If it breaks, the two columns
        # are no longer missing for the same reason and the imputation strategy
        # built on that assumption is invalid.
        Rule.NESTED_MISSINGNESS: Severity.BLOCKER,
        # Income must exist at the model boundary; 19.82% of raw rows lack it
        # (inspection §3) and that is handled upstream of this schema.
        Rule.MONTHLY_INCOME_PRESENT: Severity.BLOCKER,
        # DebtRatio median 1,159 without income vs 0.296 with income
        # (inspection §9): the column changes unit depending on another column.
        Rule.DEBT_RATIO_UNIT_GUARD: Severity.BLOCKER,
        # A null reaching the model is a crash or a silent zero, never a datum.
        Rule.NO_NULLS_IN_FEATURES: Severity.BLOCKER,
        # 609 exact duplicates, 0.41% (inspection §7). A WARNING on purpose:
        # the dataset has no identifier column (§7 searched for id, index,
        # row_id, rowid, unnamed: 0 and found none), so an identical row can be
        # the same customer ingested twice OR two customers whose 11 recorded
        # attributes happen to coincide. We cannot tell the two apart from the
        # data, and dropping a real customer is as wrong as keeping a duplicate,
        # so the decision is escalated to a human instead of guessed here.
        Rule.NO_DUPLICATE_ROWS: Severity.WARNING,
        # 3,321 rows above 1 (2.21%) but only 241 above 10 (0.16%), max 50,708
        # (inspection §8). Utilisation above 100% is genuinely possible (fees,
        # over-limit spending), and the bulk sits just above the line: 1.093 is
        # already the 99th percentile (§4). Suspicious, not impossible.
        Rule.REVOLVING_UTILIZATION_AT_MOST_ONE: Severity.WARNING,
        # Reference positive rate 6.68% (inspection §2). A drifting rate is a
        # population change to investigate, not a malformed batch — and on a
        # small batch the statistic is noisy by construction.
        Rule.POSITIVE_RATE_BAND: Severity.WARNING,
    }
)

BLOCKER_RULES: Final[frozenset[Rule]] = frozenset(
    rule for rule, severity in RULE_SEVERITY.items() if severity is Severity.BLOCKER
)
WARNING_RULES: Final[frozenset[Rule]] = frozenset(
    rule for rule, severity in RULE_SEVERITY.items() if severity is Severity.WARNING
)


def severity_for(check_name: str) -> Severity:
    """Return the severity of a failed check, by the name it reported under.

    Fails closed: a name that is not a known rule — pandera's own structural
    check names (``column_in_schema``, ``not_nullable``, ``dtype('uint8')``) or
    a rule added to a schema without being classified here — is treated as a
    ``BLOCKER``. An unclassified failure is an unknown failure, and an unknown
    failure must not reach the model unexamined.

    Args:
        check_name: The ``check`` value from ``SchemaErrors.failure_cases``.

    Returns:
        The configured severity, or ``Severity.BLOCKER`` when unrecognised.
    """
    try:
        rule = Rule(check_name)
    except ValueError:
        return Severity.BLOCKER
    return RULE_SEVERITY[rule]
