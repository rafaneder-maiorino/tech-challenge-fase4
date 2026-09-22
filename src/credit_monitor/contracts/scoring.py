"""Contracts split by when the data exists: scoring time and label arrival.

The stage-1 contracts assumed every batch carried its outcome, because the
ingestion job it was written for reads a historical file where it does. The
monitor does not: at scoring time the outcome has not happened yet, and a batch
of features has no target column at all. A contract that required one would
reject every batch the monitor actually sees — and the two label-dependent
rules (``target_binary``, ``positive_rate_band``) would be evaluated against a
column that does not exist.

So the split follows the data's arrival, not its shape:

``ScoringInputSchema``
    The ten feature columns, **target-free**. Everything that can be checked
    the moment a batch arrives for scoring: ranges, sentinels, the nested
    missingness relationship, duplicates.

``LabelArrivalSchema``
    The outcome column, checked months later when it lands. Carries the two
    rules that need it, and nothing else.

Rule names are unchanged, so the severity mapping still covers every rule
exactly once — the same thirteen entries classify both halves. The predicates
come from :mod:`credit_monitor.contracts.rules`, so no rule is implemented
twice.
"""

from typing import Final

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Category, Float64, Series, UInt8

from credit_monitor.constants import DELINQUENCY_COLUMNS, TARGET_COLUMN
from credit_monitor.contracts import rules
from credit_monitor.contracts.raw import EXPECTED_COLUMNS

# The ten contract columns a scoring batch carries, in the order the reference
# parquet stores them minus the target.
SCORING_COLUMNS: Final[tuple[str, ...]] = tuple(
    column for column in EXPECTED_COLUMNS if column != TARGET_COLUMN
)


class ScoringInputSchema(pa.DataFrameModel):
    """The ten feature columns as a scoring batch delivers them, target-free.

    ``strict`` is deliberately **not** set here, unlike the stage-1 contracts.
    A scoring batch legitimately carries columns the contract does not own —
    ``row_id`` to join the three files back together, ``income_missing`` from
    preprocessing — and rejecting those would be rejecting the pipeline's own
    plumbing. What is enforced is that the ten contract columns are present,
    correctly typed, and within their documented ranges; ``ordered`` is off for
    the same reason.
    """

    revolving_utilization_of_unsecured_lines: Series[Float64] = pa.Field(
        alias="RevolvingUtilizationOfUnsecuredLines"
    )
    age: Series[UInt8]
    number_of_time_30_59_days_past_due_not_worse: Series[UInt8] = pa.Field(
        alias="NumberOfTime30-59DaysPastDueNotWorse"
    )
    debt_ratio: Series[Float64] = pa.Field(alias="DebtRatio")
    monthly_income: Series[Float64] = pa.Field(alias="MonthlyIncome", nullable=True)
    number_of_open_credit_lines_and_loans: Series[UInt8] = pa.Field(
        alias="NumberOfOpenCreditLinesAndLoans"
    )
    number_of_times_90_days_late: Series[UInt8] = pa.Field(
        alias="NumberOfTimes90DaysLate"
    )
    number_real_estate_loans_or_lines: Series[UInt8] = pa.Field(
        alias="NumberRealEstateLoansOrLines"
    )
    number_of_time_60_89_days_past_due_not_worse: Series[UInt8] = pa.Field(
        alias="NumberOfTime60-89DaysPastDueNotWorse"
    )
    number_of_dependents: Series[Float64] = pa.Field(
        alias="NumberOfDependents", nullable=True
    )

    class Config:
        """Not strict and not ordered — see the class docstring."""

        name = "ScoringInputSchema"
        strict = False
        ordered = False
        coerce = False

    # The method name is the rule name and no `name=` is passed — see the long
    # comment in raw.py for why passing one would break inherited rules.

    @pa.check("age", description="idade em [18, 110] (inspeção §5)")
    def age_range(cls, age: Series[UInt8]) -> Series[bool]:
        """Reject ages outside [18, 110] (inspection §5)."""
        return rules.age_in_range(age)

    @pa.check(*DELINQUENCY_COLUMNS, description="códigos 96/98 recusados (inspeção §6)")
    def delinquency_sentinels(cls, counter: Series[UInt8]) -> Series[bool]:
        """Reject the 96/98 administrative codes (inspection §6)."""
        return rules.no_delinquency_sentinel(counter)

    @pa.check("MonthlyIncome", description="renda >= 0 (inspeção §4)")
    def monthly_income_non_negative(cls, income: Series[Float64]) -> Series[bool]:
        """Reject negative income (inspection §4)."""
        return rules.income_non_negative(income)

    @pa.check("NumberOfDependents", description="dependentes >= 0 (inspeção §4)")
    def dependents_non_negative(cls, dependents: Series[Float64]) -> Series[bool]:
        """Reject a negative dependant count (inspection §4)."""
        return rules.dependents_non_negative(dependents)

    @pa.check(
        "RevolvingUtilizationOfUnsecuredLines",
        description="utilização <= 1 (inspeção §8, alerta)",
    )
    def revolving_utilization_at_most_one(
        cls, utilization: Series[Float64]
    ) -> Series[bool]:
        """Flag utilisation above 1 (inspection §8)."""
        return rules.utilization_at_most_one(utilization)

    @pa.dataframe_check(description="dependentes nulos implicam renda nula (§3)")
    def nested_missingness(cls, df: pd.DataFrame) -> Series[bool]:
        """Reject a dependants-null row whose income is present (§3)."""
        return rules.nested_missingness_holds(df)

    @pa.dataframe_check(description="sem linhas duplicadas exatas (§7, alerta)")
    def no_duplicate_rows(cls, df: pd.DataFrame) -> Series[bool]:
        """Flag repeated rows (inspection §7).

        Compared over the contract columns only. A scoring batch carries a
        ``row_id`` that is unique by construction, so including it would make
        every row unique and the rule would never fire.
        """
        return rules.rows_are_unique(df[list(SCORING_COLUMNS)])


class LabelArrivalSchema(pa.DataFrameModel):
    """The outcome column, checked when it arrives — months after scoring.

    Two rules and nothing else, because only two of the thirteen need the
    outcome. Keeping them here rather than in the scoring contract is what lets
    a features-only batch validate at all, and it also makes the label delay
    visible in the code: this schema is applied by a different job, at a
    different time.
    """

    serious_dlqin_2yrs: Series[Category] = pa.Field(alias=TARGET_COLUMN)

    class Config:
        """Not strict: the label file carries ``row_id`` to join on."""

        name = "LabelArrivalSchema"
        strict = False
        ordered = False
        coerce = False

    @pa.check(TARGET_COLUMN, description="alvo em {0, 1} (inspeção §2)")
    def target_binary(cls, target: Series[Category]) -> Series[bool]:
        """Reject any label other than 0/1 (inspection §2)."""
        return rules.target_is_binary(target)

    @pa.dataframe_check(description="taxa de positivos em [5%, 9%] (§2, alerta)")
    def positive_rate_band(cls, df: pd.DataFrame) -> bool:
        """Flag a batch whose positive rate leaves [5%, 9%] (inspection §2)."""
        return rules.positive_rate_in_band(df)
