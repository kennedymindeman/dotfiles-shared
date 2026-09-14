param(
    [Alias('Profile')][ValidateSet('work', 'home')][string]$Environment,
    [switch]$DryRun,
    [string]$Overlay
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/powershell/find-python.ps1"
$python = Find-DotfilesPython
if (-not $python) { throw 'Python 3.11+ is required; run install.ps1 first' }
$userWeztermConfig = [Environment]::GetEnvironmentVariable('WEZTERM_CONFIG_FILE', 'User')
$linkArgs = @('--powershell-profile', $PROFILE.CurrentUserCurrentHost)
if ($Environment) { $linkArgs += @('--profile', $Environment) }
if ($userWeztermConfig) { $linkArgs += @('--wezterm-user-config', $userWeztermConfig) }
if ($DryRun) { $linkArgs += '--dry-run' }
if ($Overlay) { $linkArgs += @('--overlay', $Overlay) }
& $python "$PSScriptRoot/link.py" @linkArgs
if ($LASTEXITCODE -ne 0) { throw 'Configuration linking failed' }
