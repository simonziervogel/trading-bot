"""Mean-reversion strategy for Kalshi 15-min binary markets.

Hypothesis: after a sharp intra-session move, the YES probability should revert
toward the market's prior estimate.  Buy YES after a down-move that shows early
signs of recovery; buy NO after an up-move that shows early signs of decline.

NOTE: This strategy is included as a documented "failed hypothesis" contrast piece.
In production testing the TP/SL parameters produce near-zero edge after fees.
The asymmetric TP/SL (1.5c profit vs 2.5c loss) means the expected value is
negative unless the win-rate is well above 62.5%.  It is retained as an example
of hypothesis-driven strategy design and backtesting methodology.
"""

from kalshi.strategies.base import Strategy


class MeanReversionStrategy(Strategy):
    """Buy YES on recovery from a sell-off; buy NO on recovery from a rally."""

    name = "mean_reversion"

    def __init__(
        self,
        take_profit_cents: float = 1.5,
        stop_loss_cents:   float = 2.5,
        time_stop_minutes: int   = 10,
        momentum_pct:      float = 0.01,
        reversal_pct:      float = 0.003,
        history_minutes:   int   = 5,
        max_positions:     int   = 8,
        position_size_pct: float = 0.01,
    ):
        self.take_profit_cents = take_profit_cents
        self.stop_loss_cents   = stop_loss_cents
        self.time_stop_minutes = time_stop_minutes
        self.momentum_pct      = momentum_pct
        self.reversal_pct      = reversal_pct
        self.history_minutes   = history_minutes
        self.max_positions     = max_positions
        self.position_size_pct = position_size_pct

    def signal(self, ticker: str, price_history: list, market_quotes: dict) -> dict | None:
        if len(price_history) < 10:
            return None

        prices    = [p for _, p in price_history]
        current   = prices[-1]
        peak_high = max(prices)
        peak_low  = min(prices)

        if peak_low <= 0 or peak_high <= 0:
            return None

        range_pct = (peak_high - peak_low) / peak_low
        if range_pct < self.momentum_pct:
            return None

        # Use last occurrence of peak to avoid triggering on a stale historical extreme.
        peak_high_idx  = len(prices) - 1 - prices[::-1].index(peak_high)
        peak_low_idx   = len(prices) - 1 - prices[::-1].index(peak_low)
        last_idx       = len(prices) - 1
        dist_from_high = (peak_high - current) / peak_high
        dist_from_low  = (current   - peak_low) / peak_low

        signal_up   = peak_low_idx  < last_idx and dist_from_low  >= self.reversal_pct
        signal_down = peak_high_idx < last_idx and dist_from_high >= self.reversal_pct

        if not signal_up and not signal_down:
            return None
        if signal_up and signal_down:
            if dist_from_low >= dist_from_high:
                signal_down = False
            else:
                signal_up = False

        if signal_up:
            return {"side": "yes", "type": "reversal_up",
                    "range_pct": range_pct, "dist_from_low": dist_from_low,
                    "peak_low": peak_low, "current": current}
        return {"side": "no", "type": "reversal_down",
                "range_pct": range_pct, "dist_from_high": dist_from_high,
                "peak_high": peak_high, "current": current}

    def config_params(self) -> dict:
        return {
            "strategy":          self.name,
            "take_profit_cents": self.take_profit_cents,
            "stop_loss_cents":   self.stop_loss_cents,
            "time_stop_minutes": self.time_stop_minutes,
            "momentum_pct":      self.momentum_pct,
            "reversal_pct":      self.reversal_pct,
            "history_minutes":   self.history_minutes,
            "max_positions":     self.max_positions,
            "position_size_pct": self.position_size_pct,
        }
