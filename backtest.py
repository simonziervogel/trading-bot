"""backtest.py — CLI entry point for the Kalshi backtest engine.

Scans historical Kalshi markets for the favorite-longshot bias and exports
observations as CSV with optional matplotlib charts.

Usage:
    python backtest.py                                # defaults: 500 markets, both series
    python backtest.py --max-markets 100              # quick smoke test
    python backtest.py --series KXBTC15M              # single series
    python backtest.py --test-after 2026-04-01        # train/test split
    python backtest.py --csv results/longshot.csv     # custom output path
    python backtest.py --plot results/charts          # save PNG charts
    python backtest.py --threshold 0.85               # tighter signal filter
    python backtest.py --verbose                      # per-market progress
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from kalshi.backtest.engine import BacktestConfig, BacktestEngine
from kalshi.backtest.metrics import print_bucket_table, print_segment_table


def main():
    p = argparse.ArgumentParser(
        description="Kalshi longshot-bias backtest engine",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Series / scope
    p.add_argument("--series", nargs="+", default=["KXBTC15M", "KXETH15M"],
                   metavar="SERIES", help="Kalshi series tickers to scan")
    p.add_argument("--max-markets", type=int, default=500,
                   help="Maximum markets per series (most recent N)")
    p.add_argument("--from", dest="date_from", type=str, default=None,
                   metavar="YYYY-MM-DD", help="Earliest market close date (inclusive)")
    p.add_argument("--to", dest="date_to", type=str, default=None,
                   metavar="YYYY-MM-DD", help="Latest market close date (inclusive)")
    p.add_argument("--test-after", type=str, default=None,
                   metavar="YYYY-MM-DD",
                   help="Markets from this date onward go to the 'test' split")

    # Signal filters
    p.add_argument("--threshold", type=float, default=0.70,
                   help="Minimum YES mid-price to trigger signal scan")
    p.add_argument("--min-tte", type=float, default=5.0,
                   help="Minimum time-to-expiry in minutes")
    p.add_argument("--max-tte", type=float, default=13.0,
                   help="Maximum time-to-expiry in minutes")
    p.add_argument("--min-volume", type=float, default=1000.0,
                   help="Minimum volume_fp (liquidity gate)")

    # Output
    p.add_argument("--csv", type=str, default="results/backtest_observations.csv",
                   help="Output CSV path")
    p.add_argument("--plot", type=str, default=None,
                   metavar="BASE_PATH",
                   help="Save charts as <BASE_PATH>_equity.png etc. (requires matplotlib)")
    p.add_argument("--verbose", action="store_true",
                   help="Print per-market progress lines")

    args = p.parse_args()

    def _parse_date(s, flag):
        try:
            return datetime.fromisoformat(s).date()
        except ValueError:
            p.error(f"Invalid date for {flag}: '{s}' — expected YYYY-MM-DD")

    date_from  = _parse_date(args.date_from, "--from")   if args.date_from  else None
    date_to    = _parse_date(args.date_to,   "--to")     if args.date_to    else None
    test_after = _parse_date(args.test_after, "--test-after") if args.test_after else None

    config = BacktestConfig(
        series         = args.series,
        date_from      = date_from,
        date_to        = date_to,
        test_after     = test_after,
        scan_threshold = args.threshold,
        min_tte        = args.min_tte,
        max_tte        = args.max_tte,
        min_volume     = args.min_volume,
        max_markets    = args.max_markets,
    )

    print(f"backtest.py  |  series={config.series}  max={config.max_markets}  "
          f"threshold>={config.scan_threshold}  TTE=[{config.min_tte},{config.max_tte}]")
    if test_after:
        print(f"             |  train/test split at {args.test_after}")

    engine  = BacktestEngine(verbose=args.verbose)
    results = engine.run(config)

    # CSV export
    results.export_csv(args.csv)

    # Console analysis
    all_obs = results.observations
    if test_after:
        train = results.train
        test  = results.test
        print_bucket_table(train, title=f"TRAIN  (before {args.test_after})")
        print_segment_table(train)
        print()
        print_bucket_table(test,  title=f"TEST   (from   {args.test_after})")
        print_segment_table(test)
        print()
        print_bucket_table(all_obs, title="COMBINED")
        print_segment_table(all_obs)
    else:
        print_bucket_table(all_obs, title="ALL MARKETS")
        print_segment_table(all_obs)

    # Optional charts
    if args.plot:
        try:
            from kalshi.backtest.charts import save_all
            if test_after:
                save_all(results.train, args.plot + "_train", split_label="TRAIN")
                save_all(results.test,  args.plot + "_test",  split_label="TEST")
            save_all(all_obs, args.plot, split_label="ALL")
        except ImportError:
            print("\n  [WARN] matplotlib not installed — skipping charts")

    print("\nDone.")


if __name__ == "__main__":
    main()
