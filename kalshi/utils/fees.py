"""Kalshi fee calculations — single source of truth for the whole package.

Kalshi charges a taker fee of 7 % of the no-side premium, rounded UP to the
nearest cent on the whole (qty-scaled) order — not per contract then summed:
    fee = ceil(0.07 * price * (1 - price) * qty * 100) / 100

Verified against the live Kalshi API: KXBTC15M and KXETH15M both report
fee_multiplier=1, fee_type="quadratic" via get_series_list() — the same
standard tier as nearly every other series, no elevated "crypto" rate.
The exact rounding rule is corroborated by multiple independent third-party
sources (Kalshi's own fee-schedule PDF was unreachable at verification time
due to rate limiting) — treat as high-but-not-primary-source confidence.

The fee is charged once at entry.  Settlement at expiry (EXPIRED reason) is free.
Any other exit (TP, SL, TIME, session_end) incurs an additional exit fee.
"""

import math


def taker_fee(price: float, qty: int) -> float:
    """Kalshi taker fee for `qty` contracts at `price`, rounded up to the cent.

    Rounding is applied once to the qty-scaled total, matching how Kalshi
    charges a single order — ceil(a * qty) != ceil(a) * qty in general, so
    rounding per-contract-then-multiplying would be wrong for qty > 1.
    """
    raw = 0.07 * price * (1.0 - price) * qty
    return math.ceil(raw * 100 - 1e-9) / 100


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
