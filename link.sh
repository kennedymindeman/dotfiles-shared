#!/bin/sh
set -eu
DOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for candidate in python3.14 python3.13 python3.12 python3.11 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        exec "$candidate" "$DOT/link.py" "$@"
    fi
done
echo 'link: Python 3.11+ is required; install it before linking' >&2
exit 1
