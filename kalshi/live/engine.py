"""PaperTradingEngine — application-side paper trading against live Kalshi markets.

Trades are simulated: no real orders are placed.  The engine polls live market
data, applies the configured strategy, and maintains a virtual cash + positions
ledger.  Results are written to logs/ as a JSON summary and a JSONL event stream.

Key invariants:
  - entry_ts is stored as a timezone-aware UTC datetime object internally.
    It is only converted to an ISO string at export time.
  - EXPIRED exits pay zero fee (Kalshi settles without a second taker charge).
  - All fees use kalshi.utils.fees (single source of truth).
"""

import json
import logging
import random
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from kalshi.utils.fees import entry_fee as _calc_entry_fee, exit_fee as _calc_exit_fee
from kalshi.data.live import LiveMarketDataFetcher


# ---------------------------------------------------------------------------
# Shared defaults
# ---------------------------------------------------------------------------

DEFAULT_INITIAL_CASH     = 10_000.0
DEFAULT_SCAN_INTERVAL    = 2       # seconds
DEFAULT_DAILY_LOSS_LIMIT = 0.20    # fraction of initial cash
DEFAULT_COOLDOWN_SEC     = 30


# ---------------------------------------------------------------------------
# Trading-window helpers
# ---------------------------------------------------------------------------

def parse_trading_window(window_str: str):
    """Parse 'HH:MM-HH:MM' (UTC) -> ((sh, sm), (eh, em)) or raise ValueError."""
    try:
        start_s, end_s = window_str.strip().split("-", 1)
        sh, sm = map(int, start_s.split(":"))
        eh, em = map(int, end_s.split(":"))
        return (sh, sm), (eh, em)
    except Exception:
        raise ValueError(f"Bad trading window '{window_str}'. Expected HH:MM-HH:MM (UTC)")


def in_trading_window(window, now_utc: datetime) -> bool:
    """True if now_utc falls within the window; always True when window is None."""
    if window is None:
        return True
    (sh, sm), (eh, em) = window
    cur   = now_utc.hour * 60 + now_utc.minute
    start = sh * 60 + sm
    end   = eh * 60 + em
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end   # window crosses midnight


def entries_allowed(remaining_minutes: float, cutoff_minutes: float) -> bool:
    """False once the session is inside its entry cutoff.

    Exits and settlement keep running when this is False — only NEW entries
    stop, so hold-to-expiry positions can reach EXPIRED (fee-free) instead of
    being force-closed at session_end (exited at bid AND charged an exit fee).
    cutoff_minutes <= 0 disables the cutoff entirely.
    """
    if cutoff_minutes <= 0:
        return True
    return remaining_minutes > cutoff_minutes


def _parse_expires_at(raw) -> Optional[datetime]:
    """Parse ISO close_time string to UTC-aware datetime, or None."""
    if not raw:
        return None
    try:
        if isinstance(raw, str):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class PaperTradingEngine:
    """Strategy-agnostic paper trading engine for Kalshi 15-min markets.

    Args:
        strategy:           Any object with .signal() and .config_params() methods.
        series:             Kalshi series tickers to watch.
        initial_cash:       Starting virtual cash balance.
        scan_interval_sec:  Seconds between market scans.
        daily_loss_limit:   Fraction of initial_cash; triggers circuit breaker.
        cooldown_sec:       Re-entry cooldown per ticker after an exit.
        log_dir:            Directory for log files and results JSON.
        trading_window:     UTC window tuple from parse_trading_window(), or None.
    """

    def __init__(
        self,
        strategy,
        series:             list = None,
        initial_cash:       float = DEFAULT_INITIAL_CASH,
        scan_interval_sec:  int   = DEFAULT_SCAN_INTERVAL,
        daily_loss_limit:   float = DEFAULT_DAILY_LOSS_LIMIT,
        cooldown_sec:       int   = DEFAULT_COOLDOWN_SEC,
        log_dir:            str   = "logs",
        trading_window      = None,
        entry_cutoff_minutes: float = 0.0,
    ):
        self.strategy          = strategy
        self.series            = series or ["KXBTC15M", "KXETH15M", "KXUSD15M"]
        self.initial_cash      = initial_cash
        self.scan_interval_sec = scan_interval_sec
        self.daily_loss_limit  = daily_loss_limit
        self.cooldown_sec      = cooldown_sec
        self.trading_window    = trading_window
        # Stop opening new positions this many minutes before the session
        # ends, so hold-to-expiry positions can settle naturally (EXPIRED,
        # fee-free) instead of being force-closed at session_end (exited at
        # bid AND charged an exit fee). 0 = off, preserving old behavior.
        self.entry_cutoff_minutes = entry_cutoff_minutes

        # Execution params pulled from the strategy object
        self.take_profit_cents = getattr(strategy, "take_profit_cents", None)
        self.stop_loss_cents   = getattr(strategy, "stop_loss_cents",   None)
        self.time_stop_minutes = strategy.time_stop_minutes
        self.max_positions     = strategy.max_positions
        self.position_size_pct = strategy.position_size_pct
        self.history_minutes   = getattr(strategy, "history_minutes", 5)

        # State
        self.cash             = initial_cash
        self.equity           = initial_cash
        self.positions: dict  = {}                  # {ticker: position dict}
        self.closed_trades: list = []
        self.price_history    = defaultdict(list)   # {ticker: [(ts, yes_mid), ...]}
        self.last_exit_ts     = {}                  # {ticker: datetime}
        self.daily_pnl        = 0.0
        self.circuit_breaker  = False
        self.session_start    = datetime.now(timezone.utc)
        self.iteration        = 0
        self.diagnostics      = self._init_diagnostics()

        # Infrastructure
        self.fetcher = LiveMarketDataFetcher()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self._init_logging()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _init_diagnostics(self) -> dict:
        return {
            "iterations":        0,
            "raw_markets":       0,
            "signal_candidates": 0,
            "reversal_signals":  0,
            "entry_attempts":    0,
            "entries_filled":    0,
            "exit_events":       0,
            "cooldown_skips":    0,
            "rejections":        Counter(),
        }

    def _init_logging(self):
        ts = self.session_start.strftime("%Y%m%d_%H%M%S")
        log_file        = self.log_dir / f"trading_{ts}.log"
        self.jsonl_file = self.log_dir / f"trading_{ts}.jsonl"

        self.logger = logging.getLogger(f"PaperTrader_{ts}")
        self.logger.setLevel(logging.DEBUG)

        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        self.logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
        self.logger.addHandler(ch)

        self.logger.info("Session started | log: %s", log_file)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reject(self, reason: str):
        self.diagnostics["rejections"][reason] += 1

    def _write_event(self, obj: dict):
        try:
            line = json.dumps(
                {**obj, "ts": datetime.now(timezone.utc).isoformat()}, default=str
            )
            with open(self.jsonl_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            self.logger.exception("Failed to write JSONL event")

    def _mid(self, bid, ask) -> float:
        if bid and ask:
            return (float(bid) + float(ask)) / 2.0
        return float(bid or ask or 0.5)

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def _can_enter(self) -> bool:
        return not self.circuit_breaker and len(self.positions) < self.max_positions

    def _position_size(self, price: float) -> int:
        if price <= 0:
            return 0
        target     = self.cash * self.position_size_pct
        qty        = int(target / price)
        affordable = int(self.cash / price)
        return max(1, min(qty, affordable))

    def _enter(self, ticker: str, side: str, fill_price: float, signal: dict,
               expires_at_raw=None) -> bool:
        qty  = self._position_size(fill_price)
        cost = fill_price * qty
        if cost > self.cash:
            qty = int(self.cash / fill_price)
            if qty == 0:
                self._reject("insufficient_cash")
                return False
            cost = fill_price * qty

        fee        = _calc_entry_fee(fill_price, qty)
        total_cost = cost + fee

        if total_cost > self.cash:
            qty        = max(1, int((self.cash - fee) / fill_price))
            cost       = fill_price * qty
            fee        = _calc_entry_fee(fill_price, qty)
            total_cost = cost + fee
            if total_cost > self.cash:
                self._reject("insufficient_cash")
                return False

        now_utc = datetime.now(timezone.utc)
        self.positions[ticker] = {
            "entry_price":   fill_price,
            "qty":           qty,
            "entry_ts":      now_utc,           # stored as datetime, not string
            "current_price": fill_price,
            "current_bid":   fill_price,
            "side":          side,
            "signal":        signal,
            "entry_fee":     fee,
            "expires_at":    _parse_expires_at(expires_at_raw),
        }
        self.cash -= total_cost

        self.logger.info(
            "ENTRY | %-22s | %3s | price=%.4f | qty=%d | fee=%.2f | cash=%.2f",
            ticker, side.upper(), fill_price, qty, fee, self.cash
        )
        self._write_event({
            "type": "entry", "ticker": ticker, "side": side,
            "price": fill_price, "qty": qty, "cost": cost,
            "entry_fee": fee, "cash_after": self.cash, "signal": signal,
        })
        self.diagnostics["entries_filled"] += 1
        return True

    def _exit(self, ticker: str, price: float, reason: str):
        if ticker not in self.positions:
            return

        pos       = self.positions.pop(ticker)
        qty       = pos["qty"]
        fee       = _calc_exit_fee(price, qty, reason)   # 0 for EXPIRED
        entry_fee = pos.get("entry_fee", 0.0)
        gross_pnl = (price - pos["entry_price"]) * qty
        net_pnl   = gross_pnl - entry_fee - fee

        self.cash      += price * qty - fee
        self.daily_pnl += net_pnl
        self.equity     = self.cash + sum(
            p["current_price"] * p["qty"] for p in self.positions.values()
        )

        now_utc = datetime.now(timezone.utc)
        self.last_exit_ts[ticker] = now_utc

        # entry_ts is stored as a datetime object
        entry_ts_dt  = pos["entry_ts"]                  # UTC-aware datetime
        duration_sec = (now_utc - entry_ts_dt).total_seconds()

        trade = {
            "ticker":       ticker,
            "side":         pos["side"],
            "entry_price":  pos["entry_price"],
            "exit_price":   price,
            "qty":          qty,
            "gross_pnl":    round(gross_pnl, 4),
            "entry_fee":    round(entry_fee, 4),
            "exit_fee":     round(fee, 4),
            "pnl":          round(net_pnl, 4),
            "reason":       reason,
            "entry_ts":     entry_ts_dt.isoformat(),    # ISO string for export
            "exit_ts":      now_utc.isoformat(),
            "duration_sec": round(duration_sec, 1),
        }
        self.closed_trades.append(trade)

        sign = "+" if net_pnl >= 0 else ""
        self.logger.info(
            "EXIT  | %-22s | %-4s | price=%.4f | gross=%+.2f | fees=%.2f | net=%+.2f",
            ticker, reason, price, gross_pnl, entry_fee + fee, net_pnl,
        )
        self._write_event({**trade, "type": "exit", "cash_after": self.cash, "equity": self.equity})
        self.diagnostics["exit_events"] += 1

    def _update_prices(self, market_quotes: dict):
        """Update current_price (mid) and current_bid for all open positions."""
        for ticker, pos in self.positions.items():
            q = market_quotes.get(ticker)
            if not q:
                continue
            if pos["side"] == "yes":
                bid_raw = q.get("yes_bid_dollars")
                mid = self._mid(bid_raw, q.get("yes_ask_dollars"))
            else:
                bid_raw = q.get("no_bid_dollars")
                mid = self._mid(bid_raw, q.get("no_ask_dollars"))
            bid = float(bid_raw) if bid_raw else mid
            if mid > 0:
                pos["current_price"] = mid
                pos["current_bid"]   = bid if bid > 0 else mid

        self.equity = self.cash + sum(
            p["current_price"] * p["qty"] for p in self.positions.values()
        )

    def _check_exits(self):
        """Check EXPIRED / TP / SL / time-stop for all open positions."""
        now_utc  = datetime.now(timezone.utc)
        tp_delta = self.take_profit_cents / 100.0 if self.take_profit_cents else None
        sl_delta = self.stop_loss_cents   / 100.0 if self.stop_loss_cents   else None

        for ticker, pos in list(self.positions.items()):
            current = pos["current_price"]
            bid     = pos.get("current_bid", current)
            entry   = pos["entry_price"]

            # Contract expiry takes priority
            expires_at = pos.get("expires_at")
            if expires_at is not None and now_utc >= expires_at:
                self._exit(ticker, bid, "EXPIRED")
                continue

            if tp_delta and current >= entry + tp_delta:
                self._exit(ticker, bid, "TP")
            elif sl_delta and current <= entry - sl_delta:
                self._exit(ticker, bid, "SL")
            else:
                # Time-stop: compare entry_ts (datetime) with now_utc
                age_min = (now_utc - pos["entry_ts"]).total_seconds() / 60.0
                if age_min > self.time_stop_minutes:
                    self._exit(ticker, bid, "TIME")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, duration_minutes: int = 1440):
        self._print_header(duration_minutes)
        start = datetime.now(timezone.utc)

        try:
            while True:
                elapsed = (datetime.now(timezone.utc) - start).total_seconds() / 60.0
                if elapsed > duration_minutes:
                    self.logger.info("Duration limit reached (%d min)", duration_minutes)
                    break

                self.iteration += 1
                self.diagnostics["iterations"] += 1
                now_local = datetime.now()   # local time for display only

                try:
                    # --- Fetch live markets ---
                    all_markets = []
                    for series in self.series:
                        try:
                            all_markets.extend(self.fetcher.fetch_live_markets(series))
                        except Exception as e:
                            self.logger.debug("Fetch error %s: %s", series, e)

                    if not all_markets:
                        self._reject("no_markets_returned")
                        time.sleep(self.scan_interval_sec)
                        continue

                    # --- Build price map & update history ---
                    market_quotes = {}
                    for m in all_markets:
                        self.diagnostics["raw_markets"] += 1
                        ticker  = m.get("ticker")
                        yes_bid = m.get("yes_bid_dollars")
                        yes_ask = m.get("yes_ask_dollars")
                        if not ticker or yes_bid is None or yes_ask is None:
                            self._reject("missing_quotes")
                            continue
                        yes_mid = self._mid(yes_bid, yes_ask)
                        if yes_mid <= 0:
                            self._reject("invalid_mid")
                            continue

                        market_quotes[ticker] = m
                        self.price_history[ticker].append((now_local, yes_mid))

                        cutoff = now_local - timedelta(minutes=self.history_minutes)
                        self.price_history[ticker] = [
                            (ts, p) for ts, p in self.price_history[ticker] if ts >= cutoff
                        ]

                    # --- Update positions & check exits ---
                    self._update_prices(market_quotes)
                    self._check_exits()

                    # --- Trading window gate ---
                    now_utc   = datetime.now(timezone.utc)
                    in_window = in_trading_window(self.trading_window, now_utc)

                    if not in_window:
                        if self.trading_window:
                            (sh, sm), _ = self.trading_window
                            wstart = f"{sh:02d}:{sm:02d}"
                        else:
                            wstart = ""
                        msg = (
                            f"WAITING (window opens {wstart} UTC) | "
                            f"Pos: {len(self.positions)} | "
                            f"Equity: ${self.equity:,.2f} | "
                            f"PnL: ${self.daily_pnl:+,.2f} | "
                            f"Trades: {len(self.closed_trades)}"
                        )
                        print(f"[{now_local.strftime('%H:%M:%S')}] {msg}")
                        time.sleep(self.scan_interval_sec)
                        continue

                    # --- Entry cutoff before session end ---
                    # Exits and settlement above still run; only NEW entries
                    # stop, so open positions can reach EXPIRED naturally.
                    remaining = duration_minutes - elapsed
                    if not entries_allowed(remaining, self.entry_cutoff_minutes):
                        msg = (
                            f"SETTLING (no new entries, {remaining:.0f} min left) | "
                            f"Pos: {len(self.positions)} | "
                            f"Equity: ${self.equity:,.2f} | "
                            f"PnL: ${self.daily_pnl:+,.2f} | "
                            f"Trades: {len(self.closed_trades)}"
                        )
                        print(f"[{now_local.strftime('%H:%M:%S')}] {msg}")
                        time.sleep(self.scan_interval_sec)
                        continue

                    # --- Signal scan ---
                    candidates = list(market_quotes.keys())
                    random.shuffle(candidates)
                    for ticker in candidates[:20]:
                        if ticker in self.positions:
                            self._reject("already_open")
                            continue

                        if not self._can_enter():
                            self._reject("guardrail_blocked")
                            continue

                        self.diagnostics["signal_candidates"] += 1

                        signal = self.strategy.signal(
                            ticker, self.price_history[ticker], market_quotes
                        )
                        if signal is None:
                            self._reject("no_signal")
                            continue

                        self.diagnostics["reversal_signals"] += 1
                        side = signal["side"]

                        # Re-entry cooldown
                        last_exit = self.last_exit_ts.get(ticker)
                        if last_exit and (now_utc - last_exit).total_seconds() < self.cooldown_sec:
                            self.diagnostics["cooldown_skips"] += 1
                            self._reject("reentry_cooldown")
                            continue

                        # Fill at top-of-book ask
                        q       = market_quotes[ticker]
                        ask_key = "yes_ask_dollars" if side == "yes" else "no_ask_dollars"
                        ask_raw = q.get(ask_key)
                        try:
                            fill_price = float(ask_raw)
                        except (TypeError, ValueError):
                            self._reject(f"invalid_{side}_ask")
                            continue

                        if fill_price <= 0:
                            self._reject("zero_ask_price")
                            continue

                        self.diagnostics["entry_attempts"] += 1
                        self._enter(ticker, side, fill_price, signal,
                                    expires_at_raw=q.get("close_time"))

                    # --- Circuit breaker ---
                    if self.daily_pnl < -self.initial_cash * self.daily_loss_limit:
                        if not self.circuit_breaker:
                            self.logger.warning("CIRCUIT BREAKER: daily loss limit hit")
                        self.circuit_breaker = True

                    # --- Status line ---
                    status = (
                        f"Iter {self.iteration:4d} | "
                        f"Markets: {len(market_quotes):3d} | "
                        f"Pos: {len(self.positions)} | "
                        f"Equity: ${self.equity:10,.2f} | "
                        f"PnL: ${self.daily_pnl:+8,.2f} | "
                        f"Trades: {len(self.closed_trades)}"
                    )
                    print(f"[{now_local.strftime('%H:%M:%S')}] {status}")
                    self.logger.debug(status)

                except Exception:
                    self.logger.exception("Error in iteration %d", self.iteration)

                time.sleep(self.scan_interval_sec)

        except KeyboardInterrupt:
            print("\n[Stopped by user]")
            self.logger.info("Session stopped (Ctrl+C)")

        finally:
            # Close all open positions at current bid
            for ticker in list(self.positions.keys()):
                pos = self.positions[ticker]
                self._exit(ticker, pos.get("current_bid", pos["current_price"]), "session_end")
            self._print_final_report()
            self._export_results()

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _print_header(self, duration_minutes: int):
        cfg = self.strategy.config_params()
        print()
        print("=" * 70)
        print(f"  KALSHI PAPER TRADING — {self.strategy.name.upper().replace('_', ' ')}")
        print("=" * 70)
        print(f"  Duration:     {duration_minutes} min ({duration_minutes/60:.1f} h)")
        print(f"  Initial cash: ${self.initial_cash:,.2f}")
        print(f"  Series:       {', '.join(self.series)}")
        for k, v in cfg.items():
            if k == "strategy":
                continue
            print(f"  {k:<22} {v}")
        print(f"  Scan interval: {self.scan_interval_sec}s  |  Cooldown: {self.cooldown_sec}s")
        if self.trading_window:
            (sh, sm), (eh, em) = self.trading_window
            print(f"  Trading window: {sh:02d}:{sm:02d}-{eh:02d}:{em:02d} UTC")
        print("=" * 70)
        print()

    def _print_final_report(self):
        elapsed_min = (
            datetime.now(timezone.utc) - self.session_start
        ).total_seconds() / 60.0
        wins       = sum(1 for t in self.closed_trades if t["pnl"] > 0)
        losses     = sum(1 for t in self.closed_trades if t["pnl"] < 0)
        wr         = 100 * wins / len(self.closed_trades) if self.closed_trades else 0
        daily_ret  = 100 * self.daily_pnl / self.initial_cash
        total_fees = sum(
            t.get("entry_fee", 0) + t.get("exit_fee", 0) for t in self.closed_trades
        )
        gross_total = sum(t.get("gross_pnl", t["pnl"]) for t in self.closed_trades)

        print()
        print("=" * 70)
        print("  FINAL REPORT")
        print("=" * 70)
        print(f"  Duration:        {elapsed_min:.1f} min ({elapsed_min/60:.1f} h)")
        print(f"  Final equity:    ${self.equity:,.2f}")
        print(f"  Gross PnL:       ${gross_total:+,.2f}")
        print(f"  Total fees:      ${total_fees:,.2f}")
        print(f"  Net PnL:         ${self.daily_pnl:+,.2f}  ({daily_ret:+.2f}%)")
        print(f"  Trades:          {len(self.closed_trades)}  |  W/L: {wins}/{losses}  |  WR: {wr:.1f}%")
        print(f"  Circuit breaker: {'ACTIVE' if self.circuit_breaker else 'OK'}")

        if self.closed_trades:
            print()
            print(f"  {'#':>2}  {'Time':>8}  {'Hold':>5}  {'Side':>3}  {'Ticker':<14}  "
                  f"{'Entry':>6}  {'Exit':>6}  {'Gross':>8}  {'Fees':>6}  {'Net':>8}  Reason")
            print("  " + "-" * 85)
            for i, t in enumerate(self.closed_trades, 1):
                opened = datetime.fromisoformat(t["entry_ts"]).strftime("%H:%M:%S")
                hold   = int(t["duration_sec"])
                short  = t["ticker"].split("-")[0]
                fees   = t.get("entry_fee", 0) + t.get("exit_fee", 0)
                gross  = t.get("gross_pnl", t["pnl"])
                print(
                    f"  {i:>2}  {opened}  {hold//60:02d}:{hold%60:02d}  {t['side'].upper():>3}  "
                    f"{short:<14}  {t['entry_price']:6.4f}  {t['exit_price']:6.4f}  "
                    f"{gross:+8.2f}  {fees:6.2f}  {t['pnl']:+8.2f}  {t['reason']}"
                )

            print()
            for reason in ("TP", "SL", "TIME", "EXPIRED", "session_end"):
                group = [t for t in self.closed_trades if t["reason"] == reason]
                if not group:
                    continue
                g_net  = sum(t["pnl"] for t in group)
                g_w    = sum(1 for t in group if t["pnl"] > 0)
                g_fees = sum(t.get("entry_fee", 0) + t.get("exit_fee", 0) for t in group)
                print(
                    f"  {reason:<11}  count={len(group):2d}  "
                    f"W/L={g_w}/{len(group)-g_w}  fees={g_fees:.2f}  net={g_net:+.2f}"
                )

            print()
            for side in ("yes", "no"):
                group = [t for t in self.closed_trades if t["side"] == side]
                if not group:
                    continue
                g_net = sum(t["pnl"] for t in group)
                g_w   = sum(1 for t in group if t["pnl"] > 0)
                g_wr  = 100 * g_w / len(group)
                print(
                    f"  Side {side.upper():<3}  count={len(group):2d}  "
                    f"W/L={g_w}/{len(group)-g_w}  WR={g_wr:.1f}%  net={g_net:+.2f}"
                )

        print()
        print("  Diagnostics:")
        d = self.diagnostics
        print(
            f"    raw_markets={d['raw_markets']}  candidates={d['signal_candidates']}  "
            f"signals={d['reversal_signals']}  attempts={d['entry_attempts']}  "
            f"filled={d['entries_filled']}  exits={d['exit_events']}  "
            f"cooldowns={d['cooldown_skips']}"
        )
        if d["rejections"]:
            top = d["rejections"].most_common(6)
            print("    Top rejections: " + "  ".join(f"{r}={c}" for r, c in top))
        print("=" * 70)

        self.logger.info(
            "Final | equity=%.2f pnl=%+.2f ret=%.2f%% trades=%d W/L=%d/%d WR=%.1f%%",
            self.equity, self.daily_pnl, daily_ret,
            len(self.closed_trades), wins, losses, wr,
        )

    def _export_results(self):
        ts          = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path        = self.log_dir / f"results_{ts}.json"
        wins        = sum(1 for t in self.closed_trades if t["pnl"] > 0)
        total_fees  = sum(
            t.get("entry_fee", 0) + t.get("exit_fee", 0) for t in self.closed_trades
        )
        gross_total = sum(t.get("gross_pnl", t["pnl"]) for t in self.closed_trades)
        now_utc     = datetime.now(timezone.utc)

        data = {
            "session": {
                "start":            self.session_start.isoformat(),
                "end":              now_utc.isoformat(),
                "duration_minutes": (now_utc - self.session_start).total_seconds() / 60.0,
            },
            "config": {
                **self.strategy.config_params(),
                "initial_cash": self.initial_cash,
                "series":       self.series,
            },
            "summary": {
                "final_equity":    self.equity,
                "gross_pnl":       round(gross_total, 4),
                "total_fees":      round(total_fees, 4),
                "total_pnl":       self.daily_pnl,
                "return_pct":      100 * self.daily_pnl / self.initial_cash,
                "trades":          len(self.closed_trades),
                "wins":            wins,
                "losses":          len(self.closed_trades) - wins,
                "win_rate_pct":    100 * wins / len(self.closed_trades) if self.closed_trades else 0,
                "circuit_breaker": self.circuit_breaker,
            },
            "diagnostics": {
                **self.diagnostics,
                "rejections": dict(self.diagnostics["rejections"]),
            },
            "trades": self.closed_trades,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        print(f"\n  Results saved to: {path}")
        self.logger.info("Results exported to: %s", path)
