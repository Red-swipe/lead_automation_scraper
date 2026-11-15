@echo off
title Mentis AI Lead Pipeline
cd /d "%~dp0"
echo ============================================
echo  Mentis AI Pipeline (DRY RUN by default)
echo  Live sending: run_mentis.bat --live
echo ============================================
python run_pipeline.py %*
pause
