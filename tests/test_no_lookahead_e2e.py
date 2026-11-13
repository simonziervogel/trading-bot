"""End-to-end no-lookahead tests: perturb the future, expect the same signal.

The 60-second lookahead that produced the retracted Fair Value edge did not
live in any single function. `PriceSeries` looked up prices correctly and
`_scan_market` used them correctly; the error was in how raw Binance klines
were turned into timestamps *between* the two. Unit tests on either side
passed throughout.

These tests therefore make no assumption about where a lookahead could hide.
They feed each engine its raw inputs in their native API format, record the
signal produced at entry time T, then rewrite every piece of input that was
not yet observable at T and run again. A backtest that only uses the past
must return the identical observation.

For the fair-value engine the same check is also run against the old
open-time keying, where it must fail — proof that this test would have caught
the original bug.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from kalshi.backtest.engine import BacktestConfig, BacktestEngine
from kalshi.backtest.fair_value_engine import FairValueBacktestConfig, FairValueBacktestEngine
from kalshi.backtest.momentum_engine import MomentumBacktestConfig, MomentumBacktestEngine
from kalshi.data.binance_history import fetch_klines_range

_EPOCH = datetime(1970, 1, 1)
_MIN_MS = 60_000
_CLOSE = datetime(2026, 6, 1, 14, 0, 0)   # naive UTC, on a 15-minute boundary
_STRIKE = 65000.0


def _epoch_s(naive_utc: datetime) -> int:
    return int((naive_utc - _EPOCH).total_seconds())


def _epoch_ms(naive_utc: datetime) -> int:
    return _epoch_s(naive_utc) * 1000


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _kalshi_candle(bid: float, ask: float, end_naive_utc: datetime) -> dict:
    return {
        "yes_bid": {"close": bid},
        "yes_ask": {"close": ask},
        "price":   {"close": (bid + ask) / 2},
        "end_period_ts": _epoch_s(end_naive_utc),
    }


def _perturb_kalshi_after(candles: list, entry: datetime) -> list:
    """Rewrite every Kalshi candle that ended after `entry` to an extreme quote."""
    cutoff = _epoch_s(entry)
    out = []
    for c in candles:
        if c["end_period_ts"] > cutoff:
            out.append({**c, "yes_bid": {"close": 0.97}, "yes_ask": {"close": 0.99},
                        "price": {"close": 0.98}})
        else:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Fair value: raw Binance klines -> fetch_klines_range -> PriceSeries -> signal
# ---------------------------------------------------------------------------

def _raw_klines(close_naive_utc: datetime, minutes_back: int = 120) -> list:
    """Binance kline rows in the API's own format, one per minute.

    Row layout: [open_time_ms, open, high, low, close, volume, close_time_ms, ...].
    Prices hover within a few dollars of the strike, where the model is most
    sensitive to spot — a 15-minute contract's whole sigma*sqrt(tau) is ~20bp.
    """
    rows = []
    for m in range(minutes_back, -1, -1):
        open_ms = _epoch_ms(close_naive_utc - timedelta(minutes=m))
        price = _STRIKE + (4.0 if m % 2 == 0 else -4.0) + (m % 5)
        rows.append([open_ms, "0", "0", "0", str(price), "0",
                     open_ms + _MIN_MS - 1, "0", 0, "0", "0", "0"])
    return rows


def _perturb_klines_after(rows: list, entry: datetime) -> list:
    """Move every kline that had not CLOSED by `entry` up by 1% (100bp).

    A kline's close price becomes observable at open_time + 60s. Anything
    closing later than the entry time is the future as far as the signal is
    concerned, so the signal must not depend on it.
    """
    entry_ms = _epoch_ms(entry)
    out = []
    for row in rows:
        if row[0] + _MIN_MS > entry_ms:
            row = list(row)
            row[4] = str(float(row[4]) * 1.01)
        out.append(row)
    return out


def _open_time_keyed(symbol, start_ms, end_ms, interval="1m"):
    """fetch_klines_range as it behaved before the fix: keyed by open time."""
    return [(t - _MIN_MS, p) for t, p in fetch_klines_range(symbol, start_ms, end_ms, interval)]


def _fair_value_observation(raw_rows: list, candles: list, market: dict):
    """Run the engine's own load + scan path against mocked raw API payloads."""
    cfg = FairValueBacktestConfig(min_tte=2.0, max_tte=14.0, min_edge_pct=0.03,
                                  vol_window_minutes=60)
    engine = FairValueBacktestEngine(client=MagicMock(), verbose=False)
    engine.client.get_historical_market_candlesticks.return_value = {"candlesticks": candles}
    with patch("kalshi.data.binance_history.requests.get", return_value=_Resp(raw_rows)):
        series = engine._load_price_series("KXBTC15M", [market], cfg)
    return engine._scan_market(market, "KXBTC15M", series, cfg)


def _fair_value_case(entry_tte_min: int):
    market = {
        "ticker": "KXBTC15M-TEST", "result": "yes",
        "open_time":  (_CLOSE - timedelta(minutes=15)).isoformat() + "Z",
        "close_time": _CLOSE.isoformat() + "Z",
        "floor_strike": _STRIKE, "strike_type": "greater_or_equal",
    }
    entry = _CLOSE - timedelta(minutes=entry_tte_min)
    # Spot sits at the strike, so the model says ~50%. Kalshi quotes YES at
    # 0.30/0.31: a large YES edge, so the first in-window candle is the entry.
    candles = [_kalshi_candle(0.30, 0.31, entry),
               _kalshi_candle(0.30, 0.31, entry + timedelta(minutes=1)),
               _kalshi_candle(0.30, 0.31, entry + timedelta(minutes=2))]
    return market, entry, candles


@pytest.mark.parametrize("entry_tte_min", [12, 7, 3])
def test_fair_value_signal_ignores_everything_after_entry(entry_tte_min):
    market, entry, candles = _fair_value_case(entry_tte_min)
    rows = _raw_klines(_CLOSE)

    baseline = _fair_value_observation(rows, candles, market)
    assert baseline is not None, "scenario must produce a signal to be a meaningful test"
    assert baseline.tte_minutes == pytest.approx(entry_tte_min, abs=0.01)

    perturbed = _fair_value_observation(
        _perturb_klines_after(rows, entry),
        _perturb_kalshi_after(candles, entry),
        market,
    )
    assert perturbed == baseline


@pytest.mark.parametrize("entry_tte_min", [12, 7, 3])
def test_perturbation_test_catches_the_original_open_time_bug(entry_tte_min):
    """The same check, run against the pre-fix keying, must fail.

    Keyed by open time, spot_at(T) returns the close of the bar that opened at
    T — a price from T+60s. Perturbing that bar changes the signal, which is
    exactly the dependence on the future the test above rules out.
    """
    market, entry, candles = _fair_value_case(entry_tte_min)
    rows = _raw_klines(_CLOSE)

    with patch("kalshi.backtest.fair_value_engine.fetch_klines_range", _open_time_keyed):
        baseline = _fair_value_observation(rows, candles, market)
        perturbed = _fair_value_observation(
            _perturb_klines_after(rows, entry),
            _perturb_kalshi_after(candles, entry),
            market,
        )

    assert baseline is not None
    assert perturbed != baseline


# ---------------------------------------------------------------------------
# Longshot and Momentum: Kalshi candles only
# ---------------------------------------------------------------------------

def test_longshot_signal_ignores_everything_after_entry():
    entry = _CLOSE - timedelta(minutes=8)
    candles = [
        _kalshi_candle(0.59, 0.61, _CLOSE - timedelta(minutes=14)),
        _kalshi_candle(0.69, 0.71, _CLOSE - timedelta(minutes=13)),
        _kalshi_candle(0.64, 0.66, _CLOSE - timedelta(minutes=12)),
        _kalshi_candle(0.87, 0.89, entry),                          # first YES mid >= 0.85
        _kalshi_candle(0.88, 0.90, _CLOSE - timedelta(minutes=7)),
        _kalshi_candle(0.90, 0.92, _CLOSE - timedelta(minutes=6)),
    ]
    cfg = BacktestConfig(scan_threshold=0.85, min_tte=5.0, max_tte=13.0)

    def _run(cs):
        client = MagicMock()
        client.get_historical_market_candlesticks.return_value = {"candlesticks": cs}
        return BacktestEngine(client=client, verbose=False)._scan_market(
            "T", "KXBTC15M", _CLOSE - timedelta(minutes=15), _CLOSE,
            no_won=False, split="train", config=cfg,
        )

    baseline = _run(candles)
    assert baseline is not None
    assert baseline.tte_minutes == pytest.approx(8.0, abs=0.01)
    assert _run(_perturb_kalshi_after(candles, entry)) == baseline


def test_momentum_signal_ignores_everything_after_entry():
    entry = _CLOSE - timedelta(minutes=10)
    candles = [_kalshi_candle(0.395, 0.405, _CLOSE - timedelta(minutes=m))
               for m in (20, 19, 18, 17, 16, 15)]
    candles += [
        _kalshi_candle(0.395, 0.405, _CLOSE - timedelta(minutes=12)),   # flat: no signal
        _kalshi_candle(0.455, 0.465, entry),                            # +15%: the entry
        _kalshi_candle(0.50, 0.51, _CLOSE - timedelta(minutes=8)),
    ]
    cfg = MomentumBacktestConfig(momentum_threshold_pct=0.02, history_minutes=10.0,
                                 min_history_points=4, min_tte=5.0, max_tte=13.0)
    market = {
        "ticker": "T", "result": "yes",
        "open_time":  (_CLOSE - timedelta(minutes=15)).isoformat() + "Z",
        "close_time": _CLOSE.isoformat() + "Z",
    }

    def _run(cs):
        client = MagicMock()
        client.get_historical_market_candlesticks.return_value = {"candlesticks": cs}
        return MomentumBacktestEngine(client=client, verbose=False)._scan_market(
            market, "KXBTC15M", cfg)

    baseline = _run(candles)
    assert baseline is not None
    assert baseline.tte_minutes == pytest.approx(10.0, abs=0.01)
    assert _run(_perturb_kalshi_after(candles, entry)) == baseline
