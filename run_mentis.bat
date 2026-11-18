@echo off
chcp 65001 >nul
title Mentis AI Pipeline (SAFE)
cd /d "%~dp0"
echo ============================================================
echo  Mentis AI Pipeline - SAFE MODE
echo  Real scraping and real Gemini. Sends at most 1 TEST email
echo  to your own address. Use run_mentis_live.bat to send for real.
echo  Optional flags: --skip-scrape  --fake  --dry-run
echo ============================================================
python run_pipeline.py %*
pause
