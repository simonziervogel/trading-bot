"""Every figure in the README, the post-mortem and the notebook.

Each function reads only from the committed data/ snapshot (via
kalshi.report.data) and returns a matplotlib Figure. Colour roles are fixed
across figures:

    blue    backtest as originally reported (60s lookahead present)
    orange  backtest with the lookahead removed / any valid backtest
    aqua    live paper trading
"""

import math

import pandas as pd

from kalshi.report import data as D
from kalshi.report import style as S
from kalshi.utils.pricing import digital_call_probability

AQUA = "#1baf7a"   # categorical slot 3: live paper trading

_MONTH_LABEL = {"01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May",
                "06": "Jun", "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct",
                "11": "Nov", "12": "Dec"}
_MIN_PER_YEAR = 365.25 * 24 * 60
_SIGMA_REF = 0.40   # observed July 2026 median realized vol, annualized


def _plt():
    import matplotlib.pyplot as plt
    S.apply()
    return plt


def _dot_ci(ax, x, y, lo, hi, color, horizontal=False, size=7.5, zorder=3):
    """A point estimate with its confidence interval: thin whisker, ringed dot."""
    if horizontal:
        ax.plot([lo, hi], [y, y], color=color, linewidth=1.6, zorder=zorder,
                solid_capstyle="round")
        ax.plot([x], [y], "o", color=color, markersize=size, markeredgecolor=S.SURFACE,
                markeredgewidth=1.5, zorder=zorder + 1)
    else:
        ax.plot([x, x], [lo, hi], color=color, linewidth=1.6, zorder=zorder,
                solid_capstyle="round")
        ax.plot([x], [y], "o", color=color, markersize=size, markeredgecolor=S.SURFACE,
                markeredgewidth=1.5, zorder=zorder + 1)


def _legend_handles(pairs):
    from matplotlib.lines import Line2D
    return [Line2D([0], [0], marker="o", color=c, markersize=7, linewidth=1.6,
                   markeredgecolor=S.SURFACE, markeredgewidth=1.2, label=label)
            for label, c in pairs]


# ---------------------------------------------------------------------------
# 1. Hero: the edge, month by month, before and after the fix
# ---------------------------------------------------------------------------

def monthly_before_after(data_dir=D.DATA_DIR):
    plt = _plt()
    panels = [
        ("no", "NO-side signals — where the edge was reported"),
        (None, "All signals"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(S.WIDTH_IN, 4.3), sharey=True)
    dodge = 0.14
    months = []

    for ax, (side, title) in zip(axes, panels):
        tab = D.monthly_before_after(side=side, data_dir=data_dir)
        tab = tab[tab["month"] != "all"]
        months = sorted(tab["month"].unique())
        for kind, color, dx in (("with_lookahead", S.BLUE, -dodge),
                                ("corrected", S.ORANGE, +dodge)):
            for i, m in enumerate(months):
                row = tab[(tab["month"] == m) & (tab["kind"] == kind)]
                if row.empty or row.iloc[0]["n"] < 2:
                    continue
                r = row.iloc[0]
                _dot_ci(ax, i + dx, r["ev"], r["ev_lo"], r["ev_hi"], color)
        ax.axhline(0, color=S.INK_2, linewidth=0.9, zorder=2)
        ax.set_xticks(range(len(months)))
        ax.set_xticklabels([_MONTH_LABEL[m[5:]] for m in months])
        ax.set_xlim(-0.6, len(months) - 0.4)
        S.style_axes(ax, title=title)
        ax.yaxis.set_major_formatter(lambda v, _: f"{v:+.2f}" if abs(v) > 1e-9 else "0")

    axes[0].set_ylabel("net EV per contract, $ (95% CI)")
    fig.legend(handles=_legend_handles([
        ("as reported — 60s lookahead in the price series", S.BLUE),
        ("corrected — same months, same engine, lookahead removed", S.ORANGE),
    ]), loc="upper left", bbox_to_anchor=(0.012, 0.855), ncol=1, handlelength=1.6,
        borderaxespad=0)
    first, last = _MONTH_LABEL[months[0][5:]], _MONTH_LABEL[months[-1][5:]]
    span = first if first == last else f"{first}–{last}"
    S.headline(fig, "Six months of significant edge were one minute of foresight",
               f"Fair Value backtest, BTC and ETH 15-minute contracts, {span} 2026, after fees")
    fig.subplots_adjust(left=0.085, right=0.985, top=0.64, bottom=0.08, wspace=0.07)
    return fig


# ---------------------------------------------------------------------------
# 2. The bug, as a timeline
# ---------------------------------------------------------------------------

def lookahead_timeline():
    plt = _plt()
    from matplotlib.patches import FancyBboxPatch

    fig, ax = plt.subplots(figsize=(S.WIDTH_IN, 3.9))
    t = [-150, -120, -90, -60, -30, 0, 30, 60, 90, 120, 150]
    p = [100.10, 100.35, 100.15, 100.55, 100.30, 100.50, 101.00, 101.55, 101.35, 101.80, 101.70]
    strike = 100.95
    y_lo, y_hi = 99.05, 102.35

    # Everything right of T is the future as far as a signal at T is concerned.
    ax.axvspan(0, 165, color=S.GRID, alpha=0.55, linewidth=0, zorder=0)
    ax.text(160, 99.80, "not yet observable at T", ha="right", va="bottom",
            fontsize=9, color=S.INK_2)
    ax.axvline(0, color=S.INK, linewidth=1.1, zorder=2)
    ax.text(0, y_hi + 0.06, "signal time T", ha="center", va="bottom", fontsize=9.5,
            color=S.INK, fontweight="semibold")

    ax.axhline(strike, color=S.AXIS, linewidth=1.0, linestyle=(0, (4, 3)), zorder=1)
    ax.text(-160, strike + 0.06, "strike", ha="left", va="bottom", fontsize=9, color=S.INK_2)

    ax.plot(t, p, color=S.NEUTRAL, linewidth=2.0, zorder=2)
    ax.text(-150, p[0] - 0.16, "Binance spot", ha="left", va="top", fontsize=9, color=S.INK_2)

    # One-minute bars as a lane under the price path.
    lane_y, lane_h = 99.30, 0.34
    for start, label in ((-120, "bar opens T−120s"), (-60, "bar opens T−60s"),
                         (0, "bar opens T"), (60, "bar opens T+60s")):
        ax.add_patch(FancyBboxPatch(
            (start + 2.5, lane_y), 55, lane_h, boxstyle="round,pad=0,rounding_size=4",
            mutation_aspect=0.012, facecolor=S.SURFACE, edgecolor=S.AXIS, linewidth=0.9,
            zorder=3))
        ax.text(start + 30, lane_y + lane_h / 2, label, ha="center", va="center",
                fontsize=8.2, color=S.INK_2, zorder=4)

    # The two prices the lookup could return for spot_at(T).
    ax.plot([0], [p[5]], "o", color=S.ORANGE, markersize=10, markeredgecolor=S.SURFACE,
            markeredgewidth=2, zorder=5)
    ax.plot([60], [p[7]], "o", color=S.BLUE, markersize=10, markeredgecolor=S.SURFACE,
            markeredgewidth=2, zorder=5)
    arrow = dict(arrowstyle="-", color=S.INK_2, linewidth=0.8, shrinkA=2, shrinkB=7)
    ax.annotate("corrected: close of the bar that ended at T\nbelow the strike, and known at T",
                xy=(0, p[5]), xytext=(-12, 99.98), ha="right", va="top", fontsize=9,
                color=S.INK, arrowprops=arrow)
    ax.annotate("as shipped: close of the bar that opens at T\nabove the strike, a price from T+60s",
                xy=(60, p[7]), xytext=(12, 102.24), ha="left", va="top", fontsize=9,
                color=S.INK, arrowprops=arrow)

    ax.set_xlim(-165, 165)
    ax.set_ylim(y_lo, y_hi)
    ax.set_xticks([-120, -60, 0, 60, 120])
    ax.set_xticklabels(["T−120s", "T−60s", "T", "T+60s", "T+120s"])
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    S.headline(fig, "Each one-minute bar was filed under the time it opened, with the price it closed at",
               "So a lookup at T returned a price from up to 60 seconds later. Schematic.")
    fig.subplots_adjust(left=0.015, right=0.985, top=0.80, bottom=0.09)
    return fig


# ---------------------------------------------------------------------------
# 3. Why one minute is enough
# ---------------------------------------------------------------------------

def sensitivity():
    plt = _plt()
    fig, ax = plt.subplots(figsize=(S.WIDTH_IN, 3.9))
    xs = [x / 2.0 for x in range(-60, 61)]
    one_min_bp = 1e4 * _SIGMA_REF * math.sqrt(1.0 / _MIN_PER_YEAR)

    ax.axvspan(-one_min_bp, one_min_bp, color=S.GRID, alpha=0.6, linewidth=0, zorder=0)
    ax.text(0, 0.035, f"typical 1-minute move\n±{one_min_bp:.1f} bp", ha="center", va="bottom",
            fontsize=8.8, color=S.INK_2, linespacing=1.3)

    for tte, color in zip((14, 4, 2), S.BLUE_RAMP):
        ys = [digital_call_probability(1.0 + x / 1e4, 1.0, tte / _MIN_PER_YEAR, _SIGMA_REF)
              for x in xs]
        ax.plot(xs, ys, color=color, label=f"{tte} min to expiry")
    ax.axhline(0.5, color=S.AXIS, linewidth=0.9, zorder=1)

    ax.set_xlim(-30, 30)
    ax.set_ylim(0, 1)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:+.0f}" if v else "0")
    S.style_axes(ax, xlabel="spot relative to strike, basis points", ylabel="model P(YES)")
    ax.legend(loc="upper left", handlelength=1.6)
    S.headline(fig, "On a 15-minute contract, a few basis points decide the model's answer",
               f"Zero-drift lognormal model at annualized volatility {_SIGMA_REF:.2f}")
    fig.subplots_adjust(left=0.085, right=0.985, top=0.82, bottom=0.14)
    return fig


# ---------------------------------------------------------------------------
# 4. Calibration: who was right, the model or the market?
# ---------------------------------------------------------------------------

def calibration(data_dir=D.DATA_DIR):
    plt = _plt()
    before, after = D.like_for_like(D.load_fair_value("with_lookahead", data_dir),
                                    D.load_fair_value("corrected", data_dir))
    fig, axes = plt.subplots(1, 2, figsize=(S.WIDTH_IN, 4.7), sharey=True)
    for ax, df, color, title in (
        (axes[0], before, S.BLUE, "As reported — 60s lookahead"),
        (axes[1], after, S.ORANGE, "Corrected"),
    ):
        cal = D.calibration(df)
        ax.plot([0, 1], [0, 1], color=S.AXIS, linewidth=1.0, zorder=1)
        ax.plot(cal["model"], cal["market"], color=S.INK_2, linewidth=1.6, zorder=2)
        for _, r in cal.iterrows():
            _dot_ci(ax, r["model"], r["realized"], r["lo"], r["hi"], color, size=6.5)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_aspect("equal")
        ticks = [0, 0.25, 0.5, 0.75, 1.0]
        ax.set_xticks(ticks)
        ax.set_yticks(ticks)
        ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
        ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
        S.style_axes(ax, title=title, xlabel="model P(YES) at entry", grid="both")
        ax.text(0.90, 0.955, "model's own forecast", ha="right", va="bottom", fontsize=8.5,
                color=S.INK_2, rotation=45, rotation_mode="anchor")
    axes[0].set_ylabel("share of contracts that settled YES")

    from matplotlib.lines import Line2D
    handles = _legend_handles([("realized, as reported", S.BLUE),
                               ("realized, corrected", S.ORANGE)])
    handles.append(Line2D([0], [0], color=S.INK_2, linewidth=1.6, label="Kalshi's quoted price (mid)"))
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.012, 0.875), ncol=3,
               handlelength=1.6, columnspacing=1.8, borderaxespad=0)
    S.headline(fig, "With the lookahead removed, outcomes follow the market's price, not the model's",
               "Signals bucketed by the model's probability; BTC and ETH, same months in both panels")
    fig.subplots_adjust(left=0.085, right=0.985, top=0.74, bottom=0.11, wspace=0.08)
    return fig


# ---------------------------------------------------------------------------
# 5. The basis: why Binance spot cannot resolve these contracts near the strike
# ---------------------------------------------------------------------------

def basis(data_dir=D.DATA_DIR):
    plt = _plt()
    from pathlib import Path
    b = pd.read_csv(Path(data_dir) / "audit_basis.csv")
    agree = pd.read_csv(Path(data_dir) / "audit_settlement_agreement.csv")
    per = b[b["series"] != "POOLED"].reset_index(drop=True)
    sd_14 = 1e4 * _SIGMA_REF * math.sqrt(14 / _MIN_PER_YEAR)

    fig, axes = plt.subplots(1, 2, figsize=(S.WIDTH_IN, 3.9),
                             gridspec_kw={"width_ratios": [1, 1.08]})

    ax = axes[0]
    ys = list(range(len(per)))
    for y, r in zip(ys, per.itertuples()):
        ax.plot([r.basis_p10_bp, r.basis_p90_bp], [y, y], color=S.BLUE, alpha=0.35,
                linewidth=6, solid_capstyle="round", zorder=2)
        ax.plot([r.basis_median_bp], [y], "o", color=S.BLUE, markersize=8.5,
                markeredgecolor=S.SURFACE, markeredgewidth=1.5, zorder=3)
    ax.axvline(0, color=S.INK_2, linewidth=0.9, zorder=1)
    ax.axvline(sd_14, color=S.INK_2, linewidth=1.0, linestyle=(0, (4, 3)), zorder=1)
    ax.text(sd_14 - 0.4, len(per) - 0.42, f"model's whole σ√τ\nat 14 min: {sd_14:.1f} bp",
            ha="right", va="center", fontsize=8.5, color=S.INK_2, linespacing=1.3)
    ax.set_yticks(ys)
    ax.set_yticklabels([s.replace("KX", "").replace("15M", "") for s in per["series"]])
    ax.set_ylim(-0.6, len(per) - 0.05)
    ax.set_xlim(-6, 23)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:+.0f}" if v else "0")
    S.style_axes(ax, title="Binance vs settlement index at market open",
                 xlabel="basis points (dot = median, bar = 10th–90th percentile)", grid="x")

    ax = axes[1]
    xs = list(range(len(agree)))
    ax.bar(xs, agree["disagreement_rate"], width=0.5, color=S.BLUE, zorder=2)
    for x, v in zip(xs, agree["disagreement_rate"]):
        label = f"{v:.0%}" if v >= 0.005 else f"{v:.1%}"
        ax.text(x, v + 0.008, label, ha="center", va="bottom", fontsize=9, color=S.INK)
    ax.set_xticks(xs)
    ax.set_xticklabels([s.replace("-inf", "+").replace("-", "–") for s in agree["move_bucket_bp"]])
    ax.set_ylim(0, 0.42)
    ax.set_yticks([0, 0.1, 0.2, 0.3, 0.4])
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    S.style_axes(ax, title="Binance's own 15-min move disagrees with the settlement",
                 xlabel="size of Binance's 15-minute move, basis points")

    S.headline(fig, "The proxy is off by about a quarter of the model's entire scale",
               f"{int(b.loc[b['series'] == 'POOLED', 'n'].iloc[0]):,} stored signals across five series; "
               "Kalshi publishes the settlement index at open as the strike")
    fig.subplots_adjust(left=0.07, right=0.985, top=0.75, bottom=0.15, wspace=0.16)
    return fig


# ---------------------------------------------------------------------------
# 6. Live against the backtest
# ---------------------------------------------------------------------------

def live_vs_backtest(data_dir=D.DATA_DIR):
    plt = _plt()
    before, after = D.like_for_like(D.load_fair_value("with_lookahead", data_dir),
                                    D.load_fair_value("corrected", data_dir))
    live = D.load_live(data_dir)
    live = live[(live["strategy"] == "fair_value") & (live["reason"] == "EXPIRED")]

    rows = [
        ("Backtest as reported", D.summarize(before[before["side"] == "no"]), S.BLUE),
        ("Live paper run, 24 Sep 2026", D.summarize_live(live), AQUA),
        ("Backtest corrected", D.summarize(after[after["side"] == "no"]), S.ORANGE),
    ]

    fig, ax = plt.subplots(figsize=(S.WIDTH_IN, 3.35))
    for y, (label, s, color) in zip((2, 1, 0), rows):
        _dot_ci(ax, s["win_rate"], y, s["wr_lo"], s["wr_hi"], color, horizontal=True, size=9)
        ax.plot([s["avg_price"]] * 2, [y - 0.2, y + 0.2], color=S.INK, linewidth=1.6, zorder=5)
        ax.text(-0.012, y + 0.11, label, ha="right", va="center", fontsize=9.5, color=S.INK,
                transform=ax.get_yaxis_transform())
        ax.text(-0.012, y - 0.17, f"n = {s['n']:,} · win rate {s['win_rate']:.1%}", ha="right",
                va="center", fontsize=8.5, color=S.INK_2, transform=ax.get_yaxis_transform())
    ax.set_ylim(-0.55, 2.55)
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.set_xlim(0, 0.8)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    S.style_axes(ax, xlabel="NO-side win rate (95% Wilson interval)", grid="x")

    from matplotlib.lines import Line2D
    ax.legend(handles=[Line2D([0], [0], marker="|", color=S.INK, linestyle="none", markersize=10,
                              markeredgewidth=1.6,
                              label="average price paid = break-even win rate before fees")],
              loc="upper left", bbox_to_anchor=(0.0, -0.36), handletextpad=0.3, borderaxespad=0)
    S.headline(fig, "Only the reported backtest won more often than it paid for",
               "Fair Value, NO side, BTC and ETH. A win rate is an edge only where it clears the price paid.")
    fig.subplots_adjust(left=0.30, right=0.985, top=0.78, bottom=0.26)
    return fig


# ---------------------------------------------------------------------------
# 7. All four strategies on one axis
# ---------------------------------------------------------------------------

def _overview_rows(data_dir=D.DATA_DIR) -> list:
    """(strategy, evidence, summary, kind) rows for the overview; skips missing data."""
    from pathlib import Path
    root, rows = Path(data_dir), []
    live = D.load_live(data_dir)

    def _live(strategy):
        return D.summarize_live(live[live["strategy"] == strategy])

    corrected = D.load_fair_value("corrected", data_dir)
    if len(corrected):
        months = sorted(corrected["month"].unique())
        first, last = _MONTH_LABEL[months[0][5:]], _MONTH_LABEL[months[-1][5:]]
        span = f"{first} 2026" if first == last else f"{first}–{last} 2026"
        rows.append(("Fair Value", f"backtest, {span}", D.summarize(corrected), "backtest"))
    rows.append(("Fair Value", "live paper, 1 session", _live("fair_value"), "live"))

    if (root / "momentum" / "oos_split.csv.gz").exists():
        mom = D.load_side_aware("momentum/oos_split.csv.gz", data_dir)
        rows.append(("Momentum", "backtest, May–Jun 2026", D.summarize(mom), "backtest"))

    for rel, label in (("longshot/2026-02_03.csv.gz", "backtest, Feb–Mar 2026"),
                       ("longshot/2026-06_sample.csv.gz", "backtest, late Jun 2026")):
        if (root / rel).exists():
            ls = D.load_longshot(rel, data_dir)
            ls = ls[ls["yes_mid"] >= 0.85]   # the strategy's own entry threshold
            rows.append(("Favorite-Longshot", label, D.summarize(ls), "backtest"))
            break
    rows.append(("Favorite-Longshot", "live paper, 26 sessions", _live("favorite_longshot"), "live"))
    rows.append(("Mean-Reversion", "live paper, 8 sessions", _live("mean_reversion"), "live"))
    return rows


def strategy_overview(data_dir=D.DATA_DIR):
    plt = _plt()
    rows = _overview_rows(data_dir)

    fig, ax = plt.subplots(figsize=(S.WIDTH_IN, 0.5 * len(rows) + 1.75))
    ys = list(range(len(rows) - 1, -1, -1))
    prev = None
    for y, (strategy, evidence, s, kind) in zip(ys, rows):
        color = S.ORANGE if kind == "backtest" else AQUA
        _dot_ci(ax, s["ev"], y, s["ev_lo"], s["ev_hi"], color, horizontal=True, size=8.5)
        if strategy != prev:
            ax.annotate(strategy, xy=(0.012, y), xycoords=("figure fraction", "data"),
                        ha="left", va="center", fontsize=9.5, color=S.INK,
                        fontweight="semibold")
        ax.annotate(f"{evidence} · n = {s['n']:,}", xy=(0.19, y),
                    xycoords=("figure fraction", "data"), ha="left", va="center",
                    fontsize=8.8, color=S.INK_2)
        prev = strategy

    ax.axvline(0, color=S.INK_2, linewidth=0.9, zorder=2)
    lo = min(s["ev_lo"] for _, _, s, _ in rows)
    hi = max(s["ev_hi"] for _, _, s, _ in rows)
    ax.set_xlim(math.floor(lo * 20 - 0.2) / 20, math.ceil(hi * 20 + 0.2) / 20)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:+.2f}" if abs(v) > 1e-9 else "0")
    S.style_axes(ax, xlabel="net P&L per contract, $ (95% CI)", grid="x")
    fig.legend(handles=_legend_handles([("historical backtest", S.ORANGE),
                                        ("live paper trading", AQUA)]),
               loc="upper left", bbox_to_anchor=(0.012, 1 - 0.62 / fig.get_figheight()),
               ncol=2, handlelength=1.6, columnspacing=1.8, borderaxespad=0)
    S.headline(fig, "No strategy clears zero after fees")
    fig.subplots_adjust(left=0.50, right=0.965,
                        top=1 - 1.0 / fig.get_figheight(), bottom=0.62 / fig.get_figheight())
    return fig


# ---------------------------------------------------------------------------

FIGURES = {
    "fair_value_before_after": monthly_before_after,
    "lookahead_timeline":      lookahead_timeline,
    "sensitivity":             sensitivity,
    "calibration":             calibration,
    "basis":                   basis,
    "live_vs_backtest":        live_vs_backtest,
    "strategy_overview":       strategy_overview,
}
