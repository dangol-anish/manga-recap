@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
set "PIP_CACHE_DIR=%CD%\.cache\pip"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
echo Manga Recap - Windows setup using Python 3.11 x64 and CPU-only PyTorch
where ffmpeg >nul 2>nul
if errorlevel 1 goto missing_ffmpeg
where ffprobe >nul 2>nul
if errorlevel 1 goto missing_ffmpeg
py -3.11 -c "import sys,struct; sys.exit(0 if struct.calcsize('P')==8 else 1)"
if errorlevel 1 (
  echo Install Python 3.11 64-bit with the Python launcher, then reopen this terminal.
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3.11 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import sys,struct; sys.exit(0 if sys.version_info[:2]==(3,11) and struct.calcsize('P')==8 else 1)"
if errorlevel 1 (
  echo This .venv uses the wrong Python. Keep your data; create a Python 3.11 x64 virtual environment.
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --only-binary=:all: torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --only-binary=:all: -r requirements.lock.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -X utf8 -m scripts.doctor --media
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -X utf8 -m scripts.install_model
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -X utf8 -m scripts.doctor --model
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -X utf8 -m scripts.validate_model
if errorlevel 1 goto failed
echo Setup passed. Run .\run.bat and open http://127.0.0.1:8000/ in your external browser.
exit /b 0
:missing_ffmpeg
echo Install FFmpeg with ffprobe and add its bin directory to PATH, then open a NEW terminal.
echo See docs\WINDOWS_SETUP.md.
exit /b 1
:failed
echo Setup stopped. Read the error above, fix it, and run this setup again.
echo Completed downloads and your manga/media are kept. See docs\WINDOWS_SETUP.md.
exit /b 1
