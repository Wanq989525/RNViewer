@echo off
chcp 936 >nul
title RNViewer - Stop

set PORT=8501

echo ========================================
echo RNViewer - Stop Application
echo ========================================
echo.

echo [Check] Checking port %PORT% ...
set KILL_PID=

for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    set KILL_PID=%%p
)

if defined KILL_PID (
    echo [Found] Port %PORT% is used by PID=%KILL_PID%
    echo [Action] Stopping RNViewer...
    taskkill /F /PID %KILL_PID% >nul 2>&1
    if errorlevel 1 (
        echo [Error] Cannot stop process, try run as Administrator
    ) else (
        echo [Done] RNViewer stopped
    )
) else (
    echo [Info] Port %PORT% is free, RNViewer is not running
)

echo.
pause