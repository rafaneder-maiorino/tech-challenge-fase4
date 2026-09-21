"""Turn the raw parquet into a frame the model contract accepts.

Every decision here traces to a number in ``reports/inspection.md``, cited
inline. The module is deliberately split into a **stateless** half and a
**fitted** half, and the order matters:

``clean``
    Drops rows, builds the missingness indicator, neutralises units and caps
    tails. Pure function of the frame and the parameters — nothing is learned
    from the data, so it can run before the reference/holdout split without
    leaking anything across it.

``fit_imputation`` / ``impute``
    The only learned constants are two medians, and they are fitted on the
    **reference** set alone. Fitting them on everything would let the holdout
    — the control group of the day-8 A/A test — influence the values the
    reference is filled with.

Deduplication happens inside ``clean``, i.e. *before* the split, and that
ordering is not cosmetic. Inspection §7 found 609 exact duplicate rows. Split
at random, a duplicated pair lands one row in the reference and one in the
holdout, and the "unseen" control set is then holding rows the model trained
on. The contract calls duplicates a warning because ingestion cannot tell a
repeated customer from two identical ones; training has to decide something,
and the costs are not symmetric — at ingestion, dropping a row loses a real
customer, while in training, keeping it silently doubles that profile's weight
in the gradient. Same uncertainty, resolved differently at two boundaries.

The output is validated against
:class:`credit_monitor.contracts.model.ModelInputSchema` by
:func:`assert_model_ready`, which is called by the pipeline itself rather than
only by a test.
"""

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Final

import pandas as pd
import pandera.pandas as pa

from credit_monitor.constants import DELINQUENCY_COLUMNS, TARGET_COLUMN
from credit_monitor.contracts.failures import blocking_failures, resolve_rule
from credit_monitor.contracts.model import (
    DEBT_RATIO_MAX_WITH_INCOME,
    ModelInputSchema,
)
from credit_monitor.contracts.raw import (
    AGE_MAX,
    AGE_MIN,
    DELINQUENCY_SENTINELS,
    EXPECTED_COLUMNS,
    POSITIVE_LABEL,
)

log = logging.getLogger(__name__)

# The indicator added before imputation. Named once so the models, the drift
# baseline and the tests cannot disagree about it.
INCOME_MISSING_COLUMN: Final[str] = "income_missing"

# Features the models see: the ten contract features plus the indicator. The
# target is excluded, obviously, and so is nothing else — the point of the
# contract is that these ten are the agreed surface.
MODEL_FEATURES: Final[tuple[str, ...]] = (
    *(column for column in EXPECTED_COLUMNS if column != TARGET_COLUMN),
    INCOME_MISSING_COLUMN,
)


@dataclass(frozen=True, slots=True)
class PreprocessingParams:
    """The choices a reviewer is entitled to disagree with, in one place.

    Parameters rather than constants buried in the code, because each one is a
    judgement call over a documented trade-off and day 5 may want to revisit it
    without reading the function body.
    """

    utilization_cap: float = 10.0
    """Ceiling for ``RevolvingUtilizationOfUnsecuredLines``.

    **10.0, and the first version of this file had 1.0 on the strength of a
    number that does not mean what it looks like.** Inspection §10 reports the
    linear correlation between this column and the target as -0.00, which was
    read as "the 3,080 rows above 1 carry no signal to lose". That reading was
    wrong in the same way, and for the same reason, as the delinquency-counter
    correlations of ``docs/findings.md`` §4: Pearson is a covariance ratio, and
    241 rows reaching 50,708 against a mean of 6.05 (§4) dominate it entirely.

    Recomputed (``docs/findings.md`` §6):

    * Pearson on the full column: **-0.0018** — the -0.00 of §10.
    * Pearson excluding the 241 rows above 10: **+0.2816**. Sign flip, and from
      nothing to a third of the way to the target.
    * Spearman on the full column: **0.2404**. Rank correlation is immune to
      the tail and showed the real relationship the whole time.
    * Univariate AUC of this column alone: **0.7778**.

    And the default rates settle it. Against a 6.68% base rate: rows at or
    below 1 default at **5.99%** (0.90x), rows in **(1, 10] at 39.61%
    (5.93x)**, rows above 10 at **7.05%** (1.06x, i.e. indistinguishable from
    the base rate and consistent with being an artefact).

    So the column has exactly one highly discriminating region and it is the
    one a cap of 1.0 destroys, by flattening those 3,080 rows onto the same
    value as the 97.79% that default at 5.99%. A cap of 10.0 winsorises the
    241 rows that carry no signal and keeps the band that carries almost all
    of it.

    The second reason is that this project is building a drift detector. The
    tail above 1 is where an economic shock would first appear — utilisation
    rising past the limit is what a credit squeeze looks like in this column —
    and a reference distribution capped at 1.0 has no room above 1 for a
    future batch to differ in. Capping at 1.0 would delete the region drift
    lives in.

    Keeping values above 1 leaves ``revolving_utilization_at_most_one``
    failing, which is a **warning** in the contract's own classification and
    is reported as such by :func:`assert_model_ready`. That is the contract
    working as designed, not being bypassed: the rule exists to say "look at
    these rows", and we have.
    """

    debt_ratio_cap: float = DEBT_RATIO_MAX_WITH_INCOME
    """Ceiling for ``DebtRatio`` on rows that have income (inspection §9)."""

    drop_sentinel_rows: bool = True
    """Drop the 269 rows carrying 96/98 in the delinquency counters (§6)."""

    dependents_fill: float = 0.0
    """Value for the 3,924 null ``NumberOfDependents`` (§3, §4: mode and
    median are both 0, and every one of those rows also has income missing)."""


@dataclass(frozen=True, slots=True)
class CleaningReport:
    """What ``clean`` did, in counts, so a run can be audited after the fact."""

    rows_in: int
    dropped_sentinel_rows: int
    dropped_out_of_range_age: int
    source_duplicate_rows: int
    """Duplicates already present before any transform (inspection §7: 609)."""

    dropped_duplicate_rows: int
    """Duplicates actually dropped, which is the larger number.

    Capping and unit-neutralisation collapse rows that the source had as
    distinct: two income-missing borrowers whose only difference was a
    DebtRatio of 1,100 against 2,400 become the same record once both are
    neutralised. The gap between this and ``source_duplicate_rows`` is
    therefore self-inflicted, and worth watching — it is the price of the
    cleaning, counted rather than hidden.
    """
    rows_out: int
    income_missing_rows: int
    income_zero_rows: int
    utilization_capped_rows: int
    debt_ratio_capped_rows: int
    debt_ratio_neutralised_rows: int
    dependents_filled_rows: int
    positive_rate: float

    def to_dict(self) -> dict[str, Any]:
        """Render as a plain dict for logging and for the run metadata."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ImputationFit:
    """The two constants learned from the reference set, and nothing else."""

    monthly_income_median: float
    debt_ratio_median: float
    fitted_on_rows: int

    def to_dict(self) -> dict[str, Any]:
        """Render as a plain dict for MLflow params and the run metadata."""
        return asdict(self)


def sentinel_mask(frame: pd.DataFrame) -> pd.Series:
    """Rows carrying an administrative code in any delinquency counter.

    Inspection §6: 96 and 98 appear in **269 rows (0.18%)**, and always in all
    three counters at once. Those rows default at **54.65%** against **6.60%**
    elsewhere.
    """
    return frame[list(DELINQUENCY_COLUMNS)].isin(DELINQUENCY_SENTINELS).any(axis=1)


def clean(
    frame: pd.DataFrame,
    params: PreprocessingParams | None = None,
) -> tuple[pd.DataFrame, CleaningReport]:
    """Apply every stateless cleaning decision and report what it cost.

    The resulting frame still has nulls in ``MonthlyIncome`` and ``DebtRatio``
    for the missing-income group — filling those is :func:`impute`'s job, and
    it happens after the split so the fill values are learned from the
    reference set only.

    Args:
        frame: The raw frame, as delivered.
        params: The judgement calls; defaults are documented on the dataclass.

    Returns:
        The cleaned frame (11 contract columns plus ``income_missing``) and a
        report of every row that was dropped or altered.
    """
    params = params or PreprocessingParams()
    rows_in = len(frame)
    source_duplicates = int(frame.duplicated(keep="first").sum())
    out = frame.copy()

    # ----------------------------------------------------------------------
    # 1. Sentinel rows: a record-level marker, not a column error.
    #
    # Inspection §6 is unambiguous about which of the two this is. If 96/98
    # were a per-column data-entry fault, the rows carrying them would default
    # like everyone else; instead they default at 54.65% against 6.60%, and
    # the codes appear in all three counters simultaneously in all 269 rows.
    # That is one administrative status stamped across the record — a
    # population, not noise.
    #
    # They are dropped from training rather than imputed, and the distinction
    # matters: imputing would put 269 rows whose true default rate is eight
    # times the base rate back into the population as if they were ordinary
    # borrowers, and the model would learn a diluted version of both groups.
    #
    # NOTE FOR LATER STAGES: this is a deliberate scope exclusion, not a
    # discarded problem. Those 269 records are exactly the ones an operational
    # system most needs a decision for, and they deserve their own treatment —
    # a separate rule, a separate model, or a manual queue. The drift monitor
    # of stage 2 should also expect them to be absent from the reference
    # distribution, so a batch that suddenly contains many is a change in the
    # source, not drift in the population.
    # ----------------------------------------------------------------------
    sentinels = sentinel_mask(out)
    dropped_sentinels = int(sentinels.sum()) if params.drop_sentinel_rows else 0
    if params.drop_sentinel_rows:
        out = out[~sentinels]

    # 2. Implausible age. Inspection §5: exactly 1 row below 18 (it is 0), and
    # a maximum of 109, so this drops one record and touches nothing else.
    in_age_range = out["age"].between(AGE_MIN, AGE_MAX)
    dropped_age = int((~in_age_range).sum())
    out = out[in_age_range]

    # ----------------------------------------------------------------------
    # 3. The missingness indicator, BEFORE any imputation.
    #
    # This is the decision that would be silently destroyed by imputing first,
    # and inspection §9 says why it must not be. The missing-income group
    # defaults at 5.61% against 6.95% when income is present — missingness
    # points the *opposite* way from the naive reading. "No income recorded"
    # sounds like a worse borrower and is in fact a slightly better one, so a
    # median fill without a flag does not merely lose information, it replaces
    # a real negative signal with the population average.
    #
    # Income of exactly 0 is folded into the same indicator. §4 puts 1,634 rows
    # at zero income; they default at 4.04%, closer to the missing group than
    # to the rest, and the model contract already argues that a zero
    # denominator makes DebtRatio undefined. Treating "absent" and "zero" as
    # one state is one flag instead of two nearly identical ones.
    # ----------------------------------------------------------------------
    income = out["MonthlyIncome"]
    income_zero = (income == 0).fillna(False)
    missing_income = income.isna() | income_zero
    out[INCOME_MISSING_COLUMN] = missing_income.astype("int8")
    out["MonthlyIncome"] = income.where(~missing_income)

    # ----------------------------------------------------------------------
    # 4. DebtRatio: two units in one column, separated rather than averaged.
    #
    # Inspection §9 measures a median of 1,159 when income is missing against
    # 0.296 when it is present — four orders of magnitude. That is not skew,
    # it is a different quantity recorded in the same column, and the model
    # contract encodes exactly that with a rule conditioned on income presence.
    #
    # CHOSEN HANDLING: the column is defined as *the ratio*, and only where
    # income exists. In the missing-income group it is set to null and later
    # filled with the reference median of the income-present group, so the
    # column carries one unit end to end. The group is not lost — the
    # `income_missing` flag above marks every one of those rows, so a tree can
    # still isolate them and a linear model still gets the group effect.
    #
    # REJECTED ALTERNATIVES, and why:
    #   * Leave both regimes in one column. A split at 0.5 would send all
    #     ~31k missing-income rows to the same side for a reason that has
    #     nothing to do with indebtedness. This is the failure the contract
    #     exists to prevent.
    #   * Emit a second column holding the raw value for the missing group.
    #     Loses nothing, but it is zero for 79% of rows and assumes we know
    #     the other unit is currency. The assumption is plausible and
    #     undocumented, which is the wrong combination for a baseline.
    # ----------------------------------------------------------------------
    debt_neutralised = int(missing_income.sum())
    out["DebtRatio"] = out["DebtRatio"].where(~missing_income)

    # Cap the ratio where it IS a ratio. §9: with income present the 95th
    # percentile is 0.98 and the 99th is 2.99, so 1,786 rows (1.19%) sit above
    # the contract's ceiling of 2, reaching 61,106. Above the bound the value
    # is either the other unit or unreliable; either way it is not a ratio.
    above_cap = (out["DebtRatio"] > params.debt_ratio_cap).fillna(False)
    debt_capped = int(above_cap.sum())
    out["DebtRatio"] = out["DebtRatio"].clip(upper=params.debt_ratio_cap)

    # 5. Utilisation cap — see PreprocessingParams.utilization_cap for the
    # 3,321 / 241 trade-off behind the default.
    utilization = out["RevolvingUtilizationOfUnsecuredLines"]
    utilization_capped = int((utilization > params.utilization_cap).sum())
    out["RevolvingUtilizationOfUnsecuredLines"] = utilization.clip(
        upper=params.utilization_cap
    )

    # ----------------------------------------------------------------------
    # 6. Dependents. Inspection §3 counts 3,924 nulls, and §4 gives a median
    # and 25th/50th percentile of 0, so zero is both the modal and the median
    # dependant count. No separate indicator is created: §3 also shows the
    # dependants-null rows are a strict subset of the income-null rows, so
    # `income_missing` already marks every one of them and a second flag would
    # be a copy of the first. This is re-checked after the fact by
    # assert_nested_missingness_subsumed.
    # ----------------------------------------------------------------------
    dependents_null = out["NumberOfDependents"].isna()
    dependents_filled = int(dependents_null.sum())
    out["NumberOfDependents"] = out["NumberOfDependents"].fillna(params.dependents_fill)

    # 7. Exact duplicates, before the split — see the module docstring. Note
    # that `duplicated` compares the target too, so only rows identical in
    # features *and* label collapse; two borrowers with the same profile and
    # different outcomes both survive, and no label is ever discarded here.
    duplicates = out.duplicated(keep="first")
    dropped_duplicates = int(duplicates.sum())
    out = out[~duplicates]

    out = out.reset_index(drop=True)
    report = CleaningReport(
        rows_in=rows_in,
        dropped_sentinel_rows=dropped_sentinels,
        dropped_out_of_range_age=dropped_age,
        source_duplicate_rows=source_duplicates,
        dropped_duplicate_rows=dropped_duplicates,
        rows_out=len(out),
        income_missing_rows=int(out[INCOME_MISSING_COLUMN].sum()),
        income_zero_rows=int(income_zero.sum()),
        utilization_capped_rows=utilization_capped,
        debt_ratio_capped_rows=debt_capped,
        debt_ratio_neutralised_rows=debt_neutralised,
        dependents_filled_rows=dependents_filled,
        positive_rate=float((out[TARGET_COLUMN] == POSITIVE_LABEL).mean()),
    )
    log.info("preprocess.cleaned", extra=report.to_dict())
    return out, report


def assert_nested_missingness_subsumed(frame: pd.DataFrame) -> None:
    """Re-check that ``income_missing`` still covers every dependants-null row.

    Inspection §3 shows the 3,924 dependants-nulls sitting strictly inside the
    29,731 income-nulls, which is what licenses using one indicator for both.
    The containment is a property of the *source*, not of our code, so it is
    verified against the frame in front of us rather than assumed: if a future
    delivery lets dependants go missing on their own, the single flag silently
    stops representing them and this raises instead.

    Args:
        frame: A frame that has been through :func:`clean`, before imputation.

    Raises:
        ValueError: A dependants-null row is not marked as income-missing.
    """
    unmarked = frame["NumberOfDependents"].isna() & (frame[INCOME_MISSING_COLUMN] == 0)
    if bool(unmarked.any()):
        raise ValueError(
            f"{int(unmarked.sum())} rows have NumberOfDependents null without "
            "income being missing. Inspection §3 shows the two always go "
            f"missing together, so {INCOME_MISSING_COLUMN} no longer subsumes "
            "the dependants indicator and a second flag is now required."
        )


def fit_imputation(reference: pd.DataFrame) -> ImputationFit:
    """Learn the two fill constants from the reference set alone.

    Medians, not means: inspection §4 gives ``MonthlyIncome`` a mean of 6,670
    against a median of 5,400 and a maximum of 3,008,750, so the mean is a
    statement about the tail rather than about a typical borrower.

    Args:
        reference: The reference split, cleaned but not yet imputed.

    Returns:
        The fitted constants and the row count they came from.
    """
    present = reference[INCOME_MISSING_COLUMN] == 0
    fit = ImputationFit(
        monthly_income_median=float(reference.loc[present, "MonthlyIncome"].median()),
        debt_ratio_median=float(reference.loc[present, "DebtRatio"].median()),
        fitted_on_rows=int(present.sum()),
    )
    log.info("preprocess.imputation_fitted", extra=fit.to_dict())
    return fit


def impute(frame: pd.DataFrame, fit: ImputationFit) -> pd.DataFrame:
    """Fill the two columns left null by :func:`clean`, using fitted constants.

    Args:
        frame: A cleaned split (reference or holdout).
        fit: Constants from :func:`fit_imputation`, fitted on the reference.

    Returns:
        A frame with no nulls anywhere, still carrying ``income_missing`` so
        the fill remains visible to the model.
    """
    out = frame.copy()
    out["MonthlyIncome"] = out["MonthlyIncome"].fillna(fit.monthly_income_median)
    out["DebtRatio"] = out["DebtRatio"].fillna(fit.debt_ratio_median)
    # Imputation collapses the missing-income group onto two shared values, so
    # rows that differed only there are now identical. Deduplicating inside a
    # split is safe — both copies were on the same side of the split already,
    # so nothing crosses the reference/holdout boundary here.
    return out[~out.duplicated(keep="first")].reset_index(drop=True)


def assert_model_ready(frame: pd.DataFrame) -> None:
    """Enforce ``ModelInputSchema`` on the contract columns. Pipeline step.

    This runs inside the pipeline, not only in a test, because a test proves
    the code was correct on the day it was written while this proves the data
    is correct on the day it is used. Only the 11 contract columns are
    validated — ``income_missing`` is ours, not the contract's — which is why
    the frame is projected before validating rather than the schema loosened.

    Enforcement is **severity-aware**, and that is the whole point rather than
    a loophole. Pandera raises on any failed check, which flattens the policy
    the contract went to the trouble of stating: three of its thirteen rules
    are warnings, and ``revolving_utilization_at_most_one`` is one of them
    *because the contract argues those rows may be genuine and must not be
    thrown away*. Treating that as fatal here would mean the preprocessing
    step overriding the contract's own classification — and, concretely, it
    would force the utilisation cap down to 1.0 and flatten the 3,080 rows
    that default at 39.61% (see ``docs/findings.md`` §6). So a blocker raises
    and a warning is logged, exactly as the ingestion gate of stage 1 does
    with a delivered batch.

    Args:
        frame: A cleaned, imputed split.

    Raises:
        pandera.errors.SchemaErrors: A blocker-level rule failed. Raised
            lazily, so the message lists every violation at once.
    """
    try:
        ModelInputSchema.validate(frame[list(EXPECTED_COLUMNS)], lazy=True)
    except pa.errors.SchemaErrors as errors:
        blockers = blocking_failures(errors)
        if not blockers.empty:
            raise
        warnings = errors.failure_cases.copy()
        warnings["rule"] = [
            resolve_rule(str(name)).value for name in warnings["check"]
        ]
        log.warning(
            "preprocess.contract_warnings",
            extra={
                "schema": ModelInputSchema.__name__,
                "rows": len(frame),
                "warning_rules": sorted(set(warnings["rule"])),
                "warning_rows": int(warnings["index"].nunique()),
            },
        )
        return
    log.info(
        "preprocess.contract_validated",
        extra={"schema": ModelInputSchema.__name__, "rows": len(frame)},
    )


def build_feature_matrix(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Split a model-ready frame into the feature matrix and the target.

    Args:
        frame: A cleaned, imputed, validated split.

    Returns:
        ``(X, y)`` with ``y`` as int8 — the contract stores the target as a
        categorical of the strings "0"/"1" (the parquet round-trips it that
        way), and no estimator wants that.
    """
    features = frame[list(MODEL_FEATURES)].astype("float64")
    target = (frame[TARGET_COLUMN] == POSITIVE_LABEL).astype("int8")
    return features, target


@dataclass(frozen=True, slots=True)
class PreparedSplit:
    """One side of the reference/holdout split, ready for a model."""

    name: str
    frame: pd.DataFrame
    features: pd.DataFrame = field(repr=False)
    target: pd.Series = field(repr=False)

    @property
    def rows(self) -> int:
        """Number of rows in the split."""
        return len(self.frame)

    @property
    def positive_rate(self) -> float:
        """Share of the positive class, for the stratification check."""
        return float(self.target.mean())


def prepare_split(name: str, frame: pd.DataFrame, fit: ImputationFit) -> PreparedSplit:
    """Impute, validate against the contract and split into X/y.

    Args:
        name: ``"reference"`` or ``"holdout"``, for logs and metadata.
        frame: The cleaned split, before imputation.
        fit: Constants fitted on the reference set.

    Returns:
        The prepared split.
    """
    imputed = impute(frame, fit)
    assert_model_ready(imputed)
    features, target = build_feature_matrix(imputed)
    return PreparedSplit(name=name, frame=imputed, features=features, target=target)


def validate_or_raise(frame: pd.DataFrame) -> pa.errors.SchemaErrors | None:
    """Return the schema errors for a frame, or ``None`` when it is clean.

    A non-raising variant of :func:`assert_model_ready`, for callers that want
    to report rather than stop.
    """
    try:
        assert_model_ready(frame)
    except pa.errors.SchemaErrors as errors:
        return errors
    return None
