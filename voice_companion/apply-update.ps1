param([string]$InstallerPath,[string]$AppPath,[int]$PreviousProcessId,[string]$ExpectedHash,[string]$DataFolder,[string]$ReadyFile,[switch]$NoSpeech,[switch]$NoRestart)
$ErrorActionPreference = 'Stop'
$result = Join-Path $DataFolder 'update-result.json'
$log = Join-Path $DataFolder 'update-install-log.txt'
$progressFile = Join-Path (Split-Path $InstallerPath) 'install-percent.txt'
$voice = $null
$recognizer = $null
$setup = $null
function Write-UpdateError($failure) { try { $failure.ToString() | Add-Content -LiteralPath $log } catch {} }
function Close-UpdateAudio {
  Unregister-Event -SourceIdentifier VoiceCompanionUpdateStatus -ErrorAction SilentlyContinue
  Get-Event -SourceIdentifier VoiceCompanionUpdateStatus -ErrorAction SilentlyContinue | Remove-Event -ErrorAction SilentlyContinue
  if ($script:recognizer) {
    try { $script:recognizer.RecognizeAsyncCancel() } catch {}
    try { $script:recognizer.SetInputToNull() } catch {}
    try { $script:recognizer.Dispose() } catch {}
    $script:recognizer = $null
  }
}
function Restart-Companion {
  Close-UpdateAudio
  if (-not $NoRestart) {
    Start-Sleep -Milliseconds 750
    Start-Process -FilePath $AppPath -WorkingDirectory (Split-Path $AppPath)
  }
}
function Say-Percent([int]$percent) { if ($voice) { try { $voice.Speak([string]$percent + ' percent', 0) | Out-Null } catch { Write-UpdateError $_ } } }
try {
  if (-not (Test-Path -LiteralPath $AppPath)) { throw 'The installed application path is missing.' }
  if ((Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash -ne $ExpectedHash) { throw 'Installer integrity check failed.' }
  if (-not $NoSpeech) { $voice = New-Object -ComObject SAPI.SpVoice }
  if ($ReadyFile) { '{"ready":true}' | Set-Content -LiteralPath $ReadyFile -Encoding UTF8 }
  $previous = Get-Process -Id $PreviousProcessId -ErrorAction SilentlyContinue
  if ($previous) { $previous.WaitForExit(30000) | Out-Null }
  if (Get-Process -Id $PreviousProcessId -ErrorAction SilentlyContinue) { throw 'The previous app is still running.' }
  if (-not $NoSpeech) {
    try {
      Add-Type -AssemblyName System.Speech
      $recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine -ArgumentList ([System.Globalization.CultureInfo]::GetCultureInfo('en-US'))
      $choices = New-Object System.Speech.Recognition.Choices
      $choices.Add([string[]]@('status','update status'))
      $grammar = New-Object System.Speech.Recognition.Grammar -ArgumentList (New-Object System.Speech.Recognition.GrammarBuilder -ArgumentList $choices)
      $recognizer.LoadGrammar($grammar)
      $recognizer.SetInputToDefaultAudioDevice()
      Register-ObjectEvent $recognizer SpeechRecognized -SourceIdentifier VoiceCompanionUpdateStatus | Out-Null
      $recognizer.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
    } catch {
      Write-UpdateError $_.Exception
      Close-UpdateAudio
      if ($voice) { try { $voice.Speak('Voice status requests are unavailable during installation. Percentages will still be announced.',0) | Out-Null } catch { Write-UpdateError $_ } }
    }
  }
  '0' | Set-Content -LiteralPath $progressFile -Encoding ASCII
  $arguments = '/VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART /RESTARTEXITCODE=3010 /CLOSEAPPLICATIONS /NORESTARTAPPLICATIONS /LOG="' + $log + '" /UPDATESTATUSFILE="' + $progressFile + '"'
  $setup = Start-Process -FilePath $InstallerPath -ArgumentList $arguments -PassThru
  # Retain the native process handle before a fast Setup exit (Windows PowerShell 5.1).
  $setupHandle = $setup.Handle
  $percent = 0; $announced = -5
  while (-not $setup.HasExited) {
    try { $value = [int](Get-Content -LiteralPath $progressFile -Raw); $percent = [Math]::Min(99,[Math]::Max($percent,$value)) } catch { }
    if ($percent -ge $announced + 5) { Say-Percent $percent; $announced = $percent }
    if ($recognizer) {
      try {
        foreach ($notice in @(Get-Event -SourceIdentifier VoiceCompanionUpdateStatus -ErrorAction SilentlyContinue)) {
          if ($notice.SourceEventArgs.Result.Confidence -ge 0.5) { Say-Percent $percent }
          Remove-Event -EventIdentifier $notice.EventIdentifier
        }
      } catch { Write-UpdateError $_.Exception; Close-UpdateAudio }
    }
    Start-Sleep -Milliseconds 250
    $setup.Refresh()
  }
  $setup.WaitForExit()
  if ($setup.ExitCode -eq 3010) {
    '{"succeeded":false,"restart_required":true}' | Set-Content -LiteralPath $result -Encoding UTF8
    if ($voice) { $voice.Speak('Windows needs a restart to finish the update.',0) | Out-Null }
  } elseif ($setup.ExitCode -ne 0) { throw ('Setup did not finish successfully. Exit code ' + $setup.ExitCode) }
  else {
    Say-Percent 100
    '{"succeeded":true}' | Set-Content -LiteralPath $result -Encoding UTF8
  }
  Restart-Companion
} catch {
  $failure = $_.Exception
  Write-UpdateError $failure
  # If progress monitoring failed, do not restart into a still-running Setup.
  if ($setup) { try { $setup.WaitForExit() } catch { Write-UpdateError $_ } }
  Close-UpdateAudio
  @{ succeeded = $false; error = $failure.Message; installer_exit_code = $(if ($setup) { $setup.ExitCode } else { $null }) } | ConvertTo-Json | Set-Content -LiteralPath $result -Encoding UTF8
  if ($ReadyFile -and -not (Test-Path $ReadyFile)) { '{"ready":false}' | Set-Content -LiteralPath $ReadyFile -Encoding UTF8 }
  if ($voice) { try { $voice.Speak('The update did not finish. Reopening Voice Companion.',0) | Out-Null } catch {} }
  if (-not $NoRestart -and -not (Get-Process -Id $PreviousProcessId -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath $AppPath)) { Restart-Companion }
  exit 1
} finally {
  Close-UpdateAudio
  if ($voice) { try { [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($voice) | Out-Null } catch {} }
}

