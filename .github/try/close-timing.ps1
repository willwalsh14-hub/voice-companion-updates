# Try branch only: time each part of the shipped close step on Windows.
$ErrorActionPreference = 'Stop'
function Note([string]$text) { Write-Output ((Get-Date).ToString('HH:mm:ss.fff') + ' ' + $text) }
$work = Join-Path $env:RUNNER_TEMP 'timing'; New-Item -ItemType Directory -Force -Path $work | Out-Null
$setup = Join-Path $work 'old-setup.exe'
(New-Object Net.WebClient).DownloadFile('https://github.com/willwalsh14-hub/voice-companion-updates/releases/download/v0.2.173-test/VoiceCompanion-Setup.exe', $setup)
$p = Start-Process $setup -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-' -PassThru; $p.WaitForExit()
$folder = Join-Path $env:LOCALAPPDATA 'Programs\Voice Companion'
. (Join-Path $folder 'close-companion.ps1')
$script:CloseLogFile = Join-Path $work 'log.txt'
foreach ($round in 1, 2) {
  $t = Get-Date; $all = @(Get-CompanionProcessList); Note ('Round ' + $round + ': Get-CompanionProcessList ' + $all.Count + ' processes in ' + ((Get-Date) - $t).TotalSeconds + ' s')
  $t = Get-Date; $cim = @(Get-CimInstance Win32_Process); Note ('  Get-CimInstance without -Property: ' + ((Get-Date) - $t).TotalSeconds + ' s')
  $t = Get-Date; $long = Get-LongFolderPath $folder; Note ('  Get-LongFolderPath: ' + ((Get-Date) - $t).TotalSeconds + ' s -> ' + $long)
  $t = Get-Date; $inside = @($all | Where-Object { Test-InsideFolder $_.Path $folder }); Note ('  Test-InsideFolder for each: ' + ((Get-Date) - $t).TotalSeconds + ' s, inside ' + $inside.Count)
  $t = Get-Date; $chosen = @(Select-CompanionProcesses $all $folder $PID); Note ('  Select-CompanionProcesses: ' + ((Get-Date) - $t).TotalSeconds + ' s, chosen ' + $chosen.Count)
  $t = Get-Date; [void](Close-CompanionProcesses $folder $PID 1 1); Note ('  Close-CompanionProcesses: ' + ((Get-Date) - $t).TotalSeconds + ' s')
  $t = Get-Date; $files = @(Get-CompanionFiles $folder @()); Note ('  Get-CompanionFiles: ' + $files.Count + ' files in ' + ((Get-Date) - $t).TotalSeconds + ' s')
  $t = Get-Date; $locked = @(Find-LockedFiles $folder @()); Note ('  Find-LockedFiles: ' + ((Get-Date) - $t).TotalSeconds + ' s, locked ' + $locked.Count)
}
$t = Get-Date; foreach ($i in 1..200) { $null = Test-InsideFolder 'C:\Windows\System32\svchost.exe' $folder }; Note ('  200 x Test-InsideFolder (outside): ' + ((Get-Date) - $t).TotalSeconds + ' s')
$t = Get-Date; foreach ($i in 1..200) { $null = Test-StartedAfter $all[1] $all[0] }; Note ('  200 x Test-StartedAfter: ' + ((Get-Date) - $t).TotalSeconds + ' s')
Note ('Long-folder cache entries: ' + $script:LongFolders.Count)
Get-Content $script:CloseLogFile -ErrorAction SilentlyContinue
