#!/bin/sh
# heliograph launcher for macOS / Linux. See run.py for details.
cd "$(dirname "$0")" || exit 1
if [ -n "$VIRTUAL_ENV" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PY="$VIRTUAL_ENV/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "[heliograph] ERROR: Python 3 not found. Install it from https://www.python.org/downloads/" >&2
    exit 1
fi
exec "$PY" run.py "$@"
