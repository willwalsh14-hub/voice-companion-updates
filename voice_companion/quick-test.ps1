$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$installed = Join-Path $env:LOCALAPPDATA 'Programs\Voice Companion'
$installedModel = Join-Path $installed 'model'
if (-not (Test-Path (Join-Path $installedModel 'am\final.mdl'))) {
  throw 'Quick Test needs an existing Voice Companion installation with its offline speech model. Install a completed Setup build first.'
}
$env:VOICE_COMPANION_MODEL = $installedModel
$parakeet = Join-Path $installed 'model-parakeet'
if (Test-Path $parakeet) { $env:VOICE_COMPANION_PARAKEET_MODEL = $parakeet }
$browserRuntime = Join-Path $installed 'browser-runtime'
if (Test-Path $browserRuntime) { $env:PLAYWRIGHT_BROWSERS_PATH = $browserRuntime }

$quickEnv = Join-Path $env:LOCALAPPDATA 'VCQuick\py312'
$python = Join-Path $quickEnv 'Scripts\python.exe'
if (Test-Path $python) {
  & $python -c "import sys; assert sys.version_info[:2] == (3, 12)"
  if ($LASTEXITCODE -ne 0) { Remove-Item $quickEnv -Recurse -Force }
}
if (-not (Test-Path $python)) {
  if ($env:VOICE_COMPANION_BUILD_PYTHON) {
    & $env:VOICE_COMPANION_BUILD_PYTHON -m venv $quickEnv
  } else {
    py -3.12 -m venv $quickEnv
  }
  if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required for Quick Test. Run the installer builder once to prepare it.' }
}

# Keep dependencies in the short user-profile path. Playwright's own folders
# can exceed Windows' path limit when the ZIP is extracted into Downloads.
& $python -c "import vosk,sounddevice,win32crypt,playwright,google_auth_oauthlib,msal,docx"
if ($LASTEXITCODE -ne 0) {
  Write-Output 'Preparing Quick Test dependencies for the first run.'
  & $python -m pip install --disable-pip-version-check -r requirements.txt
  if ($LASTEXITCODE -ne 0) { throw 'Quick Test dependencies could not be prepared.' }
}
if (Test-Path $parakeet) {
  & $python -c "import onnx_asr,onnxruntime"
  if ($LASTEXITCODE -ne 0) {
    Write-Output 'Preparing Parakeet for Quick Test. This is needed only once.'
    & $python -m pip install --disable-pip-version-check 'onnx-asr[cpu,hub]==0.12.0'
    if ($LASTEXITCODE -ne 0) { throw 'Parakeet dependencies could not be prepared.' }
  }
}
& $python prepare_espeak.py (Join-Path $PSScriptRoot 'espeak')
if ($LASTEXITCODE -ne 0) { Write-Warning 'eSpeak setup did not finish. Windows and configured AI speech remain available.' }
& $python build_user_guides.py
if ($LASTEXITCODE -ne 0) { throw 'Quick Test could not generate the current accessible documentation.' }
& $python companion.py --check-runtime
if ($LASTEXITCODE -ne 0) { throw 'Quick Test could not verify its dependencies and speech model.' }
Write-Output 'Launching the latest source. Close Voice Companion to return to this window.'
& $python companion.py --window
if ($LASTEXITCODE -ne 0) { throw "Quick Test stopped with exit code $LASTEXITCODE." }

