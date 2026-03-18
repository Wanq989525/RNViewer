@echo off
chcp 936 >nul
title RNViewer - Start

cd /d "%~dp0"

set PORT=8501
set PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple

python --version >nul 2>&1
if errorlevel 1 (
    echo ========================================
    echo [Error] Python not found
    echo Please install Python 3.9+
    echo https://www.python.org/downloads/
    echo ========================================
    pause
    exit /b 1
)

echo ========================================
echo RNViewer - RN Notes Viewer
echo Developer: De-hamster
echo ========================================
echo.

pip --version >nul 2>&1
if errorlevel 1 (
    set PIP_CMD=python -m pip
) else (
    set PIP_CMD=pip
)

echo [Check] Checking dependencies...
%PIP_CMD% show streamlit >nul 2>&1
if errorlevel 1 (
    echo [Install] Installing streamlit...
    %PIP_CMD% install streamlit -q -i %PIP_MIRROR%
)
%PIP_CMD% show requests >nul 2>&1
if errorlevel 1 (
    echo [Install] Installing requests...
    %PIP_CMD% install requests -q -i %PIP_MIRROR%
)
echo [Done] Dependencies ready
echo.

echo [Check] Checking port %PORT% ...
set KILL_PID=

for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    set KILL_PID=%%p
)

if defined KILL_PID (
    echo [Warning] Port %PORT% is used by PID=%KILL_PID%
    taskkill /F /PID %KILL_PID% >nul 2>&1
    if errorlevel 1 (
        echo [Info] Cannot kill process, continue...
    ) else (
        echo [Done] Port released
        timeout /t 2 /nobreak >nul
    )
) else (
    echo [Info] Port %PORT% is available
)
echo.

echo [Start] Starting RNViewer...
echo [URL] http://localhost:%PORT%
echo.
python -W ignore -m streamlit run Rnv.py --server.headless true --server.port %PORT%

pause