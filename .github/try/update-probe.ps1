param([string]$Version = '0.2.173-test', [string]$Target = '0.2.174-test', [string]$HelperFrom = '')
# Try branch only: reproduce Will's update from an installed release with its own helper,
# and record every process from the install and update folders every second.
$ErrorActionPreference = 'Stop'
$work = Join-Path $env:RUNNER_TEMP 'probe'; New-Item -ItemType Directory -Force -Path $work | Out-Null
function Note([string]$text) { Write-Output ((Get-Date).ToString('HH:mm:ss.fff') + ' ' + $text) }
$base = 'https://github.com/willwalsh14-hub/voice-companion-updates/releases/download/v'
$old = Join-Path $work 'old-setup.exe'; $new = Join-Path $work 'VoiceCompanion-Setup.exe'
Note 'Downloading installers.'
(New-Object Net.WebClient).DownloadFile($base + $Version + '/VoiceCompanion-Setup.exe', $old)
(New-Object Net.WebClient).DownloadFile($base + $Target + '/VoiceCompanion-Setup.exe', $new)
Note ('Time zone: ' + (tzutil /g))
Note 'Installing the old version.'
$p = Start-Process $old -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-' -PassThru; $p.WaitForExit(); Note ('Install exit ' + $p.ExitCode)
$folder = Join-Path $env:LOCALAPPDATA 'Programs\Voice Companion'
$data = Join-Path $env:LOCALAPPDATA 'VoiceCompanion'
$app = Join-Path $folder 'VoiceCompanion.exe'
# Monitor: every second, processes from the install or data folders, and the update folder contents.
$monitorLog = Join-Path $work 'monitor.txt'
$monitor = Start-Job -ArgumentList $folder, $data, $monitorLog -ScriptBlock {
  param($folder, $data, $log)
  $script:previous = ''
  while ($true) {
    $t = (Get-Date).ToString('HH:mm:ss.fff')
    $started = Get-Date
    $list = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and ($_.ExecutablePath.StartsWith($folder, 'OrdinalIgnoreCase') -or $_.ExecutablePath.StartsWith($data, 'OrdinalIgnoreCase') -or $_.Name -eq 'powershell.exe') })
    $ms = [int]((Get-Date) - $started).TotalMilliseconds
    $lines = $list | ForEach-Object { '   ' + $_.ProcessId + ' parent ' + $_.ParentProcessId + ' ' + $_.ExecutablePath + ' started ' + $_.CreationDate.ToString('HH:mm:ss') }
    $updates = @(Get-ChildItem -LiteralPath (Join-Path $data 'Updates') -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.Name + ' voice=' + (Test-Path (Join-Path $_.FullName 'update-voice-runtime\VoiceCompanion-UpdateVoice.exe')) + ' setup=' + (Test-Path (Join-Path $_.FullName 'VoiceCompanion-Setup.exe')) })
    $snapshot = ('updates: ' + ($updates -join '; ')) + "`n" + ($lines -join "`n")
    if ($snapshot -ne $script:previous -or $ms -gt 2000) {
      ($t + ' list took ' + $ms + ' ms; ' + $snapshot) | Add-Content $log
      $script:previous = $snapshot
    }
    Start-Sleep -Seconds 1
  }
}
Note 'Starting the old app.'
$running = Start-Process $app -PassThru
Start-Sleep -Seconds 45
$running.Refresh(); Note ('App running: ' + (-not $running.HasExited) + ' pid ' + $running.Id)
# What the app does in app_updates.launch.
$updateFolder = Join-Path $data ('Updates\' + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $updateFolder | Out-Null
Copy-Item $new (Join-Path $updateFolder 'VoiceCompanion-Setup.exe')
$helperSource = $(if ($HelperFrom) { $HelperFrom } else { $folder })
Copy-Item (Join-Path $helperSource 'apply-update.ps1') $updateFolder
if (Test-Path (Join-Path $helperSource 'close-companion.ps1')) { Copy-Item (Join-Path $helperSource 'close-companion.ps1') $updateFolder }
Copy-Item (Join-Path $folder 'update-voice-runtime') (Join-Path $updateFolder 'update-voice-runtime') -Recurse
$ready = Join-Path $updateFolder 'update-ready.json'
$hash = (Get-FileHash (Join-Path $updateFolder 'VoiceCompanion-Setup.exe') -Algorithm SHA256).Hash
Note 'Starting the helper.'
$helper = Start-Process (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + (Join-Path $updateFolder 'apply-update.ps1') + '"'),
  '-InstallerPath',('"' + (Join-Path $updateFolder 'VoiceCompanion-Setup.exe') + '"'),'-AppPath',('"' + $app + '"'),'-PreviousProcessId',$running.Id,
  '-ExpectedHash',$hash,'-DataFolder',('"' + $data + '"'),'-ReadyFile',('"' + $ready + '"'),'-NoRestart') -WorkingDirectory $updateFolder -PassThru
while (-not (Test-Path $ready)) { Start-Sleep -Milliseconds 100; if ($helper.HasExited) { break } }
Note 'Helper is ready. The app closes itself now, as after install_ready.'
try { [void]$running.CloseMainWindow() } catch { Note ('Close failed ' + $_) }
if (-not $helper.WaitForExit(1200000)) { Note 'Helper still running after 20 minutes.'; $helper.Kill() }
Note ('Helper exit ' + $helper.ExitCode)
Stop-Job $monitor
Write-Output '===== update-install-log.txt'
Get-Content (Join-Path $data 'update-install-log.txt') -ErrorAction SilentlyContinue
Write-Output '===== update-result.json'
Get-Content (Join-Path $data 'update-result.json') -ErrorAction SilentlyContinue
Write-Output '===== freeze-report.txt'
Get-Content (Join-Path $data 'freeze-report.txt') -ErrorAction SilentlyContinue
Write-Output '===== monitor'
Get-Content $monitorLog
