param([string]$InstallerPath,[string]$AppPath,[int]$PreviousProcessId,[string]$ExpectedHash,[string]$DataFolder,[string]$ReadyFile,[switch]$NoSpeech,[switch]$NoRestart)
$ErrorActionPreference = 'Stop'
$result = Join-Path $DataFolder 'update-result.json'
$log = Join-Path $DataFolder 'update-install-log.txt'
$progressFile = Join-Path (Split-Path $InstallerPath) 'install-percent.txt'
$voice = $null
$recognizer = $null
function Say-Percent([int]$percent) { if ($voice) { $voice.Speak([string]$percent + ' percent', 0) | Out-Null } }
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
      $_.Exception.ToString() | Add-Content -LiteralPath $log
      if ($voice) { $voice.Speak('Voice status requests are unavailable during installation. Percentages will still be announced.',0) | Out-Null }
    }
  }
  '0' | Set-Content -LiteralPath $progressFile -Encoding ASCII
  $arguments = '/VERYSILENT /SUPPRESSMSGBOXES /SP- /NORESTART /RESTARTEXITCODE=3010 /CLOSEAPPLICATIONS /NORESTARTAPPLICATIONS /LOG="' + $log + '" /UPDATESTATUSFILE="' + $progressFile + '"'
  $setup = Start-Process -FilePath $InstallerPath -ArgumentList $arguments -PassThru
  $percent = 0; $announced = -5
  while (-not $setup.HasExited) {
    try { $value = [int](Get-Content -LiteralPath $progressFile -Raw); $percent = [Math]::Min(99,[Math]::Max($percent,$value)) } catch { }
    if ($percent -ge $announced + 5) { Say-Percent $percent; $announced = $percent }
    foreach ($event in @(Get-Event -SourceIdentifier VoiceCompanionUpdateStatus -ErrorAction SilentlyContinue)) {
      if ($event.SourceEventArgs.Result.Confidence -ge 0.5) { Say-Percent $percent }
      Remove-Event -EventIdentifier $event.EventIdentifier
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
  if ($recognizer) { $recognizer.Dispose(); $recognizer = $null }
  if (-not $NoRestart) { Start-Process -FilePath $AppPath -WorkingDirectory (Split-Path $AppPath) }
} catch {
  $_.Exception.ToString() | Add-Content -LiteralPath $log
  '{"succeeded":false}' | Set-Content -LiteralPath $result -Encoding UTF8
  if ($ReadyFile -and -not (Test-Path $ReadyFile)) { '{"ready":false}' | Set-Content -LiteralPath $ReadyFile -Encoding UTF8 }
  if ($voice) { try { $voice.Speak('The update did not finish. Reopening Voice Companion.',0) | Out-Null } catch {} }
  if (-not $NoRestart -and -not (Get-Process -Id $PreviousProcessId -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath $AppPath)) { Start-Process -FilePath $AppPath -WorkingDirectory (Split-Path $AppPath) }
  exit 1
} finally {
  if ($recognizer) { $recognizer.Dispose() }
  Unregister-Event -SourceIdentifier VoiceCompanionUpdateStatus -ErrorAction SilentlyContinue
}
