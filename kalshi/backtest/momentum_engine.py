"""MomentumBacktestEngine — scans historical Kalshi markets for trend-continuation.

Self-contained to each market's own candle sequence (no external price data
needed, unlike FairValueBacktestEngine) — replays candles chronologically and
looks for the first point where the trailing price change exceeds
momentum_threshold_pct, entering in the direction of the move.
"""

import csv
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from kalshi.client import KalshiClient
from kalshi.utils.time import to_naive_utc, utc_timestamp, parse_optional_dt
from kalshi.utils.market import parse_quote, time_segment

_API_DELAY_SEC = 0.12
_MIN_CANDLES   = 3


@dataclass
class MomentumBacktestConfig:
    series: list = field(default_factory=lambda: ["KXBTC15M", "KXETH15M"])
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    test_after: Optional[date] = None
    min_tte: float = 5.0
    max_tte: float = 13.0
    min_volume: float = 1000.0
    max_markets: int = 500
    momentum_threshold_pct: float = 0.02
    # Kalshi historical candlesticks are 1-min resolution, so the trailing
    # window must be wide enough to actually contain min_history_points
    # candles (a 3-min window can hold at most ~3 points at 1-min density).
    history_minutes: float = 5.0
    min_history_points: int = 3


@dataclass
class MomentumObservation:
    ticker: str
    series: str
    market_close_date: str
    entry_time_utc: str
    side: str
    entry_price: float          # executable price: yes_ask (yes) or 1-yes_bid (no)
    yes_bid: float
    yes_ask: float
    spread_cents: float
    side_won: bool
    tte_minutes: float
    time_segment: str
    pct_change: float
    split: str

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "series": self.series,
            "market_close_date": self.market_close_date,
            "entry_time_utc": self.entry_time_utc,
            "side": self.side, "entry_price": self.entry_price,
            "yes_bid": self.yes_bid, "yes_ask": self.yes_ask,
            "spread_cents": self.spread_cents,
            "side_won": int(self.side_won), "tte_minutes": self.tte_minutes,
            "time_segment": self.time_segment, "pct_change": self.pct_change,
            "split": self.split,
        }


class MomentumResults:
    def __init__(self, config: MomentumBacktestConfig, observations: list):
        self.config = config
        self.observations: list[MomentumObservation] = observations

    @property
    def train(self) -> list:
        return [o for o in self.observations if o.split == "train"]

    @property
    def test(self) -> list:
        return [o for o in self.observations if o.split == "test"]

    def to_dicts(self) -> list[dict]:
        return [o.to_dict() for o in self.observations]

    def export_csv(self, path) -> None:
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


class MomentumBacktestEngine:
    def __init__(self, client: Optional[KalshiClient] = None, verbose: bool = False):
        self.client  = client or KalshiClient()
        self.verbose = verbose

    def run(self, config: MomentumBacktestConfig) -> MomentumResults:
        observations = []
        for series in config.series:
            print(f"\n-- {series} ----------------------------------")
            markets = self._fetch_markets(series, config)
            print(f"  Processing: {len(markets)} markets")

            sig_count = 0
            for i, market in enumerate(markets):
                obs = self._scan_market(market, series, config)
                if obs is not None:
                    observations.append(obs)
                    sig_count += 1
                if self.verbose or (i + 1) % 50 == 0:
                    print(f"  [{i+1:>4}/{len(markets)}] {market['ticker']}  "
                          f"{'SIG' if obs else '   '}  obs={len(observations)}")
            print(f"  Signals: {sig_count} / {len(markets)}")

        print(f"\nTotal observations: {len(observations)}")
        return MomentumResults(config=config, observations=observations)

    # ------------------------------------------------------------------

    def _fetch_markets(self, series: str, config: MomentumBacktestConfig) -> list:
        all_markets, cursor = [], None
        while True:
            resp  = self.client.get_historical_markets(
                limit=1000, series_ticker=series, cursor=cursor
            )
            batch = resp.get("markets", [])
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

        qualified_sorted = sorted(qualified, key=_close_key)
        return qualified_sorted[-config.max_markets:]

    def _determine_split(self, market_end: datetime, config: MomentumBacktestConfig) -> str:
        if config.test_after is None:
            return "train"
        close_d = market_end.date() if hasattr(market_end, "date") else market_end
        return "test" if close_d >= config.test_after else "train"

    def _scan_market(self, market: dict, series: str, config: MomentumBacktestConfig):
        ticker = market["ticker"]
        result = market.get("result", "").lower()

        try:
            market_start = to_naive_utc(market["open_time"])
            market_end   = to_naive_utc(market["close_time"])
        except Exception:
            return None

        split = self._determine_split(market_end, config)

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
        window = timedelta(minutes=config.history_minutes)
        pre_history = []  # [(candle_time, mid), ...] strictly before the current candle

        for candle in candles_sorted:
            bid, ask, mid, _source = parse_quote(candle)

            ts_raw = candle.get("end_period_ts")
            if ts_raw is None:
                continue
            try:
                candle_time = to_naive_utc(int(ts_raw))
                tte_minutes = (market_end - candle_time).total_seconds() / 60.0
            except Exception:
                continue

            if mid is not None and config.min_tte <= tte_minutes <= config.max_tte:
                cutoff = candle_time - window
                trailing = [p for t, p in pre_history if t >= cutoff]

                if len(trailing) >= config.min_history_points:
                    start_price = trailing[0]
                    if start_price > 0:
                        pct_change = (mid - start_price) / start_price
                        side = None
                        if pct_change >= config.momentum_threshold_pct:
                            side = "yes"
                        elif pct_change <= -config.momentum_threshold_pct:
                            side = "no"

                        if side is not None:
                            # Signal is decided on mid (represents the move
                            # itself); the recorded price is what you'd
                            # actually pay: yes_ask for YES, 1-yes_bid for NO.
                            entry_price = ask if side == "yes" else 1.0 - bid
                            side_won = (result == side)
                            return MomentumObservation(
                                ticker=ticker, series=series,
                                market_close_date=market_end.strftime("%Y-%m-%d"),
                                entry_time_utc=candle_time.strftime("%Y-%m-%dT%H:%M:%S"),
                                side=side, entry_price=round(entry_price, 4),
                                yes_bid=round(bid, 4), yes_ask=round(ask, 4),
                                spread_cents=round((ask - bid) * 100, 2),
                                side_won=side_won, tte_minutes=round(tte_minutes, 2),
                                time_segment=time_segment(candle_time),
                                pct_change=round(pct_change, 4), split=split,
                            )

            if mid is not None:
                pre_history.append((candle_time, mid))

        return None
