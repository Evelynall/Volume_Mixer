$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host "Checking Python environment..."
try {
    & python --version 2>&1 | Out-Null
} catch {
    Write-Host "[ERROR] Python not found in PATH." -ForegroundColor Red
    Write-Host "Please install Python 3.11+ and add it to PATH."
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "Checking PyInstaller..."
try {
    & python -m PyInstaller --version 2>&1 | Out-Null
} catch {
    Write-Host "[ERROR] PyInstaller not installed." -ForegroundColor Red
    Write-Host "Run: pip install -r requirements-build.txt"
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "Cleaning old build files..."
if (Test-Path build) { Remove-Item build -Recurse -Force -ErrorAction SilentlyContinue }
if (Test-Path __pycache__) { Remove-Item __pycache__ -Recurse -Force -ErrorAction SilentlyContinue }
if (Test-Path dist) {
    try {
        Remove-Item dist -Recurse -Force -ErrorAction Stop
    } catch {
        Write-Host "[WARN] dist directory is in use, build will overwrite existing files." -ForegroundColor Yellow
    }
}

Write-Host "Building with PyInstaller..."
& python -m PyInstaller --noconfirm --clean volume_mixer.spec
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[ERROR] Build failed. Please check the output above." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Build completed successfully" -ForegroundColor Green
Write-Host " Output: dist\VolumeMixer\" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Read-Host "Press Enter to exit"
