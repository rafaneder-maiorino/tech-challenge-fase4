"""Typed access to ``configs/monitoring.yaml``."""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

from credit_monitor.constants import PROJECT_ROOT

DEFAULT_CONFIG_PATH: Final[Path] = PROJECT_ROOT / "configs" / "monitoring.yaml"


@dataclass(frozen=True, slots=True)
class MonitoringConfig:
    """The drift policy, measured in the stage-2 A/A test."""

    psi_warn_threshold: float
    psi_alert_threshold: float
    min_batch_size: int
    ks_alpha: float
    ks_decides_verdict: bool
    mmd_enabled: bool

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "MonitoringConfig":
        """Read the YAML; a missing key raises rather than defaulting."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            psi_warn_threshold=float(raw["psi_warn_threshold"]),
            psi_alert_threshold=float(raw["psi_alert_threshold"]),
            min_batch_size=int(raw["min_batch_size"]),
            ks_alpha=float(raw["ks_alpha"]),
            ks_decides_verdict=bool(raw["ks_decides_verdict"]),
            mmd_enabled=bool(raw["mmd_enabled"]),
        )
