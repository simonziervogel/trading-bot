"""Side-aware backtest metrics — win rates, expected value, z-scores, drawdown.

Generalization of kalshi.backtest.metrics for strategies that can trade either
side (YES or NO) per observation, e.g. FairValueStrategy and MomentumStrategy.
kalshi.backtest.metrics stays untouched (Longshot always trades NO) so the
existing, published backtest pipeline has zero regression risk.

Observations here are dicts or objects exposing: side ("yes"|"no"),
entry_price (float, dollars), side_won (bool).
"""

import math
from datetime import datetime
from typing import Union

Obs = Union[object, dict]


def _val(o: Obs, key: str):
    return o[key] if isinstance(o, dict) else getattr(o, key)


def wilson_ci(n: int, k: int, z: float = 1.96) -> tuple:
    """Wilson score 95% confidence interval for a binomial proportion."""
    if n == 0:
        return 0.0, 0.0
    p      = k / n
    denom  = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def win_rate_with_ci(obs: list) -> tuple:
    """Return (win_rate, ci_lo, ci_hi) — a "win" is side_won == True."""
    n = len(obs)
    if n == 0:
        return 0.0, 0.0, 0.0
    k = sum(1 for o in obs if _val(o, "side_won"))
    wr = k / n
    ci_lo, ci_hi = wilson_ci(n, k)
    return wr, ci_lo, ci_hi


def _taker_fee(price: float) -> float:
    return 0.07 * price * (1.0 - price)


def ev_per_contract(obs: list) -> float:
    """Expected value per contract in dollars: win_rate - avg_entry_price - fee."""
    n = len(obs)
    if n == 0:
        return 0.0
    k        = sum(1 for o in obs if _val(o, "side_won"))
    wr       = k / n
    avg_price = sum(_val(o, "entry_price") for o in obs) / n
    edge     = wr - avg_price
    fee      = _taker_fee(avg_price)
    return edge - fee


def z_vs_implied(obs: list) -> float:
    """Z-score of observed win rate vs. avg entry price as implied probability."""
    n = len(obs)
    if n == 0:
        return 0.0
    k       = sum(1 for o in obs if _val(o, "side_won"))
    wr      = k / n
    avg_price = sum(_val(o, "entry_price") for o in obs) / n
    se      = math.sqrt(avg_price * (1 - avg_price) / n)
    if se <= 0:
        return 0.0
    return (wr - avg_price) / se


def max_drawdown(obs: list) -> float:
    """Max drawdown of the equity curve.

    A contract costs entry_price and settles at $1 (win) or $0 (loss), so each
    observation contributes +(1 - entry_price) on a win or -entry_price on a loss.
    """
    if not obs:
        return 0.0
    equity = 0.0
    peak   = 0.0
    max_dd = 0.0
    for o in obs:
        price = _val(o, "entry_price")
        won   = _val(o, "side_won")
        equity += (1.0 - price) if won else -price
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


def sharpe_ratio(obs: list) -> dict:
    """Per-trade Sharpe ratio of net PnL after fees.

    Each observation's net PnL: win pays (1 - entry_price - entry_fee), loss
    costs -(entry_price + entry_fee) — settlement is fee-free.

    Returns a dict:
      per_trade       - mean/stdev of net PnL per trade (None if <2 obs or zero variance)
      trades_per_year - observed trade frequency, from the entry_time_utc span
      annualized      - per_trade * sqrt(trades_per_year) (None if either input is None)

    This is an *observed-frequency* annualization, not a fixed daily-return
    Sharpe — see kalshi.backtest.metrics.sharpe_ratio for the same caveat.
    """
    n = len(obs)
    if n < 2:
        return {"per_trade": None, "trades_per_year": None, "annualized": None}

    pnls = []
    for o in obs:
        price = _val(o, "entry_price")
        won   = _val(o, "side_won")
        fee   = _taker_fee(price)
        pnls.append((1.0 - price - fee) if won else -(price + fee))

    mean     = sum(pnls) / n
    variance = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    stdev    = math.sqrt(variance)
    if stdev < 1e-9:
        return {"per_trade": None, "trades_per_year": None, "annualized": None}

    per_trade = mean / stdev

    times = sorted(_val(o, "entry_time_utc") for o in obs)
    try:
        span_days = (
            datetime.fromisoformat(times[-1]) - datetime.fromisoformat(times[0])
        ).total_seconds() / 86400.0
    except Exception:
        span_days = 0.0

    if span_days <= 0:
        return {"per_trade": per_trade, "trades_per_year": None, "annualized": None}

    trades_per_year = n / (span_days / 365.25)
    annualized      = per_trade * math.sqrt(trades_per_year)
    return {"per_trade": per_trade, "trades_per_year": trades_per_year, "annualized": annualized}


def print_summary_table(obs: list, title: str = "") -> None:
    """Print overall + per-side stats to stdout."""
    if not obs:
        print(f"  {title}: no data")
        return

    n_total = len(obs)
    if title:
        print(f"\n{'='*80}")
        print(f"  {title}  --  {n_total} observations")
        print(f"{'='*80}")

    print(f"  {'Group':<12} {'N':>6}  {'Win Rate':>9}  "
          f"{'95% CI':>17}  {'Avg Price':>9}  {'EV/c':>8}  {'z':>7}")
    print(f"  {'-'*72}")

    def _row(label, group):
        n = len(group)
        if n == 0:
            print(f"  {label:<12} {'--':>6}")
            return
        wr, ci_lo, ci_hi = win_rate_with_ci(group)
        avg_price = sum(_val(o, "entry_price") for o in group) / n
        ev = ev_per_contract(group)
        z  = z_vs_implied(group)
        print(
            f"  {label:<12} {n:>6}  {wr:>8.1%}  "
            f"[{ci_lo:>5.1%}-{ci_hi:>5.1%}]  {avg_price:>9.3f}  {ev:>+7.4f}  {z:>+6.2f}"
        )

    _row("ALL", obs)
    _row("side=yes", [o for o in obs if _val(o, "side") == "yes"])
    _row("side=no",  [o for o in obs if _val(o, "side") == "no"])

    sharpe = sharpe_ratio(obs)
    if sharpe["per_trade"] is not None:
        if sharpe["annualized"] is not None:
            print(
                f"  Sharpe (per-trade): {sharpe['per_trade']:+.3f}   "
                f"annualized (observed freq, ~{sharpe['trades_per_year']:.0f}/yr): "
                f"{sharpe['annualized']:+.2f}"
            )
        else:
            print(f"  Sharpe (per-trade): {sharpe['per_trade']:+.3f}   annualized: n/a")
