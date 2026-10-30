@echo off
setlocal

rem Resolve the project from this file so double-clicking works from any directory.
cd /d "%~dp0"

rem Prefer the project environment; the Python launcher handles setup if it is missing.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "dashboard\launch.py" %*
) else (
    python "dashboard\launch.py" %*
)

rem Keep setup failures visible when this window was opened from File Explorer.
if errorlevel 1 (
    echo.
    echo Dashboard konnte nicht gestartet werden. Bitte die Meldung oben beachten.
    pause
)

endlocal
