"""Market data utilities — candle parsing and market metadata helpers."""


def parse_quote(candle: dict) -> tuple:
    """Extract yes_bid, yes_ask, mid, and source label from a candlestick.

    Priority:
      1. Bid/ask  yes_bid.close, yes_ask.close  — real executable quotes
      2. Last trade  price.close  — fallback when bid/ask are missing; treated
         as both bid and ask (mid == last trade), source flagged so callers
         can tell this isn't a real two-sided quote

    Returns (yes_bid, yes_ask, mid, source), or (None, None, None, 'unavailable').
    """
    yb = candle.get("yes_bid") or {}
    ya = candle.get("yes_ask") or {}
    pb = candle.get("price") or {}

    if yb.get("close") is not None and ya.get("close") is not None:
        bid, ask = float(yb["close"]), float(ya["close"])
        return bid, ask, (bid + ask) / 2.0, "bid_ask"
    if pb.get("close") is not None:
        last = float(pb["close"])
        return last, last, last, "last_trade"
    return None, None, None, "unavailable"


def parse_mid(candle: dict) -> tuple[float | None, str]:
    """Extract YES mid-price from a Kalshi candlestick dict.

    Thin wrapper over parse_quote() for callers that only need the midpoint
    (e.g. signal/threshold checks, which characterize market consensus and
    don't need to be executable). For anything that records an economic
    outcome (entry price feeding win-rate/EV), use parse_quote() directly and
    price at yes_ask (YES) or 1 - yes_bid (NO) — the midpoint is not a real
    fill price.

    Returns (mid_price, source_label) or (None, 'unavailable').
    """
    _bid, _ask, mid, source = parse_quote(candle)
    return mid, source


# Kalshi 15-minute series -> Binance spot symbol used as the underlying proxy.
#
# Single source of truth: both the live strategy (kalshi/strategies/fair_value.py)
# and the backtest engine (kalshi/backtest/fair_value_engine.py) import this.
# They previously kept separate copies, which is how the two silently drift.
#
# Every series here has the same contract structure — strike_type
# "greater_or_equal", settling on "close >= open" over 15 minutes against that
# asset's CF Benchmarks Real-Time Index (BRTI, SOLUSDRTI, XRPUSDRTI, ...).
# Binance spot is a proxy for that index; the basis is tight on BTC/ETH but
# wider and noisier on thin alts, which matters when reading their results.
#
# ADA/BCH/TON also list 15M series but have no historical markets, so they
# cannot be backtested and are deliberately absent.
BINANCE_SYMBOL = {
    "KXBTC15M":  "BTCUSDT",
    "KXETH15M":  "ETHUSDT",
    "KXSOL15M":  "SOLUSDT",
    "KXXRP15M":  "XRPUSDT",
    "KXDOGE15M": "DOGEUSDT",
    "KXBNB15M":  "BNBUSDT",
    "KXHYPE15M": "HYPEUSDT",
    "KXNEAR15M": "NEARUSDT",
    "KXZEC15M":  "ZECUSDT",
}


def get_series(ticker: str) -> str:
    """Extract series prefix from a full Kalshi ticker.

    e.g. 'KXBTC15M-26JUN141100-00' -> 'KXBTC15M'
    """
    return ticker.split("-")[0] if "-" in ticker else ticker


def bucket_label(yes_mid: float) -> str:
    """Return the YES-price bucket label for a given mid-price."""
    buckets = [
        (0.70, 0.75),
        (0.75, 0.80),
        (0.80, 0.85),
        (0.85, 0.90),
        (0.90, 0.95),
        (0.95, 1.00),
    ]
    for lo, hi in buckets:
        if lo <= yes_mid < hi:
            return f"{lo:.2f}-{hi:.2f}"
    return "other"


def time_segment(dt) -> str:
    """Return the named trading session for a UTC datetime.

    Segments (all times UTC):
        us_open_burst   13:30 - 15:30  (US market open, highest volatility)
        us_session      15:30 - 21:00  (remainder of US session)
        eu_session      07:00 - 13:30  (European trading hours)
        off_hours       21:00 - 07:00  (overnight / weekend)
    """
    t = dt.hour * 60.0 + dt.minute + dt.second / 60.0
    if 810 <= t < 930:
        return "us_open_burst"
    if 930 <= t < 1260:
        return "us_session"
    if 420 <= t < 810:
        return "eu_session"
    return "off_hours"
