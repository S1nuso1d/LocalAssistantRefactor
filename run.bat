@echo off
REM Launch LocalAssistant using the project's isolated environment.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  echo LocalAssistant is not set up yet. Run setup.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
if errorlevel 1 (
  echo The project environment must use Python 3.12. Run setup.bat after repairing .venv.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "main.py" %*
