"""One chart style for every figure in the repo.

Colours are role-based: two categorical slots that stay colour-blind-safe next
to each other (blue, orange), an ordered blue ramp for series that have a
natural order, and neutral ink for everything that is not data. Text never
takes a series colour; identity is carried by a legend or a direct label.
"""

SURFACE = "#fcfcfb"
INK     = "#0b0b0b"   # titles, values
INK_2   = "#52514e"   # axis titles, annotations
MUTED   = "#898781"   # tick labels
GRID    = "#e1e0d9"   # hairline gridlines
AXIS    = "#c3c2b7"   # baselines, reference rules
NEUTRAL = "#c3c2b7"   # de-emphasised marks

BLUE    = "#2a78d6"   # categorical slot 1
ORANGE  = "#eb6834"   # categorical slot 2
BLUE_RAMP = ("#86b6ef", "#2a78d6", "#104281")   # ordinal: light -> dark

WIDTH_IN = 8.8        # renders ~880px wide in a GitHub README at 100px/in
DPI      = 200

_RC = {
    "figure.facecolor":   SURFACE,
    "axes.facecolor":     SURFACE,
    "savefig.facecolor":  SURFACE,
    "font.family":        "sans-serif",
    "font.sans-serif":    ["Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"],
    "font.size":          9.5,
    "text.color":         INK,
    "axes.edgecolor":     AXIS,
    "axes.linewidth":     0.8,
    "axes.labelcolor":    INK_2,
    "axes.labelsize":     9.5,
    "axes.titlesize":     10.5,
    "axes.titleweight":   "semibold",
    "axes.titlelocation": "left",
    "axes.titlepad":      10,
    "axes.spines.top":    False,
    "axes.spines.right":  False,
    "axes.axisbelow":     True,
    "axes.grid":          False,
    "grid.color":         GRID,
    "grid.linewidth":     0.8,
    "grid.linestyle":     "-",
    "xtick.color":        MUTED,
    "ytick.color":        MUTED,
    "xtick.labelsize":    9,
    "ytick.labelsize":    9,
    "xtick.major.size":   0,
    "ytick.major.size":   0,
    "xtick.major.pad":    6,
    "ytick.major.pad":    6,
    "legend.frameon":     False,
    "legend.fontsize":    9,
    "lines.linewidth":    2.0,
    "lines.solid_capstyle": "round",
    "figure.dpi":         100,
    "savefig.dpi":        DPI,
}


def apply() -> None:
    """Install the shared style into matplotlib's rcParams."""
    import matplotlib as mpl
    mpl.rcParams.update(_RC)


def style_axes(ax, title=None, xlabel=None, ylabel=None, grid="y"):
    """Title/labels plus a recessive solid hairline grid on one axis (or none)."""
    if title:
        ax.set_title(title)
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)
    if grid in ("x", "y", "both"):
        ax.grid(True, axis=grid)
    return ax


def headline(fig, text: str, sub: str = None) -> None:
    """Figure-level title (the finding) with an optional one-line subtitle."""
    fig.text(0.012, 0.975, text, ha="left", va="top", fontsize=12.5,
             fontweight="semibold", color=INK)
    if sub:
        fig.text(0.012, 0.975 - 0.26 / fig.get_figheight(), sub, ha="left", va="top",
                 fontsize=9.5, color=INK_2)


def save(fig, path) -> None:
    import matplotlib.pyplot as plt
    from pathlib import Path

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=DPI)
    plt.close(fig)
    print(f"  figure -> {out}")
