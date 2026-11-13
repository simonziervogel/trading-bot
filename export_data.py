"""export_data.py — snapshot the backtest and live results into data/.

results/ and trading.db are local working files (gitignored: large, and
regenerable from the Kalshi/Binance APIs). This script copies the subset the
figures and the notebook depend on into data/, which IS committed, so
`make_figures.py` and `analysis.ipynb` run from a fresh clone with no API key.

Observation CSVs are gzip-compressed (pandas reads .csv.gz directly); the small
audit and live tables stay plain CSV so they diff readably.

Usage:
    python export_data.py                 # results/ + trading.db -> data/
    python export_data.py --dry-run       # list what would be written
"""

import argparse
import gzip
import shutil
import sqlite3
from pathlib import Path

import pandas as pd

# destination (under data/) -> one or more source files (under results/).
# Several sources are concatenated in order.
_FAIR_VALUE_LOOKAHEAD = {
    "2026-01": ["fv_month_2026-01.csv"],
    "2026-02": ["fv_month_2026-02.csv"],
    "2026-03": ["fv_month_2026-03.csv"],
    "2026-04": ["fv_month_2026-04.csv"],
    "2026-05": ["backtest_fair_value_may_full.csv"],
    "2026-06": ["fv_month_2026-06.csv"],
    # July was run per series as an eight-instrument cross-section.
    "2026-07": ["july_KXBTC15M.csv", "july_KXETH15M.csv", "july_KXSOL15M.csv",
                "july_KXXRP15M.csv", "july_KXDOGE15M.csv", "july_KXBNB15M.csv",
                "july_KXNEAR15M.csv", "july_KXZEC15M.csv"],
}
_FAIR_VALUE_CORRECTED = {
    "2026-01": ["fixed_2026-01.csv"],
    "2026-02": ["fixed_2026-02.csv"],
    "2026-03": ["fixed_2026-03.csv"],
    "2026-04": ["fixed_2026-04.csv"],
    "2026-05": ["fixed_2026-05.csv"],
    "2026-06": ["fixed_2026-06.csv"],
    "2026-07": ["fixed_july_btceth.csv"],
}
_OTHER = {
    "fair_value/with_lookahead/oos_split.csv.gz": ["backtest_fair_value_oos.csv"],
    "longshot/2026-06_sample.csv.gz":             ["backtest_observations.csv"],
    "longshot/2026-02_03.csv.gz":                 ["longshot_febmar_fixed.csv"],
    "momentum/2026-06_sample.csv.gz":             ["backtest_momentum.csv"],
    "momentum/oos_split.csv.gz":                  ["backtest_momentum_oos.csv"],
}
_LIVE_TABLES = {"live/runs.csv": "runs", "live/trades.csv": "trades"}


def _manifest() -> dict:
    out = {}
    for month, srcs in _FAIR_VALUE_LOOKAHEAD.items():
        out[f"fair_value/with_lookahead/{month}.csv.gz"] = srcs
    for month, srcs in _FAIR_VALUE_CORRECTED.items():
        out[f"fair_value/corrected/{month}.csv.gz"] = srcs
    out.update(_OTHER)
    return out


def _write_gz(dest: Path, sources: list) -> int:
    """Concatenate CSVs (single header) into a deterministic .csv.gz."""
    frames = [pd.read_csv(s, dtype=str, keep_default_na=False) for s in sources]
    df = pd.concat(frames, ignore_index=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 keeps the archive byte-identical across re-exports of the same
    # data, so git only sees a change when the content changed.
    with open(dest, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(df.to_csv(index=False, lineterminator="\n").encode("utf-8"))
    return len(df)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--results-dir", default="results")
    p.add_argument("--db", default="trading.db")
    p.add_argument("--out-dir", default="data")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    results, out = Path(args.results_dir), Path(args.out_dir)
    missing = []

    for rel, names in _manifest().items():
        sources = [results / n for n in names]
        absent = [s for s in sources if not s.exists()]
        if absent:
            missing.append((rel, [str(a) for a in absent]))
            continue
        if args.dry_run:
            print(f"  would write {out / rel}  <- {', '.join(names)}")
            continue
        n = _write_gz(out / rel, sources)
        print(f"  {out / rel}  ({n} rows)")

    for name in ("audit_lookahead.csv", "audit_basis.csv", "audit_settlement_agreement.csv"):
        src = results / name
        if src.exists() and not (out / name).exists() and not args.dry_run:
            out.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out / name)
            print(f"  {out / name}  (copied; re-run audit_fair_value.py --out-dir {out} to refresh)")

    db = Path(args.db)
    if db.exists():
        con = sqlite3.connect(db)
        try:
            for rel, table in _LIVE_TABLES.items():
                df = pd.read_sql(f"select * from {table}", con)
                if args.dry_run:
                    print(f"  would write {out / rel}  <- {db}:{table} ({len(df)} rows)")
                    continue
                (out / rel).parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(out / rel, index=False, lineterminator="\n")
                print(f"  {out / rel}  ({len(df)} rows)")
        finally:
            con.close()
    else:
        missing.append(("live/*.csv", [str(db)]))

    if missing:
        print("\nSkipped (source not found):")
        for rel, absent in missing:
            print(f"  {rel}  <- missing {', '.join(absent)}")


if __name__ == "__main__":
    main()
