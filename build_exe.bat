@echo off
REM Builds a windowed LiveAssistant.exe with PyInstaller (Windows only).
REM Whisper models are NOT bundled: faster-whisper downloads the selected
REM model on first start, so the first launch needs internet access.
setlocal
python -m pip install --upgrade pyinstaller || goto :error
python -m pip install -r requirements.txt || goto :error

python -m PyInstaller --noconfirm --clean --windowed --name LiveAssistant ^
  --collect-all faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all soundcard ^
  --hidden-import keyboard ^
  main.py || goto :error

echo.
echo Done: dist\LiveAssistant\LiveAssistant.exe
exit /b 0

:error
echo Build failed.
exit /b 1
