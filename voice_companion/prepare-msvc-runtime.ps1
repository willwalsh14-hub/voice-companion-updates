$ErrorActionPreference = 'Stop'
$runtimeInstaller = Join-Path ([IO.Path]::GetTempPath()) ('VoiceCompanion-VC-' + [Guid]::NewGuid().ToString() + '.exe')
try {
    Write-Output 'Preparing the Microsoft Visual C++ x64 runtime required by eSpeak. Windows may ask for administrator approval.'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri 'https://aka.ms/vc14/vc_redist.x64.exe' -OutFile $runtimeInstaller
    $signature = Get-AuthenticodeSignature -FilePath $runtimeInstaller
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation(?:,|$)') {
        throw 'The Microsoft runtime installer signature could not be verified.'
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $options = @{ FilePath = $runtimeInstaller; ArgumentList = '/install', '/passive', '/norestart'; Wait = $true; PassThru = $true }
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { $options.Verb = 'RunAs' }
    $process = Start-Process @options
    if ($process.ExitCode -notin @(0, 1638, 3010)) { throw "Microsoft runtime preparation failed with exit code $($process.ExitCode)." }
} finally {
    Remove-Item $runtimeInstaller -Force -ErrorAction SilentlyContinue
}
