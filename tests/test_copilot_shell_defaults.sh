#!/bin/sh
set -eu

export DOTFILES_ENV=home
unset COPILOT_HOME
repo=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
shell=$(command -v sh)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/test-copilot-shell-defaults.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

mkdir -p \
  "$tmp/actual-aws/project" \
  "$tmp/home/projects/demo" \
  "$tmp/home/.gnupg/project" \
  "$tmp/home/.ssh/project" \
  "$tmp/home/.config/Bitwarden CLI/project" \
  "$tmp/managed-copilot-actual/project" \
  "$tmp/managed-copilot/project" \
  "$tmp/bin"
ln -s "$tmp/actual-aws" "$tmp/home/.aws" 2>/dev/null || true
touch "$tmp/tun"
export COPILOT_SANDBOX_TUN_DEVICE="$tmp/tun"
cat > "$tmp/bin/copilot" <<'EOF'
#!/bin/sh
printf '%s\n' "$@"
EOF
chmod +x "$tmp/bin/copilot"
cat > "$tmp/bin/bwrap" <<'EOF'
#!/bin/sh
echo "bubblewrap 0.5.0"
EOF
chmod +x "$tmp/bin/bwrap"
cat > "$tmp/bin/uname" <<'EOF'
#!/bin/sh
echo "Linux"
EOF
chmod +x "$tmp/bin/uname"
for command in slirp4netns unshare nsenter iptables ip6tables iptables-restore ip6tables-restore
do
  cat >"$tmp/bin/$command" <<'EOF'
#!/bin/sh
echo "--map-current-user --keep-caps"
EOF
  chmod +x "$tmp/bin/$command"
done

output=$(
  cd "$tmp/home/projects/demo"
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
)
printf '%s\n' "$output" | grep -Fx -- '--experimental'
printf '%s\n' "$output" | grep -Fx -- '--disable-builtin-mcps'
printf '%s\n' "$output" | grep -Fx -- '--no-remote'
printf '%s\n' "$output" | grep -Fx -- '--no-remote-export'
printf '%s\n' "$output" | grep -Fx -- '--deny-tool=shell(git clean)'
if printf '%s\n' "$output" | grep -q -- '--assisted-approval'; then
  echo "FAIL: assisted approval remains enabled" >&2
  exit 1
fi
if printf '%s\n' "$output" | grep -Fqx -- '--deny-tool=shell(git push)'; then
  echo "FAIL: git push remains permanently denied" >&2
  exit 1
fi

output=$(
  cd "$tmp/home/projects/demo"
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" COPILOT_HOME="$tmp/managed-copilot" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
)
printf '%s\n' "$output" | grep -Fx -- '--experimental'

if (
  cd "$tmp/home/projects/demo"
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" COPILOT_HOME="$tmp/managed-copilot" sh -c \
    '. "$1/copilot/shell-defaults.sh"; COPILOT_HOME="$2"; export COPILOT_HOME; copilot --version' \
    sh "$repo" "$tmp/other-copilot"
) >"$tmp/environment-output" 2>&1; then
  echo "FAIL: COPILOT_HOME override after wrapper load was allowed" >&2
  exit 1
fi
grep -q 'COPILOT_HOME changed' "$tmp/environment-output"

if (
  cd "$tmp/managed-copilot/project"
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" COPILOT_HOME="$tmp/managed-copilot" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/sensitive-output" 2>&1; then
  echo "FAIL: managed Copilot home workspace was allowed" >&2
  exit 1
fi
grep -q 'workspace overlaps' "$tmp/sensitive-output"

if [ -L "$tmp/home/.aws" ]; then
  if (
    cd "$tmp/actual-aws/project"
    HOME="$tmp/home" PATH="$tmp/bin:$PATH" sh -c \
      '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
  ) >"$tmp/sensitive-output" 2>&1; then
    echo "FAIL: symlinked sensitive workspace was allowed" >&2
    exit 1
  fi
  grep -q 'workspace overlaps' "$tmp/sensitive-output"
fi

if (
  cd "$tmp/managed-copilot-actual/project"
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" COPILOT_HOME="$tmp/managed-copilot-actual/../managed-copilot-actual" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/sensitive-output" 2>&1; then
  echo "FAIL: managed Copilot home path alias was allowed" >&2
  exit 1
fi
grep -q 'workspace overlaps' "$tmp/sensitive-output"

if (
  cd "$tmp/home"
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/home-output" 2>&1; then
  echo "FAIL: home-directory workspace was allowed" >&2
  exit 1
fi
grep -q 'workspace is the home directory' "$tmp/home-output"

if (
  cd "$tmp/home/projects/demo"
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --allow-all' sh "$repo"
) >"$tmp/flag-output" 2>&1; then
  echo "FAIL: permission escalation flag was allowed" >&2
  exit 1
fi
grep -q 'weakens the hardened defaults' "$tmp/flag-output"

for flag in \
  '--no-sandbox' \
  '-w' \
  '-wbranch' \
  '--allow-tool=shell' \
  '--additional-mcp-config=@mcp.json' \
  '--plugin-dir=plugin' \
  '--enable-all-github-mcp-tools' \
  '--add-github-mcp-tool=*' \
  '--add-github-mcp-toolset=all' \
  '--allow-all-mcp-server-instructions' \
  '--remote' \
  '--remote-export' \
  '-C' \
  '-C/tmp/other' \
  '-rsession' \
  '--resume=session' \
  '--continue' \
  '--session-id=session' \
  '--connect=session' \
  '--worktree=branch'
do
  if (
    cd "$tmp/home/projects/demo"
    HOME="$tmp/home" PATH="$tmp/bin:$PATH" sh -c \
      '. "$1/copilot/shell-defaults.sh"; copilot "$2"' sh "$repo" "$flag"
  ) >"$tmp/flag-output" 2>&1; then
    echo "FAIL: escalation flag was allowed: $flag" >&2
    exit 1
  fi
  grep -q 'weakens the hardened defaults' "$tmp/flag-output"
done

for workspace in "$tmp/home/.config/Bitwarden CLI" "$tmp/home/.config/Bitwarden CLI/project"
do
  if (
    cd "$workspace"
    HOME="$tmp/home" PATH="$tmp/bin:$PATH" sh -c \
      '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
  ) >"$tmp/sensitive-output" 2>&1; then
    echo "FAIL: Bitwarden workspace was allowed: $workspace" >&2
    exit 1
  fi
  grep -q 'workspace overlaps' "$tmp/sensitive-output"
done

if (
  cd "$tmp/home/.ssh/project"
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/sensitive-output" 2>&1; then
  echo "FAIL: sensitive-directory workspace was allowed" >&2
  exit 1
fi
grep -q 'workspace overlaps' "$tmp/sensitive-output"

if (
  cd "$tmp/home/.gnupg/project"
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/sensitive-output" 2>&1; then
  echo "FAIL: GnuPG workspace was allowed" >&2
  exit 1
fi
grep -q 'workspace overlaps' "$tmp/sensitive-output"

if (
  cd "$tmp/home/projects/demo"
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" COPILOT_ALLOW_ALL=true sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/environment-output" 2>&1; then
  echo "FAIL: permission escalation environment variable was allowed" >&2
  exit 1
fi
grep -q 'permission-escalation environment variable' "$tmp/environment-output"

if (
  cd "$tmp/home/projects/demo"
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" COPILOT_PROVIDER_BASE_URL=https://example.invalid sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/environment-output" 2>&1; then
  echo "FAIL: custom provider routing environment variable was allowed" >&2
  exit 1
fi
grep -q 'custom model provider' "$tmp/environment-output"

for command in \
  'mcp add test cmd' \
  'mcp remove test' \
  'skill add owner/repo' \
  'skill remove test' \
  'plugin install owner/repo' \
  'plugin update test' \
  'plugin marketplace add test owner/repo' \
  'plugin marketplace rm test' \
  'plugins enable test' \
  'plugins install test' \
  'plugins marketplaces refresh test'
do
  if (
    cd "$tmp/home/projects/demo"
    HOME="$tmp/home" PATH="$tmp/bin:$PATH" sh -c \
      '. "$1/copilot/shell-defaults.sh"; shift; copilot "$@"' sh "$repo" $command
  ) >"$tmp/management-output" 2>&1; then
    echo "FAIL: mutating management command was allowed: $command" >&2
    exit 1
  fi
  grep -q 'changes Copilot extension configuration' "$tmp/management-output"
done

output=$(
  cd "$tmp/home/projects/demo"
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --name plugin -i update' sh "$repo"
)
printf '%s\n' "$output" | grep -Fx -- 'plugin'
printf '%s\n' "$output" | grep -Fx -- 'update'

if (
  cd "$tmp/home/projects/demo"
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" OTEL_EXPORTER_OTLP_ENDPOINT=https://example.invalid sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/environment-output" 2>&1; then
  echo "FAIL: OTel session export environment variable was allowed" >&2
  exit 1
fi
grep -q 'session telemetry' "$tmp/environment-output"

mkdir -p "$tmp/missing-bin"
cat >"$tmp/missing-bin/slirp4netns" <<'EOF'
#!/bin/sh
exit 1
EOF
chmod +x "$tmp/missing-bin/slirp4netns"
if ! (
  cd "$tmp/home/projects/demo"
  HOME="$tmp/home" PATH="$tmp/missing-bin:$tmp/bin:$PATH" "$shell" -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/prerequisite-output" 2>&1; then
  echo "FAIL: unavailable Linux sandbox blocked launch" >&2
  exit 1
fi
grep -q 'slirp4netns is unusable' "$tmp/prerequisite-output" || {
  cat "$tmp/prerequisite-output" >&2
  exit 1
}
grep -Fxq -- '--experimental' "$tmp/prerequisite-output"

if (
  cd /
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/root-output" 2>&1; then
  echo "FAIL: filesystem-root workspace was allowed" >&2
  exit 1
fi
grep -q 'workspace contains the home directory' "$tmp/root-output"

mkdir -p "$tmp/old-bin"
cat > "$tmp/old-bin/bwrap" <<'EOF'
#!/bin/sh
echo "bubblewrap 0.4.0"
EOF
chmod +x "$tmp/old-bin/bwrap"
if ! (
  cd "$tmp/home/projects/demo"
  HOME="$tmp/home" PATH="$tmp/old-bin:$tmp/bin:$PATH" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/sandbox-output" 2>&1; then
  echo "FAIL: unsupported bubblewrap version blocked launch" >&2
  exit 1
fi
grep -q 'bwrap 0.5.0 or newer is missing, failed, or reported an unusable version' "$tmp/sandbox-output"
grep -Fxq -- '--experimental' "$tmp/sandbox-output"

cat > "$tmp/old-bin/bwrap" <<'EOF'
#!/bin/sh
echo "unknown"
EOF
if ! (
  cd "$tmp/home/projects/demo"
  HOME="$tmp/home" PATH="$tmp/old-bin:$tmp/bin:$PATH" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/sandbox-output" 2>&1; then
  echo "FAIL: unparsable bubblewrap version blocked launch" >&2
  exit 1
fi
grep -q 'bwrap 0.5.0 or newer is missing, failed, or reported an unusable version' "$tmp/sandbox-output"
grep -Fxq -- '--experimental' "$tmp/sandbox-output"

cat > "$tmp/old-bin/bwrap" <<'EOF'
#!/bin/sh
echo "bubblewrap 0.5.0"
exit 1
EOF
if ! (
  cd "$tmp/home/projects/demo"
  HOME="$tmp/home" PATH="$tmp/old-bin:$tmp/bin:$PATH" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/sandbox-output" 2>&1; then
  echo "FAIL: failed bubblewrap probe blocked launch" >&2
  exit 1
fi
grep -q 'bwrap 0.5.0 or newer is missing, failed, or reported an unusable version' "$tmp/sandbox-output"
grep -Fxq -- '--experimental' "$tmp/sandbox-output"

cat > "$tmp/old-bin/bwrap" <<'EOF'
#!/bin/sh
echo "bubblewrap 0.5.0"
EOF
cat > "$tmp/old-bin/unshare" <<'EOF'
#!/bin/sh
echo "--map-current-user --keep-caps"
exit 1
EOF
chmod +x "$tmp/old-bin/unshare"
if ! (
  cd "$tmp/home/projects/demo"
  HOME="$tmp/home" PATH="$tmp/old-bin:$tmp/bin:$PATH" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/sandbox-output" 2>&1; then
  echo "FAIL: failed unshare probe blocked launch" >&2
  exit 1
fi
grep -q 'without compatible util-linux' "$tmp/sandbox-output"
grep -Fxq -- '--experimental' "$tmp/sandbox-output"

cat > "$tmp/bin/python3.14" <<'EOF'
#!/bin/sh
[ "$1" = -c ] && exit 0
echo 'policy verification failed' >&2
exit 1
EOF
chmod +x "$tmp/bin/python3.14"
if (
  cd "$tmp/home/projects/demo"
  HOME="$tmp/home" DOTFILES_ENV=work PATH="$tmp/bin:$PATH" sh -c \
    '. "$1/copilot/shell-defaults.sh"; copilot --version' sh "$repo"
) >"$tmp/policy-output" 2>&1; then
  echo "FAIL: work launch proceeded without mandatory policy" >&2
  exit 1
fi
grep -q 'policy verification failed' "$tmp/policy-output"
if grep -q -- '--experimental' "$tmp/policy-output"; then
  echo "FAIL: Copilot launched after policy verification failed" >&2
  exit 1
fi

echo "ok: Copilot shell defaults enforce the hardened launch policy"
