# Build a portable Windows 11 folder. Run from PowerShell on a Windows PC.
param([switch]$Parakeet, [switch]$NoPublish, [switch]$Unattended)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
Start-Transcript -Path (Join-Path $PSScriptRoot 'VoiceCompanion-Build-Log.txt') -Force | Out-Null
$receiptPath = Join-Path $PSScriptRoot 'VoiceCompanion-Build-Passed.json'
Remove-Item -LiteralPath $receiptPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $PSScriptRoot 'VoiceCompanion-Published.json') -Force -ErrorAction SilentlyContinue
$previousSetup = Join-Path $PSScriptRoot 'VoiceCompanion-Setup.exe'
if (Test-Path $previousSetup) { Remove-Item $previousSetup -Force }
function Invoke-BuildPublishing([switch]$PrepareOnly) {
  if ($PrepareOnly) { Write-Output 'BUILD STAGE: Preparing GitHub sign-in. Follow any instructions in this window.' }
  else { Write-Output 'BUILD STAGE: Publishing the checked installer automatically. Keep this window open.' }
  $publishFailed = $false
  $publishFailureMessage = $null
  Stop-Transcript | Out-Null
  try {
    & (Join-Path $PSScriptRoot 'publish-windows.ps1') -Python $python -PrepareOnly:$PrepareOnly
  } catch {
    $publishFailed = $true
    $publishFailureMessage = $_.Exception.Message
    Write-Output ('GitHub step did not finish: ' + $publishFailureMessage)
  } finally {
    Start-Transcript -Path (Join-Path $PSScriptRoot 'VoiceCompanion-Build-Log.txt') -Append | Out-Null
  }
  if ($publishFailed) { Write-Output ('GitHub step did not finish: ' + $publishFailureMessage); throw 'GitHub step failed. Read the console and VoiceCompanion-Publish-Log.txt if available. The build has not reported publishing success.' }
  if ($PrepareOnly) { Write-Output 'BUILD STAGE: GitHub sign-in ready. Continuing with the installer build.' }
  else { Write-Output 'BUILD STAGE: Publishing finished. Checking the publication confirmation.' }
}
function Find-InnoCompiler {
  $onPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1
  if ($onPath) { return $onPath }
  foreach ($base in @("${env:ProgramFiles(x86)}", "${env:ProgramFiles}", "${env:LOCALAPPDATA}\Programs")) {
    if (-not $base -or -not (Test-Path $base)) { continue }
    $found = Get-ChildItem -LiteralPath $base -Directory -Filter 'Inno Setup *' -ErrorAction SilentlyContinue |
      ForEach-Object { Join-Path $_.FullName 'ISCC.exe' } |
      Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($found) { return $found }
  }
  return $null
}
$iscc = Find-InnoCompiler
if (-not $iscc) {
  if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
    throw 'Inno Setup compiler was not found and Windows Package Manager is unavailable.'
  }
  & winget install --id JRSoftware.InnoSetup --exact --source winget --scope user --silent --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) { throw 'Windows could not install the Inno Setup compiler.' }
  $iscc = Find-InnoCompiler
  if (-not $iscc) { throw 'Inno Setup installed but ISCC.exe could not be found. Check the build log.' }
}
Write-Output "Installer compiler: $iscc"
$buildEnv = Join-Path $env:LOCALAPPDATA 'VCBuild\py312'
$python = Join-Path $buildEnv 'Scripts\python.exe'
if (Test-Path $python) {
  & $python -c "import sys; assert sys.version_info[:2] == (3, 12)"
  if ($LASTEXITCODE -ne 0) { Remove-Item $buildEnv -Recurse -Force }
}
if (-not (Test-Path $python)) {
  if ($env:VOICE_COMPANION_BUILD_PYTHON) {
    & $env:VOICE_COMPANION_BUILD_PYTHON -m venv $buildEnv
  } else {
    py -3.12 -m venv $buildEnv
  }
  if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 could not create the build environment.' }
}
& $python -c "import sys; assert sys.version_info[:2] == (3, 12), 'Python 3.12 is required'"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required for the installer builder.' }
# Prepare publishing before the long build. Expected signed-out status and
# device-code guidance are handled by Python, not PowerShell's error stream.
if (-not $NoPublish) {
  Invoke-BuildPublishing -PrepareOnly
}
& $python -m pip install --disable-pip-version-check -r requirements.txt 'pyinstaller==6.22.3' 'pyinstaller-hooks-contrib==2026.7'
if ($LASTEXITCODE -ne 0) { throw 'Could not install the Windows build tools.' }
& $python build_user_guides.py
if ($LASTEXITCODE -ne 0) { throw 'The accessible user guides could not be generated or validated.' }
$releaseOutput = Join-Path $PSScriptRoot 'VoiceCompanion-Release-Tests.txt'
$releaseErrors = Join-Path $PSScriptRoot 'VoiceCompanion-Release-Errors.txt'
$releaseTests = Start-Process -FilePath $python -NoNewWindow -Wait -PassThru `
  -ArgumentList @('-m','unittest','-q','smoke_tests','test_email_delivery','test_mailbox',
    'test_web_assistant','test_document_formatting','test_text_selection','test_contacts_and_composing','test_media_hub','test_onboard_help','test_practice_tutorial','test_ai_speech','test_ai_voice_settings','test_voice_choices','test_build_packaging','test_gmail_refresh','test_dictation_text','test_workflow_regressions','test_draft_cancel','test_structural_selection','test_cross_area_selection','test_email_rich_formatting','test_document_controls','test_formatting_navigation','test_espeak_speech','test_command_latency','test_speech_controls','test_guide_files','test_app_updates','test_release_publishing','test_github_login','test_menu_prompts','test_sapi_build_check','test_settings', 'test_windows_actions','test_native_settings','test_keyboard_text','test_release_documents','test_menu_keyboard','test_accessibility_fixes','test_reading_responsiveness','test_email_reader') `
  -RedirectStandardOutput $releaseOutput -RedirectStandardError $releaseErrors
if (Test-Path $releaseErrors) { Get-Content $releaseErrors | Out-Host }
if ($releaseTests.ExitCode -ne 0) {
  if (Test-Path $releaseOutput) { Get-Content $releaseOutput | Out-Host }
  throw "The document, email, or browser release checks failed. See $releaseErrors"
}
& $python prepare_espeak.py (Join-Path $PSScriptRoot 'espeak')
if ($LASTEXITCODE -ne 0) { throw 'The local eSpeak speech runtime could not be prepared.' }
$browserRuntime = Join-Path $PSScriptRoot 'browser-runtime'
$env:PLAYWRIGHT_BROWSERS_PATH = $browserRuntime
& $python -m playwright install firefox
if ($LASTEXITCODE -ne 0) { throw 'The guided Firefox runtime could not be installed.' }
$modelZip = Join-Path $PSScriptRoot 'vosk-model.zip'
$modelFolder = Join-Path $PSScriptRoot 'model'
if (-not (Test-Path (Join-Path $modelFolder 'am\final.mdl'))) {
  if (Test-Path $modelFolder) { Remove-Item $modelFolder -Recurse -Force }
  $extract = Join-Path $PSScriptRoot 'model-extracted'
  if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
  Invoke-WebRequest -Uri 'https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip' -OutFile $modelZip
  Expand-Archive -Path $modelZip -DestinationPath $extract -Force
  Move-Item (Join-Path $extract 'vosk-model-small-en-us-0.15') $modelFolder -Force
}
$arguments = @('--clean', '--noconfirm', '--onedir', '--contents-directory', '.',
  '--add-data', 'update-settings.json:.', '--add-data', 'apply-update.ps1:.',
  '--add-data', 'model:model', '--add-data', 'browser-runtime:browser-runtime',
  '--add-data', 'documentation-manifest.json:.',
  '--add-data', 'START HERE - Veteran.txt:.',
  '--add-data', 'START HERE - Veteran.html:.',
  '--add-data', 'START HERE - Veteran.docx:.',
  '--add-data', 'START HERE - Veteran.epub:.',
  '--add-data', 'START HERE - Veteran.brf:.',
  '--add-data', 'Voice Companion Release Notes.txt:.',
  '--add-data', 'Voice Companion Release Notes.html:.',
  '--add-data', 'Voice Companion Release Notes.docx:.',
  '--add-data', 'Voice Companion Release Notes.epub:.',
  '--add-data', 'Voice Companion Release Notes - DAISY 3.zip:.',
  '--add-data', 'Voice Companion Release Notes.brf:.',
  '--add-data', 'START HERE - Veteran - DAISY 3.zip:.', '--hidden-import', 'sounddevice',
  '--hidden-import', 'pythoncom', '--hidden-import', 'pywintypes', '--hidden-import', 'win32crypt', '--hidden-import', 'win32clipboard', '--hidden-import', 'google_contacts', '--hidden-import', 'tkinter',
  '--collect-all', 'win32com', '--collect-all', 'vosk',
  '--collect-all', 'google_auth_oauthlib', '--collect-all', 'msal',
  '--collect-all', 'playwright')
if ($Parakeet) {
  & $python -m pip install --disable-pip-version-check 'onnx-asr[cpu,hub]==0.12.0'
  if ($LASTEXITCODE -ne 0) { throw 'Could not install Parakeet runtime.' }
  $env:VOICE_COMPANION_PARAKEET_FOLDER = Join-Path $PSScriptRoot 'model-parakeet'
  & $python -c "import os,onnx_asr; onnx_asr.load_model('nemo-parakeet-tdt-0.6b-v3', os.environ['VOICE_COMPANION_PARAKEET_FOLDER'], quantization='int8')"
  if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the Parakeet model.' }
  # Interrupted model downloads can leave large hidden partial files. The
  # completed encoder-model.int8.onnx is retained.
  Get-ChildItem -LiteralPath $env:VOICE_COMPANION_PARAKEET_FOLDER -File -Force |
    Where-Object { $_.Name -like '.encoder-model.int8.onnx.*' } |
    Remove-Item -Force
  $arguments += @('--add-data', 'model-parakeet:model-parakeet',
    '--collect-all', 'onnx_asr', '--collect-all', 'onnxruntime')
}
foreach ($oldBuild in @('dist\VoiceCompanion', 'dist\VoiceCompanion-Diagnostics')) {
  $oldPath = Join-Path $PSScriptRoot $oldBuild
  if (Test-Path $oldPath) { Remove-Item $oldPath -Recurse -Force }
}
$oldInstaller = Join-Path $PSScriptRoot 'dist\installer\VoiceCompanion-Setup.exe'
if (Test-Path $oldInstaller) { Remove-Item $oldInstaller -Force }
function Invoke-PyInstaller([string]$name, [string[]]$options) {
  $outputLog = Join-Path $PSScriptRoot "VoiceCompanion-$name-PyInstaller-Output.txt"
  $errorLog = Join-Path $PSScriptRoot "VoiceCompanion-$name-PyInstaller-Errors.txt"
  Write-Output "Packaging $name. Detailed logs: $outputLog and $errorLog"
  $optionsFile = Join-Path $PSScriptRoot "VoiceCompanion-$name-Packaging-Options.json"
  try {
    ConvertTo-Json -InputObject @($options) -Compress | Set-Content -LiteralPath $optionsFile -Encoding UTF8
    & $python (Join-Path $PSScriptRoot 'build_packaging.py') $optionsFile $outputLog $errorLog
    $packagingExitCode = $LASTEXITCODE
  } finally {
    Remove-Item -LiteralPath $optionsFile -Force -ErrorAction SilentlyContinue
  }
  if ($packagingExitCode -ne 0) {
    Write-Output "PyInstaller failed with exit code $packagingExitCode. Recent errors:"
    if (Test-Path $errorLog) { Get-Content -LiteralPath $errorLog -Tail 100 | Out-Host }
    Write-Output 'Recent standard output:'
    if (Test-Path $outputLog) { Get-Content -LiteralPath $outputLog -Tail 40 | Out-Host }
    throw "Could not package $name. Send both PyInstaller log files along with VoiceCompanion-Build-Log.txt."
  }
  Write-Output "$name packaged successfully."
}
$consoleArguments = @('--console', '--name', 'VoiceCompanion-Diagnostics') + $arguments
Invoke-PyInstaller 'Diagnostics' $consoleArguments
$windowArguments = @('--windowed', '--name', 'VoiceCompanion') + $arguments
Invoke-PyInstaller 'Application' $windowArguments
$diagnosticSource = Join-Path $PSScriptRoot 'dist\VoiceCompanion-Diagnostics\VoiceCompanion-Diagnostics.exe'
$diagnosticTarget = Join-Path $PSScriptRoot 'dist\VoiceCompanion\VoiceCompanion-Diagnostics.exe'
if (-not (Test-Path $diagnosticSource)) { throw 'The diagnostic executable is missing.' }
Copy-Item $diagnosticSource $diagnosticTarget -Force
$exe = Join-Path $PSScriptRoot 'dist\VoiceCompanion\VoiceCompanion.exe'
& $diagnosticTarget --check-runtime
if ($LASTEXITCODE -ne 0) { throw 'A packaged runtime dependency is missing.' }
& $exe --check-update-environment
if ($LASTEXITCODE -ne 0) { throw 'The packaged updater inherited application files or its working directory.' }
& $exe --check-runtime
if ($LASTEXITCODE -ne 0) { throw 'The quiet application is missing a runtime dependency.' }
$modelCheck = & $diagnosticTarget --check-model
if ($LASTEXITCODE -ne 0) {
  $modelCheck | Out-Host
  throw 'The packaged speech model did not load. Read the error above.'
}
if ($Parakeet -and -not (($modelCheck -join ' ') -match 'Parakeet model loaded and inference completed')) {
  $modelCheck | Out-Host
  throw 'The packaged Parakeet model did not complete an inference check.'
}
if ($Unattended) {
  & $diagnosticTarget --check-speech-file
  if ($LASTEXITCODE -ne 0) { throw 'The packaged Windows voice did not generate valid speech audio.' }
  & $exe --check-speech-file
  if ($LASTEXITCODE -ne 0) { throw 'The quiet Windows application did not generate valid speech audio.' }
} else {
  & $diagnosticTarget --check-speech
  if ($LASTEXITCODE -ne 0) { throw 'The packaged Windows SAPI voice could not start.' }
  & $exe --check-speech
  if ($LASTEXITCODE -ne 0) { throw 'The quiet Windows application could not start speech.' }
  & $diagnosticTarget --check-speech-control
  if ($LASTEXITCODE -ne 0) { throw 'The speech pause, resume, or interruption check failed.' }
}
$testData = Join-Path $PSScriptRoot '.build-smoke-data'
if (Test-Path $testData) { Remove-Item $testData -Recurse -Force }
try {
  $env:VOICE_COMPANION_DATA_DIR = $testData
  $testCommands = "wake up`ncreate a document`nname document Build check`nstart dictation`nThis is a test sentence.`npause dictation`nread paragraph`nleave document`nyes`nBuild check`nokay`nwrite an email`nuse outlook`nemail to example@example.com`nsubject is Build test`nstart dictation`nThis is a local email draft.`npause dictation`nsend email`nshut down companion`n"
  $testOutput = $testCommands | & $diagnosticTarget --text-mode
  if ($LASTEXITCODE -ne 0 -or -not (($testOutput -join ' ') -match 'This is a test sentence') -or
      -not (($testOutput -join ' ') -match 'This is a local email draft') -or
      -not (($testOutput -join ' ') -match 'It has not been sent')) {
    $testOutput | Out-Host
    throw 'The packaged document or email draft workflow failed its text-mode check. Read the output above.'
  }
  if (-not (Test-Path (Join-Path $testData 'Documents\Build check.docx')) -or
      -not (Get-ChildItem (Join-Path $testData 'Email Drafts') -Filter '*.eml')) {
    throw 'The packaged program did not write its test document and email draft.'
  }
} finally {
  Remove-Item Env:\VOICE_COMPANION_DATA_DIR -ErrorAction SilentlyContinue
  if (Test-Path $testData) { Remove-Item $testData -Recurse -Force }
}
Copy-Item (Join-Path $PSScriptRoot 'check-setup.cmd') (Join-Path $PSScriptRoot 'dist\VoiceCompanion\check-setup.cmd') -Force
Copy-Item (Join-Path $PSScriptRoot 'test-microphone.cmd') (Join-Path $PSScriptRoot 'dist\VoiceCompanion\test-microphone.cmd') -Force
foreach ($guide in @('documentation-manifest.json', 'START HERE - Veteran.txt', 'START HERE - Veteran.html', 'START HERE - Veteran.docx', 'START HERE - Veteran.epub', 'START HERE - Veteran - DAISY 3.zip', 'START HERE - Veteran.brf', 'Voice Companion Release Notes.txt', 'Voice Companion Release Notes.html', 'Voice Companion Release Notes.docx', 'Voice Companion Release Notes.epub', 'Voice Companion Release Notes - DAISY 3.zip', 'Voice Companion Release Notes.brf')) {
  Copy-Item (Join-Path $PSScriptRoot $guide) (Join-Path $PSScriptRoot 'dist\VoiceCompanion') -Force
}
Copy-Item (Join-Path $PSScriptRoot 'TRAINER TEST CHECKLIST.txt') (Join-Path $PSScriptRoot 'dist\VoiceCompanion\TRAINER TEST CHECKLIST.txt') -Force
Copy-Item (Join-Path $PSScriptRoot 'README.txt') (Join-Path $PSScriptRoot 'dist\VoiceCompanion\README.txt') -Force
Copy-Item (Join-Path $PSScriptRoot 'EMAIL SETUP - Helper.txt') (Join-Path $PSScriptRoot 'dist\VoiceCompanion\EMAIL SETUP - Helper.txt') -Force
$googleRegistration = Join-Path $PSScriptRoot 'google-client.json'
if (Test-Path $googleRegistration) {
  & $python -c "import json,sys; c=json.load(open(sys.argv[1],encoding='utf-8')); allowed={'client_id','project_id','auth_uri','token_uri','auth_provider_x509_cert_url','client_secret','redirect_uris'}; assert set(c)=={'installed'} and set(c['installed']) <= allowed, 'Only a public Google Desktop OAuth registration may be packaged; user tokens and server credentials are forbidden'; assert all(isinstance(v,str) for k,v in c['installed'].items() if k!='redirect_uris'); assert c['installed']['client_id'] and c['installed']['client_secret'] and c['installed']['auth_uri'] and c['installed']['token_uri']" $googleRegistration
  if ($LASTEXITCODE -ne 0) { throw 'The supplied Google Desktop app registration is invalid.' }
  Copy-Item $googleRegistration (Join-Path $PSScriptRoot 'dist\VoiceCompanion\google-client.json') -Force
}
# Ship the distributor public Microsoft registration beside the executable.
$microsoftRegistration = Join-Path $PSScriptRoot 'microsoft-registration.json'
if (Test-Path $microsoftRegistration) {
  & $python -c "import json,sys,uuid; c=json.load(open(sys.argv[1],encoding='utf-8')); uuid.UUID(c['microsoft_client_id'])" $microsoftRegistration
  if ($LASTEXITCODE -ne 0) { throw 'The supplied Microsoft desktop app registration is invalid.' }
  Copy-Item $microsoftRegistration (Join-Path $PSScriptRoot 'dist\VoiceCompanion\microsoft-registration.json') -Force
}
Copy-Item (Join-Path $PSScriptRoot 'espeak') (Join-Path $PSScriptRoot 'dist\VoiceCompanion\espeak') -Recurse -Force
& $iscc (Join-Path $PSScriptRoot 'VoiceCompanion.iss')
if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $PSScriptRoot 'dist\installer\VoiceCompanion-Setup.exe'))) {
  throw 'The Windows installer could not be created.'
}
$setup = Join-Path $PSScriptRoot 'dist\installer\VoiceCompanion-Setup.exe'
$portableSetup = Join-Path $PSScriptRoot 'VoiceCompanion-Setup.exe'
Copy-Item -LiteralPath $setup -Destination $portableSetup -Force
if ((Get-FileHash -LiteralPath $portableSetup -Algorithm SHA256).Hash -ne
    (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash) {
  throw 'The copied Setup file differs from the built installer.'
}
Write-Output "SETUP CREATED: $portableSetup"
Write-Output "Starting the installer: $setup"
if ($Unattended) {
  Write-Output 'BUILD STAGE: Testing installation without user interaction.'
  $installation = Start-Process -FilePath $setup -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-') -PassThru
} else {
  $installation = Start-Process -FilePath $setup -PassThru
}
$installation.WaitForExit()
if ($installation.ExitCode -ne 0) { throw "Setup did not complete. Exit code: $($installation.ExitCode)" }
$installedFolder = Join-Path $env:LOCALAPPDATA 'Programs\Voice Companion'
$installedDiagnostics = Join-Path $installedFolder 'VoiceCompanion-Diagnostics.exe'
$installedApp = Join-Path $installedFolder 'VoiceCompanion.exe'
if (-not (Test-Path $installedApp) -or -not (Test-Path $installedDiagnostics)) {
  throw 'Setup finished, but the installed application files were not found.'
}
if ((Get-FileHash -LiteralPath $installedApp -Algorithm SHA256).Hash -ne
    (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -or
    (Get-FileHash -LiteralPath $installedDiagnostics -Algorithm SHA256).Hash -ne
    (Get-FileHash -LiteralPath $diagnosticTarget -Algorithm SHA256).Hash) {
  throw 'The installed EXE files do not match this build. Do not use the old shortcut.'
}
if (Test-Path $googleRegistration) {
  $installedGoogle = Join-Path $installedFolder 'google-client.json'
  if (-not (Test-Path $installedGoogle) -or
      (Get-FileHash -LiteralPath $installedGoogle -Algorithm SHA256).Hash -ne
      (Get-FileHash -LiteralPath $googleRegistration -Algorithm SHA256).Hash) {
    throw 'The installed Gmail registration is missing or does not match this build.'
  }
}
$installedVersion = & $installedDiagnostics --version
if ($LASTEXITCODE -ne 0 -or -not (($installedVersion -join ' ') -match '^Voice Companion 0\.2\.104-test$')) {
  throw 'The installed program is not the current Voice Companion build.'
}
& $installedDiagnostics --refresh-guides
if ($LASTEXITCODE -ne 0) { throw 'The installed documentation did not copy and verify in Documents.' }
& $installedDiagnostics --check-runtime
if ($LASTEXITCODE -ne 0) { throw 'The installed program is missing a runtime component.' }
Write-Output 'INSTALL PASSED. Voice Companion 0.2.104-test is installed. Use the desktop icon or Start menu.'
if ($Unattended) {
  & (Join-Path $PSScriptRoot 'test-update-audio.ps1')
  if ($LASTEXITCODE -ne 0) { throw 'Updater microphone release checks failed.' }
  Write-Output 'BUILD STAGE: Testing the updater helper with a silent installation.'
  $updateCheck = Join-Path $PSScriptRoot '.update-helper-check'
  New-Item -ItemType Directory -Path $updateCheck -Force | Out-Null
  try {
    & (Join-Path $PSScriptRoot 'apply-update.ps1') -InstallerPath $portableSetup -AppPath $installedApp -PreviousProcessId -1 -ExpectedHash (Get-FileHash $portableSetup -Algorithm SHA256).Hash -DataFolder $updateCheck -ReadyFile (Join-Path $updateCheck 'ready.json') -NoSpeech -NoRestart
    $updateResult = Get-Content (Join-Path $updateCheck 'update-result.json') -Raw | ConvertFrom-Json
    if (-not $updateResult.succeeded) { throw 'The updater helper did not install successfully.' }
    $installPercent = Get-Content (Join-Path $PSScriptRoot 'install-percent.txt') -Raw
    if ([int]$installPercent -lt 1) { throw 'Setup did not report installation progress.' }
    Write-Output 'Updater helper silent installation and progress checks passed.'
  } finally {
    Remove-Item $updateCheck -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $PSScriptRoot 'install-percent.txt') -Force -ErrorAction SilentlyContinue
  }
}

foreach ($staging in @('dist\VoiceCompanion', 'dist\VoiceCompanion-Diagnostics')) {
  Remove-Item (Join-Path $PSScriptRoot $staging) -Recurse -Force
}
Write-Output "COPY THIS INSTALLER TO OTHER COMPUTERS: $portableSetup"
Write-Output 'The temporary runnable EXEs have been removed to avoid confusing them with Setup.'

# Record exactly the installer that passed all packaged and installed checks.
@{ version = '0.2.104-test'; sha256 = (Get-FileHash -LiteralPath $portableSetup -Algorithm SHA256).Hash.ToLowerInvariant(); size = (Get-Item -LiteralPath $portableSetup).Length } |
  ConvertTo-Json | Set-Content -LiteralPath $receiptPath -Encoding UTF8
if (-not $NoPublish) {
  Invoke-BuildPublishing
  $publishedPath = Join-Path $PSScriptRoot 'VoiceCompanion-Published.json'
  if (-not (Test-Path -LiteralPath $publishedPath)) { throw 'The publisher did not return a success confirmation. Publishing is not confirmed.' }
  $published = Get-Content -LiteralPath $publishedPath -Raw | ConvertFrom-Json
  $built = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
  if ($published.version -ne $built.version -or $published.sha256 -ne $built.sha256) {
    throw 'The publication confirmation does not match this installer. Publishing is not confirmed.'
  }
  Write-Output ('BUILD, INSTALL, AND PUBLISH PASSED: ' + $published.url)
} else {
  Write-Output 'Local build only. Publishing was skipped.'
}
Stop-Transcript | Out-Null




