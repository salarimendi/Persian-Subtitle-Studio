@echo off
rem Messages are English on purpose: the Windows console may not render Persian correctly.
rem Uses the shared virtual environment in ..\SubVENV. Never creates a venv inside the project.
setlocal
cd /d "%~dp0"

if not exist "..\SubVENV\Scripts\python.exe" (
  echo ERROR: Shared virtual environment was not found at ..\SubVENV
  pause
  exit /b 1
)

set "VPY=..\SubVENV\Scripts\python.exe"

"%VPY%" -m pip install -q -r requirements.txt || echo WARNING: dependency check failed - continuing.

where ffmpeg >nul 2>nul
if errorlevel 1 if not exist "ffmpeg.exe" (
  echo WARNING: FFmpeg was not found. Install it with: winget install Gyan.FFmpeg
)

echo Starting at http://127.0.0.1:8000 - close this window to stop.
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8000"

"%VPY%" main.py
pause
