# dotfiles-shared

Shared terminal, editor, Git, and Copilot configuration for Windows, Red Hat Linux,
and macOS. Clone this repository over HTTPS without signing into GitHub. It contains
no Git identity, credential helpers, personal services, history sync, or private
repository dependency.

Use `work` on employer machines. Use the employer's approved account when signing
into Copilot or another service; cloning these files needs no account.

## Set up Red Hat Linux

Install Git and Python 3.11 or newer through your approved package source. Clone
this repository, then inspect the package plan:

```sh
git clone https://github.com/kennedymindeman/dotfiles-shared.git ~/dotfiles-shared
sh ~/dotfiles-shared/install.sh --profile work --dry-run
sh ~/dotfiles-shared/install.sh --profile work
sh ~/dotfiles-shared/link.sh --profile work --dry-run
sh ~/dotfiles-shared/link.sh --profile work
```

The package installer uses enabled DNF repositories. It stops before installing
anything if a required package is unavailable. Give that report to IT, or install
the missing tools through an approved source and rerun. It does not enable EPEL,
CRB, or Copr, add repositories, or execute remote install scripts. RHEL releases
and enabled repositories differ, so a full setup can require additional approved
packages.

Start a new `zsh` shell after linking. The installer does not change your login shell.
Bash receives the Copilot launcher; the full interactive shell configuration is zsh.

## Set up Windows

Install Git, Python 3.11+, and PowerShell 7 with winget, then open a new PowerShell 7
window so the new executables are on PATH:

```powershell
winget install --id Git.Git --exact --source winget
winget install --id Python.Python.3.14 --exact --source winget
winget install --id Microsoft.PowerShell --exact --source winget
```

Clone and review the setup plan:

```powershell
git clone https://github.com/kennedymindeman/dotfiles-shared.git "$HOME\dotfiles-shared"
& "$HOME\dotfiles-shared\install.ps1" -Profile work -DryRun
& "$HOME\dotfiles-shared\install.ps1" -Profile work
& "$HOME\dotfiles-shared\link.ps1" -Profile work -DryRun
& "$HOME\dotfiles-shared\link.ps1" -Profile work
```

The installer uses exact winget package IDs and installs PSFzf for the current user.
The linker preserves existing PowerShell profile content and uses copies where
Windows symlinks would need extra privileges. It does not change execution policy
or PowerShell Gallery trust. Native Windows has no tmux; use tmux on the Linux host.

## Set up macOS

With Homebrew and Python 3.11+ already installed, run the same `install.sh` and
`link.sh` commands. Package installation uses Homebrew and does not bootstrap it.

## Configure Git identity

Existing Git identity and credential settings are preserved. A fresh installation
requires you to choose an identity before committing:

```sh
git config --global user.name 'Your Name'
git config --global user.email 'your.work@example.com'
```

Use a repository-local identity or your employer's include rules when appropriate.

If the host sets `COPILOT_HOME`, the linker installs Copilot instructions and
settings there. The shell launcher pins the inherited location and rejects later
overrides.

## Enable Copilot for work

The work launcher warns when a local mandatory sandbox policy is absent. If a
policy file is present, the launcher refuses to start unless it passes
validation. Ask IT to review
[the policy example](copilot/managed-settings.example.json) and install it using
[GitHub's managed settings deployment instructions](https://docs.github.com/en/copilot/how-tos/administer-copilot/manage-for-enterprise/use-managed-settings/deploy-managed-settings).
The file belongs at:

| Platform | Managed policy file |
| --- | --- |
| Red Hat Linux | `/etc/github-copilot/managed-settings.json` |
| Windows | `%ProgramFiles%\GitHubCopilot\managed-settings.json` |
| macOS | `/Library/Application Support/GitHubCopilot/managed-settings.json` |

The policy requires `sandbox.enabled: true`, `sandbox.failIfUnavailable: true`,
and `sandbox.allowBypass: false`. These settings make Copilot block model and tool
execution when it cannot enforce sandboxing. See [GitHub's sandbox policy reference](https://docs.github.com/en/copilot/reference/enterprise-administrators/enterprise-managed-settings#sandbox).
The linker cannot install an administrator-owned policy.

On Linux, the launcher also checks bubblewrap 0.5+, slirp4netns, compatible
`unshare`/`nsenter`, iptables tools, and `/dev/net/tun`. An older RHEL release can
lack the required util-linux capabilities. On Windows it checks the native
sandbox capabilities. If a host fails these checks, the launcher warns and lets
Copilot apply its configured sandbox behavior. Use a supported, employer-approved
host or environment before working with sensitive data.

The shell functions are convenience checks. Copilot and its managed policy enforce
the security boundary. A missing local policy file does not prove that managed
settings delivered through registry, MDM, or the server are absent. On Windows, IT
must protect a local policy file with the appropriate ACLs; the Python check does not
validate ACLs. Package tests and mocked launcher tests do not prove OS containment.
Before using work data, check `/sandbox status` and `/sandbox policy` in the installed
CLI and validate the effective policy on that machine with IT.

Copilot uses manual approvals, disables its built-in MCP servers in the launcher,
and restricts sensitive paths. Outbound internet remains allowed. This repository
does not authorize uploading employer data to any AI service.

## Update or change profiles

The profile resolves from `--profile`, then `DOTFILES_ENV`, then `~/.dotfiles-env`.
There is no default. Invalid or absent choices stop before configuration writes.
`--dry-run` validates conflicts and prints planned changes without writing files.

Rerun the linker after updating this checkout. It records installed files and
managed blocks in `~/.config/dotfiles/state.json`. Before replacing a file it saves
its previous contents under `~/.config/dotfiles/backups/`. Unix config symlinks load
updates from this checkout immediately; Windows copies refresh when relinked.

Prefer a fresh OS account for a work setup. To change a previously installed home
profile to work, use this public repository with `--profile work`. The linker removes
unchanged files it owns and replaces its managed blocks. Edited files, old untracked
installations, external MCP registrations, and known private startup or service references
require review before the switch. Existing agent instructions outside managed blocks
also require review. An old Windows `WEZTERM_CONFIG_FILE` override must be reviewed
and removed from the user environment before switching. Backups can contain personal
settings, so migrating an existing personal account does not sanitize that account.
Restart shells, tmux servers, and agent sessions after migration to clear loaded code.

## Add a private overlay

Keep common configuration here, personal additions in a separate private home
repository, and work additions in an employer-owned private repository. Clone only
the overlay needed on that machine. The public setup never fetches an overlay.

An overlay must contain `profiles/work.json` for `--profile work`, or
`profiles/home.json` for `--profile home`. The filename declares the supported
profile; a home-only overlay cannot be selected for work. There is no fallback to
the other profile. Start with [the synthetic work example](examples/work-overlay/README.md).

After cloning and reviewing an employer-approved overlay at `~/dotfiles-work`:

```sh
sh ~/dotfiles-shared/link.sh --profile work --overlay ~/dotfiles-work --dry-run
sh ~/dotfiles-shared/link.sh --profile work --overlay ~/dotfiles-work
```

```powershell
& "$HOME\dotfiles-shared\link.ps1" -Profile work -Overlay "$HOME\dotfiles-work" -DryRun
& "$HOME\dotfiles-shared\link.ps1" -Profile work -Overlay "$HOME\dotfiles-work"
```

Use `--profile home` with the separate home overlay on personal machines.
Always pass `--overlay` when retaining it: omission removes the previous overlay's
unchanged owned files and replaces its managed blocks with shared configuration.
Selecting another overlay performs the same cleanup before installing its additions.
Existing references to the previous overlay outside managed blocks require review.
Backups remain on the machine; restart shells, tmux, and agents after a change.

The manifest maps destination paths relative to the user's home to source paths
relative to the overlay. Groups are `files` for every platform, `unix` or `windows`,
and optionally `darwin` or `linux`. Shared files, generated blocks, policy helpers,
and ownership state cannot be replaced by manifest entries. Files use the same
ownership checks and backups as shared configuration. The overlay is trusted code;
review it through the employer's normal process. It does not waive work Copilot's
mandatory policy checks.

These optional files load after the shared configuration; `<profile>` is exactly
`work` or `home`. Omit anything the environment does not need.

| Overlay file | Behavior |
| --- | --- |
| `gitconfig.<profile>` | Git include after shared preferences; identity or environment-specific includes |
| `agents/<profile>.md` | Appended to generated agent instructions; shared work rules remain present |
| `zshrc.<profile>`, `bashrc.<profile>` | Sourced after the corresponding shared shell setup on Unix |
| `tmux.<profile>.conf` | Sourced after shared tmux on Unix |
| `powershell/profile.<profile>.ps1` | Sourced after shared PowerShell setup on Windows |
| `copilot/subagents.json` | Merged subagent fields; unchanged owned fields removed when no longer configured |

Shell blocks set `DOTFILES_ENV` and `DOTFILES_PRIVATE_DIR` to the selected profile
and overlay; they clear `DOTFILES_PRIVATE_DIR` when no overlay is selected. Keep
credentials in machine-local storage or an approved credential manager. Private
repositories are suitable for environment-specific instructions and identity,
not secrets.

For tmux's shared `C-b ?` menu, an overlay can set
`@dotfiles-window-switch-command` (default `choose-tree -Zw`) and
`@dotfiles-waiting-command` (default empty). The latter enables the optional
`waiting agent` action on `g`. The six common actions stay defined in the shared
menu. Loading shared tmux again restores both defaults; the selected overlay loads
after it. Separate key bindings can use the same commands.

## Contribute

Read [AGENTS.md](AGENTS.md) before editing or publishing. Changes, fixtures, commit
metadata, issues, and pull requests must be suitable for public access.

## Validate changes

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
sh tests/test_copilot_shell_defaults.sh
```

On Windows, also run `./tests/test_copilot_shell_defaults.ps1` in PowerShell.
CI runs the Python tests on Linux, macOS, and Windows, plus the native shell tests.
