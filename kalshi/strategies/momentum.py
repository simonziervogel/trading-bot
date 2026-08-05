"""Momentum (trend-continuation) strategy for Kalshi 15-min binary markets.

Deliberate mirror image of MeanReversionStrategy: instead of betting a sharp
move reverts, this bets a recent move continues into expiry. Unlike
MeanReversion, this strategy holds to expiry rather than using TP/SL — that
makes it a single-decision, hold-to-expiry hypothesis (like
FavoriteLongshotStrategy), which can be validated with the same rigorous
historical backtest instead of live-only paper trading.
"""

from datetime import datetime, timezone

from kalshi.strategies.base import Strategy


def _parse_expires_at(raw) -> datetime | None:
    if not raw:
        return None
    try:
        if isinstance(raw, str):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return None
    except Exception:
        return None


class MomentumStrategy(Strategy):
    """Buy in the direction of a recent price move and hold to expiry.

    Entry filters (applied in order):
      1. Spread <= max_spread_cents (liquidity gate)
      2. Time-to-expiry in [min_tte_minutes, max_tte_minutes]
      3. At least min_history_points price observations in the trailing window
      4. |relative price change over the window| >= momentum_threshold_pct
    """

    name = "momentum"

    def __init__(
        self,
        momentum_threshold_pct: float = 0.02,
        history_minutes:        int   = 3,
        min_history_points:     int   = 4,
        time_stop_minutes:      int   = 14,
        max_positions:          int   = 8,
        position_size_pct:      float = 0.01,
        min_tte_minutes:        int   = 5,
        max_tte_minutes:        int   = 13,
        max_spread_cents:       float = 8.0,
    ):
        self.momentum_threshold_pct = momentum_threshold_pct
        self.history_minutes        = history_minutes
        self.min_history_points     = min_history_points
        self.time_stop_minutes      = time_stop_minutes
        self.max_positions          = max_positions
        self.position_size_pct      = position_size_pct
        self.min_tte_minutes        = min_tte_minutes
        self.max_tte_minutes        = max_tte_minutes
        self.max_spread_cents       = max_spread_cents
        # TradingEngine reads these via getattr; momentum has no fixed TP/SL
        self.take_profit_cents      = None
        self.stop_loss_cents        = None

    def signal(self, ticker: str, price_history: list, market_quotes: dict) -> dict | None:
        q = market_quotes.get(ticker)
        if not q:
            return None

        yes_bid = q.get("yes_bid_dollars")
        yes_ask = q.get("yes_ask_dollars")
        if yes_bid is None or yes_ask is None:
            return None
        try:
            yes_bid_f, yes_ask_f = float(yes_bid), float(yes_ask)
        except (TypeError, ValueError):
            return None

        spread_cents = (yes_ask_f - yes_bid_f) * 100.0
        if spread_cents > self.max_spread_cents:
            return None

        expires_at = _parse_expires_at(q.get("close_time"))
        if expires_at is None:
            return None
        tte = (expires_at - datetime.now(timezone.utc)).total_seconds() / 60.0
        if tte < self.min_tte_minutes or tte > self.max_tte_minutes:
            return None

        prices = [p for _, p in price_history]
        if len(prices) < self.min_history_points:
            return None

        start, current = prices[0], prices[-1]
        if start <= 0:
            return None

        pct_change = (current - start) / start

        if pct_change >= self.momentum_threshold_pct:
            side = "yes"
        elif pct_change <= -self.momentum_threshold_pct:
            side = "no"
        else:
            return None

        return {"side": side, "type": "momentum", "pct_change": pct_change,
                "window_start": start, "current": current}

    def config_params(self) -> dict:
        return {
            "strategy":               self.name,
            "momentum_threshold_pct": self.momentum_threshold_pct,
            "history_minutes":        self.history_minutes,
            "min_history_points":     self.min_history_points,
            "time_stop_minutes":      self.time_stop_minutes,
            "max_positions":          self.max_positions,
            "position_size_pct":      self.position_size_pct,
            "min_tte_minutes":        self.min_tte_minutes,
            "max_tte_minutes":        self.max_tte_minutes,
            "max_spread_cents":       self.max_spread_cents,
        }
