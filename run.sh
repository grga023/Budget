#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# No sudo / no system packages needed: try a venv, but fall back to a plain
# user-level install if the venv module is unavailable (e.g. python3-venv missing).
PY=python3

if "$PY" -m venv --help >/dev/null 2>&1 && "$PY" -c "import ensurepip" >/dev/null 2>&1; then
    if [ ! -d ".venv" ]; then
        echo "Creating virtual environment..."
        "$PY" -m venv .venv
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
    PY=python
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet -r requirements.txt
else
    echo "venv unavailable; using a user-level install (no sudo required)."
    if ! "$PY" -c "import flask" >/dev/null 2>&1; then
        "$PY" -m pip install --user --quiet -r requirements.txt
    fi
fi

echo "Starting Budget app on http://127.0.0.1:5000"
"$PY" app.py

