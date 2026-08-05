"""Abstract base class and Signal dataclass for all trading strategies."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Signal:
    """Entry signal returned by a strategy's signal() method."""
    side: str           # "yes" | "no"
    ticker: str
    yes_mid: float
    metadata: dict = field(default_factory=dict)


class Strategy(ABC):
    """Abstract base for all Kalshi paper trading strategies.

    Subclasses must implement signal() and config_params().
    Strategy-level execution parameters (take_profit_cents, stop_loss_cents,
    time_stop_minutes, max_positions, position_size_pct) are read by the
    TradingEngine via getattr so they must be set as instance attributes.
    """

    name: str = "base"

    @abstractmethod
    def signal(self, ticker: str, price_history: list, market_quotes: dict) -> dict | None:
        """Return an entry signal dict (must include 'side') or None if no signal."""

    @abstractmethod
    def config_params(self) -> dict:
        """Return a dict of all configurable parameters for logging / results JSON."""
