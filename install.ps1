# Package installation only. Run link.ps1 separately to configure the shell.
[CmdletBinding()]
param(
    [Alias('Profile')][ValidateSet('work', 'home')][string]$Environment,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Validate before bootstrapping Python. Match dotfiles_profile.resolve_profile.
if (-not $Environment) { $Environment = $env:DOTFILES_ENV }
if (-not $Environment) {
    $marker = Join-Path $HOME '.dotfiles-env'
    if (Test-Path -LiteralPath $marker) { $Environment = (Get-Content -LiteralPath $marker -Raw).Trim() }
}
if ($Environment -cnotin @('work', 'home')) {
    throw 'Choose a profile first: .\install.ps1 -Profile work or -Profile home.'
}

. "$PSScriptRoot/powershell/find-python.ps1"

$python = Find-DotfilesPython
if (-not $python) {
    if ($DryRun) {
        Write-Output 'Python 3.11+ is missing. Bootstrap: winget install --exact --id Python.Python.3.14 --source winget'
        Write-Output 'Package preflight needs Python. No packages or profiles were changed.'
        exit 1
    }
    if (-not (Get-Command winget -CommandType Application -ErrorAction SilentlyContinue)) {
        throw 'Python 3.11+ and WinGet are required. Install them through an approved source first.'
    }
    & winget install --exact --id Python.Python.3.14 --source winget
    if ($LASTEXITCODE -ne 0) { throw "Python installation failed with exit code $LASTEXITCODE." }
    $env:Path = $env:Path + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    $python = Find-DotfilesPython
    if (-not $python) { throw 'Python was installed but is unavailable. Reopen PowerShell and rerun install.ps1.' }
}
$arguments = @((Join-Path $PSScriptRoot 'install.py'), '--profile', $Environment)
if ($DryRun) { $arguments += '--dry-run' }
& $python @arguments
exit $LASTEXITCODE
