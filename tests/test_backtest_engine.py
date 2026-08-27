"""Tests for kalshi.backtest.engine — no-lookahead guarantee and split logic."""
import pytest
from datetime import datetime, timezone, date, timedelta
from unittest.mock import MagicMock

from kalshi.backtest.engine import BacktestConfig, BacktestEngine, Observation


# ---------------------------------------------------------------------------
# Helpers: minimal mock candle data
# ---------------------------------------------------------------------------

_EPOCH = datetime(1970, 1, 1)   # naive UTC epoch reference


def _utc_epoch(naive_utc: datetime) -> int:
    """Convert a naive UTC datetime to Unix seconds without using .timestamp()
    (which would interpret naive datetimes as local time on some platforms)."""
    return int((naive_utc - _EPOCH).total_seconds())


def _candle(yes_bid, yes_ask, end_naive_utc: datetime):
    """Build a minimal historical candlestick dict as returned by the Kalshi API.

    end_naive_utc must be a naive UTC datetime (no tzinfo).  The engine's
    to_naive_utc() returns naive UTC, so _scan_market computes TTE as
    (naive_market_end - naive_candle_time) — both sides must be naive to avoid
    TypeError.
    """
    return {
        "yes_bid": {"close": yes_bid},
        "yes_ask": {"close": yes_ask},
        "price":   {"close": (yes_bid + yes_ask) / 2},
        "end_period_ts": _utc_epoch(end_naive_utc),
    }


def _market(ticker, close_naive_utc: datetime, result="yes"):
    """Build a minimal historical market dict."""
    open_dt = close_naive_utc - timedelta(minutes=15)
    return {
        "ticker":     ticker,
        "result":     result,
        "volume_fp":  "5000",
        "open_time":  open_dt.isoformat() + "Z",
        "close_time": close_naive_utc.isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# BacktestConfig
# ---------------------------------------------------------------------------

class TestBacktestConfig:
    def test_defaults(self):
        cfg = BacktestConfig()
        assert cfg.series == ["KXBTC15M", "KXETH15M"]
        assert cfg.scan_threshold == 0.70
        assert cfg.min_tte == 5.0
        assert cfg.max_tte == 13.0
        assert cfg.min_volume == 1000.0
        assert cfg.max_markets == 500
        assert cfg.test_after is None

    def test_custom_values(self):
        cfg = BacktestConfig(series=["KXBTC15M"], max_markets=50, scan_threshold=0.85)
        assert cfg.series == ["KXBTC15M"]
        assert cfg.max_markets == 50
        assert cfg.scan_threshold == 0.85


# ---------------------------------------------------------------------------
# Observation.to_dict
# ---------------------------------------------------------------------------

class TestObservation:
    def test_to_dict_no_won_as_int(self):
        obs = Observation(
            ticker="T", series="KXBTC15M",
            market_close_date="2026-06-01",
            entry_time_utc="2026-06-01T10:00:00",
            yes_mid=0.88, no_price=0.12,
            yes_bid=0.87, yes_ask=0.89, spread_cents=2.0,
            tte_minutes=7.5, no_won=False,
            yes_bucket="0.85-0.90", time_segment="us_open_burst",
            pre_entry_vol=0.02, price_source="bid_ask", split="train",
        )
        d = obs.to_dict()
        assert d["no_won"] == 0       # bool converted to int for CSV compat
        assert d["ticker"] == "T"
        assert d["split"] == "train"

    def test_to_dict_keys(self):
        obs = Observation(
            ticker="T", series="S",
            market_close_date="2026-06-01", entry_time_utc="2026-06-01T10:00:00",
            yes_mid=0.9, no_price=0.1,
            yes_bid=0.89, yes_ask=0.91, spread_cents=2.0,
            tte_minutes=7.0, no_won=True,
            yes_bucket="0.85-0.90", time_segment="off_hours",
            pre_entry_vol=0.0, price_source="bid_ask", split="test",
        )
        keys = set(obs.to_dict().keys())
        expected = {
            "ticker", "series", "market_close_date", "entry_time_utc",
            "yes_mid", "no_price", "yes_bid", "yes_ask", "spread_cents",
            "tte_minutes", "no_won", "yes_bucket",
            "time_segment", "pre_entry_vol", "price_source", "split",
        }
        assert keys == expected

    def test_no_price_is_executable_not_midpoint(self):
        # no_price must be 1 - yes_bid, NOT 1 - yes_mid — a NO contract is
        # bought against the YES bid, never at the midpoint.
        obs = Observation(
            ticker="T", series="S",
            market_close_date="2026-06-01", entry_time_utc="2026-06-01T10:00:00",
            yes_mid=0.80, no_price=0.25,   # mid=0.80 but bid=0.75 -> no_price=1-0.75=0.25
            yes_bid=0.75, yes_ask=0.85, spread_cents=10.0,
            tte_minutes=7.0, no_won=True,
            yes_bucket="0.75-0.80", time_segment="off_hours",
            pre_entry_vol=0.0, price_source="bid_ask", split="train",
        )
        assert obs.no_price == pytest.approx(1.0 - obs.yes_bid)
        assert obs.no_price != pytest.approx(1.0 - obs.yes_mid)


# ---------------------------------------------------------------------------
# BacktestEngine._scan_market — no-lookahead guarantee
# ---------------------------------------------------------------------------

class TestScanMarket:
    """Tests _scan_market in isolation via a mocked KalshiClient."""

    def _make_engine(self, candles):
        mock_client = MagicMock()
        mock_client.get_historical_market_candlesticks.return_value = {
            "candlesticks": candles
        }
        engine = BacktestEngine(client=mock_client, verbose=False)
        return engine

    def test_returns_none_with_too_few_candles(self):
        engine = self._make_engine(candles=[])
        close = datetime(2026, 6, 1, 12, 0, 0)   # naive UTC — matches to_naive_utc output
        cfg   = BacktestConfig()
        obs   = engine._scan_market(
            "T", "KXBTC15M",
            close - timedelta(minutes=15), close, no_won=False, split="train", config=cfg,
        )
        assert obs is None

    def test_first_qualifying_candle_only(self):
        """Only the FIRST candle that qualifies becomes the signal — not subsequent ones."""
        close = datetime(2026, 6, 1, 14, 0, 0)   # naive UTC

        # Two candles qualify (TTE ~ 9 and 7 minutes). Engine must return the first.
        c1 = _candle(0.87, 0.89, close - timedelta(minutes=9))   # qualifies 1st
        c2 = _candle(0.88, 0.90, close - timedelta(minutes=7))   # would qualify 2nd
        c3 = _candle(0.50, 0.52, close - timedelta(minutes=12))  # pre-signal candle

        engine = self._make_engine([c3, c1, c2])   # unsorted to test sort logic
        cfg    = BacktestConfig(scan_threshold=0.85, min_tte=5.0, max_tte=13.0)

        obs = engine._scan_market(
            "T", "KXBTC15M",
            close - timedelta(minutes=15), close, no_won=True, split="train", config=cfg,
        )
        assert obs is not None
        assert obs.tte_minutes == pytest.approx(9.0, abs=0.1)

    def test_recorded_price_is_executable_ask_bid_not_mid(self):
        """no_price must come from yes_bid, not the mid used for the threshold check."""
        close = datetime(2026, 6, 1, 14, 0, 0)
        pre1 = _candle(0.40, 0.42, close - timedelta(minutes=12))
        pre2 = _candle(0.41, 0.43, close - timedelta(minutes=10))
        # yes_bid=0.85, yes_ask=0.95 -> mid=0.90 (qualifies >= 0.85 threshold)
        # but the real NO fill price is 1 - yes_bid = 0.15, not 1 - mid = 0.10
        sig = _candle(0.85, 0.95, close - timedelta(minutes=9))

        engine = self._make_engine([pre1, pre2, sig])
        cfg    = BacktestConfig(scan_threshold=0.85, min_tte=5.0, max_tte=13.0)
        obs = engine._scan_market(
            "T", "KXBTC15M",
            close - timedelta(minutes=15), close, no_won=True, split="train", config=cfg,
        )
        assert obs is not None
        assert obs.yes_bid == pytest.approx(0.85)
        assert obs.yes_ask == pytest.approx(0.95)
        assert obs.no_price == pytest.approx(0.15)     # 1 - yes_bid
        assert obs.no_price != pytest.approx(0.10)      # NOT 1 - mid
        assert obs.spread_cents == pytest.approx(10.0)

    def test_no_lookahead_pre_entry_vol(self):
        """pre_entry_vol must only use candles that arrived BEFORE the signal candle."""
        close = datetime(2026, 6, 1, 14, 0, 0)   # naive UTC

        # Pre-signal candles with varying YES mids
        pre1 = _candle(0.59, 0.61, close - timedelta(minutes=14))  # mid 0.60
        pre2 = _candle(0.69, 0.71, close - timedelta(minutes=13))  # mid 0.70
        pre3 = _candle(0.64, 0.66, close - timedelta(minutes=12))  # mid 0.65

        # Signal candle: YES mid = 0.88 at TTE = 8 min
        sig  = _candle(0.87, 0.89, close - timedelta(minutes=8))

        engine = self._make_engine([pre1, pre2, pre3, sig])
        cfg    = BacktestConfig(scan_threshold=0.85, min_tte=5.0, max_tte=13.0)

        obs = engine._scan_market(
            "T", "KXBTC15M",
            close - timedelta(minutes=15), close, no_won=False, split="train", config=cfg,
        )
        assert obs is not None
        # pre_mids = [0.60, 0.70, 0.65] -> range = max - min = 0.70 - 0.60 = 0.10
        # Signal candle's mid (0.88) must NOT be included in pre_entry_vol
        assert obs.pre_entry_vol == pytest.approx(0.10, abs=0.01)

    def test_bad_timestamp_skipped_not_added_to_pre_mids(self):
        """A candle with an unparseable timestamp must not contaminate pre_mids."""
        close = datetime(2026, 6, 1, 14, 0, 0)   # naive UTC
        bad   = {"yes_bid": {"close": 0.95}, "yes_ask": {"close": 0.97},
                 "price": {"close": 0.96}, "end_period_ts": "not_a_number"}
        pre   = _candle(0.60, 0.62, close - timedelta(minutes=12))   # mid 0.61
        sig   = _candle(0.87, 0.89, close - timedelta(minutes=8))

        engine = self._make_engine([bad, pre, sig])
        cfg    = BacktestConfig(scan_threshold=0.85, min_tte=5.0, max_tte=13.0)

        obs = engine._scan_market(
            "T", "KXBTC15M",
            close - timedelta(minutes=15), close, no_won=False, split="train", config=cfg,
        )
        assert obs is not None
        # bad candle skipped entirely (unparseable ts) — only pre contributes mid=0.61
        # pre_mids = [0.61] -> only 1 element -> vol = 0.0 (need >= 2 for range)
        assert obs.pre_entry_vol == 0.0


# ---------------------------------------------------------------------------
# BacktestEngine._determine_split
# ---------------------------------------------------------------------------

class TestDetermineplit:
    def setup_method(self):
        self.engine = BacktestEngine(client=MagicMock(), verbose=False)

    def test_no_split_date_returns_train(self):
        cfg = BacktestConfig(test_after=None)
        dt  = datetime(2026, 6, 1, tzinfo=timezone.utc)
        assert self.engine._determine_split(dt, cfg) == "train"

    def test_before_split_is_train(self):
        cfg = BacktestConfig(test_after=date(2026, 4, 1))
        dt  = datetime(2026, 3, 31, tzinfo=timezone.utc)
        assert self.engine._determine_split(dt, cfg) == "train"

    def test_on_split_date_is_test(self):
        cfg = BacktestConfig(test_after=date(2026, 4, 1))
        dt  = datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert self.engine._determine_split(dt, cfg) == "test"

    def test_after_split_is_test(self):
        cfg = BacktestConfig(test_after=date(2026, 4, 1))
        dt  = datetime(2026, 5, 15, tzinfo=timezone.utc)
        assert self.engine._determine_split(dt, cfg) == "test"
