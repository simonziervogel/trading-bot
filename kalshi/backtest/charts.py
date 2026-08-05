"""Matplotlib charts for backtest results.

Each function returns a matplotlib Figure so callers can save or display it.
Three chart types:
  equity_curve   — cumulative NO-side PnL over time
  bucket_chart   — bar chart: observed NO WR vs implied per YES-price bucket
  segment_heatmap — time segment vs metric comparison
"""

from typing import Union
import math


def _val(o, key: str):
    return o[key] if isinstance(o, dict) else getattr(o, key)


# Canonical bucket and segment order
_BUCKETS  = ["0.70-0.75", "0.75-0.80", "0.80-0.85",
              "0.85-0.90", "0.90-0.95", "0.95-1.00"]
_SEGMENTS = ["us_open_burst", "us_session", "eu_session", "off_hours"]


def equity_curve(obs: list, title: str = "Equity Curve — NO side"):
    """Cumulative NO-side PnL over entry time.

    Each trade contributes +no_price (win) or -no_price (loss).
    Returns a matplotlib Figure.
    """
    import matplotlib.pyplot as plt

    if not obs:
        fig, ax = plt.subplots()
        ax.set_title(title)
        ax.text(0.5, 0.5, "No observations", ha="center", va="center",
                transform=ax.transAxes)
        return fig

    sorted_obs = sorted(obs, key=lambda o: _val(o, "entry_time_utc"))
    equity     = 0.0
    xs, ys     = [], []
    for o in sorted_obs:
        no_price = _val(o, "no_price")
        won      = _val(o, "no_won")
        equity  += no_price if won else -no_price
        xs.append(_val(o, "entry_time_utc"))
        ys.append(equity)

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(range(len(ys)), ys, linewidth=1.2, color="#2196F3")
    ax.axhline(0, color="gray", linewidth=0.7, linestyle="--")
    ax.fill_between(range(len(ys)), ys, 0,
                    where=[y >= 0 for y in ys], alpha=0.15, color="#4CAF50")
    ax.fill_between(range(len(ys)), ys, 0,
                    where=[y < 0 for y in ys], alpha=0.15, color="#F44336")
    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.set_xlabel("Trade number")
    ax.set_ylabel("Cumulative PnL (contracts)")
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    return fig


def bucket_chart(obs: list, title: str = "NO Win Rate vs Implied by YES Bucket"):
    """Bar chart comparing observed NO win rate vs implied probability per bucket.

    Error bars show the Wilson 95% confidence interval.
    Returns a matplotlib Figure.
    """
    import matplotlib.pyplot as plt
    import numpy as np
    from kalshi.backtest.metrics import summary_by_bucket, wilson_ci

    bm = summary_by_bucket(obs)

    labels    = []
    wr_vals   = []
    impl_vals = []
    err_lo    = []
    err_hi    = []
    ns        = []

    for lbl in _BUCKETS:
        m = bm[lbl]
        if m.get("n", 0) == 0:
            continue
        labels.append(lbl)
        wr_vals.append(m["win_rate"])
        impl_vals.append(m["implied"])
        err_lo.append(m["win_rate"] - m["ci_lo"])
        err_hi.append(m["ci_hi"] - m["win_rate"])
        ns.append(m["n"])

    if not labels:
        fig, ax = plt.subplots()
        ax.set_title(title)
        ax.text(0.5, 0.5, "No observations", ha="center", va="center",
                transform=ax.transAxes)
        return fig

    x      = np.arange(len(labels))
    width  = 0.35
    fig, ax = plt.subplots(figsize=(10, 5))

    bars_wr = ax.bar(x - width / 2, wr_vals, width, label="Observed NO WR",
                     color="#2196F3", alpha=0.85,
                     yerr=[err_lo, err_hi], capsize=5, error_kw={"linewidth": 1.2})
    bars_im = ax.bar(x + width / 2, impl_vals, width, label="Implied (NO price)",
                     color="#FF9800", alpha=0.70)

    # Annotate with N
    for bar, n in zip(bars_wr, ns):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.003,
                f"n={n}", ha="center", va="bottom", fontsize=8, color="gray")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=0)
    ax.set_xlabel("YES mid-price bucket")
    ax.set_ylabel("Win rate")
    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.legend()
    ax.set_ylim(0, min(1.0, max(wr_vals + impl_vals) * 1.25))
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    return fig


def segment_heatmap(obs: list, title: str = "NO Win Rate by Time Segment"):
    """Horizontal bar chart of NO win rate per trading session.

    Color encodes edge (green = above overall WR, red = below).
    Returns a matplotlib Figure.
    """
    import matplotlib.pyplot as plt
    from kalshi.backtest.metrics import summary_by_segment, win_rate_with_ci

    overall_wr, _, _ = win_rate_with_ci(obs)
    sm = summary_by_segment(obs)

    segs, wrs, ns, evs, err_lo, err_hi = [], [], [], [], [], []
    for seg in _SEGMENTS:
        m = sm[seg]
        if m.get("n", 0) == 0:
            continue
        segs.append(seg.replace("_", "\n"))
        wrs.append(m["win_rate"])
        ns.append(m["n"])
        evs.append(m["ev"])
        err_lo.append(m["win_rate"] - m["ci_lo"])
        err_hi.append(m["ci_hi"] - m["win_rate"])

    if not segs:
        fig, ax = plt.subplots()
        ax.set_title(title)
        ax.text(0.5, 0.5, "No observations", ha="center", va="center",
                transform=ax.transAxes)
        return fig

    colors = ["#4CAF50" if wr >= overall_wr else "#F44336" for wr in wrs]

    fig, ax = plt.subplots(figsize=(9, 4))
    y = range(len(segs))
    ax.barh(y, wrs, xerr=[err_lo, err_hi], color=colors, alpha=0.80,
            capsize=4, error_kw={"linewidth": 1.2})
    ax.axvline(overall_wr, color="navy", linestyle="--", linewidth=1.2,
               label=f"Overall WR {overall_wr:.1%}")

    ax.set_yticks(list(y))
    ax.set_yticklabels(segs, fontsize=9)
    ax.set_xlabel("NO win rate")
    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.legend(fontsize=9)

    for i, (wr, n) in enumerate(zip(wrs, ns)):
        ax.text(wr + 0.002, i, f"n={n}", va="center", fontsize=8, color="gray")

    ax.grid(axis="x", linestyle="--", alpha=0.4)
    fig.tight_layout()
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
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  Chart saved -> {out_path}")

