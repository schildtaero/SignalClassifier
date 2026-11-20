param([Parameter(ValueFromRemainingArguments=$true)][string[]]$AppArgs)
$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $PSScriptRoot
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Write-Host 'WSL2 wird benötigt. In einer administrativen PowerShell: wsl --install -d Ubuntu-24.04'
    exit 1
}
$LinuxDir = & wsl.exe --exec wslpath -a $ProjectDir
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Keine nutzbare WSL-Distribution. Einrichten: wsl --install -d Ubuntu-24.04'
    exit 1
}
$LinuxDir = ($LinuxDir | Out-String).Trim()
& wsl.exe --cd $LinuxDir --exec bash ./start.sh @AppArgs
exit $LASTEXITCODE
