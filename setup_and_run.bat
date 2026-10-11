@echo off
title Media Hub Setup & Launcher
cd /d "%~dp0"

echo ===================================================
echo           Media Hub Setup & Launcher
echo ===================================================
echo.

:: 1. Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python from https://www.python.org/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: 2. Install / Update dependencies
echo [1/3] Checking and installing dependencies...
pip install -r requirements.txt --quiet --disable-pip-version-check
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

:: 3. Ensure FFmpeg binaries are set up via static-ffmpeg
echo [2/3] Verifying FFmpeg binaries...
python -c "import static_ffmpeg; static_ffmpeg.add_paths()" >nul 2>&1

:: 4. Launch the application
echo [3/3] Launching Media Hub...
start "" pythonw app.py

echo Done! The app should now be running.
timeout /t 2 >nul
exit