# HIVE OS Maestro machine-PC agent installer.
# Copy the HIVE OS folder to the machine PC, then run as Administrator.

param(
    [string]$MachineKey,
    [string]$BrokerHost,
    [string]$LogFolder,
    [string]$CentralApiBase,
    [Security.SecureString]$AgentToken
)

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

function Find-MaestroLogFolder {
    $Candidates = @(
        "C:\SCM\Maestro\Logs",
        "C:\Program Files\SCM Group\Maestro\Logs",
        "C:\ProgramData\SCM Group\Maestro\Logs",
        "D:\SCM\Maestro\Logs"
    )
    return $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

function ConvertFrom-HiveSecureString([Security.SecureString]$Value) {
    $Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    }
}

Require-Admin
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [Environment]::GetEnvironmentVariable("Path", "User")
}

if ([string]::IsNullOrWhiteSpace($MachineKey)) {
    $MachineKey = Read-Host "Machine key, for example morbidelli_cx100"
}
if ($MachineKey -notmatch '^[a-z0-9_]+$') { throw "Invalid machine key: $MachineKey" }
if ([string]::IsNullOrWhiteSpace($BrokerHost)) {
    $BrokerHost = Read-Host "Central HIVE/CV PC IP address"
}
if ([string]::IsNullOrWhiteSpace($BrokerHost)) { throw "Central HIVE IP is required." }
if ([string]::IsNullOrWhiteSpace($LogFolder)) { $LogFolder = Find-MaestroLogFolder }
if ([string]::IsNullOrWhiteSpace($LogFolder)) {
    $LogFolder = Read-Host "Maestro log folder"
}
if (-not (Test-Path $LogFolder)) {
    throw "Maestro log folder does not exist: $LogFolder"
}

$MqttTest = Test-NetConnection -ComputerName $BrokerHost -Port 1883 -WarningAction SilentlyContinue
if (-not $MqttTest.TcpTestSucceeded) {
    throw "Cannot reach MQTT at $BrokerHost`:1883. Check the central PC and firewall first."
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

$LatestLog = Get-ChildItem $LogFolder -Filter *.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$AnalysisPath = $null
if ($LatestLog) {
    $SamplePath = "$InstallDir\logs\commissioning-sample.txt"
    Get-Content $LatestLog.FullName -Tail 500 | Set-Content $SamplePath -Encoding UTF8
    Write-Host "Commissioning sample: $SamplePath" -ForegroundColor Cyan
}
if ($LatestLog -and -not [string]::IsNullOrWhiteSpace($CentralApiBase)) {
    try {
        $Sample = (Get-Content $LatestLog.FullName -Tail 500) -join "`n"
        $Body = @{
            machine_key = $MachineKey
            log_text = $Sample
            persist = $false
            site_timezone = "Asia/Kolkata"
        } | ConvertTo-Json
        if (-not $CentralApiBase.StartsWith("https://")) { throw "CentralApiBase must use HTTPS." }
        if (-not $AgentToken) { $AgentToken = Read-Host "HIVE machine integration key" -AsSecureString }
        $PlainAgentToken = ConvertFrom-HiveSecureString $AgentToken
        try {
            $Headers = @{ Authorization = "Bearer $PlainAgentToken" }
            $Analysis = Invoke-RestMethod -Method Post -Uri "$($CentralApiBase.TrimEnd('/'))/api/commissioning/log/analyze" `
                -Headers $Headers -ContentType "application/json" -Body $Body -TimeoutSec 20
        } finally {
            $Headers = $null
            $PlainAgentToken = $null
        }
        $AnalysisPath = "$InstallDir\logs\commissioning-analysis.json"
        $Analysis | ConvertTo-Json -Depth 8 | Set-Content $AnalysisPath -Encoding UTF8
        Write-Host "Parser recognition: $([math]::Round($Analysis.recognition_rate * 100))%" -ForegroundColor Cyan
        if (-not $Analysis.ready_to_replay) {
            Write-Warning "Agent installed, but this log format has not passed HIVE parser checks. Open Commission in HIVE."
        }
    } catch {
        Write-Warning "Agent installed, but central log analysis failed: $($_.Exception.Message)"
    }
} elseif ($LatestLog) {
    Write-Warning "Central analysis skipped. After HTTPS is commissioned, rerun with -CentralApiBase; the installer will securely prompt for a machine integration key."
}

Write-Host ""
Write-Host "HIVE machine agent installed for $MachineKey." -ForegroundColor Green
Write-Host "It should appear online in Central HIVE Diagnostics within 1-3 minutes."
Write-Host "Agent log: $InstallDir\logs\agent.log"
if ($AnalysisPath) {
    Write-Host "Commissioning analysis: $AnalysisPath"
}
