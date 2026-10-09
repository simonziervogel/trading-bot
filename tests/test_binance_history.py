"""Tests for kalshi.data.binance_history — no-lookahead guarantees on kline lookups."""
from unittest.mock import patch

from kalshi.data.binance_history import fetch_klines_range, PriceSeries

_MIN = 60_000


def _raw(open_ms: int, close_price: float) -> list:
    """A Binance kline row; only index 0 (open time) and 4 (close) are used."""
    return [open_ms, "0", "0", "0", str(close_price), "0",
            open_ms + _MIN - 1, "0", 0, "0", "0", "0"]


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestFetchKlinesRangeTimestamps:
    """A kline opening at T does not close until T+60s, so its close price is
    not knowable at T. Keying the series on open time would backdate every
    price by a minute and hand the backtest 60s of foresight."""

    def test_points_are_keyed_by_close_time_not_open_time(self):
        rows = [_raw(0, 101.0), _raw(_MIN, 102.0), _raw(2 * _MIN, 103.0)]
        with patch("kalshi.data.binance_history.requests.get",
                   side_effect=[_Resp(rows), _Resp([])]):
            pts = fetch_klines_range("BTCUSDT", 0, 2 * _MIN)

        assert pts == [(_MIN, 101.0), (2 * _MIN, 102.0), (3 * _MIN, 103.0)]

    def test_spot_at_never_returns_a_future_price(self):
        rows = [_raw(0, 101.0), _raw(_MIN, 102.0), _raw(2 * _MIN, 103.0)]
        with patch("kalshi.data.binance_history.requests.get",
                   side_effect=[_Resp(rows), _Resp([])]):
            series = PriceSeries(fetch_klines_range("BTCUSDT", 0, 2 * _MIN))

        # Nothing has closed yet at t=0, and mid-way through the first bar the
        # only settled information is still nothing.
        assert series.spot_at(0) is None
        assert series.spot_at(_MIN - 1) is None
        # The 101.0 close becomes knowable exactly at t=60s, not before.
        assert series.spot_at(_MIN) == 101.0
        assert series.spot_at(2 * _MIN - 1) == 101.0
        assert series.spot_at(2 * _MIN) == 102.0

    def test_trailing_closes_excludes_the_unclosed_bar(self):
        rows = [_raw(i * _MIN, 100.0 + i) for i in range(5)]
        with patch("kalshi.data.binance_history.requests.get",
                   side_effect=[_Resp(rows), _Resp([])]):
            series = PriceSeries(fetch_klines_range("BTCUSDT", 0, 4 * _MIN))

        # At t=3min the bars opening at 0/1/2 min have closed; the one opening
        # at 3min has not, so 103.0 must not appear.
        closes = series.trailing_closes(3 * _MIN, 60 * _MIN)
        assert closes == [100.0, 101.0, 102.0]


class TestPriceSeries:
    def test_empty_series_returns_none(self):
        series = PriceSeries([])
        assert series.spot_at(12345) is None
        assert series.trailing_closes(12345, 60 * _MIN) == []

    def test_trailing_closes_respects_window_and_order(self):
        """Both ends inclusive: an N-minute window holds N+1 closes, which is
        the N log returns that window spans."""
        series = PriceSeries([(i * _MIN, float(i)) for i in range(10)])
        assert series.trailing_closes(9 * _MIN, 3 * _MIN) == [6.0, 7.0, 8.0, 9.0]
