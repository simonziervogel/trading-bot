"""Kalshi fee calculations — single source of truth for the whole package.

Kalshi charges a taker fee of 7 % of the no-side premium:
    fee = 0.07 * no_price * (1 - no_price) * qty

The fee is charged once at entry.  Settlement at expiry (EXPIRED reason) is free.
Any other exit (TP, SL, TIME, session_end) incurs an additional exit fee.
"""


def taker_fee(price: float, qty: int) -> float:
    """Kalshi taker fee for one side of a trade at `price` for `qty` contracts."""
    return 0.07 * price * (1.0 - price) * qty


def entry_fee(price: float, qty: int) -> float:
    """Fee charged when entering a position."""
    return taker_fee(price, qty)


def exit_fee(price: float, qty: int, reason: str) -> float:
    """Fee charged when exiting a position.

    Returns 0 for EXPIRED exits because Kalshi settles expiring contracts
    without charging a second taker fee.
    """
    if reason == "EXPIRED":
        return 0.0
    return taker_fee(price, qty)


def round_trip_fee(entry_price: float, exit_price: float, qty: int, exit_reason: str) -> float:
    """Total fees for a complete trade (entry + exit)."""
    return entry_fee(entry_price, qty) + exit_fee(exit_price, qty, exit_reason)
