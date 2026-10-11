@echo off
title Installing Trading Bot Libraries...
echo ===================================================
echo   Installing required libraries for Trading Bot
echo ===================================================
echo.

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo ===================================================
echo   Installation completed successfully!
echo ===================================================
pause
