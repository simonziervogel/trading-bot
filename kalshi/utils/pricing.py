"""Digital-option pricing — closed-form probability model, no external dependencies.

Kalshi's 15-min BTC/ETH contracts resolve YES if the reference price at close is
>= (or <=) a strike set at market open (see rules_primary on the market object).
That makes them cash-or-nothing digital options: a zero-drift lognormal model of
the underlying gives a closed-form "fair" probability of finishing in the money,
directly comparable to Kalshi's quoted price.
"""

import math
from typing import Optional


def normal_cdf(x: float) -> float:
    """Standard normal CDF N(x), via the error function (stdlib math.erf)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def digital_call_probability(
    spot: float,
    strike: float,
    tte_years: float,
    sigma: float,
    strike_type: str = "greater_or_equal",
) -> Optional[float]:
    """Zero-drift lognormal probability that the underlying finishes ITM.

    d2 = (ln(S/K) - 0.5*sigma^2*tau) / (sigma*sqrt(tau))
    P(finish >= K) = N(d2); P(finish <= K) = 1 - N(d2)

    Returns None (fail closed) for non-positive inputs or an unrecognized
    strike_type, rather than guessing — a wrong strike direction would silently
    trade the wrong side.
    """
    if spot is None or strike is None:
        return None
    if spot <= 0 or strike <= 0 or tte_years <= 0 or sigma <= 0:
        return None

    d2 = (math.log(spot / strike) - 0.5 * sigma * sigma * tte_years) / (
        sigma * math.sqrt(tte_years)
    )
    p_above = normal_cdf(d2)

    if strike_type == "greater_or_equal":
        return p_above
    if strike_type == "less_or_equal":
        return 1.0 - p_above
    return None


def realized_vol_annualized(closes: list, interval_minutes: float = 1.0) -> Optional[float]:
    """Annualized realized volatility from a sequence of close prices.

    Uses the stdev of consecutive log returns, scaled by sqrt(minutes per year
    / interval_minutes). Returns None if fewer than 2 usable returns.
    """
    if len(closes) < 3:
        return None

    log_returns = []
    for prev, cur in zip(closes, closes[1:]):
        if prev is None or cur is None or prev <= 0 or cur <= 0:
            continue
        log_returns.append(math.log(cur / prev))

    n = len(log_returns)
    if n < 2:
        return None

    mean = sum(log_returns) / n
    variance = sum((r - mean) ** 2 for r in log_returns) / (n - 1)
    stdev = math.sqrt(variance)
    if stdev <= 0:
        return None

    minutes_per_year = 365.25 * 24 * 60
    return stdev * math.sqrt(minutes_per_year / interval_minutes)
