"""Tests guarding the live fair-value validation run.

The failure mode these protect against is silent: a config drift between
FairValueStrategy.config_params() and the db ingest registry means every
live session is rejected at ingest time ("config mismatch") and weeks of
paper-trading data never reach trading.db.
"""
import pytest
from datetime import datetime, timezone, timedelta

from kalshi.strategies.fair_value import FairValueStrategy
from kalshi.db.ingest import STRATEGY_REGISTRY, config_matches


# The exact flags run_fair_value_live.bat passes.
LIVE_KWARGS = dict(
    min_edge_pct=0.03,
    time_stop_minutes=14,
    max_positions=8,
    position_size_pct=0.01,
    min_tte_minutes=2.0,
    max_tte_minutes=14.0,
    max_spread_cents=8.0,
    vol_window_minutes=60,
    no_side_only=True,
)


class TestLiveConfigIsIngestible:
    def test_fair_value_is_registered(self):
        assert "fair_value" in STRATEGY_REGISTRY

    def test_live_config_accepted_by_ingest(self):
        cfg = FairValueStrategy(**LIVE_KWARGS).config_params()
        ok, reason = config_matches(cfg)
        assert ok, f"live fair-value config would be rejected at ingest: {reason}"

    def test_registry_keys_all_exist_in_config_params(self):
        """A registry key that config_params() never emits can never match."""
        cfg = FairValueStrategy(**LIVE_KWARGS).config_params()
        missing = set(STRATEGY_REGISTRY["fair_value"]) - set(cfg)
        assert not missing, f"registry keys absent from config_params(): {missing}"

    def test_wrong_tte_window_is_rejected(self):
        """Guards the specific bug this was written for: inheriting Longshot's
        5/13 TTE defaults instead of the validated 2.0/14.0 window."""
        bad = dict(LIVE_KWARGS, min_tte_minutes=5, max_tte_minutes=13)
        ok, reason = config_matches(FairValueStrategy(**bad).config_params())
        assert not ok
        assert "tte" in reason.lower()

    def test_both_sides_config_is_rejected(self):
        """no_side_only=False is a different experiment — must not be
        silently ingested as if it were the pre-registered NO-side run."""
        bad = dict(LIVE_KWARGS, no_side_only=False)
        ok, _ = config_matches(FairValueStrategy(**bad).config_params())
        assert not ok


class TestEntryCutoff:
    """Hold-to-expiry positions must settle naturally, not be force-closed."""

    def test_disabled_by_default(self):
        from kalshi.live.engine import entries_allowed
        # cutoff 0 = off: entries allowed even with seconds left
        assert entries_allowed(remaining_minutes=0.1, cutoff_minutes=0.0) is True

    def test_blocks_entries_inside_cutoff(self):
        from kalshi.live.engine import entries_allowed
        assert entries_allowed(remaining_minutes=10.0, cutoff_minutes=16.0) is False
        assert entries_allowed(remaining_minutes=16.0, cutoff_minutes=16.0) is False

    def test_allows_entries_outside_cutoff(self):
        from kalshi.live.engine import entries_allowed
        assert entries_allowed(remaining_minutes=16.1, cutoff_minutes=16.0) is True
        assert entries_allowed(remaining_minutes=120.0, cutoff_minutes=16.0) is True

    def test_engine_accepts_cutoff_param(self, tmp_path):
        from kalshi.live.engine import PaperTradingEngine
        strategy = FairValueStrategy(**LIVE_KWARGS)
        engine = PaperTradingEngine(
            strategy=strategy, entry_cutoff_minutes=16.0, log_dir=str(tmp_path)
        )
        assert engine.entry_cutoff_minutes == 16.0


def _quotes(ticker, yes_bid, yes_ask, no_bid, no_ask, floor_strike, close_in_min=8):
    close = datetime.now(timezone.utc) + timedelta(minutes=close_in_min)
    return {
        ticker: {
            "yes_bid_dollars": yes_bid, "yes_ask_dollars": yes_ask,
            "no_bid_dollars":  no_bid,  "no_ask_dollars":  no_ask,
            "floor_strike":    floor_strike, "strike_type": "greater_or_equal",
            "close_time":      close.isoformat(),
        }
    }


_FAKE_CLOSES = [65000, 65010, 64995, 65005, 65020, 65000, 64990, 65010, 65005]


class TestNoSideOnly:
    def _patch_binance(self, monkeypatch, closes=_FAKE_CLOSES):
        monkeypatch.setattr(
            "kalshi.strategies.fair_value._get_binance_closes",
            lambda ticker, window_minutes=60: closes,
        )

    def test_yes_edge_is_ignored_when_no_side_only(self, monkeypatch):
        """Spot far ABOVE strike -> model P(yes)~1 -> the YES side has the
        big edge. With no_side_only the strategy must take nothing at all,
        not fall back to the YES trade."""
        self._patch_binance(monkeypatch)
        q = _quotes("T", 0.50, 0.52, 0.47, 0.49, floor_strike=60000.0)

        both = FairValueStrategy(**dict(LIVE_KWARGS, no_side_only=False))
        assert both.signal("T", [], q)["side"] == "yes"   # baseline: takes YES

        no_only = FairValueStrategy(**LIVE_KWARGS)
        assert no_only.signal("T", [], q) is None

    def test_no_edge_still_taken_when_no_side_only(self, monkeypatch):
        """Spot far BELOW strike -> model P(yes)~0 -> the NO side has the
        edge, which is the trade we do want."""
        self._patch_binance(monkeypatch)
        q = _quotes("T", 0.50, 0.52, 0.47, 0.49, floor_strike=70000.0)

        result = FairValueStrategy(**LIVE_KWARGS).signal("T", [], q)
        assert result is not None
        assert result["side"] == "no"

    def test_never_emits_yes_side(self, monkeypatch):
        """Sweep strikes across the whole range — no_side_only must never
        produce a yes signal, regardless of where the model lands."""
        self._patch_binance(monkeypatch)
        strategy = FairValueStrategy(**LIVE_KWARGS)
        for strike in (55000.0, 60000.0, 64000.0, 65000.0, 66000.0, 70000.0, 75000.0):
            q = _quotes("T", 0.50, 0.52, 0.47, 0.49, floor_strike=strike)
            result = strategy.signal("T", [], q)
            if result is not None:
                assert result["side"] == "no", f"emitted yes signal at strike {strike}"
