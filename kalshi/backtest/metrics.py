"""Backtest metrics — win rates, expected value, z-scores, drawdown.

All functions accept a list of Observation objects (or dicts with the same keys).
They return plain Python scalars or dicts — no external dependencies.
"""

import math
from typing import Union


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

Obs = Union[object, dict]   # Observation dataclass or equivalent dict


def _val(o: Obs, key: str):
    """Read a field from either an Observation dataclass or a dict."""
    return o[key] if isinstance(o, dict) else getattr(o, key)


# ---------------------------------------------------------------------------
# Core statistics
# ---------------------------------------------------------------------------

def wilson_ci(n: int, k: int, z: float = 1.96) -> tuple:
    """Wilson score 95% confidence interval for a binomial proportion.

    Returns (ci_lo, ci_hi). Returns (0.0, 0.0) for empty samples.
    """
    if n == 0:
        return 0.0, 0.0
    p      = k / n
    denom  = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def win_rate_with_ci(obs: list) -> tuple:
    """Return (win_rate, ci_lo, ci_hi) for a list of observations.

    A "win" is when no_won == True (or 1).
    """
    n = len(obs)
    if n == 0:
        return 0.0, 0.0, 0.0
    k = sum(1 for o in obs if _val(o, "no_won"))
    wr = k / n
    ci_lo, ci_hi = wilson_ci(n, k)
    return wr, ci_lo, ci_hi


def _taker_fee(no_price: float) -> float:
    """Per-contract entry fee (cents) at a given NO price."""
    return 0.07 * no_price * (1.0 - no_price)


def ev_per_contract(obs: list) -> float:
    """Expected value per contract in dollars.

    EV = (observed_WR - implied_probability) - taker_fee
    Uses mean actual no_price as the implied probability (not bucket midpoint).
    """
    n = len(obs)
    if n == 0:
        return 0.0
    k        = sum(1 for o in obs if _val(o, "no_won"))
    wr       = k / n
    avg_no   = sum(_val(o, "no_price") for o in obs) / n
    edge     = wr - avg_no
    fee      = _taker_fee(avg_no)
    return edge - fee


def z_vs_implied(obs: list) -> float:
    """Z-score: how many standard errors does observed WR deviate from implied?

    Uses avg no_price as the null hypothesis (implied probability).
    Returns 0.0 for empty or degenerate samples.
    """
    n = len(obs)
    if n == 0:
        return 0.0
    k       = sum(1 for o in obs if _val(o, "no_won"))
    wr      = k / n
    avg_no  = sum(_val(o, "no_price") for o in obs) / n
    se      = math.sqrt(avg_no * (1 - avg_no) / n)
    if se <= 0:
        return 0.0
    return (wr - avg_no) / se


def max_drawdown(obs: list) -> float:
    """Maximum drawdown of the NO-side PnL equity curve.

    Each observation contributes +no_price (win) or -no_price (loss).
    Fees are not subtracted here (use ev_per_contract for fee-adjusted EV).
    Returns 0.0 for empty samples.
    """
    if not obs:
        return 0.0
    equity = 0.0
    peak   = 0.0
    max_dd = 0.0
    for o in obs:
        no_price = _val(o, "no_price")
        won      = _val(o, "no_won")
        equity  += no_price if won else -no_price
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


# ---------------------------------------------------------------------------
# Breakdown summaries
# ---------------------------------------------------------------------------

_BUCKETS = [
    "0.70-0.75",
    "0.75-0.80",
    "0.80-0.85",
    "0.85-0.90",
    "0.90-0.95",
    "0.95-1.00",
]

_SEGMENTS = ["us_open_burst", "us_session", "eu_session", "off_hours"]


def summary_by_bucket(obs: list) -> dict:
    """Return metrics keyed by yes_bucket label.

    Each value is a dict with keys: n, k, win_rate, ci_lo, ci_hi, implied,
    edge, ev, z_score.
    """
    result = {}
    for lbl in _BUCKETS:
        bucket = [o for o in obs if _val(o, "yes_bucket") == lbl]
        result[lbl] = _bucket_metrics(bucket)
    return result


def summary_by_segment(obs: list) -> dict:
    """Return metrics keyed by time_segment name.

    Each value is a dict with keys: n, k, win_rate, ci_lo, ci_hi, z_vs_overall, ev.
    Z-score here compares segment WR vs overall WR (not vs implied).
    """
    overall_wr, _, _ = win_rate_with_ci(obs)
    result = {}
    for seg in _SEGMENTS:
        seg_obs = [o for o in obs if _val(o, "time_segment") == seg]
        result[seg] = _segment_metrics(seg_obs, overall_wr)
    return result


def _bucket_metrics(obs: list) -> dict:
    n = len(obs)
    if n == 0:
        return {"n": 0}
    k            = sum(1 for o in obs if _val(o, "no_won"))
    wr           = k / n
    ci_lo, ci_hi = wilson_ci(n, k)
    avg_no       = sum(_val(o, "no_price") for o in obs) / n
    implied      = avg_no
    edge         = wr - implied
    fee          = _taker_fee(avg_no)
    ev           = edge - fee
    se           = math.sqrt(implied * (1 - implied) / n) if implied > 0 else 1
    z            = (wr - implied) / se if se > 0 else 0.0
    return dict(n=n, k=k, win_rate=wr, ci_lo=ci_lo, ci_hi=ci_hi,
                implied=implied, edge=edge, ev=ev, z_score=z)


def _segment_metrics(obs: list, overall_wr: float) -> dict:
    n = len(obs)
    if n == 0:
        return {"n": 0}
    k            = sum(1 for o in obs if _val(o, "no_won"))
    wr           = k / n
    ci_lo, ci_hi = wilson_ci(n, k)
    se           = math.sqrt(overall_wr * (1 - overall_wr) / n) if n > 0 else 1
    z            = (wr - overall_wr) / se if se > 0 else 0.0
    avg_no       = sum(_val(o, "no_price") for o in obs) / n
    ev           = (wr - avg_no) - _taker_fee(avg_no)
    return dict(n=n, k=k, win_rate=wr, ci_lo=ci_lo, ci_hi=ci_hi,
                z_vs_overall=z, ev=ev)


# ---------------------------------------------------------------------------
# Pretty-print helpers (used by CLI)
# ---------------------------------------------------------------------------

def print_bucket_table(obs: list, title: str = "") -> None:
    """Print a formatted bucket analysis table to stdout."""
    if not obs:
        print(f"  {title}: no data")
        return

    n_total = len(obs)
    k_total = sum(1 for o in obs if _val(o, "no_won"))
    bm      = summary_by_bucket(obs)

    if title:
        print(f"\n{'='*92}")
        print(f"  {title}  --  {n_total} observations")
        print(f"{'='*92}")

    print(f"  {'YES Price':<14} {'N':>6}  {'NO WR':>7}  "
          f"{'95% CI':>17}  {'Implied':>8}  {'Edge':>7}  "
          f"{'EV/c':>8}  {'z':>7}")
    print(f"  {'-'*82}")

    for lbl in _BUCKETS:
        m = bm[lbl]
        if m.get("n", 0) == 0:
            print(f"  {lbl:<14} {'--':>6}")
            continue
        flag = "  < EDGE" if m["ev"] > 0 else ""
        print(
            f"  {lbl:<14} {m['n']:>6}  {m['win_rate']:>6.1%}  "
            f"[{m['ci_lo']:>5.1%}-{m['ci_hi']:>5.1%}]  "
            f"{m['implied']:>7.1%}  {m['edge']:>+6.1%}  {m['ev']:>+7.4f}  "
            f"{m['z_score']:>+6.2f}{flag}"
        )

    # Overall row
    wr_all, ci_lo, ci_hi = win_rate_with_ci(obs)
    avg_no_all = sum(_val(o, "no_price") for o in obs) / n_total
    ev_all     = ev_per_contract(obs)
    z_all      = z_vs_implied(obs)
    print(f"  {'-'*82}")
    print(
        f"  {'OVERALL':<14} {n_total:>6}  {wr_all:>6.1%}  "
        f"[{ci_lo:>5.1%}-{ci_hi:>5.1%}]  "
        f"{avg_no_all:>7.1%}  {wr_all - avg_no_all:>+6.1%}  "
        f"{ev_all:>+7.4f}  {z_all:>+6.2f}"
    )


def print_segment_table(obs: list) -> None:
    """Print a formatted time-segment analysis table to stdout."""
    if not obs:
        return
    overall_wr, _, _ = win_rate_with_ci(obs)
    sm = summary_by_segment(obs)

    print(f"\n  {'Time Segment':<20} {'N':>6}  {'NO WR':>7}  "
          f"{'95% CI':>17}  {'z vs all':>10}  {'EV/c':>8}")
    print(f"  {'-'*75}")

    for seg in _SEGMENTS:
        m = sm[seg]
        if m.get("n", 0) == 0:
            print(f"  {seg:<20} {'--':>6}")
            continue
        print(
            f"  {seg:<20} {m['n']:>6}  {m['win_rate']:>6.1%}  "
            f"[{m['ci_lo']:>5.1%}-{m['ci_hi']:>5.1%}]  "
            f"{m['z_vs_overall']:>+8.2f}  {m['ev']:>+7.4f}"
        )
