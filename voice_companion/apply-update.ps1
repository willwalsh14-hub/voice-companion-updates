param([string]$InstallerPath,[string]$AppPath,[int]$PreviousProcessId,[string]$ExpectedHash,[string]$DataFolder,[string]$ReadyFile,[switch]$NoSpeech,[switch]$NoRestart)
$ErrorActionPreference = 'Stop'
$result = Join-Path $DataFolder 'update-result.json'
$log = Join-Path $DataFolder 'update-install-log.txt'
$updateFolder = Split-Path $InstallerPath
Set-Location -LiteralPath $updateFolder
[Environment]::CurrentDirectory = $updateFolder
$progressFile = Join-Path $updateFolder 'install-percent.txt'
$voice = $null; $listener = $null; $setup = $null
function Write-UpdateError($failure) { try { $failure.ToString() | Add-Content -LiteralPath $log } catch {} }
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
function Restart-Companion {
  Close-UpdateAudio
  if (-not $NoRestart) {
    Start-Process -FilePath $AppPath -ArgumentList '--resume-after-update' -WorkingDirectory (Split-Path $AppPath)
  }
}
function Say-Percent([int]$percent) {
  # The independent listener owns progress speech while its microphone is on.
  if (-not $listener -and $voice) { try { $voice.Speak([string]$percent + ' percent.', 1) | Out-Null } catch { Write-UpdateError $_ } }
}
function Close-InstalledBrowserHosts {
  # Native messaging hosts belong to Chrome/Edge, so exiting Companion alone
  # does not release their copies of the installed DLLs. Never stop browsers
  # or hosts belonging to a different installation.
  $hostPath = [IO.Path]::GetFullPath((Join-Path (Split-Path $AppPath) 'VoiceCompanion-BrowserHost.exe'))
  foreach ($browserHost in @(Get-Process -Name 'VoiceCompanion-BrowserHost' -ErrorAction SilentlyContinue)) {
    try {
      if ($browserHost.HasExited) { continue }
      if (-not [string]::Equals($browserHost.Path, $hostPath, [StringComparison]::OrdinalIgnoreCase)) { continue }
      $browserHost.Kill()
      if (-not $browserHost.WaitForExit(10000)) { throw 'The browser connection did not close for installation.' }
    } finally { $browserHost.Dispose() }
  }
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
  if ((Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash -ne $ExpectedHash) { throw 'Installer integrity check failed.' }
  if (-not $NoSpeech) { $voice = New-Object -ComObject SAPI.SpVoice }
  if ($ReadyFile) { '{"ready":true}' | Set-Content -LiteralPath $ReadyFile -Encoding UTF8 }
  Stop-PreviousCompanion (Get-Process -Id $PreviousProcessId -ErrorAction SilentlyContinue)
  Close-InstalledBrowserHosts
  '0' | Set-Content -LiteralPath $progressFile -Encoding ASCII
  if (-not $NoSpeech) {
    $listenerPath = Join-Path $updateFolder 'update-voice-runtime\VoiceCompanion-UpdateVoice.exe'
    if (-not (Test-Path -LiteralPath $listenerPath)) { throw 'The update speech files are missing.' }
    $listener = Start-Process -FilePath $listenerPath -ArgumentList @('--folder',('"' + $updateFolder + '"')) -WorkingDirectory (Split-Path $listenerPath) -PassThru
    $listenerHandle = $listener.Handle
    $deadline = (Get-Date).AddSeconds(30)
    while (-not (Test-Path -LiteralPath (Join-Path $updateFolder 'voice-ready.json'))) {
      if ($listener.HasExited) { throw 'The update speech window did not start.' }
      if ((Get-Date) -gt $deadline) { throw 'The update speech window did not become ready.' }
      Start-Sleep -Milliseconds 100; $listener.Refresh()
    }
  }
  $arguments = '/VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART /RESTARTEXITCODE=3010 /CLOSEAPPLICATIONS /NORESTARTAPPLICATIONS /LOG="' + $log + '" /UPDATESTATUSFILE="' + $progressFile + '"'
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
  Close-UpdateAudio
  @{ succeeded = $false; error = $failure.Message; installer_exit_code = $(if ($setup) { $setup.ExitCode } else { $null }) } | ConvertTo-Json | Set-Content -LiteralPath $result -Encoding UTF8
  if ($ReadyFile -and -not (Test-Path $ReadyFile)) { '{"ready":false}' | Set-Content -LiteralPath $ReadyFile -Encoding UTF8 }
  if ($voice) { try { $voice.Speak('The update did not finish. Reopening Voice Companion.',3) | Out-Null } catch {} }
  if (-not $NoRestart -and -not (Get-Process -Id $PreviousProcessId -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath $AppPath)) { Restart-Companion }
  exit 1
} finally {
  Close-UpdateAudio
  if ($voice) { try { [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($voice) | Out-Null } catch {} }
}
