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
python run_pipeline.py --live %*
pause
