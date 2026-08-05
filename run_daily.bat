@echo off
cd /d "C:\Users\simon\trading-bot"
"C:\Users\simon\trading-bot\venv\Scripts\python.exe" -u runner.py ^
    --strategy favorite_longshot ^
    --duration-minutes 120 ^
    --trading-window 13:30-15:30
"C:\Users\simon\trading-bot\venv\Scripts\python.exe" db_ingest.py --all
