@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

where python >nul 2>&1
if errorlevel 1 (
    echo Python was not found on PATH.
    echo Install Python 3 and make sure "python" works from Command Prompt.
    exit /b 1
)

python "scripts\run_dev.py"
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
    echo.
    echo StonePi stopped with error %ERR%.
    pause
)
endlocal
exit /b %ERR%
