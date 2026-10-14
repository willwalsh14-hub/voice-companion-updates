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
  if (-not $listener -and $voice) { try { $voice.Speak('Installing the update. ' + [string]$percent + ' percent.', 3) | Out-Null } catch { Write-UpdateError $_ } }
}
try {
  if (-not (Test-Path -LiteralPath $AppPath)) { throw 'The installed application path is missing.' }
  if ((Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash -ne $ExpectedHash) { throw 'Installer integrity check failed.' }
  if (-not $NoSpeech) { $voice = New-Object -ComObject SAPI.SpVoice }
  if ($ReadyFile) { '{"ready":true}' | Set-Content -LiteralPath $ReadyFile -Encoding UTF8 }
  $previous = Get-Process -Id $PreviousProcessId -ErrorAction SilentlyContinue
  if ($previous) { $previous.WaitForExit(30000) | Out-Null }
  if (Get-Process -Id $PreviousProcessId -ErrorAction SilentlyContinue) { throw 'The previous app is still running.' }
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
    if ($percent -ge $announced + 5) { Say-Percent $percent; $announced = $percent }
    Start-Sleep -Milliseconds 100; $setup.Refresh()
  }
  $setup.WaitForExit()
  if ($setup.ExitCode -eq 3010) {
    '{"succeeded":false,"restart_required":true}' | Set-Content -LiteralPath $result -Encoding UTF8
  } elseif ($setup.ExitCode -ne 0) { throw ('Setup did not finish successfully. Exit code ' + $setup.ExitCode) }
  else {
    '100' | Set-Content -LiteralPath $progressFile -Encoding ASCII
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
