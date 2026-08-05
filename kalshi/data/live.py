"""Live Market Data Fetcher — polls the Kalshi Production API (public endpoints).

No authentication required for:
- GET /markets — list all markets or filter by series
- GET /markets/{ticker}/orderbook — fetch current orderbook

This enables realistic paper trading on REAL market data.
"""
import requests
import time
from typing import Dict, List, Optional


class LiveMarketDataFetcher:
    """Fetches live market data from Kalshi Production API (public endpoints)."""

    def __init__(self, base_url: str = "https://api.elections.kalshi.com/trade-api/v2"):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.timeout = 10

        # Cache to reduce API calls and respect rate limits
        self.market_cache = {}  # {series_ticker: (markets, timestamp)}
        self.orderbook_cache = {}  # {ticker: (orderbook, timestamp)}
        self.cache_ttl_seconds = 10  # Refresh cache every 10 seconds (reduces 429s on production)

        # Diagnostics
        self.fetch_count = 0
        self.error_count = 0

    def _is_cache_fresh(self, cached_time: float) -> bool:
        """Check if cached data is still fresh."""
        return time.time() - cached_time < self.cache_ttl_seconds

    def _filter_liquid_markets(self, markets: List[Dict]) -> List[Dict]:
        """
        Filter out illiquid markets (zero or missing bid/ask).

        Returns only markets where both YES bid and YES ask are non-zero,
        indicating actual liquidity and valid quotes.
        """
        return [
            m for m in markets
            if m.get("yes_bid_dollars") and m.get("yes_ask_dollars") and
               float(m.get("yes_bid_dollars", 0)) > 0 and
               float(m.get("yes_ask_dollars", 0)) > 0
        ]

    def fetch_live_markets(
        self,
        series_ticker: str = "KXBTC15M",
        status: Optional[str] = None,
        use_cache: bool = True
    ) -> List[Dict]:
        """
        Fetch all active markets for a given series from Production API.

        Args:
            series_ticker: e.g. "KXBTC15M" for 15-min Bitcoin markets
            status: optional API status filter; omit to fetch all markets in the series
            use_cache: use cache if fresh

        Returns:
            List of market dicts, each with keys:
            - ticker: e.g. "KXBTC15M-26MAY060500-00"
            - yes_ask_dollars, yes_bid_dollars
            - no_ask_dollars, no_bid_dollars
            - created_at, expires_at
        """
        cache_key = f"{series_ticker}:{status or 'all'}"

        # Try cache first
        if use_cache and cache_key in self.market_cache:
            markets, cached_at = self.market_cache[cache_key]
            if self._is_cache_fresh(cached_at):
                return markets

        try:
            url = f"{self.base_url}/markets"
            params = {
                "series_ticker": series_ticker,
                "limit": 100  # Fetch up to 100 markets per request
            }
            # Only send a status filter when the caller explicitly asks for one.
            if status:
                params["status"] = status

            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            markets = data.get("markets", [])

            # Filter out illiquid markets (bid/ask = 0.0, no quotes)
            liquid_markets = self._filter_liquid_markets(markets)

            # Update cache
            self.market_cache[cache_key] = (liquid_markets, time.time())
            self.fetch_count += 1

            return liquid_markets

        except requests.RequestException as e:
            self.error_count += 1
            print(f"[ERROR] Failed to fetch markets: {e}")
            # Return cached data if available, even if stale
            if cache_key in self.market_cache:
                markets, _ = self.market_cache[cache_key]
                return markets
            return []

    def fetch_orderbook(
        self,
        ticker: str,
        use_cache: bool = True
    ) -> Optional[Dict]:
        """
        Fetch live orderbook for a specific market.

        Args:
            ticker: market ticker, e.g. "KXBTC15M-26MAY060500-00"
            use_cache: use cache if fresh

        Returns:
            Orderbook dict with keys:
            - orderbook_fp: {
                "yes_dollars": [[price, volume], ...],
                "no_dollars": [[price, volume], ...]
              }
        """
        # Try cache first
        if use_cache and ticker in self.orderbook_cache:
            orderbook, cached_at = self.orderbook_cache[ticker]
            if self._is_cache_fresh(cached_at):
                return orderbook

        try:
            url = f"{self.base_url}/markets/{ticker}/orderbook"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()

            orderbook = response.json()

            # Update cache
            self.orderbook_cache[ticker] = (orderbook, time.time())
            self.fetch_count += 1

            return orderbook

        except requests.RequestException as e:
            self.error_count += 1
            print(f"[ERROR] Failed to fetch orderbook for {ticker}: {e}")
            # Return cached data if available
            if ticker in self.orderbook_cache:
                orderbook, _ = self.orderbook_cache[ticker]
                return orderbook
            return None

    def get_best_bid_ask(self, ticker: str) -> tuple:
        """
        Get best bid/ask prices for a market from live orderbook.

        Returns:
            (yes_bid, yes_ask, no_bid, no_ask) or (None, None, None, None) if unavailable
        """
        orderbook = self.fetch_orderbook(ticker)
        if not orderbook:
            return (None, None, None, None)

        try:
            ob = orderbook.get("orderbook_fp", {})

            # Parse YES side: bids are in yes_dollars[0], asks in yes_dollars[1:]
            yes_prices = ob.get("yes_dollars", [])
            yes_bid = float(yes_prices[0][0]) if yes_prices else None
            yes_ask = float(yes_prices[0][0]) if yes_prices else None

            # Parse NO side
            no_prices = ob.get("no_dollars", [])
            no_bid = float(no_prices[0][0]) if no_prices else None
            no_ask = float(no_prices[0][0]) if no_prices else None

            return (yes_bid, yes_ask, no_bid, no_ask)

        except (KeyError, ValueError, IndexError):
            return (None, None, None, None)

    def get_stats(self) -> Dict:
        """Return diagnostic stats."""
        return {
            "fetch_count": self.fetch_count,
            "error_count": self.error_count,
            "cached_markets": len(self.market_cache),
            "cached_orderbooks": len(self.orderbook_cache),
        }
