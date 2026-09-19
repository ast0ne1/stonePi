@echo off
REM UX overlay push — works from CMD or PowerShell without changing execution policy.
REM PowerShell:
REM   cmd /c "scripts\push-ux-fixes.cmd -Apply"
REM CMD / double-click:
REM   scripts\push-ux-fixes.cmd -Apply

setlocal EnableExtensions EnableDelayedExpansion

set "PIHOST=adam@192.168.0.225"
set "APPLY="

:parse
if "%~1"=="" goto resolve
if /I "%~1"=="-Apply" (
  set "APPLY=-Apply"
  shift
  goto parse
)
if /I "%~1"=="/Apply" (
  set "APPLY=-Apply"
  shift
  goto parse
)
set "PIHOST=%~1"
shift
goto parse

:resolve
REM Find the scripts folder (PowerShell can leave %%~dp0 empty/wrong).
set "HERE="
if exist "%~dp0push-ux-fixes.ps1" set "HERE=%~dp0"
if not defined HERE if exist "%~f0" (
  for %%I in ("%~f0") do set "HERE=%%~dpI"
)
if not defined HERE if exist "%CD%\scripts\push-ux-fixes.ps1" set "HERE=%CD%\scripts\"
if not defined HERE if exist "%CD%\push-ux-fixes.ps1" set "HERE=%CD%\"

if not defined HERE (
  echo Cannot locate push-ux-fixes.ps1
  echo Run from the stonePi repo, e.g.:
  echo   cmd /c "scripts\push-ux-fixes.cmd -Apply"
  pause
  exit /b 1
)

REM Ensure trailing backslash
if not "!HERE:~-1!"=="\" set "HERE=!HERE!\"

set "PS1=!HERE!push-ux-fixes.ps1"
if not exist "!PS1!" (
  echo Missing:
  echo   !PS1!
  pause
  exit /b 1
)

REM Repo root is parent of scripts\
for %%I in ("!HERE!..") do set "ROOT=%%~fI"

echo StonePi UX push  host=%PIHOST%  %APPLY%
echo Script: !PS1!
echo.

pushd "!ROOT!" >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "!PS1!" -PiUserHost "%PIHOST%" %APPLY%
set "ERR=!ERRORLEVEL!"
popd >nul

if not "!ERR!"=="0" (
  echo.
  echo Failed with exit !ERR!.
  pause
  exit /b !ERR!
)
echo.
pause
endlocal
