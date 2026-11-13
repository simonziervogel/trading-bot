"""Matplotlib charts for Favorite-Longshot backtest results.

Each function returns a matplotlib Figure so callers can save or display it.
Three chart types:
  equity_curve    — cumulative NO-side PnL over entry order
  bucket_chart    — observed NO win rate vs the price paid, per YES-price bucket
  segment_heatmap — NO win rate per trading session vs the overall rate

Styling comes from kalshi.report.style, shared with every other figure.
"""


def _val(o, key: str):
    return o[key] if isinstance(o, dict) else getattr(o, key)


# Canonical bucket and segment order
_BUCKETS  = ["0.70-0.75", "0.75-0.80", "0.80-0.85",
              "0.85-0.90", "0.90-0.95", "0.95-1.00"]
_SEGMENTS = ["us_open_burst", "us_session", "eu_session", "off_hours"]


def _empty(plt, title):
    fig, ax = plt.subplots()
    ax.set_title(title)
    ax.text(0.5, 0.5, "No observations", ha="center", va="center", transform=ax.transAxes)
    return fig


def _dot_ci_row(ax, S, y, value, lo, hi, n):
    """One horizontal estimate: whisker for the interval, ringed dot, n at the right."""
    ax.plot([lo, hi], [y, y], color=S.ORANGE, linewidth=1.6, solid_capstyle="round", zorder=3)
    ax.plot([value], [y], "o", color=S.ORANGE, markersize=8.5,
            markeredgecolor=S.SURFACE, markeredgewidth=1.5, zorder=4)
    ax.text(1.01, y, f"n = {n:,}", ha="left", va="center", fontsize=8.8,
            color=S.INK_2, transform=ax.get_yaxis_transform())


def equity_curve(obs: list, title: str = "Equity Curve — NO side"):
    """Cumulative NO-side PnL over entry order.

    A NO contract costs no_price and settles at $1 (win) or $0 (loss), so each
    trade contributes +(1 - no_price) on a win or -no_price on a loss.
    Returns a matplotlib Figure.
    """
    import matplotlib.pyplot as plt
    from kalshi.report import style as S
    S.apply()

    if not obs:
        return _empty(plt, title)

    sorted_obs = sorted(obs, key=lambda o: _val(o, "entry_time_utc"))
    equity, ys = 0.0, []
    for o in sorted_obs:
        no_price = _val(o, "no_price")
        equity  += (1.0 - no_price) if _val(o, "no_won") else -no_price
        ys.append(equity)

    fig, ax = plt.subplots(figsize=(S.WIDTH_IN, 3.6))
    xs = range(len(ys))
    ax.fill_between(xs, ys, 0, color=S.ORANGE, alpha=0.10, linewidth=0)
    ax.plot(xs, ys, color=S.ORANGE)
    ax.axhline(0, color=S.INK_2, linewidth=0.9)
    S.style_axes(ax, title=title, xlabel="trade number",
                 ylabel="cumulative PnL, $ per contract (before fees)")
    fig.tight_layout()
    return fig


def bucket_chart(obs: list, title: str = "NO Win Rate vs Implied by YES Bucket"):
    """Observed NO win rate (dot, Wilson 95% CI) against the price paid (tick),
    per YES-price bucket. There is an edge only where the dot sits clear to
    the right of the tick.

    Returns a matplotlib Figure.
    """
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from kalshi.backtest.metrics import summary_by_bucket
    from kalshi.report import style as S
    S.apply()

    bm   = summary_by_bucket(obs)
    rows = [(lbl, bm[lbl]) for lbl in _BUCKETS if bm[lbl].get("n", 0) > 0]
    if not rows:
        return _empty(plt, title)

    fig, ax = plt.subplots(figsize=(S.WIDTH_IN, 0.5 * len(rows) + 1.6))
    for y, (_lbl, m) in zip(range(len(rows) - 1, -1, -1), rows):
        _dot_ci_row(ax, S, y, m["win_rate"], m["ci_lo"], m["ci_hi"], m["n"])
        ax.plot([m["implied"]] * 2, [y - 0.22, y + 0.22], color=S.INK, linewidth=1.6, zorder=5)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([lbl.replace("-", "–") for lbl, _ in reversed(rows)])
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xlim(0, None)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    S.style_axes(ax, title=title, xlabel="NO win rate (95% Wilson interval)",
                 ylabel="YES mid-price at entry", grid="x")
    ax.legend(handles=[
        Line2D([0], [0], marker="o", color=S.ORANGE, markersize=7, linewidth=1.6,
               markeredgecolor=S.SURFACE, markeredgewidth=1.2, label="observed"),
        Line2D([0], [0], marker="|", color=S.INK, linestyle="none", markersize=10,
               markeredgewidth=1.6, label="price paid (break-even before fees)"),
    ], loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2, handlelength=1.6,
        columnspacing=1.8, borderaxespad=0.3)
    fig.tight_layout(rect=(0, 0, 0.93, 1))
    return fig


def segment_heatmap(obs: list, title: str = "NO Win Rate by Time Segment"):
    """NO win rate per trading session (dot, Wilson 95% CI) against the overall
    rate (vertical rule).

    Returns a matplotlib Figure.
    """
    import matplotlib.pyplot as plt
    from kalshi.backtest.metrics import summary_by_segment, win_rate_with_ci
    from kalshi.report import style as S
    S.apply()

    overall_wr, _, _ = win_rate_with_ci(obs)
    sm   = summary_by_segment(obs)
    rows = [(seg, sm[seg]) for seg in _SEGMENTS if sm[seg].get("n", 0) > 0]
    if not rows:
        return _empty(plt, title)

    fig, ax = plt.subplots(figsize=(S.WIDTH_IN, 0.5 * len(rows) + 1.6))
    for y, (_seg, m) in zip(range(len(rows) - 1, -1, -1), rows):
        _dot_ci_row(ax, S, y, m["win_rate"], m["ci_lo"], m["ci_hi"], m["n"])
    ax.axvline(overall_wr, color=S.INK_2, linewidth=0.9, zorder=2)
    ax.text(overall_wr, len(rows) - 0.45, f" overall {overall_wr:.1%}", ha="left",
            va="top", fontsize=8.8, color=S.INK_2)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([seg.replace("_", " ") for seg, _ in reversed(rows)])
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xlim(0, None)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    S.style_axes(ax, title=title, xlabel="NO win rate (95% Wilson interval)", grid="x")
    fig.tight_layout(rect=(0, 0, 0.93, 1))
    return fig


def save_all(obs: list, path: str, split_label: str = "ALL") -> None:
    """Save all three charts as separate PNG files.

    Files are named <path>_equity.png, <path>_buckets.png, <path>_segments.png.
    The .png extension (if present) is stripped from `path` before appending suffixes.
    """
    import matplotlib.pyplot as plt
    from pathlib import Path

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    base = str(out.with_suffix(""))

    chart_specs = [
        (equity_curve,    f"{base}_equity.png",   f"Equity Curve [{split_label}]"),
        (bucket_chart,    f"{base}_buckets.png",  f"Bucket Analysis [{split_label}]"),
        (segment_heatmap, f"{base}_segments.png", f"Segment Breakdown [{split_label}]"),
    ]

    for fn, out_path, title in chart_specs:
        fig = fn(obs, title=title)
        fig.savefig(out_path, dpi=200)
        plt.close(fig)
        print(f"  Chart saved -> {out_path}")
