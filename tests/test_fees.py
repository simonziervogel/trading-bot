"""Tests for kalshi.utils.fees — single source of truth for fee calculations."""
import pytest
from kalshi.utils.fees import taker_fee, entry_fee, exit_fee, round_trip_fee


class TestTakerFee:
    def test_typical_no_price(self):
        # 0.07 * 0.15 * 0.85 * 100 = 0.8925
        result = taker_fee(0.15, 100)
        assert abs(result - 0.07 * 0.15 * 0.85 * 100) < 1e-9

    def test_boundary_zero_price(self):
        assert taker_fee(0.0, 100) == 0.0

    def test_boundary_one_price(self):
        assert taker_fee(1.0, 100) == 0.0

    def test_symmetric_around_half(self):
        # Fee is symmetric: taker_fee(0.3, 1) == taker_fee(0.7, 1)
        assert abs(taker_fee(0.3, 1) - taker_fee(0.7, 1)) < 1e-12

    def test_max_fee_at_half(self):
        # 0.07 * 0.5 * 0.5 = 0.0175 per contract
        assert abs(taker_fee(0.5, 1) - 0.07 * 0.25) < 1e-9

    def test_qty_scales_linearly(self):
        assert abs(taker_fee(0.15, 10) - 10 * taker_fee(0.15, 1)) < 1e-9


class TestEntryFee:
    def test_equals_taker_fee(self):
        assert entry_fee(0.20, 50) == taker_fee(0.20, 50)


class TestExitFee:
    def test_expired_is_free(self):
        assert exit_fee(0.15, 100, "EXPIRED") == 0.0

    def test_tp_charges_fee(self):
        assert exit_fee(0.15, 100, "TP") == taker_fee(0.15, 100)

    def test_sl_charges_fee(self):
        assert exit_fee(0.15, 100, "SL") == taker_fee(0.15, 100)

    def test_time_charges_fee(self):
        assert exit_fee(0.15, 100, "TIME") == taker_fee(0.15, 100)

    def test_session_end_charges_fee(self):
        assert exit_fee(0.15, 100, "session_end") == taker_fee(0.15, 100)


class TestRoundTripFee:
    def test_expired_is_entry_only(self):
        result = round_trip_fee(0.10, 0.05, 10, "EXPIRED")
        assert abs(result - entry_fee(0.10, 10)) < 1e-9

    def test_tp_is_entry_plus_exit(self):
        result = round_trip_fee(0.10, 0.90, 10, "TP")
        expected = entry_fee(0.10, 10) + exit_fee(0.90, 10, "TP")
        assert abs(result - expected) < 1e-9
