"""
PodVault — the program. Opens the window (PyQt6).

Run:  gui.bat / ./gui.sh      (or: python Main.py)

Every tool is also a standalone script in src/ and works from the command
line. On the first run the window asks for the basic settings; they are
saved to settings.json and can be changed later on the Settings screen.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

if __name__ == "__main__":
    from gui.window import run
    sys.exit(run())
