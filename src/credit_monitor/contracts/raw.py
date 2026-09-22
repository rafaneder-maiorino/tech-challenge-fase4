"""Contract for the raw ingested credit data.

Every rule below is traceable to a number in ``reports/inspection.md``; the
section is cited in the docstring or comment that justifies it. Rules that the
report shows the *current* source already violating (age 0, the 96/98 codes,
utilisation above 1, duplicates) are still written here: the contract states
what the pipeline requires, and stage 2 decides — via
:mod:`credit_monitor.contracts.severity` — whether a violation refuses the
batch or only raises an alert.

Two deliberate design choices are worth reading before the code:

``coerce`` is off
    Dtypes are asserted, never repaired. The counters are ``uint8``, whose
    range is 0-255, so a value arriving as 300 would wrap silently to 44 under
    coercion — a fabricated, plausible-looking delinquency count. Likewise a
    null appearing in ``age`` would widen the column to ``float64``; we want
    that reported as a structural change to the source, not absorbed.

the three batch-level rules need a batch
    ``nested_missingness``, ``no_duplicate_rows`` and ``positive_rate_band``
    are statistics over the frame, not per-row predicates. On a handful of rows
    the positive-rate band in particular is noise: with 15 rows a single
    positive is already 6.67%, and one more is 13.3%. Stage 2 is expected to
    validate batches, not single records.
"""

from typing import Final

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Category, Float64, Series, UInt8

from credit_monitor.constants import DELINQUENCY_COLUMNS, TARGET_COLUMN
from credit_monitor.contracts import rules

# Re-exported so every existing `from ...contracts.raw import AGE_MIN` keeps
# working. The definitions moved to `rules`, which is where a threshold and
# the predicate that uses it belong together; this module is the schema.
from credit_monitor.contracts.rules import (  # noqa: F401
    AGE_MAX,
    AGE_MIN,
    DELINQUENCY_SENTINELS,
    POSITIVE_LABEL,
    POSITIVE_RATE_MAX,
    POSITIVE_RATE_MIN,
    REVOLVING_UTILIZATION_MAX,
    TARGET_LABELS,
)


class RawCreditSchema(pa.DataFrameModel):
    """The 11 columns of the raw dataset, in order, with their arrival dtypes.

    Column order and dtypes are those of ``data/raw/gmsc.parquet`` as recorded
    in inspection §1. ``strict`` refuses an unexpected column, ``ordered``
    refuses a reordered one: both are structural changes to the source that
    would otherwise be discovered downstream, by a model reading the wrong
    feature.

    Nullability follows §3 exactly: ``MonthlyIncome`` (19.82% null) and
    ``NumberOfDependents`` (2.62% null) are the only two columns with any
    missing value, so they are the only two declared nullable. A null anywhere
    else is a new failure mode.
    """

    serious_dlqin_2yrs: Series[Category] = pa.Field(alias=TARGET_COLUMN)
    revolving_utilization_of_unsecured_lines: Series[Float64] = pa.Field(
        alias="RevolvingUtilizationOfUnsecuredLines"
    )
    age: Series[UInt8]
    number_of_time_30_59_days_past_due_not_worse: Series[UInt8] = pa.Field(
        alias="NumberOfTime30-59DaysPastDueNotWorse"
    )
    debt_ratio: Series[Float64] = pa.Field(alias="DebtRatio")
    # Nullable at ingestion, not at the model boundary — see the package
    # docstring and ModelInputSchema.
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
        """Structural half of the contract, reported as `schema_columns_and_dtypes`."""

        name = "RawCreditSchema"
        strict = True
        ordered = True
        coerce = False

    # ----------------------------------------------------------------------
    # Column rules
    #
    # Naming convention, and it is load-bearing: **each method is named after
    # the Rule it implements**, and no `name=` is passed to @check. Pandera
    # reports a failed check under its name in the `check` column of
    # `failure_cases`, which is how stage 2 looks a failure up in
    # RULE_SEVERITY, so the two must agree.
    #
    # `name=` is avoided rather than merely unnecessary. Pandera builds a Check
    # by popping `name` out of the decorator's stored kwargs
    # (pandera.api.base.model_components.BaseCheckInfo.to_check), and a
    # subclass reuses the *same* stored kwargs object as its parent. The first
    # schema built consumes the name and every schema built afterwards falls
    # back to the method's __name__ — so ModelInputSchema would report its
    # inherited rules under different names than RawCreditSchema, silently,
    # depending on import order. Taking the __name__ path for all of them makes
    # the two schemas agree by construction.
    #
    # `error=` is likewise left unset: pandera puts `error` in the `check`
    # column when it is present, which would overwrite the rule name. The
    # human-facing explanation goes in `description`, which travels with the
    # schema without displacing anything.
    #
    # tests/test_contracts.py asserts that the resulting names are exactly the
    # keys of RULE_SEVERITY, in both directions.
    # ----------------------------------------------------------------------

    @pa.check(
        "age",
        description=(
            f"age must be within [{AGE_MIN}, {AGE_MAX}]; inspection §5 found "
            "1 row at 0 and a maximum of 109"
        ),
    )
    def age_range(cls, age: Series[UInt8]) -> Series[bool]:
        """Reject ages outside [18, 110] (inspection §5: 1 row at age 0)."""
        return rules.age_in_range(age)

    @pa.check(
        *DELINQUENCY_COLUMNS,
        description=(
            "96 and 98 are administrative codes, not counts; inspection §6 "
            "found 269 rows carrying them in all three counters at once, "
            "defaulting at 54.65% against 6.60% elsewhere"
        ),
    )
    def delinquency_sentinels(cls, counter: Series[UInt8]) -> Series[bool]:
        """Reject 96/98 in any delinquency counter (inspection §6).

        The highest legitimate value in the three columns is 13, 17 and 11
        respectively, so 96 and 98 are not extreme counts — there is nothing
        between 17 and 96. Read as counts they would dominate any model that
        uses these features, which is the three columns with the strongest
        correlation to the target (0.13, 0.12, 0.10 in §10).
        """
        return rules.no_delinquency_sentinel(counter)

    @pa.check(
        TARGET_COLUMN,
        description=(
            "the target admits only the labels 0 and 1; inspection §2 observed "
            "139,974 zeros and 10,026 ones and nothing else"
        ),
    )
    def target_binary(cls, target: Series[Category]) -> Series[bool]:
        """Reject any label other than 0/1 (inspection §2).

        A third label means the target definition changed upstream, which
        invalidates the model and every reference distribution derived from it.
        """
        return rules.target_is_binary(target)

    @pa.check(
        "MonthlyIncome",
        description=(
            "MonthlyIncome must be >= 0; inspection §4 reports a minimum of 0 "
            "over the 120,269 non-null rows"
        ),
    )
    def monthly_income_non_negative(cls, income: Series[Float64]) -> Series[bool]:
        """Reject negative income (inspection §4: observed minimum is 0).

        Nulls are skipped, not failed: 19.82% of rows have none (§3) and that
        is acceptable here by design. Zero is allowed — 1% of the non-null
        rows sit at 0 and an unemployed applicant is a real applicant.
        """
        return rules.income_non_negative(income)

    @pa.check(
        "NumberOfDependents",
        description=(
            "NumberOfDependents must be >= 0; inspection §4 reports a range of "
            "0 to 20 over the 146,076 non-null rows"
        ),
    )
    def dependents_non_negative(cls, dependents: Series[Float64]) -> Series[bool]:
        """Reject a negative dependent count (inspection §4: range 0 to 20)."""
        return rules.dependents_non_negative(dependents)

    @pa.check(
        "RevolvingUtilizationOfUnsecuredLines",
        description=(
            "utilisation is a fraction of the available limit and should be "
            "<= 1; inspection §8 found 3,321 rows above 1 but only 241 above 10"
        ),
    )
    def revolving_utilization_at_most_one(
        cls, utilization: Series[Float64]
    ) -> Series[bool]:
        """Flag utilisation above 1 (inspection §8: 3,321 rows, max 50,708).

        A warning rather than a blocker: 2.21% of rows exceed 1 and only 0.16%
        exceed 10, so most of the excess is the plausible kind (fees, an
        over-limit month) while the 50,708 tail is not. The line is drawn at
        the semantic bound so the alert fires on the definition, and severity
        decides what to do about it.
        """
        return rules.utilization_at_most_one(utilization)

    # ----------------------------------------------------------------------
    # Batch rules
    # ----------------------------------------------------------------------

    @pa.dataframe_check(
        description=(
            "NumberOfDependents null implies MonthlyIncome null; inspection §3 "
            "shows 3,924 dependent-nulls inside 29,731 income-nulls with "
            "29,731 rows missing at least one value, so the containment is exact"
        ),
    )
    def nested_missingness(cls, df: pd.DataFrame) -> Series[bool]:
        """Reject a dependents-null row whose income is present (inspection §3).

        The two null counts are 3,924 and 29,731, and §3 also reports that
        exactly 29,731 rows are missing at least one value. Those three numbers
        together prove the dependent-nulls are a strict subset of the
        income-nulls: the columns go missing together, from one upstream cause.

        This is the rule most likely to catch a source change that every other
        rule would pass, because it constrains the *relationship* between two
        columns rather than either column's values. A batch where dependents go
        missing on their own is arriving from a different process than the one
        the report describes, and any imputation tuned on the current pattern
        is then untested.
        """
        return rules.nested_missingness_holds(df)

    @pa.dataframe_check(
        description=(
            "no exact duplicate rows across all 11 columns; inspection §7 "
            "found 609 (0.41%), leaving 149,391 distinct rows"
        ),
    )
    def no_duplicate_rows(cls, df: pd.DataFrame) -> Series[bool]:
        """Flag repeated rows (inspection §7: 609 duplicates, 0.41%).

        ``keep="first"`` so the count matches the report's method: the first
        occurrence is innocent, the extra copies are the finding. Note for the
        consumer: pandera melts a frame-level check across columns, so this
        rule contributes one failure *cell* per column of each repeated row —
        count distinct values of ``failure_cases["index"]`` to recover rows.

        A warning, not a blocker, and the reason is an absence in the data
        rather than a tolerance: §7 searched for an identifier column (id,
        index, row_id, rowid, unnamed: 0) and found none. Without an id there
        is no way to distinguish one customer ingested twice from two customers
        whose 11 recorded attributes coincide — and with 11 mostly small-integer
        columns the second is entirely possible. We do not know which of the 609
        is which, so the contract reports and a human decides.
        """
        return rules.rows_are_unique(df)

    @pa.dataframe_check(
        description=(
            f"positive class rate must fall in "
            f"[{POSITIVE_RATE_MIN:.0%}, {POSITIVE_RATE_MAX:.0%}]; inspection "
            "§2 measured 6.68% (10,026 of 150,000)"
        ),
    )
    def positive_rate_band(cls, df: pd.DataFrame) -> bool:
        """Flag a batch whose positive rate leaves [5%, 9%] (inspection §2).

        The reference is 6.68%, a 13.96:1 negative-to-positive ratio. A batch
        outside the band is a different population — a changed labelling window,
        a filtered segment, a drifting portfolio — and the model's calibration
        was fitted on this one.

        Returns a single boolean rather than a per-row series because the rate
        is a property of the batch; no individual row can be blamed for it.
        """
        return rules.positive_rate_in_band(df)


# --------------------------------------------------------------------------
# Derived from the schema rather than restated, so the two can never disagree.
# --------------------------------------------------------------------------
EXPECTED_COLUMNS: Final[tuple[str, ...]] = tuple(RawCreditSchema.to_schema().columns)
FEATURE_COLUMNS: Final[tuple[str, ...]] = tuple(
    column for column in EXPECTED_COLUMNS if column != TARGET_COLUMN
)
