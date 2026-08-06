"""backtest_momentum.py — CLI entry point for the momentum (trend-continuation) backtest.

Scans each historical market's own candle sequence for a trailing price move
past momentum_threshold_pct and exports observations as CSV with an optional
equity chart.

Usage:
    python backtest_momentum.py                            # defaults: 500 markets, both series
    python backtest_momentum.py --max-markets 100           # quick smoke test
    python backtest_momentum.py --threshold 0.03             # require a bigger move
    python backtest_momentum.py --plot docs/images/momentum  # save equity chart
"""

import argparse
from datetime import datetime

from kalshi.backtest.momentum_engine import MomentumBacktestConfig, MomentumBacktestEngine
from kalshi.backtest.common_metrics import print_summary_table
from kalshi.backtest.common_charts import save_equity_curve


def main():
    p = argparse.ArgumentParser(
        description="Kalshi momentum (trend-continuation) backtest engine",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--series", nargs="+", default=["KXBTC15M", "KXETH15M"])
    p.add_argument("--max-markets", type=int, default=500)
    p.add_argument("--from", dest="date_from", type=str, default=None, metavar="YYYY-MM-DD")
    p.add_argument("--to", dest="date_to", type=str, default=None, metavar="YYYY-MM-DD")
    p.add_argument("--test-after", type=str, default=None, metavar="YYYY-MM-DD",
                   help="Markets from this date onward go to the 'test' split")
    p.add_argument("--min-tte", type=float, default=5.0)
    p.add_argument("--max-tte", type=float, default=13.0)
    p.add_argument("--min-volume", type=float, default=1000.0)
    p.add_argument("--threshold", type=float, default=0.02,
                   help="Minimum relative price change over the trailing window to trigger a signal")
    p.add_argument("--history-minutes", type=float, default=5.0)
    p.add_argument("--min-history-points", type=int, default=3)
    p.add_argument("--csv", type=str, default="results/backtest_momentum.csv")
    p.add_argument("--plot", type=str, default=None, metavar="BASE_PATH")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    def _parse_date(s, flag):
        try:
            return datetime.fromisoformat(s).date()
        except ValueError:
            p.error(f"Invalid date for {flag}: '{s}' — expected YYYY-MM-DD")

    test_after = _parse_date(args.test_after, "--test-after") if args.test_after else None

    config = MomentumBacktestConfig(
        series=args.series,
        date_from=_parse_date(args.date_from, "--from") if args.date_from else None,
        date_to=_parse_date(args.date_to, "--to") if args.date_to else None,
        test_after=test_after,
        min_tte=args.min_tte, max_tte=args.max_tte,
        min_volume=args.min_volume, max_markets=args.max_markets,
        momentum_threshold_pct=args.threshold,
        history_minutes=args.history_minutes,
        min_history_points=args.min_history_points,
    )

    print(f"backtest_momentum.py  |  series={config.series}  max={config.max_markets}  "
          f"threshold={config.momentum_threshold_pct}  TTE=[{config.min_tte},{config.max_tte}]")
    if test_after:
        print(f"                      |  train/test split at {args.test_after}")

    engine  = MomentumBacktestEngine(verbose=args.verbose)
    results = engine.run(config)
    results.export_csv(args.csv)

    all_obs = results.observations
    if test_after:
        train, test = results.train, results.test
        print_summary_table(train,   title=f"TRAIN  (before {args.test_after})")
        print_summary_table(test,    title=f"TEST   (from   {args.test_after})")
        print_summary_table(all_obs, title="COMBINED")
    else:
        print_summary_table(all_obs, title="MOMENTUM — ALL MARKETS")

    if args.plot:
        try:
            if test_after:
                save_equity_curve(results.train, f"{args.plot}_train_equity.png",
                                 title="Momentum — Equity Curve (TRAIN)")
                save_equity_curve(results.test,  f"{args.plot}_test_equity.png",
                                 title="Momentum — Equity Curve (TEST)")
            save_equity_curve(all_obs, f"{args.plot}_equity.png",
                             title="Momentum — Equity Curve")
        except ImportError:
            print("\n  [WARN] matplotlib not installed — skipping chart")

    print("\nDone.")


if __name__ == "__main__":
    main()
