"""Fair-value (digital-option mispricing) strategy for Kalshi 15-min binary markets.

Kalshi's KXBTC15M/KXETH15M contracts resolve YES if the reference price at
close is >= (or <=) a strike fixed at market open — i.e. cash-or-nothing
digital options on "does the price finish above its own opening level."
That gives a closed-form fair probability (see kalshi.utils.pricing) computed
independently from Kalshi's own quote. When the two disagree by more than the
round-trip cost, there's a tradeable edge.
"""

import requests
from datetime import datetime, timezone

from kalshi.strategies.base import Strategy
from kalshi.utils.pricing import digital_call_probability, realized_vol_annualized
from kalshi.utils.fees import taker_fee


_BINANCE_SYMBOL = {"KXBTC15M": "BTCUSDT", "KXETH15M": "ETHUSDT"}
_VOL_WINDOW_MINUTES = 60

# Module-level Binance klines cache: symbol -> (closes, fetched_at)
_KLINES_CACHE: dict[str, tuple[list, datetime]] = {}
_KLINES_CACHE_TTL = 30.0


def _get_binance_closes(ticker: str, window_minutes: int = _VOL_WINDOW_MINUTES):
    """Return a list of recent 1-min close prices for the ticker's underlying.

    Cached per symbol for 30s to avoid redundant HTTP calls across tickers
    that share the same underlying (e.g. multiple open KXBTC15M contracts).
    """
    series = ticker.split("-")[0] if "-" in ticker else ticker
    symbol = _BINANCE_SYMBOL.get(series)
    if not symbol:
        return None

    now = datetime.now(timezone.utc)
    cached = _KLINES_CACHE.get(symbol)
    if cached is not None:
        closes, fetched_at = cached
        if (now - fetched_at).total_seconds() < _KLINES_CACHE_TTL:
            return closes

    try:
        url = "https://api.binance.com/api/v3/klines"
        r = requests.get(
            url,
            params={"symbol": symbol, "interval": "1m", "limit": window_minutes + 1},
            timeout=3,
        )
        raw = r.json()
        if not raw or len(raw) < 2:
            return None
        closes = [float(c[4]) for c in raw]
        _KLINES_CACHE[symbol] = (closes, now)
        return closes
    except Exception:
        return None


def _parse_expires_at(raw) -> datetime | None:
    if not raw:
        return None
    try:
        if isinstance(raw, str):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return None
    except Exception:
        return None


class FairValueStrategy(Strategy):
    """Trade whichever side (YES/NO) is mispriced vs. a digital-option fair value.

    Fair value comes from a zero-drift lognormal model of the underlying
    (spot from Binance, volatility from trailing realized vol, strike from
    Kalshi's own floor_strike field). Holds to expiry — settlement is free,
    so a held position only ever pays the one entry fee.
    """

    name = "fair_value"

    def __init__(
        self,
        min_edge_pct:            float = 0.03,
        time_stop_minutes:       int   = 14,
        max_positions:           int   = 8,
        position_size_pct:       float = 0.01,
        min_tte_minutes:         float = 2.0,
        max_tte_minutes:         float = 14.0,
        max_spread_cents:        float = 8.0,
        vol_window_minutes:      int   = _VOL_WINDOW_MINUTES,
        no_side_only:            bool  = False,
    ):
        self.min_edge_pct       = min_edge_pct
        self.time_stop_minutes  = time_stop_minutes
        self.max_positions      = max_positions
        self.position_size_pct  = position_size_pct
        self.min_tte_minutes    = min_tte_minutes
        self.max_tte_minutes    = max_tte_minutes
        self.max_spread_cents   = max_spread_cents
        self.vol_window_minutes = vol_window_minutes
        # Live validation runs NO-side-only: that's the side the backtest
        # edge is concentrated on, so trading both sides would spend most
        # position slots on the no-edge YES population. Default off keeps
        # backtest parity.
        self.no_side_only       = no_side_only
        # TradingEngine reads these via getattr; fair_value has no fixed TP/SL
        self.take_profit_cents  = None
        self.stop_loss_cents    = None
        self.history_minutes    = 1

    def signal(self, ticker: str, price_history: list, market_quotes: dict) -> dict | None:
        q = market_quotes.get(ticker)
        if not q:
            return None

        strike      = q.get("floor_strike")
        strike_type = q.get("strike_type")
        if strike is None or not strike_type:
            return None

        yes_bid = q.get("yes_bid_dollars")
        yes_ask = q.get("yes_ask_dollars")
        no_bid  = q.get("no_bid_dollars")
        no_ask  = q.get("no_ask_dollars")
        if yes_bid is None or yes_ask is None or no_bid is None or no_ask is None:
            return None

        try:
            yes_ask_f, yes_bid_f = float(yes_ask), float(yes_bid)
            no_ask_f, no_bid_f   = float(no_ask), float(no_bid)
        except (TypeError, ValueError):
            return None

        spread_cents = (yes_ask_f - yes_bid_f) * 100.0
        if spread_cents > self.max_spread_cents:
            return None

        expires_at = _parse_expires_at(q.get("close_time"))
        if expires_at is None:
            return None
        tte_minutes = (expires_at - datetime.now(timezone.utc)).total_seconds() / 60.0
        if tte_minutes < self.min_tte_minutes or tte_minutes > self.max_tte_minutes:
            return None
        tte_years = tte_minutes / (365.25 * 24 * 60)

        closes = _get_binance_closes(ticker, self.vol_window_minutes)
        if not closes:
            return None
        spot = closes[-1]
        sigma = realized_vol_annualized(closes)
        if sigma is None:
            return None

        p_yes = digital_call_probability(spot, float(strike), tte_years, sigma, strike_type)
        if p_yes is None:
            return None

        edge_yes = p_yes - yes_ask_f - taker_fee(yes_ask_f, 1)
        edge_no  = (1.0 - p_yes) - no_ask_f - taker_fee(no_ask_f, 1)

        if self.no_side_only:
            # Only the NO side carries a validated edge — ignore the YES side
            # entirely rather than letting a larger YES edge win the compare.
            if edge_no < self.min_edge_pct:
                return None
            side, edge = "no", edge_no
        else:
            if edge_yes < self.min_edge_pct and edge_no < self.min_edge_pct:
                return None

            if edge_yes >= edge_no:
                side, edge = "yes", edge_yes
            else:
                side, edge = "no", edge_no

        return {
            "side": side, "type": "fair_value",
            "model_prob": p_yes, "spot": spot, "strike": strike,
            "sigma": sigma, "edge": edge,
        }

    def config_params(self) -> dict:
        return {
            "strategy":           self.name,
            "min_edge_pct":       self.min_edge_pct,
            "time_stop_minutes":  self.time_stop_minutes,
            "max_positions":      self.max_positions,
            "position_size_pct":  self.position_size_pct,
            "min_tte_minutes":    self.min_tte_minutes,
            "max_tte_minutes":    self.max_tte_minutes,
            "max_spread_cents":   self.max_spread_cents,
            "vol_window_minutes": self.vol_window_minutes,
            "no_side_only":       self.no_side_only,
        }
