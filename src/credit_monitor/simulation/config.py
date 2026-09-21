"""Typed access to ``configs/simulation.yaml``.

The config is the experiment. Every number a reviewer might disagree with lives
there and nothing here supplies a default that silently disagrees with the
file — a missing key raises rather than falling back, because a simulation run
whose parameters came half from a file and half from a forgotten default is not
reproducible, and its frozen report is not checkable.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from credit_monitor.constants import PROJECT_ROOT

DEFAULT_CONFIG_PATH: Final[Path] = PROJECT_ROOT / "configs" / "simulation.yaml"


@dataclass(frozen=True, slots=True)
class CompositionParams:
    """Mechanism 1: the exponent weights and the cascade lags."""

    b_util: float
    b_age: float
    b_d30: float
    b_d60: float
    b_d90: float
    lag_d60_months: int
    lag_d90_months: int


@dataclass(frozen=True, slots=True)
class PairSpec:
    """One column pair whose dependence gets inverted."""

    label: str
    column_x: str
    column_y: str
    target_spearman: float


@dataclass(frozen=True, slots=True)
class MultivariateParams:
    """The dependence-only scenario: which pairs, and the target Spearmans.

    A list rather than a single pair, because the pair chosen by dependence
    strength turned out to be harmless to the model. Keeping both is what
    separates "univariate tests cannot see this" from "this does not matter" —
    two claims that the primary pair alone would have conflated.
    """

    tolerance: float
    pairs: tuple[PairSpec, ...]

    @property
    def primary(self) -> PairSpec:
        """The pair selected by the rule in ``docs/simulation.md`` §4."""
        return self.pairs[0]


@dataclass(frozen=True, slots=True)
class MechanismToggles:
    """Which mechanisms are active. The ablation walks these three."""

    composition: bool
    inflation: bool
    stress: bool

    def only(self, name: str) -> "MechanismToggles":
        """Return toggles with exactly one mechanism on."""
        return MechanismToggles(
            composition=name == "composition",
            inflation=name == "inflation",
            stress=name == "stress",
        )


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """The whole experiment, as data."""

    seed: int
    n_months: int
    batch_size: int
    composition: CompositionParams
    pi_total: float
    extra_default_rate_at_full: float
    multivariate: MultivariateParams
    mechanisms: MechanismToggles
    psi_bins: int
    psi_warn_threshold: float
    psi_alert_threshold: float

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "SimulationConfig":
        """Read and validate the YAML.

        Args:
            path: The config file.

        Returns:
            The parsed config.

        Raises:
            KeyError: A required key is missing.
        """
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            seed=int(raw["seed"]),
            n_months=int(raw["n_months"]),
            batch_size=int(raw["batch_size"]),
            composition=CompositionParams(**raw["composition"]),
            pi_total=float(raw["inflation"]["pi_total"]),
            extra_default_rate_at_full=float(
                raw["stress"]["extra_default_rate_at_full"]
            ),
            multivariate=MultivariateParams(
                tolerance=float(raw["multivariate"]["tolerance"]),
                pairs=tuple(PairSpec(**pair) for pair in raw["multivariate"]["pairs"]),
            ),
            mechanisms=MechanismToggles(**raw["mechanisms"]),
            psi_bins=int(raw["psi"]["bins"]),
            psi_warn_threshold=float(raw["psi"]["warn_threshold"]),
            psi_alert_threshold=float(raw["psi"]["alert_threshold"]),
        )

    def with_mechanisms(self, toggles: MechanismToggles) -> "SimulationConfig":
        """Return a copy with different mechanism toggles, for the ablation."""
        return SimulationConfig(**{**asdict_shallow(self), "mechanisms": toggles})

    def to_dict(self) -> dict[str, Any]:
        """Render the frozen parameters for the report."""
        return {
            "seed": self.seed,
            "n_months": self.n_months,
            "batch_size": self.batch_size,
            **{f"composition.{k}": v for k, v in asdict(self.composition).items()},
            "inflation.pi_total": self.pi_total,
            "stress.extra_default_rate_at_full": self.extra_default_rate_at_full,
            "multivariate.tolerance": self.multivariate.tolerance,
            "multivariate.pairs": [
                f"{p.label}: {p.column_x} / {p.column_y} -> {p.target_spearman}"
                for p in self.multivariate.pairs
            ],
            **{f"mechanisms.{k}": v for k, v in asdict(self.mechanisms).items()},
            "psi.bins": self.psi_bins,
        }

    def intensity(self, month: int) -> float:
        """Shock intensity ``s = month / n_months``; month 0 is the control."""
        return month / self.n_months

    def lag_ramp(self, month: int, lag_months: int) -> float:
        """Zero before ``lag_months``, then a linear ramp to 1 at the last month.

        What makes the cascade of the DAG a cascade: the 60-89 and 90+ buckets
        stay flat while the 30-59 bucket is already moving, then catch up.
        """
        if month <= lag_months:
            return 0.0
        return (month - lag_months) / (self.n_months - lag_months)


def asdict_shallow(config: SimulationConfig) -> dict[str, Any]:
    """Field-by-field copy that keeps nested dataclasses as objects.

    ``dataclasses.asdict`` recurses and would turn the nested params into
    dicts, which the constructor does not accept.
    """
    return {
        field: getattr(config, field) for field in SimulationConfig.__dataclass_fields__
    }
