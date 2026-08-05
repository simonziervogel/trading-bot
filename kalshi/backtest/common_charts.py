"""Side-aware equity curve chart — shared by the fair-value and momentum backtests.

Sibling of kalshi.backtest.charts (which is Longshot/NO-only); see
kalshi.backtest.common_metrics for why this is a separate, additive module.
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


def save_equity_curve(obs: list, path: str, title: str = "Equity Curve") -> None:
    import matplotlib.pyplot as plt
    from pathlib import Path

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig = equity_curve(obs, title=title)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Chart saved -> {out}")
