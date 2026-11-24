@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish_to_master.ps1"
if errorlevel 1 (
  echo.
  echo Publishing failed. Review the message above.
) else (
  echo.
  echo Publishing completed.
)
pause
