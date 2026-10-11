@echo off
title Pushing Project to GitHub...
cd /d "%~dp0"
echo ========================================================
echo        PUSHING CODE TO GITHUB REPOSITORY
echo   https://github.com/iamfk02-ux/SWING-TRADING-AGENT
echo ========================================================
echo.
echo If a browser window opens, please click "Sign in" or "Authorize".
echo.
git push -u origin main
echo.
echo ========================================================
echo                   PUSH FINISHED!
echo ========================================================
pause
