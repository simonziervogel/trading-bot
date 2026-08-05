"""Kalshi Paper Trading Runner — CLI entry point.

All strategy and engine logic lives in the kalshi/ package.
This file is a thin wrapper: parse args, construct objects, run.

Strategies (--strategy flag):
  mean_reversion    — buy YES/NO after a reversal from an extreme
  favorite_longshot — buy NO on contracts priced >88c YES, hold to expiry

Usage:
    python runner.py [--strategy favorite_longshot] [--duration-minutes 60] ...
"""
import os
import sys
import argparse

ROOT = os.path.dirname(__file__)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from kalshi.strategies.longshot import FavoriteLongshotStrategy
from kalshi.strategies.mean_reversion import MeanReversionStrategy
from kalshi.live.engine import PaperTradingEngine, parse_trading_window


DEFAULT_INITIAL_CASH     = 10_000.0
DEFAULT_SCAN_INTERVAL    = 2
DEFAULT_COOLDOWN_SEC     = 30


def main():
    p = argparse.ArgumentParser(
        description="Kalshi paper trading runner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--strategy", default=os.getenv("STRATEGY", "mean_reversion"),
                   choices=["mean_reversion", "favorite_longshot"])
    p.add_argument("--duration-minutes", type=int,
                   default=int(os.getenv("DURATION_MINUTES", "60")))
    p.add_argument("--initial-cash", type=float,
                   default=float(os.getenv("INITIAL_CASH", str(DEFAULT_INITIAL_CASH))))
    p.add_argument("--scan-interval", type=int,
                   default=int(os.getenv("SCAN_INTERVAL_SEC", str(DEFAULT_SCAN_INTERVAL))))
    p.add_argument("--log-dir", default=os.getenv("LOG_DIR", "logs"))
    p.add_argument("--trading-window", default=os.getenv("TRADING_WINDOW", ""),
                   metavar="HH:MM-HH:MM",
                   help="UTC window for signal scanning, e.g. 13:30-15:30 (empty=always)")

    # mean_reversion params
    p.add_argument("--take-profit-cents", type=float,
                   default=float(os.getenv("TAKE_PROFIT_CENTS", "1.5")))
    p.add_argument("--stop-loss-cents", type=float,
                   default=float(os.getenv("STOP_LOSS_CENTS", "2.5")))
    p.add_argument("--time-stop-minutes", type=int,
                   default=int(os.getenv("TIME_STOP_MINUTES", "10")))
    p.add_argument("--momentum-pct", type=float,
                   default=float(os.getenv("MOMENTUM_PCT", "0.01")))
    p.add_argument("--reversal-pct", type=float,
                   default=float(os.getenv("REVERSAL_PCT", "0.003")))
    p.add_argument("--history-minutes", type=int,
                   default=int(os.getenv("HISTORY_MINUTES", "5")))
    p.add_argument("--max-positions", type=int,
                   default=int(os.getenv("MAX_POSITIONS", "8")))

    # favorite_longshot params
    p.add_argument("--longshot-threshold", type=float,
                   default=float(os.getenv("LONGSHOT_THRESHOLD", "0.85")))
    p.add_argument("--longshot-time-stop", type=int,
                   default=int(os.getenv("LONGSHOT_TIME_STOP", "14")))
    p.add_argument("--min-tte-minutes", type=int,
                   default=int(os.getenv("MIN_TTE_MINUTES", "5")))
    p.add_argument("--max-tte-minutes", type=int,
                   default=int(os.getenv("MAX_TTE_MINUTES", "13")))
    p.add_argument("--max-spread-cents", type=float,
                   default=float(os.getenv("MAX_SPREAD_CENTS", "8.0")))
    p.add_argument("--no-momentum-filter", action="store_true", default=False,
                   help="Disable Binance momentum filter")
    p.add_argument("--momentum-window", type=int,
                   default=int(os.getenv("MOMENTUM_WINDOW", "2")))
    p.add_argument("--momentum-threshold", type=float,
                   default=float(os.getenv("MOMENTUM_THRESHOLD", "0.3")))

    args = p.parse_args()

    trading_window = None
    if args.trading_window:
        trading_window = parse_trading_window(args.trading_window)

    if args.strategy == "mean_reversion":
        strategy = MeanReversionStrategy(
            take_profit_cents = args.take_profit_cents,
            stop_loss_cents   = args.stop_loss_cents,
            time_stop_minutes = args.time_stop_minutes,
            momentum_pct      = args.momentum_pct,
            reversal_pct      = args.reversal_pct,
            history_minutes   = args.history_minutes,
            max_positions     = args.max_positions,
        )
    else:
        strategy = FavoriteLongshotStrategy(
            longshot_threshold      = args.longshot_threshold,
            time_stop_minutes       = args.longshot_time_stop,
            max_positions           = args.max_positions,
            min_tte_minutes         = args.min_tte_minutes,
            max_tte_minutes         = args.max_tte_minutes,
            max_spread_cents        = args.max_spread_cents,
            momentum_filter         = not args.no_momentum_filter,
            momentum_window_minutes = args.momentum_window,
            momentum_threshold_pct  = args.momentum_threshold,
        )

    engine = PaperTradingEngine(
        strategy          = strategy,
        initial_cash      = args.initial_cash,
        scan_interval_sec = args.scan_interval,
        log_dir           = args.log_dir,
        trading_window    = trading_window,
    )
    engine.run(duration_minutes=args.duration_minutes)


if __name__ == "__main__":
    main()
