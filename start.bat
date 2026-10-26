@echo off
rem Messages are English on purpose: the Windows console does not render Persian correctly.
rem Uses a shared virtual environment in the parent folder (..\.venv) if it exists, otherwise creates .venv here.
setlocal
cd /d "%~dp0"

if exist "..\.venv\Scripts\python.exe" (
  set "VPY=..\.venv\Scripts\python.exe"
) else (
  where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
  %PY% -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,13) else 1)" >nul 2>nul
  if errorlevel 1 (
    echo Python 3.10 - 3.13 was not found. Install it from https://www.python.org/downloads/
    pause & exit /b 1
  )
  if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv || (echo Could not create .venv & pause & exit /b 1)
  set "VPY=.venv\Scripts\python.exe"
)

"%VPY%" -m pip install -q -r requirements.txt || echo WARNING: dependency check failed - continuing.

where ffmpeg >nul 2>nul || if not exist "ffmpeg.exe" (
  echo WARNING: FFmpeg was not found. Install it with:  winget install Gyan.FFmpeg
)

echo Starting at http://127.0.0.1:8000  - close this window to stop.
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8000"
"%VPY%" main.py
pause
