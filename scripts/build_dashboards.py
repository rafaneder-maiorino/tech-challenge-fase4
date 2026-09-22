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

OUT = PROJECT_ROOT / "monitoring" / "grafana" / "dashboards"
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
) -> dict[str, Any]:
    """Bars keyed by ``batch_id``, sorted so the months read left to right."""
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
        "options": {
            "xField": "batch_id",
            "orientation": "vertical",
            "showValue": "auto",
            "legend": {"displayMode": "list", "placement": "bottom"},
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

FEATURE_VAR: dict[str, Any] = {
    "name": "feature",
    "type": "query",
    "label": "feature",
    "datasource": PROM,
    "query": {"query": "label_values(drift_psi, feature)", "refId": "A"},
    "refresh": 1,
    "current": {
        "text": "RevolvingUtilizationOfUnsecuredLines",
        "value": "RevolvingUtilizationOfUnsecuredLines",
    },
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
            stat(
                f"{scenario} · gap de calibração (mês 6)",
                f'calibration_gap{{scenario="{scenario}",batch_id="month_06"}}',
                6,
                y,
                6,
                4,
                decimals=4,
                legend="gap",
            ),
            stat(
                f"{scenario} · AUC (mês 6)",
                f'auc{{scenario="{scenario}",batch_id="month_06"}}',
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
        barchart(
            "Duração por estágio (s)",
            'stage_duration_seconds{scenario="$scenario"}',
            0,
            1,
            8,
            7,
            "s",
            3,
            "{{batch_id}} · {{stage}}",
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
        {
            "type": "heatmap",
            "title": "PSI por feature e mês (mapa de calor)",
            "datasource": PROM,
            "gridPos": {"x": 0, "y": 9, "w": 16, "h": 9},
            "targets": [target('drift_psi{scenario="$scenario"}', "{{feature}}")],
            "transformations": [
                {
                    "id": "labelsToFields",
                    "options": {
                        "mode": "columns",
                        "keepLabels": ["batch_id", "feature"],
                    },
                }
            ],
            "options": {
                "calculate": False,
                "color": {"mode": "scheme", "scheme": "Oranges", "steps": 64},
                "yAxis": {"axisLabel": "feature"},
                "cellGap": 2,
            },
        },
        barchart(
            "PSI ponderado pelo ganho (o que importa)",
            'batch:drift_psi_weighted:max{scenario="$scenario"}',
            16,
            9,
            8,
            9,
            None,
            4,
        ),
        barchart(
            "PSI do score previsto",
            'prediction_psi{scenario="$scenario"}',
            0,
            18,
            12,
            6,
            None,
            4,
        ),
        barchart(
            "Features em faixa de alerta e crítica",
            'drifted_features{scenario="$scenario"}',
            12,
            18,
            12,
            6,
            None,
            0,
            "{{batch_id}} · {{level}}",
        ),
        row("Saúde do modelo (só onde o rótulo chegou)", 24),
        barchart("AUC por mês", 'auc{scenario="$scenario"}', 0, 25, 8, 7, None, 4),
        barchart("Brier por mês", 'brier{scenario="$scenario"}', 8, 25, 8, 7, None, 4),
        barchart(
            "Gap de calibração por mês",
            'calibration_gap{scenario="$scenario"}',
            16,
            25,
            8,
            7,
            None,
            4,
        ),
        row("Logs", 32),
        logs(
            "Logs do pipeline (Loki)",
            '{job="credit-monitor", scenario="$scenario"}',
            0,
            33,
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
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"  {name:20} uid={payload['uid']:14} painéis={len(payload['panels'])}")


if __name__ == "__main__":
    main()
