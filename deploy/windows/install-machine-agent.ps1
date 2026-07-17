# HIVE OS Maestro machine-PC agent installer.

param(
    [string]$EnrollmentBundle,
    [string]$MachineKey,
    [string]$BrokerHost,
    [string]$LogFolder,
    [string]$CentralApiBase,
    [Security.SecureString]$AgentToken,
    [switch]$AllowOnlinePrerequisites
)

$ErrorActionPreference = "Stop"
$InstallDir = "C:\HIVE-Agent"
$BundleRoot = $null
$TemporaryBundleRoot = $null
$StageDir = "$InstallDir.stage-$([Guid]::NewGuid().ToString('N'))"
$PreviousDir = "$InstallDir.previous"

function Require-Admin {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
    if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this installer from an Administrator PowerShell window."
    }
}

function Find-MaestroLogFolder {
    return @(
        "C:\SCM\Maestro\Logs",
        "C:\Program Files\SCM Group\Maestro\Logs",
        "C:\ProgramData\SCM Group\Maestro\Logs",
        "D:\SCM\Maestro\Logs"
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Container } | Select-Object -First 1
}

function ConvertFrom-HiveSecureString([Security.SecureString]$Value) {
    $Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer) }
}

function Select-EnrollmentBundle {
    Add-Type -AssemblyName System.Windows.Forms
    $Dialog = New-Object System.Windows.Forms.OpenFileDialog
    $Dialog.Title = "Select the HIVE machine enrollment ZIP"
    $Dialog.Filter = "HIVE enrollment (*.zip)|*.zip"
    if ($Dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { return $null }
    return $Dialog.FileName
}

function Get-HivePython312 {
    $Command = Get-Command python.exe -ErrorAction SilentlyContinue
    $Candidates = @(
        $(if ($Command) { $Command.Source }),
        "C:\Program Files\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -Unique
    foreach ($Candidate in $Candidates) {
        $Identity = & $Candidate -c "import sys, struct; print(f'{sys.version_info.major}.{sys.version_info.minor}-{struct.calcsize(`"P`") * 8}')" 2>$null
        if ($Identity -eq "3.12-64") { return $Candidate }
    }
    return $null
}

function Test-HiveAgentPayload([string]$Root) {
    $ManifestPath = Join-Path $Root "agent-payload.json"
    $SidecarPath = Join-Path $Root "agent-payload.json.sha256"
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $SidecarPath -PathType Leaf)) { return $null }
    $ExpectedManifestHash = ((Get-Content -LiteralPath $SidecarPath -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
    if ($ExpectedManifestHash -notmatch '^[0-9a-f]{64}$') { throw "Agent payload manifest hash is malformed." }
    $ActualManifestHash = (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualManifestHash -ne $ExpectedManifestHash) { throw "Agent payload manifest hash does not match." }
    $PayloadManifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if ($PayloadManifest.format -ne "hive-offline-agent-payload" -or $PayloadManifest.format_version -ne 1) {
        throw "Unsupported HIVE agent payload format."
    }
    if ($PayloadManifest.target -ne "windows-x64" -or $PayloadManifest.python_version -ne "3.12-64" -or
        -not [Environment]::Is64BitOperatingSystem) {
        throw "This HIVE agent payload requires Windows x64 and Python 3.12."
    }
    $Seen = @{}
    foreach ($File in @($PayloadManifest.files)) {
        $Relative = [string]$File.path
        if ([string]::IsNullOrWhiteSpace($Relative) -or $Relative.Contains('\') -or
            [IO.Path]::IsPathRooted($Relative) -or $Relative -match '(^|/)\.\.(/|$)' -or $Seen.ContainsKey($Relative)) {
            throw "Unsafe or duplicate agent payload path: $Relative"
        }
        $Seen[$Relative] = $true
        $FullPath = Join-Path $Root ($Relative -replace '/', '\')
        if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) { throw "Agent payload file is missing: $Relative" }
        $Item = Get-Item -LiteralPath $FullPath
        if ($Item.Length -ne [long]$File.size) { throw "Agent payload size does not match: $Relative" }
        $ExpectedHash = ([string]$File.sha256).ToLowerInvariant()
        if ($ExpectedHash -notmatch '^[0-9a-f]{64}$' -or
            (Get-FileHash -LiteralPath $FullPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) {
            throw "Agent payload hash does not match: $Relative"
        }
    }
    foreach ($Required in @(
        "install-machine-agent.ps1", "payload/src/maestro_agent.py", "payload/src/mqtt_client.py",
        "payload/runtime/python-3.12-x64.exe", "payload/requirements-agent.txt"
    )) {
        if (-not $Seen.ContainsKey($Required)) { throw "Agent payload is incomplete: $Required" }
    }
    if (-not ($Seen.Keys | Where-Object { $_ -like "payload/wheels/*.whl" })) {
        throw "Agent payload contains no dependency wheels."
    }
    return $PayloadManifest
}

function Install-HiveBundledPython([string]$Installer) {
    $Process = Start-Process -FilePath $Installer -ArgumentList @(
        "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_test=0"
    ) -Wait -PassThru
    if ($Process.ExitCode -notin @(0, 3010, 1641)) {
        throw "Bundled Python installer failed with exit code $($Process.ExitCode)."
    }
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [Environment]::GetEnvironmentVariable("Path", "User")
}

Require-Admin
if (Test-Path -LiteralPath "$PSScriptRoot\enrollment.json") {
    $BundleRoot = $PSScriptRoot
} else {
    if ([string]::IsNullOrWhiteSpace($EnrollmentBundle)) { $EnrollmentBundle = Select-EnrollmentBundle }
    if ([string]::IsNullOrWhiteSpace($EnrollmentBundle) -or -not (Test-Path -LiteralPath $EnrollmentBundle -PathType Leaf)) {
        throw "A HIVE enrollment ZIP is required. Issue one in Access control > Device certificates."
    }
    $TemporaryBundleRoot = Join-Path $env:TEMP "hive-enrollment-$([Guid]::NewGuid().ToString('N'))"
    Expand-Archive -LiteralPath $EnrollmentBundle -DestinationPath $TemporaryBundleRoot
    $BundleRoot = $TemporaryBundleRoot
}

try {
    $EnrollmentPath = Join-Path $BundleRoot "enrollment.json"
    if (-not (Test-Path -LiteralPath $EnrollmentPath -PathType Leaf)) {
        throw "The selected ZIP is not a HIVE enrollment bundle."
    }
    $Enrollment = Get-Content -LiteralPath $EnrollmentPath -Raw | ConvertFrom-Json
    if ($Enrollment.format -ne "hive-mqtt-enrollment-v1") { throw "Unsupported HIVE enrollment format." }
    if ([string]::IsNullOrWhiteSpace($MachineKey)) { $MachineKey = $Enrollment.machine_key }
    if ([string]::IsNullOrWhiteSpace($BrokerHost)) { $BrokerHost = $Enrollment.broker_host }
    $BrokerPort = [int]$Enrollment.broker_port
    if ($MachineKey -notmatch '^[a-z0-9_]+$') { throw "Invalid machine key: $MachineKey" }
    if ([string]::IsNullOrWhiteSpace($BrokerHost)) { throw "Central HIVE IP is required." }

    $PayloadManifest = Test-HiveAgentPayload $BundleRoot
    $OfflinePayloadReady = $null -ne $PayloadManifest
    if (-not $OfflinePayloadReady -and -not $AllowOnlinePrerequisites) {
        throw "This enrollment does not contain a verified offline runtime. Download a current enrollment from the installed central HIVE release, or explicitly use -AllowOnlinePrerequisites."
    }
    $SourceDir = Join-Path $BundleRoot "payload"
    if (-not (Test-Path -LiteralPath "$SourceDir\src\maestro_agent.py" -PathType Leaf)) {
        throw "The enrollment bundle does not contain the HIVE machine agent."
    }

    $PythonExe = Get-HivePython312
    if (-not $PythonExe -and $OfflinePayloadReady) {
        Install-HiveBundledPython (Join-Path $SourceDir "runtime\python-3.12-x64.exe")
        $PythonExe = Get-HivePython312
    } elseif (-not $PythonExe -and $AllowOnlinePrerequisites) {
        winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [Environment]::GetEnvironmentVariable("Path", "User")
        $PythonExe = Get-HivePython312
    }
    if (-not $PythonExe) { throw "64-bit Python 3.12 is unavailable after prerequisite installation." }

    if ([string]::IsNullOrWhiteSpace($LogFolder)) { $LogFolder = Find-MaestroLogFolder }
    if ([string]::IsNullOrWhiteSpace($LogFolder)) { $LogFolder = Read-Host "Maestro log folder" }
    if (-not (Test-Path -LiteralPath $LogFolder -PathType Container)) {
        throw "Maestro log folder does not exist: $LogFolder"
    }
    $MqttTest = Test-NetConnection -ComputerName $BrokerHost -Port $BrokerPort -WarningAction SilentlyContinue
    if (-not $MqttTest.TcpTestSucceeded) {
        throw "Cannot reach secure MQTT at $BrokerHost`:$BrokerPort. Check the central PC and firewall first."
    }

    Remove-Item -LiteralPath $StageDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path "$StageDir\src", "$StageDir\config", "$StageDir\logs", "$StageDir\certs" | Out-Null
    Copy-Item -LiteralPath "$SourceDir\src\maestro_agent.py", "$SourceDir\src\mqtt_client.py" -Destination "$StageDir\src" -Force
    Copy-Item -LiteralPath "$BundleRoot\certs\ca.crt", "$BundleRoot\certs\client.crt", "$BundleRoot\certs\client.key" -Destination "$StageDir\certs" -Force
    & icacls "$StageDir\certs" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not secure the machine certificate directory." }

    $EscapedLogFolder = $LogFolder.Replace("\", "\\")
    @"
mqtt:
  broker_host: "$BrokerHost"
  broker_port: $BrokerPort
  keepalive: 60
  topic_prefix: "hive/machines"
  require_tls: true
  tls:
    enabled: true
    ca_cert: "../certs/ca.crt"
    client_cert: "../certs/client.crt"
    client_key: "../certs/client.key"
maestro_agents:
  - machine_key: "$MachineKey"
    label: "$MachineKey"
    host: "localhost"
    log_folder: "$EscapedLogFolder"
    cnc_folder: null
"@ | Set-Content "$StageDir\config\machines.yaml" -Encoding UTF8

    & $PythonExe -m venv "$StageDir\.venv"
    if ($LASTEXITCODE -ne 0) { throw "Could not create the machine-agent Python environment." }
    if ($OfflinePayloadReady) {
        & "$StageDir\.venv\Scripts\python.exe" -m pip install --no-index --find-links "$SourceDir\wheels" -r "$SourceDir\requirements-agent.txt"
    } elseif ($AllowOnlinePrerequisites) {
        & "$StageDir\.venv\Scripts\python.exe" -m pip install "paho-mqtt>=2.1,<3" "PyYAML>=6,<7"
    }
    if ($LASTEXITCODE -ne 0) { throw "Machine-agent dependency installation failed." }
    & "$StageDir\.venv\Scripts\python.exe" "$StageDir\src\maestro_agent.py" --machine $MachineKey --config "$StageDir\config\machines.yaml" --check-mqtt
    if ($LASTEXITCODE -ne 0) { throw "Machine-agent mutual-TLS MQTT verification failed." }

    @"
@echo off
cd /d C:\HIVE-Agent
set PYTHONPATH=src
.venv\Scripts\python.exe src\maestro_agent.py --machine $MachineKey --config config\machines.yaml >> logs\agent.log 2>&1
"@ | Set-Content "$StageDir\start-agent.cmd" -Encoding ASCII

    $TaskName = "HIVE Agent - $MachineKey"
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath "$InstallDir\logs") {
        Copy-Item -Path "$InstallDir\logs\*" -Destination "$StageDir\logs" -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $PreviousDir -Recurse -Force -ErrorAction SilentlyContinue
    $MovedPrevious = $false
    try {
        if (Test-Path -LiteralPath $InstallDir) {
            Move-Item -LiteralPath $InstallDir -Destination $PreviousDir
            $MovedPrevious = $true
        }
        Move-Item -LiteralPath $StageDir -Destination $InstallDir
        schtasks /Create /TN $TaskName /SC ONSTART /RU SYSTEM /RL HIGHEST /TR "$InstallDir\start-agent.cmd" /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not register the HIVE machine-agent startup task." }
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 2
        $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        if ($Task.State -ne "Running") { throw "HIVE agent task entered state $($Task.State)." }
        Remove-Item -LiteralPath $PreviousDir -Recurse -Force -ErrorAction SilentlyContinue
    } catch {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
        if ($MovedPrevious -and (Test-Path -LiteralPath $PreviousDir)) {
            Move-Item -LiteralPath $PreviousDir -Destination $InstallDir
            Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        } else {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        }
        throw
    }

    $LatestLog = Get-ChildItem -LiteralPath $LogFolder -Filter *.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $AnalysisPath = $null
    if ($LatestLog) {
        $SamplePath = "$InstallDir\logs\commissioning-sample.txt"
        Get-Content -LiteralPath $LatestLog.FullName -Tail 500 | Set-Content $SamplePath -Encoding UTF8
        Write-Host "Commissioning sample: $SamplePath" -ForegroundColor Cyan
    }
    if ($LatestLog -and -not [string]::IsNullOrWhiteSpace($CentralApiBase)) {
        try {
            $Sample = (Get-Content -LiteralPath $LatestLog.FullName -Tail 500) -join "`n"
            $Body = @{ machine_key=$MachineKey; log_text=$Sample; persist=$false; site_timezone="Asia/Kolkata" } | ConvertTo-Json
            if (-not $CentralApiBase.StartsWith("https://")) { throw "CentralApiBase must use HTTPS." }
            if (-not $AgentToken) { $AgentToken = Read-Host "HIVE machine integration key" -AsSecureString }
            $PlainAgentToken = ConvertFrom-HiveSecureString $AgentToken
            try {
                $Headers = @{ Authorization = "Bearer $PlainAgentToken" }
                $Analysis = Invoke-RestMethod -Method Post -Uri "$($CentralApiBase.TrimEnd('/'))/api/commissioning/log/analyze" -Headers $Headers -ContentType "application/json" -Body $Body -TimeoutSec 20
            } finally { $Headers = $null; $PlainAgentToken = $null }
            $AnalysisPath = "$InstallDir\logs\commissioning-analysis.json"
            $Analysis | ConvertTo-Json -Depth 8 | Set-Content $AnalysisPath -Encoding UTF8
            if (-not $Analysis.ready_to_replay) { Write-Warning "Agent installed, but this log format has not passed HIVE parser checks. Open Commission in HIVE." }
        } catch { Write-Warning "Agent installed, but central log analysis failed: $($_.Exception.Message)" }
    }

    Write-Host ""
    Write-Host "HIVE machine agent installed for $MachineKey." -ForegroundColor Green
    Write-Host "Verified offline runtime and mutual-TLS MQTT identity are active." -ForegroundColor Green
    Write-Host "It should appear online in Central HIVE Diagnostics within 1-3 minutes."
    Write-Host "Agent log: $InstallDir\logs\agent.log"
    if ($AnalysisPath) { Write-Host "Commissioning analysis: $AnalysisPath" }
} finally {
    Remove-Item -LiteralPath $StageDir -Recurse -Force -ErrorAction SilentlyContinue
    if ($TemporaryBundleRoot) { Remove-Item -LiteralPath $TemporaryBundleRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
