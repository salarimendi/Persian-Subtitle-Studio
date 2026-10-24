
@echo off
setlocal
cd /d "%~dp0"

set "VENV=%~dp0..\SubVENV\Scripts\python.exe"

if not exist "%VENV%" (
    echo ERROR: Python environment not found:
    echo %VENV%
    pause
    exit /b 1
)

"%VENV%" -m uvicorn main:app --host 127.0.0.1 --port 8000

pause
