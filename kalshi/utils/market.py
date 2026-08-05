"""Market data utilities — candle parsing and market metadata helpers."""


def parse_mid(candle: dict) -> tuple[float | None, str]:
    """Extract YES mid-price from a Kalshi candlestick dict.

    Priority:
      1. Bid/ask mid  (yes_bid.close + yes_ask.close) / 2  — reflects live quotes
      2. Last trade   price.close                           — fallback, may be stale

    Returns (mid_price, source_label) or (None, 'unavailable').
    """
    yb = candle.get("yes_bid") or {}
    ya = candle.get("yes_ask") or {}
    pb = candle.get("price") or {}

    if yb.get("close") is not None and ya.get("close") is not None:
        return (float(yb["close"]) + float(ya["close"])) / 2.0, "bid_ask"
    if pb.get("close") is not None:
        return float(pb["close"]), "last_trade"
    return None, "unavailable"


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
