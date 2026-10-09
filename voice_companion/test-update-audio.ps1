$ErrorActionPreference = 'Stop'
$helper = Join-Path $PSScriptRoot 'apply-update.ps1'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($helper, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Updater helper has PowerShell syntax errors.' }
foreach ($definition in $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
  Invoke-Expression $definition.Extent.Text
}
$script:released = @()
function New-TestRecognizer {
  $fake = New-Object PSObject
  $fake | Add-Member ScriptMethod RecognizeAsyncCancel { $script:released += 'cancel' }
  $fake | Add-Member ScriptMethod SetInputToNull { $script:released += 'detach' }
  $fake | Add-Member ScriptMethod Dispose { $script:released += 'dispose' }
  return $fake
}
function Start-Process {
  param($FilePath, $WorkingDirectory)
  if ($script:recognizer) { throw 'Restart attempted before microphone release.' }
  if (($script:released -join ',') -ne 'cancel,detach,dispose') { throw 'Microphone cleanup order is incorrect.' }
  $script:restarted = $true
}
function Start-Sleep { param($Milliseconds) }
$NoRestart = $false; $AppPath = 'C:\VoiceCompanion.exe'
$script:recognizer = New-TestRecognizer
$script:restarted = $false
Restart-Companion
if (-not $script:restarted) { throw 'Restart was not attempted.' }
Close-UpdateAudio # Repeated cleanup must be safe.
$voice = New-Object PSObject
$voice | Add-Member ScriptMethod Speak { throw 'Simulated audio device failure' }
$log = Join-Path $env:TEMP ('vc-update-audio-' + [guid]::NewGuid().ToString() + '.txt')
try {
  Say-Percent 5 # An audio error must not abort installation monitoring.
  if (-not (Test-Path $log)) { throw 'Audio error was not logged.' }
} finally { Remove-Item $log -ErrorAction SilentlyContinue }
Write-Output 'Updater microphone release before restart and audio failure isolation checks passed.'
exit 0
