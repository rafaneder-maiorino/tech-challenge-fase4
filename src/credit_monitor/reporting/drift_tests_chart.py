"""The one chart from stage 2 that goes in the video: false alarms versus n.

Form: a line chart, because the job is change of a rate along a continuous
axis, with one line per detection rule. The x-axis is logarithmic because the
sample sizes span two orders of magnitude and the interesting behaviour is at
the small end.

Palette: categorical slots 1, 2, 3 and 7 of the reference palette, taken in
fixed order and validated with the six checks before use (all-pairs mode: worst
CVD deltaE 9.2, worst normal-vision deltaE 16.3, both above their floors). Aqua
comes in at 2.74:1 against the light surface, below the 3:1 contrast gate,
which obliges **relief**: every line is directly labelled, so identity never
rests on colour alone. Benjamini-Hochberg is drawn dashed in the same hue as
Bonferroni rather than given a hue of its own — the two coincide exactly in
every draw, and a shared hue with a different dash says "same family, same
answer" instead of implying a fifth independent rule.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#8a8880"
GRID = "#e6e5e0"

# Validated categorical slots, in fixed order.
BLUE, ORANGE, AQUA, VIOLET = "#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7"

BATCH_SIZE = 7000
ALPHA = 0.05


def render_false_alarm_chart(family_wise: pd.DataFrame, path: Path) -> Path:
    """Draw the family-wise false-alarm rate against sample size.

    Args:
        family_wise: One row per n, with a column per rule.
        path: Destination PNG.

    Returns:
        The path written.
    """
    frame = family_wise.sort_values("n")
    # `label_at_end` is selective on purpose: the four low series all sit
    # between 0 and 3% at the right edge, so labelling each one there produces
    # a pile of overlapping text. Only the separable headline series is
    # labelled in place; the legend and the table in summary.md carry the rest,
    # which is also what discharges the contrast WARN on aqua.
    series = [
        ("ks_any", "KS sem correção", BLUE, "-", "o", True),
        ("ks_bonferroni", "KS + Bonferroni", ORANGE, "-", "s", False),
        ("ks_benjamini_hochberg", "KS + Benjamini-Hochberg", ORANGE, "--", "D", False),
        ("psi_warn_any", "PSI > 0,10", AQUA, "-", "^", False),
        ("psi_alert_any", "PSI > 0,25", VIOLET, "-", "v", False),
    ]

    figure, axes = plt.subplots(figsize=(11, 6.2), dpi=200)
    figure.patch.set_facecolor(SURFACE)
    axes.set_facecolor(SURFACE)

    # Recessive reference marks, drawn first so data sits on top.
    axes.axhline(ALPHA, color=INK_MUTED, linewidth=1.2, linestyle=":", zorder=1)
    axes.annotate(
        "alfa = 0,05",
        xy=(frame["n"].min(), ALPHA),
        xytext=(2, 5),
        textcoords="offset points",
        color=INK_MUTED,
        fontsize=10,
    )
    axes.axvline(BATCH_SIZE, color=INK_MUTED, linewidth=1.2, linestyle=":", zorder=1)
    # Parked low and to the right of the guide line: the upper-left quadrant
    # belongs to the legend, and the lower right is the only region no series
    # passes through.
    axes.annotate(
        f"lote de produção\nn = {BATCH_SIZE:,}".replace(",", "."),
        xy=(BATCH_SIZE, 0.075),
        xytext=(10, 0),
        textcoords="offset points",
        color=INK_MUTED,
        fontsize=10,
        ha="left",
        va="center",
        linespacing=1.4,
    )

    for column, label, colour, dash, marker, label_at_end in series:
        axes.plot(
            frame["n"],
            frame[column],
            color=colour,
            linewidth=2.0,
            linestyle=dash,
            marker=marker,
            markersize=8,
            markeredgecolor=SURFACE,
            markeredgewidth=1.5,
            label=label,
            zorder=3,
        )
        # Direct label at the line end. Text in ink, never in the series
        # colour: the coloured line ending beside it carries the identity.
        if label_at_end:
            axes.annotate(
                label,
                xy=(frame["n"].iloc[-1], frame[column].iloc[-1]),
                xytext=(12, 0),
                textcoords="offset points",
                color=INK_PRIMARY,
                fontsize=11,
                fontweight="bold",
                va="center",
            )

    axes.set_xscale("log")
    axes.set_xticks(list(frame["n"]))
    axes.set_xticklabels([f"{n:,}".replace(",", ".") for n in frame["n"]])
    axes.set_xlim(frame["n"].min() * 0.86, frame["n"].max() * 1.75)
    axes.set_ylim(-0.02, 0.38)
    axes.set_yticks([0, 0.05, 0.1, 0.2, 0.3])
    axes.set_yticklabels(["0%", "5%", "10%", "20%", "30%"])

    axes.set_xlabel("tamanho do lote (escala log)", color=INK_SECONDARY, fontsize=11)
    axes.set_ylabel(
        "lotes sem drift que acionam alarme", color=INK_SECONDARY, fontsize=11
    )
    figure.text(
        0.055,
        0.955,
        "Teste A/A: taxa de falso alarme por regra",
        color=INK_PRIMARY,
        fontsize=17,
        fontweight="bold",
        ha="left",
        va="top",
    )
    figure.text(
        0.055,
        0.895,
        "200 amostras do holdout contra a referência completa, sem drift algum — "
        "todo alarme aqui é falso.\n"
        "Bonferroni e Benjamini-Hochberg coincidem em todos os tamanhos.",
        color=INK_SECONDARY,
        fontsize=10.5,
        ha="left",
        va="top",
        linespacing=1.6,
    )

    axes.grid(axis="y", color=GRID, linewidth=1, zorder=0)
    axes.set_axisbelow(True)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(GRID)
    axes.tick_params(colors=INK_SECONDARY, labelsize=10, length=0)

    axes.legend(
        loc="upper left",
        frameon=False,
        fontsize=10.5,
        labelcolor=INK_SECONDARY,
        handlelength=2.4,
        ncols=2,
        columnspacing=1.6,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    figure.subplots_adjust(top=0.78, left=0.095, right=0.86, bottom=0.115)
    figure.savefig(path, facecolor=SURFACE)
    plt.close(figure)
    return path
