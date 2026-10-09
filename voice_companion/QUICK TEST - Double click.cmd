@echo off
setlocal
cd /d "%~dp0"
title Voice Companion Quick Test
echo Starting the latest Voice Companion test code without building Setup.
echo Your existing installed app will remain unchanged.
echo Close the installed Voice Companion before continuing; this test uses the same accounts and documents.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0quick-test.ps1"
if errorlevel 1 (
  echo.
  echo Quick Test could not start. Read the error above.
  pause
  exit /b 1
)
echo.
echo Voice Companion closed.
pause
