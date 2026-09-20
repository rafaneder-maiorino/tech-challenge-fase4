"""Contract for cleaned, model-ready data.

Same dataset, stricter contract. ``ModelInputSchema`` inherits every rule of
:class:`credit_monitor.contracts.raw.RawCreditSchema` — including the 96/98
sentinel rejection, which is what satisfies the "no sentinel values remaining"
requirement at this boundary without declaring a second rule for it — and adds
the three guarantees the raw source cannot give.

Why the boundary exists, in one number each (``reports/inspection.md``):

``MonthlyIncome``
    Null in 29,731 of 150,000 rows, 19.82% (§3). Acceptable on arrival, fatal
    as model input: an estimator handed a null either raises or, worse, is fed
    an undeclared imputation. Raw says "may be missing", the model says "was
    resolved upstream".

``DebtRatio``
    Median 1,159 when income is missing against 0.296 when income is present
    (§9) — four orders of magnitude. The column is not merely skewed in one
    group; it is carrying a different unit there, which is why no range rule on
    it appears in the raw schema at all.

nulls
    §3 lists exactly two columns with any missing value. Once income is
    resolved, the model boundary permits none anywhere.

The inherited batch rules (``nested_missingness``, ``no_duplicate_rows``,
``positive_rate_band``) stay in force. ``nested_missingness`` is vacuous here
by construction — nothing is null — and is kept rather than suppressed so the
two schemas report the same rule set and stage 2 needs no special case.
"""

from typing import Final

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Float64, Series

from credit_monitor.contracts.raw import FEATURE_COLUMNS, RawCreditSchema

# Inspection §9: with income present, DebtRatio has a median of 0.296, a 75th
# percentile of 0.4826 and a 95th of 1.1288. A ceiling of 2 sits above the 95th
# percentile of the population this rule applies to, so it admits a genuinely
# over-indebted borrower while excluding the 1,159-median regime that belongs to
# the missing-income group — where the column means something else entirely.
DEBT_RATIO_MAX_WITH_INCOME: Final[float] = 2.0

# Inspection §4: income has an observed minimum of 0 over the 120,269 non-null
# rows. Zero is tolerated in the raw contract (an applicant can be unemployed)
# but not here: DebtRatio is debt divided by income, so a zero denominator makes
# the ratio undefined, and the model consumes the ratio.
MONTHLY_INCOME_MIN_EXCLUSIVE: Final[float] = 0.0


class ModelInputSchema(RawCreditSchema):
    """The raw contract plus the guarantees the model itself depends on.

    Only the two nullable columns are redeclared, to close them. Every other
    column, dtype, order and check is inherited unchanged, so a rule added to
    the raw schema automatically applies here too — the subclass cannot drift
    behind the source it tightens.
    """

    monthly_income: Series[Float64] = pa.Field(alias="MonthlyIncome", nullable=False)
    number_of_dependents: Series[Float64] = pa.Field(
        alias="NumberOfDependents", nullable=False
    )

    class Config:
        """Inherits strict/ordered/coerce from RawCreditSchema.Config."""

        name = "ModelInputSchema"

    # Method name == rule name, and no `name=` kwarg — see the long comment in
    # raw.py for why passing one would break the inherited rules of this very
    # class.

    @pa.check(
        "MonthlyIncome",
        ignore_na=False,
        description=(
            "MonthlyIncome must be present and strictly positive; inspection §3 "
            "reports it null in 19.82% of raw rows, which must be resolved "
            "before this boundary"
        ),
    )
    def monthly_income_present(cls, income: Series[Float64]) -> Series[bool]:
        """Reject null or non-positive income (inspection §3: 19.82% null raw).

        ``ignore_na=False`` on purpose. The default would skip nulls and let
        ``nullable=False`` report them as pandera's generic ``not_nullable``,
        which carries no rule name; with it off, a missing income is attributed
        to this rule and reaches stage 2 already classified. A null therefore
        surfaces twice — once here, once as ``not_nullable`` — and both are
        blockers, so the outcome is identical either way.
        """
        return income.notna() & (income > MONTHLY_INCOME_MIN_EXCLUSIVE)

    @pa.dataframe_check(
        description=(
            f"DebtRatio must be <= {DEBT_RATIO_MAX_WITH_INCOME:g} wherever "
            "MonthlyIncome is present; inspection §9 measured medians of 1,159 "
            "without income and 0.296 with it"
        ),
    )
    def debt_ratio_unit_guard(cls, df: pd.DataFrame) -> Series[bool]:
        """Reject an out-of-unit DebtRatio on rows that have income (§9).

        This is a semantic guard, not an outlier filter. Inspection §9 splits
        the column by income presence and the two halves are not the same
        measurement: median 1,159 (75th percentile 2,382) without income
        against median 0.296 (75th percentile 0.4826) with it. A ratio near
        1,159 is not a debt-to-income ratio at all — it reads as a monetary
        amount recorded where a fraction was expected.

        The condition on ``MonthlyIncome`` is written out even though
        ``monthly_income_present`` already guarantees income exists at this
        boundary. The predicate is the contract: *this ceiling holds only where
        income is known*. Dropping the condition would encode a range rule for
        DebtRatio in general, which §9 shows is false for a fifth of the raw
        source and would be the wrong rule for anyone reusing this check.
        """
        income = df["MonthlyIncome"]
        out_of_unit = income.notna() & (df["DebtRatio"] > DEBT_RATIO_MAX_WITH_INCOME)
        return ~out_of_unit

    @pa.dataframe_check(
        description=(
            "no null in any of the 10 feature columns; inspection §3 reports "
            "MonthlyIncome (19.82%) and NumberOfDependents (2.62%) as the only "
            "columns with missing values, and both are resolved before this point"
        ),
    )
    def no_nulls_in_features(cls, df: pd.DataFrame) -> Series[bool]:
        """Reject any row with a null feature (inspection §3).

        ``nullable=False`` already closes the two columns §3 names, column by
        column. This check states the guarantee over the whole feature set
        instead, so a column that becomes nullable in a future revision of the
        raw contract cannot quietly widen the model boundary with it. The
        target is excluded: it is the label, not a feature, and §3 reports it
        complete in every row.
        """
        return df.loc[:, list(FEATURE_COLUMNS)].notna().all(axis="columns")
