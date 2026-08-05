"""Favorite-Longshot strategy for Kalshi 15-min binary markets.

Academic motivation: the favorite-longshot bias in prediction markets causes YES
contracts priced above ~85c to win slightly less often than implied.  Buying NO on
such contracts and holding to expiry captures that edge — settlement is free
(no exit taker fee), so the round-trip cost is just one entry fee.
"""

import requests
from datetime import datetime, timezone

from kalshi.strategies.base import Strategy


# Module-level Binance momentum cache: symbol -> (momentum_pct, fetched_at)
_BINANCE_SYMBOL = {"KXBTC15M": "BTCUSDT", "KXETH15M": "ETHUSDT"}
_BINANCE_CACHE: dict[str, tuple[float, datetime]] = {}
_BINANCE_CACHE_TTL = 30.0


def _get_binance_momentum(ticker: str, window_minutes: int) -> float | None:
    """Return BTC/ETH pct change over the last `window_minutes` 1-min candles.

    Results are cached per symbol for 30 s to avoid redundant HTTP calls when
    multiple Kalshi tickers share the same underlying asset.
    """
    series = ticker.split("-")[0] if "-" in ticker else ticker
    symbol = _BINANCE_SYMBOL.get(series)
    if not symbol:
        return None

    now = datetime.now(timezone.utc)
    cached = _BINANCE_CACHE.get(symbol)
    if cached is not None:
        value, fetched_at = cached
        if (now - fetched_at).total_seconds() < _BINANCE_CACHE_TTL:
            return value

    try:
        url = "https://api.binance.com/api/v3/klines"
        r = requests.get(url, params={"symbol": symbol, "interval": "1m",
                                      "limit": window_minutes + 1}, timeout=3)
        candles = r.json()
        if not candles or len(candles) < 2:
            return None
        first_open = float(candles[0][1])
        last_close = float(candles[-1][4])
        if first_open <= 0:
            return None
        result = (last_close - first_open) / first_open * 100.0
        _BINANCE_CACHE[symbol] = (result, now)
        return result
    except Exception:
        return None


def _parse_expires_at(raw) -> datetime | None:
    """Parse an ISO expires_at string to a UTC-aware datetime, or return None."""
    if not raw:
        return None
    try:
        if isinstance(raw, str):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return None
    except Exception:
        return None


class FavoriteLongshotStrategy(Strategy):
    """Buy NO on high-YES contracts and hold to expiry.

    Entry filters (applied in order):
      1. YES mid >= longshot_threshold (default 0.85)
      2. Time-to-expiry in [min_tte_minutes, max_tte_minutes]
      3. Spread <= max_spread_cents (liquidity gate)
      4. |Binance momentum| <= momentum_threshold_pct (skip trending markets)
    """

    name = "favorite_longshot"

    def __init__(
        self,
        longshot_threshold:      float = 0.85,
        time_stop_minutes:       int   = 14,
        max_positions:           int   = 8,
        position_size_pct:       float = 0.01,
        min_tte_minutes:         int   = 5,
        max_tte_minutes:         int   = 13,
        max_spread_cents:        float = 8.0,
        momentum_filter:         bool  = True,
        momentum_window_minutes: int   = 2,
        momentum_threshold_pct:  float = 0.3,
    ):
        self.longshot_threshold      = longshot_threshold
        self.time_stop_minutes       = time_stop_minutes
        self.max_positions           = max_positions
        self.position_size_pct       = position_size_pct
        self.min_tte_minutes         = min_tte_minutes
        self.max_tte_minutes         = max_tte_minutes
        self.max_spread_cents        = max_spread_cents
        self.momentum_filter         = momentum_filter
        self.momentum_window_minutes = momentum_window_minutes
        self.momentum_threshold_pct  = momentum_threshold_pct
        # TradingEngine reads these via getattr; longshot has no fixed TP/SL
        self.take_profit_cents       = None
        self.stop_loss_cents         = None
        self.history_minutes         = 1

    def signal(self, ticker: str, price_history: list, market_quotes: dict) -> dict | None:
        q = market_quotes.get(ticker)
        if not q:
            return None

        yes_bid = q.get("yes_bid_dollars")
        yes_ask = q.get("yes_ask_dollars")
        if yes_bid is None or yes_ask is None:
            return None
        yes_mid = (float(yes_bid) + float(yes_ask)) / 2.0

        if yes_mid < self.longshot_threshold:
            return None

        expires_at = _parse_expires_at(q.get("close_time"))
        if expires_at is None:
            return None  # skip markets with unknown expiry — can't validate TTE
        tte = (expires_at - datetime.now(timezone.utc)).total_seconds() / 60.0
        if tte < self.min_tte_minutes or tte > self.max_tte_minutes:
            return None

        spread_cents = (float(yes_ask) - float(yes_bid)) * 100.0
        if spread_cents > self.max_spread_cents:
            return None

        if self.momentum_filter:
            mom_pct = _get_binance_momentum(ticker, self.momentum_window_minutes)
            if mom_pct is not None and abs(mom_pct) > self.momentum_threshold_pct:
                return None

        return {"side": "no", "type": "longshot",
                "yes_mid": yes_mid, "threshold": self.longshot_threshold}

    def config_params(self) -> dict:
        return {
            "strategy":               self.name,
            "longshot_threshold":     self.longshot_threshold,
            "time_stop_minutes":      self.time_stop_minutes,
            "max_positions":          self.max_positions,
            "position_size_pct":      self.position_size_pct,
            "min_tte_minutes":        self.min_tte_minutes,
            "max_tte_minutes":        self.max_tte_minutes,
            "max_spread_cents":       self.max_spread_cents,
            "momentum_filter":        self.momentum_filter,
            "momentum_window_minutes": self.momentum_window_minutes,
            "momentum_threshold_pct": self.momentum_threshold_pct,
        }
