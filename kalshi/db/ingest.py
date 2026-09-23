"""Trade database ingestion — reads results_*.json files from logs/ into trading.db (SQLite).

Idempotent — re-running never duplicates runs.

Derived fields computed at ingest time:
  series           — KXBTC15M / KXETH15M / KXUSD15M
  sl_gap_cents     — actual gap in cents for SL trades
  tte_at_entry_min — minutes until contract expiry at moment of entry
  price_bucket     — low (<0.25) / mid (0.25-0.75) / high (>0.75)
  pnl_pct          — % change of contract price from entry to exit

Config filter: only runs matching a registered strategy config are imported.
STRATEGY_REGISTRY is the single source of truth — add new strategies here.
Old strategies are never removed so historical runs stay importable.
"""

import sqlite3
import json
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta

DB_PATH  = Path("trading.db")
LOGS_DIR = Path("logs")

# Legacy timezone offsets — used only as fallback for old runs that logged naive local time.
# New runs (runner.py post-fix) write UTC ISO strings directly.
EDT  = timezone(timedelta(hours=-4))   # US Eastern Daylight Time (summer)
CEST = timezone(timedelta(hours=2))    # Central European Summer Time (summer)

# Registry of all strategy configs ever tested.
# OLD ENTRIES ARE NEVER REMOVED — historical runs must stay importable.
# To change a strategy's params, add a new key (e.g. "mean_reversion_v2").
STRATEGY_REGISTRY: dict[str, dict] = {
    "mean_reversion": {
        "take_profit_cents": 1.5,
        "stop_loss_cents":   2.5,
        "time_stop_minutes": 10,
        "momentum_pct":      0.01,
        "reversal_pct":      0.003,
        "history_minutes":   5,
        "max_positions":     8,
        "position_size_pct": 0.01,
    },
    # Live validation config — must match exactly what run_fair_value_live.bat
    # passes, since config_matches() compares every key listed here against
    # the run's config. TTE [2.0, 14.0] is the window the backtest was
    # validated on (NOT the shared Longshot-calibrated 5/13 defaults).
    "fair_value": {
        "min_edge_pct":       0.03,
        "time_stop_minutes":  14,
        "max_positions":      8,
        "position_size_pct":  0.01,
        "min_tte_minutes":    2.0,
        "max_tte_minutes":    14.0,
        "max_spread_cents":   8.0,
        "vol_window_minutes": 60,
        "no_side_only":       True,
    },
    "favorite_longshot": {
        "longshot_threshold":      0.85,
        "time_stop_minutes":       14,
        "max_positions":           8,
        "position_size_pct":       0.01,
        "min_tte_minutes":         5,
        "max_tte_minutes":         13,
        "max_spread_cents":        8.0,
        "momentum_filter":         True,
        "momentum_window_minutes": 2,
        "momentum_threshold_pct":  0.3,
    },
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file       TEXT    UNIQUE,
    start_ts          TEXT,
    end_ts            TEXT,
    duration_min      REAL,
    -- strategy identifier
    strategy          TEXT    DEFAULT 'mean_reversion',
    -- config snapshot (NULL for strategies that don't use a given param)
    tp_cents          REAL,
    sl_cents          REAL,
    time_stop_min     INTEGER,
    momentum_pct      REAL,
    reversal_pct      REAL,
    history_min       INTEGER,
    max_positions     INTEGER,
    position_size_pct REAL,
    initial_cash      REAL,
    -- session summary
    total_trades      INTEGER,
    wins              INTEGER,
    losses            INTEGER,
    win_rate          REAL,
    total_pnl         REAL,
    return_pct        REAL,
    circuit_breaker   INTEGER
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            INTEGER REFERENCES runs(run_id),
    ticker            TEXT,
    series            TEXT,       -- KXBTC15M / KXETH15M / KXUSD15M
    side              TEXT,       -- yes / no
    entry_price       REAL,
    exit_price        REAL,
    qty               INTEGER,
    gross_pnl         REAL,       -- pre-fee profit: (exit-entry)*qty
    entry_fee         REAL,       -- Kalshi taker fee paid at entry
    exit_fee          REAL,       -- Kalshi taker fee paid at exit
    pnl               REAL,       -- net profit after fees
    reason            TEXT,       -- TP / SL / TIME / session_end
    entry_ts          TEXT,
    exit_ts           TEXT,
    duration_sec      REAL,
    -- derived at ingest
    sl_gap_cents      REAL,       -- NULL unless reason=SL
    tte_at_entry_min  REAL,       -- minutes to expiry at entry time
    price_bucket      TEXT,       -- low / mid / high
    pnl_pct           REAL        -- % change of contract price: (exit-entry)/entry*100
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def init_db(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)

    # --- Migrate runs table ---
    run_cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    if "strategy" not in run_cols:
        conn.execute("ALTER TABLE runs ADD COLUMN strategy TEXT DEFAULT 'mean_reversion'")
        conn.execute("UPDATE runs SET strategy = 'mean_reversion' WHERE strategy IS NULL")

    # --- Migrate trades table ---
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)")}

    if "pnl_pct" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN pnl_pct REAL")
        conn.execute(
            "UPDATE trades SET pnl_pct = ROUND((exit_price - entry_price) / entry_price * 100, 4)"
            " WHERE entry_price > 0"
        )

    if "gross_pnl" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN gross_pnl REAL")
        conn.execute("UPDATE trades SET gross_pnl = pnl WHERE gross_pnl IS NULL")

    if "entry_fee" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN entry_fee REAL")
        conn.execute(
            "UPDATE trades SET entry_fee = ROUND(0.07 * entry_price * (1.0 - entry_price) * qty, 4)"
            " WHERE entry_fee IS NULL AND entry_price > 0"
        )

    if "exit_fee" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN exit_fee REAL")
        conn.execute(
            "UPDATE trades SET exit_fee = ROUND(0.07 * exit_price * (1.0 - exit_price) * qty, 4)"
            " WHERE exit_fee IS NULL AND exit_price > 0"
        )

    conn.commit()


def parse_expiry_utc(ticker: str) -> datetime | None:
    """
    Parse contract expiry (UTC) from ticker name.
    Example: KXBTC15M-26MAY090545-45
      → year=2026, month=May, day=09, time=05:45 EDT → 09:45 UTC
    Kalshi uses US Eastern time for contract windows.
    """
    m = re.match(r"KX\w+?-(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})-\d+", ticker)
    if not m:
        return None
    month_map = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5,  "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }
    try:
        year   = 2000 + int(m.group(1))
        month  = month_map[m.group(2)]
        day    = int(m.group(3))
        hour   = int(m.group(4))
        minute = int(m.group(5))
        expiry_edt = datetime(year, month, day, hour, minute, tzinfo=EDT)
        return expiry_edt.astimezone(timezone.utc)
    except Exception:
        return None


def get_series(ticker: str) -> str:
    for s in ("KXBTC15M", "KXETH15M", "KXUSD15M"):
        if ticker.startswith(s):
            return s
    return ticker.split("-")[0]


def get_price_bucket(price: float) -> str:
    if price < 0.25:
        return "low"
    if price <= 0.75:
        return "mid"
    return "high"


def config_matches(cfg: dict) -> tuple[bool, str]:
    """Check if cfg matches any registered strategy config."""
    strategy = cfg.get("strategy")
    if strategy not in STRATEGY_REGISTRY:
        known = ", ".join(STRATEGY_REGISTRY)
        return False, f"unknown strategy '{strategy}' (registered: {known})"
    expected = STRATEGY_REGISTRY[strategy]
    for key, val in expected.items():
        actual = cfg.get(key)
        if isinstance(val, float) and isinstance(actual, (int, float)):
            if abs(float(actual) - val) > 1e-9:
                return False, f"{key}: expected={val}, got={actual}"
        elif actual != val:
            return False, f"{key}: expected={val}, got={actual}"
    return True, ""


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def ingest_file(conn: sqlite3.Connection, path: Path) -> str:
    """Returns 'imported', 'duplicate', or 'skipped:<reason>'."""
    cur = conn.execute("SELECT run_id FROM runs WHERE source_file = ?", (path.name,))
    if cur.fetchone():
        return "duplicate"

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"skipped:json_error({e})"

    cfg  = data.get("config", {})
    ok, reason = config_matches(cfg)
    if not ok:
        return f"skipped:config_mismatch({reason})"

    sess     = data["session"]
    summ     = data["summary"]
    strategy = cfg["strategy"]

    conn.execute(
        """
        INSERT INTO runs (
            source_file, start_ts, end_ts, duration_min,
            strategy,
            tp_cents, sl_cents, time_stop_min, momentum_pct, reversal_pct,
            history_min, max_positions, position_size_pct, initial_cash,
            total_trades, wins, losses, win_rate, total_pnl, return_pct, circuit_breaker
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            path.name,
            sess["start"], sess["end"], sess["duration_minutes"],
            strategy,
            cfg.get("take_profit_cents"), cfg.get("stop_loss_cents"), cfg.get("time_stop_minutes"),
            cfg.get("momentum_pct"), cfg.get("reversal_pct"), cfg.get("history_minutes"),
            cfg.get("max_positions"), cfg.get("position_size_pct"), cfg.get("initial_cash"),
            summ["trades"], summ["wins"], summ["losses"], summ["win_rate_pct"],
            summ["total_pnl"], summ["return_pct"], int(summ["circuit_breaker"]),
        ),
    )
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    rows = []
    for t in data.get("trades", []):
        # SL gap: how far did price actually move vs expected 2.5c trigger
        sl_gap = None
        if t["reason"] == "SL":
            sl_gap = round(abs(t["exit_price"] - t["entry_price"]) * 100, 4)

        # Time-to-expiry at entry moment
        tte = None
        expiry_utc = parse_expiry_utc(t["ticker"])
        if expiry_utc:
            try:
                entry_raw = t["entry_ts"]
                parsed    = datetime.fromisoformat(entry_raw)
                if parsed.tzinfo is not None:
                    # New runs: runner.py writes UTC ISO strings with timezone info
                    entry_utc = parsed.astimezone(timezone.utc)
                else:
                    # Legacy runs: naive local time assumed CEST (UTC+2, summer only)
                    entry_utc = parsed.replace(tzinfo=CEST).astimezone(timezone.utc)
                tte = round((expiry_utc - entry_utc).total_seconds() / 60, 2)
            except Exception:
                pass

        pnl_pct = None
        if t["entry_price"] > 0:
            pnl_pct = round((t["exit_price"] - t["entry_price"]) / t["entry_price"] * 100, 4)

        # Fee fields: new runs have them; old runs → approximate from prices
        ep, xp, qty = t["entry_price"], t["exit_price"], t["qty"]
        gross_pnl = t.get("gross_pnl", round((xp - ep) * qty, 4))
        entry_fee = t.get("entry_fee", round(0.07 * ep * (1.0 - ep) * qty, 4) if ep > 0 else None)
        exit_fee  = t.get("exit_fee",  round(0.07 * xp * (1.0 - xp) * qty, 4) if xp > 0 else None)

        rows.append((
            run_id,
            t["ticker"],
            get_series(t["ticker"]),
            t["side"],
            ep,
            xp,
            qty,
            gross_pnl,
            entry_fee,
            exit_fee,
            t["pnl"],
            t["reason"],
            t["entry_ts"],
            t["exit_ts"],
            t["duration_sec"],
            sl_gap,
            tte,
            get_price_bucket(ep),
            pnl_pct,
        ))

    conn.executemany(
        """
        INSERT INTO trades (
            run_id, ticker, series, side,
            entry_price, exit_price, qty,
            gross_pnl, entry_fee, exit_fee, pnl, reason,
            entry_ts, exit_ts, duration_sec,
            sl_gap_cents, tte_at_entry_min, price_bucket, pnl_pct
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    conn.commit()
    return f"imported ({len(rows)} trades)"


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

def print_status(conn: sqlite3.Connection, strategy_filter: str | None = None):
    w = 78

    where = ""
    params: tuple = ()
    if strategy_filter:
        where  = "WHERE strategy = ?"
        params = (strategy_filter,)

    strategies = conn.execute(
        f"SELECT DISTINCT strategy FROM runs {where} ORDER BY strategy", params
    ).fetchall()
    strategies = [r[0] for r in strategies]

    total_trades = conn.execute(
        f"SELECT COUNT(*) FROM trades t JOIN runs r ON t.run_id=r.run_id {where}", params
    ).fetchone()[0]
    total_pnl = conn.execute(
        f"SELECT SUM(t.pnl) FROM trades t JOIN runs r ON t.run_id=r.run_id {where}", params
    ).fetchone()[0] or 0.0
    total_runs = conn.execute(
        f"SELECT COUNT(*) FROM runs {where}", params
    ).fetchone()[0]

    print()
    print("=" * w)
    label = f"  trading.db  --  {total_runs} run(s)  |  {total_trades} total trades  |  net PnL: ${total_pnl:+,.2f}"
    if strategy_filter:
        label += f"  [filter: {strategy_filter}]"
    print(label)
    print("=" * w)

    for strat in strategies:
        runs = conn.execute(
            "SELECT run_id, source_file, duration_min, total_trades, win_rate, total_pnl "
            "FROM runs WHERE strategy=? ORDER BY start_ts",
            (strat,),
        ).fetchall()
        strat_trades = conn.execute(
            "SELECT COUNT(*), SUM(t.pnl) FROM trades t JOIN runs r ON t.run_id=r.run_id WHERE r.strategy=?",
            (strat,),
        ).fetchone()
        strat_pnl = strat_trades[1] or 0.0

        print(f"\n  [{strat}]  {len(runs)} run(s)  |  {strat_trades[0]} trades  |  net PnL: ${strat_pnl:+,.2f}")
        print(f"  {'#':>2}  {'File':<38}  {'Duration':>8}  {'Trades':>6}  {'WR%':>5}  {'PnL':>9}")
        print("  " + "-" * (w - 2))
        for r in runs:
            run_id, src, dur, n, wr, pnl = r
            print(f"  {run_id:>2}  {src:<38}  {dur:>6.0f}min  {n:>6}  {wr:>5.1f}  {pnl:>+9.2f}")

    print()
    if len(strategies) > 1:
        print("  Strategy comparison:")
        for strat in strategies:
            row = conn.execute(
                "SELECT COUNT(*), ROUND(AVG(win_rate),1), ROUND(SUM(total_pnl),2) FROM runs WHERE strategy=?",
                (strat,),
            ).fetchone()
            print(f"    {strat:<20}  runs={row[0]}  avg_WR={row[1]}%  total_PnL=${row[2]:+,.2f}")
        print()

    print("=" * w)
    print()
