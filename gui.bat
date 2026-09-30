@echo off
rem Opens the PodVault window (PyQt6), without a console.
rem The same interpreter order as run.bat; pythonw keeps the console away.
cd /d "%~dp0"

if exist "%~dp0.python\pythonw.exe" (
    start "" "%~dp0.python\pythonw.exe" "%~dp0Main.py" --gui
) else if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0Main.py" --gui
) else (
    where pyw >nul 2>nul && (start "" pyw -3 "%~dp0Main.py" --gui) || (start "" pythonw "%~dp0Main.py" --gui)
)
