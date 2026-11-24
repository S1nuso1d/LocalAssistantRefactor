@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo Python Launcher was not found. Install Python 3.12 and enable the py launcher.
  pause
  exit /b 1
)

py -3.12 --version >nul 2>nul
if errorlevel 1 (
  echo Python 3.12 was not found. Install Python 3.12 from python.org first.
  pause
  exit /b 1
)

if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv || goto :error
)

.venv\Scripts\python.exe -c "import sys; print('Using Python', sys.version.split()[0]); raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
if errorlevel 1 (
  echo The existing .venv is not Python 3.12. Rename or remove .venv, then run setup.bat again.
  exit /b 1
)

.venv\Scripts\python.exe -m pip install --upgrade pip || goto :error
.venv\Scripts\python.exe -m pip install -r requirements.txt || goto :error

echo.
echo Setup complete. Run run.bat to start LocalAssistant.
echo For a visible console, run: .venv\Scripts\python.exe main.py
pause
exit /b 0

:error
echo Setup failed. Check the error above.
pause
exit /b 1
