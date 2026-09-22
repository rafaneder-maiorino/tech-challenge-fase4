"""The blind window: months of real degradation with no signal of any kind.

A monitor is characterised by two numbers, and the second one is rarely
reported. The first is how well it detects. The second is **how long it is
wrong for** — the interval in which the model is already degrading and nothing
the operator can see says so.

That interval exists because the two signal families arrive at different times.
Feature drift is available the moment a batch is scored. Performance needs the
outcome, which arrives ``label_lag_months`` later. So a degradation that moves
features is visible immediately; one that does not is invisible until the
labels land, however bad it is.

The stage-2 ablation built exactly that case on purpose: ``stress_only``
changes ``P(y|X)`` without moving a single feature. This module measures what
that costs in months.
"""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BlindWindow:
    """One scenario's blind window."""

    scenario: str
    label_lag_months: int
    degraded_months: tuple[int, ...]
    blind_months: tuple[int, ...]
    first_degraded_month: int | None
    first_signal_month: int | None
    first_drift_alarm_month: int | None

    @property
    def months_blind(self) -> int:
        """How many months pass with degradation present and nothing visible."""
        return len(self.blind_months)

    @property
    def lead_time_months(self) -> int | None:
        """Signed: ``first_degraded - first_signal``.

        **Positive is early warning, negative is blindness.** The unsigned
        count was clipped at zero and therefore hid half the story: it said
        "0 months blind" both for a monitor that warned exactly on time and for
        one that warned a month ahead, which are not the same monitor.

        The sign is the finding. The *same* monitor, on the *same* data, warns
        early or late depending on which mechanism is at work — early when the
        degradation drags features with it, late when it does not.

        ``None`` when nothing ever degraded: there is no lead time to a warning
        about something that did not happen.
        """
        if self.first_degraded_month is None or self.first_signal_month is None:
            return None
        return self.first_degraded_month - self.first_signal_month

    def to_dict(self) -> dict[str, Any]:
        """Render for the report and the findings table."""
        return {**asdict(self), "months_blind": self.months_blind}


def compute(
    scenario: str,
    calibration_gap_by_month: dict[int, float],
    drift_alarm_by_month: dict[int, bool],
    label_lag_months: int,
    degradation_gap_threshold: float,
) -> BlindWindow:
    """Measure the blind window for one scenario.

    A month counts as blind when the degradation is real and **neither** signal
    family can show it yet:

    * feature drift would show it now — but only if the drift rule actually
      fires for that month;
    * performance would show it — but only once the labels for some already
      degraded month have arrived, which takes ``label_lag_months``.

    Args:
        scenario: Which ablation arm.
        calibration_gap_by_month: Predicted mean minus observed, per month.
        drift_alarm_by_month: Whether the drift verdict was warning or worse.
        label_lag_months: Months between scoring and the outcome.
        degradation_gap_threshold: Negative; a gap at or below it is real
            degradation.

    Returns:
        The measurement.
    """
    months = sorted(calibration_gap_by_month)
    degraded = tuple(
        month
        for month in months
        if calibration_gap_by_month[month] <= degradation_gap_threshold
    )

    blind: list[int] = []
    for month in degraded:
        drift_visible = drift_alarm_by_month.get(month, False)
        # Labels are only informative once they belong to a month that was
        # already degrading; labels from a healthy month say nothing.
        labels_visible = any(
            earlier <= month - label_lag_months for earlier in degraded
        )
        if not drift_visible and not labels_visible:
            blind.append(month)

    first_degraded = degraded[0] if degraded else None

    # The earliest drift alarm of the whole run, degraded month or not. This
    # is the correction that makes the lead time signed: an alarm that fires
    # in month 2 for a degradation that starts in month 3 is one month of
    # warning, and restricting the search to degraded months threw that away.
    alarm_months = sorted(
        month for month, firing in drift_alarm_by_month.items() if firing
    )
    first_drift_alarm = alarm_months[0] if alarm_months else None

    first_signal: int | None = None
    if first_degraded is not None:
        candidates = [first_degraded + label_lag_months]
        if first_drift_alarm is not None:
            candidates.append(first_drift_alarm)
        first_signal = min(candidates)

    return BlindWindow(
        scenario=scenario,
        label_lag_months=label_lag_months,
        degraded_months=degraded,
        blind_months=tuple(blind),
        first_degraded_month=first_degraded,
        first_signal_month=first_signal,
        first_drift_alarm_month=first_drift_alarm,
    )
