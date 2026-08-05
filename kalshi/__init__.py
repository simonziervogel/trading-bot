"""
kalshi — Algorithmic paper trading and backtesting for Kalshi prediction markets.

Package layout:
    kalshi.client           Authenticated Kalshi API client
    kalshi.utils.time       UTC datetime helpers
    kalshi.utils.fees       Fee calculations (entry, exit, EXPIRED-aware)
    kalshi.utils.market     Candle mid-price parsing (single source of truth)
    kalshi.data.historical  Historical candlestick fetching
    kalshi.data.live        Live market data fetching
    kalshi.strategies       Strategy base class + implementations
    kalshi.backtest         Backtesting engine, metrics, charts
    kalshi.live             Paper trading engine
    kalshi.db               SQLite schema and results ingestion
"""

__version__ = "0.1.0"
