"""Reading a pandera failure report through the severity policy.

The bridge between what pandera reports and what the contract says it means.
It lives in the contracts package rather than in the pipeline because
resolving a check name to a rule is contract vocabulary, and there is now more
than one consumer: the ingestion gate decides whether to quarantine a batch,
and the preprocessing step decides whether a cleaned frame may go to a model.
Neither should own the translation.

Kept separate from :mod:`credit_monitor.contracts.severity`, which is
deliberately pandera-free — that module is the policy as plain data, and this
one is the part that has to know how pandera names things.
"""

import logging

import pandas as pd
import pandera.pandas as pa

from credit_monitor.contracts.severity import RULE_SEVERITY, Rule, Severity

log = logging.getLogger(__name__)

# Pandera's own check names for the structural half of the contract. They are
# not rules anyone wrote, so they are not in RULE_SEVERITY; they are all
# manifestations of the same one, `schema_columns_and_dtypes`. Listed
# explicitly rather than pattern-matched so that a name pandera adds in a
# future version raises instead of being quietly folded into this bucket.
_STRUCTURAL_CHECK_NAMES: frozenset[str] = frozenset(
    {
        "column_in_schema",  # a column the contract does not declare (strict)
        "column_in_dataframe",  # a declared column missing from the batch
        "column_ordered",  # declared columns delivered in another order
        "column_name_type",  # a non-string column label
        "not_nullable",  # a null in a column declared non-nullable
        "field_uniqueness",  # unique=True on a field
        "multiple_fields_uniqueness",  # Config.unique across fields
    }
)

# Parameterised names: pandera renders the dtype into the check name, so
# `dtype('uint8')` cannot be matched by equality.
_STRUCTURAL_CHECK_PREFIXES: tuple[str, ...] = ("dtype(", "coerce_dtype(")


class UnknownRuleError(LookupError):
    """A failed check reported under a name no rule claims.

    Raised instead of defaulting to a severity, because the two available
    defaults are both wrong: warning ships unexamined data to the model, and
    blocker hides the fact that the mapping and the schema have drifted apart.
    """


def resolve_rule(check_name: str) -> Rule:
    """Map the name pandera reported a failure under back to a rule.

    Args:
        check_name: The ``check`` value from ``SchemaErrors.failure_cases``.

    Returns:
        The rule that failed.

    Raises:
        UnknownRuleError: The name is neither a rule nor a known structural
            check of pandera's own.
    """
    try:
        return Rule(check_name)
    except ValueError:
        pass
    if check_name in _STRUCTURAL_CHECK_NAMES or check_name.startswith(
        _STRUCTURAL_CHECK_PREFIXES
    ):
        return Rule.SCHEMA_COLUMNS_AND_DTYPES
    raise UnknownRuleError(
        f"check {check_name!r} maps to no rule in RULE_SEVERITY and is not a "
        "known pandera structural check. Either it was added to a schema "
        "without being classified in credit_monitor.contracts.severity, or a "
        "check method was renamed and its rule name went with it."
    )


def registered_rule_names(schema: type[pa.DataFrameModel]) -> set[str]:
    """Return the name every named check of a schema will report under."""
    built = schema.to_schema()
    names = {
        str(check.name) for column in built.columns.values() for check in column.checks
    }
    return names | {str(check.name) for check in built.checks}


def assert_rules_are_classified(schema: type[pa.DataFrameModel]) -> None:
    """Fail before reading data if any of the schema's checks has no severity.

    The preflight that makes the strict lookup usable. Without it the pipeline
    would discover an unclassified rule only when some batch happened to
    violate it — possibly months later, possibly in production, and only on the
    path where it matters.

    Args:
        schema: The contract about to be applied.

    Raises:
        UnknownRuleError: At least one registered check has no severity.
    """
    classified = {rule.value for rule in RULE_SEVERITY}
    unclassified = sorted(registered_rule_names(schema) - classified)
    if unclassified:
        raise UnknownRuleError(
            f"{schema.__name__} registers checks with no severity: "
            f"{', '.join(unclassified)}. Classify them in "
            "credit_monitor.contracts.severity before ingesting anything."
        )
    log.info(
        "contract.rules_verified",
        extra={"schema": schema.__name__, "rules": len(classified)},
    )


def blocking_failures(errors: pa.errors.SchemaErrors) -> pd.DataFrame:
    """Return only the failure cases whose rule is a blocker.

    Pandera raises on any failed check, which flattens a severity policy the
    contract went to the trouble of stating: ``no_duplicate_rows`` and
    ``revolving_utilization_at_most_one`` are warnings *because* the contract
    argues those rows may be genuine and must not be thrown away. A caller
    that treats every pandera failure as fatal has silently overridden that.

    Args:
        errors: The exception raised by a lazy validation.

    Returns:
        The subset of ``failure_cases`` carrying blocker-level rules. Empty
        means the frame violated nothing that should stop a pipeline.
    """
    cases = errors.failure_cases.copy()
    cases["rule"] = [resolve_rule(str(name)).value for name in cases["check"]]
    cases["severity"] = [RULE_SEVERITY[Rule(rule)].value for rule in cases["rule"]]
    return cases[cases["severity"] == Severity.BLOCKER.value]
