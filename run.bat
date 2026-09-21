@echo off
rem Launches Music Utility. The console is switched to UTF-8, otherwise
rem Cyrillic in titles turns into garbage.
rem
rem Interpreter, first match wins:
rem   .python\python.exe       portable Python next to the project (git-ignored)
rem   .venv\Scripts\python.exe a virtual environment
rem   py -3 / python           whatever is installed system-wide
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"

if exist "%~dp0.python\python.exe" (
    "%~dp0.python\python.exe" "%~dp0Main.py" %*
) else if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0Main.py" %*
) else (
    where py >nul 2>nul && (py -3 "%~dp0Main.py" %*) || (python "%~dp0Main.py" %*)
)
