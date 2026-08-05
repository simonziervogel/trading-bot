"""db_ingest.py — CLI entry point for trade database ingestion.

All ingestion logic lives in kalshi/db/ingest.py. This file is a thin wrapper:
parse args, open the DB, dispatch.

Usage:
    python db_ingest.py                          # show DB status (all strategies)
    python db_ingest.py --all                    # import all results_*.json in logs/
    python db_ingest.py logs/results_XYZ.json    # import specific file(s)
    python db_ingest.py --status                 # alias for no-arg status view
    python db_ingest.py --strategy mean_reversion  # filter status to one strategy
"""

import sqlite3
import argparse
from pathlib import Path

from kalshi.db.ingest import DB_PATH, LOGS_DIR, init_db, ingest_file, print_status


def main():
    p = argparse.ArgumentParser(description="Ingest trading results into trading.db")
    p.add_argument("files",       nargs="*", help="Specific results_*.json files to import")
    p.add_argument("--all",       action="store_true", help="Import all results_*.json in logs/")
    p.add_argument("--status",    action="store_true", help="Show database status (default when no args)")
    p.add_argument("--strategy",  default=None, help="Filter status to one strategy (e.g. mean_reversion)")
    args = p.parse_args()

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    if args.status or (not args.files and not args.all):
        print_status(conn, strategy_filter=args.strategy)
        conn.close()
        return

    targets: list[Path] = []
    if args.all:
        targets = sorted(LOGS_DIR.glob("results_*.json"))
    else:
        targets = [Path(f) for f in args.files]

    if not targets:
        print("No files found.")
        conn.close()
        return

    print()
    for path in targets:
        result = ingest_file(conn, path)
        icon = "OK" if result.startswith("imported") else ("--" if result == "duplicate" else "!!")
        print(f"  {icon}  {path.name:<45}  {result}")

    print_status(conn, strategy_filter=args.strategy)
    conn.close()


if __name__ == "__main__":
    main()
