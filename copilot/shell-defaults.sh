_copilot_expected_home=${COPILOT_HOME:-}

copilot() {
  if [ "${DOTFILES_ENV:-}" = work ] ||
    [ "$(cat "$HOME/.dotfiles-env" 2>/dev/null)" = work ]; then
    copilot_python=
    for copilot_candidate in python3.14 python3.13 python3.12 python3.11 python3; do
      if command -v "$copilot_candidate" >/dev/null 2>&1 &&
        "$copilot_candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        copilot_python=$copilot_candidate
        break
      fi
    done
    if [ -z "$copilot_python" ]; then
      echo 'copilot: Python 3.11+ is required to verify the work sandbox policy' >&2
      return 2
    fi
    "$copilot_python" "$HOME/.config/dotfiles/check-copilot-policy.py" || return 2
  fi
  case "$(uname -s)" in
    Darwin)
      if ! command -v sandbox-exec >/dev/null 2>&1; then
        echo "copilot: warning: this macOS host cannot enforce the Copilot sandbox" >&2
      fi
      ;;
    Linux)
      copilot_bwrap_version=
      if copilot_bwrap_output=$(bwrap --version 2>/dev/null); then
        copilot_bwrap_version=$(
          printf '%s\n' "$copilot_bwrap_output" |
            sed -n 's/.* \([0-9][0-9.]*\)$/\1/p'
        )
      fi
      copilot_bwrap_major=${copilot_bwrap_version%%.*}
      copilot_bwrap_rest=${copilot_bwrap_version#*.}
      copilot_bwrap_minor=${copilot_bwrap_rest%%.*}
      if [ -z "$copilot_bwrap_version" ] ||
        { [ "$copilot_bwrap_major" -lt 1 ] && [ "$copilot_bwrap_minor" -lt 5 ]; }; then
        echo "copilot: warning: this Linux host cannot enforce the Copilot sandbox without bwrap 0.5.0 or newer" >&2
      fi
      for copilot_sandbox_command in \
        slirp4netns \
        unshare \
        nsenter \
        iptables \
        ip6tables \
        iptables-restore \
        ip6tables-restore
      do
        if ! command -v "$copilot_sandbox_command" >/dev/null 2>&1; then
          echo "copilot: warning: this Linux host cannot enforce the Copilot sandbox without $copilot_sandbox_command" >&2
        fi
      done
      if command -v slirp4netns >/dev/null 2>&1 &&
        ! slirp4netns --version >/dev/null 2>&1; then
        echo "copilot: warning: this Linux host cannot enforce the Copilot sandbox because slirp4netns is unusable" >&2
      fi
      if command -v unshare >/dev/null 2>&1; then
        if ! copilot_unshare_help=$(unshare --help 2>/dev/null) ||
          ! printf '%s\n' "$copilot_unshare_help" | grep -q -- '--map-current-user' ||
          ! printf '%s\n' "$copilot_unshare_help" | grep -q -- '--keep-caps'; then
          echo "copilot: warning: this Linux host cannot enforce the Copilot sandbox without compatible util-linux 2.35+ tools" >&2
        fi
      fi
      copilot_tun_device=${COPILOT_SANDBOX_TUN_DEVICE:-/dev/net/tun}
      if [ ! -r "$copilot_tun_device" ] || [ ! -w "$copilot_tun_device" ]; then
        echo "copilot: warning: this Linux host cannot enforce the Copilot sandbox without read/write access to /dev/net/tun" >&2
      fi
      ;;
    *)
      echo "copilot: warning: this host cannot enforce the Copilot sandbox" >&2
      ;;
  esac

  if [ "${COPILOT_ALLOW_ALL:-}" = true ] ||
    [ "${COPILOT_ASSISTED_APPROVAL:-}" = true ]; then
    echo "copilot: refusing launch because a permission-escalation environment variable is enabled" >&2
    return 2
  fi
  if [ -n "${COPILOT_PROVIDER_BASE_URL:-}" ]; then
    echo "copilot: refusing launch because COPILOT_PROVIDER_BASE_URL can redirect session data to a custom model provider" >&2
    return 2
  fi
  for copilot_otel_name in \
    COPILOT_OTEL_ENABLED \
    COPILOT_OTEL_FILE_EXPORTER_PATH \
    OTEL_EXPORTER_OTLP_ENDPOINT \
    OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT
  do
    eval "copilot_otel_value=\${$copilot_otel_name:-}"
    if [ -n "$copilot_otel_value" ]; then
      echo "copilot: refusing launch because $copilot_otel_name can export Copilot session telemetry" >&2
      return 2
    fi
  done
  if [ "${COPILOT_HOME:-}" != "$_copilot_expected_home" ]; then
    echo "copilot: refusing launch because COPILOT_HOME changed after the hardened configuration loaded" >&2
    return 2
  fi

  copilot_home=$(CDPATH= cd -- "$HOME" && pwd -P) || return 2
  copilot_config_home=${_copilot_expected_home:-"$copilot_home/.copilot"}
  if [ -d "$copilot_config_home" ]; then
    copilot_config_home=$(CDPATH= cd -- "$copilot_config_home" && pwd -P) ||
      return 2
  fi
  copilot_cwd=$(pwd -P) || return 2
  if [ "$copilot_cwd" = "$copilot_home" ]; then
    echo "copilot: refusing launch because the workspace is the home directory" >&2
    return 2
  fi
  if [ "$copilot_cwd" = / ]; then
    echo "copilot: refusing launch because the workspace contains the home directory" >&2
    return 2
  fi
  case "$copilot_home/" in
    "$copilot_cwd/"*)
      echo "copilot: refusing launch because the workspace contains the home directory" >&2
      return 2
      ;;
  esac

  for copilot_sensitive in \
    "$copilot_home/.aws" \
    "$copilot_home/.claude" \
    "$copilot_home/.codex" \
    "$copilot_home/.config/alerts" \
    "$copilot_home/.config/Bitwarden CLI" \
    "$copilot_home/.config/gh" \
    "$copilot_home/.copilot" \
    "$copilot_config_home" \
    "$copilot_home/.gnupg" \
    "$copilot_home/.ssh" \
    "$copilot_home/Library/Application Support/Bitwarden" \
    "$copilot_home/Library/Application Support/Bitwarden CLI"
  do
    if [ -d "$copilot_sensitive" ]; then
      copilot_sensitive=$(CDPATH= cd -- "$copilot_sensitive" && pwd -P) || {
        echo "copilot: refusing launch because sensitive path '$copilot_sensitive' cannot be resolved" >&2
        return 2
      }
    fi
    case "$copilot_cwd/" in
      "$copilot_sensitive/"*)
        echo "copilot: refusing launch because the workspace overlaps '$copilot_sensitive'" >&2
        return 2
        ;;
    esac
    case "$copilot_sensitive/" in
      "$copilot_cwd/"*)
        echo "copilot: refusing launch because the workspace contains '$copilot_sensitive'" >&2
        return 2
        ;;
    esac
  done

  copilot_position_1=
  copilot_position_2=
  copilot_position_3=
  copilot_skip_next=false
  copilot_options_ended=false
  for copilot_arg in "$@"; do
    case "$copilot_arg" in
      -C|-C?*|-r|-r?*|-w|-w?*|\
      --allow-all|--allow-all-mcp-server-instructions|--allow-all-paths|--allow-all-tools|--allow-all-urls|--allow-tool|--allow-tool=*|--allow-url|--allow-url=*|--assisted-approval|\
      --add-dir|--add-dir=*|--add-github-mcp-tool|--add-github-mcp-tool=*|--add-github-mcp-toolset|--add-github-mcp-toolset=*|--additional-mcp-config|--additional-mcp-config=*|\
      --config-dir|--config-dir=*|--connect|--connect=*|--continue|--enable-all-github-mcp-tools|--enable-mcp-server|--enable-mcp-server=*|--extension-sdk-path|--extension-sdk-path=*|\
      --no-experimental|--no-sandbox|--plugin-dir|--plugin-dir=*|--remote|--remote-export|--resume|--resume=*|--session-id|--session-id=*|--share|--share=*|--share-gist|--worktree|--worktree=*|--yolo)
        echo "copilot: refusing launch because '$copilot_arg' weakens the hardened defaults" >&2
        return 2
        ;;
    esac

    if [ "$copilot_skip_next" = true ]; then
      copilot_skip_next=false
      continue
    fi
    if [ "$copilot_options_ended" = false ] && [ "$copilot_arg" = -- ]; then
      copilot_options_ended=true
      continue
    fi
    if [ "$copilot_options_ended" = false ]; then
      case "$copilot_arg" in
        -i|-n|-p|\
        --agent|--attachment|--available-tools|--context|--deny-tool|--deny-url|--disable-mcp-server|--effort|--excluded-tools|\
        --interactive|--log-dir|--log-level|--max-ai-credits|--max-autopilot-continues|--mode|--model|--name|--output-format|\
        --prompt|--reasoning-effort|--secret-env-vars|--stream|--usage-output-file)
          copilot_skip_next=true
          continue
          ;;
        -*)
          continue
          ;;
      esac
    fi
    if [ -z "$copilot_position_1" ]; then
      copilot_position_1=$copilot_arg
    elif [ -z "$copilot_position_2" ]; then
      copilot_position_2=$copilot_arg
    elif [ -z "$copilot_position_3" ]; then
      copilot_position_3=$copilot_arg
    fi
  done

  case "$copilot_position_1:$copilot_position_2" in
    mcp:add|mcp:remove|skill:add|skill:remove|\
    plugin:add|plugin:install|plugin:remove|plugin:uninstall|plugin:update|\
    plugins:add|plugins:disable|plugins:enable|plugins:install|plugins:remove|plugins:rm|plugins:update)
      echo "copilot: refusing launch because '$copilot_position_1 $copilot_position_2' changes Copilot extension configuration" >&2
      return 2
      ;;
  esac
  case "$copilot_position_1:$copilot_position_2:$copilot_position_3" in
    plugin:marketplace:add|plugin:marketplace:refresh|plugin:marketplace:remove|plugin:marketplace:rm|plugin:marketplace:update|\
    plugins:marketplace:add|plugins:marketplace:refresh|plugins:marketplace:remove|plugins:marketplace:rm|plugins:marketplace:update|\
    plugins:marketplaces:add|plugins:marketplaces:refresh|plugins:marketplaces:remove|plugins:marketplaces:rm|plugins:marketplaces:update)
      echo "copilot: refusing launch because '$copilot_position_1 $copilot_position_2 $copilot_position_3' changes Copilot extension configuration" >&2
      return 2
      ;;
  esac

  command copilot \
    --experimental \
    --disable-builtin-mcps \
    --no-remote \
    --no-remote-export \
    --secret-env-vars='ANTHROPIC_API_KEY,AWS_ACCESS_KEY_ID,AWS_SECRET_ACCESS_KEY,AWS_SESSION_TOKEN,AZURE_CLIENT_SECRET,BW_SESSION,COPILOT_GITHUB_TOKEN,GH_TOKEN,GITHUB_TOKEN,NPM_TOKEN,OPENAI_API_KEY,PYPI_TOKEN,TELEGRAM_TOKEN,TS_OAUTH_CLIENT_SECRET' \
    --deny-tool='shell(bw)' \
    --deny-tool='shell(rbw)' \
    --deny-tool='shell(git reset --hard)' \
    --deny-tool='shell(git clean)' \
    --deny-tool='shell(git branch -D)' \
    --deny-tool='shell(terraform apply)' \
    --deny-tool='shell(terraform destroy)' \
    --deny-tool='shell(npm publish)' \
    --deny-tool='shell(gh release)' \
    --deny-tool='shell(gh repo delete)' \
    --deny-tool='shell(gh auth token)' \
    --deny-tool='shell(gh auth logout)' \
    "$@"
}

alias cc=copilot
