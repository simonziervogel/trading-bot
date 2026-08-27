"""Tests for kalshi.utils.fees — single source of truth for fee calculations."""
import pytest
from kalshi.utils.fees import taker_fee, entry_fee, exit_fee, round_trip_fee


class TestTakerFee:
    def test_typical_no_price(self):
        # raw = 0.07 * 0.15 * 0.85 * 100 = 0.8925 -> rounded up to 0.90
        result = taker_fee(0.15, 100)
        assert abs(result - 0.90) < 1e-9

    def test_boundary_zero_price(self):
        assert taker_fee(0.0, 100) == 0.0

    def test_boundary_one_price(self):
        assert taker_fee(1.0, 100) == 0.0

    def test_symmetric_around_half(self):
        # Fee is symmetric: taker_fee(0.3, 1) == taker_fee(0.7, 1)
        assert abs(taker_fee(0.3, 1) - taker_fee(0.7, 1)) < 1e-12

    def test_max_fee_at_half(self):
        # raw = 0.07 * 0.5 * 0.5 = 0.0175 per contract -> rounded up to 0.02
        assert abs(taker_fee(0.5, 1) - 0.02) < 1e-9

    def test_rounds_up_not_down(self):
        # 0.0175 always rounds up to 0.02, never truncates to 0.01
        assert taker_fee(0.5, 1) == 0.02

    def test_rounding_applied_once_on_qty_scaled_total(self):
        # 100 contracts at 50c: raw = 0.07*0.5*0.5*100 = 1.75 exactly (a real
        # Kalshi reference point: "$1.75 per 100 contracts at 50c"). Rounding
        # the total (already a clean cent value) must leave it unchanged —
        # rounding per-contract-then-summing would instead give
        # ceil(0.0175*100)/100 * 100 = 0.02 * 100 = $2.00, which is wrong.
        assert abs(taker_fee(0.5, 100) - 1.75) < 1e-9

    def test_qty_does_not_scale_exactly_linearly_once_rounded(self):
        # Rounding breaks exact linearity for arbitrary qty (expected, not a bug)
        assert taker_fee(0.15, 10) != pytest.approx(10 * taker_fee(0.15, 1))


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
