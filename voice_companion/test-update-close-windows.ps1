$ErrorActionPreference = 'Stop'
# The close step with real Windows processes and a real file lock.
# Stand-ins: two small programs built here (one waits, one starts a program and
# waits) play Voice Companion's programs in a test install folder; a PowerShell process outside the folder that holds a file
# open plays the user's Word. Nothing outside the test folder is ever closed.
. (Join-Path $PSScriptRoot 'close-companion.ps1')
function Assert($condition, [string]$message) { if (-not $condition) { throw $message } }
$root = Join-Path ([IO.Path]::GetTempPath()) ('VC close test ' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$folder = Join-Path $root 'Voice Companion'
$started = New-Object System.Collections.ArrayList
$script:CloseLogFile = Join-Path $root 'close-log.txt'
function Start-Hidden([string]$file, [string]$arguments) {
  $process = Start-Process -FilePath $file -ArgumentList $arguments -WindowStyle Hidden -PassThru
  [void]$started.Add($process)
  return $process
}
function Wait-Until([scriptblock]$condition, [int]$seconds, [string]$message) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while (-not (& $condition)) { if ((Get-Date) -gt $deadline) { throw $message }; Start-Sleep -Milliseconds 200 }
}
try {
  New-Item -ItemType Directory -Path (Join-Path $folder 'browser-runtime') -Force | Out-Null
  # A similar folder name next to it must never match.
  New-Item -ItemType Directory -Path ($folder + ' Old') -Force | Out-Null
  $system = [Environment]::GetFolderPath('System')
  $tools = Join-Path $root 'tools'; New-Item -ItemType Directory -Path $tools | Out-Null
  $waiter = Join-Path $tools 'waiter.exe'; $launcher = Join-Path $tools 'launcher.exe'
  Add-Type -OutputType ConsoleApplication -OutputAssembly $waiter -TypeDefinition 'public static class Waiter { public static void Main() { System.Threading.Thread.Sleep(600000); } }'
  Add-Type -OutputType ConsoleApplication -OutputAssembly $launcher -TypeDefinition @'
public static class Launcher {
  public static void Main(string[] arguments) {
    var start = new System.Diagnostics.ProcessStartInfo(arguments[0], arguments.Length > 1 ? arguments[1] : "");
    start.UseShellExecute = false; start.CreateNoWindow = true;
    System.Diagnostics.Process.Start(start);
    System.Threading.Thread.Sleep(600000);
  }
}
'@
  Copy-Item $waiter (Join-Path $folder 'VoiceCompanion-BrowserHost.exe')
  Copy-Item $waiter (Join-Path $folder 'browser-runtime\chrome.exe')
  Copy-Item $launcher (Join-Path $folder 'VoiceCompanion.exe')
  Copy-Item $waiter (Join-Path ($folder + ' Old') 'VoiceCompanion.exe')
  'guide' | Set-Content -LiteralPath (Join-Path $folder 'START HERE - Veteran.docx')
  'browser' | Set-Content -LiteralPath (Join-Path $folder 'browser-native-host.json')

  # Voice Companion's programs: two browser hosts, a browser, and the app, which
  # starts a program from outside the folder (the user's program, never closed).
  $host1 = Start-Hidden (Join-Path $folder 'VoiceCompanion-BrowserHost.exe') 'host'
  $host2 = Start-Hidden (Join-Path $folder 'VoiceCompanion-BrowserHost.exe') 'host'
  $browser = Start-Hidden (Join-Path $folder 'browser-runtime\chrome.exe') 'browser'
  $app = Start-Hidden (Join-Path $folder 'VoiceCompanion.exe') ('"' + $waiter + '"')
  $other = Start-Hidden (Join-Path ($folder + ' Old') 'VoiceCompanion.exe') 'other'
  # The user's program, holding a Voice Companion file open.
  $holderScript = Join-Path $root 'holder.ps1'
  "`$s = [IO.File]::Open('" + (Join-Path $folder 'START HERE - Veteran.docx') + "', 'Open', 'ReadWrite', 'None'); 'held' | Set-Content -LiteralPath '" + (Join-Path $root 'held.txt') + "'; Start-Sleep -Seconds 600" | Set-Content -LiteralPath $holderScript
  $holder = Start-Hidden (Join-Path $system 'WindowsPowerShell\v1.0\powershell.exe') ('-NoProfile -ExecutionPolicy Bypass -File "' + $holderScript + '"')
  Wait-Until { Test-Path (Join-Path $root 'held.txt') } 30 'The stand-in for the user''s program did not open the file.'
  Wait-Until { @(Get-CimInstance Win32_Process -Filter ('ParentProcessId=' + $app.Id) | Where-Object { $_.Name -eq 'waiter.exe' }).Count } 30 'The app stand-in did not start its child.'
  $userChild = @(Get-CimInstance Win32_Process -Filter ('ParentProcessId=' + $app.Id) | Where-Object { $_.Name -eq 'waiter.exe' })[0]

  Assert (Test-FileLocked (Join-Path $folder 'START HERE - Veteran.docx')) 'A file held open by another program must count as in use.'
  Assert (-not (Test-FileLocked (Join-Path $folder 'browser-native-host.json'))) 'A free file must not count as in use.'
  $users = @(Get-FileUsers @(Join-Path $folder 'START HERE - Veteran.docx'))
  Assert ($users.ProcessId -contains $holder.Id) ('Restart Manager must name the program that holds the file: ' + ($users | Out-String))

  # Close: every Voice Companion program goes; the user's program is named and kept.
  $outcome = Invoke-CompanionClose $folder @() $PID 2 5 4
  Assert ($outcome.Code -eq 2) ('The held file must stop the update with code 2, not ' + $outcome.Code + ': ' + ($outcome.Names -join ', '))
  Assert (@($outcome.Names).Count -ge 1 -and -not (($outcome.Names -join ' ') -match 'Voice Companion')) ('The blocker must be named: ' + ($outcome.Names -join ', '))
  foreach ($process in @($host1, $host2, $browser, $app)) { $process.Refresh(); Assert $process.HasExited ('Voice Companion stand-in ' + $process.Id + ' must be closed.') }
  $holder.Refresh(); Assert (-not $holder.HasExited) 'The user''s program must never be closed.'
  $other.Refresh(); Assert (-not $other.HasExited) 'A program in a similar folder must never be closed.'
  Assert ([bool](Get-Process -Id $userChild.ProcessId -ErrorAction SilentlyContinue)) 'A program Voice Companion opened from outside its folder must never be closed.'

  # Script mode, as Setup runs it: the reason is written and the browser manifest comes back.
  $reason = Join-Path $root 'reason.txt'
  $shell = Join-Path $system 'WindowsPowerShell\v1.0\powershell.exe'
  & $shell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'close-companion.ps1') -InstallFolder $folder -ReasonFile $reason -BlockedSeconds 2 -GentleSeconds 1 -ForceSeconds 2
  Assert ($LASTEXITCODE -eq 2) ('Setup''s close step must exit with 2 when a file is held, not ' + $LASTEXITCODE)
  $lines = @(Get-Content -LiteralPath $reason)
  Assert ($lines[0] -eq '2' -and $lines.Count -ge 2 -and $lines[1]) ('The reason file must name the program: ' + ($lines -join ' | '))
  Assert ((Get-Content -LiteralPath (Join-Path $folder 'browser-native-host.json')) -eq 'browser') 'The browser manifest must come back when the update stops.'

  # The user closes their program: the files are free and the update goes on.
  $holder.Kill(); [void]$holder.WaitForExit(10000)
  $outcome = Invoke-CompanionClose $folder @() $PID 2 5 10
  Assert ($outcome.Code -eq 0) ('With the program closed the update must go on: ' + $outcome.Code + ' ' + ($outcome.Names -join ', '))
  Assert (Test-Path (Join-Path $folder 'browser-native-host.json.update-paused')) 'Browsers must not start the host while Setup runs.'
  Resume-NativeHostManifests $folder
  Assert (Test-Path (Join-Path $folder 'browser-native-host.json')) 'The manifest must come back.'

  # The update is started by the app: the close step running under it closes the
  # app but never itself.
  $host3 = Start-Hidden (Join-Path $folder 'VoiceCompanion-BrowserHost.exe') 'host'
  $closeLog = Join-Path $root 'nested-close-log.txt'
  $parent = Start-Hidden (Join-Path $folder 'VoiceCompanion.exe') ('"' + (Join-Path $system 'WindowsPowerShell\v1.0\powershell.exe') + '" "-NoProfile -ExecutionPolicy Bypass -File \"' + (Join-Path $PSScriptRoot 'close-companion.ps1') + '\" -InstallFolder \"' + $folder + '\" -LogFile \"' + $closeLog + '\" -GentleSeconds 1 -ForceSeconds 2 -BlockedSeconds 5"')
  Wait-Until { $parent.Refresh(); $parent.HasExited } 60 'The app stand-in that started the update must be closed by it.'
  Wait-Until { $host3.Refresh(); $host3.HasExited } 30 'The browser host must be closed.'
  Wait-Until { (Test-Path $closeLog) -and ((Get-Content -LiteralPath $closeLog -Raw) -match 'All Voice Companion files are free') } 60 ('The close step must finish its own work after closing the app that started it: ' + $(if (Test-Path $closeLog) { Get-Content -LiteralPath $closeLog -Raw }))
  Write-Output 'PASS: real processes closed by folder, children first, the user''s programs and similar folders untouched, a held file named by Restart Manager, Setup''s exit code and reason, manifests paused and restored, and the update never closing itself.'
} finally {
  foreach ($process in $started) { try { if (-not $process.HasExited) { $process.Kill() } } catch {} }
  Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }
  if ($userChild) { try { Stop-Process -Id $userChild.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }
  Start-Sleep -Milliseconds 500
  Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
