@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=python"
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
  "%%~fD\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set PY="%%~fD\python.exe"
)
if not defined PY (
  echo Python is not installed. Installing it with winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo.
    echo Automatic install failed. Download Python from https://www.python.org/downloads/
    echo During setup TICK "Add python.exe to PATH", then run run.bat again.
    start https://www.python.org/downloads/
    pause
    exit /b 1
  )
  echo.
  echo Python installed. CLOSE this window and run run.bat AGAIN.
  pause
  exit /b 0
)
if not exist .venv (
  echo Preparing, first start takes about a minute...
  %PY% -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --disable-pip-version-check -q -r requirements.txt
python app.py
if errorlevel 1 pause
