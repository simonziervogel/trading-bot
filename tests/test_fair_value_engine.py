"""Tests for kalshi.backtest.fair_value_engine — signal detection against a synthetic price series."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from kalshi.backtest.fair_value_engine import FairValueBacktestConfig, FairValueBacktestEngine
from kalshi.data.binance_history import PriceSeries
from kalshi.utils.pricing import digital_call_probability, realized_vol_annualized
from kalshi.utils.time import utc_timestamp

_EPOCH = datetime(1970, 1, 1)


def _utc_epoch(naive_utc: datetime) -> int:
    return int((naive_utc - _EPOCH).total_seconds())


def _ms(naive_utc: datetime) -> int:
    return _utc_epoch(naive_utc) * 1000


def _candle(mid, end_naive_utc: datetime):
    return {
        "yes_bid": {"close": mid - 0.005},
        "yes_ask": {"close": mid + 0.005},
        "price":   {"close": mid},
        "end_period_ts": _utc_epoch(end_naive_utc),
    }


def _market(ticker, close_naive_utc: datetime, floor_strike, strike_type="greater_or_equal",
           result="yes"):
    open_dt = close_naive_utc - timedelta(minutes=15)
    return {
        "ticker":       ticker,
        "result":       result,
        "open_time":    open_dt.isoformat() + "Z",
        "close_time":   close_naive_utc.isoformat() + "Z",
        "floor_strike": floor_strike,
        "strike_type":  strike_type,
    }


def _flat_price_series(close_naive_utc: datetime, spot: float, minutes_back=90) -> PriceSeries:
    """Synthetic 1-min series with small alternating noise, so realized vol is
    finite and positive, spanning well before the market window for the
    volatility lookback."""
    points = []
    for m in range(minutes_back, -1, -1):
        t = close_naive_utc - timedelta(minutes=m)
        noise = 5.0 if m % 2 == 0 else -5.0
        points.append((_ms(t), spot + noise))
    return PriceSeries(points)


def _make_engine():
    return FairValueBacktestEngine(client=MagicMock(), verbose=False)


class TestScanMarket:
    def test_returns_none_with_too_few_candles(self):
        close  = datetime(2026, 6, 1, 14, 0, 0)
        engine = _make_engine()
        engine.client.get_historical_market_candlesticks.return_value = {"candlesticks": []}
        cfg    = FairValueBacktestConfig()
        market = _market("T", close, floor_strike=60000.0)
        series = _flat_price_series(close, spot=65000.0)
        obs = engine._scan_market(market, "KXBTC15M", series, cfg)
        assert obs is None

    def test_deep_itm_underpriced_yes_signals(self):
        close  = datetime(2026, 6, 1, 14, 0, 0)
        cfg    = FairValueBacktestConfig(min_tte=2.0, max_tte=14.0, min_edge_pct=0.03,
                                         vol_window_minutes=60)
        market = _market("T", close, floor_strike=60000.0, result="yes")

        # Spot ~65000, far above the 60000 strike -> model P(yes) near 1.
        # Kalshi quotes YES at 0.50 -> large mispricing on the yes side.
        candles = [
            _candle(0.50, close - timedelta(minutes=12)),
            _candle(0.50, close - timedelta(minutes=10)),
            _candle(0.50, close - timedelta(minutes=8)),
        ]
        engine = _make_engine()
        engine.client.get_historical_market_candlesticks.return_value = {"candlesticks": candles}
        series = _flat_price_series(close, spot=65000.0)

        obs = engine._scan_market(market, "KXBTC15M", series, cfg)
        assert obs is not None
        assert obs.side == "yes"
        assert obs.side_won is True   # result == "yes"
        assert obs.tte_minutes == pytest.approx(12.0, abs=0.1)   # first qualifying candle
        assert obs.model_prob > 0.9

    def test_no_signal_when_market_price_matches_model(self):
        close  = datetime(2026, 6, 1, 14, 0, 0)
        cfg    = FairValueBacktestConfig(min_tte=2.0, max_tte=14.0, min_edge_pct=0.03)
        strike = 65000.0
        market = _market("T", close, floor_strike=strike, result="yes")
        entry_time = close - timedelta(minutes=12)
        series = _flat_price_series(close, spot=65000.0)

        # Compute exactly what the engine will compute, then quote Kalshi's
        # price at that same value so there's ~zero edge on either side.
        candle_ms  = _ms(entry_time)
        spot       = series.spot_at(candle_ms)
        sigma      = realized_vol_annualized(
            series.trailing_closes(candle_ms, cfg.vol_window_minutes * 60_000)
        )
        tte_years  = 12.0 / (365.25 * 24 * 60)
        p_yes      = digital_call_probability(spot, strike, tte_years, sigma)

        candles = [_candle(p_yes, entry_time),
                  _candle(p_yes, close - timedelta(minutes=10)),
                  _candle(p_yes, close - timedelta(minutes=8))]
        engine = _make_engine()
        engine.client.get_historical_market_candlesticks.return_value = {"candlesticks": candles}

        obs = engine._scan_market(market, "KXBTC15M", series, cfg)
        assert obs is None

    def test_no_signal_when_spot_unavailable(self):
        close  = datetime(2026, 6, 1, 14, 0, 0)
        cfg    = FairValueBacktestConfig(min_tte=2.0, max_tte=14.0)
        market = _market("T", close, floor_strike=60000.0)
        candles = [_candle(0.50, close - timedelta(minutes=t)) for t in (12, 10, 8)]
        engine = _make_engine()
        engine.client.get_historical_market_candlesticks.return_value = {"candlesticks": candles}
        empty_series = PriceSeries([])   # no Binance data at all -> spot_at() returns None

        obs = engine._scan_market(market, "KXBTC15M", empty_series, cfg)
        assert obs is None


class TestFetchMarketsFiltersMissingStrike:
    def test_markets_without_floor_strike_are_excluded(self):
        engine = _make_engine()
        close = datetime(2026, 6, 1, 14, 0, 0)
        good = _market("A", close, floor_strike=60000.0)
        good["volume_fp"] = "5000"
        bad = _market("B", close, floor_strike=None)
        bad["volume_fp"] = "5000"
        del bad["floor_strike"]
        engine.client.get_historical_markets.return_value = {"markets": [good, bad], "cursor": None}

        cfg = FairValueBacktestConfig(max_markets=10, min_volume=1000.0)
        markets = engine._fetch_markets("KXBTC15M", cfg)
        tickers = {m["ticker"] for m in markets}
        assert tickers == {"A"}
