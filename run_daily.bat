@echo off
REM %~dp0 is this script's own folder, so the task works wherever the repo lives.
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" -u runner.py ^
    --strategy favorite_longshot ^
    --duration-minutes 120 ^
    --trading-window 13:30-15:30
"%~dp0venv\Scripts\python.exe" db_ingest.py --all
