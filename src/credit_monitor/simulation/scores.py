"""Rank-based normal scores, fitted on the reference set.

The simulator weights customers by how extreme their features are. The obvious
way to express "extreme" is a z-score, and on this dataset the obvious way is
wrong — twice over, both times documented:

* ``docs/findings.md`` §4: the three delinquency counters had standard
  deviation ~4.19 against mean ~0.42, and 269 rows (0.18%) produced almost all
  of that spread.
* ``docs/findings.md`` §6: ``RevolvingUtilizationOfUnsecuredLines`` has
  standard deviation 249.76 and a maximum of 50,708 against a mean of 6.05,
  where 241 rows (0.16%) dominate every moment-based statistic.

A z-score here would hand a few dozen rows astronomical sampling weight, and
the resulting "composition drift" would be the same trap as those two findings,
re-entered through the simulator. So extremeness is measured by **rank**, which
is invariant to any monotone transform of the column and therefore cannot be
moved by a tail.

Two details that matter:

**Mid-ranks for ties.** The counters are zero for the overwhelming majority of
rows. A left-continuous ECDF would map that modal value to ~0.0 and a
right-continuous one to ~0.95, so the most common value in the column would get
an extreme score either way. Averaging the two puts it near the middle, which
is where "the typical customer" belongs.

**Fitted on the reference, applied to the holdout.** The reference is the
baseline the monitor compares against, so it is also the right yardstick for
what counts as extreme. Fitting on each batch instead would re-centre the scale
every month and the drift would partly cancel itself out.
"""

from dataclasses import dataclass, field
from typing import Final

import numpy as np
import pandas as pd
from scipy.stats import norm

# Keeps norm.ppf away from its asymptotes: without it a value beyond every
# reference observation maps to +/-inf and its weight becomes 0 or inf.
CLIP_EPSILON: Final[float] = 1e-6


@dataclass(frozen=True, slots=True)
class RankNormalScorer:
    """Mid-rank ECDF of the reference, per column, as a reusable transform."""

    sorted_reference: dict[str, np.ndarray] = field(repr=False)

    @classmethod
    def fit(
        cls, reference: pd.DataFrame, columns: tuple[str, ...]
    ) -> "RankNormalScorer":
        """Store the sorted reference values for each column.

        Args:
            reference: The reference split.
            columns: Columns the simulator will score on.

        Returns:
            A fitted scorer.
        """
        return cls(
            sorted_reference={
                column: np.sort(reference[column].to_numpy(dtype="float64"))
                for column in columns
            }
        )

    def uniform(self, values: pd.Series) -> np.ndarray:
        """Mid-rank percentile of each value against the reference, in (0, 1).

        Args:
            values: Column to transform; its name selects the fitted column.

        Returns:
            Percentiles, clipped away from 0 and 1.
        """
        reference = self.sorted_reference[str(values.name)]
        target = values.to_numpy(dtype="float64")
        below = np.searchsorted(reference, target, side="left")
        at_or_below = np.searchsorted(reference, target, side="right")
        midrank = (below + at_or_below) / (2 * len(reference))
        return np.clip(midrank, CLIP_EPSILON, 1 - CLIP_EPSILON)

    def normal(self, values: pd.Series) -> np.ndarray:
        """Normal score: the inverse normal CDF of :meth:`uniform`.

        Puts every column on one interpretable scale — roughly mean 0, standard
        deviation 1 — so the ``b_*`` weights in the config are comparable to
        each other without knowing anything about the columns' units.
        """
        return norm.ppf(self.uniform(values))
