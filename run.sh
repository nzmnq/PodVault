#!/bin/sh
# Launches Music Utility on macOS / Linux (run.bat on Windows).
# Works from any directory.
#
# Interpreter, first match wins:
#   .python/bin/python3   portable Python next to the project (git-ignored)
#   .venv/bin/python      a virtual environment
#   python3 / python      whatever is installed system-wide
cd "$(dirname "$0")" || exit 1
export PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1

for py in .python/bin/python3 .venv/bin/python; do
    [ -x "$py" ] && exec "$py" Main.py "$@"
done
exec "$(command -v python3 || command -v python)" Main.py "$@"
