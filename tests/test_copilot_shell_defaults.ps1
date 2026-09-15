# Keep tests independent of a work profile already installed on the host.
$originalDotfilesEnvironment = $env:DOTFILES_ENV
$originalCopilotHome = $env:COPILOT_HOME
$env:DOTFILES_ENV = 'home'
$env:COPILOT_HOME = Join-Path $HOME '.managed-copilot-test'
function Test-Path {
    param([Alias('LiteralPath')][string]$Path)
    if ($Path -eq (Join-Path $HOME '.dotfiles-env')) { return $false }
    Microsoft.PowerShell.Management\Test-Path -LiteralPath $Path
}
try {
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
. "$repo\copilot\shell-defaults.ps1"

$project = $repo
function Add-Type { throw 'sandbox probe prerequisite unavailable' }
if (Test-CopilotSandboxSupported) {
    throw 'failed sandbox probe reported support'
}
Remove-Item Function:Add-Type

function Test-CopilotSandboxSupported { return $true }

if (Test-CopilotSandboxCapabilities -Capabilities 1) {
    throw 'Windows sandbox accepted create-process support without filesystem denial'
}
if (-not (Test-CopilotSandboxCapabilities -Capabilities 3)) {
    throw 'Windows sandbox rejected the required capability set'
}

Push-Location $project
try {
    foreach ($argument in @(
            '--allow-all',
            '--no-sandbox',
            '-w',
            '-wbranch',
            '--allow-tool=shell',
            '--additional-mcp-config=@mcp.json',
            '--plugin-dir=plugin',
            '--enable-all-github-mcp-tools',
            '--add-github-mcp-tool=*',
            '--add-github-mcp-toolset=all',
            '--allow-all-mcp-server-instructions',
            '--remote',
            '--remote-export',
            '-C',
            '-CC:\Users\tester\.ssh',
            '-rsession',
            '--resume=session',
            '--continue',
            '--session-id=session',
            '--connect=session',
            '--worktree=branch'
        ))
    {
        if ((Get-CopilotLaunchBlockReason -Arguments @($argument)) -notmatch 'weakens') {
            throw "permission escalation flag was allowed: $argument"
        }
    }
    if ((Get-CopilotLaunchBlockReason -Arguments @('--enable-mcp-server=github-mcp-server')) -notmatch 'weakens') {
        throw 'MCP re-enable flag was allowed'
    }
    foreach ($arguments in @(
            @('mcp', 'add', 'test', 'cmd'),
            @('mcp', 'remove', 'test'),
            @('skill', 'add', 'owner/repo'),
            @('skill', 'remove', 'test'),
            @('plugin', 'install', 'owner/repo'),
            @('plugin', 'update', 'test'),
            @('plugin', 'marketplace', 'add', 'test', 'owner/repo'),
            @('plugin', 'marketplace', 'rm', 'test'),
            @('plugins', 'enable', 'test'),
            @('plugins', 'install', 'test'),
            @('plugins', 'marketplaces', 'refresh', 'test')
        ))
    {
        if ((Get-CopilotLaunchBlockReason -Arguments $arguments) -notmatch 'changes Copilot extension configuration') {
            throw "mutating management command was allowed: $($arguments -join ' ')"
        }
    }
    foreach ($arguments in @(
            @('mcp', 'list'),
            @('mcp', 'get', 'test'),
            @('plugin', 'list'),
            @('plugins', 'list'),
            @('--name', 'plugin', '-i', 'update')
        ))
    {
        if (Get-CopilotLaunchBlockReason -Arguments $arguments) {
            throw "read-only management command was blocked: $($arguments -join ' ')"
        }
    }
    if (Get-CopilotLaunchBlockReason -Arguments @('--version')) {
        throw 'ordinary launch was blocked'
    }
    $expectedCopilotHome = $env:COPILOT_HOME
    try {
        $env:COPILOT_HOME = Join-Path $HOME '.other-copilot-test'
        if ((Get-CopilotLaunchBlockReason -Arguments @('--version')) -notmatch 'COPILOT_HOME changed') {
            throw 'Copilot home override after wrapper load was allowed'
        }
        $env:COPILOT_HOME = $expectedCopilotHome.ToUpperInvariant()
        if ((Get-CopilotLaunchBlockReason -Arguments @('--version')) -notmatch 'COPILOT_HOME changed') {
            throw 'Case-only Copilot home override after wrapper load was allowed'
        }
    }
    finally {
        $env:COPILOT_HOME = $expectedCopilotHome
    }
    $originalExpectedHome = $CopilotExpectedHome
    try {
        $env:COPILOT_HOME = "$HOME\managed\..\managed"
        $CopilotExpectedHome = $env:COPILOT_HOME
        $script:TestWorkspace = Join-Path $HOME 'managed\project'
        function Get-Location {
            [pscustomobject]@{ Provider = @{ Name = 'FileSystem' }; ProviderPath = $script:TestWorkspace }
        }
        try {
            if ((Get-CopilotLaunchBlockReason -Arguments @('--version')) -notmatch 'overlaps sensitive path') {
                throw 'managed Copilot home path alias was allowed'
            }
        }
        finally {
            Remove-Item Function:\Get-Location
        }
    }
    finally {
        $env:COPILOT_HOME = $expectedCopilotHome
        $CopilotExpectedHome = $originalExpectedHome
    }

    function copilot.exe { $script:CapturedCopilotArgs = @($args) }
    $refused = $false
    try {
        copilot --allow-all
    }
    catch {
        $refused = $_.Exception.Message -match 'refusing launch'
    }
    if (-not $refused) {
        throw 'refused launch did not raise a terminating error'
    }
    copilot --version
    foreach ($expected in @(
            '--experimental',
            '--disable-builtin-mcps',
            '--no-remote',
            '--no-remote-export',
            '--deny-tool=shell(git clean)'
        ))
    {
        if ($CapturedCopilotArgs -notcontains $expected) {
            throw "missing expected Copilot argument: $expected"
        }
    }
    if ($CapturedCopilotArgs -contains '--assisted-approval') {
        throw 'assisted approval remains enabled'
    }
    if ($CapturedCopilotArgs -contains '--deny-tool=shell(git push)') {
        throw 'git push remains permanently denied'
    }
}
finally {
    Pop-Location
}

if (-not (Test-CopilotPathOverlap -Left "$HOME\.ssh\project" -Right "$HOME\.ssh")) {
    throw 'sensitive-directory overlap was not detected'
}

# Substitute only the current directory, without creating paths in the user's home.
foreach ($suffix in @('', '\project')) {
    $script:TestWorkspace = "$HOME\.config\Bitwarden CLI$suffix"
    function Get-Location {
        [pscustomobject]@{ Provider = @{ Name = 'FileSystem' }; ProviderPath = $script:TestWorkspace }
    }
    try {
        if ((Get-CopilotLaunchBlockReason -Arguments @('--version')) -notmatch 'overlaps sensitive path') {
            throw "Bitwarden workspace was allowed: $script:TestWorkspace"
        }
    }
    finally {
        Remove-Item Function:\Get-Location
    }
}

Push-Location $HOME
try {
    if ((Get-CopilotLaunchBlockReason -Arguments @('--version')) -notmatch 'home directory') {
        throw 'home-directory workspace was allowed'
    }
}
finally {
    Pop-Location
}

$originalProviderBaseUrl = $env:COPILOT_PROVIDER_BASE_URL
try {
    $env:COPILOT_PROVIDER_BASE_URL = 'https://example.invalid'
    Push-Location $project
    try {
        if ((Get-CopilotLaunchBlockReason -Arguments @('--version')) -notmatch 'custom model provider') {
            throw 'custom provider routing environment variable was allowed'
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:COPILOT_PROVIDER_BASE_URL = $originalProviderBaseUrl
}

$originalOtelEndpoint = $env:OTEL_EXPORTER_OTLP_ENDPOINT
try {
    $env:OTEL_EXPORTER_OTLP_ENDPOINT = 'https://example.invalid'
    Push-Location $project
    try {
        if ((Get-CopilotLaunchBlockReason -Arguments @('--version')) -notmatch 'session telemetry') {
            throw 'OTel session export environment variable was allowed'
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:OTEL_EXPORTER_OTLP_ENDPOINT = $originalOtelEndpoint
}

function Test-CopilotSandboxSupported { return $false }
Push-Location $project
try {
    if (Get-CopilotLaunchBlockReason -Arguments @('--version')) {
        throw 'unsupported Windows sandbox host blocked an ordinary launch'
    }
}
finally {
    Pop-Location
}

Write-Output 'ok: Copilot PowerShell defaults enforce the hardened launch policy'

$previousEnvironment = $env:DOTFILES_ENV
$previousWarningPreference = $WarningPreference
try {
    $env:DOTFILES_ENV = 'work'
    $WarningPreference = 'Stop'
    function Test-CopilotSandboxSupported { return $true }
    function Find-DotfilesPython { return 'Test-PolicyPython' }
    function Test-PolicyPython
    {
        Write-Output 'copilot: warning: managed sandbox policy is missing'
        $global:LASTEXITCODE = 0
    }
    $policyMessages = @(Get-CopilotLaunchBlockReason -Arguments @('--version') 3>&1)
    $reason = $policyMessages | Where-Object {
        $_ -isnot [System.Management.Automation.WarningRecord]
    }
    if ($reason) {
        throw "missing policy blocked launch: $($reason -join "`n")"
    }
    if (($policyMessages -join "`n") -notmatch 'managed sandbox policy is missing') {
        throw 'missing policy warning was not shown'
    }

    function Test-CopilotSandboxSupported { return $false }
    $sandboxMessages = @(Get-CopilotLaunchBlockReason -Arguments @('--version') 3>&1)
    $reason = $sandboxMessages | Where-Object {
        $_ -isnot [System.Management.Automation.WarningRecord]
    }
    if ($reason) {
        throw "unsupported Windows sandbox blocked launch: $($reason -join "`n")"
    }
    if (($sandboxMessages -join "`n") -notmatch 'cannot enforce the work sandbox') {
        throw 'unsupported Windows sandbox warning was not shown'
    }

    function Test-CopilotSandboxSupported { return $true }
    function Test-PolicyPython
    {
        Write-Output 'managed policy must set sandbox.enabled to true'
        $global:LASTEXITCODE = 1
    }
    if ((Get-CopilotLaunchBlockReason -Arguments @('--version')) -notmatch 'could not be verified') {
        throw 'invalid managed policy did not block launch'
    }

    function Find-DotfilesPython { return $null }
    if ((Get-CopilotLaunchBlockReason -Arguments @('--version')) -notmatch 'Python is required') {
        throw 'work launch proceeded without policy verification'
    }
} finally {
    Remove-Item Function:Find-DotfilesPython
    Remove-Item Function:Test-PolicyPython
    $env:DOTFILES_ENV = $previousEnvironment
    $WarningPreference = $previousWarningPreference
}

} finally {
    Remove-Item Function:Test-Path
    $env:DOTFILES_ENV = $originalDotfilesEnvironment
    $env:COPILOT_HOME = $originalCopilotHome
}
$global:LASTEXITCODE = 0
