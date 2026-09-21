"""Render the drift analysis as markdown, plus an index for the HTML reports.

The summary is what a reviewer reads instead of opening eight HTML files. It
carries the configuration (so the numbers are interpretable), the cross-check
against our own PSI (so the numbers are trustworthy), and the ranking by
drift x importance (so the numbers are actionable).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pandas as pd

from credit_monitor.reporting.drift import (
    CONTINUOUS_FEATURES,
    VERDICT_MODERATE,
    VERDICT_SIGNIFICANT,
    ColumnDrift,
)
from credit_monitor.reporting.drift import (
    verdict as verdict_of,
)

BAND: Final[dict[str, str]] = {
    VERDICT_SIGNIFICANT: "🔴",
    VERDICT_MODERATE: "🟡",
}


@dataclass(frozen=True, slots=True)
class ReportLink:
    """One committed HTML report, for the index."""

    path: str
    title: str
    description: str
    size_mb: float


def _band(value: float, verdict: str) -> str:
    return f"{value:.3f} {BAND.get(verdict, '🟢')}"


def psi_matrix(rows: list[ColumnDrift], gain: dict[str, float]) -> str:
    """Feature x month PSI, with the champion's gain share as a column."""
    frame = pd.DataFrame([row.to_dict() for row in rows])
    months = sorted(frame["month"].unique())
    order = sorted(gain, key=lambda column: -gain[column])

    header = "| feature | ganho | " + " | ".join(f"mês {m}" for m in months) + " |"
    divider = "|---" * (len(months) + 2) + "|"
    lines = [header, divider]
    for column in order:
        cells = []
        for month in months:
            row = frame[(frame["column"] == column) & (frame["month"] == month)].iloc[0]
            cells.append(_band(row["psi"], row["verdict"]))
        lines.append(f"| `{column}` | {gain[column]:.2%} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def cross_check_table(rows: list[ColumnDrift], month: int) -> str:
    """Our PSI against Evidently's native PSI, for one month."""
    frame = pd.DataFrame([r.to_dict() for r in rows if r.month == month])
    lines = [
        "| feature | nosso PSI (quantis do reference) | PSI nativo do Evidently "
        "(Sturges) | razão | mesmo veredito |",
        "|---|---|---|---|---|",
    ]
    for row in frame.itertuples():
        native = row.psi_evidently_native
        ratio = row.psi / native if native > 1e-9 else float("inf")
        agree = "✅" if verdict_of(native) == row.verdict else "❌"
        cells = f"{row.psi:.4f} | {native:.4f} | {ratio:.2f}x | {agree}"
        lines.append(f"| `{row.column}` | {cells} |")
    return "\n".join(lines)


def ks_table(rows: list[ColumnDrift], month: int) -> str:
    """KS statistic and p-value for the continuous features of one month."""
    lines = [
        "| feature | estatística KS | p-valor | PSI | veredito (por PSI) |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        if row.month != month or row.column not in CONTINUOUS_FEATURES:
            continue
        pvalue = "< 1e-300" if row.ks_pvalue == 0 else f"{row.ks_pvalue:.3e}"
        lines.append(
            f"| `{row.column}` | {row.ks_statistic:.4f} | {pvalue} | "
            f"{row.psi:.4f} | {row.verdict} |"
        )
    return "\n".join(lines)


def impact_ranking(rows: list[ColumnDrift], month: int, top: int = 6) -> str:
    """Features ranked by PSI x gain share for one month."""
    selected = sorted(
        (row for row in rows if row.month == month),
        key=lambda row: -row.impact,
    )[:top]
    lines = [
        "| # | feature | PSI | ganho | **PSI x ganho** | veredito |",
        "|---|---|---|---|---|---|",
    ]
    for index, row in enumerate(selected, 1):
        lines.append(
            f"| {index} | `{row.column}` | {row.psi:.3f} | {row.gain_share:.2%} "
            f"| **{row.impact:.4f}** | {row.verdict} |"
        )
    return "\n".join(lines)


def index_html(links: list[ReportLink], generated: str) -> str:
    """A tiny, self-contained index page for the committed reports."""
    items = "".join(
        f"<li><a href='{link.path}'>{link.title}</a>"
        f"<span class='size'>{link.size_mb:.1f} MB</span>"
        f"<p>{link.description}</p></li>"
        for link in links
    )
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Relatórios de drift — etapa 2</title>
<style>
body {{ margin:0; padding:2.5rem 3rem 4rem; color:#16191d; background:#fff;
  font:16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
h1 {{ font-size:1.35rem; margin:0 0 .3rem; }}
p.lead {{ color:#5c636b; margin:0 0 2rem; }}
ul {{ list-style:none; padding:0; margin:0; }}
li {{ border:1px solid #e6e8eb; border-radius:8px; padding:1rem 1.2rem;
  margin-bottom:.8rem; background:#fafbfc; }}
li a {{ font-weight:650; font-size:1.05rem; color:#0b4f9e; text-decoration:none; }}
li a:hover {{ text-decoration:underline; }}
li p {{ margin:.35rem 0 0; color:#5c636b; font-size:.9rem; }}
.size {{ float:right; color:#5c636b; font-size:.8rem;
  font-variant-numeric:tabular-nums; }}
code {{ background:#f2f3f5; padding:.1rem .35rem; border-radius:3px; }}
</style>
</head>
<body>
<h1>Relatórios de drift — etapa 2</h1>
<p class="lead">
  Gerados com <code>evidently==0.7.23</code> em {generated}. Todos abrem
  offline: o JavaScript está embutido, nenhuma CDN é consultada. O resumo em
  números está em <code>summary.md</code>.
</p>
<ul>{items}</ul>
</body>
</html>
"""


# The curated set committed to the repository. Defined here, beside the titles
# and descriptions keyed by the same names, so the generator and the publisher
# cannot disagree about which files are tracked.
CURATED_REPORTS: Final[tuple[str, ...]] = (
    "scoring_month_00.html",
    "scoring_month_03.html",
    "scoring_month_06.html",
    "label_month_06.html",
    "stress_only_scoring_month_06.html",
    "stress_only_label_month_06.html",
)

TITLES: Final[dict[str, str]] = {
    "scoring_month_00.html": "Mês 0 — scoring-time (controle)",
    "scoring_month_03.html": "Mês 3 — scoring-time",
    "scoring_month_06.html": "Mês 6 — scoring-time",
    "label_month_06.html": "Mês 6 — label-time",
    "stress_only_scoring_month_06.html": "stress_only, mês 6 — scoring-time 🟢",
    "stress_only_label_month_06.html": "stress_only, mês 6 — label-time 🔴",
}

DESCRIPTIONS: Final[dict[str, str]] = {
    "scoring_month_00.html": (
        "Lote de controle, nenhum mecanismo aplicado. É o que o ruído de "
        "amostragem parece antes de qualquer sinal."
    ),
    "scoring_month_03.html": (
        "Meio do choque. Três features entram na faixa amarela; a cascata de "
        "atrasos ainda não chegou às faixas mais graves."
    ),
    "scoring_month_06.html": (
        "Fim do choque. Cinco features na faixa vermelha e o drift do próprio "
        "score de saída."
    ),
    "label_month_06.html": (
        "O mesmo lote do mês 6, com os rótulos que em produção chegariam meses "
        "depois: drift de alvo e qualidade de classificação."
    ),
    "stress_only_scoring_month_06.html": (
        "Braço só-estresse. Os rótulos mudaram, as features não — e este "
        "relatório fica VERDE. Metade do 2x2 da etapa."
    ),
    "stress_only_label_month_06.html": (
        "O mesmo lote, mesmo mês, com rótulo: VERMELHO. A outra metade, e a "
        "prova de que vigiar feature sozinho não basta."
    ),
}


def render(
    rows: list[ColumnDrift],
    stress_rows: list[ColumnDrift],
    gain: dict[str, float],
    months: list[int],
    reference_rows: int,
    generated: str,
    committed: tuple[str, ...],
    out_dir: Path,
) -> str:
    """Assemble the whole markdown summary."""
    last = months[-1]
    sizes = "\n".join(
        f"| `{name}` | {(out_dir / name).stat().st_size / 1_048_576:.2f} MB |"
        for name in committed
    )
    stress_max_psi = max(row.psi for row in stress_rows)
    stress_worst = max(stress_rows, key=lambda row: row.psi)
    stress_pair = "\n".join(
        [
            "| relatório | o que mostra |",
            "|---|---|",
            f"| `stress_only_scoring_month_{last:02d}.html` | 🟢 PSI máximo de "
            f"**{stress_max_psi:.4f}** (`{stress_worst.column}`), todas as "
            "features estáveis |",
            f"| `stress_only_label_month_{last:02d}.html` | 🔴 qualidade de "
            "classificação degradada, drift de alvo |",
        ]
    )
    return f"""# Relatórios de drift — resumo

Gerados por `make drift-reports` com **`evidently==0.7.23`** em {generated}.
Referência: {reference_rows:,} linhas. A versão, a escolha de API e o porquê de
cada configuração estão em [`docs/evidently.md`](../../docs/evidently.md).

Nenhuma métrica foi deixada por omissão: **PSI em todas as 11 features**
(método `reference_psi`, limiares 0,10 / 0,25), **KS nas 4 contínuas**, e PSI no
score previsto. 🟢 estável · 🟡 moderado · 🔴 significativo.

---

## 1. Cross-check: o nosso PSI contra o nativo do Evidently

Antes de confiar em qualquer número do monitor, os dois estimadores foram
postos lado a lado no mês {last}. A divergência é real e tem causa conhecida —
ver `docs/findings.md` §10.

{cross_check_table(rows, last)}

Nas colunas de contagem os dois concordam em três casas decimais: o Evidently
também troca para **um bin por valor** quando a coluna tem 20 ou menos valores
distintos, então **não** tem o bug que o dia 6 encontrou no nosso.

Nas contínuas ele usa bins de **largura igual** sobre a união de referência e
lote, e aí diverge — inclusive trocando o veredito de uma feature. O relatório
usa o `reference_psi` registrado, com bins de quantil cortados no `reference`.

## 2. PSI por feature e por mês

Ordenado por participação no ganho do campeão, que é a coluna que diz se um
drift importa.

{psi_matrix(rows, gain)}

## 3. KS nas features contínuas — mês {last}

{ks_table(rows, last)}

Os p-valores estão **registrados e não são usados para veredito**. Com ~7,4 mil
linhas contra {reference_rows:,}, o KS tem poder para declarar significante
qualquer diferença. Quanto disso é falso alarme é o que o dia 8 mede.

## 4. Ranking por PSI x ganho

Um drift de 0,30 numa feature que carrega 28% do ganho e o mesmo 0,30 numa que
carrega 2% não são o mesmo evento. Uma tabela ordenada só por PSI os apresenta
como se fossem.

### Mês 3

{impact_ranking(rows, 3)}

### Mês {last}

{impact_ranking(rows, last)}

## 5. O 2x2, como dois arquivos

O braço `stress_only` no mês {last}: os rótulos mudaram, **nenhuma feature
mudou**.

{stress_pair}

O mesmo lote, o mesmo mês, dois vereditos opostos — porque um dos relatórios
pode ver o rótulo e o outro não. É a razão pela qual monitoramento baseado em
rótulo não é complementar, é obrigatório.

## 6. Arquivos versionados

Todos abrem offline: o JavaScript vai embutido e nenhuma CDN é consultada
(verificado — zero tags `<script src=...>` ou `<link href=...>` apontando para
fora). O tamanho é o preço disso.

| arquivo | tamanho |
|---|---|
{sizes}

O restante (`scoring_month_01/02/04/05`, `label_month_00..05`) é regenerável
por `make drift-reports` e fica fora do repositório.
"""
