@echo off
REM Live paper-trading validation for FairValueStrategy (NO side only).
REM
REM Ad-hoc use: start it whenever the machine is on, it runs for the given
REM duration and then ingests its own results into trading.db automatically.
REM
REM Config below MUST stay in sync with the "fair_value" entry in
REM kalshi/db/ingest.py STRATEGY_REGISTRY, otherwise db_ingest rejects the
REM run as a config mismatch. TTE [2.0, 14.0] is the window the backtest was
REM validated on.
REM
REM Duration is deliberately >= ~1h: with a 16-minute entry cutoff, shorter
REM sessions spend most of their time not entering anything.

cd /d "C:\Users\simon\trading-bot"

"C:\Users\simon\trading-bot\venv\Scripts\python.exe" -u runner.py ^
    --strategy fair_value ^
    --duration-minutes 120 ^
    --fair-value-no-side-only ^
    --fair-value-min-tte 2.0 ^
    --fair-value-max-tte 14.0 ^
    --fair-value-min-edge 0.03 ^
    --fair-value-vol-window 60 ^
    --entry-cutoff-minutes 16

"C:\Users\simon\trading-bot\venv\Scripts\python.exe" db_ingest.py --all
