param([string]$Python, [switch]$PrepareOnly)
$ErrorActionPreference = 'Stop'
function Find-GitHubCli {
  $command = Get-Command gh.exe -ErrorAction SilentlyContinue
  if ($command) { return $command.Source }
  foreach ($path in @("$env:ProgramFiles\GitHub CLI\gh.exe", "${env:ProgramFiles(x86)}\GitHub CLI\gh.exe", "$env:LOCALAPPDATA\Programs\GitHub CLI\gh.exe")) {
    if (Test-Path -LiteralPath $path) { return $path }
  }
  return $null
}
if (-not $Python) { $Python = Join-Path $env:LOCALAPPDATA 'VCBuild\py312\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run the installer builder first.' }
$gh = Find-GitHubCli
if (-not $gh) {
  if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) { throw 'Install GitHub CLI from https://cli.github.com then retry publishing.' }
  Write-Output 'Installing GitHub CLI for update publishing.'
  & winget install --id GitHub.cli --exact --source winget --silent --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) { throw 'GitHub CLI installation failed. The local installer is still available.' }
  $gh = Find-GitHubCli
  if (-not $gh) { throw 'GitHub CLI could not be located. Restart this publishing helper after installing it.' }
}
& $Python -u (Join-Path $PSScriptRoot 'github_login.py') --gh $gh
if ($LASTEXITCODE -ne 0) { throw 'GitHub sign-in did not complete. Run the installer builder again to retry.' }
if ($PrepareOnly) { Write-Output 'GitHub preparation complete. The installer builder will continue automatically.'; return }
Write-Output 'Starting update publishing. Progress appears below and is saved in VoiceCompanion-Publish-Log.txt.'
& $Python -u (Join-Path $PSScriptRoot 'publish_release.py') --setup (Join-Path $PSScriptRoot 'VoiceCompanion-Setup.exe') --gh $gh
if ($LASTEXITCODE -ne 0) { throw 'Publishing failed. Read the message above; run the installer builder again to retry.' }
