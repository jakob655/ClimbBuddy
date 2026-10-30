@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "dashboard\launch.py" %*
) else (
    python "dashboard\launch.py" %*
)
if errorlevel 1 (
    echo.
    echo Dashboard konnte nicht gestartet werden. Bitte die Meldung oben beachten.
    pause
)
endlocal
