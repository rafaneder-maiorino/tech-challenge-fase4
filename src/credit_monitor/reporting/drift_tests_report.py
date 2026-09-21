"""Assemble the stage-2 statistical results into one markdown report."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from credit_monitor.drift_tests.aa import (
    ALPHA,
    AAResult,
    effective_bins,
    theoretical_psi_bias,
)
from credit_monitor.reporting.drift import CONTINUOUS_FEATURES


def _md(frame: pd.DataFrame, formats: dict[str, str]) -> str:
    header = "| " + " | ".join(frame.columns) + " |"
    divider = "|---" * len(frame.columns) + "|"
    lines = [header, divider]
    for row in frame.itertuples(index=False):
        cells = [
            format(value, formats.get(column, ""))
            for column, value in zip(frame.columns, row, strict=True)
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def family_wise_table(result: AAResult) -> str:
    """Batch-level false-alarm rate per rule and n."""
    frame = result.family_wise_rates().rename(
        columns={
            "n": "n",
            "ks_any": "KS sem correção",
            "ks_bonferroni": "KS + Bonferroni",
            "ks_benjamini_hochberg": "KS + BH",
            "psi_warn_any": "PSI > 0,10",
            "psi_alert_any": "PSI > 0,25",
        }
    )
    return _md(frame, {c: ".1%" for c in frame.columns if c != "n"} | {"n": ",d"})


def ks_by_type_table(result: AAResult) -> str:
    """Per-feature KS rate, split into continuous and tie-dominated counts."""
    per_feature = result.per_feature_rates()
    per_feature["tipo"] = np.where(
        per_feature["feature"].isin(CONTINUOUS_FEATURES), "contínua", "contagem"
    )
    pivot = per_feature.groupby(["tipo", "n"])["ks_far"].mean().unstack().reset_index()
    pivot.columns = ["tipo"] + [
        f"n = {int(c):,}".replace(",", ".") for c in pivot.columns[1:]
    ]
    return _md(pivot, {c: ".1%" for c in pivot.columns if c != "tipo"})


def psi_bias_table(result: AAResult, reference: pd.DataFrame) -> str:
    """Measured mean PSI under the null against the chi-square prediction."""
    bins = effective_bins(reference)
    per_feature = result.per_feature_rates()
    rows = []
    for n in sorted(per_feature["n"].unique()):
        measured = float(per_feature[per_feature["n"] == n]["psi_mean"].mean())
        predicted = float(
            np.mean(
                [
                    theoretical_psi_bias(int(n), result.reference_rows, bins[c])
                    for c in bins
                ]
            )
        )
        rows.append(
            {
                "n": int(n),
                "PSI médio medido": measured,
                "previsto (bins-1)(1/n+1/m)": predicted,
                "razão": measured / predicted,
            }
        )
    frame = pd.DataFrame(rows)
    return _md(
        frame,
        {
            "n": ",d",
            "PSI médio medido": ".4f",
            "previsto (bins-1)(1/n+1/m)": ".4f",
            "razão": ".2f",
        },
    )


def small_n_offenders_table(result: AAResult, reference: pd.DataFrame) -> str:
    """Which features drive the PSI false alarms at n = 250, and their bin count."""
    bins = effective_bins(reference)
    per_feature = result.per_feature_rates()
    small = per_feature[
        (per_feature["n"] == 250) & (per_feature["psi_warn_far"] > 0)
    ].sort_values("psi_warn_far", ascending=False)
    frame = pd.DataFrame(
        {
            "feature": small["feature"],
            "disparos PSI > 0,10": small["psi_warn_far"],
            "bins": [bins[c] for c in small["feature"]],
            "viés previsto": [
                theoretical_psi_bias(250, result.reference_rows, bins[c])
                for c in small["feature"]
            ],
        }
    )
    return _md(
        frame,
        {"disparos PSI > 0,10": ".1%", "bins": "d", "viés previsto": ".4f"},
    )


def sensitivity_table(frame: pd.DataFrame, column: str) -> str:
    """KS and PSI next to what the model actually did."""
    subset = frame[frame["column"] == column].copy()
    out = pd.DataFrame(
        {
            "π": subset["pi_total"],
            "n": subset["n"],
            "p-valor KS": subset["ks_pvalue"],
            "PSI": subset["psi"],
            "Δ previsão média": subset["mean_prediction_shift"],
            "Δ AUC": subset["auc_delta"],
        }
    )
    return _md(
        out,
        {
            "π": ".2f",
            "n": ",d",
            "p-valor KS": ".2e",
            "PSI": ".4f",
            "Δ previsão média": "+.5f",
            "Δ AUC": "+.5f",
        },
    )


def mmd_tables(cache: Path) -> tuple[str, str, str]:
    """Batch table, self-A/A line and localization, from the cached run."""
    batches = pd.read_parquet(cache / "mmd_batches.parquet")
    table = _md(
        pd.DataFrame(
            {
                "lote": batches["label"],
                "MMD²": batches["mmd2"],
                "p": batches["pvalue"],
                "detecta": np.where(batches["detected"], "**SIM**", "não"),
            }
        ),
        {"MMD²": ".6f", "p": ".4f"},
    )
    pvalues = np.load(cache / "mmd_self_aa.npy")
    self_aa = (
        f"Taxa de falso alarme: **{float((pvalues < ALPHA).mean()):.1%}** em "
        f"{len(pvalues)} repetições, com p-valores uniformes "
        f"(quartis {np.percentile(pvalues, [25, 50, 75]).round(3).tolist()})."
    )
    payload = json.loads((cache / "mmd_localization.json").read_text())
    blocks = []
    for label, data in payload["localization"].items():
        rows = pd.DataFrame(
            {
                "par": [
                    f"`{p['column_x']}` / `{p['column_y']}`"
                    + (
                        " **← par invertido**"
                        if {p["column_x"], p["column_y"]} == set(data["expected"])
                        else ""
                    )
                    for p in data["pairs"][:4]
                ],
                "MMD²": [p["mmd2"] for p in data["pairs"][:4]],
                "p": [p["pvalue"] for p in data["pairs"][:4]],
            }
        )
        blocks.append(
            f"**{label}** — o par invertido sai em **#{data['rank_of_expected']}**\n\n"
            + _md(rows, {"MMD²": ".6f", "p": ".4f"})
        )
    return table, self_aa, "\n\n".join(blocks)
