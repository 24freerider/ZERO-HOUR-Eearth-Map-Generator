@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Zero Hour Earth - building the .exe
echo ==========================================================
echo   Zero Hour Earth - building a single .exe you can share
echo ==========================================================
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo Python is not installed. Installing it with winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  echo.
  echo Python installed. CLOSE this window and run build_exe.bat AGAIN.
  pause
  exit /b 0
)
if not exist .venv (
  echo Creating a build environment...
  %PY% -m venv .venv
)
call .venv\Scripts\activate.bat
echo Installing libraries (first time takes a minute)...
python -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller
if errorlevel 1 goto fail
python -c "import app; open('version.txt','w').write(app.VERSION)"
set /p VER=<version.txt
del version.txt
echo Building Zero Hour Earth v%VER% ...
pyinstaller --noconfirm --clean --onefile --windowed --name ZeroHourEarth --icon icon.ico ^
  --add-data "template.map;." --add-data "icon.ico;." --add-data "icon.png;." --add-data "logo.png;." app.py
if errorlevel 1 goto fail
set OUT=ZeroHourEarth_v%VER%_Windows
if exist "%OUT%" rmdir /s /q "%OUT%"
mkdir "%OUT%"
copy /y dist\ZeroHourEarth.exe "%OUT%\" >nul
copy /y README_EN.txt "%OUT%\README.txt" >nul
copy /y CHANGELOG_EN.txt "%OUT%\CHANGELOG.txt" >nul
rem --- source code for people who want to change the program
mkdir "%OUT%\source"
for %%F in (*.py run.bat build_exe.bat requirements.txt template.map icon.ico icon.png logo.png README_EN.txt README_SK.txt CHANGELOG_EN.txt CHANGELOG_SK.txt DEVELOPERS.txt) do copy /y "%%F" "%OUT%\source\" >nul
copy /y DEVELOPERS.txt "%OUT%\DEVELOPERS.txt" >nul
powershell -NoProfile -Command "Compress-Archive -Force -Path '%OUT%\*' -DestinationPath '%OUT%.zip'"
echo.
echo ==========================================================
echo   DONE
echo   Program : dist\ZeroHourEarth.exe
echo   To share: %OUT%.zip   (exe + README + CHANGELOG + source code)
echo ==========================================================
explorer /select,"%CD%\%OUT%.zip"
pause
exit /b 0
:fail
echo.
echo BUILD FAILED - send the text above to get help.
pause
exit /b 1
