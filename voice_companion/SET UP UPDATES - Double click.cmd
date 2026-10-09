@echo off
cd /d "%~dp0"
set "VC_UPDATE_PY=%LOCALAPPDATA%\vcbuild\py312\Scripts\python.exe"
if not exist "%VC_UPDATE_PY%" (
  echo Run the installer builder once first to prepare the private Python runtime.
  pause
  exit /b 1
)
"%VC_UPDATE_PY%" update_publisher.py
if errorlevel 1 pause
