# HIVE OS central/Cabinet Vision PC installer.
# Run from an Administrator PowerShell window with internet access.

$ErrorActionPreference = "Stop"
$InstallDir = "C:\HIVE-OS"
$SourceDir = (Resolve-Path "$PSScriptRoot\..\..").Path

function Require-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this installer from an Administrator PowerShell window."
    }
}

function Ensure-WingetPackage($id, $command) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        Write-Host "Installing $id..."
        winget install --id $id --silent --accept-package-agreements --accept-source-agreements
    }
}

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [Environment]::GetEnvironmentVariable("Path", "User")
}

Require-Admin
Ensure-WingetPackage "Python.Python.3.12" "python"
Ensure-WingetPackage "OpenJS.NodeJS.LTS" "node"
Ensure-WingetPackage "EclipseMosquitto.Mosquitto" "mosquitto"
if (-not (Get-OdbcDriver -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "ODBC Driver 18 for SQL Server*" })) {
    Write-Host "Installing Microsoft ODBC Driver 18 for SQL Server..."
    winget install --id Microsoft.msodbcsql.18 --exact --silent `
        --accept-package-agreements --accept-source-agreements
}
Refresh-Path

$CvFolder = Read-Host "Cabinet Vision export folder [C:\CabinetVision\Export]"
if ([string]::IsNullOrWhiteSpace($CvFolder)) { $CvFolder = "C:\CabinetVision\Export" }

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
robocopy $SourceDir $InstallDir /E /XD .git .pytest_cache node_modules dist dashboard\node_modules dashboard\dist /XF *.db *.db-shm *.db-wal | Out-Null
New-Item -ItemType Directory -Force -Path "$InstallDir\logs" | Out-Null

Set-Location $InstallDir
python -m venv .venv
& "$InstallDir\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$InstallDir\.venv\Scripts\pip.exe" install -r requirements.txt

Set-Location "$InstallDir\dashboard"
npm ci
npm run build

$ConfigPath = "$InstallDir\config\machines.yaml"
$Config = Get-Content $ConfigPath -Raw
$EscapedCvFolder = $CvFolder.Replace("\", "\\")
$Config = $Config -replace 'cv_watch_folder:.*', "cv_watch_folder: `"$EscapedCvFolder`""
Set-Content $ConfigPath $Config -Encoding UTF8

@"
listener 1883
allow_anonymous true
persistence true
persistence_location C:\HIVE-OS\data\
log_dest file C:\HIVE-OS\logs\mosquitto.log
"@ | Set-Content "$InstallDir\config\mosquitto.conf" -Encoding ASCII
New-Item -ItemType Directory -Force -Path "$InstallDir\data" | Out-Null
Stop-Service mosquitto -ErrorAction SilentlyContinue
Set-Service mosquitto -StartupType Disabled -ErrorAction SilentlyContinue

@"
@echo off
cd /d C:\HIVE-OS
start "HIVE MQTT" /min cmd /c "mosquitto -c config\mosquitto.conf"
start "HIVE Backend" /min cmd /c "set PYTHONPATH=src && .venv\Scripts\uvicorn.exe src.main:app --host 0.0.0.0 --port 8000 >> logs\backend.log 2>&1"
"@ | Set-Content "$InstallDir\start-hive.cmd" -Encoding ASCII

schtasks /Create /TN "HIVE OS Central" /SC ONSTART /RU SYSTEM /RL HIGHEST `
    /TR "$InstallDir\start-hive.cmd" /F | Out-Null

New-NetFirewallRule -DisplayName "HIVE OS API" -Direction Inbound `
    -Protocol TCP -LocalPort 8000 -RemoteAddress LocalSubnet `
    -Action Allow -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "HIVE OS MQTT" -Direction Inbound `
    -Protocol TCP -LocalPort 1883 -RemoteAddress LocalSubnet `
    -Action Allow -ErrorAction SilentlyContinue | Out-Null

Start-Process "$InstallDir\start-hive.cmd"

@"
[InternetShortcut]
URL=http://localhost:8000
"@ | Set-Content "$env:PUBLIC\Desktop\HIVE OS.url" -Encoding ASCII

Write-Host ""
Write-Host "HIVE OS central installation complete." -ForegroundColor Green
Write-Host "Dashboard: http://localhost:8000"
Write-Host "Diagnostics: open the dashboard and click Diagnostics."
Write-Host "Logs: $InstallDir\logs"
