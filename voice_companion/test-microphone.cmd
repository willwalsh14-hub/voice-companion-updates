@echo off
cd /d "%~dp0"
if not exist "VoiceCompanion-Diagnostics.exe" (
  echo VoiceCompanion-Diagnostics.exe is missing. Ask your trainer for the complete program folder.
  pause
  exit /b 1
)
echo This is a trainer-run test. The app will record seven seconds and read back what it heard.
echo The recording is kept in memory for this test and is not saved.
VoiceCompanion-Diagnostics.exe --record-test
if errorlevel 1 goto failed
echo Microphone test passed.
pause
exit /b 0
:failed
echo The microphone test failed. Share the output above with the trainer.
pause
exit /b 1
