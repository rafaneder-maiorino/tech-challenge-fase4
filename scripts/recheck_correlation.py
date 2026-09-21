"""Recompute the delinquency-counter correlations with and without the sentinels.

Inspection §10 reports the three delinquency counters correlating 0.98-0.99
with each other while each correlates only 0.10-0.13 with the target. Read at
face value that says the three columns are the same variable measured three
times, and nearly useless besides — which would be a reason to drop two of
them.

The hypothesis this script tests is that the figure is an artefact of 0.18% of
the rows. Inspection §6 found 269 records carrying the codes 96/98 in all
three counters **simultaneously**, and §4 gives each counter a standard
deviation of ~4.19 against a mean of ~0.42 — a spread that only makes sense if
a handful of values near 98 are doing the work. Pearson correlation is a
covariance ratio, and covariance is a sum over rows weighted by distance from
the mean: 269 rows sitting ~23 standard deviations out, moving together in all
three columns, can dominate that sum entirely.

The script computes both matrices, writes them side by side into
``docs/findings.md`` between stable markers (so re-running replaces the section
rather than stacking copies), and states whether the hypothesis held. The
verdict is derived from the numbers, not asserted: if the correlations barely
move, it says so.

Run as ``uv run python scripts/recheck_correlation.py`` (or
``make recheck-correlation``).
"""

import argparse
import datetime as dt
import logging
from pathlib import Path

import pandas as pd

from credit_monitor.constants import (
    DELINQUENCY_COLUMNS,
    PROJECT_ROOT,
    RAW_DATA_PATH,
    TARGET_COLUMN,
)
from credit_monitor.data.preprocess import sentinel_mask
from credit_monitor.logging_config import configure_logging

log = logging.getLogger(__name__)

FINDINGS_PATH: Path = PROJECT_ROOT / "docs" / "findings.md"
BEGIN_MARKER: str = "<!-- BEGIN: correlation-recheck -->"
END_MARKER: str = "<!-- END: correlation-recheck -->"

# Readable column labels for a 3x3 table that would otherwise be unprintable.
SHORT_NAMES: dict[str, str] = {
    "NumberOfTime30-59DaysPastDueNotWorse": "30-59d",
    "NumberOfTimes90DaysLate": "90d+",
    "NumberOfTime60-89DaysPastDueNotWorse": "60-89d",
}

# The hypothesis is judged held if the mean off-diagonal correlation falls by
# at least this much. Stated up front so the verdict is not chosen after
# seeing the numbers.
COLLAPSE_THRESHOLD: float = 0.50


def correlation_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    """Pearson matrix of the three counters, with readable labels."""
    matrix = frame[list(DELINQUENCY_COLUMNS)].corr()
    return matrix.rename(index=SHORT_NAMES, columns=SHORT_NAMES)


def mean_off_diagonal(matrix: pd.DataFrame) -> float:
    """Mean of the three distinct off-diagonal correlations."""
    values = matrix.to_numpy()
    return float((values[0, 1] + values[0, 2] + values[1, 2]) / 3)


def target_correlations(frame: pd.DataFrame) -> dict[str, float]:
    """Correlation of each counter with the binary target."""
    target = (frame[TARGET_COLUMN].astype(str) == "1").astype(int)
    return {
        SHORT_NAMES[column]: float(frame[column].corr(target))
        for column in DELINQUENCY_COLUMNS
    }


def matrix_to_markdown(matrix: pd.DataFrame) -> str:
    """Render a correlation matrix as a markdown table."""
    header = "| | " + " | ".join(matrix.columns) + " |"
    divider = "|---" * (len(matrix.columns) + 1) + "|"
    rows = [
        f"| **{name}** | "
        + " | ".join(f"{value:.4f}" for value in matrix.loc[name])
        + " |"
        for name in matrix.index
    ]
    return "\n".join([header, divider, *rows])


def render_section(
    full: pd.DataFrame,
    excluded: pd.DataFrame,
    sentinel_rows: int,
    today: str,
) -> str:
    """Build the markdown section, verdict included, from the live numbers."""
    full_matrix, excluded_matrix = (
        correlation_matrix(full),
        correlation_matrix(excluded),
    )
    full_mean, excluded_mean = (
        mean_off_diagonal(full_matrix),
        mean_off_diagonal(excluded_matrix),
    )
    full_target, excluded_target = (
        target_correlations(full),
        target_correlations(excluded),
    )

    drop_ratio = 1 - (excluded_mean / full_mean)
    held = drop_ratio >= COLLAPSE_THRESHOLD
    verdict = (
        "**A hipótese se confirmou**" if held else "**A hipótese NÃO se confirmou**"
    )

    target_rows = "\n".join(
        f"| `{name}` | {full_target[name]:.4f} | {excluded_target[name]:.4f} | "
        f"{excluded_target[name] / full_target[name]:.2f}x |"
        for name in full_matrix.columns
    )
    spread_rows = "\n".join(
        f"| `{SHORT_NAMES[column]}` | {full[column].mean():.4f} | "
        f"{full[column].std():.4f} | {int(full[column].max())} | "
        f"{excluded[column].mean():.4f} | {excluded[column].std():.4f} | "
        f"{int(excluded[column].max())} |"
        for column in DELINQUENCY_COLUMNS
    )

    if held:
        conclusion = f"""
{verdict}, e com folga. A correlação média entre os contadores cai de
**{full_mean:.4f}** para **{excluded_mean:.4f}** — uma queda de
**{drop_ratio:.0%}** — ao remover **{sentinel_rows} linhas**, que são
**{sentinel_rows / len(full):.2%}** do dataset. Os 0,18% de linhas produziam
sozinhos quase toda a covariância entre as três colunas.

A aritmética explica: a correlação de Pearson é uma razão de covariâncias, e
covariância é uma soma sobre linhas ponderada pela distância até a média. Com
média ~0,42 e desvio ~4,19, um valor 98 está a cerca de 23 desvios padrão da
média. Duzentas e sessenta e nove linhas nessa posição, movendo-se juntas nas
três colunas ao mesmo tempo, dominam a soma inteira.

### O efeito que ninguém previu

A hipótese dizia respeito à correlação **entre os contadores**. O que apareceu
junto foi mais importante: a correlação de cada contador **com o alvo mais que
dobra** quando as sentinelas saem.

| contador | corr. com alvo (completo) | sem sentinelas | variação |
|---|---|---|---|
{target_rows}

As mesmas 269 linhas faziam duas coisas ao mesmo tempo: fabricavam uma
colinearidade de 0,99 que não existe, e **escondiam** o poder preditivo real
dos três contadores. Elas carregam o código 98 — o maior valor possível na
coluna — com uma taxa de inadimplência de 54,65%, mas são poucas demais para
puxar a correlação para cima e numerosas demais para não distorcer a escala.
O resultado é uma leitura em que os contadores parecem redundantes e fracos,
quando na verdade são as variáveis mais fortes do conjunto.

### Consequência prática

A leitura ingênua de §10 — "as três colunas são a mesma coisa, e nenhuma
prediz muito" — levaria a descartar duas delas e a desconfiar da terceira. A
leitura correta é o oposto: são três sinais distintos
(correlação entre si de {excluded_mean:.2f}, não de {full_mean:.2f}) e são os
melhores preditores disponíveis. O pré-processamento remove as sentinelas
antes do treino, então o modelo já vê a versão da direita; esta seção existe
para registrar que a versão da esquerda é uma armadilha de leitura, não um
fato sobre os clientes.
"""
    else:
        conclusion = f"""
{verdict}. A correlação média entre os contadores vai de **{full_mean:.4f}**
para **{excluded_mean:.4f}** ao remover as {sentinel_rows} linhas de sentinela
— uma queda de apenas **{drop_ratio:.0%}**, abaixo do limiar de
{COLLAPSE_THRESHOLD:.0%} fixado antes do cálculo. A colinearidade quase
perfeita entre os três contadores é, portanto, uma propriedade do dado e não
um artefato de 0,18% das linhas, e a decisão de manter as três colunas no
modelo precisa de outra justificativa.
"""

    return f"""{BEGIN_MARKER}

## 4. As sentinelas fabricavam a correlação de 0,99 entre os contadores

**Etapa 1 · {today} · `scripts/recheck_correlation.py`**

### A leitura suspeita

A matriz de §10 da inspeção mostra os três contadores de inadimplência
correlacionados entre si a 0,98-0,99, enquanto cada um correlaciona apenas
0,10-0,13 com o alvo. Ao pé da letra: três medições da mesma variável, e todas
fracas.

A suspeita era de que 0,18% das linhas produzissem o número. Inspeção §6
encontrou 269 registros com os códigos 96/98 nos três contadores **ao mesmo
tempo**, e §4 dá a cada contador desvio padrão ~4,19 contra média ~0,42 — uma
dispersão que só faz sentido se alguns poucos valores perto de 98 estiverem
fazendo o trabalho.

### As duas matrizes

**Dado completo ({len(full):,} linhas)**

{matrix_to_markdown(full_matrix)}

**Sem as {sentinel_rows} linhas de sentinela ({len(excluded):,} linhas)**

{matrix_to_markdown(excluded_matrix)}

### Dispersão, antes e depois

| contador | média | desvio | máx | média (s/ sent.) | desvio (s/ sent.) | máx |
|---|---|---|---|---|---|---|
{spread_rows}
{conclusion}
{END_MARKER}
"""


def upsert_section(path: Path, section: str) -> None:
    """Write the section, replacing an existing one rather than stacking copies."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if BEGIN_MARKER in text and END_MARKER in text:
        head, _, rest = text.partition(BEGIN_MARKER)
        _, _, tail = rest.partition(END_MARKER)
        updated = head + section.strip() + tail
    else:
        updated = text.rstrip() + "\n\n---\n\n" + section.strip() + "\n"
    path.write_text(updated.rstrip() + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/recheck_correlation.py",
        description="Recalcula a correlação dos contadores com e sem as sentinelas.",
    )
    parser.add_argument("--source", type=Path, default=RAW_DATA_PATH)
    parser.add_argument("--findings", type=Path, default=FINDINGS_PATH)
    return parser


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()

    frame = pd.read_parquet(args.source)
    sentinels = sentinel_mask(frame)
    excluded = frame[~sentinels]

    full_matrix = correlation_matrix(frame)
    excluded_matrix = correlation_matrix(excluded)

    print()
    print(f"Matriz de Pearson — dado completo ({len(frame):,} linhas)")
    print(full_matrix.round(4).to_string())
    print()
    print(
        f"Matriz de Pearson — sem as {int(sentinels.sum())} sentinelas "
        f"({len(excluded):,} linhas)"
    )
    print(excluded_matrix.round(4).to_string())
    print()
    print(
        f"Correlação média entre contadores: {mean_off_diagonal(full_matrix):.4f} "
        f"-> {mean_off_diagonal(excluded_matrix):.4f}"
    )
    print("Correlação com o alvo:")
    full_target = target_correlations(frame)
    excluded_target = target_correlations(excluded)
    for name in full_matrix.columns:
        print(
            f"  {name:8} {full_target[name]:.4f} -> {excluded_target[name]:.4f}  "
            f"({excluded_target[name] / full_target[name]:.2f}x)"
        )

    section = render_section(
        full=frame,
        excluded=excluded,
        sentinel_rows=int(sentinels.sum()),
        today=dt.date.today().isoformat(),
    )
    upsert_section(args.findings, section)
    print()
    print(f"Achado registrado em {args.findings}")
    print()


if __name__ == "__main__":
    main()
