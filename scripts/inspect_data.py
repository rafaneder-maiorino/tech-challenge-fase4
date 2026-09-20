"""Factual inspection of the raw dataset — writes reports/inspection.md.

No modelling, no cleaning, no interpretation. Every section reports numbers and
stops there; deciding which of them become data-contract rules is a separate,
human step.

Run with `make inspect` (requires `make download` first).
"""

import logging
from collections.abc import Callable, Sequence
from io import StringIO

import pandas as pd

from credit_monitor.constants import (
    DELINQUENCY_COLUMNS,
    RAW_DATA_PATH,
    REPORTS_DIR,
    TARGET_COLUMN,
)
from credit_monitor.logging_config import configure_logging

log = logging.getLogger("inspect_data")

REPORT_PATH = REPORTS_DIR / "inspection.md"

# Percentiles requested on top of pandas' default quartiles. The tails are what
# matter for a range rule: 1%/99% show where the bulk ends, 5%/95% give the
# less brittle alternative.
PERCENTILES = [0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99]

# Values at or above this are pulled out for inspection in the delinquency
# counters — 96 and 98 are suspected sentinel codes rather than real counts.
SENTINEL_THRESHOLD = 90

# Column names that would denote a row identifier. This dataset has none; the
# check stays so the report states that as a fact rather than by omission.
ID_COLUMN_CANDIDATES = frozenset({"id", "index", "unnamed: 0", "row_id", "rowid"})


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def fmt_int(value: float) -> str:
    """Format a count with thousands separators."""
    return f"{int(value):,}"


def fmt_pct(value: float) -> str:
    """Format a fraction in [0, 1] as a percentage with two decimals."""
    return f"{value * 100:.2f}%"


def fmt_num(value: object, decimals: int = 4) -> str:
    """Format a numeric cell, leaving NaN and non-numerics readable."""
    if value is None:
        return "—"
    if isinstance(value, (int, float)) and pd.isna(value):
        return "NaN"
    if isinstance(value, (int, float)):
        return f"{value:,.{decimals}f}"
    return str(value)


def markdown_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Render a GitHub-flavoured markdown table.

    Written by hand rather than via DataFrame.to_markdown so the report does
    not depend on `tabulate` being installed.

    Args:
        headers: Column headers.
        rows: Already-formatted cells, one sequence per row.

    Returns:
        The table as a markdown string, terminated by a newline.
    """
    out = StringIO()
    out.write("| " + " | ".join(headers) + " |\n")
    out.write("|" + "|".join("---" for _ in headers) + "|\n")
    for row in rows:
        out.write("| " + " | ".join(row) + " |\n")
    return out.getvalue()


def frame_to_markdown(
    frame: pd.DataFrame,
    index_header: str,
    formatter: Callable[[object], str] = fmt_num,
) -> str:
    """Render a DataFrame as a markdown table, with its index as first column."""
    headers = [index_header, *(str(c) for c in frame.columns)]
    rows = [
        [str(idx), *(formatter(value) for value in record)]
        for idx, record in zip(frame.index, frame.to_numpy(), strict=True)
    ]
    return markdown_table(headers, rows)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_raw() -> pd.DataFrame:
    """Load the raw parquet, failing with a usable message when it is absent."""
    if not RAW_DATA_PATH.exists():
        raise SystemExit(
            f"Raw dataset not found at {RAW_DATA_PATH}. Run `make download` first."
        )
    return pd.read_parquet(RAW_DATA_PATH)


def numeric_target(frame: pd.DataFrame) -> pd.Series:
    """Return the target as int64.

    OpenML serves SeriousDlqin2yrs as a pandas `category`, which keeps it out of
    describe() and corr(). It is a 0/1 label, so it is coerced back to an
    integer for the numeric sections — a presentation choice, stated in the
    report, not a change to the stored file.
    """
    return frame[TARGET_COLUMN].astype("int64")


def numeric_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return every numeric column, with the coerced target in its original position."""
    out = frame.copy()
    out[TARGET_COLUMN] = numeric_target(frame)
    return out.select_dtypes(include="number")


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def section_shape(frame: pd.DataFrame) -> str:
    """1. Shape, dtypes and memory usage."""
    memory = frame.memory_usage(deep=True)
    total_mb = memory.sum() / 1024**2

    rows = [
        [
            str(col),
            str(frame[col].dtype),
            f"{memory[col] / 1024**2:.3f} MB",
        ]
        for col in frame.columns
    ]
    rows.append(["**(index)**", "—", f"{memory['Index'] / 1024**2:.3f} MB"])

    body = [
        "## 1. Shape, dtypes e uso de memória\n",
        f"- Linhas: **{fmt_int(frame.shape[0])}**",
        f"- Colunas: **{frame.shape[1]}**",
        f"- Memória total (deep): **{total_mb:.2f} MB**\n",
        markdown_table(["Coluna", "dtype", "Memória (deep)"], rows),
    ]
    return "\n".join(body)


def section_target(frame: pd.DataFrame) -> str:
    """2. Target column, class counts and positive rate."""
    target = numeric_target(frame)
    counts = target.value_counts().sort_index()
    total = len(target)

    rows = [
        [str(cls), fmt_int(count), fmt_pct(count / total)]
        for cls, count in counts.items()
    ]
    positive_rate = target.mean()

    body = [
        "## 2. Coluna alvo, contagem de classes e taxa de positivos\n",
        f"- Coluna alvo: **`{TARGET_COLUMN}`**",
        f"- dtype armazenado: `{frame[TARGET_COLUMN].dtype}`",
        f"- Taxa de positivos (classe 1): **{fmt_pct(positive_rate)}**",
        f"- Razão negativos:positivos: "
        f"**{(1 - positive_rate) / positive_rate:.2f} : 1**\n",
        markdown_table(["Classe", "Contagem", "% do total"], rows),
    ]
    return "\n".join(body)


def section_missing(frame: pd.DataFrame) -> str:
    """3. Missing values per column and rows with any missing value."""
    total = len(frame)
    missing = frame.isna().sum()

    rows = [
        [str(col), fmt_int(missing[col]), fmt_pct(missing[col] / total)]
        for col in frame.columns
    ]
    any_missing = int(frame.isna().any(axis=1).sum())
    all_present = total - any_missing

    body = [
        "## 3. Valores ausentes\n",
        markdown_table(["Coluna", "Ausentes", "% ausentes"], rows),
        f"- Linhas com **pelo menos um** valor ausente: "
        f"**{fmt_int(any_missing)}** ({fmt_pct(any_missing / total)})",
        f"- Linhas completas: **{fmt_int(all_present)}** "
        f"({fmt_pct(all_present / total)})\n",
    ]
    return "\n".join(body)


def section_describe(frame: pd.DataFrame) -> str:
    """4. describe() for every numeric column, with tail percentiles."""
    described = numeric_frame(frame).describe(percentiles=PERCENTILES).transpose()

    body = [
        "## 4. Estatísticas descritivas das colunas numéricas\n",
        f"Percentis incluídos: {', '.join(f'{p:.0%}' for p in PERCENTILES)}. "
        f"`{TARGET_COLUMN}` aparece aqui convertida de `category` para inteiro "
        "(ver seção 2); o arquivo em disco não foi alterado.\n",
        frame_to_markdown(described, "Coluna"),
    ]
    return "\n".join(body)


def section_age(frame: pd.DataFrame) -> str:
    """5. Implausible ages."""
    age = frame["age"]

    rows = [
        ["age < 18", fmt_int((age < 18).sum()), fmt_pct((age < 18).mean())],
        ["age == 0", fmt_int((age == 0).sum()), fmt_pct((age == 0).mean())],
        ["age > 100", fmt_int((age > 100).sum()), fmt_pct((age > 100).mean())],
    ]

    body = [
        "## 5. Idade: valores implausíveis\n",
        markdown_table(["Condição", "Contagem", "% do total"], rows),
        f"- `age` mínima: **{age.min()}**",
        f"- `age` máxima: **{age.max()}**\n",
    ]
    return "\n".join(body)


def section_sentinels(frame: pd.DataFrame) -> str:
    """6. High values in the delinquency counters and their default rate."""
    target = numeric_target(frame)
    total = len(frame)
    parts = [
        f"## 6. Contadores de inadimplência: valores >= {SENTINEL_THRESHOLD}\n",
        "`value_counts` completo dos valores altos de cada contador, com a taxa "
        "de inadimplência (`" + TARGET_COLUMN + "` = 1) das linhas que os "
        "carregam.\n",
    ]

    for column in DELINQUENCY_COLUMNS:
        high = frame[column] >= SENTINEL_THRESHOLD
        subset = frame.loc[high, column]

        rows = [
            [
                str(value),
                fmt_int(count),
                fmt_pct(count / total),
                fmt_pct(target[frame[column] == value].mean()),
            ]
            for value, count in subset.value_counts().sort_index().items()
        ]

        parts.append(f"### `{column}`\n")
        if not rows:
            parts.append(f"Nenhum valor >= {SENTINEL_THRESHOLD} nesta coluna.\n")
            continue

        parts.append(
            markdown_table(
                ["Valor", "Contagem", "% do total", "Taxa de inadimplência"], rows
            )
        )
        n_high = int(high.sum())
        parts.append(
            f"- Agregado (valores >= {SENTINEL_THRESHOLD}): "
            f"**{fmt_int(n_high)}** linhas ({fmt_pct(n_high / total)}), "
            f"taxa de inadimplência **{fmt_pct(target[high].mean())}**"
        )
        parts.append(
            f"- Linhas com valor < {SENTINEL_THRESHOLD}: taxa de inadimplência "
            f"**{fmt_pct(target[~high].mean())}**",
        )
        parts.append(
            f"- Valor máximo abaixo do corte: **{frame.loc[~high, column].max()}**\n"
        )

    return "\n".join(parts)


def find_id_column(frame: pd.DataFrame) -> str | None:
    """Return the name of a row-identifier column, or None when there is none."""
    for column in frame.columns:
        if str(column).strip().lower() in ID_COLUMN_CANDIDATES:
            return str(column)
    return None


def section_duplicates(frame: pd.DataFrame) -> str:
    """7. Exact duplicate rows, with and without an ID column."""
    total = len(frame)
    dup_all = int(frame.duplicated(keep="first").sum())
    id_column = find_id_column(frame)

    body = ["## 7. Linhas duplicadas exatas\n"]
    body.append(
        f"- Duplicatas considerando **todas as {frame.shape[1]} colunas**: "
        f"**{fmt_int(dup_all)}** ({fmt_pct(dup_all / total)})"
    )

    if id_column is None:
        body.append(
            "- Nenhuma coluna de identificador foi encontrada neste dataset "
            f"(procurado por: {', '.join(sorted(ID_COLUMN_CANDIDATES))}). "
            "A contagem ignorando ID é, portanto, idêntica à de cima: "
            f"**{fmt_int(dup_all)}**."
        )
    else:
        dup_no_id = int(frame.drop(columns=[id_column]).duplicated(keep="first").sum())
        body.append(f"- Coluna de identificador detectada: **`{id_column}`**")
        body.append(
            f"- Duplicatas **ignorando `{id_column}`**: "
            f"**{fmt_int(dup_no_id)}** ({fmt_pct(dup_no_id / total)})"
        )

    n_unique = total - dup_all
    body.append(f"- Linhas distintas: **{fmt_int(n_unique)}**\n")
    return "\n".join(body)


def section_utilization(frame: pd.DataFrame) -> str:
    """8. RevolvingUtilizationOfUnsecuredLines above 1 and above 10."""
    column = "RevolvingUtilizationOfUnsecuredLines"
    values = frame[column]

    rows = [
        [f"{column} > 1", fmt_int((values > 1).sum()), fmt_pct((values > 1).mean())],
        [f"{column} > 10", fmt_int((values > 10).sum()), fmt_pct((values > 10).mean())],
    ]

    body = [
        f"## 8. `{column}` acima de 1 e de 10\n",
        markdown_table(["Condição", "Contagem", "% do total"], rows),
        f"- Valor máximo observado: **{fmt_num(values.max(), 2)}**\n",
    ]
    return "\n".join(body)


def section_debt_ratio_by_income(frame: pd.DataFrame) -> str:
    """9. DebtRatio split by whether MonthlyIncome is missing, plus default rates."""
    target = numeric_target(frame)
    income_missing = frame["MonthlyIncome"].isna()

    groups = {
        "MonthlyIncome ausente": income_missing,
        "MonthlyIncome presente": ~income_missing,
    }

    described = pd.DataFrame(
        {
            label: frame.loc[mask, "DebtRatio"].describe(percentiles=PERCENTILES)
            for label, mask in groups.items()
        }
    ).transpose()

    rate_rows = [
        [
            label,
            fmt_int(mask.sum()),
            fmt_pct(mask.mean()),
            fmt_pct(target[mask].mean()),
        ]
        for label, mask in groups.items()
    ]

    body = [
        "## 9. `DebtRatio` por presença de `MonthlyIncome`\n",
        "### Estatísticas descritivas de `DebtRatio`\n",
        frame_to_markdown(described, "Grupo"),
        "### Tamanho do grupo e taxa de inadimplência\n",
        markdown_table(
            ["Grupo", "Linhas", "% do total", "Taxa de inadimplência"], rate_rows
        ),
    ]
    return "\n".join(body)


def section_correlation(frame: pd.DataFrame) -> str:
    """10. Pearson correlation matrix of the numeric columns."""
    corr = numeric_frame(frame).corr(method="pearson", numeric_only=True)

    body = [
        "## 10. Matriz de correlação de Pearson\n",
        "Colunas numéricas, duas casas decimais. Pares com valor ausente são "
        "descartados par a par (comportamento padrão de `DataFrame.corr`), de "
        "modo que as correlações envolvendo `MonthlyIncome` e "
        "`NumberOfDependents` usam menos linhas que as demais.\n",
        frame_to_markdown(corr, "", formatter=lambda v: fmt_num(v, 2)),
    ]
    return "\n".join(body)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def build_report(frame: pd.DataFrame) -> str:
    """Assemble the full markdown report."""
    header = (
        "# Relatório de inspeção — dataset bruto\n\n"
        f"Fonte: `{RAW_DATA_PATH.relative_to(RAW_DATA_PATH.parents[2])}` "
        "(OpenML id 45577, *Give-Me-Some-Credit*), lido sem nenhuma limpeza, "
        "imputação ou transformação.\n\n"
        "Este documento apenas **relata números**. Nenhuma linha aqui "
        "interpreta os valores ou propõe regra de contrato de dados.\n"
    )

    sections = [
        section_shape(frame),
        section_target(frame),
        section_missing(frame),
        section_describe(frame),
        section_age(frame),
        section_sentinels(frame),
        section_duplicates(frame),
        section_utilization(frame),
        section_debt_ratio_by_income(frame),
        section_correlation(frame),
    ]
    return header + "\n---\n\n" + "\n\n---\n\n".join(sections)


def main() -> None:
    """Load the raw dataset and write reports/inspection.md."""
    configure_logging()

    frame = load_raw()
    log.info("Loaded %s with shape %s", RAW_DATA_PATH.name, frame.shape)

    report = build_report(frame)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    log.info("Wrote %s (%d lines)", REPORT_PATH, report.count("\n") + 1)
    print(f"Relatório escrito em {REPORT_PATH}")


if __name__ == "__main__":
    main()
