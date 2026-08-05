"""Historical Binance klines — public endpoint, no auth required.

Used by the fair-value backtest to reconstruct the BTC/ETH price path (spot +
realized volatility) at any point in a historical Kalshi market's lifetime,
without lookahead.
"""

import bisect
import time
import requests

_KLINES_URL   = "https://api.binance.com/api/v3/klines"
_MAX_PER_CALL = 1000
_API_DELAY_SEC = 0.05


def fetch_klines_range(symbol: str, start_ms: int, end_ms: int, interval: str = "1m") -> list:
    """Fetch all 1-min klines for `symbol` between start_ms and end_ms (inclusive).

    Returns a list of (open_time_ms, close_price) tuples, sorted ascending.
    Paginates over Binance's 1000-candle-per-request limit.
    """
    out = []
    cursor = start_ms
    interval_ms = 60_000 if interval == "1m" else None
    if interval_ms is None:
        raise ValueError(f"Unsupported interval: {interval}")

    while cursor <= end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": _MAX_PER_CALL,
        }
        resp = requests.get(_KLINES_URL, params=params, timeout=10)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break

        for row in batch:
            open_time_ms = int(row[0])
            close_price  = float(row[4])
            out.append((open_time_ms, close_price))

        last_open = int(batch[-1][0])
        if len(batch) < _MAX_PER_CALL or last_open <= cursor:
            break
        cursor = last_open + interval_ms
        time.sleep(_API_DELAY_SEC)

    out.sort(key=lambda t: t[0])
    return out


class PriceSeries:
    """Sorted (timestamp_ms, price) series with no-lookahead lookups."""

    def __init__(self, points: list):
        self._points = sorted(points, key=lambda t: t[0])
        self._times  = [p[0] for p in self._points]

    def spot_at(self, ts_ms: int):
        """Most recent price at or before ts_ms, or None if none exists yet."""
        idx = self._bisect_right(ts_ms)
        if idx == 0:
            return None
        return self._points[idx - 1][1]

    def trailing_closes(self, ts_ms: int, window_ms: int) -> list:
        """Close prices in (ts_ms - window_ms, ts_ms], strictly no lookahead."""
        idx = self._bisect_right(ts_ms)
        lo  = ts_ms - window_ms
        closes = []
        for i in range(idx - 1, -1, -1):
            t, price = self._points[i]
            if t < lo:
                break
            closes.append(price)
        closes.reverse()
        return closes

    def _bisect_right(self, ts_ms: int) -> int:
        return bisect.bisect_right(self._times, ts_ms)
