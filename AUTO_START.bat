@echo off
rem =====================================================================
rem  SWING TRADING BOT - AUTO STARTUP LAUNCHER
rem  Launches MetaTrader 5 (if not already open), the Bot Engine, and Dashboard
rem =====================================================================

cd /d "%~dp0"

rem 1. Check if MetaTrader 5 is running; if not, launch it
tasklist /fi "imagename eq terminal64.exe" 2>NUL | find /i "terminal64.exe" >NUL
if "%ERRORLEVEL%"=="1" (
    echo Launching MetaTrader 5...
    if exist "C:\Program Files\MetaTrader 5\terminal64.exe" (
        start "" "C:\Program Files\MetaTrader 5\terminal64.exe"
        timeout /t 5 /nobreak >NUL
    )
)

rem 2. Launch Trading Bot scanning engine in background
start /min "SwingBotEngine" python run_bot.py

rem 3. Launch Dashboard Control Center in background and open browser
start /min "SwingBotDashboard" python -m streamlit run dashboard/app.py --server.headless=true
