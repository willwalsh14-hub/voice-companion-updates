@echo off
cd /d "%~dp0"
if not exist "VoiceCompanion-Diagnostics.exe" (
  echo VoiceCompanion-Diagnostics.exe is missing. Ask your trainer for the complete program folder.
  pause
  exit /b 1
)
echo Checking bundled speech models. This may take a minute.
VoiceCompanion-Diagnostics.exe --check-runtime
if errorlevel 1 goto failed
VoiceCompanion-Diagnostics.exe --check-model
if errorlevel 1 goto failed
echo.
echo Checking the microphone and speaker. You should hear a spoken test sentence.
VoiceCompanion-Diagnostics.exe --check-audio
if errorlevel 1 goto failed
echo.
echo With JAWS turned off, did you hear Voice Companion itself speak?
choice /c YN /n /m "Press Y for yes or N for no: "
if errorlevel 2 goto failed_speech
echo.
echo Checks completed. Try saying Wake up after opening VoiceCompanion.exe.
pause
exit /b 0
:failed
echo.
echo A check failed. Please share the text above with the trainer.
pause
exit /b 1
:failed_speech
echo.
echo Speech was not heard. This build is not ready for use without a screen reader.
echo Ask the trainer to check Windows sound output and the installed SAPI voice.
pause
exit /b 1
