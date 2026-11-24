@echo off
REM Build the Windows app using only the project's virtual environment.
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  echo LocalAssistant is not set up. Run setup.bat first.
  exit /b 1
)

.venv\Scripts\python.exe -m pip install --upgrade pyinstaller || goto :error
.venv\Scripts\python.exe -m pip install -r requirements.txt || goto :error

if not exist assets\app.ico (
  .venv\Scripts\python.exe tools\make_icon.py
  if errorlevel 1 goto :error
)

.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onedir --name LocalAssistant ^
  --icon assets\app.ico ^
  --collect-all faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all soundcard ^
  --collect-all keyboard ^
  --collect-all pypdf ^
  --collect-all docx ^
  --hidden-import PySide6.QtNetwork ^
  main.py || goto :error

if not exist dist\LocalAssistant\LocalAssistant.exe goto :error

echo.
echo Build successful: dist\LocalAssistant\LocalAssistant.exe
echo Whisper models are downloaded on first use and are not bundled.
exit /b 0

:error
echo.
echo Build failed. Review the error above.
exit /b 1
