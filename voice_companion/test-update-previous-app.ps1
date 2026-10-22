$ErrorActionPreference = 'Stop'
$source = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'apply-update.ps1') -Raw
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Updater syntax errors.' }
$function = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Stop-PreviousCompanion' }, $true)
if (-not $function) { throw 'Stopping a stuck previous app is missing.' }
Invoke-Expression $function.Extent.Text
$script:logged = @()
function Write-UpdateError($failure) { $script:logged += [string]$failure }
$AppPath = Join-Path $PSScriptRoot 'fixture-install\VoiceCompanion.exe'
# Fake process: exits after $exitAfter calls to WaitForExit, or when the given step happens.
function New-FakeApp($path, [string]$exitsOn) {
  $item = [pscustomobject]@{ Path = $path; ExitsOn = $exitsOn; Exited = $false; Closed = $false; Killed = $false; Disposed = $false; Waits = @() }
  $item | Add-Member ScriptMethod WaitForExit { param($milliseconds) $this.Waits += $milliseconds; return $this.Exited }
  $item | Add-Member ScriptMethod CloseMainWindow { $this.Closed = $true; if ($this.ExitsOn -eq 'close') { $this.Exited = $true }; return $true }
  $item | Add-Member ScriptMethod Kill { $this.Killed = $true; if ($this.ExitsOn -eq 'kill') { $this.Exited = $true } }
  $item | Add-Member ScriptMethod Dispose { $this.Disposed = $true }
  return $item
}

# Closed normally within 30 seconds: nothing is stopped.
$normal = New-FakeApp $AppPath 'close'; $normal.Exited = $true
Stop-PreviousCompanion $normal
if ($normal.Closed -or $normal.Killed -or ($normal.Waits -join ',') -ne '30000' -or -not $normal.Disposed) { throw 'A closed app must not be stopped.' }

# Stuck, but closes when asked: it is asked politely and not killed.
$polite = New-FakeApp $AppPath.ToUpperInvariant() 'close'
Stop-PreviousCompanion $polite
if (-not $polite.Closed -or $polite.Killed -or -not $polite.Disposed) { throw 'A stuck app must first be asked to close.' }

# Stuck and ignores the close request: it is stopped so the update can install and restart.
$stuck = New-FakeApp $AppPath 'kill'
Stop-PreviousCompanion $stuck
if (-not $stuck.Closed -or -not $stuck.Killed -or -not $stuck.Disposed) { throw 'A stuck app must be stopped after the wait.' }
if (($stuck.Waits -join ',') -ne '30000,10000,10000') { throw 'The waits must be 30 seconds, then 10 seconds each.' }

# A process from another program that reused the id is never touched.
$other = New-FakeApp (Join-Path $PSScriptRoot 'another-install\VoiceCompanion.exe') 'kill'
Stop-PreviousCompanion $other
if ($other.Closed -or $other.Killed -or -not $other.Disposed) { throw 'Another program must never be stopped.' }

# Cannot be stopped at all: installation must not start.
$frozen = New-FakeApp $AppPath 'never'
$caught = $false
try { Stop-PreviousCompanion $frozen } catch { $caught = $true }
if (-not $caught -or -not $frozen.Killed -or -not $frozen.Disposed) { throw 'An app that cannot be stopped must prevent installation.' }

# No previous app at all.
Stop-PreviousCompanion $null

if ($source -match "WaitForExit\(30000\) \| Out-Null \}\s*\r?\n\s*if \(Get-Process -Id \`$PreviousProcessId") { throw 'The helper still gives up when the old app is slow to close.' }
if ($source.IndexOf("`n  Stop-PreviousCompanion") -lt 0 -or $source.IndexOf("`n  Stop-PreviousCompanion") -gt $source.IndexOf("`n  Close-InstalledBrowserHosts")) { throw 'The previous app must be closed before installation starts.' }
Write-Output 'PASS: normal exit, polite close, stopping a stuck app, other programs untouched, unstoppable app, and ordering.'
