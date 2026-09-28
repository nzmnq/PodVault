#!/bin/sh
# Opens the Music Utility window (PyQt6) on macOS / Linux (gui.bat on Windows).
exec "$(dirname "$0")/run.sh" --gui "$@"
