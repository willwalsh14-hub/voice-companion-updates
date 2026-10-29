# Build a portable Windows 11 folder. Run from PowerShell on a Windows PC.
param([switch]$Parakeet, [switch]$NoPublish, [switch]$Unattended)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
# On GitHub, put the reason a build stopped into the run's annotations, which
# can be read without the full log.
function Write-BuildAnnotation([string]$Title, [string]$Message) {
  if (-not $env:GITHUB_ACTIONS) { return }
  $clean = ($Message -replace '%','%25' -replace "`r",'' -replace "`n",'%0A')
  # Keep the start (the failed test names) and the end (the last output).
  # Keeping only the end once hid which tests had failed.
  if ($clean.Length -gt 3500) { $clean = $clean.Substring(0, 1200) + '%0A...%0A' + $clean.Substring($clean.Length - 2300) }
  Write-Output ('::error title=' + $Title + '::' + $clean)
}
# Run a unittest step and, on GitHub, put the failed test names and the end of
# their output into the run annotations (the full log cannot be read remotely).
function Invoke-UnitTests([string]$Title, [string[]]$Arguments) {
  $log = Join-Path $PSScriptRoot ('VoiceCompanion-Tests-' + ($Title -replace '[^A-Za-z0-9]','') + '.txt')
  $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  & $python -m unittest @Arguments 2>&1 | ForEach-Object { $line = "$_"; if ($line -eq 'System.Management.Automation.RemoteException') { '' } else { $line } } | Tee-Object -FilePath $log | Out-Host
  $code = $LASTEXITCODE; $ErrorActionPreference = $previous
  if ($code -ne 0 -and (Test-Path $log)) {
    $failed = @(Select-String -LiteralPath $log -Pattern '^(FAIL|ERROR): ' | ForEach-Object { $_.Line })
    $tail = (Get-Content -LiteralPath $log -Tail 60) -join "`n"
    Write-BuildAnnotation ($Title + ' failed') ((($failed | Select-Object -First 15) -join "`n") + "`n---`n" + $tail) | Out-Host
    # One short annotation per failed test with its error line, so every failure
    # names its reason even when the combined output above is cut short.
    $current = $null; $reasons = 0
    foreach ($line in (Get-Content -LiteralPath $log)) {
      if ($line -match '^(FAIL|ERROR): (\S+)') { $current = $Matches[2]; continue }
      if ($current -and $line -match '^[A-Za-z_][\w.]*(Error|Exception|Exit|Failure)\b') {
        $reason = if ($line.Length -gt 2400) { $line.Substring(0, 2400) + '...' } else { $line }
        if ($reasons -lt 8) { Write-BuildAnnotation ($Title + ': ' + $current) $reason | Out-Host }
        $reasons += 1; $current = $null
      }
    }
  }
  return $code
}
# Run a packaged self-check and, on GitHub, put its own output (the error and
# traceback) into the run annotations, so a failed check names its reason.
function Invoke-PackagedCheck([string]$Title, [string]$Program, [string[]]$Arguments) {
  $log = Join-Path $PSScriptRoot ('VoiceCompanion-Check-' + ($Title -replace '[^A-Za-z0-9]','') + '.txt')
  $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  & $Program @Arguments 2>&1 | ForEach-Object { $line = "$_"; if ($line -eq 'System.Management.Automation.RemoteException') { '' } else { $line } } | Tee-Object -FilePath $log | Out-Host
  $code = $LASTEXITCODE; $ErrorActionPreference = $previous
  if ($code -ne 0) {
    $tail = if (Test-Path $log) { (Get-Content -LiteralPath $log -Tail 60) -join "`n" } else { '(no output)' }
    Write-BuildAnnotation ($Title + ' failed (exit code ' + $code + ')') $tail | Out-Host
  }
  return $code
}
trap {
  Write-BuildAnnotation 'Build stopped' $_.Exception.Message
  break
}
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
# Replace the older pip bundled with Python before installing build dependencies.
& $python -m pip install --disable-pip-version-check --upgrade 'pip==26.2.1'
if ($LASTEXITCODE -ne 0) { throw 'Could not update the package installer securely.' }
& $python -m pip install --disable-pip-version-check -r requirements.txt 'pyinstaller==6.22.3' 'pyinstaller-hooks-contrib==2026.7'
# Build PyInstaller's launcher here, from source, rather than using the stock
# launcher shared by every PyInstaller program. Antivirus tools learn the stock
# launcher from malware that also uses it, and then flag Voice Companion too.
$env:PYINSTALLER_COMPILE_BOOTLOADER = '1'
& $python -m pip install --disable-pip-version-check --force-reinstall --no-deps --no-binary pyinstaller 'pyinstaller==6.22.3'
$bootloaderExit = $LASTEXITCODE
Remove-Item Env:PYINSTALLER_COMPILE_BOOTLOADER
if ($bootloaderExit -ne 0) { throw 'PyInstaller could not build its own launcher from source. Check that the Visual Studio C++ build tools are installed.' }
$bootloader = & $python -c "import pathlib, PyInstaller; print(pathlib.Path(PyInstaller.__file__).parent / 'bootloader' / 'Windows-64bit-intel' / 'runw.exe')"
if (-not (Test-Path -LiteralPath $bootloader)) { throw "PyInstaller's launcher was not built: $bootloader" }
Write-Output ('PyInstaller launcher built here: ' + (Get-FileHash -LiteralPath $bootloader -Algorithm SHA256).Hash)
if ($LASTEXITCODE -ne 0) { throw 'Could not install the Windows build tools.' }
# Keep the compiler's output so a failure names the line and the error (logs cannot be read remotely).
$webviewLog = Join-Path $PSScriptRoot 'VoiceCompanion-WebView2-Build.txt'
$previousPreference = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
& $python build_webview2.py 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $webviewLog | Out-Host
$webviewExit = $LASTEXITCODE; $ErrorActionPreference = $previousPreference
if ($webviewExit -ne 0) {
  $errors = (Get-Content -LiteralPath $webviewLog -ErrorAction SilentlyContinue | Where-Object { $_ -match 'error|warning CS|\.cs\(' }) -join "`n"
  if (-not $errors) { $errors = (Get-Content -LiteralPath $webviewLog -Tail 40 -ErrorAction SilentlyContinue) -join "`n" }
  Write-BuildAnnotation 'Embedded Edge host compile errors' $errors
  throw 'The embedded Edge host could not be built.'
}
$env:VOICE_COMPANION_REQUIRE_WEBVIEW2 = '1'
$embeddedResult = Invoke-UnitTests 'Embedded Edge checks' @('-q','-f','test_webview2.EmbeddedLiveTests','test_webview2.EmbeddedConsumerTests','test_webview2.EmbeddedSemanticTests','test_webview2.EmbeddedPerformanceTests','test_youtube_webview2')
if ($embeddedResult -ne 0) { throw 'Embedded Edge voice and keyboard browser parity checks failed.' }
Remove-Item Env:VOICE_COMPANION_REQUIRE_WEBVIEW2
$extensionTestRuntime = Join-Path $buildEnv 'extension-test-browser'
$env:PLAYWRIGHT_BROWSERS_PATH = $extensionTestRuntime
& $python -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'The fixture browser for extension integration checks could not be prepared.' }
Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH
& $python build_user_guides.py
if ($LASTEXITCODE -ne 0) { throw 'The accessible user guides could not be generated or validated.' }
& $python -m offline_ocr
if ($LASTEXITCODE -ne 0) { throw 'The bundled offline OCR models could not load.' }
$releaseOutput = Join-Path $PSScriptRoot 'VoiceCompanion-Release-Tests.txt'
$releaseErrors = Join-Path $PSScriptRoot 'VoiceCompanion-Release-Errors.txt'
# Real-window key tests run afterwards, each class in its own process (real_window_tests.py).
$env:VOICE_COMPANION_WINDOW_TESTS_APART = '1'
$releaseTests = Start-Process -FilePath $python -NoNewWindow -Wait -PassThru `
  -ArgumentList @('-m','unittest','-q','smoke_tests','test_email_delivery','test_mailbox','test_chrome_setup',
    'test_media_update_fixes','test_web_fixes','test_document_fixes','test_core_email_fixes','test_storage','test_embedded_edge_default','test_thunderbird_mail','test_thunderbird_setup','test_web_performance',
    'test_web_assistant','test_browser_recovery','test_web_browse','test_browser_accessibility','test_browser_media_polish','test_virtual_browser','test_document_formatting','test_text_selection','test_contacts_and_composing','test_media_hub','test_onboard_help','test_practice_tutorial','test_ai_speech','test_ai_voice_settings','test_voice_choices','test_build_packaging','test_dictation_text','test_ai_dictation','test_dictation_echo','test_file_manager','test_file_explorer','test_input_parity','test_task_navigation','test_media_player_menu','test_document_reader','test_rich_documents','test_workflow_regressions','test_draft_cancel','test_structural_selection','test_cross_area_selection','test_email_rich_formatting','test_document_controls','test_formatting_navigation','test_espeak_speech','test_command_latency','test_speech_controls','test_guide_files','test_app_updates','test_release_publishing','test_github_login','test_menu_prompts','test_sapi_build_check','test_settings', 'test_windows_actions','test_native_settings','test_keyboard_text','test_release_documents','test_menu_keyboard','test_list_boundaries','test_accessibility_fixes','test_browser_menu','test_documentation_quality','test_reading_responsiveness','test_email_reader','test_email_status_startup','test_consumer_workflows','test_consumer_regressions','test_browser_bridge','test_regular_browser','test_browser_cdp_relay','test_launch_review_fixes','test_notepad_and_document_options','test_removed_mail_accounts','test_yahoo_setup','test_yahoo_mail','test_screen_menus','test_web_modes','test_web_history','test_web_fields','test_email_accounts','test_audio_ducking','test_time_date','test_time_date_windows','test_thunderbird_accounts','test_user_stop','test_key_state','test_microphone_switch','test_microphone_off_startup','test_shared_playwright','test_document_reader_menu','test_guide_html_accessibility','test_main_menu_key','test_notepad_menu','test_heading_links','test_email_links_open_in_guided_browser','test_downloads','test_spoken_dates','test_contacts_screen','test_calendar_screen','test_email_attachments','test_attachment_flows','test_email_docs_audit','test_attachment_voice','test_display_options','test_startup_announcement','test_core_polish','test_speech_transcript','test_web_media_audit','test_site_audit_report','test_update_close','test_document_save_as','test_consumer_walkthrough','test_consumer_journeys','test_main_menu_document_words','test_calendar_contacts_sources','test_undo_redo','test_message_headers','test_hint_line','test_window_widget_release','test_media_web_answers','test_youtube') `
  -RedirectStandardOutput $releaseOutput -RedirectStandardError $releaseErrors
if (Test-Path $releaseErrors) { Get-Content $releaseErrors | Out-Host }
if ($releaseTests.ExitCode -ne 0) {
  if (Test-Path $releaseOutput) { Get-Content $releaseOutput | Out-Host }
  if (Test-Path $releaseErrors) {
    $failed = @(Select-String -LiteralPath $releaseErrors -Pattern '^(FAIL|ERROR): ' | ForEach-Object { $_.Line })
    $tail = (Get-Content -LiteralPath $releaseErrors -Tail 40) -join "`n"
    Write-BuildAnnotation 'Release tests failed' ((($failed | Select-Object -First 15) -join "`n") + "`n---`n" + $tail)
  }
  throw "The document, email, or browser release checks failed. See $releaseErrors"
}
Remove-Item Env:VOICE_COMPANION_WINDOW_TESTS_APART
foreach ($windowTests in @('test_keyboard_text.WindowKeyboardTests','test_input_parity.WindowScreenKeyTests','test_time_date_windows.WindowClockKeyTests','test_alt_keys_window.AltKeyWindowTests','test_save_as_window.SaveAsKeyWindowTests','test_now_playing_window.WindowNowPlayingKeyTests','test_header_keys_window.HeaderKeyWindowTests','test_youtube_window.YouTubeWindowKeyTests')) {
  if ((Invoke-UnitTests ('Window key tests ' + $windowTests) @('-v', $windowTests)) -ne 0) { throw ('The real window key tests failed: ' + $windowTests + '. See the annotation.') }
}
& $python prepare_espeak.py (Join-Path $PSScriptRoot 'espeak')
if ($LASTEXITCODE -ne 0) { throw 'The local eSpeak speech runtime could not be prepared.' }
& $python check_security.py
if ($LASTEXITCODE -ne 0) { throw 'Source security checks failed.' }
& $python -m pip install pip-audit==2.10.1
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the dependency security audit.' }
& $python -m pip_audit --strict --format json --output security-dependency-audit.json
if ($LASTEXITCODE -ne 0) {
  $findings = @()
  if (Test-Path 'security-dependency-audit.json') {
    $audit = Get-Content -Raw 'security-dependency-audit.json' | ConvertFrom-Json
    foreach ($dependency in $audit.dependencies) {
      foreach ($finding in $dependency.vulns) {
        $line = 'Security finding: ' + $dependency.name + ' ' + $dependency.version + '; ' + $finding.id + '; fixed versions: ' + ($finding.fix_versions -join ', ')
        Write-Output $line; $findings += $line
      }
    }
  }
  if (-not $findings) { $findings = @('No findings were recorded. The audit service may have been unreachable.') }
  throw ('Dependency security audit failed. ' + ($findings -join ' | '))
}
$securityResult = Invoke-UnitTests 'Privacy and security checks' @('test_security_privacy','-v')
if ($securityResult -ne 0) { throw 'Privacy or security checks failed.' }
$recoveryResult = Invoke-UnitTests 'Updater recovery checks' @('test_update_recovery','-v')
if ($recoveryResult -ne 0) { throw 'Updater recovery and bookmark opening checks failed.' }
# The bundled Firefox engine plays radio, podcasts and media files (media_player.py).
# Guided websites use embedded Edge or Chrome, both Chromium browsers.
$browserRuntime = Join-Path $PSScriptRoot 'browser-runtime'
$env:PLAYWRIGHT_BROWSERS_PATH = $browserRuntime
& $python -m playwright install firefox
if ($LASTEXITCODE -ne 0) { throw 'The Firefox engine for the audio player could not be installed.' }
$env:VOICE_COMPANION_REQUIRE_WEB_TESTS = '1'
$audioResult = Invoke-UnitTests 'Audio player checks' @('-q','test_consumer_audio','test_radio_stream_live')
if ($audioResult -ne 0) { throw 'The audio player checks failed.' }
# Page checks run in Playwright's Chromium (prepared above for the extension checks).
$env:PLAYWRIGHT_BROWSERS_PATH = $extensionTestRuntime
$liveResult = Invoke-UnitTests 'Real browser checks' @('-v','test_web_live','test_consumer_web','test_web_semantics','test_web_modes_live','test_web_history','test_web_fields','test_web_media_live','test_youtube_live')
if ($liveResult -ne 0) { throw 'The real browser page and application keyboard accessibility checks failed.' }
$env:PLAYWRIGHT_BROWSERS_PATH = $browserRuntime
Remove-Item Env:VOICE_COMPANION_REQUIRE_WEB_TESTS
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
  '--add-data', 'update-settings.json:.', '--add-data', 'apply-update.ps1:.', '--add-data', 'close-companion.ps1:.',
  '--add-data', 'model:model', '--add-data', 'browser-runtime:browser-runtime', '--add-data', 'webview2:webview2',
  '--add-data', 'documentation-manifest.json:.',
  '--add-data', 'accessible_names.js:.', '--add-data', 'accessible_names.LICENSE.txt:.',
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
  '--hidden-import', 'pythoncom', '--hidden-import', 'pywintypes', '--hidden-import', 'win32crypt', '--hidden-import', 'win32clipboard', '--hidden-import', 'tkinter', '--hidden-import', 'chrome_setup', '--hidden-import', 'audio_ducking', '--hidden-import', 'key_state', '--hidden-import', 'downloads', '--hidden-import', 'download_screen', '--hidden-import', 'contacts_book', '--hidden-import', 'contacts_screen', '--hidden-import', 'calendar_events', '--hidden-import', 'calendar_screen', '--hidden-import', 'spoken_dates', '--hidden-import', 'thunderbird_pim', '--hidden-import', 'pim_sources', '--hidden-import', 'attachment_screen', '--hidden-import', 'youtube_page', '--hidden-import', 'youtube_screen', '--hidden-import', 'mail_attachments', '--hidden-import', 'imap_structure',
  '--hidden-import', 'thunderbird_bridge', '--hidden-import', 'thunderbird_mailbox', '--hidden-import', 'thunderbird_setup', '--hidden-import', 'thunderbird_accounts',
  '--collect-all', 'win32com', '--collect-all', 'vosk',
  '--collect-all', 'playwright', '--collect-all', 'rapidocr', '--collect-all', 'onnxruntime',
  '--collect-all', 'pypdfium2', '--collect-all', 'pypdfium2_raw', '--collect-all', 'shapely',
  '--collect-all', 'pyclipper', '--collect-all', 'omegaconf', '--hidden-import', 'rapidocr.inference_engine.onnxruntime')
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
function Invoke-PyInstaller([string]$name, [string[]]$options, [string]$entry = 'companion.py') {
  $outputLog = Join-Path $PSScriptRoot "VoiceCompanion-$name-PyInstaller-Output.txt"
  $errorLog = Join-Path $PSScriptRoot "VoiceCompanion-$name-PyInstaller-Errors.txt"
  Write-Output "Packaging $name. Detailed logs: $outputLog and $errorLog"
  $optionsFile = Join-Path $PSScriptRoot "VoiceCompanion-$name-Packaging-Options.json"
  try {
    ConvertTo-Json -InputObject @($options) -Compress | Set-Content -LiteralPath $optionsFile -Encoding UTF8
    & $python (Join-Path $PSScriptRoot 'build_packaging.py') $optionsFile $outputLog $errorLog $entry
    $packagingExitCode = $LASTEXITCODE
  } finally {
    Remove-Item -LiteralPath $optionsFile -Force -ErrorAction SilentlyContinue
  }
  if ($packagingExitCode -ne 0) {
    Write-Output "PyInstaller failed with exit code $packagingExitCode. Recent errors:"
    if (Test-Path $errorLog) { Get-Content -LiteralPath $errorLog -Tail 100 | Out-Host }
    Write-Output 'Recent standard output:'
    if (Test-Path $outputLog) { Get-Content -LiteralPath $outputLog -Tail 40 | Out-Host }
    if (Test-Path $errorLog) { Write-BuildAnnotation ('Packaging ' + $name + ' failed') ((Get-Content -LiteralPath $errorLog -Tail 40) -join "`n") }
    throw "Could not package $name. Send both PyInstaller log files along with VoiceCompanion-Build-Log.txt."
  }
  Write-Output "$name packaged successfully."
}
$consoleArguments = @('--console', '--name', 'VoiceCompanion-Diagnostics') + $arguments
Invoke-PyInstaller 'Diagnostics' $consoleArguments
$windowArguments = @('--windowed', '--name', 'VoiceCompanion') + $arguments
Invoke-PyInstaller 'Application' $windowArguments
$browserHostArguments = @('--clean','--noconfirm','--onefile','--console','--name','VoiceCompanion-BrowserHost','--hidden-import','win32com.client','--hidden-import','thunderbird_bridge')
Invoke-PyInstaller 'BrowserHost' $browserHostArguments 'browser_bridge_host.py'
Copy-Item (Join-Path $PSScriptRoot 'dist\VoiceCompanion-BrowserHost.exe') (Join-Path $PSScriptRoot 'dist\VoiceCompanion\VoiceCompanion-BrowserHost.exe') -Force
Copy-Item (Join-Path $PSScriptRoot 'regular_browser_extension') (Join-Path $PSScriptRoot 'dist\VoiceCompanion\regular_browser_extension') -Recurse -Force
# Thunderbird add-on: the helper installs this file once in Thunderbird (Add-ons, Install Add-on From File).
& $python (Join-Path $PSScriptRoot 'build_thunderbird_addon.py') (Join-Path $PSScriptRoot 'dist\VoiceCompanion\Voice Companion Mail Connection.xpi')
if ($LASTEXITCODE -ne 0) { throw 'The Thunderbird mail add-on could not be packaged.' }
$diagnosticSource = Join-Path $PSScriptRoot 'dist\VoiceCompanion-Diagnostics\VoiceCompanion-Diagnostics.exe'
$diagnosticTarget = Join-Path $PSScriptRoot 'dist\VoiceCompanion\VoiceCompanion-Diagnostics.exe'
if (-not (Test-Path $diagnosticSource)) { throw 'The diagnostic executable is missing.' }
Copy-Item $diagnosticSource $diagnosticTarget -Force
$updateVoiceArguments = @('--clean','--noconfirm','--onedir','--windowed','--contents-directory','.', '--name','VoiceCompanion-UpdateVoice', '--add-data','model:model','--collect-all','vosk','--collect-all','win32com','--hidden-import','sounddevice','--hidden-import','tkinter')
Invoke-PyInstaller 'UpdateVoice' $updateVoiceArguments 'update_voice.py'
Copy-Item (Join-Path $PSScriptRoot 'dist\VoiceCompanion-UpdateVoice') (Join-Path $PSScriptRoot 'dist\VoiceCompanion\update-voice-runtime') -Recurse -Force
& $python (Join-Path $PSScriptRoot 'check_update_voice.py') (Join-Path $PSScriptRoot 'dist\VoiceCompanion\update-voice-runtime\VoiceCompanion-UpdateVoice.exe')
if ($LASTEXITCODE -ne 0) { throw 'The packaged offline updater Status listener or microphone handoff failed.' }
$exe = Join-Path $PSScriptRoot 'dist\VoiceCompanion\VoiceCompanion.exe'
if ((Invoke-PackagedCheck 'Packaged runtime check' $diagnosticTarget @('--check-runtime')) -ne 0) { throw 'A packaged runtime dependency is missing.' }
if ((Invoke-PackagedCheck 'Packaged bookmark check' $diagnosticTarget @('--check-browser-bookmarks')) -ne 0) { throw 'Saved bookmarks failed in the actual packaged browser. See the Packaged bookmark check annotation.' }
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
  $testCommands = "wake up`ncreate a document`nname document Build check`nconfirm`nstart dictation`nThis is a test sentence.`npause dictation`nread paragraph`nleave document`nyes`nwrite an email`nuse outlook`nemail to example@example.com`nsubject is Build test`nstart dictation`nThis is a local email draft.`npause dictation`nsend email`nshut down companion`n"
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
Copy-Item (Join-Path $PSScriptRoot 'AI VOICE SETUP - Trainer.txt') (Join-Path $PSScriptRoot 'dist\VoiceCompanion\AI VOICE SETUP - Trainer.txt') -Force
Copy-Item (Join-Path $PSScriptRoot 'UPDATES - Trainer.txt') (Join-Path $PSScriptRoot 'dist\VoiceCompanion\UPDATES - Trainer.txt') -Force
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
$browserManifest = Join-Path $installedFolder 'browser-native-host.json'
if (-not (Test-Path $browserManifest)) { throw 'The installed browser native host manifest is missing.' }
$browserRegistration = Get-Content $browserManifest -Raw | ConvertFrom-Json
if ($browserRegistration.path -ne (Join-Path $installedFolder 'VoiceCompanion-BrowserHost.exe') -or
    $browserRegistration.allowed_origins.Count -ne 2 -or
    $browserRegistration.allowed_origins[0] -ne 'chrome-extension://lmpkdmlehooibijenhkhpbjcgjehjbig/' -or
    $browserRegistration.allowed_origins[1] -ne 'chrome-extension://ijojiipjhcfaajafdbinnbehcenfchcc/') {
  throw 'The installed browser native host origin or executable is incorrect.'
}
$mailManifest = Join-Path $installedFolder 'mail-native-host.json'
if (-not (Test-Path $mailManifest)) { throw 'The installed Thunderbird mail host manifest is missing.' }
$mailRegistration = Get-Content $mailManifest -Raw | ConvertFrom-Json
if ($mailRegistration.path -ne (Join-Path $installedFolder 'VoiceCompanion-BrowserHost.exe') -or
    $mailRegistration.allowed_extensions.Count -ne 1 -or $mailRegistration.allowed_extensions[0] -ne 'voice-companion-mail@willwalsh14-hub') {
  throw 'The installed Thunderbird mail host origin or executable is incorrect.'
}
if ((Get-Item 'HKCU:\Software\Mozilla\NativeMessagingHosts\com.voicecompanion.mail').GetValue('') -ne $mailManifest) { throw 'The installed Thunderbird mail host registry entry is incorrect.' }
if (-not (Test-Path (Join-Path $installedFolder 'Voice Companion Mail Connection.xpi'))) { throw 'The Thunderbird mail add-on is missing from the installation.' }
foreach ($browserKey in @('HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.voicecompanion.browser', 'HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\com.voicecompanion.browser')) {
  if ((Get-Item $browserKey).GetValue('') -ne $browserManifest) { throw 'The installed browser native host registry entry is incorrect.' }
}
& $python (Join-Path $PSScriptRoot 'check_browser_connection.py') (Join-Path $installedFolder 'VoiceCompanion-BrowserHost.exe')
if ($LASTEXITCODE -ne 0) { throw 'The installed native browser connection failed its end-to-end pipe check.' }
& $python (Join-Path $PSScriptRoot 'check_extension_browser.py') $installedFolder $extensionTestRuntime
if ($LASTEXITCODE -ne 0) { throw 'The installed extension did not preserve the standard browser navigation and forms.' }
$installedVersion = & $installedDiagnostics --version
if ($LASTEXITCODE -ne 0 -or -not (($installedVersion -join ' ') -match '^Voice Companion 0\.2\.173-test$')) {
  throw 'The installed program is not the current Voice Companion build.'
}
& $installedDiagnostics --refresh-guides
if ($LASTEXITCODE -ne 0) { throw 'The installed documentation did not copy and verify in Documents.' }
& $installedDiagnostics --check-runtime
if ($LASTEXITCODE -ne 0) { throw 'The installed program is missing a runtime component.' }
Write-Output 'INSTALL PASSED. Voice Companion 0.2.173-test is installed. Use the desktop icon or Start menu.'
if ($Unattended) {
  & (Join-Path $PSScriptRoot 'test-update-audio.ps1')
  if ($LASTEXITCODE -ne 0) { throw 'Updater microphone release checks failed.' }
  Write-Output 'BUILD STAGE: Testing the updater helper with a silent installation.'
  $updateCheck = Join-Path $PSScriptRoot '.update-helper-check'
  New-Item -ItemType Directory -Path $updateCheck -Force | Out-Null
  # Will's 0.2.171 update stopped because Setup could not close the browser hosts
  # (Chrome and Thunderbird start them). Two run here, as on his computer.
  $runningHosts = @(1..2 | ForEach-Object { Start-Process -FilePath (Join-Path $installedFolder 'VoiceCompanion-BrowserHost.exe') -WindowStyle Hidden -PassThru })
  try {
    & (Join-Path $PSScriptRoot 'apply-update.ps1') -InstallerPath $portableSetup -AppPath $installedApp -PreviousProcessId -1 -ExpectedHash (Get-FileHash $portableSetup -Algorithm SHA256).Hash -DataFolder $updateCheck -ReadyFile (Join-Path $updateCheck 'ready.json') -NoSpeech -NoRestart
    $updateResult = Get-Content (Join-Path $updateCheck 'update-result.json') -Raw | ConvertFrom-Json
    $updateLog = Get-Content -LiteralPath (Join-Path $updateCheck 'update-install-log.txt') -Raw -ErrorAction SilentlyContinue
    if (-not $updateResult.succeeded) {
      throw ('The updater helper did not install successfully: ' + $updateResult.error + "`n" + [string]$updateLog)
    }
    foreach ($runningHost in $runningHosts) { $runningHost.Refresh(); if (-not $runningHost.HasExited) { throw ('The update left a browser host running.' + "`n" + [string]$updateLog) } }
    $leftOver = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($installedFolder + '\', [StringComparison]::OrdinalIgnoreCase) })
    if ($leftOver.Count) { throw ('The update left Voice Companion programs running: ' + (($leftOver | ForEach-Object { $_.Name }) -join ', ')) }
    if ($updateLog -notmatch 'Closing VoiceCompanion-BrowserHost.exe' -or $updateLog -notmatch '----- Setup log -----') { throw ('The update log does not show each step.' + "`n" + [string]$updateLog) }
    if (-not (Test-Path (Join-Path $installedFolder 'browser-native-host.json')) -or (Test-Path (Join-Path $installedFolder 'browser-native-host.json.update-paused'))) { throw 'The browser connection was not registered again after the update.' }
    $installPercent = Get-Content (Join-Path $PSScriptRoot 'install-percent.txt') -Raw
    if ([int]$installPercent -lt 1) { throw 'Setup did not report installation progress.' }
    Write-Output 'Updater helper silent installation and progress checks passed.'
  } finally {
    foreach ($runningHost in $runningHosts) { try { if (-not $runningHost.HasExited) { $runningHost.Kill() } } catch {} }
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
@{ version = '0.2.173-test'; sha256 = (Get-FileHash -LiteralPath $portableSetup -Algorithm SHA256).Hash.ToLowerInvariant(); size = (Get-Item -LiteralPath $portableSetup).Length } |
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




