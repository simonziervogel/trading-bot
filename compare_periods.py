"""compare_periods.py — compare all backtestable strategies across calendar months.

For each strategy (Favorite-Longshot, Momentum, Fair Value) and each calendar
month in the requested range, runs that strategy's existing backtest engine
scoped to the month and records one summary row (N, win rate, EV/contract,
Sharpe, z-score). Writes results/period_comparison.csv and prints a table —
the "favorite_longshot March 2026 / April 2026 / ..." view.

MeanReversionStrategy is excluded — it's TP/SL-based and validated live only
(see README "Strategies" section), not part of the historical-backtest
pipeline the other three share.

Usage:
    python compare_periods.py                                # last 4 full months
    python compare_periods.py --months 2026-02 2026-03        # specific months
    python compare_periods.py --max-markets-per-month 300     # slower/finer sample
"""
import argparse
import calendar
import csv
from datetime import date
from pathlib import Path

from kalshi.backtest.engine import BacktestConfig, BacktestEngine
from kalshi.backtest.metrics import (
    win_rate_with_ci as no_win_rate_with_ci,
    ev_per_contract as no_ev_per_contract,
    z_vs_implied as no_z_vs_implied,
    sharpe_ratio as no_sharpe_ratio,
)
from kalshi.backtest.momentum_engine import MomentumBacktestConfig, MomentumBacktestEngine
from kalshi.backtest.fair_value_engine import FairValueBacktestConfig, FairValueBacktestEngine
from kalshi.backtest.common_metrics import (
    win_rate_with_ci as side_win_rate_with_ci,
    ev_per_contract as side_ev_per_contract,
    z_vs_implied as side_z_vs_implied,
    sharpe_ratio as side_sharpe_ratio,
)

DEFAULT_SERIES     = ["KXBTC15M", "KXETH15M"]
LATEST_AVAILABLE   = date(2026, 6, 6)   # last date with archived Kalshi data as of this run


def _month_bounds(year_month: str):
    year, month = map(int, year_month.split("-"))
    first    = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    return first, date(year, month, last_day)


def _default_months(n: int = 4, end_date: date = LATEST_AVAILABLE) -> list:
    """Last n fully-available calendar months before end_date's (possibly partial) month."""
    months = []
    y, m = end_date.year, end_date.month
    for _ in range(n):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        months.append(f"{y:04d}-{m:02d}")
    return list(reversed(months))


def run_longshot_month(date_from, date_to, max_markets):
    cfg = BacktestConfig(series=DEFAULT_SERIES, date_from=date_from, date_to=date_to,
                         max_markets=max_markets)
    return BacktestEngine(verbose=False).run(cfg).observations


def run_momentum_month(date_from, date_to, max_markets):
    cfg = MomentumBacktestConfig(series=DEFAULT_SERIES, date_from=date_from, date_to=date_to,
                                 max_markets=max_markets)
    return MomentumBacktestEngine(verbose=False).run(cfg).observations


def run_fair_value_month(date_from, date_to, max_markets):
    cfg = FairValueBacktestConfig(series=DEFAULT_SERIES, date_from=date_from, date_to=date_to,
                                  max_markets=max_markets)
    return FairValueBacktestEngine(verbose=False).run(cfg).observations


STRATEGIES = [
    ("favorite_longshot", run_longshot_month,
     no_win_rate_with_ci, no_ev_per_contract, no_z_vs_implied, no_sharpe_ratio),
    ("momentum", run_momentum_month,
     side_win_rate_with_ci, side_ev_per_contract, side_z_vs_implied, side_sharpe_ratio),
    ("fair_value", run_fair_value_month,
     side_win_rate_with_ci, side_ev_per_contract, side_z_vs_implied, side_sharpe_ratio),
]


def _summarize(strategy_name, period, obs, win_rate_fn, ev_fn, z_fn, sharpe_fn) -> dict:
    n = len(obs)
    if n == 0:
        return {"strategy": strategy_name, "period": period, "n": 0,
                "win_rate": None, "ev_per_contract": None, "z_score": None,
                "sharpe_per_trade": None}
    wr, _, _ = win_rate_fn(obs)
    sharpe    = sharpe_fn(obs)
    return {
        "strategy": strategy_name, "period": period, "n": n,
        "win_rate": round(wr, 4), "ev_per_contract": round(ev_fn(obs), 4),
        "z_score": round(z_fn(obs), 2),
        "sharpe_per_trade": round(sharpe["per_trade"], 3) if sharpe["per_trade"] is not None else None,
    }


def main():
    p = argparse.ArgumentParser(description="Compare strategies across calendar months")
    p.add_argument("--months", nargs="+", default=None, metavar="YYYY-MM",
                   help="Calendar months to include (default: last 4 fully-available months)")
    p.add_argument("--max-markets-per-month", type=int, default=150,
                   help="Per-series market cap per month (bounds runtime)")
    p.add_argument("--csv", type=str, default="results/period_comparison.csv")
    args = p.parse_args()

    months = args.months or _default_months()
    rows = []

    for month in months:
        date_from, date_to = _month_bounds(month)
        print(f"\n=== {month}  ({date_from} - {date_to}) ===")
        for name, run_fn, wr_fn, ev_fn, z_fn, sharpe_fn in STRATEGIES:
            print(f"  {name:<18}", end=" ", flush=True)
            obs = run_fn(date_from, date_to, args.max_markets_per_month)
            row = _summarize(name, month, obs, wr_fn, ev_fn, z_fn, sharpe_fn)
            rows.append(row)
            print(f"N={row['n']:<5} WR={row['win_rate']}  EV/c={row['ev_per_contract']}  "
                  f"z={row['z_score']}  Sharpe={row['sharpe_per_trade']}")

    out = Path(args.csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved -> {out}  ({len(rows)} rows)")


if __name__ == "__main__":
    main()
