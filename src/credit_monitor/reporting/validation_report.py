"""The shape of a validation result, and the two ways it is written down.

This module owns the report model — :class:`Verdict`, :class:`Rejection`,
:class:`RuleCount`, :class:`ValidationResult` — rather than the ingestion
script that produces it. The dependency runs one way (ingest imports reporting,
never the reverse) and the reason is that the report is the stable artefact:
the JSON is what stage 3 will aggregate and what the tests assert against, so
its structure should be readable without opening the pipeline.

Two outputs, two audiences:

``<batch_id>.json``
    The machine record. Counts, per-rule severities, the verdict, and every
    rejection. Stable keys, no prose.

``<batch_id>.html``
    The human record, self-contained: no CDN, no external stylesheet, no
    JavaScript. It has to render from a ``file://`` URL on a projector in a
    room where the wifi is someone else's problem, so everything is inlined
    and the verdict is a banner you can read from the back row.
"""

import datetime as dt
import html
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from credit_monitor.contracts.severity import Severity

# How many rejected rows the HTML shows before it stops. The file is meant to
# be read, not scrolled: a 12-row sample makes the point and the JSON next to
# it carries every rejection for anyone who needs them all.
HTML_REJECTION_SAMPLE_SIZE: int = 12


class Verdict(StrEnum):
    """The batch-level outcome, and the only thing the pipeline branches on."""

    ACCEPTED = "ACCEPTED"
    """No rule fired. The batch moves on untouched."""

    ACCEPTED_WITH_WARNINGS = "ACCEPTED_WITH_WARNINGS"
    """Only warning rules fired. The batch moves on and somebody is told."""

    BLOCKED = "BLOCKED"
    """At least one blocker fired. The pipeline stops with a non-zero exit."""


class Scope(StrEnum):
    """Whether a failure can be pinned to rows or only to the batch."""

    ROW = "row"
    """Pandera named the offending rows; they can be quarantined."""

    BATCH = "batch"
    """A statistic or a structural fact about the whole frame.

    ``positive_rate_band`` is a property of the batch — no single row can be
    blamed for it — and a wrong dtype or a missing column is a fact about the
    column, not about any row in it. Nothing is quarantined for a batch-scoped
    failure; when it is a blocker it stops the pipeline on its own.
    """


@dataclass(frozen=True, slots=True)
class Rejection:
    """One row failing one rule, in the form a human needs to act on it."""

    row_index: int | None
    """Positional index into the ingested batch. ``None`` when batch-scoped."""

    rule: str
    column: str | None
    observed_value: Any
    severity: Severity
    scope: Scope


@dataclass(frozen=True, slots=True)
class RuleCount:
    """How many rows one rule rejected, and what that rule is worth."""

    rule: str
    severity: Severity
    scope: Scope
    rows: int
    """Distinct rows for a row-scoped rule; occurrences for a batch-scoped one.

    Distinct rows, not failure cases: pandera melts a frame-level check across
    every column, so a single duplicated row arrives as eleven failure cases.
    Counting them raw would report 44 duplicates where the generator injected
    four, and the manifest check would be meaningless.
    """


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Everything known about one batch after validation."""

    batch_id: str
    source_path: str
    schema_name: str
    timestamp: str
    rows_in: int
    rows_quarantined: int
    duration_seconds: float
    rule_counts: list[RuleCount] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)

    @property
    def rows_passed(self) -> int:
        """Rows carrying no blocker-level rejection.

        The complement of the quarantine by construction, which is what makes
        ``rows_passed + rows_quarantined == rows_in`` an invariant rather than
        a coincidence. It is a report statistic, not a delivery: when the
        verdict is BLOCKED nothing is forwarded at all.
        """
        return self.rows_in - self.rows_quarantined

    @property
    def verdict(self) -> Verdict:
        """BLOCKED if any blocker fired, else warnings decide."""
        severities = {count.severity for count in self.rule_counts}
        if Severity.BLOCKER in severities:
            return Verdict.BLOCKED
        if Severity.WARNING in severities:
            return Verdict.ACCEPTED_WITH_WARNINGS
        return Verdict.ACCEPTED

    @property
    def blocked(self) -> bool:
        """Whether the pipeline must stop. The exit code is this, negated."""
        return self.verdict is Verdict.BLOCKED

    def to_dict(self) -> dict[str, Any]:
        """Render the result as the JSON document written to disk."""
        return {
            "batch_id": self.batch_id,
            "source_path": self.source_path,
            "schema": self.schema_name,
            "timestamp": self.timestamp,
            "verdict": self.verdict.value,
            "rows": {
                "in": self.rows_in,
                "passed": self.rows_passed,
                "quarantined": self.rows_quarantined,
            },
            "duration_seconds": round(self.duration_seconds, 4),
            "rules": [asdict(count) for count in self.rule_counts],
            "rejections": [asdict(rejection) for rejection in self.rejections],
        }


def utc_timestamp() -> str:
    """Return the current UTC time, ISO-8601, seconds resolution."""
    return dt.datetime.now(tz=dt.UTC).replace(microsecond=0).isoformat()


def write_json_report(result: ValidationResult, directory: Path) -> Path:
    """Write ``<batch_id>.json`` and return its path.

    Args:
        result: The validation outcome to serialise.
        directory: Destination directory, created if absent.

    Returns:
        The path written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.batch_id}.json"
    # default=str so a Severity, a Scope or a numpy scalar that slipped through
    # degrades to its string form instead of losing the whole report.
    path.write_text(
        json.dumps(result.to_dict(), indent=2, default=str) + "\n", encoding="utf-8"
    )
    return path


# --------------------------------------------------------------------------
# HTML
#
# Written as string templates rather than with a templating engine: the whole
# point of this file is that it has no runtime dependencies to resolve when
# somebody opens it from a USB stick.
# --------------------------------------------------------------------------

_VERDICT_COLORS: dict[Verdict, tuple[str, str]] = {
    Verdict.ACCEPTED: ("#0b6b3a", "#e6f4ec"),
    Verdict.ACCEPTED_WITH_WARNINGS: ("#8a5a00", "#fdf3e0"),
    Verdict.BLOCKED: ("#a01324", "#fdeaec"),
}

_VERDICT_SUBTITLES: dict[Verdict, str] = {
    Verdict.ACCEPTED: "Nenhuma regra violada — lote segue para o modelo.",
    Verdict.ACCEPTED_WITH_WARNINGS: (
        "Somente regras de alerta — lote segue, com registro para análise."
    ),
    Verdict.BLOCKED: (
        "Regra bloqueante violada — pipeline interrompido, linhas em quarentena."
    ),
}

_STYLE = """
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2.5rem 3rem 4rem;
  font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: #16191d; background: #fff;
}
h1 { font-size: 1.35rem; margin: 0 0 .35rem; font-weight: 650; }
h2 {
  font-size: 1.05rem; margin: 2.75rem 0 .85rem; font-weight: 650;
  padding-bottom: .4rem; border-bottom: 2px solid #e6e8eb;
}
.meta { color: #5c636b; font-size: .85rem; margin: 0 0 2rem; }
.meta code { background: #f2f3f5; padding: .1rem .35rem; border-radius: 3px; }
.banner {
  border-radius: 10px; padding: 1.6rem 1.9rem; margin: 0 0 2.25rem;
  border-left: 12px solid currentColor;
}
.banner .verdict {
  font-size: 2.6rem; font-weight: 800; letter-spacing: -.02em; line-height: 1.1;
}
.banner .subtitle { color: #16191d; font-size: 1rem; margin-top: .4rem; }
.tiles { display: flex; gap: 1rem; flex-wrap: wrap; margin: 0 0 .5rem; }
.tile {
  flex: 1 1 8rem; border: 1px solid #e6e8eb; border-radius: 8px;
  padding: .9rem 1.1rem; background: #fafbfc;
}
.tile .n { font-size: 1.9rem; font-weight: 700; letter-spacing: -.02em; }
.tile .k {
  font-size: .72rem; text-transform: uppercase; letter-spacing: .07em;
  color: #5c636b; margin-top: .15rem;
}
table { border-collapse: collapse; width: 100%; font-size: .92rem; }
th, td {
  text-align: left; padding: .55rem .75rem; border-bottom: 1px solid #e6e8eb;
  vertical-align: top;
}
th {
  font-size: .72rem; text-transform: uppercase; letter-spacing: .07em;
  color: #5c636b; border-bottom: 2px solid #d6d9dd;
}
td.num { text-align: right; font-variant-numeric: tabular-nums; }
code, td.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.pill {
  display: inline-block; padding: .12rem .5rem; border-radius: 999px;
  font-size: .72rem; font-weight: 700; letter-spacing: .04em;
}
.pill.blocker { background: #fdeaec; color: #a01324; }
.pill.warning { background: #fdf3e0; color: #8a5a00; }
tr.blocker td:first-child { box-shadow: inset 3px 0 0 #a01324; }
.note { color: #5c636b; font-size: .85rem; margin: .7rem 0 0; }
"""


def _cell(value: object) -> str:
    """Escape a value for a table cell, rendering a missing one visibly."""
    if value is None:
        return "<span style='color:#9aa1a9'>—</span>"
    return html.escape(str(value))


def _tiles(result: ValidationResult) -> str:
    counts = (
        ("linhas recebidas", result.rows_in),
        ("aprovadas", result.rows_passed),
        ("em quarentena", result.rows_quarantined),
        ("regras violadas", len(result.rule_counts)),
    )
    tiles = "".join(
        f"<div class='tile'><div class='n'>{value}</div>"
        f"<div class='k'>{html.escape(label)}</div></div>"
        for label, value in counts
    )
    return f"<div class='tiles'>{tiles}</div>"


def _rule_table(result: ValidationResult) -> str:
    if not result.rule_counts:
        return "<p class='note'>Nenhuma regra violada.</p>"
    # Blockers first: the reader's question is "why was this stopped", and the
    # answer should not be below the fold.
    ordered = sorted(
        result.rule_counts,
        key=lambda count: (count.severity is not Severity.BLOCKER, -count.rows),
    )
    rows = ""
    for count in ordered:
        css = count.severity.value
        rows += (
            f"<tr class='{css}'>"
            f"<td class='mono'>{html.escape(count.rule)}</td>"
            f"<td><span class='pill {css}'>{css.upper()}</span></td>"
            f"<td>{count.scope.value}</td>"
            f"<td class='num'>{count.rows}</td></tr>"
        )
    return (
        "<table><thead><tr><th>regra</th><th>severidade</th><th>escopo</th>"
        f"<th class='num'>linhas</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _rejection_table(result: ValidationResult) -> str:
    blockers = [
        rejection
        for rejection in result.rejections
        if rejection.severity is Severity.BLOCKER
    ]
    if not blockers:
        return "<p class='note'>Nenhuma linha em quarentena.</p>"

    sample = blockers[:HTML_REJECTION_SAMPLE_SIZE]
    rows = ""
    for rejection in sample:
        rows += (
            f"<tr><td class='num'>{_cell(rejection.row_index)}</td>"
            f"<td class='mono'>{html.escape(rejection.rule)}</td>"
            f"<td class='mono'>{_cell(rejection.column)}</td>"
            f"<td class='mono'>{_cell(rejection.observed_value)}</td></tr>"
        )
    note = (
        f"<p class='note'>Mostrando {len(sample)} de {len(blockers)} rejeições "
        "bloqueantes. O JSON e o JSONL da quarentena trazem todas.</p>"
    )
    return (
        "<table><thead><tr><th class='num'>linha</th><th>regra</th>"
        "<th>coluna</th><th>valor observado</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>{note}"
    )


def render_html(result: ValidationResult) -> str:
    """Render the full self-contained HTML report as a string."""
    verdict = result.verdict
    foreground, background = _VERDICT_COLORS[verdict]
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Validação — {html.escape(result.batch_id)}</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>Relatório de validação de lote</h1>
<p class="meta">
  lote <code>{html.escape(result.batch_id)}</code> &middot;
  contrato <code>{html.escape(result.schema_name)}</code> &middot;
  origem <code>{html.escape(result.source_path)}</code> &middot;
  {html.escape(result.timestamp)} &middot;
  {result.duration_seconds:.3f}s
</p>

<div class="banner" style="color:{foreground};background:{background}">
  <div class="verdict">{verdict.value}</div>
  <div class="subtitle">{html.escape(_VERDICT_SUBTITLES[verdict])}</div>
</div>

<h2>Contagem</h2>
{_tiles(result)}

<h2>Regras violadas</h2>
{_rule_table(result)}

<h2>Amostra de linhas rejeitadas</h2>
{_rejection_table(result)}
</body>
</html>
"""


def write_html_report(result: ValidationResult, directory: Path) -> Path:
    """Write ``<batch_id>.html`` and return its path.

    Args:
        result: The validation outcome to render.
        directory: Destination directory, created if absent.

    Returns:
        The path written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.batch_id}.html"
    path.write_text(render_html(result), encoding="utf-8")
    return path
