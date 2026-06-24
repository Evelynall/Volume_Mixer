@echo off
setlocal
cd /d "%~dp0"

echo Checking Python environment...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo Please install Python 3.11+ and add it to PATH.
    pause
    exit /b 1
)

echo Checking PyInstaller...
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PyInstaller not installed.
    echo Run: pip install -r requirements-build.txt
    pause
    exit /b 1
)

echo Cleaning old build files...
if exist build rmdir /s /q build 2>nul
if exist __pycache__ rmdir /s /q __pycache__ 2>nul
if exist dist (
    rmdir /s /q dist 2>nul
    if exist dist (
        echo [WARN] dist directory is in use, build will overwrite existing files.
    )
)

echo Building with PyInstaller...
python -m PyInstaller --noconfirm --clean volume_mixer.spec
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Please check the output above.
    pause
    exit /b 1
)

echo.
echo ========================================
echo  Build completed successfully
echo  Output: dist\volume_mixer\
echo ========================================
echo.
pause
endlocal
