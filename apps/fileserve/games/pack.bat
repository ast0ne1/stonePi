@echo off
REM Pack games\<name> into dist\<name>.zip for FileServe upload.
cd /d "%~dp0\.."
if "%~1"=="" (
  python games\pack.py --all
) else (
  python games\pack.py %*
)
