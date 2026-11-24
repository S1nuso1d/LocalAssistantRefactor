@echo off
REM Update the clean master checkout without touching user data in AppData.
setlocal
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
  echo Git for Windows was not found.
  exit /b 1
)

for /f "delims=" %%B in ('git branch --show-current') do set CURRENT_BRANCH=%%B
if not "%CURRENT_BRANCH%"=="master" (
  echo Update is allowed only from the master branch. Current: %CURRENT_BRANCH%
  exit /b 1
)

set "DIRTY="
for /f "delims=" %%S in ('git status --porcelain') do set "DIRTY=1"
if defined DIRTY (
  echo Your working tree has local or untracked changes. Commit, stash, or back them up before updating.
  exit /b 1
)

git pull --ff-only origin master
if errorlevel 1 goto :error

if not exist .venv\Scripts\python.exe (
  echo Virtual environment missing. Run setup.bat first.
  exit /b 1
)
.venv\Scripts\python.exe -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
if errorlevel 1 (
  echo The project environment must use Python 3.12. Repair .venv with setup.bat before updating.
  exit /b 1
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Update complete. Run run.bat to launch LocalAssistant.
exit /b 0

:error
echo.
echo Update failed. Review the error above.
exit /b 1
