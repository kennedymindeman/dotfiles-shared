function Find-DotfilesPython {
    $candidates = @()
    foreach ($name in @('python3', 'python')) {
        $candidates += @(Get-Command $name -All -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    }
    # Discover python.org installations even before their PATH change takes effect.
    foreach ($key in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore', 'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        foreach ($version in @(Get-ChildItem -LiteralPath $key -ErrorAction SilentlyContinue)) {
            $location = Join-Path $version.PSPath 'InstallPath'
            if (Test-Path -LiteralPath $location) {
                $directory = (Get-Item -LiteralPath $location).GetValue('')
                if ($directory) { $candidates += Join-Path $directory 'python.exe' }
            }
        }
    }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        # Store aliases and the Python manager can download runtimes when invoked.
        if ($candidate -like '*\Microsoft\WindowsApps\*' -or -not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            $path = & $candidate -c 'import sys; print(sys.executable); sys.exit(sys.version_info < (3, 11))' 2>$null
            if ($LASTEXITCODE -eq 0 -and $path) { return [string]($path | Select-Object -Last 1) }
        }
        catch { continue }
    }
    return $null
}

