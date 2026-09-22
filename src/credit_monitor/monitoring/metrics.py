"""Metrics for a batch job, pushed to the Pushgateway.

**Why push and not scrape.** The drift job is a batch: it runs, reports, and
exits. There is no process left standing for Prometheus to scrape, and a
``/metrics`` endpoint that exists for two seconds would essentially never
coincide with a scrape interval. The Pushgateway is the documented bridge for
exactly this shape of job.

**Why the grouping key carries the batch id.** The Pushgateway **rejects client
timestamps** — every sample it holds is stamped with the moment Prometheus
scrapes it, not the moment the job produced it. Pushing months 0 through 6 back
to back would therefore collapse a six-month ramp into one instant, and the
whole point of the stage-2 scenario is that the ramp is visible.

The way out is not a timestamp; it is a label. The grouping key is
``{job, scenario, batch_id}``, so each month persists as its own series and
Grafana plots the ramp by label instead of by time. ``scenario`` carries the
stage-2 ablation arms, so day 10 can put the 2x2 on one screen.

Every metric is a **gauge**. A counter would be wrong: these are the state of
one finished batch, not a monotonically increasing total, and a job that reruns
must overwrite its own numbers rather than add to them.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Final

from prometheus_client import (
    CollectorRegistry,
    Gauge,
    delete_from_gateway,
    push_to_gateway,
)

log = logging.getLogger(__name__)

DEFAULT_GATEWAY: Final[str] = "localhost:9091"

SCORING_JOB: Final[str] = "credit_monitor_scoring"
LABEL_JOB: Final[str] = "credit_monitor_labels"

# Numeric codes for the drift verdict. Prometheus stores float64, so a verdict
# has to be a number; the mapping is documented in the README and here, and
# -1 is deliberately outside the 0..2 ordering so a dashboard cannot read
# "insufficient sample" as "less drift than ok".
VERDICT_OK: Final[int] = 0
VERDICT_WARNING: Final[int] = 1
VERDICT_CRITICAL: Final[int] = 2
VERDICT_INSUFFICIENT_SAMPLE: Final[int] = -1
VERDICT_BLOCKED: Final[int] = -2
"""The contract refused the batch; no drift was computed at all.

Distinct from every other code, and negative for the same reason ``-1`` is: a
dashboard sorting by the number must not read "blocked" as a position on the
drift scale. There is no drift reading for a blocked batch — not a low one, not
a high one. Computing a colour from rows the contract rejected would be
describing data the pipeline decided not to trust.
"""

# stage_status is 1 ok / 0 failed, and -1 for a stage that never ran because an
# earlier one blocked. Zero would be a lie (the stage did not fail, it was
# never attempted) and 1 would be a worse one.
STAGE_OK: Final[int] = 1
STAGE_FAILED: Final[int] = 0
STAGE_SKIPPED: Final[int] = -1


@dataclass(frozen=True, slots=True)
class ScoringMetrics:
    """What one scoring-time run reports. No labels anywhere in here."""

    rows_in: int
    rows_quarantined: int
    batch_size: int
    sample_sufficient: bool
    drift_verdict: int
    last_success_timestamp_seconds: float
    prediction_mean: float | None = None
    """``None`` when the batch was blocked and never scored."""

    prediction_psi: float | None = None
    drifted_warning: int | None = None
    drifted_critical: int | None = None
    labels_pending: bool = False
    """Whether this batch has been scored but its outcome has not arrived.

    Summed per scenario by a recording rule into ``labels_pending_batches``,
    which is what puts the blind window on screen instead of leaving it
    implicit in an absence of data.
    """

    gain_share_by_feature: dict[str, float] = field(default_factory=dict)
    """Champion gain share per feature, pushed so alert rules can join on it.

    Severity has to follow impact, not magnitude — the PSI x gain principle of
    day 7 and the MMD harm-ranking finding of day 8. A rule cannot weigh a
    drift by the feature's importance unless the importance is a series it can
    join against.
    """

    psi_by_feature: dict[str, float] = field(default_factory=dict)
    psi_weighted_by_feature: dict[str, float] = field(default_factory=dict)
    violations: dict[tuple[str, str], int] = field(default_factory=dict)
    stage_durations_seconds: dict[str, float] = field(default_factory=dict)
    stage_status: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LabelMetrics:
    """What one label-arrival run reports, months after the scoring run."""

    auc: float
    brier: float
    calibration_gap: float
    observed_default_rate: float
    label_lag_months: int


def grouping_key(scenario: str, batch_id: str) -> dict[str, str]:
    """The Pushgateway grouping key: one persistent series per batch.

    ``job`` is supplied separately by the push call; these are the labels that
    make two pushes distinct groups rather than one overwriting the other.
    """
    return {"scenario": scenario, "batch_id": batch_id}


def build_scoring_registry(metrics: ScoringMetrics) -> CollectorRegistry:
    """Build a fresh registry holding one scoring run's gauges.

    A new registry per call, never the global default: the default is process
    wide, so a job that reported two batches would carry the first batch's
    label values into the second push.
    """
    registry = CollectorRegistry()

    simple = {
        "rows_in": ("linhas recebidas no lote", float(metrics.rows_in)),
        "rows_quarantined": (
            "linhas em quarentena por regra bloqueante",
            float(metrics.rows_quarantined),
        ),
        "batch_size": ("tamanho do lote pontuado", float(metrics.batch_size)),
        "sample_sufficient": (
            "1 se o lote atinge min_batch_size, 0 se não",
            float(metrics.sample_sufficient),
        ),
        "drift_verdict": (
            "veredito de drift: 0 ok, 1 alerta, 2 crítico, -1 amostra insuficiente",
            float(metrics.drift_verdict),
        ),
        "last_success_timestamp_seconds": (
            "instante unix da última execução bem-sucedida",
            metrics.last_success_timestamp_seconds,
        ),
        "labels_pending": (
            "1 se o lote foi pontuado e o desfecho ainda não chegou",
            float(metrics.labels_pending),
        ),
    }
    # Absent, not zero. A blocked batch has no prediction mean, and publishing
    # 0 would put a plausible-looking number on a dashboard for a batch that
    # was never scored — the worst of the three options, because it is the
    # only one a reader cannot tell from a real measurement.
    if metrics.prediction_mean is not None:
        simple["prediction_mean"] = (
            "probabilidade média prevista pelo campeão",
            metrics.prediction_mean,
        )
    if metrics.prediction_psi is not None:
        simple["prediction_psi"] = (
            "PSI da distribuição de score contra a referência",
            metrics.prediction_psi,
        )
    for name, (documentation, value) in simple.items():
        Gauge(name, documentation, registry=registry).set(value)

    psi = Gauge(
        "drift_psi",
        "PSI por feature contra a referência da etapa 1",
        ["feature"],
        registry=registry,
    )
    for feature, value in metrics.psi_by_feature.items():
        psi.labels(feature=feature).set(value)

    # PSI weighted by the champion's gain share (day 7): a drift of 0.30 on a
    # feature carrying 28% of the gain is not the same event as the same drift
    # on one carrying 2%, and a dashboard sorted by raw PSI presents them as
    # if it were.
    weighted = Gauge(
        "drift_psi_weighted",
        "PSI x participação da feature no ganho do campeão",
        ["feature"],
        registry=registry,
    )
    for feature, value in metrics.psi_weighted_by_feature.items():
        weighted.labels(feature=feature).set(value)

    # Pushed so an alert rule can JOIN on it. Severity has to follow impact,
    # not magnitude — the PSI x gain principle of day 7 and the MMD
    # harm-ranking finding of day 8 — and a rule cannot weigh a drift by the
    # feature's importance unless the importance is itself a series.
    gain = Gauge(
        "feature_gain_share",
        "participação da feature no ganho do campeão",
        ["feature"],
        registry=registry,
    )
    for feature, value in metrics.gain_share_by_feature.items():
        gain.labels(feature=feature).set(value)

    if metrics.drifted_warning is not None and metrics.drifted_critical is not None:
        drifted = Gauge(
            "drifted_features",
            "quantas features cruzaram cada faixa de PSI",
            ["level"],
            registry=registry,
        )
        drifted.labels(level="warning").set(float(metrics.drifted_warning))
        drifted.labels(level="critical").set(float(metrics.drifted_critical))

    violations = Gauge(
        "contract_violations",
        "linhas que violaram cada regra do contrato",
        ["rule", "severity"],
        registry=registry,
    )
    for (rule, severity), count in metrics.violations.items():
        violations.labels(rule=rule, severity=severity).set(float(count))

    duration = Gauge(
        "stage_duration_seconds",
        "duração de cada estágio do pipeline",
        ["stage"],
        registry=registry,
    )
    for stage, seconds in metrics.stage_durations_seconds.items():
        duration.labels(stage=stage).set(seconds)

    status = Gauge(
        "stage_status",
        "1 se o estágio terminou ok, 0 se falhou",
        ["stage"],
        registry=registry,
    )
    for stage, ok in metrics.stage_status.items():
        status.labels(stage=stage).set(float(ok))

    return registry


def build_label_registry(metrics: LabelMetrics) -> CollectorRegistry:
    """Build a registry for one label-arrival run."""
    registry = CollectorRegistry()
    values = {
        "auc": ("AUC-ROC do campeão no lote", metrics.auc),
        "brier": ("Brier score do campeão no lote", metrics.brier),
        "calibration_gap": (
            "previsto médio menos observado — o sinal de degradação silenciosa",
            metrics.calibration_gap,
        ),
        "observed_default_rate": (
            "taxa de inadimplência observada no lote",
            metrics.observed_default_rate,
        ),
        "label_lag_months": (
            "meses entre a pontuação e a chegada do rótulo",
            float(metrics.label_lag_months),
        ),
    }
    for name, (documentation, value) in values.items():
        Gauge(name, documentation, registry=registry).set(value)
    return registry


def push(
    registry: CollectorRegistry,
    job: str,
    scenario: str,
    batch_id: str,
    gateway: str = DEFAULT_GATEWAY,
) -> None:
    """Push one registry under its grouping key."""
    push_to_gateway(
        gateway,
        job=job,
        registry=registry,
        grouping_key=grouping_key(scenario, batch_id),
    )
    log.info(
        "monitor.pushed",
        extra={"job": job, "scenario": scenario, "batch_id": batch_id},
    )


def delete_group(
    job: str, scenario: str, batch_id: str, gateway: str = DEFAULT_GATEWAY
) -> None:
    """Delete a stale group before re-pushing it.

    The Pushgateway keeps whatever it was last told until something deletes it,
    so a rerun with fewer labels — one less violated rule, say — would leave
    the old label set behind and the dashboard would show a violation that no
    longer exists.
    """
    try:
        delete_from_gateway(
            gateway, job=job, grouping_key=grouping_key(scenario, batch_id)
        )
    except Exception as error:
        log.debug("monitor.delete_skipped", extra={"error": str(error)})


def registry_samples(registry: CollectorRegistry) -> dict[str, Any]:
    """Flatten a registry into ``{name{labels}: value}``, for tests and printing."""
    samples: dict[str, Any] = {}
    for metric in registry.collect():
        for sample in metric.samples:
            key = sample.name
            if sample.labels:
                rendered = ",".join(
                    f'{k}="{v}"' for k, v in sorted(sample.labels.items())
                )
                key = f"{sample.name}{{{rendered}}}"
            samples[key] = sample.value
    return samples
