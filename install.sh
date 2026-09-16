#!/bin/sh
# Install packages; link.py configures the shell separately.
set -eu
script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
for python_bin in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$python_bin" >/dev/null 2>&1 &&
    "$python_bin" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    exec "$python_bin" "$script_dir/install.py" "$@"
  fi
done
echo 'Python 3.11+ is required. Install it through an approved package source, then rerun install.sh.' >&2
exit 1
