@echo off
setlocal enabledelayedexpansion

echo.
echo === ExeGate PowerExpert Manager - Build EXE ===
echo.

:: --- Detect Python (try py launcher, then python, then python3) ---
set PYTHON=

py --version >nul 2>&1
if not errorlevel 1 ( set PYTHON=py & goto :found_python )

python --version >nul 2>&1
if not errorlevel 1 ( set PYTHON=python & goto :found_python )

python3 --version >nul 2>&1
if not errorlevel 1 ( set PYTHON=python3 & goto :found_python )

:: Last resort: search common install locations
for %%d in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python39\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
) do (
    if exist %%d ( set PYTHON=%%d & goto :found_python )
)

echo [ERROR] Python not found via py / python / python3 or common paths.
echo.
echo Solutions:
echo   1. Reinstall Python from https://www.python.org/downloads/
echo      and check "Add Python to PATH" during install.
echo   2. Or run this in cmd first:
echo         set PATH=%%LOCALAPPDATA%%\Programs\Python\Python312;%%PATH%%
echo      (adjust version number to match your install)
echo.
pause
exit /b 1

:found_python
for /f "tokens=*" %%v in ('!PYTHON! --version 2^>^&1') do echo Python: %%v   (command: !PYTHON!)

:: --- Detect pip ---
set PIP=
!PYTHON! -m pip --version >nul 2>&1
if not errorlevel 1 ( set PIP=!PYTHON! -m pip & goto :found_pip )

pip --version >nul 2>&1
if not errorlevel 1 ( set PIP=pip & goto :found_pip )

pip3 --version >nul 2>&1
if not errorlevel 1 ( set PIP=pip3 & goto :found_pip )

echo [ERROR] pip not found. Run: !PYTHON! -m ensurepip
pause
exit /b 1

:found_pip
echo pip:    !PIP!

:: --- Install dependencies ---
echo.
echo [1/3] Installing dependencies...
!PIP! install --quiet --upgrade PyQt5 pyqtgraph pyserial pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies. Check internet connection.
    pause
    exit /b 1
)
echo Done.

:: --- Clean previous build ---
echo.
echo [2/3] Cleaning previous build...
if exist "dist\ExeGate_PowerExpert_Manager.exe" del /f /q "dist\ExeGate_PowerExpert_Manager.exe"
if exist "build" rmdir /s /q build
echo Done.

:: --- Build EXE ---
echo.
echo [3/3] Building EXE (this may take 1-3 minutes)...
echo.

!PYTHON! -m PyInstaller ExeGate_UPS_Manager.spec --noconfirm --clean

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. See details above.
    pause
    exit /b 1
)

:: --- Result ---
echo.
if exist "dist\ExeGate_PowerExpert_Manager.exe" (
    echo ================================================
    echo  BUILD SUCCESSFUL
    echo  dist\ExeGate_PowerExpert_Manager.exe
    echo ================================================
    for %%f in ("dist\ExeGate_PowerExpert_Manager.exe") do (
        set SIZE=%%~zf
        echo File size: !SIZE! bytes
    )
    echo.
    echo Press Y to launch now, or any other key to exit.
    choice /c YN /n /t 10 /d N
    if !errorlevel!==1 start "" "dist\ExeGate_PowerExpert_Manager.exe"
) else (
    echo [ERROR] EXE file not found after build.
)

echo.
pause
endlocal
