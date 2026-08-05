"""Tests for kalshi.utils.pricing — digital-option probability model."""
import math
import pytest
from kalshi.utils.pricing import normal_cdf, digital_call_probability, realized_vol_annualized


class TestNormalCdf:
    def test_zero_is_half(self):
        assert abs(normal_cdf(0.0) - 0.5) < 1e-9

    def test_symmetric(self):
        assert abs(normal_cdf(1.0) + normal_cdf(-1.0) - 1.0) < 1e-9

    def test_large_positive_approaches_one(self):
        assert normal_cdf(10.0) > 0.999999

    def test_large_negative_approaches_zero(self):
        assert normal_cdf(-10.0) < 0.000001


class TestDigitalCallProbability:
    def test_at_the_money_is_near_half(self):
        # spot == strike, zero drift -> d2 has only the -0.5*sigma^2*tau term,
        # which is small for short-dated/moderate-vol inputs -> P close to 0.5
        p = digital_call_probability(spot=100.0, strike=100.0, tte_years=0.001, sigma=0.5)
        assert abs(p - 0.5) < 0.05

    def test_deep_in_the_money_approaches_one(self):
        p = digital_call_probability(spot=200.0, strike=100.0, tte_years=0.01, sigma=0.5)
        assert p > 0.999

    def test_deep_out_of_the_money_approaches_zero(self):
        p = digital_call_probability(spot=50.0, strike=100.0, tte_years=0.01, sigma=0.5)
        assert p < 0.001

    def test_less_or_equal_is_complement(self):
        p_ge = digital_call_probability(100.0, 90.0, 0.01, 0.4, strike_type="greater_or_equal")
        p_le = digital_call_probability(100.0, 90.0, 0.01, 0.4, strike_type="less_or_equal")
        assert abs(p_ge + p_le - 1.0) < 1e-9

    def test_unknown_strike_type_returns_none(self):
        assert digital_call_probability(100.0, 100.0, 0.01, 0.3, strike_type="weird") is None

    def test_non_positive_sigma_returns_none(self):
        assert digital_call_probability(100.0, 100.0, 0.01, 0.0) is None

    def test_non_positive_tte_returns_none(self):
        assert digital_call_probability(100.0, 100.0, 0.0, 0.3) is None

    def test_none_inputs_return_none(self):
        assert digital_call_probability(None, 100.0, 0.01, 0.3) is None
        assert digital_call_probability(100.0, None, 0.01, 0.3) is None


class TestRealizedVolAnnualized:
    def test_zero_variance_returns_none(self):
        # Constant price series -> zero stdev of log returns -> None (not 0.0)
        assert realized_vol_annualized([100.0] * 10) is None

    def test_too_few_points_returns_none(self):
        assert realized_vol_annualized([100.0, 101.0]) is None

    def test_positive_for_varying_series(self):
        closes = [100.0, 101.0, 99.5, 100.8, 99.9, 101.2, 100.1]
        vol = realized_vol_annualized(closes)
        assert vol is not None
        assert vol > 0.0

    def test_ignores_invalid_prices(self):
        closes = [100.0, 0.0, 101.0, -5.0, 99.0, 100.5]
        vol = realized_vol_annualized(closes)
        assert vol is None or vol > 0.0
