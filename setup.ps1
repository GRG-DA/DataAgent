# DataAgent Setup Script
# Run: powershell -ExecutionPolicy Bypass -File setup.ps1

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  DataAgent - Setup Script" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check Python version
try {
    $pyVersion = python --version 2>&1
    Write-Host "✓ Python: $pyVersion (requires 3.11-3.13)" -ForegroundColor Green
} catch {
    Write-Host "✗ Python not found. Install Python 3.11-3.13 from python.org" -ForegroundColor Red
    exit 1
}

# Create virtual environment
if (-not (Test-Path "venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv venv
    Write-Host "✓ Virtual environment created" -ForegroundColor Green
} else {
    Write-Host "✓ Virtual environment exists" -ForegroundColor Green
}

# Activate and install dependencies
Write-Host "Installing dependencies..." -ForegroundColor Yellow
& .\venv\Scripts\pip install -r requirements.txt
Write-Host "✓ Dependencies installed" -ForegroundColor Green

# Create required directories
$dirs = @("data/raw", "data/staging", "data/curated", "logs", "outputs/charts", "outputs/reports", "config/pipelines", "config/schedules")
foreach ($dir in $dirs) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
Write-Host "✓ Data directories created" -ForegroundColor Green

# Initialize database
Write-Host "Initializing database..." -ForegroundColor Yellow
& .\venv\Scripts\python run.py init-db
Write-Host "✓ Database initialized" -ForegroundColor Green

# Run system check
Write-Host "Running system check..." -ForegroundColor Yellow
& .\venv\Scripts\python run.py check

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Setup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "To start the application:"
Write-Host "  .\venv\Scripts\python run.py init-db  # first run only"
Write-Host "  .\venv\Scripts\python run.py web      # serve API + UI (port from config/settings.yaml)"
Write-Host ""
Write-Host "Or use Docker: docker compose up --build (requires JWT_SECRET>=16 chars)"
Write-Host ""
