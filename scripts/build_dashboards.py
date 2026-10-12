"""Generate the Grafana dashboards as JSON.

Dashboard JSON is written by a machine and read by nobody: forty lines of
nested objects to say "a bar chart of PSI by month". Generating it keeps the
reasoning in Python, where a comment can explain *why* a panel is an instant
query with a label-to-field transform instead of a time series — which is the
one thing about these dashboards that is not obvious.

**Why instant queries keyed by ``batch_id``.** The Pushgateway rejects client
timestamps (day 9), so every month's sample carries the scrape time, not the
month. Plotting these against time would draw seven months as one vertical
line. The batch id is the x-axis, ordered ``month_00 .. month_06``.

Run as ``uv run python scripts/build_dashboards.py`` (or ``make dashboards``).
"""

import json
from typing import Any

from credit_monitor.constants import PROJECT_ROOT
from credit_monitor.data.preprocess import MODEL_FEATURES
from credit_monitor.monitoring.config import MonitoringConfig

OUT = PROJECT_ROOT / "monitoring" / "grafana" / "dashboards"
# The last batch whose labels have arrived. The simulation runs months 0..6 and
# `label_lag_months` is 2, so 5 and 6 are scored but never labelled. Anything
# label-based on a fixed month has to point here or it renders empty.
LAST_LABELLED = "month_04"
MONTHS = tuple(f"month_{m:02d}" for m in range(7))

# The calibration gap is shown to six decimals, not four. composition_only's
# month-4 gap is -0.0000209: at four decimals it reads "-0.0000", which is a
# claim of exact zero the data does not make. Six keeps the order of magnitude
# visible on every scenario (full -0.020971, composition_only -0.000021).
GAP_DECIMALS = 6

PROM: dict[str, str] = {"type": "prometheus", "uid": "prometheus"}
LOKI: dict[str, str] = {"type": "loki", "uid": "loki"}
SCENARIOS = ("full", "composition_only", "stress_only")

# Verdict codes as text and colour. A raw negative on screen invites reading
# "-2" as "less drift than 0"; the codes are sentinels, not positions.
VERDICT_MAP: list[dict[str, Any]] = [
    {
        "options": {
            "-2": {"text": "BLOQUEADO", "color": "dark-red", "index": 0},
            "-1": {"text": "amostra insuficiente", "color": "text", "index": 1},
            "0": {"text": "ok", "color": "green", "index": 2},
            "1": {"text": "alerta", "color": "orange", "index": 3},
            "2": {"text": "crítico", "color": "red", "index": 4},
        },
        "type": "value",
    }
]
STAGE_MAP: list[dict[str, Any]] = [
    {
        "options": {
            "-1": {"text": "pulado", "color": "text", "index": 0},
            "0": {"text": "FALHOU", "color": "red", "index": 1},
        },
        "type": "value",
    }
]


def target(
    expr: str, legend: str = "{{batch_id}}", fmt: str = "table"
) -> dict[str, Any]:
    """An instant query — see the module docstring for why never a range."""
    return {
        "datasource": PROM,
        "expr": expr,
        "legendFormat": legend,
        "instant": True,
        "range": False,
        "format": fmt,
        "refId": "A",
    }


def stat(
    title: str,
    expr: str,
    gx: int,
    gy: int,
    w: int,
    h: int,
    mappings: list[dict[str, Any]] | None = None,
    unit: str | None = None,
    decimals: int | None = None,
    legend: str = "{{scenario}}",
) -> dict[str, Any]:
    """A single big number, coloured by its value mapping."""
    defaults: dict[str, Any] = {
        "mappings": mappings or [],
        "color": {"mode": "thresholds"},
        "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]},
    }
    if unit:
        defaults["unit"] = unit
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "type": "stat",
        "title": title,
        "datasource": PROM,
        "gridPos": {"x": gx, "y": gy, "w": w, "h": h},
        "targets": [target(expr, legend, "time_series")],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "value_and_name",
            "colorMode": "background",
            "orientation": "horizontal",
        },
    }


def barchart(
    title: str,
    expr: str,
    gx: int,
    gy: int,
    w: int,
    h: int,
    unit: str | None = None,
    decimals: int = 3,
    legend: str = "{{batch_id}}",
    rotate: bool = False,
) -> dict[str, Any]:
    """Bars keyed by ``batch_id``, sorted so the months read left to right.

    ``rotate`` angles the tick labels, for panels a third of the screen wide
    that also carry ``small_demo`` and ``dirty_batch``: nine ids do not fit
    level there and run into each other.
    """
    options: dict[str, Any] = {
        "xField": "batch_id",
        "orientation": "vertical",
        "showValue": "auto",
        "legend": {"displayMode": "list", "placement": "bottom"},
    }
    if rotate:
        options["xTickLabelRotation"] = -45
    return {
        "type": "barchart",
        "title": title,
        "datasource": PROM,
        "gridPos": {"x": gx, "y": gy, "w": w, "h": h},
        "targets": [target(expr, legend)],
        "transformations": [
            {
                "id": "labelsToFields",
                "options": {
                    "mode": "columns",
                    "keepLabels": ["batch_id", "scenario", "feature", "stage", "level"],
                },
            },
            {
                "id": "sortBy",
                "options": {"fields": {}, "sort": [{"field": "batch_id"}]},
            },
        ],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "decimals": decimals,
                "custom": {"lineWidth": 1, "fillOpacity": 80},
            },
            "overrides": [],
        },
        "options": options,
    }


def grouped_barchart(
    title: str,
    expr: str,
    series_label: str,
    gx: int,
    gy: int,
    w: int,
    h: int,
    unit: str | None = None,
    decimals: int | None = 3,
    stacking: str = "none",
    rename: dict[str, str] | None = None,
    colours: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One bar group per batch, one series per ``series_label`` value.

    The plain ``barchart`` draws one bar per *series*, so a metric with a second
    label (stage, level) put ``month_00`` under the axis three times and the
    labels ran into each other. ``groupingToMatrix`` turns the rows into one row
    per batch and one column per label value: nine tick labels instead of 25.
    """
    matrix_key = f"batch_id\\{series_label}"
    overrides = [
        {
            "matcher": {"id": "byName", "options": name},
            "properties": [
                {"id": "color", "value": {"mode": "fixed", "fixedColor": colour}}
            ],
        }
        for name, colour in (colours or {}).items()
    ]
    return {
        "type": "barchart",
        "title": title,
        "datasource": PROM,
        "gridPos": {"x": gx, "y": gy, "w": w, "h": h},
        "targets": [target(expr, "")],
        "transformations": [
            {
                "id": "groupingToMatrix",
                "options": {
                    "rowField": "batch_id",
                    "columnField": series_label,
                    "valueField": "Value",
                    "emptyValue": "null",
                },
            },
            {
                "id": "organize",
                "options": {"renameByName": {matrix_key: "lote", **(rename or {})}},
            },
            {"id": "sortBy", "options": {"fields": {}, "sort": [{"field": "lote"}]}},
        ],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "decimals": decimals,
                "custom": {"lineWidth": 1, "fillOpacity": 80},
            },
            "overrides": overrides,
        },
        "options": {
            "xField": "lote",
            "orientation": "vertical",
            "stacking": stacking,
            "showValue": "never",
            # Angled: nine batch ids side by side do not fit level in a third
            # of the screen width, and this panel goes on screen in the video.
            "xTickLabelRotation": -45,
            "xTickLabelSpacing": 0,
            "legend": {"displayMode": "list", "placement": "bottom"},
        },
    }


def psi_heatmap(
    title: str, scenario_filter: str, gx: int, gy: int, w: int, h: int
) -> dict[str, Any]:
    """PSI per feature (rows) and month (columns), each cell coloured by band.

    A table with coloured cells, not Grafana's heatmap panel. The heatmap panel
    needs a time or numeric x-axis; with series keyed by ``batch_id`` it fell
    back to the scrape time, drew 07:30..13:00 across the bottom and no feature
    on the side — the same trap the module docstring describes, and the one
    panel that still had it. ``groupingToMatrix`` builds the grid from the
    instant query directly: ``batch_id`` across, ``feature`` down.

    Colour is the alert bands from configs/monitoring.yaml, not a continuous
    scale: the question this panel answers is which feature crossed which line
    in which month, and the bands are where those lines are.
    """
    config = MonitoringConfig.load()
    matrix_key = "feature\\batch_id"
    return {
        "type": "table",
        "title": title,
        "description": (
            f"Verde < {config.psi_warn_threshold:.2f} ≤ laranja < "
            f"{config.psi_alert_threshold:.2f} ≤ vermelho. Linhas ordenadas pelo "
            "PSI do mês 6: a feature que mais derivou fica no topo."
        ),
        "datasource": PROM,
        "gridPos": {"x": gx, "y": gy, "w": w, "h": h},
        # Months only: small_demo carries a PSI too, but it is a 200-row batch
        # whose PSI is mostly sampling noise, and it is not a month.
        "targets": [target(f'drift_psi{{{scenario_filter},batch_id=~"month_.*"}}', "")],
        "transformations": [
            {
                "id": "groupingToMatrix",
                "options": {
                    "rowField": "feature",
                    "columnField": "batch_id",
                    "valueField": "Value",
                    "emptyValue": "null",
                },
            },
            {
                "id": "organize",
                "options": {
                    "renameByName": {matrix_key: "feature"},
                    # Column order is otherwise the order the series arrive in.
                    "indexByName": {
                        matrix_key: 0,
                        **{month: i + 1 for i, month in enumerate(MONTHS)},
                    },
                },
            },
        ],
        "fieldConfig": {
            "defaults": {
                "decimals": 3,
                "color": {"mode": "thresholds"},
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "orange", "value": config.psi_warn_threshold},
                        {"color": "red", "value": config.psi_alert_threshold},
                    ],
                },
                "custom": {
                    "align": "center",
                    # Small enough that the seven months fit beside the feature
                    # names without a horizontal scrollbar hiding months 5 and 6.
                    "minWidth": 60,
                    "cellOptions": {"type": "color-background", "mode": "basic"},
                },
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "feature"},
                    "properties": [
                        {"id": "custom.cellOptions", "value": {"type": "auto"}},
                        {"id": "custom.align", "value": "left"},
                        {"id": "custom.width", "value": 330},
                    ],
                }
            ],
        },
        "options": {
            "showHeader": True,
            "cellHeight": "sm",
            "sortBy": [{"displayName": MONTHS[-1], "desc": True}],
        },
    }


def table(
    title: str,
    expr: str,
    gx: int,
    gy: int,
    w: int,
    h: int,
    mappings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A table of label sets, for the panels whose point is the labels."""
    return {
        "type": "table",
        "title": title,
        "datasource": PROM,
        "gridPos": {"x": gx, "y": gy, "w": w, "h": h},
        "targets": [target(expr, "")],
        "transformations": [
            {
                "id": "labelsToFields",
                "options": {
                    "mode": "columns",
                    "keepLabels": [
                        "scenario",
                        "batch_id",
                        "rule",
                        "severity",
                        "stage",
                        "feature",
                    ],
                },
            },
            {
                "id": "organize",
                "options": {
                    "excludeByName": {"Time": True, "job": True, "instance": True}
                },
            },
        ],
        "fieldConfig": {"defaults": {"mappings": mappings or []}, "overrides": []},
        "options": {"showHeader": True},
    }


def logs(title: str, expr: str, gx: int, gy: int, w: int, h: int) -> dict[str, Any]:
    """A Loki panel; the expression carries the batch/scenario filter."""
    return {
        "type": "logs",
        "title": title,
        "datasource": LOKI,
        "gridPos": {"x": gx, "y": gy, "w": w, "h": h},
        "targets": [
            {"datasource": LOKI, "expr": expr, "queryType": "range", "refId": "A"}
        ],
        "options": {
            "showTime": True,
            "wrapLogMessage": True,
            "sortOrder": "Descending",
        },
    }


def row(title: str, gy: int) -> dict[str, Any]:
    """A section header."""
    return {
        "type": "row",
        "title": title,
        "gridPos": {"x": 0, "y": gy, "w": 24, "h": 1},
        "collapsed": False,
        "panels": [],
    }


def dashboard(
    uid: str,
    title: str,
    description: str,
    panels: list[dict[str, Any]],
    templating: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Wrap panels into a dashboard, with the cross-links between the three."""
    return {
        "uid": uid,
        "title": title,
        "description": description,
        "tags": ["credit-monitor"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "editable": False,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "templating": {"list": templating or []},
        "links": [
            {
                "type": "dashboards",
                "title": "Credit Monitor",
                "tags": ["credit-monitor"],
                "asDropdown": True,
                "includeVars": True,
                "keepTime": True,
            }
        ],
        "panels": panels,
    }


SCENARIO_VAR: dict[str, Any] = {
    "name": "scenario",
    "type": "custom",
    "label": "cenário",
    "query": ",".join(SCENARIOS),
    "current": {"text": "full", "value": "full"},
    "options": [{"text": s, "value": s, "selected": s == "full"} for s in SCENARIOS],
}

# The feature list is the model's, fixed in code: a custom variable, not a
# `label_values(drift_psi, feature)` query. The query version had a default in
# the JSON and still opened on "No data" in all five panels on Windows: with
# `refresh: 1` it runs once on dashboard load, and a dashboard opened before
# `make monitor-all` pushed anything gets an empty list, drops the default and
# never asks again. A static list cannot be empty.
#
# Default: the feature the champion leans on most. NumberOfTimes90DaysLate
# carries 28.47% of the gain, six points ahead of the next one
# (NumberOfTime30-59DaysPastDueNotWorse, 22.17%) — a margin the cross-platform
# XGBoost difference (findings §17) does not come close to moving.
DEFAULT_FEATURE = "NumberOfTimes90DaysLate"
assert DEFAULT_FEATURE in MODEL_FEATURES

FEATURE_VAR: dict[str, Any] = {
    "name": "feature",
    "type": "custom",
    "label": "feature",
    "query": ",".join(MODEL_FEATURES),
    "current": {"text": DEFAULT_FEATURE, "value": DEFAULT_FEATURE},
    "options": [
        {"text": f, "value": f, "selected": f == DEFAULT_FEATURE}
        for f in MODEL_FEATURES
    ],
}


def build_overview() -> dict[str, Any]:
    """One row per scenario. The 2x2 has to be readable on this screen alone."""
    panels = [row("Veredito por cenário — o 2x2 numa tela", 0)]
    y = 1
    for scenario in SCENARIOS:
        panels += [
            stat(
                f"{scenario} · drift (mês 6)",
                f'drift_verdict{{scenario="{scenario}",batch_id="month_06"}}',
                0,
                y,
                6,
                4,
                VERDICT_MAP,
                legend="veredito",
            ),
            # Month 4, not month 6, and this is the label lag made visible.
            # Drift exists at scoring time; AUC and the calibration gap need
            # the outcome, which arrives `label_lag_months` = 2 later — so
            # month 6 has no labelled metrics and never will inside a
            # seven-month run. Pinned to month 6, these two panels read "No
            # data" forever. They did not look broken for a while only because
            # the Pushgateway was serving stale series from earlier runs; the
            # day-10 wipe fix is what exposed them.
            stat(
                f"{scenario} · gap de calibração (mês 4, último com rótulo)",
                f'calibration_gap{{scenario="{scenario}",batch_id="{LAST_LABELLED}"}}',
                6,
                y,
                6,
                4,
                decimals=GAP_DECIMALS,
                legend="gap",
            ),
            stat(
                f"{scenario} · AUC (mês 4, último com rótulo)",
                f'auc{{scenario="{scenario}",batch_id="{LAST_LABELLED}"}}',
                12,
                y,
                6,
                4,
                decimals=4,
                legend="AUC",
            ),
            stat(
                f"{scenario} · lotes sem desfecho",
                f'labels_pending_batches{{scenario="{scenario}"}}',
                18,
                y,
                6,
                4,
                legend="pendentes",
            ),
        ]
        y += 4
    panels += [
        row("Pipeline e alertas", y),
        table(
            "Estágios com falha ou pulados",
            "stage_status != 1",
            0,
            y + 1,
            12,
            6,
            STAGE_MAP,
        ),
        {
            "type": "alertlist",
            "title": "Alertas disparando",
            "gridPos": {"x": 12, "y": y + 1, "w": 12, "h": 6},
            "options": {
                "showOptions": "current",
                "maxItems": 20,
                "sortOrder": 1,
                "stateFilter": {"firing": True, "pending": True},
            },
        },
    ]
    return dashboard(
        "cm-overview",
        "Credit Monitor · Visão geral",
        "Uma linha por cenário. O 2x2 da etapa 2 tem de ser legível só nesta "
        "tela: composition_only com drift crítico e calibração intacta, "
        "stress_only com drift ok e o modelo degradado.",
        panels,
    )


def build_drilldown() -> dict[str, Any]:
    """Pipeline, drift, model health and logs, filtered by scenario."""
    panels = [
        row("Pipeline", 0),
        # No fixed decimals: the stages take tens of milliseconds, and "s" with
        # three decimals rendered the axis as "80.000 ms".
        grouped_barchart(
            "Duração por estágio",
            'stage_duration_seconds{scenario="$scenario"}',
            "stage",
            0,
            1,
            8,
            7,
            "s",
            None,
        ),
        barchart(
            "Linhas em quarentena",
            'rows_quarantined{scenario="$scenario"}',
            8,
            1,
            8,
            7,
            None,
            0,
            rotate=True,
        ),
        table(
            "Violações de contrato por regra e severidade",
            'contract_violations{scenario="$scenario"}',
            16,
            1,
            8,
            7,
        ),
        row("Drift", 8),
        psi_heatmap(
            "PSI por feature e mês (mapa de calor)",
            'scenario="$scenario"',
            0,
            9,
            16,
            13,
        ),
        barchart(
            "PSI ponderado pelo ganho (o que importa)",
            'batch:drift_psi_weighted:max{scenario="$scenario"}',
            16,
            9,
            8,
            13,
            None,
            4,
            rotate=True,
        ),
        barchart(
            "PSI do score previsto",
            'prediction_psi{scenario="$scenario"}',
            0,
            22,
            12,
            6,
            None,
            4,
        ),
        grouped_barchart(
            "Features em faixa de alerta e crítica",
            'drifted_features{scenario="$scenario"}',
            "level",
            12,
            22,
            12,
            6,
            None,
            0,
            stacking="normal",
            rename={"warning": "alerta", "critical": "crítico"},
            colours={"alerta": "orange", "crítico": "red"},
        ),
        row("Saúde do modelo (só onde o rótulo chegou)", 28),
        barchart("AUC por mês", 'auc{scenario="$scenario"}', 0, 29, 8, 7, None, 4),
        barchart("Brier por mês", 'brier{scenario="$scenario"}', 8, 29, 8, 7, None, 4),
        barchart(
            "Gap de calibração por mês",
            'calibration_gap{scenario="$scenario"}',
            16,
            29,
            8,
            7,
            None,
            GAP_DECIMALS,
        ),
        row("Logs", 36),
        logs(
            "Logs do pipeline (Loki)",
            '{job="credit-monitor", scenario="$scenario"}',
            0,
            37,
            24,
            10,
        ),
    ]
    return dashboard(
        "cm-drilldown",
        "Credit Monitor · Detalhe",
        "Pipeline, drift, saúde do modelo e logs. Os meses sem rótulo aparecem "
        "AUSENTES nos painéis de saúde, não como zero: zero seria "
        "indistinguível de uma medição real.",
        panels,
        [SCENARIO_VAR],
    )


def build_feature() -> dict[str, Any]:
    """One feature at a time, with its gain share beside its drift."""
    panels = [
        stat(
            "Participação no ganho do campeão",
            'max(feature_gain_share{feature="$feature"})',
            0,
            0,
            8,
            4,
            None,
            "percentunit",
            2,
            "ganho",
        ),
        stat(
            "PSI máximo entre os meses",
            'max(drift_psi{feature="$feature",scenario="$scenario"})',
            8,
            0,
            8,
            4,
            None,
            None,
            4,
            "PSI máx",
        ),
        stat(
            "PSI ponderado máximo",
            'max(drift_psi_weighted{feature="$feature",scenario="$scenario"})',
            16,
            0,
            8,
            4,
            None,
            None,
            4,
            "ponderado",
        ),
        barchart(
            "PSI por mês",
            'drift_psi{feature="$feature",scenario="$scenario"}',
            0,
            4,
            12,
            8,
            None,
            4,
        ),
        barchart(
            "PSI ponderado pelo ganho, por mês",
            'drift_psi_weighted{feature="$feature",scenario="$scenario"}',
            12,
            4,
            12,
            8,
            None,
            5,
        ),
    ]
    return dashboard(
        "cm-feature",
        "Credit Monitor · Feature",
        "Uma feature por vez: PSI, PSI ponderado e a participação dela no ganho "
        "do campeão lado a lado. O KS fica FORA do veredito de propósito — a "
        "etapa 2 mediu 21,5% de falso alarme sem correção; ele é diagnóstico e "
        "vive em reports/drift_tests/summary.md.",
        panels,
        [SCENARIO_VAR, FEATURE_VAR],
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    built = {
        "00-overview.json": build_overview(),
        "10-drilldown.json": build_drilldown(),
        "20-feature.json": build_feature(),
    }
    for name, payload in built.items():
        path = OUT / name
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"  {name:20} uid={payload['uid']:14} painéis={len(payload['panels'])}")


if __name__ == "__main__":
    main()
