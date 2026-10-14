$ErrorActionPreference = 'Stop'
$helper = Join-Path $PSScriptRoot 'apply-update.ps1'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($helper, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Updater helper has PowerShell syntax errors.' }
foreach ($definition in $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
  Invoke-Expression $definition.Extent.Text
}
$script:released = @()
$fake = New-Object PSObject
$fake | Add-Member ScriptMethod WaitForExit { param($timeout) $script:released += 'wait'; return $true }
$fake | Add-Member ScriptMethod Dispose { $script:released += 'dispose' }
$script:listener = $fake
function Start-Process {
  param($FilePath, $WorkingDirectory, $ArgumentList)
  if ($script:listener) { throw 'Restart attempted before microphone release.' }
  if (($script:released -join ',') -ne 'wait,dispose') { throw 'Microphone cleanup order is incorrect.' }
  if ($ArgumentList -ne '--resume-after-update') { throw 'Restart is missing the update handoff flag.' }
  $script:restarted = $true
}
$updateFolder = Join-Path $env:TEMP ('vc-update-audio-' + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $updateFolder | Out-Null
$NoRestart = $false; $AppPath = 'C:\VoiceCompanion.exe'
$script:restarted = $false
try {
  Restart-Companion
  if (-not $script:restarted -or -not (Test-Path (Join-Path $updateFolder 'voice-stop'))) { throw 'Restart release handshake failed.' }
  Close-UpdateAudio
  $voice = New-Object PSObject
  $voice | Add-Member ScriptMethod Speak { throw 'Simulated audio device failure' }
  $log = Join-Path $updateFolder 'error.txt'
  Say-Percent 5
  if (-not (Test-Path $log)) { throw 'Audio error was not logged.' }
} finally { Remove-Item $updateFolder -Recurse -Force }
Write-Output 'Updater microphone release before awake restart and audio failure isolation checks passed.'
exit 0
