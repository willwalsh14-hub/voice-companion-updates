@echo off
setlocal
cd /d "%~dp0"
title Voice Companion Windows builder
echo Building Voice Companion for Windows. Keep this window open.
echo Internet access and several gigabytes of free space are needed.
echo This can take a while, especially when downloading the offline speech files.
echo.
call :find_python
if defined VOICE_COMPANION_BUILD_PYTHON goto build
py -3.12 -c "import sys; assert sys.version_info[:2] == (3,12)" >nul 2>&1
if not errorlevel 1 goto build
echo Python 3.12 is not present. Trying Windows Package Manager.
where winget >nul 2>&1
if errorlevel 1 goto no_winget
winget install --id Python.Python.3.12 --exact --source winget --scope user --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto python_failed
call :find_python
if defined VOICE_COMPANION_BUILD_PYTHON goto build
py -3.12 -c "import sys; assert sys.version_info[:2] == (3,12)" >nul 2>&1
if errorlevel 1 goto python_failed
:build
echo.
echo Checking installer builder. GitHub sign-in is prepared automatically before the long build.
echo Building the offline Parakeet version and checking the packaged program.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-windows.ps1" -Parakeet
if errorlevel 1 goto build_failed
if not exist "%~dp0VoiceCompanion-Setup.exe" goto build_failed
echo.
echo BUILD, INSTALL, AND PUBLISH PASSED. Open Voice Companion from the desktop icon or Start menu.
echo To install on another computer, copy VoiceCompanion-Setup.exe from this folder.
pause
exit /b 0
:find_python
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "VOICE_COMPANION_BUILD_PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined VOICE_COMPANION_BUILD_PYTHON if exist "%ProgramFiles%\Python312\python.exe" set "VOICE_COMPANION_BUILD_PYTHON=%ProgramFiles%\Python312\python.exe"
exit /b 0
:no_winget
echo Windows Package Manager was not found. A trainer must install Python 3.12.
goto failed
:python_failed
echo Python 3.12 could not be installed or found. Share this window with the trainer.
goto failed
:build_failed
echo The build, installation checks, or publishing did not finish. Read the error above.
echo Run this installer builder again after resolving the error. Sign-in and publishing are included.
echo Share the error shown above with the trainer.
if exist "%~dp0VoiceCompanion-Build-Log.txt" (
  echo Opening the saved build log in Notepad.
  start "" notepad.exe "%~dp0VoiceCompanion-Build-Log.txt"
)
:failed
pause
exit /b 1
