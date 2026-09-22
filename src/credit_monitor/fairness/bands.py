"""Per-band classification and calibration metrics at a fixed operating point.

The dataset carries no race, sex or marital status. It carries ``age``, which
is a protected criterion in credit in its own right — not a stand-in for one.
Nothing here simulates an attribute the data does not have.

Every rate is reported with a Wilson interval, because band sizes differ by an
order of magnitude and a bare point estimate hides that: the 18-25 band is
small enough that its TPR moves several points on a handful of rows.
"""

import math
from dataclasses import dataclass
from typing import Final

import numpy as np
import pandas as pd

AGE_BANDS: Final[tuple[tuple[str, int, int], ...]] = (
    ("18-25", 18, 25),
    ("26-35", 26, 35),
    ("36-45", 36, 45),
    ("46-55", 46, 55),
    ("56-65", 56, 65),
    ("66+", 66, 200),
)

# Below this many rows in a cell, a rate is reported but not compared: the
# interval is wider than any disparity worth arguing about.
MIN_CELL_FOR_COMPARISON: Final[int] = 100

Z_95: Final[float] = 1.959963984540054


def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Wilson and not the normal approximation: with a positive rate near 6% and
    bands down to a few hundred rows, the normal interval runs below zero and
    stops meaning anything. Wilson stays inside [0, 1] by construction.

    Args:
        successes: Count of the event.
        total: Count of trials.
        z: Standard-normal quantile; the default is 95%.

    Returns:
        ``(low, high)``, or ``(nan, nan)`` when ``total`` is zero.
    """
    if total == 0:
        return (math.nan, math.nan)
    phat = successes / total
    denominator = 1.0 + z**2 / total
    centre = (phat + z**2 / (2 * total)) / denominator
    margin = (
        z * math.sqrt(phat * (1 - phat) / total + z**2 / (4 * total**2)) / denominator
    )
    low, high = max(0.0, centre - margin), min(1.0, centre + margin)
    # Em p = 0 ou p = 1 os dois termos se cancelam algebricamente e o limite é
    # exatamente 0 ou 1; em ponto flutuante sobra um ulp do lado errado, e o
    # intervalo passa a não conter a própria estimativa. O aperto corrige o
    # erro de arredondamento, não o cálculo.
    return (min(low, phat), max(high, phat))


def band_of(age: int) -> str:
    """Map one age to its band label."""
    for label, low, high in AGE_BANDS:
        if low <= age <= high:
            return label
    return "fora"


def assign_bands(ages: pd.Series) -> pd.Series:
    """Map a column of ages to band labels, as an ordered categorical."""
    labels = [label for label, _, _ in AGE_BANDS]
    return pd.Categorical(
        [band_of(int(age)) for age in ages], categories=labels, ordered=True
    )


@dataclass(frozen=True, slots=True)
class BandMetrics:
    """One row of the per-band table.

    ``approved`` means *scored below the operating threshold*: this project has
    no lending decision attached to it, so "approval" is the name of the model's
    negative prediction and nothing more.
    """

    band: str
    n: int
    base_rate: float
    """Observed default rate in the band. The reason the criteria conflict."""
    mean_predicted: float
    approval_rate: float
    approval_ci: tuple[float, float]
    tpr: float
    tpr_ci: tuple[float, float]
    tpr_n: int
    """Positives in the band — the denominator that gets small first."""
    fpr: float
    fpr_ci: tuple[float, float]
    fpr_n: int
    precision: float
    precision_n: int
    calibration_gap: float
    """Predicted mean minus observed rate. Negative means under-prediction."""

    @property
    def comparable(self) -> bool:
        """Whether the positive cell is large enough to argue about."""
        return self.tpr_n >= MIN_CELL_FOR_COMPARISON


def _safe_rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else math.nan


def band_metrics(
    bands: pd.Series,
    y_true: np.ndarray,
    y_score: np.ndarray,
    threshold: float,
) -> list[BandMetrics]:
    """Classification and calibration metrics per band at one threshold.

    Args:
        bands: Band label per row.
        y_true: Observed outcome, 1 for default.
        y_score: Calibrated probability of default.
        threshold: Scores at or above it are rejected.

    Returns:
        One ``BandMetrics`` per band present, in band order.
    """
    frame = pd.DataFrame(
        {"band": np.asarray(bands), "y": np.asarray(y_true), "p": np.asarray(y_score)}
    )
    frame["rejected"] = frame["p"] >= threshold

    rows: list[BandMetrics] = []
    for label, _, _ in AGE_BANDS:
        group = frame[frame["band"] == label]
        if group.empty:
            continue
        n = len(group)
        positives = group[group["y"] == 1]
        negatives = group[group["y"] == 0]
        rejected = group[group["rejected"]]

        approved_n = int((~group["rejected"]).sum())
        tp = int(positives["rejected"].sum())
        fp = int(negatives["rejected"].sum())

        rows.append(
            BandMetrics(
                band=label,
                n=n,
                base_rate=float(group["y"].mean()),
                mean_predicted=float(group["p"].mean()),
                approval_rate=approved_n / n,
                approval_ci=wilson_interval(approved_n, n),
                tpr=_safe_rate(tp, len(positives)),
                tpr_ci=wilson_interval(tp, len(positives)),
                tpr_n=len(positives),
                fpr=_safe_rate(fp, len(negatives)),
                fpr_ci=wilson_interval(fp, len(negatives)),
                fpr_n=len(negatives),
                precision=_safe_rate(tp, len(rejected)),
                precision_n=len(rejected),
                calibration_gap=float(group["p"].mean() - group["y"].mean()),
            )
        )
    return rows


@dataclass(frozen=True, slots=True)
class FairnessCriteria:
    """The three criteria, each as the spread across comparable bands.

    They are reported together because they cannot all be satisfied at once
    when base rates differ — which they do here. Reporting one alone is how a
    fairness claim gets made without being tested.
    """

    demographic_parity_difference: float
    """Max minus min approval rate. Zero iff every band is approved equally."""
    tpr_difference: float
    fpr_difference: float
    equalised_odds_difference: float
    """The larger of the two gaps above: the usual single-number summary."""
    calibration_difference: float
    """Max minus min calibration gap across bands."""
    base_rate_difference: float
    """Max minus min observed default rate. The source of the conflict."""
    bands_compared: tuple[str, ...]

    @property
    def summary(self) -> dict[str, float]:
        """The five numbers, for a table or a log line."""
        return {
            "demographic_parity_difference": self.demographic_parity_difference,
            "tpr_difference": self.tpr_difference,
            "fpr_difference": self.fpr_difference,
            "equalised_odds_difference": self.equalised_odds_difference,
            "calibration_difference": self.calibration_difference,
            "base_rate_difference": self.base_rate_difference,
        }


def comparable_bands(rows: list[BandMetrics]) -> tuple[str, ...]:
    """The bands whose positive cell clears the floor, in band order."""
    return tuple(row.band for row in rows if row.comparable)


def common_comparable_bands(*tables: list[BandMetrics]) -> tuple[str, ...]:
    """Bands comparable in **every** table given, in band order.

    Two populations rarely have the same comparable set: a band that clears the
    floor in a 44,000-row holdout may not clear it in a 7,368-row batch. A
    criterion computed over each population's own set and then subtracted is a
    difference of two different questions. Comparisons across populations go
    through this.
    """
    sets = [set(comparable_bands(table)) for table in tables]
    if not sets:
        return ()
    shared = set.intersection(*sets)
    return tuple(label for label, _, _ in AGE_BANDS if label in shared)


def fairness_criteria(
    rows: list[BandMetrics], restrict_to: tuple[str, ...] | None = None
) -> FairnessCriteria:
    """Reduce the per-band table to the three named criteria.

    Only bands whose positive cell clears ``MIN_CELL_FOR_COMPARISON`` enter the
    spreads. A band with eleven positives produces a TPR that swings on one
    row, and letting it set the maximum would be reporting noise as disparity.

    Args:
        rows: Output of :func:`band_metrics`.
        restrict_to: Compare only these bands, on top of the size floor. Used to
            hold the band set fixed across populations — see
            :func:`common_comparable_bands`. ``None`` uses every comparable band.

    Returns:
        The criteria, with the bands that were actually compared.
    """
    comparable = [row for row in rows if row.comparable]
    if restrict_to is not None:
        comparable = [row for row in comparable if row.band in restrict_to]
    if not comparable:
        msg = "nenhuma faixa tem células grandes o suficiente para comparar"
        raise ValueError(msg)

    approvals = [row.approval_rate for row in comparable]
    tprs = [row.tpr for row in comparable]
    fprs = [row.fpr for row in comparable]
    gaps = [row.calibration_gap for row in comparable]
    bases = [row.base_rate for row in comparable]

    tpr_gap = max(tprs) - min(tprs)
    fpr_gap = max(fprs) - min(fprs)
    return FairnessCriteria(
        demographic_parity_difference=max(approvals) - min(approvals),
        tpr_difference=tpr_gap,
        fpr_difference=fpr_gap,
        equalised_odds_difference=max(tpr_gap, fpr_gap),
        calibration_difference=max(gaps) - min(gaps),
        base_rate_difference=max(bases) - min(bases),
        bands_compared=tuple(row.band for row in comparable),
    )
