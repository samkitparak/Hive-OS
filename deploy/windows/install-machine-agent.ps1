# HIVE OS Maestro machine-PC agent installer.
# Copy the HIVE OS folder to the machine PC, then run as Administrator.

$ErrorActionPreference = "Stop"
$InstallDir = "C:\HIVE-Agent"
$SourceDir = (Resolve-Path "$PSScriptRoot\..\..").Path

function Require-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this installer from an Administrator PowerShell window."
    }
}

Require-Admin
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [Environment]::GetEnvironmentVariable("Path", "User")
}

$MachineKey = Read-Host "Machine key, for example morbidelli_cx100"
$BrokerHost = Read-Host "Central HIVE/CV PC IP address"
$LogFolder = Read-Host "Maestro log folder [C:\SCM\Maestro\Logs]"
if ([string]::IsNullOrWhiteSpace($LogFolder)) { $LogFolder = "C:\SCM\Maestro\Logs" }
if (-not (Test-Path $LogFolder)) {
    throw "Maestro log folder does not exist: $LogFolder"
}

New-Item -ItemType Directory -Force -Path "$InstallDir\src" | Out-Null
New-Item -ItemType Directory -Force -Path "$InstallDir\config" | Out-Null
New-Item -ItemType Directory -Force -Path "$InstallDir\logs" | Out-Null
Copy-Item "$SourceDir\src\maestro_agent.py" "$InstallDir\src\" -Force
Copy-Item "$SourceDir\requirements.txt" "$InstallDir\" -Force

$EscapedLogFolder = $LogFolder.Replace("\", "\\")
@"
mqtt:
  broker_host: "$BrokerHost"
  broker_port: 1883
  keepalive: 60
  topic_prefix: "hive/machines"
maestro_agents:
  - machine_key: "$MachineKey"
    label: "$MachineKey"
    host: "localhost"
    log_folder: "$EscapedLogFolder"
    cnc_folder: null
"@ | Set-Content "$InstallDir\config\machines.yaml" -Encoding UTF8

Set-Location $InstallDir
python -m venv .venv
& "$InstallDir\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$InstallDir\.venv\Scripts\pip.exe" install "paho-mqtt>=2.1,<3" "PyYAML>=6,<7"

@"
@echo off
cd /d C:\HIVE-Agent
set PYTHONPATH=src
.venv\Scripts\python.exe src\maestro_agent.py --machine $MachineKey --config config\machines.yaml >> logs\agent.log 2>&1
"@ | Set-Content "$InstallDir\start-agent.cmd" -Encoding ASCII

schtasks /Create /TN "HIVE Agent - $MachineKey" /SC ONSTART /RU SYSTEM /RL HIGHEST `
    /TR "$InstallDir\start-agent.cmd" /F | Out-Null
Start-Process "$InstallDir\start-agent.cmd"

Write-Host ""
Write-Host "HIVE machine agent installed for $MachineKey." -ForegroundColor Green
Write-Host "It should appear online in Central HIVE Diagnostics within 1-3 minutes."
Write-Host "Agent log: $InstallDir\logs\agent.log"
