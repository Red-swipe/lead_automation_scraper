@echo off
chcp 65001 >nul
title Mentis AI Pipeline (SAFE)
cd /d "%~dp0"
echo ============================================================
echo  Mentis AI Pipeline - SAFE MODE
echo  Real scraping and provider failover. Sends at most 1 TEST email
echo  to your own address. Use run_mentis_live.bat to send for real.
echo  Optional flags: --skip-scrape  --fake  --dry-run
echo ============================================================
set "PYTHONUTF8=1"
set "PYTHONWARNINGS=ignore::FutureWarning"
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 -c "import sys" >nul 2>&1
if errorlevel 1 goto try_python
py -3 -u run_pipeline.py %*
if not errorlevel 1 goto finished
:try_python
python -X utf8 -u run_pipeline.py %*
if errorlevel 1 echo Python was not found or failed to start.
:finished
pause
