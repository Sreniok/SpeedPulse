@echo off
REM Windows Deployment Script (Wrapper)
REM This script runs deploy.sh using Git Bash or WSL

echo ========================================
echo   SpeedPulse Deployment Tool (Windows)
echo ========================================
echo.

REM Check if Git Bash exists
where bash >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo Using Git Bash...
    bash "%~dp0deploy.sh"
    goto :end
)

REM Check if WSL exists
where wsl >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo Using WSL...
    wsl bash "%~dp0deploy.sh"
    goto :end
)

REM Neither found
echo ERROR: Git Bash or WSL required
echo.
echo Please install one of the following:
echo   1. Git for Windows (includes Git Bash)
echo      https://git-scm.com/download/win
echo.
echo   2. Windows Subsystem for Linux (WSL)
echo      wsl --install
echo.
pause
exit /b 1

:end
pause
