@echo off
title Launching Trading Bot Control Center...
cd /d "%~dp0"
echo ===================================================
echo     Starting Trading Bot Control Center
echo ===================================================
echo.
start http://localhost:8501
python -m streamlit run dashboard/app.py
pause
