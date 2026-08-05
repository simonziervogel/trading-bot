"""Market Analysis Script
Samples live Kalshi market data to measure:
- Spreads (bid/ask in cents)
- Price volatility per minute
- Momentum signal frequency at various thresholds
- Liquidity (orderbook depth)

Run for 5+ minutes to get meaningful statistics.
Usage: python market_analysis.py [--duration 5] [--interval 5]
"""
import time
import json
import argparse
import sys
import os
from datetime import datetime, timedelta
from collections import defaultdict
from statistics import mean, stdev, median

ROOT = os.path.dirname(__file__)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from kalshi.data.live import LiveMarketDataFetcher

SERIES = ["KXBTC15M", "KXETH15M", "KXUSD15M"]

# Thresholds to test for signal frequency
MOMENTUM_THRESHOLDS = [0.005, 0.01, 0.02, 0.03, 0.05]  # 0.5%, 1%, 2%, 3%, 5%
MOMENTUM_WINDOW_MINUTES = 5


def parse_market_quote(market: dict) -> tuple:
    """
    Extract best bid/ask from market listing snapshot.

    Kalshi market objects include top-of-book prices directly:
      yes_bid_dollars: best YES bid (highest price a buyer will pay)
      yes_ask_dollars: best YES ask (lowest price a seller will accept)

    The 1-cent spread means: yes_ask = yes_bid + 0.01
    yes + no prices sum to ~1.01 (the 1-cent exchange spread).

    Returns: (yes_bid, yes_ask, spread_cents, no_bid, no_ask)
    """
    try:
        yes_bid = market.get("yes_bid_dollars")
        yes_ask = market.get("yes_ask_dollars")
        no_bid = market.get("no_bid_dollars")
        no_ask = market.get("no_ask_dollars")

        yes_bid = float(yes_bid) if yes_bid else None
        yes_ask = float(yes_ask) if yes_ask else None
        no_bid = float(no_bid) if no_bid else None
        no_ask = float(no_ask) if no_ask else None

        if yes_bid is not None and yes_ask is not None and yes_ask > yes_bid:
            spread_cents = round((yes_ask - yes_bid) * 100, 2)
        else:
            spread_cents = None

        return yes_bid, yes_ask, spread_cents, no_bid, no_ask
    except (ValueError, TypeError):
        return None, None, None, None, None


class MarketAnalyzer:
    def __init__(self, duration_minutes: int = 5, sample_interval_seconds: int = 5):
        self.duration_minutes = duration_minutes
        self.sample_interval = sample_interval_seconds
        self.fetcher = LiveMarketDataFetcher()

        # {ticker: [(ts, yes_bid, yes_ask, spread_cents, mid), ...]}
        self.samples: dict[str, list] = defaultdict(list)
        # {ticker: series}
        self.ticker_series: dict[str, str] = {}

    def run(self):
        end_time = datetime.now() + timedelta(minutes=self.duration_minutes)
        total_samples = 0
        iteration = 0

        print(f"\n{'='*60}")
        print(f"  KALSHI MARKET ANALYSIS")
        print(f"  Duration: {self.duration_minutes} min | Interval: {self.sample_interval}s")
        print(f"  Series: {', '.join(SERIES)}")
        print(f"{'='*60}\n")

        while datetime.now() < end_time:
            iteration += 1
            remaining = (end_time - datetime.now()).seconds
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Iteration {iteration} | {remaining}s remaining", end="")

            iter_samples = 0
            for series in SERIES:
                markets = self.fetcher.fetch_live_markets(series, use_cache=False)
                for market in markets:
                    ticker = market.get("ticker")
                    if not ticker:
                        continue

                    self.ticker_series[ticker] = series

                    yes_bid, yes_ask, spread_cents, no_bid, no_ask = parse_market_quote(market)

                    if yes_bid is None or yes_ask is None or spread_cents is None:
                        continue
                    # Skip illiquid markets (spread > 10 cents = no real activity)
                    if spread_cents > 10:
                        continue

                    mid = round((yes_bid + yes_ask) / 2, 4)
                    ts = datetime.now()
                    self.samples[ticker].append((ts, yes_bid, yes_ask, spread_cents, mid))
                    iter_samples += 1

            total_samples += iter_samples
            print(f" | {iter_samples} quotes collected")
            time.sleep(self.sample_interval)

        print(f"\nCollection complete. {total_samples} total samples across {len(self.samples)} tickers.\n")

    def analyze(self) -> dict:
        """Compute statistics from collected samples."""
        results = {
            "meta": {
                "duration_minutes": self.duration_minutes,
                "sample_interval_seconds": self.sample_interval,
                "total_tickers": len(self.samples),
                "timestamp": datetime.now().isoformat(),
            },
            "by_series": {},
            "signal_frequency": {},
        }

        # Group tickers by series
        series_tickers = defaultdict(list)
        for ticker, series in self.ticker_series.items():
            if ticker in self.samples and len(self.samples[ticker]) >= 2:
                series_tickers[series].append(ticker)

        # Per-series stats
        for series, tickers in series_tickers.items():
            spreads = []
            yes_no_sums = []
            mid_prices = []
            price_moves_per_min = []  # absolute price change per minute
            price_ranges = []  # max - min over observation window

            for ticker in tickers:
                s = self.samples[ticker]
                ticker_spreads = [x[3] for x in s if x[3] is not None]
                ticker_mids = [x[4] for x in s]
                ticker_bids = [x[1] for x in s]
                ticker_asks = [x[2] for x in s]
                _ = ticker_bids, ticker_asks  # used below for yes+no sum

                spreads.extend(ticker_spreads)
                mid_prices.extend(ticker_mids)


                # Price movement: compare first and last sample
                if len(ticker_mids) >= 2:
                    duration_min = (s[-1][0] - s[0][0]).total_seconds() / 60
                    if duration_min > 0:
                        abs_move = abs(ticker_mids[-1] - ticker_mids[0])
                        move_per_min = abs_move / duration_min
                        price_moves_per_min.append(move_per_min)

                    price_range = max(ticker_mids) - min(ticker_mids)
                    price_ranges.append(price_range)

            def safe_stats(values):
                if not values:
                    return {"min": None, "max": None, "mean": None, "median": None, "p25": None, "p75": None}
                s = sorted(values)
                n = len(s)
                return {
                    "min": round(min(s), 4),
                    "max": round(max(s), 4),
                    "mean": round(mean(s), 4),
                    "median": round(median(s), 4),
                    "p25": round(s[n // 4], 4),
                    "p75": round(s[3 * n // 4], 4),
                    "n": n,
                }

            results["by_series"][series] = {
                "tickers_observed": len(tickers),
                "spread_cents": safe_stats(spreads),
                "mid_price": safe_stats(mid_prices),
                "abs_price_move_per_min_dollars": safe_stats(price_moves_per_min),
                "price_range_over_window_dollars": safe_stats(price_ranges),
                "price_range_over_window_cents": safe_stats([r * 100 for r in price_ranges]),
            }

        # Signal frequency analysis
        # For each ticker, check how often momentum threshold is met in any 5-min window
        signal_counts = {t: 0 for t in MOMENTUM_THRESHOLDS}
        total_checks = 0

        for ticker, s in self.samples.items():
            if len(s) < 2:
                continue

            mids = [(x[0], x[4]) for x in s]  # (ts, mid)

            # Slide a 5-min window over the samples
            for i in range(len(mids)):
                ts_i, price_i = mids[i]
                window_start = ts_i - timedelta(minutes=MOMENTUM_WINDOW_MINUTES)
                recent = [(ts, p) for ts, p in mids if ts >= window_start and ts <= ts_i]

                if len(recent) < 2:
                    continue

                oldest_price = recent[0][1]
                if oldest_price <= 0:
                    continue

                pct_change = abs((price_i - oldest_price) / oldest_price)
                total_checks += 1

                for threshold in MOMENTUM_THRESHOLDS:
                    if pct_change >= threshold:
                        signal_counts[threshold] += 1

        results["signal_frequency"] = {
            "total_price_checks": total_checks,
            "by_threshold": {
                f"{int(t*100)}pct": {
                    "triggers": signal_counts[t],
                    "trigger_rate_pct": round(100 * signal_counts[t] / total_checks, 2) if total_checks > 0 else 0,
                }
                for t in MOMENTUM_THRESHOLDS
            },
            "note": f"5-min rolling window, {len(self.samples)} tickers",
        }

        return results

    def print_report(self, results: dict):
        print(f"\n{'='*60}")
        print("  ANALYSIS REPORT")
        print(f"{'='*60}\n")

        for series, stats in results["by_series"].items():
            print(f"--- {series} ---")
            print(f"  Tickers observed:    {stats['tickers_observed']}")

            sp = stats["spread_cents"]
            print(f"  Spread (cents):      min={sp['min']}  mean={sp['mean']}  median={sp['median']}  max={sp['max']}")

            mp = stats["mid_price"]
            print(f"  Mid price:           min={mp['min']}  mean={mp['mean']}  max={mp['max']}")

            mv = stats["abs_price_move_per_min_dollars"]
            if mv["mean"] is not None:
                print(f"  Move/min (cents):    mean={round(mv['mean']*100,3)}  median={round(mv['median']*100,3)}  max={round(mv['max']*100,3)}")

            rng = stats["price_range_over_window_cents"]
            if rng["mean"] is not None:
                print(f"  Range over {self.duration_minutes}min (cents): mean={rng['mean']}  median={rng['median']}  max={rng['max']}")
            print()

        print("--- Signal Frequency (5-min momentum window) ---")
        sf = results["signal_frequency"]
        print(f"  Total price checks:  {sf['total_price_checks']}")
        for label, data in sf["by_threshold"].items():
            bar = "#" * min(50, int(data["trigger_rate_pct"] * 2))
            print(f"  {label:6s}: {data['triggers']:5d} triggers  ({data['trigger_rate_pct']:5.1f}%)  {bar}")

        print(f"\n{'='*60}")
        print("  CALIBRATION SUGGESTIONS")
        print(f"{'='*60}")

        # Derive calibration hints
        for series, stats in results["by_series"].items():
            sp = stats["spread_cents"]
            rng = stats["price_range_over_window_cents"]
            mv = stats["abs_price_move_per_min_dollars"]

            if sp["median"] is None or rng["median"] is None:
                continue

            spread_med = sp["median"]
            range_med = rng["median"]
            move_per_min = mv["mean"] * 100 if mv["mean"] else 0

            min_viable_tp = spread_med + 0.5  # must beat spread + margin
            print(f"\n  {series}:")
            print(f"    Spread cost per round-trip:  ~{spread_med:.1f}c")
            print(f"    Typical {self.duration_minutes}-min price range:    {range_med:.1f}c")
            print(f"    Mean price move per minute:  {move_per_min:.2f}c/min")
            print(f"    >> Min TP to beat spread:     >{min_viable_tp:.1f}c")
            suggested_tp = max(min_viable_tp, range_med * 0.3)
            suggested_sl = suggested_tp * 1.5
            print(f"    >> Suggested TP target:       ~{suggested_tp:.1f}c")
            print(f"    >> Suggested SL (1.5x risk):  ~{suggested_sl:.1f}c")

        print()


def main():
    parser = argparse.ArgumentParser(description="Kalshi Market Analysis")
    parser.add_argument("--duration", type=int, default=5, help="Sampling duration in minutes (default: 5)")
    parser.add_argument("--interval", type=int, default=5, help="Sample interval in seconds (default: 5)")
    parser.add_argument("--output", type=str, default=None, help="Save JSON results to file")
    args = parser.parse_args()

    analyzer = MarketAnalyzer(
        duration_minutes=args.duration,
        sample_interval_seconds=args.interval,
    )

    try:
        analyzer.run()
    except KeyboardInterrupt:
        print("\n\nInterrupted. Analyzing collected data...\n")

    results = analyzer.analyze()
    analyzer.print_report(results)

    # Save JSON
    output_path = args.output or f"logs/market_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  Results saved to: {output_path}\n")


if __name__ == "__main__":
    main()
