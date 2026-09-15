. "$PSScriptRoot/../powershell/find-python.ps1"

$CopilotDeniedTools = @(
    'shell(bw)'
    'shell(rbw)'
    'shell(git reset --hard)'
    'shell(git clean)'
    'shell(git branch -D)'
    'shell(terraform apply)'
    'shell(terraform destroy)'
    'shell(npm publish)'
    'shell(gh release)'
    'shell(gh repo delete)'
    'shell(gh auth token)'
    'shell(gh auth logout)'
)

$CopilotSecretEnvironmentVariables = @(
    'ANTHROPIC_API_KEY'
    'AWS_ACCESS_KEY_ID'
    'AWS_SECRET_ACCESS_KEY'
    'AWS_SESSION_TOKEN'
    'AZURE_CLIENT_SECRET'
    'BW_SESSION'
    'COPILOT_GITHUB_TOKEN'
    'GH_TOKEN'
    'GITHUB_TOKEN'
    'NPM_TOKEN'
    'OPENAI_API_KEY'
    'PYPI_TOKEN'
    'TELEGRAM_TOKEN'
    'TS_OAUTH_CLIENT_SECRET'
)

function Test-CopilotSandboxCapabilities
{
    param([UInt64]$Capabilities)

    $requiredCapabilities = 0x1 -bor 0x2
    return ($Capabilities -band $requiredCapabilities) -eq $requiredCapabilities
}

function Test-CopilotSandboxSupported
{
    if (-not ('CopilotSandboxNative' -as [type]))
    {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class CopilotSandboxNative
{
    private const uint LoadLibrarySearchSystem32 = 0x00000800;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr LoadLibraryExW(string fileName, IntPtr file, uint flags);

    [DllImport("kernel32.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    private static extern IntPtr GetProcAddress(IntPtr module, string name);

    [DllImport("kernel32.dll")]
    private static extern bool FreeLibrary(IntPtr module);

    [UnmanagedFunctionPointer(CallingConvention.Winapi, SetLastError = true)]
    private delegate int QuerySandboxSupport(out ulong capabilities);

    public static bool TryGetCapabilities(out ulong capabilities)
    {
        capabilities = 0;
        IntPtr module = LoadLibraryExW("processmodel.dll", IntPtr.Zero, LoadLibrarySearchSystem32);
        if (module == IntPtr.Zero)
            return false;

        try
        {
            IntPtr queryPointer = GetProcAddress(module, "Experimental_QuerySandboxSupport");
            if (queryPointer == IntPtr.Zero)
                return false;

            QuerySandboxSupport query =
                Marshal.GetDelegateForFunctionPointer<QuerySandboxSupport>(queryPointer);
            return query(out capabilities) != 0;
        }
        finally
        {
            FreeLibrary(module);
        }
    }
}
'@
    }

    try
    {
        [UInt64]$capabilities = 0
        return [CopilotSandboxNative]::TryGetCapabilities([ref]$capabilities) -and
            (Test-CopilotSandboxCapabilities -Capabilities $capabilities)
    }
    catch
    {
        return $false
    }
}

function Test-CopilotPathOverlap
{
    param(
        [Parameter(Mandatory)]
        [string]$Left,

        [Parameter(Mandatory)]
        [string]$Right
    )

    $leftPath = [IO.Path]::GetFullPath($Left).TrimEnd('\', '/')
    $rightPath = [IO.Path]::GetFullPath($Right).TrimEnd('\', '/')
    return $leftPath.Equals($rightPath, [StringComparison]::OrdinalIgnoreCase) -or
        $leftPath.StartsWith($rightPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        $rightPath.StartsWith($leftPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

function Resolve-CopilotPhysicalPath
{
    param([Parameter(Mandatory)][string]$Path)

    $fullPath = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($fullPath)
    $current = Get-Item -LiteralPath $root -Force
    $relative = $fullPath.Substring($root.Length)
    $parts = @($relative.Split(
            [IO.Path]::DirectorySeparatorChar,
            [StringSplitOptions]::RemoveEmptyEntries
        ))
    for ($index = 0; $index -lt $parts.Count; $index++)
    {
        $part = $parts[$index]
        $candidate = Join-Path $current.FullName $part
        if (-not (Test-Path -LiteralPath $candidate))
        {
            $remaining = [string]::Join(
                [IO.Path]::DirectorySeparatorChar,
                $parts[$index..($parts.Count - 1)]
            )
            return [IO.Path]::GetFullPath((Join-Path $current.FullName $remaining))
        }
        $current = Get-Item -LiteralPath $candidate -Force
        $target = $current.ResolveLinkTarget($true)
        if ($target)
        {
            $current = $target
        }
    }
    return $current.FullName
}

$CopilotExpectedHome = $env:COPILOT_HOME

function Get-CopilotPositionals
{
    param([string[]]$Arguments)

    $optionsWithValues = @(
        '-i',
        '-n',
        '-p',
        '--agent',
        '--attachment',
        '--available-tools',
        '--context',
        '--deny-tool',
        '--deny-url',
        '--disable-mcp-server',
        '--effort',
        '--excluded-tools',
        '--interactive',
        '--log-dir',
        '--log-level',
        '--max-ai-credits',
        '--max-autopilot-continues',
        '--mode',
        '--model',
        '--name',
        '--output-format',
        '--prompt',
        '--reasoning-effort',
        '--secret-env-vars',
        '--stream',
        '--usage-output-file'
    )
    $positionals = @()
    $skipNext = $false
    $optionsEnded = $false
    foreach ($argument in $Arguments)
    {
        if ($skipNext)
        {
            $skipNext = $false
            continue
        }
        if (-not $optionsEnded -and $argument -eq '--')
        {
            $optionsEnded = $true
            continue
        }
        if (-not $optionsEnded -and $argument.StartsWith('-'))
        {
            $option = $argument.Split('=', 2)[0]
            if (-not $argument.Contains('=') -and $option -in $optionsWithValues)
            {
                $skipNext = $true
            }
            continue
        }
        $positionals += $argument
    }
    return $positionals
}

function Get-CopilotLaunchBlockReason
{
    param([string[]]$Arguments)

    $marker = Join-Path $HOME '.dotfiles-env'
    $work = $env:DOTFILES_ENV -eq 'work' -or ((Test-Path $marker) -and (Get-Content $marker -Raw).Trim() -eq 'work')
    if ($work) {
        $python = Find-DotfilesPython
        if (-not $python) { return 'Python is required to verify the work sandbox policy' }
        $policyOutput = & $python (Join-Path $HOME '.config/dotfiles/check-copilot-policy.py') 2>&1
        if ($LASTEXITCODE -ne 0) { return "required managed sandbox policy could not be verified: $($policyOutput -join "`n")" }
        if (-not (Test-CopilotSandboxSupported)) {
            return 'this Windows host cannot enforce the required work sandbox'
        }
    }

    if ($env:COPILOT_ALLOW_ALL -eq 'true' -or $env:COPILOT_ASSISTED_APPROVAL -eq 'true')
    {
        return 'a Copilot permission-escalation environment variable is enabled'
    }

    if ($env:COPILOT_PROVIDER_BASE_URL)
    {
        return 'COPILOT_PROVIDER_BASE_URL can redirect session data to a custom model provider'
    }

    foreach ($name in @(
            'COPILOT_OTEL_ENABLED',
            'COPILOT_OTEL_FILE_EXPORTER_PATH',
            'OTEL_EXPORTER_OTLP_ENDPOINT',
            'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT'
        ))
    {
        if ([Environment]::GetEnvironmentVariable($name))
        {
            return "$name can export Copilot session telemetry"
        }
    }

    if ($env:COPILOT_HOME -cne $CopilotExpectedHome)
    {
        return 'COPILOT_HOME changed after the hardened configuration loaded'
    }

    if ((Get-Location).Provider.Name -ne 'FileSystem')
    {
        return 'the current PowerShell location is not a filesystem path'
    }

    $current = (Get-Location).ProviderPath
    $homePath = (Resolve-CopilotPhysicalPath $HOME).TrimEnd('\', '/')
    $currentPath = (Resolve-CopilotPhysicalPath $current).TrimEnd('\', '/')
    $copilotHomePath = Resolve-CopilotPhysicalPath $(if ($CopilotExpectedHome)
        { $CopilotExpectedHome } else { "$HOME\.copilot" })
    if ($currentPath.Equals($homePath, [StringComparison]::OrdinalIgnoreCase) -or
        $homePath.StartsWith($currentPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase))
    {
        return "the workspace '$currentPath' contains the home directory"
    }

    $sensitivePaths = @(
        "$homePath\.aws"
        "$homePath\.claude"
        "$homePath\.codex"
        "$homePath\.config\alerts"
        "$homePath\.config\Bitwarden CLI"
        "$homePath\.config\gh"
        "$homePath\.copilot"
        $copilotHomePath
        "$homePath\.gnupg"
        "$homePath\.ssh"
        "$homePath\AppData\Local\Bitwarden"
        "$homePath\AppData\Roaming\Bitwarden"
        "$homePath\AppData\Roaming\GitHub CLI"
    )
    foreach ($path in $sensitivePaths)
    {
        $physicalPath = Resolve-CopilotPhysicalPath $path
        if (Test-CopilotPathOverlap -Left $currentPath -Right $physicalPath)
        {
            return "the workspace '$currentPath' overlaps sensitive path '$physicalPath'"
        }
    }

    foreach ($argument in $Arguments)
    {
        if ($argument -in @(
                '-C',
                '-r',
                '-w',
                '--allow-all',
                '--allow-all-mcp-server-instructions',
                '--allow-all-paths',
                '--allow-all-tools',
                '--allow-all-urls',
                '--allow-tool',
                '--allow-url',
                '--assisted-approval',
                '--add-dir',
                '--add-github-mcp-tool',
                '--add-github-mcp-toolset',
                '--additional-mcp-config',
                '--config-dir',
                '--connect',
                '--continue',
                '--enable-all-github-mcp-tools',
                '--enable-mcp-server',
                '--extension-sdk-path',
                '--no-experimental',
                '--no-sandbox',
                '--plugin-dir',
                '--remote',
                '--remote-export',
                '--resume',
                '--session-id',
                '--share',
                '--share-gist',
                '--worktree',
                '--yolo'
            ) -or
            ($argument.Length -gt 2 -and
                ($argument.StartsWith('-C', [StringComparison]::Ordinal) -or
                    $argument.StartsWith('-r', [StringComparison]::Ordinal) -or
                    $argument.StartsWith('-w', [StringComparison]::Ordinal))) -or
            $argument.StartsWith('-C=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('-r=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--allow-tool=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--allow-url=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--add-dir=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--add-github-mcp-tool=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--add-github-mcp-toolset=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--additional-mcp-config=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--config-dir=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--connect=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--enable-mcp-server=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--extension-sdk-path=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--plugin-dir=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--resume=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--session-id=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--share=', [StringComparison]::Ordinal) -or
            $argument.StartsWith('--worktree=', [StringComparison]::Ordinal))
        {
            return "argument '$argument' weakens the hardened Copilot defaults"
        }
    }

    $positionals = @(Get-CopilotPositionals -Arguments $Arguments)
    if ($positionals.Count -ge 2)
    {
        $managementCommand = $positionals[0]
        $subcommand = $positionals[1]
        $mutatingSubcommands = switch ($managementCommand)
        {
            'mcp' { @('add', 'remove') }
            'skill' { @('add', 'remove') }
            'plugin' { @('add', 'install', 'remove', 'uninstall', 'update') }
            'plugins' { @('add', 'disable', 'enable', 'install', 'remove', 'rm', 'update') }
            default { @() }
        }
        if ($subcommand -in $mutatingSubcommands)
        {
            return "command '$managementCommand $subcommand' changes Copilot extension configuration"
        }
        if ($managementCommand -in @('plugin', 'plugins') -and
            $subcommand -in @('marketplace', 'marketplaces') -and
            $positionals.Count -ge 3 -and
            $positionals[2] -in @('add', 'refresh', 'remove', 'rm', 'update'))
        {
            return "command '$managementCommand $subcommand $($positionals[2])' changes Copilot extension configuration"
        }
    }

    return $null
}

function copilot
{
    $blockReason = Get-CopilotLaunchBlockReason -Arguments $args
    if ($blockReason)
    {
        throw "copilot: refusing launch because $blockReason"
    }

    $defaults = @(
        '--experimental'
        '--disable-builtin-mcps'
        '--no-remote'
        '--no-remote-export'
        "--secret-env-vars=$($CopilotSecretEnvironmentVariables -join ',')"
    )
    $defaults += $CopilotDeniedTools | ForEach-Object { "--deny-tool=$_" }
    & copilot.exe @defaults @args
}

Set-Alias cc copilot
