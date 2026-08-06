"""Tests for sharpe_ratio() in kalshi.backtest.metrics and kalshi.backtest.common_metrics."""
import pytest

from kalshi.backtest.metrics import sharpe_ratio as no_sharpe_ratio
from kalshi.backtest.common_metrics import sharpe_ratio as side_sharpe_ratio


def _no_obs(no_price, no_won, entry_time_utc):
    return {"no_price": no_price, "no_won": no_won, "entry_time_utc": entry_time_utc}


def _side_obs(side, entry_price, side_won, entry_time_utc):
    return {"side": side, "entry_price": entry_price, "side_won": side_won,
            "entry_time_utc": entry_time_utc}


class TestNoSideSharpe:
    def test_too_few_observations_returns_none(self):
        result = no_sharpe_ratio([_no_obs(0.10, True, "2026-01-01T00:00:00")])
        assert result["per_trade"] is None
        assert result["annualized"] is None

    def test_zero_variance_returns_none(self):
        # Every trade identical (same price, same outcome) -> zero-variance PnL series
        obs = [_no_obs(0.10, True, f"2026-01-0{i}T00:00:00") for i in range(1, 6)]
        result = no_sharpe_ratio(obs)
        assert result["per_trade"] is None

    def test_mixed_outcomes_produce_finite_sharpe(self):
        obs = [
            _no_obs(0.10, True,  "2026-01-01T00:00:00"),
            _no_obs(0.10, False, "2026-01-02T00:00:00"),
            _no_obs(0.10, True,  "2026-01-03T00:00:00"),
            _no_obs(0.10, True,  "2026-01-04T00:00:00"),
            _no_obs(0.10, False, "2026-01-05T00:00:00"),
        ]
        result = no_sharpe_ratio(obs)
        assert result["per_trade"] is not None
        assert result["trades_per_year"] is not None
        assert result["annualized"] is not None
        # Win rate 60% at a cheap 10c price should be a strongly positive Sharpe
        assert result["per_trade"] > 0

    def test_zero_time_span_gives_no_annualization(self):
        # All trades at the exact same timestamp -> span_days == 0
        obs = [
            _no_obs(0.10, True,  "2026-01-01T00:00:00"),
            _no_obs(0.20, False, "2026-01-01T00:00:00"),
            _no_obs(0.10, True,  "2026-01-01T00:00:00"),
        ]
        result = no_sharpe_ratio(obs)
        assert result["annualized"] is None
        assert result["trades_per_year"] is None


class TestSideAwareSharpe:
    def test_too_few_observations_returns_none(self):
        result = side_sharpe_ratio([_side_obs("yes", 0.5, True, "2026-01-01T00:00:00")])
        assert result["per_trade"] is None

    def test_zero_variance_returns_none(self):
        obs = [_side_obs("no", 0.30, True, f"2026-01-0{i}T00:00:00") for i in range(1, 6)]
        result = side_sharpe_ratio(obs)
        assert result["per_trade"] is None

    def test_mixed_sides_and_outcomes(self):
        obs = [
            _side_obs("no",  0.30, True,  "2026-01-01T00:00:00"),
            _side_obs("yes", 0.60, False, "2026-01-05T00:00:00"),
            _side_obs("no",  0.30, True,  "2026-01-10T00:00:00"),
            _side_obs("yes", 0.55, True,  "2026-01-15T00:00:00"),
        ]
        result = side_sharpe_ratio(obs)
        assert result["per_trade"] is not None
        assert result["trades_per_year"] is not None
        assert result["annualized"] == pytest.approx(
            result["per_trade"] * (result["trades_per_year"] ** 0.5)
        )
