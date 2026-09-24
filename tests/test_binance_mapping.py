"""Tests for the Kalshi-series -> Binance-symbol mapping.

This mapping used to be duplicated across the live strategy and the backtest
engine. That is precisely how a live run and the backtest that validated it
drift apart without anyone noticing, so these tests pin the single source of
truth in place.
"""
import pytest

from kalshi.utils.market import BINANCE_SYMBOL
import kalshi.strategies.fair_value as live_fv
import kalshi.backtest.fair_value_engine as bt_fv
import kalshi.strategies.longshot as live_ls


class TestSingleSourceOfTruth:
    def test_live_and_backtest_share_one_mapping(self):
        """Not just equal — the same object, so they cannot diverge."""
        assert live_fv.BINANCE_SYMBOL is BINANCE_SYMBOL
        assert bt_fv.BINANCE_SYMBOL is BINANCE_SYMBOL
        assert live_ls.BINANCE_SYMBOL is BINANCE_SYMBOL

    def test_no_private_copies_remain(self):
        """A module-level _BINANCE_SYMBOL anywhere means the refactor leaked."""
        for mod in (live_fv, bt_fv, live_ls):
            assert not hasattr(mod, "_BINANCE_SYMBOL"), f"{mod.__name__} kept a private copy"


class TestMappingContents:
    def test_original_series_unchanged(self):
        """The two validated series must keep their existing symbols."""
        assert BINANCE_SYMBOL["KXBTC15M"] == "BTCUSDT"
        assert BINANCE_SYMBOL["KXETH15M"] == "ETHUSDT"

    def test_all_values_are_usdt_pairs(self):
        for series, symbol in BINANCE_SYMBOL.items():
            assert symbol.endswith("USDT"), f"{series} -> {symbol}"
            assert symbol.isupper()

    def test_keys_are_15m_series(self):
        for series in BINANCE_SYMBOL:
            assert series.startswith("KX") and series.endswith("15M"), series

    def test_no_duplicate_symbols(self):
        """Two Kalshi series mapping to one Binance symbol would silently
        price one asset off another's spot."""
        symbols = list(BINANCE_SYMBOL.values())
        assert len(symbols) == len(set(symbols))

    def test_series_without_historical_data_are_absent(self):
        """ADA/BCH/TON list 15M series but have no historical markets, so they
        cannot be backtested — including them would allow live trading of an
        unbacktestable series."""
        for series in ("KXADA15M", "KXBCH15M", "KXTON15M"):
            assert series not in BINANCE_SYMBOL

    def test_unmapped_series_resolves_to_none(self):
        """Callers rely on .get() returning None to skip a series safely."""
        assert BINANCE_SYMBOL.get("KXGOLD15M") is None
