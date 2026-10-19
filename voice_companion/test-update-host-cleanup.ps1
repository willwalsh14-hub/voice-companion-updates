$ErrorActionPreference = 'Stop'
$source = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'apply-update.ps1') -Raw
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Updater syntax errors.' }
$function = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Close-InstalledBrowserHosts' }, $true)
if (-not $function) { throw 'Browser host cleanup is missing.' }
Invoke-Expression $function.Extent.Text
$AppPath = Join-Path $PSScriptRoot 'fixture-install\VoiceCompanion.exe'
function New-FakeHost($path, $exited = $false) {
  $item = [pscustomobject]@{ Path = $path; HasExited = $exited; Killed = $false; Disposed = $false; Waited = $false }
  $item | Add-Member ScriptMethod Kill { $this.Killed = $true }
  $item | Add-Member ScriptMethod WaitForExit { param($milliseconds) $this.Waited = ($milliseconds -eq 10000); return $true }
  $item | Add-Member ScriptMethod Dispose { $this.Disposed = $true }
  return $item
}
$expected = Join-Path (Split-Path $AppPath) 'VoiceCompanion-BrowserHost.exe'
$ours = New-FakeHost $expected.ToUpperInvariant()
$other = New-FakeHost (Join-Path $PSScriptRoot 'another-install\VoiceCompanion-BrowserHost.exe')
$exited = New-FakeHost $expected $true
function Get-Process { param($Name, $ErrorAction) return @($ours, $other, $exited) }
Close-InstalledBrowserHosts
if (-not $ours.Killed -or -not $ours.Waited) { throw 'Installed helper was not stopped and awaited.' }
if ($other.Killed -or $exited.Killed) { throw 'Unrelated or exited helper was stopped.' }
if (-not $ours.Disposed -or -not $other.Disposed -or -not $exited.Disposed) { throw 'Process handles leaked.' }
$failure = New-FakeHost $expected
$failure | Add-Member ScriptMethod WaitForExit { param($milliseconds) return $false } -Force
$ours = $failure; $other = $exited
$caught = $false
try { Close-InstalledBrowserHosts } catch { $caught = $true }
if (-not $caught -or -not $failure.Disposed) { throw 'A helper that remains running must prevent installation.' }
if ($source.IndexOf("`n  Close-InstalledBrowserHosts") -gt $source.IndexOf("`n  `$setup = Start-Process")) { throw 'Cleanup must precede setup.' }
Write-Output 'PASS: installed helper cleanup, path isolation, exited helpers, timeout failure, handle disposal, and installation ordering.'
