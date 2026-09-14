# -------------------------------
#  Basic environment setup
# -------------------------------
Set-StrictMode -Version Latest

# Add common CLI tools to PATH if installed via Scoop or other managers
$commonBins = @(
    "$HOME\scoop\shims",
    "$HOME\.local\bin"
)

foreach ($bin in $commonBins)
{
    if (Test-Path $bin)
    {
        $env:PATH = "$bin;$env:PATH"
    }
}

# -------------------------------
#  zoxide (directory jumping)
# -------------------------------
# Note: requires "zoxide init powershell"
if (Get-Command zoxide -ErrorAction SilentlyContinue) {
    Invoke-Expression (zoxide init powershell | Out-String)
}

# Alt+z → interactive smart directory search (zoxide + fzf picker)
# Opens the fuzzy directory picker directly and cd's into the choice,
# without echoing a command onto the prompt.
Set-PSReadLineKeyHandler -Chord 'Alt+z' -ScriptBlock {
    $dir = zoxide query --interactive
    if ($dir)
    {
        Set-Location $dir
    }
    [Microsoft.PowerShell.PSConsoleReadLine]::InvokePrompt()
}

# -------------------------------
#  fzf integration
# -------------------------------
# Ctrl+t = file picker
# Ctrl+r = history search
# Alt+c   = directory jump
if ((Get-Module -ListAvailable PSFzf) -and (Get-Command fzf -ErrorAction SilentlyContinue)) {
Import-Module PSFzf
Set-PsFzfOption `
    -PSReadlineChordProvider 'Ctrl+t' `
    -PSReadlineChordReverseHistory 'Ctrl+r' `
    -PSReadlineChordSetLocation 'Alt+c'
}

# -------------------------------
#  eza aliases (modern ls)
# -------------------------------
# Simple list
if (Get-Command eza -ErrorAction SilentlyContinue) { Set-Alias ls eza }

# Long/pretty list
function ll
{
    eza -l --git @args
}

# Long/hidden/all
function la
{
    eza -la --git @args
}

# Tree view
function lt
{
    eza -T --level=2 @args
}

# -------------------------------
# Clipboard helpers
# -------------------------------
# Copy current directory path to clipboard
function cwd
{
    (Get-Location).Path | clip
}

# Ctrl+Alt+C → copy working directory to clipboard
Set-PSReadLineKeyHandler -Chord 'Ctrl+Alt+c' -ScriptBlock {
    cwd
}

# -------------------------------
#  Search tools
# -------------------------------
# fd: wrapped further down to match the mac hidden/ignore policy
# rg: ripgrep for searching inside files
if (Get-Command rg -ErrorAction SilentlyContinue) { Set-Alias grep rg }

# Example helper: search text recursively with line numbers
function rgl
{
    rg --line-number @args
}

# -------------------------------
#  Git aliases
# -------------------------------
# Core shortcuts
Set-Alias g git

# Fuzzy checkout a commit by message
function gco
{
    $selected = git log --oneline | fzf --no-sort --prompt "checkout> "
    if ($selected)
    {
        $hash = ($selected -split ' ')[0]
        git checkout $hash
    }
}

# -------------------------------
#  Editor default (Neovim, matching the mac zshrc)
# -------------------------------
$env:EDITOR = "nvim"
$env:VISUAL = "nvim"

# Type "vim <file>" to open in Neovim
function vim { nvim @args }

# History UX
Set-PSReadLineOption -PredictionSource History
Set-PSReadLineOption -PredictionViewStyle ListView

# -------------------------------
# Search tools (extended)
# -------------------------------
# fd: show hidden files but still honor .gitignore — matches the zshrc `fd`
# alias (--hidden --exclude .git). Call fd.exe, not bare fd, so this function
# doesn't recurse into itself.
function fd
{
    fd.exe --hidden --exclude .git @args
}

# fda: also disable .gitignore handling, so it walks node_modules/.venv/etc.
function fda
{
    fd.exe -I -H @args
}

# rg: ignore .gitignore and include hidden files
function rga
{
    rg -uu @args
}

# -------------------------------
#  Notes (~/.notes)
# -------------------------------
$notesFile = "$HOME\.notes"

# Fuzzy search notes
# Usage: lookup          (browse all entries)
#        lookup "CIM"    (pre-fill fzf query)
function lookup
{
    param(
        [string]$Query = ""
    )

    Get-Content $notesFile |
        fzf --query $Query `
            --prompt "lookup> " `
            --no-sort `
            --preview-window hidden
}

# Append a new line to the notes file
# Usage: note TERM = definition  (quotes optional)
function note
{
    param(
        [Parameter(Mandatory, ValueFromRemainingArguments)]
        [string[]]$Words
    )

    $line = $Words -join ' '
    Add-Content -Path $notesFile -Value $line
    Write-Host "Added: $line" -ForegroundColor Green
}

# Interactively delete lines from the notes file
# TAB to multi-select, ENTER to confirm deletion
function delnote
{
    $content = Get-Content $notesFile
    $toDelete = $content |
        fzf --multi `
            --prompt "delete> " `
            --no-sort `
            --preview-window hidden `
            --header "TAB: select  ENTER: delete"

    if (-not $toDelete)
    {
        return
    }

    $remaining = $content | Where-Object { $toDelete -notcontains $_ }
    $remaining | Set-Content $notesFile
    Write-Host "Removed $(@($toDelete).Count) line(s)." -ForegroundColor Yellow
}

. "$PSScriptRoot\..\copilot\shell-defaults.ps1"

# mac-style open: files->default app, .->Explorer, urls->browser
function open { if ($args.Count) { Start-Process @args } else { Start-Process . } }

# -------------------------------
#  starship prompt ($env:STARSHIP_CONFIG is set by link.ps1, no hardcoded path)
# -------------------------------
$machineNamePath = Join-Path $HOME '.machine-name'
if (-not [Environment]::GetEnvironmentVariable('MACHINE_NAME'))
{
    $machineName = if (Test-Path $machineNamePath)
    {
        (Get-Content $machineNamePath -Raw).Trim()
    }
    else
    {
        ''
    }
    $env:MACHINE_NAME = if ($machineName) { $machineName } else { [Environment]::MachineName }
}
if (Get-Command starship -ErrorAction SilentlyContinue) { Invoke-Expression (&starship init powershell) }
