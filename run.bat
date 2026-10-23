@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Create this repo's virtual environment and install requirements.txt first.
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
