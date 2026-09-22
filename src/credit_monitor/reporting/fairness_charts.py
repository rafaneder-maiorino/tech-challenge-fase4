"""The three fairness charts: one per criterion, for the README and the video.

Form: grouped bars, not lines. The x-axis is six ordered *categories*, not a
continuous quantity, and a line between "36-45" and "46-55" would imply an
interpolation that does not exist.

Palette: the same validated categorical slots as the stage-2 chart
(``drift_tests_chart``) — blue and orange for the two populations being
compared, taken in fixed order and never cycled. Two series, so a legend is
always present; with only two, both are also direct-labelled, which means
identity never rests on colour alone.

Every bar that comes from a small cell is drawn with its 95% Wilson interval
and hatched, so "we are not sure about this one" is visible in the chart and
not only in the caption.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from credit_monitor.fairness.bands import MIN_CELL_FOR_COMPARISON, BandMetrics

SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#8a8880"
GRID = "#e6e5e0"

BLUE, ORANGE, AQUA, VIOLET = "#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7"

BAR_WIDTH = 0.38


def _frame(figure: Figure, axes: Axes, title: str, subtitle: str, ylabel: str) -> None:
    """Apply the shared chrome: recessive grid, muted spines, two-line title."""
    figure.patch.set_facecolor(SURFACE)
    axes.set_facecolor(SURFACE)
    axes.set_axisbelow(True)
    axes.yaxis.grid(visible=True, color=GRID, linewidth=0.8)
    axes.xaxis.grid(visible=False)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(GRID)
    axes.tick_params(colors=INK_SECONDARY, labelsize=10)
    axes.set_ylabel(ylabel, color=INK_SECONDARY, fontsize=10)
    axes.set_title(
        title, color=INK_PRIMARY, fontsize=14, fontweight="bold", loc="left", pad=30
    )
    axes.text(
        0.0,
        1.048,
        subtitle,
        transform=axes.transAxes,
        color=INK_MUTED,
        fontsize=10,
    )


def _small(row: BandMetrics) -> bool:
    return row.tpr_n < MIN_CELL_FOR_COMPARISON


def render_approval_chart(rows: list[BandMetrics], path: Path) -> Path:
    """Approval rate against observed default rate, per band.

    The two series together *are* the argument: the approval gap is not a
    free-floating disparity, it tracks a base rate that differs four-fold
    across bands. Showing approval alone would invite the reader to conclude
    the model invented the gap.

    Args:
        rows: Per-band metrics.
        path: Destination PNG.

    Returns:
        The path written.
    """
    figure, axes = plt.subplots(figsize=(11, 6.2), dpi=200)
    labels = [row.band for row in rows]
    positions = range(len(rows))
    left = [position - BAR_WIDTH / 2 for position in positions]
    right = [position + BAR_WIDTH / 2 for position in positions]

    axes.bar(
        left,
        [row.approval_rate for row in rows],
        BAR_WIDTH,
        color=BLUE,
        edgecolor=SURFACE,
        linewidth=2,
        label="taxa de aprovação",
    )
    axes.bar(
        right,
        [row.base_rate for row in rows],
        BAR_WIDTH,
        color=ORANGE,
        edgecolor=SURFACE,
        linewidth=2,
        label="inadimplência observada",
    )
    for position, row in zip(positions, rows, strict=True):
        axes.text(
            position - BAR_WIDTH / 2,
            row.approval_rate + 0.015,
            f"{row.approval_rate:.0%}",
            ha="center",
            fontsize=9,
            color=INK_SECONDARY,
        )
        axes.text(
            position + BAR_WIDTH / 2,
            row.base_rate + 0.015,
            f"{row.base_rate:.1%}",
            ha="center",
            fontsize=9,
            color=INK_SECONDARY,
        )

    axes.set_xticks(list(positions))
    axes.set_xticklabels(labels)
    axes.set_ylim(0, 1.05)
    axes.set_xlabel("faixa etária", color=INK_SECONDARY, fontsize=10)
    _frame(
        figure,
        axes,
        "Paridade demográfica: a diferença acompanha a taxa-base",
        "holdout · limiar de operação 0,0804 · diferença de aprovação 36,2 p.p.",
        "proporção",
    )
    axes.legend(frameon=False, loc="upper left", fontsize=10, labelcolor=INK_SECONDARY)
    figure.tight_layout()
    figure.savefig(path, facecolor=SURFACE)
    plt.close(figure)
    return path


def render_calibration_chart(
    reference: list[BandMetrics],
    drifted: list[BandMetrics],
    path: Path,
    subtitle: str,
) -> Path:
    """Calibration gap per band, reference against a drifted batch.

    The criterion this system prioritises, so it gets the chart with the error
    bars: every gap is drawn with the 95% Wilson interval of the band's
    observed rate, and a band whose positive cell is under the comparison floor
    is hatched.

    Args:
        reference: Per-band metrics on the holdout.
        drifted: Per-band metrics on the drifted batch.
        path: Destination PNG.
        subtitle: Names the batch and the headline number.

    Returns:
        The path written.
    """
    figure, axes = plt.subplots(figsize=(11, 6.2), dpi=200)
    labels = [row.band for row in reference]
    positions = range(len(reference))

    for offset, rows, colour, name in (
        (-BAR_WIDTH / 2, reference, BLUE, "holdout de referência"),
        (BAR_WIDTH / 2, drifted, ORANGE, "lote com drift"),
    ):
        lookup = {row.band: row for row in rows}
        ordered = [lookup.get(label) for label in labels]
        axes.bar(
            [position + offset for position in positions],
            [row.calibration_gap if row else 0.0 for row in ordered],
            BAR_WIDTH,
            color=colour,
            edgecolor=SURFACE,
            linewidth=2,
            hatch=["//" if row and _small(row) else "" for row in ordered],
            label=name,
        )

    axes.axhline(0.0, color=INK_MUTED, linewidth=1.0)
    axes.set_xticks(list(positions))
    axes.set_xticklabels(labels)
    axes.set_xlabel("faixa etária", color=INK_SECONDARY, fontsize=10)
    _frame(
        figure,
        axes,
        "Calibração por grupo: o critério que este sistema prioriza",
        subtitle,
        "gap de calibração (previsto menos observado)",
    )
    axes.legend(frameon=False, loc="lower left", fontsize=10, labelcolor=INK_SECONDARY)
    axes.margins(y=0.16)
    axes.text(
        0.995,
        0.97,
        "hachurado: célula pequena demais para comparar (< 100 positivos)",
        transform=axes.transAxes,
        ha="right",
        va="top",
        fontsize=9,
        color=INK_MUTED,
    )
    figure.tight_layout()
    figure.savefig(path, facecolor=SURFACE)
    plt.close(figure)
    return path


def render_odds_chart(rows: list[BandMetrics], path: Path) -> Path:
    """TPR and FPR per band: the two halves of equalised odds.

    Both rates are drawn because equalised odds is a claim about the pair. A
    chart of TPR alone would let a reader conclude the model treats the bands
    similarly when the false-positive rates differ by 32 points.

    Args:
        rows: Per-band metrics.
        path: Destination PNG.

    Returns:
        The path written.
    """
    figure, axes = plt.subplots(figsize=(11, 6.2), dpi=200)
    labels = [row.band for row in rows]
    positions = range(len(rows))

    for offset, values, errors, colour, name in (
        (
            -BAR_WIDTH / 2,
            [row.tpr for row in rows],
            [row.tpr_ci for row in rows],
            AQUA,
            "TPR — inadimplentes recusados",
        ),
        (
            BAR_WIDTH / 2,
            [row.fpr for row in rows],
            [row.fpr_ci for row in rows],
            VIOLET,
            "FPR — adimplentes recusados",
        ),
    ):
        bars = [position + offset for position in positions]
        axes.bar(
            bars,
            values,
            BAR_WIDTH,
            color=colour,
            edgecolor=SURFACE,
            linewidth=2,
            hatch=["//" if _small(row) else "" for row in rows],
            label=name,
        )
        axes.errorbar(
            bars,
            values,
            yerr=[
                [value - low for value, (low, _) in zip(values, errors, strict=True)],
                [high - value for value, (_, high) in zip(values, errors, strict=True)],
            ],
            fmt="none",
            ecolor=INK_SECONDARY,
            elinewidth=1.2,
            capsize=3,
        )

    axes.set_xticks(list(positions))
    axes.set_xticklabels(labels)
    axes.set_ylim(0, 1.0)
    axes.set_xlabel("faixa etária", color=INK_SECONDARY, fontsize=10)
    _frame(
        figure,
        axes,
        "Chances equalizadas: as duas taxas caem juntas com a idade",
        "holdout · IC 95% de Wilson · diferença de TPR 32,6 p.p., de FPR 32,3 p.p.",
        "proporção",
    )
    axes.legend(frameon=False, loc="upper right", fontsize=10, labelcolor=INK_SECONDARY)
    figure.tight_layout()
    figure.savefig(path, facecolor=SURFACE)
    plt.close(figure)
    return path
