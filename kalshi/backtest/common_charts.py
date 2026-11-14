"""Side-aware equity curve chart — shared by the fair-value and momentum backtests.

Sibling of kalshi.backtest.charts (which is Longshot/NO-only); see
kalshi.backtest.common_metrics for why this is a separate, additive module.
Styling comes from kalshi.report.style, shared with every other figure.
"""


def _val(o, key: str):
    return o[key] if isinstance(o, dict) else getattr(o, key)


def equity_curve(obs: list, title: str = "Equity Curve"):
    """Cumulative PnL over entry order.

    A contract costs entry_price and settles at $1 (win) or $0 (loss), so each
    trade contributes +(1 - entry_price) on a win or -entry_price on a loss.
    Returns a matplotlib Figure.
    """
    import matplotlib.pyplot as plt
    from kalshi.report import style as S
    S.apply()

    if not obs:
        fig, ax = plt.subplots()
        ax.set_title(title)
        ax.text(0.5, 0.5, "No observations", ha="center", va="center",
                transform=ax.transAxes)
        return fig

    sorted_obs = sorted(obs, key=lambda o: _val(o, "entry_time_utc"))
    equity     = 0.0
    ys         = []
    for o in sorted_obs:
        price = _val(o, "entry_price")
        won   = _val(o, "side_won")
        equity += (1.0 - price) if won else -price
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


def save_equity_curve(obs: list, path: str, title: str = "Equity Curve") -> None:
    import matplotlib.pyplot as plt
    from pathlib import Path

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig = equity_curve(obs, title=title)
    fig.savefig(out, dpi=200)
    plt.close(fig)
    print(f"  Chart saved -> {out}")
