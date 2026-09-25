@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.14 -m venv .venv
    if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m unittest discover -s tests -v
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m PyInstaller ExeGate_UPS_Manager.spec --noconfirm --clean
if errorlevel 1 exit /b 1
echo Built dist\ExeGate_PowerExpert_Manager.exe
endlocal
