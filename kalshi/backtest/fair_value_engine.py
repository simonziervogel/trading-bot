"""FairValueBacktestEngine — scans historical Kalshi markets for digital-option mispricing.

For each historical market, replays Kalshi's own candlesticks against an
independently reconstructed Binance BTC/ETH price series to find the first
candle (within the TTE window) where the model probability disagrees with
Kalshi's quoted, *executable* price (yes_ask / 1-yes_bid, fee-adjusted — never
the midpoint, which isn't a real fill) by more than min_edge_pct. No
lookahead on either data source: only candles/klines strictly at-or-before
the scan time are used.
"""

import csv
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from kalshi.client import KalshiClient
from kalshi.utils.time import to_naive_utc, utc_timestamp, parse_optional_dt
from kalshi.utils.market import parse_quote, time_segment
from kalshi.utils.pricing import digital_call_probability, realized_vol_annualized
from kalshi.utils.fees import taker_fee
from kalshi.data.binance_history import fetch_klines_range, PriceSeries

_BINANCE_SYMBOL = {"KXBTC15M": "BTCUSDT", "KXETH15M": "ETHUSDT"}
_API_DELAY_SEC  = 0.12
_MIN_CANDLES    = 3

# Sanity bounds on spot/strike. These contracts settle against the underlying
# 15 minutes after the strike is fixed, so the ratio is normally within a
# fraction of a percent of 1.0 — deliberately generous bounds that only catch
# gross data corruption, not legitimate market moves. Kalshi has been observed
# returning floor_strike off by ~4 orders of magnitude (e.g. 0.22 instead of
# 2229.30 on 2026-04-13); without this guard those rows produce model_prob=1.0
# and a bogus signal.
_MIN_SPOT_STRIKE_RATIO = 0.5
_MAX_SPOT_STRIKE_RATIO = 2.0


@dataclass
class FairValueBacktestConfig:
    series: list = field(default_factory=lambda: ["KXBTC15M", "KXETH15M"])
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    test_after: Optional[date] = None
    min_tte: float = 2.0
    max_tte: float = 14.0
    min_volume: float = 1000.0
    max_markets: int = 500
    min_edge_pct: float = 0.03
    vol_window_minutes: int = 60


@dataclass
class FairValueObservation:
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
    model_prob: float
    edge: float
    spot: float
    strike: float
    sigma: float
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
            "time_segment": self.time_segment,
            "model_prob": self.model_prob, "edge": self.edge,
            "spot": self.spot, "strike": self.strike, "sigma": self.sigma,
            "split": self.split,
        }


class FairValueResults:
    def __init__(self, config: FairValueBacktestConfig, observations: list):
        self.config = config
        self.observations: list[FairValueObservation] = observations

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


class FairValueBacktestEngine:
    def __init__(self, client: Optional[KalshiClient] = None, verbose: bool = False):
        self.client  = client or KalshiClient()
        self.verbose = verbose

    def run(self, config: FairValueBacktestConfig) -> FairValueResults:
        observations = []
        for series in config.series:
            print(f"\n-- {series} ----------------------------------")
            markets = self._fetch_markets(series, config)
            print(f"  Processing: {len(markets)} markets")
            price_series = self._load_price_series(series, markets, config)
            if price_series is None:
                print(f"  [WARN] no Binance symbol mapped for {series} — skipping")
                continue

            sig_count = 0
            for i, market in enumerate(markets):
                obs = self._scan_market(market, series, price_series, config)
                if obs is not None:
                    observations.append(obs)
                    sig_count += 1
                if self.verbose or (i + 1) % 50 == 0:
                    print(f"  [{i+1:>4}/{len(markets)}] {market['ticker']}  "
                          f"{'SIG' if obs else '   '}  obs={len(observations)}")
            print(f"  Signals: {sig_count} / {len(markets)}")

        print(f"\nTotal observations: {len(observations)}")
        return FairValueResults(config=config, observations=observations)

    # ------------------------------------------------------------------

    def _fetch_markets(self, series: str, config: FairValueBacktestConfig) -> list:
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
            and m.get("floor_strike") is not None and m.get("strike_type")
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

    def _load_price_series(self, series: str, markets: list,
                            config: FairValueBacktestConfig) -> Optional[PriceSeries]:
        symbol = _BINANCE_SYMBOL.get(series)
        if not symbol or not markets:
            return None

        opens  = [to_naive_utc(m["open_time"])  for m in markets]
        closes = [to_naive_utc(m["close_time"]) for m in markets]
        start  = min(opens)
        end    = max(closes)

        start_ms = utc_timestamp(start) * 1000 - config.vol_window_minutes * 60_000
        end_ms   = utc_timestamp(end) * 1000

        print(f"  Fetching {symbol} Binance history: "
              f"{start} - {end} ({(end_ms - start_ms) / 60_000:.0f} min)")
        klines = fetch_klines_range(symbol, start_ms, end_ms)
        print(f"  Got {len(klines)} {symbol} klines")
        return PriceSeries(klines)

    def _determine_split(self, market_end: datetime, config: FairValueBacktestConfig) -> str:
        if config.test_after is None:
            return "train"
        close_d = market_end.date() if hasattr(market_end, "date") else market_end
        return "test" if close_d >= config.test_after else "train"

    def _scan_market(self, market: dict, series: str, price_series: PriceSeries,
                      config: FairValueBacktestConfig):
        ticker      = market["ticker"]
        result      = market.get("result", "").lower()
        strike      = float(market["floor_strike"])
        strike_type = market["strike_type"]

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

        for candle in candles_sorted:
            bid, ask, mid, _source = parse_quote(candle)
            if bid is None or ask is None:
                continue

            ts_raw = candle.get("end_period_ts")
            if ts_raw is None:
                continue
            try:
                candle_time = to_naive_utc(int(ts_raw))
                tte_minutes = (market_end - candle_time).total_seconds() / 60.0
            except Exception:
                continue

            if not (config.min_tte <= tte_minutes <= config.max_tte):
                continue

            candle_ms = utc_timestamp(candle_time) * 1000
            spot = price_series.spot_at(candle_ms)
            if spot is None:
                continue
            # Reject implausible strikes (bad Kalshi data) rather than pricing
            # off them — see _MIN/_MAX_SPOT_STRIKE_RATIO above.
            ratio = spot / strike
            if not (_MIN_SPOT_STRIKE_RATIO < ratio < _MAX_SPOT_STRIKE_RATIO):
                if self.verbose:
                    print(f"  [WARN] {ticker}: implausible spot/strike={ratio:.4g} "
                          f"(spot={spot}, strike={strike}) — skipping")
                continue
            closes = price_series.trailing_closes(candle_ms, config.vol_window_minutes * 60_000)
            sigma = realized_vol_annualized(closes)
            if sigma is None:
                continue

            tte_years = tte_minutes / (365.25 * 24 * 60)
            p_yes = digital_call_probability(spot, strike, tte_years, sigma, strike_type)
            if p_yes is None:
                continue

            # Compare the model directly against what you'd actually pay —
            # yes_ask (buy YES) / 1-yes_bid (buy NO) — never the midpoint,
            # and net out the entry fee. Mirrors the live FairValueStrategy
            # exactly (kalshi/strategies/fair_value.py), which already did
            # this correctly; the backtest previously did not.
            yes_price = ask
            no_price  = 1.0 - bid
            edge_yes  = p_yes - yes_price - taker_fee(yes_price, 1)
            edge_no   = (1.0 - p_yes) - no_price - taker_fee(no_price, 1)

            if edge_yes < config.min_edge_pct and edge_no < config.min_edge_pct:
                continue

            if edge_yes >= edge_no:
                side, entry_price, edge = "yes", yes_price, edge_yes
            else:
                side, entry_price, edge = "no", no_price, edge_no

            side_won = (result == side)

            return FairValueObservation(
                ticker=ticker, series=series,
                market_close_date=market_end.strftime("%Y-%m-%d"),
                entry_time_utc=candle_time.strftime("%Y-%m-%dT%H:%M:%S"),
                side=side, entry_price=round(entry_price, 4),
                yes_bid=round(bid, 4), yes_ask=round(ask, 4),
                spread_cents=round((ask - bid) * 100, 2),
                side_won=side_won,
                tte_minutes=round(tte_minutes, 2),
                time_segment=time_segment(candle_time),
                model_prob=round(p_yes, 4), edge=round(edge, 4),
                spot=round(spot, 2), strike=round(strike, 2), sigma=round(sigma, 4),
                split=split,
            )

        return None
