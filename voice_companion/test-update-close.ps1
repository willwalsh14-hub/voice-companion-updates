$ErrorActionPreference = 'Stop'
# The closing steps in close-companion.ps1, with fake processes and fake file locks.
# Runs in Windows PowerShell 5.1 and PowerShell 7.
$source = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'close-companion.ps1') -Raw
$tokens = $null; $errors = $null
[void][Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ('close-companion.ps1 has syntax errors: ' + ($errors -join '; ')) }
. (Join-Path $PSScriptRoot 'close-companion.ps1')
function Assert($condition, [string]$message) { if (-not $condition) { throw $message } }
$Folder = 'C:\Users\Will\AppData\Local\Programs\Voice Companion'
$script:logged = @()
function Write-CloseLog([string]$message) { $script:logged += $message }

# ---- Close-CompanionProcesses: gently, then forced, children first ----
function New-FakeProcess([int]$id, [string]$name, [bool]$window, [string]$exitsOn) {
  $item = [pscustomobject]@{ Id = $id; Name = $name; Window = $window; ExitsOn = $exitsOn; Exited = $false; Closed = 0; Kills = 0; Disposed = $false; Waits = @() }
  $item | Add-Member ScriptProperty HasExited { $this.Exited }
  $item | Add-Member ScriptProperty MainWindowHandle { if ($this.Window) { [IntPtr]1 } else { [IntPtr]::Zero } }
  $item | Add-Member ScriptMethod CloseMainWindow { $this.Closed++; if ($this.ExitsOn -eq 'close') { $this.Exited = $true }; return $true }
  $item | Add-Member ScriptMethod Kill { $this.Kills++; $script:killOrder += $this.Id; if ($this.ExitsOn -eq 'kill' -or ($this.ExitsOn -eq 'second kill' -and $this.Kills -ge 2)) { $this.Exited = $true } }
  $item | Add-Member ScriptMethod WaitForExit { param($milliseconds) $this.Waits += $milliseconds; return $this.Exited }
  $item | Add-Member ScriptMethod Dispose { $this.Disposed = $true }
  return $item
}
$script:fakes = @{}
$script:list = @()
function Get-CompanionProcessList { $script:list }
function Open-CompanionProcess($entry) { $script:fakes[[int]$entry.ProcessId] }
function Set-Running($items) {
  $script:list = @(); $script:fakes = @{}
  foreach ($item in $items) {
    $script:list += [pscustomobject]@{ ProcessId = $item[0]; ParentProcessId = $item[1]; Path = $item[2]; Name = ($item[2] -split '\\')[-1]; Created = (Get-Date '2026-10-28T10:00:00').AddSeconds($item[0]) }
    if ($item[3]) { $script:fakes[$item[0]] = $item[3] }
  }
}
$script:killOrder = @()
$app = New-FakeProcess 100 'VoiceCompanion.exe' $true 'close'
$node = New-FakeProcess 101 'node.exe' $false 'kill'
$hostParent = New-FakeProcess 201 'VoiceCompanion-BrowserHost.exe' $false 'kill'
$hostChild = New-FakeProcess 202 'VoiceCompanion-BrowserHost.exe' $false 'kill'
$word = New-FakeProcess 300 'WINWORD.EXE' $true 'close'
Set-Running @(
  @(100, 10, "$Folder\VoiceCompanion.exe", $app),
  @(101, 100, "$Folder\playwright\driver\node.exe", $node),
  @(200, 10, 'C:\Program Files\Google\Chrome\Application\chrome.exe', $null),
  @(201, 200, "$Folder\VoiceCompanion-BrowserHost.exe", $hostParent),
  @(202, 201, "$Folder\VoiceCompanion-BrowserHost.exe", $hostChild),
  @(300, 100, 'C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE', $word))
$left = @(Close-CompanionProcesses $Folder 999 10 10)
Assert ($left.Count -eq 0) ('Everything should have closed: ' + ($left -join ', '))
Assert ($app.Closed -eq 1 -and $app.Kills -eq 0) 'A window that closes when asked must not be forced.'
Assert ($node.Kills -eq 1 -and $hostParent.Kills -eq 1 -and $hostChild.Kills -eq 1) 'Programs without a window must be forced after the gentle wait.'
Assert ($script:killOrder.IndexOf(202) -lt $script:killOrder.IndexOf(201)) 'A child must be stopped before its parent.'
Assert ($word.Closed -eq 0 -and $word.Kills -eq 0) 'The user''s Word must never be closed, even when Voice Companion opened it.'
Assert (-not $word.Disposed) 'Word was never opened, so it is not touched at all.'
Assert ($app.Disposed -and $node.Disposed -and $hostParent.Disposed -and $hostChild.Disposed) 'Every process handle must be released.'
Assert ($app.Waits[0] -gt 9000 -and $app.Waits[0] -le 10000) 'The gentle wait must be 10 seconds.'
Assert (($script:logged -join "`n") -match 'Closing VoiceCompanion-BrowserHost.exe \(process 201, program in the install folder\)') 'Each closed program must be logged with why.'

# A program that only stops on the second try is retried; one that never stops is named.
$script:killOrder = @()
$slow = New-FakeProcess 101 'node.exe' $false 'second kill'
$stuck = New-FakeProcess 102 'chrome.exe' $false 'never'
Set-Running @(@(101, 10, "$Folder\playwright\driver\node.exe", $slow), @(102, 10, "$Folder\browser-runtime\chrome.exe", $stuck))
$left = @(Close-CompanionProcesses $Folder 999 1 1)
Assert ($slow.Kills -eq 2 -and $slow.Exited) 'A program that ignores the first stop must be stopped again.'
Assert ($left.Count -eq 1 -and $left[0] -eq 'chrome.exe') ('A program that will not stop must be named: ' + ($left -join ', '))
Assert ($stuck.Disposed) 'A stuck process handle must still be released.'

# A process that ended between the list and the close is skipped.
Set-Running @(, @(101, 10, "$Folder\playwright\driver\node.exe", $null))
$left = @(Close-CompanionProcesses $Folder 999 1 1)
Assert ($left.Count -eq 0) 'A process that already ended is not an error.'

# ---- Get-BlockedMessage: what the user hears ----
$one = Get-BlockedMessage @('Microsoft Word')
Assert ($one -eq 'Microsoft Word is using a Voice Companion file. Save your work in Microsoft Word, then close it. The update will go on after that.') ('Wrong message: ' + $one)
$two = Get-BlockedMessage @('Notepad', 'Microsoft Word', 'Notepad')
Assert ($two.StartsWith('Microsoft Word and Notepad are using a Voice Companion file.')) ('Wrong message: ' + $two)
$unknown = Get-BlockedMessage @('Another program')
Assert ($unknown -eq 'Another program is using a Voice Companion file. Save your work and close your other programs. The update will go on after that.') ('Wrong message: ' + $unknown)
Assert ((Join-Names @('A', 'B', 'C')) -eq 'A, B and C') 'Three names are joined with commas and "and".'
Assert ((Get-FriendlyName ([pscustomobject]@{ AppName = '' }) ([pscustomobject]@{ Name = 'WINWORD.EXE' })) -eq 'WINWORD') 'A program without a name uses its file name.'
Assert ((Get-FriendlyName ([pscustomobject]@{ AppName = '<b>Word</b>' }) $null) -eq 'bWordb') 'Names keep only plain letters.'

# ---- Wait-CompanionFilesFree: name the user's program, close ours again ----
function Start-Sleep { param($Seconds, $Milliseconds) }
$script:said = @()
function Say-CloseMessage([string]$message) { $script:said += $message }
$script:lockRounds = @()
function Find-LockedFiles { if ($script:lockRounds.Count) { $round = $script:lockRounds[0]; $script:lockRounds = @($script:lockRounds | Select-Object -Skip 1); return $round }; return @() }
$script:users = @()
function Get-FileUsers { if ($script:usersFail) { throw 'Restart Manager is not available.' }; $script:users }
$script:closedAgain = 0
function Close-CompanionProcesses { $script:closedAgain++; return @() }
Set-Running @(@(300, 10, 'C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE', $null), @(201, 200, "$Folder\VoiceCompanion-BrowserHost.exe", $null))

# Word holds the guide, then the user closes Word.
$script:lockRounds = @(, @('C:\Users\Will\Documents\Voice Companion\User Guides\START HERE - Veteran.docx'))
$script:users = @([pscustomobject]@{ ProcessId = 300; AppName = 'Microsoft Word'; ServiceName = '' })
$result = Wait-CompanionFilesFree $Folder @() 999 120
Assert ($null -eq $result) 'When the user closes Word, the update goes on.'
Assert ($script:said.Count -eq 1 -and $script:said[0].StartsWith('Microsoft Word is using a Voice Companion file.')) ('The user must hear which program to close: ' + ($script:said -join ' | '))
Assert ($script:closedAgain -eq 0) 'Word is never closed by the update.'

# Word stays open past the time limit: the update stops and names Word.
$script:said = @()
$script:lockRounds = @(@('a'), @('a'), @('a'))
$result = Wait-CompanionFilesFree $Folder @() 999 0
Assert ($result.Code -eq 2 -and @($result.Names).Count -eq 1 -and $result.Names[0] -eq 'Microsoft Word') 'A program that stays open stops the update and is named.'
Assert ($script:said.Count -eq 1) 'The user hears the reason even when time runs out at once.'

# Chrome started the browser host again: it is closed again and nothing is said.
$script:said = @()
$script:lockRounds = @(, @("$Folder\VoiceCompanion-BrowserHost.exe"))
$script:users = @([pscustomobject]@{ ProcessId = 201; AppName = 'VoiceCompanion-BrowserHost.exe'; ServiceName = '' })
$result = Wait-CompanionFilesFree $Folder @() 999 120
Assert ($null -eq $result -and $script:closedAgain -eq 1 -and $script:said.Count -eq 0) 'A Voice Companion program that started again is closed again quietly.'

# The user cannot tell which program: a plain message, and a clear code when time runs out.
$script:said = @(); $script:usersFail = $true
$script:lockRounds = @(, @('a'))
$result = Wait-CompanionFilesFree $Folder @() 999 0
Assert ($result.Code -eq 2 -and $result.Names[0] -eq 'Another program') 'An unknown program still gives a reason.'
Assert ($script:said[0].StartsWith('Another program is using')) 'An unknown program still tells the user what to do.'
$script:usersFail = $false

# Our own program keeps coming back past the limit: code 1, not blamed on the user.
$script:lockRounds = @(, @('a'))
$script:users = @([pscustomobject]@{ ProcessId = 201; AppName = ''; ServiceName = '' })
$result = Wait-CompanionFilesFree $Folder @() 999 0
Assert ($result.Code -eq 1) 'Voice Companion programs that keep starting are not blamed on the user.'

# An antivirus scan (a Windows service) is waited for quietly, and named only at the end.
$script:said = @()
$script:lockRounds = @(@('a'), @('a'))
$script:users = @([pscustomobject]@{ ProcessId = 4000; AppName = 'Microsoft Defender Antivirus Service'; ServiceName = 'WinDefend' })
$result = Wait-CompanionFilesFree $Folder @() 999 120
Assert ($null -eq $result -and $script:said.Count -eq 0) 'A short antivirus scan must not be blamed on the user.'
$script:lockRounds = @(, @('a'))
$result = Wait-CompanionFilesFree $Folder @() 999 0
Assert ($result.Code -eq 2 -and $result.Names[0] -eq 'Microsoft Defender Antivirus Service') 'A service that never lets go is still named at the end.'

# This script itself never counts as the user's program.
$script:said = @()
$script:lockRounds = @(, @('a'))
$script:users = @([pscustomobject]@{ ProcessId = 999; AppName = 'Windows PowerShell'; ServiceName = '' })
$result = Wait-CompanionFilesFree $Folder @() 999 0
Assert (-not ($script:said -join ' ').Contains('PowerShell')) 'The update itself must never be named as the blocker.'

# ---- Invoke-CompanionClose: retry once, then report ----
$script:closeResults = @(@('node.exe'), @())
function Close-CompanionProcesses { $next = $script:closeResults[0]; $script:closeResults = @($script:closeResults | Select-Object -Skip 1); return $next }
function Suspend-NativeHostManifests { $script:suspended = $true }
function Wait-CompanionFilesFree { return $null }
$outcome = Invoke-CompanionClose $Folder @() 999
Assert ($outcome.Code -eq 0 -and $script:suspended) 'A program that closes on the second try lets the update go on.'
$script:closeResults = @(@('node.exe'), @('node.exe'))
$outcome = Invoke-CompanionClose $Folder @() 999
Assert ($outcome.Code -eq 1 -and $outcome.Names[0] -eq 'node.exe') 'A program that never closes stops the update with its name.'
function Wait-CompanionFilesFree { return [pscustomobject]@{ Code = 2; Names = @('Microsoft Word') } }
$script:closeResults = @(, @())
$outcome = Invoke-CompanionClose $Folder @() 999
Assert ($outcome.Code -eq 2 -and $outcome.Names[0] -eq 'Microsoft Word') 'A user program that holds a file stops the update with its name.'

# ---- Browser host manifests: paused for the update, restored if it stops ----
. (Join-Path $PSScriptRoot 'close-companion.ps1')
function Write-CloseLog([string]$message) { }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('vc-close-test-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
  'browser' | Set-Content -LiteralPath (Join-Path $temporary 'browser-native-host.json')
  'mail' | Set-Content -LiteralPath (Join-Path $temporary 'mail-native-host.json')
  Suspend-NativeHostManifests $temporary
  Assert (-not (Test-Path (Join-Path $temporary 'browser-native-host.json'))) 'Browsers must not find the host during the update.'
  Assert ((Get-Content (Join-Path $temporary 'mail-native-host.json.update-paused')) -eq 'mail') 'The paused manifest keeps its content.'
  # Setup wrote a new browser manifest; the mail one was not written (update stopped).
  'new browser' | Set-Content -LiteralPath (Join-Path $temporary 'browser-native-host.json')
  Resume-NativeHostManifests $temporary
  Assert ((Get-Content (Join-Path $temporary 'browser-native-host.json')) -eq 'new browser') 'A manifest Setup wrote must be kept.'
  Assert ((Get-Content (Join-Path $temporary 'mail-native-host.json')) -eq 'mail') 'A paused manifest must come back when the update stops.'
  Assert (-not (Get-ChildItem -LiteralPath $temporary -Filter '*.update-paused')) 'No paused copies may be left behind.'
  Resume-NativeHostManifests $temporary
  Suspend-NativeHostManifests (Join-Path $temporary 'missing')
} finally { Remove-Item -LiteralPath $temporary -Recurse -Force }

Write-Output 'PASS: gentle then forced closing, children first, user programs untouched, retries, spoken reasons, restarted hosts closed again, the update never blamed, and browser host manifests paused and restored.'
