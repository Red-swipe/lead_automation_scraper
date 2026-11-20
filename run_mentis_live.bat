@echo off
chcp 65001 >nul
title Mentis AI Pipeline (LIVE)
cd /d "%~dp0"
echo ============================================================
echo  Mentis AI Pipeline - LIVE MODE
echo  This can send REAL emails to real leads.
echo  You must type SEND ^<n^> at the prompt to confirm.
echo  Optional flags: --skip-scrape  --fake  --dry-run
echo ============================================================
set "PYTHONUTF8=1"
set "PYTHONWARNINGS=ignore::FutureWarning"
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 -c "import sys" >nul 2>&1
if errorlevel 1 goto try_python
py -3 -u run_pipeline.py --live %*
if not errorlevel 1 goto finished
:try_python
python -X utf8 -u run_pipeline.py --live %*
if errorlevel 1 echo Python was not found or failed to start.
:finished
pause
