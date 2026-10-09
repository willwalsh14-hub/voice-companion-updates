@echo off
setlocal
cd /d "%~dp0"
title Voice Companion update publishing
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish-windows.ps1"
if errorlevel 1 (
  echo Publishing did not finish. The installer remains on this computer.
  pause
  exit /b 1
)
echo Update published successfully.
pause
