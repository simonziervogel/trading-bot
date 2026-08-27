"""BacktestEngine — scans historical Kalshi markets for the favorite-longshot bias.

Design principles (preserved from backtest_v2.py):
  - No lookahead: pre_entry_vol only uses candles that arrived BEFORE the signal.
  - One observation per market (first qualifying candle).
  - Actual observed entry price for EV calculation, not bucket midpoint.
  - Executable entry price, not midpoint: no_price = 1 - yes_bid, since a NO
    contract is bought against the YES bid, not at the average of bid/ask.
    The signal threshold itself still uses the midpoint (it characterizes
    market consensus, not what you'd pay).
  - Train/test split by market close date (time-stable).
  - Full CSV export compatible with the original backtest_v2.py format.
"""

import csv
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from kalshi.client import KalshiClient
from kalshi.utils.time import to_naive_utc, utc_timestamp, parse_optional_dt
from kalshi.utils.market import parse_quote, bucket_label, time_segment


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    """All parameters that define a single backtest run."""
    series: list = field(default_factory=lambda: ["KXBTC15M", "KXETH15M"])
    date_from: Optional[date] = None        # earliest market close date (inclusive)
    date_to: Optional[date] = None          # latest market close date (inclusive)
    test_after: Optional[date] = None       # markets from this date onward -> test split
    scan_threshold: float = 0.70            # minimum YES mid to trigger signal scan
    min_tte: float = 5.0                    # minimum time-to-expiry in minutes
    max_tte: float = 13.0                   # maximum time-to-expiry in minutes
    min_volume: float = 1000.0              # minimum volume_fp (liquidity gate)
    max_markets: int = 500                  # cap per series (most recent N)
    strategy_name: str = "longshot"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class Observation:
    """One qualified signal observation from a single historical market."""
    ticker: str
    series: str
    market_close_date: str      # "YYYY-MM-DD"
    entry_time_utc: str         # "YYYY-MM-DDTHH:MM:SS"
    yes_mid: float
    no_price: float              # executable NO entry price: 1 - yes_bid (not mid)
    yes_bid: float
    yes_ask: float
    spread_cents: float
    tte_minutes: float
    no_won: bool
    yes_bucket: str
    time_segment: str
    pre_entry_vol: float
    price_source: str
    split: str                  # "train" | "test"

    def to_dict(self) -> dict:
        return {
            "ticker":            self.ticker,
            "series":            self.series,
            "market_close_date": self.market_close_date,
            "entry_time_utc":    self.entry_time_utc,
            "yes_mid":           self.yes_mid,
            "no_price":          self.no_price,
            "yes_bid":           self.yes_bid,
            "yes_ask":           self.yes_ask,
            "spread_cents":      self.spread_cents,
            "tte_minutes":       self.tte_minutes,
            "no_won":            int(self.no_won),
            "yes_bucket":        self.yes_bucket,
            "time_segment":      self.time_segment,
            "pre_entry_vol":     self.pre_entry_vol,
            "price_source":      self.price_source,
            "split":             self.split,
        }


class BacktestResults:
    """Container for a completed backtest run."""

    def __init__(self, config: BacktestConfig, observations: list):
        self.config = config
        self.observations: list[Observation] = observations

    @property
    def train(self) -> list:
        return [o for o in self.observations if o.split == "train"]

    @property
    def test(self) -> list:
        return [o for o in self.observations if o.split == "test"]

    def to_dicts(self) -> list[dict]:
        return [o.to_dict() for o in self.observations]

    def export_csv(self, path) -> None:
        """Write all observations to a CSV file. Column order matches backtest_v2.py."""
        rows = self.to_dicts()
        if not rows:
            print("  No observations to export.")
            return
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"  CSV saved -> {p}  ({len(rows)} rows)")


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

_API_DELAY_SEC = 0.12
_MIN_CANDLES   = 3


class BacktestEngine:
    """Scans historical Kalshi markets and collects signal observations.

    Usage:
        engine = BacktestEngine()
        config = BacktestConfig(series=["KXBTC15M"], max_markets=100)
        results = engine.run(config)
        results.export_csv("out.csv")
    """

    def __init__(self, client: Optional[KalshiClient] = None, verbose: bool = False):
        self.client  = client or KalshiClient()
        self.verbose = verbose

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, config: BacktestConfig) -> BacktestResults:
        """Run the backtest for all series in config and return results."""
        observations = []
        for series in config.series:
            print(f"\n-- {series} ----------------------------------")
            obs = self._process_series(series, config)
            observations.extend(obs)
        print(f"\nTotal observations: {len(observations)}")
        return BacktestResults(config=config, observations=observations)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_markets(self, series: str, config: BacktestConfig) -> list:
        """Fetch all settled+liquid historical markets for a series."""
        all_markets, cursor = [], None
        while True:
            resp   = self.client.get_historical_markets(
                limit=1000, series_ticker=series, cursor=cursor
            )
            batch  = resp.get("markets", [])
            all_markets.extend(batch)
            cursor = resp.get("cursor")
            if not cursor:
                break

        def _close_key(m):
            c = parse_optional_dt(m.get("close_time"))
            return c or datetime.min

        qualified = [
            m for m in all_markets
            if m.get("result") and m["result"] != ""
            and float(m.get("volume_fp") or 0) >= config.min_volume
            and m.get("open_time") and m.get("close_time")
        ]

        # Apply optional date range filters
        if config.date_from or config.date_to:
            filtered = []
            for m in qualified:
                close_dt = parse_optional_dt(m.get("close_time"))
                if close_dt is None:
                    continue
                close_d = close_dt.date() if hasattr(close_dt, "date") else close_dt
                if config.date_from and close_d < config.date_from:
                    continue
                if config.date_to and close_d > config.date_to:
                    continue
                filtered.append(m)
            qualified = filtered

        # Sort oldest-first, then cap to most recent N for reproducibility
        qualified_sorted = sorted(qualified, key=_close_key)
        return qualified_sorted[-config.max_markets:]

    def _process_series(self, series: str, config: BacktestConfig) -> list:
        markets = self._fetch_markets(series, config)
        print(f"  Processing: {len(markets)} markets")

        observations = []
        sig_count    = 0

        for i, market in enumerate(markets):
            ticker = market["ticker"]
            result = market.get("result", "").lower()
            no_won = result == "no"

            try:
                market_start = to_naive_utc(market["open_time"])
                market_end   = to_naive_utc(market["close_time"])
            except Exception:
                continue

            split = self._determine_split(market_end, config)
            obs = self._scan_market(ticker, series, market_start, market_end,
                                    no_won, split, config)
            if obs is not None:
                observations.append(obs)
                sig_count += 1

            if self.verbose or (i + 1) % 50 == 0:
                print(
                    f"  [{i+1:>4}/{len(markets)}] {ticker}  "
                    f"{'SIG' if obs else '   '}  "
                    f"obs={len(observations)}"
                )

        print(f"  Signals: {sig_count} / {len(markets)}")
        return observations

    def _determine_split(self, market_end: datetime, config: BacktestConfig) -> str:
        if config.test_after is None:
            return "train"
        close_d = market_end.date() if hasattr(market_end, "date") else market_end
        return "test" if close_d >= config.test_after else "train"

    def _scan_market(self, ticker, series, market_start, market_end,
                     no_won, split, config: BacktestConfig):
        """Fetch candles for one market and return the first qualifying Observation."""
        try:
            resp = self.client.get_historical_market_candlesticks(
                ticker=ticker,
                start_ts=utc_timestamp(market_start),
                end_ts=utc_timestamp(market_end),
                period_interval=1,
            )
            candles = resp.get("candlesticks", []) or []
        except Exception as e:
            if self.verbose:
                print(f"  [WARN] {ticker}: {e}")
            time.sleep(_API_DELAY_SEC)
            return None

        time.sleep(_API_DELAY_SEC)

        if len(candles) < _MIN_CANDLES:
            return None

        def _ts_key(c):
            try:
                return int(c.get("end_period_ts") or 0)
            except (TypeError, ValueError):
                return 0

        candles_sorted = sorted(candles, key=_ts_key)

        pre_mids = []

        for candle in candles_sorted:
            bid, ask, mid, source = parse_quote(candle)

            ts_raw = candle.get("end_period_ts")
            if ts_raw is None:
                if mid is not None:
                    pre_mids.append(mid)
                continue

            try:
                candle_time = to_naive_utc(int(ts_raw))
                tte         = (market_end - candle_time).total_seconds() / 60.0
            except Exception:
                # Unparseable timestamp — skip entirely (position unknown, no lookahead risk).
                continue

            is_signal = (
                mid is not None
                and mid >= config.scan_threshold
                and config.min_tte <= tte <= config.max_tte
            )

            if is_signal:
                pre_vol = (
                    (max(pre_mids) - min(pre_mids)) if len(pre_mids) >= 2 else 0.0
                )
                # Longshot always buys NO — the executable price is 1 - yes_bid,
                # not the midpoint (which is never actually fillable).
                return Observation(
                    ticker            = ticker,
                    series            = series,
                    market_close_date = market_end.strftime("%Y-%m-%d"),
                    entry_time_utc    = candle_time.strftime("%Y-%m-%dT%H:%M:%S"),
                    yes_mid           = round(mid, 4),
                    no_price          = round(1.0 - bid, 4),
                    yes_bid           = round(bid, 4),
                    yes_ask           = round(ask, 4),
                    spread_cents      = round((ask - bid) * 100, 2),
                    tte_minutes       = round(tte, 2),
                    no_won            = no_won,
                    yes_bucket        = bucket_label(mid),
                    time_segment      = time_segment(candle_time),
                    pre_entry_vol     = round(pre_vol, 4),
                    price_source      = source,
                    split             = split,
                )

            # Candle not a signal — accumulate as pre-entry history (no lookahead)
            if mid is not None:
                pre_mids.append(mid)

        return None
