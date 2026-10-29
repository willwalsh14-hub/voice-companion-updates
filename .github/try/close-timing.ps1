# Try branch only: the shipped close step against the fixed one, run as the update
# runs them: from a current folder on drive C (the update folder).
$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'Programs\Voice Companion'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$updates = Join-Path $env:LOCALAPPDATA ('VoiceCompanion\Updates\' + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $updates | Out-Null
$shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
foreach ($version in 'old', 'new') {
  foreach ($where in $updates, $env:RUNNER_TEMP) {
    $script = Join-Path $PSScriptRoot ($version + '-close.ps1')
    $command = "Set-Location -LiteralPath '$where'; [Environment]::CurrentDirectory = '$where'; . '$script'; " +
               "`$t = [Diagnostics.Stopwatch]::StartNew(); `$long = Get-LongFolderPath '$folder'; `$a = `$t.Elapsed.TotalSeconds; " +
               "`$all = @(Get-CompanionProcessList); [void](Select-CompanionProcesses `$all '$folder' `$PID); " +
               "'$version close step, current folder $where : long name ' + [Math]::Round(`$a, 2) + ' s, finding programs ' + [Math]::Round(`$t.Elapsed.TotalSeconds, 2) + ' s -> ' + `$long"
    $line = (& $shell -NoProfile -ExecutionPolicy Bypass -Command $command 2>&1 | Out-String).Trim(); if ($line.Length -gt 260) { $line = $line.Substring(0, 260) + " ... (" + $line.Length + " characters)" }; $line
  }
}
