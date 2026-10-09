param([string]$InstallerPath,[string]$AppPath,[int]$PreviousProcessId,[string]$ExpectedHash,[string]$DataFolder)
$ErrorActionPreference = 'Stop'
$result = Join-Path $DataFolder 'update-result.json'
try {
  $previous = Get-Process -Id $PreviousProcessId -ErrorAction SilentlyContinue
  if ($previous) { $previous.WaitForExit(30000) | Out-Null }
  if (Get-Process -Id $PreviousProcessId -ErrorAction SilentlyContinue) { throw 'The previous app is still running.' }
  if ((Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash -ne $ExpectedHash) { throw 'Installer integrity check failed.' }
  $setup = Start-Process -FilePath $InstallerPath -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /RESTARTEXITCODE=3010 /CLOSEAPPLICATIONS /NORESTARTAPPLICATIONS /LOG' -PassThru
  $setup.WaitForExit()
  if ($setup.ExitCode -eq 3010) {
    '{"succeeded":false,"restart_required":true}' | Set-Content -LiteralPath $result -Encoding UTF8
    Start-Process -FilePath $AppPath
    exit 0
  }
  if ($setup.ExitCode -ne 0) { throw 'Setup did not finish successfully.' }
  '{"succeeded":true}' | Set-Content -LiteralPath $result -Encoding UTF8
  Start-Process -FilePath $AppPath
} catch {
  '{"succeeded":false}' | Set-Content -LiteralPath $result -Encoding UTF8
  if (-not (Get-Process -Id $PreviousProcessId -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath $AppPath)) { Start-Process -FilePath $AppPath }
}
