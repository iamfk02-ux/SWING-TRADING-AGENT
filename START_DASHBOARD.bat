@echo off
title Multi-Agent Swing Trading Bot - Dashboard
echo ===================================================
echo     Launching Trading Control Center Dashboard
echo ===================================================
echo.
start http://localhost:8501
python -m streamlit run dashboard/app.py
echo.
pause
