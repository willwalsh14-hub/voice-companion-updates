param([string]$InstallerPath,[string]$AppPath,[int]$PreviousProcessId,[string]$ExpectedHash,[string]$DataFolder,[string]$ReadyFile,[switch]$NoSpeech,[switch]$NoRestart)
$ErrorActionPreference = 'Stop'
$result = Join-Path $DataFolder 'update-result.json'
$log = Join-Path $DataFolder 'update-install-log.txt'
# Setup writes its own log (it replaces the file it is given); it is added to ours after Setup ends.
$setupLog = Join-Path $DataFolder 'update-setup-log.txt'
$updateFolder = Split-Path $InstallerPath
$script:blockedBy = @()
Set-Location -LiteralPath $updateFolder
[Environment]::CurrentDirectory = $updateFolder
$progressFile = Join-Path $updateFolder 'install-percent.txt'
$voice = $null; $listener = $null; $setup = $null
function Write-UpdateError($failure) { try { ((Get-Date).ToString('HH:mm:ss') + ' ' + $failure.ToString()) | Add-Content -LiteralPath $log -Encoding UTF8 } catch {} }
function Write-UpdateLog([string]$message) { Write-UpdateError $message }
function Start-UpdateLog {
  # A new log for each update; Setup's own log is added at the end.
  try { ((Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + ' Update helper started for ' + $InstallerPath) | Set-Content -LiteralPath $log -Encoding UTF8 } catch {}
}
Start-UpdateLog
# While this file exists, a Voice Companion that is opened again keeps this update's
# files and waits (app_updates.update_running). Will's 0.2.173 update stopped
# because a second start deleted the installer and the update voice.
$runningFile = Join-Path $DataFolder 'update-running.json'
function Set-UpdateRunning {
  try { ('{"pid":' + $PID + ',"started":' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + '}') | Set-Content -LiteralPath $runningFile -Encoding ASCII }
  catch { Write-UpdateError $_ }
}
function Clear-UpdateRunning { try { Remove-Item -LiteralPath $runningFile -Force -ErrorAction SilentlyContinue } catch {} }
Set-UpdateRunning
$script:stepStarted = Get-Date
function Write-StepTime([string]$step) {
  # Each step says how long it took, so a slow step names itself in the log.
  Write-UpdateLog ($step + ' took ' + [Math]::Round(((Get-Date) - $script:stepStarted).TotalSeconds, 1) + ' seconds.')
  $script:stepStarted = Get-Date
}
# The close step (close-companion.ps1) is copied next to this helper.
$closeScript = Join-Path $PSScriptRoot 'close-companion.ps1'
if (Test-Path -LiteralPath $closeScript) { . $closeScript; $script:CloseLogFile = $log }
# Set after the close step is loaded: its parameters share these names.
$installFolder = Split-Path $AppPath
function Close-UpdateAudio {
  if ($script:listener) {
    'stop' | Set-Content -LiteralPath (Join-Path $updateFolder 'voice-stop') -Encoding ASCII
    if (-not $script:listener.WaitForExit(10000)) {
      $script:listener.Kill()
      $script:listener.WaitForExit()
    }
    $script:listener.Dispose()
    $script:listener = $null
  }
}
function Test-CompanionRunning {
  # By program file, not by process id: Windows reuses the old app's id.
  try {
    $appFullPath = [IO.Path]::GetFullPath($AppPath)
    return [bool](@(Get-CompanionProcessList | Where-Object { $_.Path -and [string]::Equals([IO.Path]::GetFullPath($_.Path), $appFullPath, [StringComparison]::OrdinalIgnoreCase) }).Count)
  } catch { Write-UpdateError $_; return $false }
}
function Restart-Companion {
  Close-UpdateAudio
  Release-Installer
  Clear-UpdateRunning
  if (-not $NoRestart) {
    Write-UpdateLog 'Starting Voice Companion again.'
    Start-Process -FilePath $AppPath -ArgumentList '--resume-after-update' -WorkingDirectory (Split-Path $AppPath)
  }
}
function Add-SetupLog {
  # Setup's own log goes into the update log once, so one file tells the whole story.
  if (-not (Test-Path -LiteralPath $setupLog)) { return }
  try {
    '----- Setup log -----' | Add-Content -LiteralPath $log -Encoding UTF8
    Get-Content -LiteralPath $setupLog | Add-Content -LiteralPath $log -Encoding UTF8
    '----- End of Setup log -----' | Add-Content -LiteralPath $log -Encoding UTF8
    Remove-Item -LiteralPath $setupLog -Force
  } catch { Write-UpdateError $_ }
}
function Start-UpdateVoice {
  # The update voice reads progress and hears "status". It is a help, never a
  # reason to stop: without it, Windows speech says the percentages.
  $listenerPath = Join-Path $updateFolder 'update-voice-runtime\VoiceCompanion-UpdateVoice.exe'
  if (-not (Test-Path -LiteralPath $listenerPath)) { Write-UpdateLog ('The update voice is missing (' + $listenerPath + '). Windows speech reads the progress instead.'); return $null }
  $started = $null
  try {
    $started = Start-Process -FilePath $listenerPath -ArgumentList @('--folder',('"' + $updateFolder + '"')) -WorkingDirectory (Split-Path $listenerPath) -PassThru
    $script:listenerHandle = $started.Handle
    $deadline = (Get-Date).AddSeconds(30)
    while (-not (Test-Path -LiteralPath (Join-Path $updateFolder 'voice-ready.json'))) {
      if ($started.HasExited) { throw ('The update voice stopped as it started (exit code ' + $started.ExitCode + ').') }
      if ((Get-Date) -gt $deadline) { throw 'The update voice did not become ready in 30 seconds.' }
      Start-Sleep -Milliseconds 100; $started.Refresh()
    }
    Write-UpdateLog 'The update voice is ready.'
    return $started
  } catch {
    Write-UpdateLog ('The update voice could not start: ' + $_.Exception.Message + ' Windows speech reads the progress instead.')
    if ($started) { try { if (-not $started.HasExited) { $started.Kill() }; $started.Dispose() } catch {} }
    return $null
  }
}
function Hold-Installer {
  # Keep the installer open (others may read it, not delete it) from its check
  # until Setup ends, and check the bytes read through this same handle.
  $script:installerStream = [IO.File]::Open($InstallerPath, 'Open', 'Read', 'Read')
  $hasher = [Security.Cryptography.SHA256]::Create()
  try { $hash = -join ($hasher.ComputeHash($script:installerStream) | ForEach-Object { $_.ToString('X2') }) } finally { $hasher.Dispose() }
  return $hash
}
function Release-Installer { if ($script:installerStream) { try { $script:installerStream.Dispose() } catch {}; $script:installerStream = $null } }
function Say-Percent([int]$percent) {
  # The independent listener owns progress speech while its microphone is on.
  if (-not $listener -and $voice) { try { $voice.Speak([string]$percent + ' percent.', 1) | Out-Null } catch { Write-UpdateError $_ } }
}
function Stop-PreviousCompanion($previous) {
  # The app saves documents, drafts and notes before it hands over. If it has not
  # closed after 30 seconds it is stuck, so ask it to close, then stop it, so the
  # update still ends with Voice Companion running. Only this installation's copy
  # is ever stopped, through the handle opened before the wait (no reused process id).
  if (-not $previous) { return }
  try {
    if ($previous.WaitForExit(30000)) { return }
    $appFullPath = [IO.Path]::GetFullPath($AppPath)
    if (-not $previous.Path -or -not [string]::Equals([IO.Path]::GetFullPath($previous.Path), $appFullPath, [StringComparison]::OrdinalIgnoreCase)) { return }
    Write-UpdateError 'The previous app did not close within 30 seconds. Stopping it.'
    try { $previous.CloseMainWindow() | Out-Null } catch { Write-UpdateError $_ }
    if ($previous.WaitForExit(10000)) { return }
    $previous.Kill()
    if (-not $previous.WaitForExit(10000)) { throw 'The previous app is still running.' }
  } finally { $previous.Dispose() }
}
try {
  if (-not (Test-Path -LiteralPath $AppPath)) { throw 'The installed application path is missing.' }
  if ((Hold-Installer) -ne $ExpectedHash.ToUpperInvariant()) { throw 'Installer integrity check failed.' }
  if (-not $NoSpeech) { try { $voice = New-Object -ComObject SAPI.SpVoice } catch { Write-UpdateLog ('Windows speech is not available: ' + $_.Exception.Message) } }
  if ($ReadyFile) { '{"ready":true}' | Set-Content -LiteralPath $ReadyFile -Encoding UTF8 }
  Write-UpdateLog ('Installer verified. Waiting for Voice Companion (process ' + $PreviousProcessId + ') to close.')
  $script:stepStarted = Get-Date
  if ($PreviousProcessId -gt 0) { Stop-PreviousCompanion (Get-Process -Id $PreviousProcessId -ErrorAction SilentlyContinue) }
  Write-StepTime 'Closing the old Voice Companion'
  # Close everything else that runs from the install folder (the browser hosts,
  # Playwright, WebView2, eSpeak, a second copy), and wait until the files are free.
  # A program that is not Voice Companion is named to the user, never closed.
  if (-not (Get-Command Invoke-CompanionClose -ErrorAction SilentlyContinue)) { throw 'The update close step is missing.' }
  $script:CloseVoice = $voice
  $documents = [Environment]::GetFolderPath('MyDocuments')
  $guides = @($(if ($documents) { Join-Path $documents 'Voice Companion\User Guides' }))
  $closing = Invoke-CompanionClose $installFolder $guides $PID
  Write-StepTime 'The close step'
  if ($closing.Code -eq 2) {
    $script:blockedBy = @($closing.Names)
    throw ('Files are in use by ' + (Join-Names $closing.Names) + '.')
  } elseif ($closing.Code -ne 0) {
    # Setup closes Voice Companion's own programs again, by force if needed.
    Write-UpdateLog ('Part of Voice Companion did not close (' + (Join-Names $closing.Names) + '). Setup closes it.')
  }
  '0' | Set-Content -LiteralPath $progressFile -Encoding ASCII
  if (-not $NoSpeech) { $listener = Start-UpdateVoice; Write-StepTime 'Starting the update voice' }
  $arguments = '/VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART /RESTARTEXITCODE=3010 /CLOSEAPPLICATIONS /NORESTARTAPPLICATIONS /LOGCLOSEAPPLICATIONS /LOG="' + $setupLog + '" /UPDATESTATUSFILE="' + $progressFile + '"'
  Write-UpdateLog 'Starting Setup.'
  $setup = Start-Process -FilePath $InstallerPath -ArgumentList $arguments -WorkingDirectory $updateFolder -PassThru
  $setupHandle = $setup.Handle
  $percent = 0; $announced = -5
  while (-not $setup.HasExited) {
    try { $value = [int](Get-Content -LiteralPath $progressFile -Raw); $percent = [Math]::Min(99,[Math]::Max($percent,$value)) } catch {}
    if ($listener) {
      $listener.Refresh()
      if ($listener.HasExited) { $listener.Dispose(); $listener=$null; Write-UpdateError 'Update speech listener stopped.' }
    }
    $rounded = [int]([Math]::Floor($percent / 5) * 5)
    while ($announced + 5 -le $rounded) { $announced += 5; Say-Percent $announced }
    Start-Sleep -Milliseconds 100; $setup.Refresh()
  }
  $setup.WaitForExit()
  Write-UpdateLog ('Setup ended with exit code ' + $setup.ExitCode + '.')
  Add-SetupLog
  if ($setup.ExitCode -eq 3010) {
    '{"succeeded":false,"restart_required":true}' | Set-Content -LiteralPath $result -Encoding UTF8
  } elseif ($setup.ExitCode -ne 0) { throw ('Setup did not finish successfully. Exit code ' + $setup.ExitCode) }
  else {
    '100' | Set-Content -LiteralPath $progressFile -Encoding ASCII
    'complete' | Set-Content -LiteralPath (Join-Path $updateFolder 'install-complete') -Encoding ASCII
    if ($listener) {
      $deadline = (Get-Date).AddSeconds(35)
      while (-not (Test-Path -LiteralPath (Join-Path $updateFolder 'completion-spoken'))) {
        $listener.Refresh()
        if ($listener.HasExited -or (Get-Date) -gt $deadline) { Write-UpdateError 'Installation completion speech did not finish.'; break }
        Start-Sleep -Milliseconds 100
      }
    } elseif ($voice) {
      try { $voice.Speak('100 percent. Installation complete, restarting Voice Companion.',1) | Out-Null; $voice.WaitUntilDone(30000) | Out-Null } catch { Write-UpdateError $_ }
    }
    '{"succeeded":true}' | Set-Content -LiteralPath $result -Encoding UTF8
  }
  Restart-Companion
} catch {
  $failure = $_.Exception; Write-UpdateError $failure
  if ($setup) { try { $setup.WaitForExit() } catch { Write-UpdateError $_ } }
  Add-SetupLog
  Close-UpdateAudio
  @{ succeeded = $false; error = $failure.Message; installer_exit_code = $(if ($setup) { $setup.ExitCode } else { $null }); blocked_by = @($script:blockedBy) } | ConvertTo-Json | Set-Content -LiteralPath $result -Encoding UTF8
  if ($ReadyFile -and -not (Test-Path $ReadyFile)) { '{"ready":false}' | Set-Content -LiteralPath $ReadyFile -Encoding UTF8 }
  if ($voice) {
    $reason = 'The update did not finish. Reopening Voice Companion.'
    if ($script:blockedBy.Count) { $reason = 'The update did not finish, because ' + (Join-Names $script:blockedBy) + $(if ($script:blockedBy.Count -gt 1) { ' are' } else { ' is' }) + ' still using a Voice Companion file. Reopening Voice Companion.' }
    try { $voice.Speak($reason,3) | Out-Null; $voice.WaitUntilDone(15000) | Out-Null } catch {}
  }
  if (-not $NoRestart -and -not (Test-CompanionRunning) -and (Test-Path -LiteralPath $AppPath)) { Restart-Companion }
  exit 1
} finally {
  Release-Installer
  Clear-UpdateRunning
  # Browsers find the browser host again (Setup's last step writes new files when it succeeds).
  if (Get-Command Resume-NativeHostManifests -ErrorAction SilentlyContinue) { Resume-NativeHostManifests $installFolder }
  Close-UpdateAudio
  if ($voice) { try { [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($voice) | Out-Null } catch {} }
}
