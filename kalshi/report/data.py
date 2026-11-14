"""Loaders and summary statistics over the committed data/ snapshot.

Everything a figure or a README number depends on is computed here, from
data/ only, reusing the backtest metrics so there is exactly one definition of
win rate, EV per contract and z-score in the repo.
"""

import math
from pathlib import Path

import pandas as pd

from kalshi.backtest.common_metrics import (
    ev_per_contract, win_rate_with_ci, wilson_ci, z_vs_implied,
)

DATA_DIR    = Path("data")
MAIN_SERIES = ("KXBTC15M", "KXETH15M")
_FEE_RATE   = 0.07   # same per-contract convention as common_metrics._taker_fee


# -- loading -----------------------------------------------------------------

def load_fair_value(kind: str, data_dir=DATA_DIR, series=MAIN_SERIES) -> pd.DataFrame:
    """All monthly fair-value observations of one kind, with a `month` column.

    kind: "with_lookahead" (as originally reported) or "corrected".
    """
    frames = []
    for path in sorted((Path(data_dir) / "fair_value" / kind).glob("2026-*.csv.gz")):
        df = pd.read_csv(path)
        df["month"] = path.name[:7]
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if series is not None:
        df = df[df["series"].isin(series)]
    df = df.drop_duplicates(subset=["ticker", "entry_time_utc"]).reset_index(drop=True)
    df["side_won"] = df["side_won"].astype(bool)
    return df


def like_for_like(before: pd.DataFrame, after: pd.DataFrame) -> tuple:
    """Restrict both frames to months present in both, and within each month to
    the market-close dates both runs cover (the original July run stopped on
    the 25th; the corrected one ran the whole month)."""
    months = sorted(set(before["month"]) & set(after["month"]))
    b_parts, a_parts = [], []
    for m in months:
        b, a = before[before["month"] == m], after[after["month"] == m]
        lo = max(b["market_close_date"].min(), a["market_close_date"].min())
        hi = min(b["market_close_date"].max(), a["market_close_date"].max())
        b_parts.append(b[b["market_close_date"].between(lo, hi)])
        a_parts.append(a[a["market_close_date"].between(lo, hi)])
    if not months:
        return before.iloc[0:0], after.iloc[0:0]
    return pd.concat(b_parts, ignore_index=True), pd.concat(a_parts, ignore_index=True)


def load_side_aware(rel: str, data_dir=DATA_DIR) -> pd.DataFrame:
    """A side-aware observation file (momentum, fair value)."""
    df = pd.read_csv(Path(data_dir) / rel)
    df["side_won"] = df["side_won"].astype(bool)
    return df


def load_longshot(rel: str, data_dir=DATA_DIR) -> pd.DataFrame:
    """Longshot observations, with side-aware aliases so the common metrics apply.

    Longshot always buys NO at 1 - yes_bid, so side="no", entry_price=no_price.
    """
    df = pd.read_csv(Path(data_dir) / rel)
    df["no_won"] = df["no_won"].astype(bool)
    df["side"], df["entry_price"], df["side_won"] = "no", df["no_price"], df["no_won"]
    return df


def load_live(data_dir=DATA_DIR) -> pd.DataFrame:
    """Live paper trades joined to their run's strategy, P&L per contract."""
    runs   = pd.read_csv(Path(data_dir) / "live" / "runs.csv")
    trades = pd.read_csv(Path(data_dir) / "live" / "trades.csv")
    df = trades.merge(runs[["run_id", "strategy"]], on="run_id", how="left")
    df["pnl_per_contract"] = df["pnl"] / df["qty"]
    return df


# -- statistics --------------------------------------------------------------

def net_pnl_per_contract(df: pd.DataFrame) -> pd.Series:
    """Hold-to-expiry P&L per contract after the entry fee."""
    price = df["entry_price"]
    gross = (1.0 - price).where(df["side_won"], -price)
    return gross - _FEE_RATE * price * (1.0 - price)


def _mean_ci(values: pd.Series) -> tuple:
    n = len(values)
    if n < 2:
        return float("nan"), float("nan")
    half = 1.96 * values.std(ddof=1) / math.sqrt(n)
    return values.mean() - half, values.mean() + half


def summarize(df: pd.DataFrame) -> dict:
    """n, win rate + Wilson CI, average price paid, net EV per contract + CI, z."""
    n = len(df)
    if n == 0:
        return {"n": 0}
    obs = df[["side", "entry_price", "side_won"]].to_dict("records")
    wr, lo, hi = win_rate_with_ci(obs)
    net = net_pnl_per_contract(df)
    half = (1.96 * net.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
    ev = ev_per_contract(obs)
    return {
        "n": n, "win_rate": wr, "wr_lo": lo, "wr_hi": hi,
        "avg_price": df["entry_price"].mean(),
        "ev": ev, "ev_lo": ev - half, "ev_hi": ev + half,
        "gross_ev": wr - df["entry_price"].mean(),
        "z": z_vs_implied(obs),
    }


def summarize_live(df: pd.DataFrame) -> dict:
    """Same shape as summarize(), from realized per-contract P&L of live trades."""
    n = len(df)
    if n == 0:
        return {"n": 0}
    ppc = df["pnl_per_contract"]
    wins = int((df["pnl"] > 0).sum())
    lo, hi = wilson_ci(n, wins)
    ev_lo, ev_hi = _mean_ci(ppc)
    return {
        "n": n, "win_rate": wins / n, "wr_lo": lo, "wr_hi": hi,
        "avg_price": df["entry_price"].mean(),
        "ev": ppc.mean(), "ev_lo": ev_lo, "ev_hi": ev_hi,
        "total_pnl": df["pnl"].sum(),
    }


def monthly_before_after(side=None, data_dir=DATA_DIR) -> pd.DataFrame:
    """One row per (month, kind) for the fair-value before/after comparison."""
    before, after = like_for_like(load_fair_value("with_lookahead", data_dir),
                                  load_fair_value("corrected", data_dir))
    rows = []
    for kind, df in (("with_lookahead", before), ("corrected", after)):
        if side is not None:
            df = df[df["side"] == side]
        for month, group in df.groupby("month"):
            rows.append({"month": month, "kind": kind, **summarize(group)})
        rows.append({"month": "all", "kind": kind, **summarize(df)})
    return pd.DataFrame(rows)


def calibration(df: pd.DataFrame, bins: int = 10, min_n: int = 30) -> pd.DataFrame:
    """Model P(YES) vs the market's price vs how often YES actually happened."""
    d = df.copy()
    d["yes_won"] = d["side_won"].where(d["side"] == "yes", ~d["side_won"])
    d["market"]  = (d["yes_bid"] + d["yes_ask"]) / 2.0
    edges = [i / bins for i in range(bins + 1)]
    d["bucket"] = pd.cut(d["model_prob"], edges, include_lowest=True)
    rows = []
    for _, g in d.groupby("bucket", observed=True):
        if len(g) < min_n:
            continue
        lo, hi = wilson_ci(len(g), int(g["yes_won"].sum()))
        rows.append({"n": len(g), "model": g["model_prob"].mean(),
                     "market": g["market"].mean(),
                     "realized": g["yes_won"].mean(), "lo": lo, "hi": hi})
    return pd.DataFrame(rows)
