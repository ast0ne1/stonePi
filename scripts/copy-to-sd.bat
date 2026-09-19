@echo off
REM Copy StonePi onto the Raspberry Pi boot partition (FAT) without Windows venvs.
REM Usage: scripts\copy-to-sd.bat E:\
REM        scripts\copy-to-sd.bat E:\stonePi

setlocal
set "SRC=%~dp0.."
set "DEST=%~1"

if "%DEST%"=="" (
  echo Usage: scripts\copy-to-sd.bat BOOTDRIVE:\
  echo Example: scripts\copy-to-sd.bat E:\
  echo After Imager writes the OS, Windows remounts the boot partition - use that letter.
  exit /b 1
)

if not exist "%DEST%" (
  echo Destination not found: %DEST%
  exit /b 1
)

REM If DEST is a drive root, create stonePi\ under it.
echo %DEST%| findstr /R /C:"\\$" >nul
if %ERRORLEVEL%==0 (
  set "DEST=%DEST%stonePi"
)

echo Source: %SRC%
echo Dest:   %DEST%
mkdir "%DEST%" 2>nul

robocopy "%SRC%" "%DEST%" /E /XD .venv data .git dist __pycache__ .pytest_cache node_modules .cursor /XF *.pyc .env /NFL /NDL /NJH /NJS /nc /ns /np
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 (
  echo robocopy failed with code %RC%
  exit /b %RC%
)

echo.
echo Copied. Eject the SD card, boot the Pi, then SSH in and run:
echo   sudo bash /boot/firmware/stonePi/deploy/install.sh --hostname stonepi
echo.
exit /b 0
