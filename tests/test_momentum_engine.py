"""Tests for kalshi.backtest.momentum_engine — signal detection and no-lookahead guarantee."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from kalshi.backtest.momentum_engine import MomentumBacktestConfig, MomentumBacktestEngine

_EPOCH = datetime(1970, 1, 1)


def _utc_epoch(naive_utc: datetime) -> int:
    return int((naive_utc - _EPOCH).total_seconds())


def _candle(mid, end_naive_utc: datetime):
    return {
        "yes_bid": {"close": mid - 0.005},
        "yes_ask": {"close": mid + 0.005},
        "price":   {"close": mid},
        "end_period_ts": _utc_epoch(end_naive_utc),
    }


def _market(ticker, close_naive_utc: datetime, result="yes"):
    open_dt = close_naive_utc - timedelta(minutes=15)
    return {
        "ticker":     ticker,
        "result":     result,
        "open_time":  open_dt.isoformat() + "Z",
        "close_time": close_naive_utc.isoformat() + "Z",
    }


def _make_engine(candles):
    mock_client = MagicMock()
    mock_client.get_historical_market_candlesticks.return_value = {"candlesticks": candles}
    return MomentumBacktestEngine(client=mock_client, verbose=False)


class TestScanMarket:
    def test_returns_none_with_too_few_candles(self):
        close  = datetime(2026, 6, 1, 14, 0, 0)
        engine = _make_engine([])
        cfg    = MomentumBacktestConfig()
        obs    = engine._scan_market(_market("T", close), "KXBTC15M", cfg)
        assert obs is None

    def test_upward_trend_signals_yes(self):
        close = datetime(2026, 6, 1, 14, 0, 0)
        cfg   = MomentumBacktestConfig(
            momentum_threshold_pct=0.02, history_minutes=10.0, min_history_points=4,
            min_tte=5.0, max_tte=13.0,
        )
        # Flat pre-history (never a signal candidate: TTE > max_tte)
        pre = [_candle(0.40, close - timedelta(minutes=m)) for m in (20, 19, 18, 17, 16, 15)]
        # First in-window candle: flat -> no signal
        flat_in_window = _candle(0.40, close - timedelta(minutes=12))
        # Second in-window candle: +15% -> should be the first qualifying signal
        rising = _candle(0.46, close - timedelta(minutes=10))
        # Third in-window candle: further rise — must NOT be picked (not first)
        later = _candle(0.60, close - timedelta(minutes=8))

        candles = pre + [flat_in_window, rising, later]
        engine  = _make_engine(candles)
        market  = _market("T", close, result="yes")

        obs = engine._scan_market(market, "KXBTC15M", cfg)
        assert obs is not None
        assert obs.side == "yes"
        assert obs.tte_minutes == pytest.approx(10.0, abs=0.1)
        assert obs.side_won is True   # result == "yes" == side

    def test_recorded_price_is_executable_ask_bid_not_mid(self):
        """entry_price must come from yes_ask/yes_bid, not the mid used for the signal."""
        close = datetime(2026, 6, 1, 14, 0, 0)
        cfg   = MomentumBacktestConfig(
            momentum_threshold_pct=0.02, history_minutes=10.0, min_history_points=4,
            min_tte=5.0, max_tte=13.0,
        )
        pre = [_candle(0.40, close - timedelta(minutes=m)) for m in (20, 19, 18, 17, 16, 15)]
        # Wide spread on the signal candle: mid=0.46 (qualifies, +15%), but
        # yes_bid=0.41 / yes_ask=0.51 -> real YES fill price is 0.51, not 0.46
        wide = {
            "yes_bid": {"close": 0.41}, "yes_ask": {"close": 0.51},
            "price": {"close": 0.46}, "end_period_ts": _utc_epoch(close - timedelta(minutes=10)),
        }
        engine = _make_engine(pre + [wide])
        market = _market("T", close, result="yes")

        obs = engine._scan_market(market, "KXBTC15M", cfg)
        assert obs is not None
        assert obs.side == "yes"
        assert obs.yes_bid == pytest.approx(0.41)
        assert obs.yes_ask == pytest.approx(0.51)
        assert obs.entry_price == pytest.approx(0.51)   # yes_ask
        assert obs.entry_price != pytest.approx(0.46)     # NOT mid
        assert obs.spread_cents == pytest.approx(10.0)

    def test_downward_trend_signals_no(self):
        close = datetime(2026, 6, 1, 14, 0, 0)
        cfg   = MomentumBacktestConfig(
            momentum_threshold_pct=0.02, history_minutes=10.0, min_history_points=4,
            min_tte=5.0, max_tte=13.0,
        )
        pre     = [_candle(0.60, close - timedelta(minutes=m)) for m in (20, 19, 18, 17, 16, 15)]
        falling = _candle(0.50, close - timedelta(minutes=10))   # -16.7% -> signal

        candles = pre + [falling]
        engine  = _make_engine(candles)
        market  = _market("T", close, result="no")

        obs = engine._scan_market(market, "KXBTC15M", cfg)
        assert obs is not None
        assert obs.side == "no"
        assert obs.side_won is True   # result == "no" == side

    def test_no_lookahead_future_spike_ignored(self):
        """A later spike must not influence the trailing window of an earlier candle."""
        close = datetime(2026, 6, 1, 14, 0, 0)
        cfg   = MomentumBacktestConfig(
            momentum_threshold_pct=0.5, history_minutes=10.0, min_history_points=4,
            min_tte=5.0, max_tte=13.0,
        )
        pre = [_candle(0.40, close - timedelta(minutes=m)) for m in (20, 19, 18, 17, 16, 15)]
        # In-window candle: tiny move, well below the 50% threshold on its own trailing window
        small_move = _candle(0.41, close - timedelta(minutes=12))
        # Huge spike AFTER the small-move candle — must not retroactively qualify it
        spike = _candle(0.90, close - timedelta(minutes=8))

        candles = pre + [small_move, spike]
        engine  = _make_engine(candles)
        market  = _market("T", close, result="yes")

        obs = engine._scan_market(market, "KXBTC15M", cfg)
        # small_move doesn't qualify (2.5% < 50%); spike does (0.90 vs 0.40 baseline = +125%)
        assert obs is not None
        assert obs.tte_minutes == pytest.approx(8.0, abs=0.1)

    def test_no_signal_when_history_too_short(self):
        close = datetime(2026, 6, 1, 14, 0, 0)
        cfg   = MomentumBacktestConfig(
            momentum_threshold_pct=0.02, history_minutes=10.0, min_history_points=10,
            min_tte=5.0, max_tte=13.0,
        )
        pre = [_candle(0.40, close - timedelta(minutes=m)) for m in (16, 15)]
        rising = _candle(0.60, close - timedelta(minutes=10))
        engine = _make_engine(pre + [rising])
        obs = engine._scan_market(_market("T", close), "KXBTC15M", cfg)
        assert obs is None


class TestDetermineSplit:
    def setup_method(self):
        self.engine = MomentumBacktestEngine(client=MagicMock(), verbose=False)

    def test_no_split_date_returns_train(self):
        from datetime import timezone
        cfg = MomentumBacktestConfig(test_after=None)
        dt  = datetime(2026, 6, 1, tzinfo=timezone.utc)
        assert self.engine._determine_split(dt, cfg) == "train"

    def test_after_split_is_test(self):
        from datetime import date, timezone
        cfg = MomentumBacktestConfig(test_after=date(2026, 4, 1))
        dt  = datetime(2026, 5, 15, tzinfo=timezone.utc)
        assert self.engine._determine_split(dt, cfg) == "test"
