"""audit_fair_value.py — measure the two input errors that produced the retracted
Fair Value edge, and write the results to CSV for the analysis notebook.

Two independent measurements, both run against stored backtest observations so
they need no Kalshi calls:

  lookahead   Replays every stored NO signal at its own entry candle, once with
              the engine's original spot lookup (klines keyed by OPEN time, so
              spot_at(T) returned a price up to 60s in the future) and once with
              a strictly-settled lookup. Entry candle, quotes and settlement
              outcome are held fixed; only the spot/vol estimate changes.

  basis       Kalshi's floor_strike IS the settlement index at market open, so
              the Binance-vs-index basis is measurable with no external source:
              10000 * (binance_at_open / floor_strike - 1). Also reports how
              often the sign of Binance's own 15-minute move disagrees with how
              Kalshi actually settled, binned by the size of that move.

Usage:
    python audit_fair_value.py results/july_KXBTC15M.csv results/fv_month_2026-01.csv
    python audit_fair_value.py --out-dir results results/july_*.csv
"""

import argparse
import csv
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta

from kalshi.data.binance_history import fetch_klines_range, PriceSeries
from kalshi.utils.market import BINANCE_SYMBOL
from kalshi.utils.pricing import digital_call_probability, realized_vol_annualized
from kalshi.utils.fees import taker_fee

_EPOCH      = datetime(1970, 1, 1)
_VOL_MS     = 60 * 60_000
_MIN_EDGE   = 0.03
_EDGE_TOL   = 0.005
_CONTRACT_M = 15

# Observations store spot/strike rounded to 2dp, which is far too coarse to
# reconstruct ln(S/K) for low-priced underlyings (XRP ~1.04, DOGE ~0.07) where
# the model's whole denominator is ~20 basis points. Only series with enough
# significant digits at 2dp can be audited retroactively.
_AUDITABLE = ("KXBTC15M", "KXETH15M", "KXBNB15M", "KXSOL15M", "KXZEC15M")

# Same plausibility bounds the engine enforces: Kalshi has been observed
# returning floor_strike off by orders of magnitude.
_RATIO_LO, _RATIO_HI = 0.5, 2.0

_MOVE_BINS = [0.0, 2.0, 5.0, 10.0, 20.0, 40.0, float("inf")]


def _ms(dt: datetime) -> int:
    return int((dt - _EPOCH).total_seconds()) * 1000


def _pct(sorted_vals: list, q: float) -> float:
    if not sorted_vals:
        return float("nan")
    i = int(q * (len(sorted_vals) - 1))
    return sorted_vals[max(0, min(len(sorted_vals) - 1, i))]


class _Settled(PriceSeries):
    """The lookup the engine should always have had.

    Shifts every query back one interval, undoing the open-time keying that
    fetch_klines_range used before the fix. It lives here rather than in the
    library because it exists only to reproduce the opposite of the old bug.
    """

    def spot_at(self, ts_ms):
        return super().spot_at(ts_ms - 60_000)

    def trailing_closes(self, ts_ms, window_ms):
        return super().trailing_closes(ts_ms - 60_000, window_ms)


def _load_rows(paths: list) -> dict:
    """Group deduplicated observations by series, auditable series only."""
    seen, by_series = set(), defaultdict(list)
    for path in paths:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("series") not in _AUDITABLE:
                    continue
                key = (r["ticker"], r["entry_time_utc"])
                if key in seen:
                    continue
                seen.add(key)
                r["_entry"] = datetime.strptime(r["entry_time_utc"], "%Y-%m-%dT%H:%M:%S")
                by_series[r["series"]].append(r)
    return by_series


def _price_series(series: str, rows: list) -> tuple:
    """Fetch klines covering every row and return (buggy, settled, truth) views.

    fetch_klines_range now returns close-keyed points — that was the fix — so
    the offset is undone here to recover the pre-fix behaviour for comparison.
    """
    symbol = BINANCE_SYMBOL[series]
    lo = _ms(min(r["_entry"] for r in rows)) - (_CONTRACT_M + 70) * 60_000
    hi = _ms(max(r["_entry"] for r in rows)) + _CONTRACT_M * 60_000
    pts = fetch_klines_range(symbol, lo, hi)
    open_keyed = [(t - 60_000, p) for t, p in pts]
    return PriceSeries(open_keyed), _Settled(open_keyed), PriceSeries(pts)


def audit_lookahead(by_series: dict, cache: dict) -> list:
    """Realized vs claimed edge on NO signals, with and without the lookahead."""
    out = []
    for series, rows in sorted(by_series.items()):
        no_rows = [r for r in rows if r["side"] == "no"]
        if not no_rows:
            continue
        buggy, settled, _truth = cache[series]
        a = defaultdict(float)
        for r in no_rows:
            cms      = _ms(r["_entry"])
            strike   = float(r["strike"])
            tau      = float(r["tte_minutes"]) / (365.25 * 24 * 60)
            no_price = 1.0 - float(r["yes_bid"])
            fee      = taker_fee(no_price, 1)
            won      = int(r["side_won"])
            pnl      = (1.0 - no_price) if won else -no_price

            s0 = buggy.spot_at(cms)
            v0 = realized_vol_annualized(buggy.trailing_closes(cms, _VOL_MS))
            if s0 is None or v0 is None:
                continue
            p0 = digital_call_probability(s0, strike, tau, v0, "greater_or_equal")
            if p0 is None:
                continue
            e0 = (1.0 - p0) - no_price - fee
            # Only trust rows this reproduces: proof the replay matches the run
            # that produced them.
            if abs(e0 - float(r["edge"])) > _EDGE_TOL:
                a["unreconstructable"] += 1
                continue

            s1 = settled.spot_at(cms)
            v1 = realized_vol_annualized(settled.trailing_closes(cms, _VOL_MS))
            if s1 is None or v1 is None:
                continue
            p1 = digital_call_probability(s1, strike, tau, v1, "greater_or_equal")
            if p1 is None:
                continue
            e1 = (1.0 - p1) - no_price - fee

            a["n"] += 1
            a["claim_buggy"] += e0
            a["real_buggy"]  += pnl
            a["wins_buggy"]  += won
            if e1 >= _MIN_EDGE:
                a["kept"] += 1
                a["claim_settled"] += e1
                a["real_settled"]  += pnl
                a["wins_settled"]  += won

        if not a["n"]:
            continue
        n, k = a["n"], a["kept"] or 1
        out.append({
            "series": series,
            "n": int(n),
            "signal_rate_kept":        round(a["kept"] / n, 4),
            "claim_with_lookahead":    round(a["claim_buggy"] / n, 4),
            "realized_with_lookahead": round(a["real_buggy"] / n, 4),
            "win_rate_with_lookahead": round(a["wins_buggy"] / n, 4),
            "claim_no_lookahead":      round(a["claim_settled"] / k, 4),
            "realized_no_lookahead":   round(a["real_settled"] / k, 4),
            "win_rate_no_lookahead":   round(a["wins_settled"] / k, 4),
            "unreconstructable":       int(a["unreconstructable"]),
        })
    return out


def audit_basis(by_series: dict, cache: dict) -> tuple:
    """Basis vs floor_strike, and settlement agreement by size of move."""
    per_series, bins, basis_all = [], defaultdict(lambda: [0, 0]), []
    for series, rows in sorted(by_series.items()):
        _buggy, _settled, truth = cache[series]
        basis, skipped = [], 0
        for r in rows:
            close_dt = r["_entry"] + timedelta(minutes=float(r["tte_minutes"]))
            open_dt  = close_dt - timedelta(minutes=_CONTRACT_M)
            b_open   = truth.spot_at(_ms(open_dt))
            b_close  = truth.spot_at(_ms(close_dt))
            if b_open is None or b_close is None:
                continue
            strike = float(r["strike"])
            if not (_RATIO_LO < b_open / strike < _RATIO_HI):
                skipped += 1
                continue
            basis.append(10000.0 * (b_open / strike - 1.0))

            move_bp     = 10000.0 * (b_close / b_open - 1.0)
            kalshi_yes  = (r["side"] == "yes") == bool(int(r["side_won"]))
            binance_yes = b_close >= b_open
            for i in range(len(_MOVE_BINS) - 1):
                if _MOVE_BINS[i] <= abs(move_bp) < _MOVE_BINS[i + 1]:
                    bins[i][0] += 1
                    bins[i][1] += int(kalshi_yes == binance_yes)
                    break

        if not basis:
            continue
        s = sorted(basis)
        basis_all += basis
        per_series.append({
            "series": series,
            "n": len(s),
            "basis_p10_bp":         round(_pct(s, 0.10), 2),
            "basis_median_bp":      round(_pct(s, 0.50), 2),
            "basis_p90_bp":         round(_pct(s, 0.90), 2),
            "median_abs_basis_bp":  round(sorted(abs(x) for x in s)[len(s) // 2], 2),
            "implausible_strike_rows": skipped,
        })

    if basis_all:
        s = sorted(basis_all)
        per_series.append({
            "series": "POOLED",
            "n": len(s),
            "basis_p10_bp":         round(_pct(s, 0.10), 2),
            "basis_median_bp":      round(_pct(s, 0.50), 2),
            "basis_p90_bp":         round(_pct(s, 0.90), 2),
            "median_abs_basis_bp":  round(sorted(abs(x) for x in s)[len(s) // 2], 2),
            "implausible_strike_rows": "",
        })

    agree = []
    for i in range(len(_MOVE_BINS) - 1):
        n, ok = bins[i]
        if not n:
            continue
        hi = "inf" if _MOVE_BINS[i + 1] == float("inf") else "%g" % _MOVE_BINS[i + 1]
        agree.append({
            "move_bucket_bp":    "%g-%s" % (_MOVE_BINS[i], hi),
            "n":                 n,
            "agreement_rate":    round(ok / n, 4),
            "disagreement_rate": round((n - ok) / n, 4),
        })
    return per_series, agree


def _write(path: str, rows: list) -> None:
    if not rows:
        print("  (nothing to write for %s)" % path)
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("  CSV saved -> %s  (%d rows)" % (path, len(rows)))


def main():
    p = argparse.ArgumentParser(
        description="Measure the lookahead and basis errors behind the retracted edge",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("csvs", nargs="+", help="backtest observation CSVs to audit")
    p.add_argument("--out-dir", default="results")
    args = p.parse_args()

    by_series = _load_rows(args.csvs)
    if not by_series:
        p.error("no auditable observations found (need one of: %s)" % ", ".join(_AUDITABLE))

    print("Auditing %d series: %s" % (len(by_series), ", ".join(sorted(by_series))))
    cache = {}
    for series, rows in sorted(by_series.items()):
        print("  fetching %s klines for %d observations..."
              % (BINANCE_SYMBOL[series], len(rows)), flush=True)
        cache[series] = _price_series(series, rows)

    print("\n-- lookahead ---------------------------------")
    look = audit_lookahead(by_series, cache)
    for r in look:
        print("  %-10s N=%5d  with: %.3f WR / %+.3f EV   without: %.3f WR / %+.3f EV"
              "  (%.1f%% of signals kept)"
              % (r["series"], r["n"],
                 r["win_rate_with_lookahead"], r["realized_with_lookahead"],
                 r["win_rate_no_lookahead"], r["realized_no_lookahead"],
                 100.0 * r["signal_rate_kept"]))
    _write(os.path.join(args.out_dir, "audit_lookahead.csv"), look)

    print("\n-- basis -------------------------------------")
    basis, agree = audit_basis(by_series, cache)
    for r in basis:
        print("  %-10s N=%6d  median %+.1fbp  [p10 %+.1f, p90 %+.1f]"
              % (r["series"], r["n"], r["basis_median_bp"],
                 r["basis_p10_bp"], r["basis_p90_bp"]))
    print()
    for r in agree:
        print("  |move| %-9s N=%6d  disagreement %5.1f%%"
              % (r["move_bucket_bp"], r["n"], 100.0 * r["disagreement_rate"]))
    _write(os.path.join(args.out_dir, "audit_basis.csv"), basis)
    _write(os.path.join(args.out_dir, "audit_settlement_agreement.csv"), agree)

    sd14 = 10000 * 0.40 * math.sqrt(14 / (365.25 * 24 * 60))
    sd2  = 10000 * 0.40 * math.sqrt(2 / (365.25 * 24 * 60))
    print("\nFor scale: sigma*sqrt(tau) = %.1fbp at 14 min, %.1fbp at 2 min (sigma=0.40)."
          % (sd14, sd2))


if __name__ == "__main__":
    main()
