param(
    [switch]$SkipPrerequisites,
    [string]$OfflineWheelDir = "",
    [switch]$DashboardPrebuilt,
    [string]$PythonExe = "python"
)

# HIVE OS central/Cabinet Vision PC installer. Normal mode uses the internet;
# the verified offline wrapper invokes prerequisite-free mode.

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

function New-SecureToken {
    $Bytes = New-Object byte[] 32
    $Rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Rng.GetBytes($Bytes)
    } finally {
        $Rng.Dispose()
    }
    return [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

Require-Admin
if (-not $SkipPrerequisites) {
    Ensure-WingetPackage "Python.Python.3.12" "python"
    Ensure-WingetPackage "OpenJS.NodeJS.LTS" "node"
    Ensure-WingetPackage "EclipseMosquitto.Mosquitto" "mosquitto"
}
if (-not (Get-Command ssh-keygen.exe -ErrorAction SilentlyContinue)) {
    if ($SkipPrerequisites) { throw "OpenSSH Client is absent from the verified offline installation." }
    $SshClient = Get-WindowsCapability -Online | Where-Object Name -Like 'OpenSSH.Client*' | Select-Object -First 1
    if (-not $SshClient) { throw "Windows OpenSSH Client is required for remote machine setup." }
    if ($SshClient.State -ne 'Installed') { Add-WindowsCapability -Online -Name $SshClient.Name | Out-Null }
}
if (-not $SkipPrerequisites -and -not (Get-OdbcDriver -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "ODBC Driver 18 for SQL Server*" })) {
    Write-Host "Installing Microsoft ODBC Driver 18 for SQL Server..."
    winget install --id Microsoft.msodbcsql.18 --exact --silent `
        --accept-package-agreements --accept-source-agreements
}
Refresh-Path

$CvFolder = Read-Host "Cabinet Vision export folder [C:\CabinetVision\Export]"
if ([string]::IsNullOrWhiteSpace($CvFolder)) { $CvFolder = "C:\CabinetVision\Export" }
$DetectedAddress = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Sort-Object InterfaceMetric | Select-Object -First 1 -ExpandProperty IPAddress
$BrokerAddress = Read-Host "Central PC static LAN IP or DNS name [$DetectedAddress]"
if ([string]::IsNullOrWhiteSpace($BrokerAddress)) { $BrokerAddress = $DetectedAddress }
if ([string]::IsNullOrWhiteSpace($BrokerAddress)) { throw "A stable central PC LAN IP or DNS name is required." }

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
robocopy $SourceDir $InstallDir /E /XD .git .pytest_cache node_modules dist dashboard\node_modules dashboard\dist `
    /XF *.db *.db-shm *.db-wal hive-bootstrap.token hive-agent.token | Out-Null
New-Item -ItemType Directory -Force -Path "$InstallDir\logs" | Out-Null
New-Item -ItemType Directory -Force -Path "$InstallDir\data" | Out-Null
New-Item -ItemType Directory -Force -Path "$InstallDir\data\ssh" | Out-Null

$SshIdentity = "$InstallDir\data\ssh\id_ed25519"
if (-not (Test-Path -LiteralPath $SshIdentity)) {
    $KeygenArgs = "-q -t ed25519 -N `"`" -C `"HIVE OS deployment`" -f `"$SshIdentity`""
    $Keygen = Start-Process -FilePath ssh-keygen.exe -ArgumentList $KeygenArgs -Wait -PassThru -NoNewWindow
    if ($Keygen.ExitCode -ne 0) { throw "Could not generate the HIVE SSH deployment identity." }
}
& icacls.exe "$InstallDir\data\ssh" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null

$MachineBootstrapDir = "$env:PUBLIC\Desktop\HIVE Machine Bootstrap"
New-Item -ItemType Directory -Force -Path $MachineBootstrapDir | Out-Null
Copy-Item "$InstallDir\deploy\windows\enable-hive-ssh.ps1" "$MachineBootstrapDir\" -Force
Copy-Item "$SshIdentity.pub" "$MachineBootstrapDir\hive-deploy.pub" -Force
@"
Open an Administrator PowerShell window in this folder and run:

Set-ExecutionPolicy -Scope Process Bypass
.\enable-hive-ssh.ps1 -PublicKeyPath .\hive-deploy.pub

Then compare the printed SSH fingerprint with HIVE Setup before approving it.
"@ | Set-Content "$MachineBootstrapDir\README.txt" -Encoding ASCII

$BootstrapTokenPath = "$InstallDir\data\hive-bootstrap.token"
if (-not (Test-Path $BootstrapTokenPath)) {
    New-SecureToken | Set-Content $BootstrapTokenPath -Encoding ASCII -NoNewline
}
& icacls $BootstrapTokenPath /inheritance:r /grant:r "*S-1-5-18:F" "*S-1-5-32-544:F" | Out-Null

Set-Location $InstallDir
& $PythonExe -m venv .venv
if ($OfflineWheelDir) {
    $ResolvedWheels = (Resolve-Path -LiteralPath $OfflineWheelDir).Path
    & "$InstallDir\.venv\Scripts\python.exe" -m pip install --no-index --find-links $ResolvedWheels -r requirements.txt
} else {
    & "$InstallDir\.venv\Scripts\python.exe" -m pip install --upgrade pip
    & "$InstallDir\.venv\Scripts\pip.exe" install -r requirements.txt
}

$env:PYTHONPATH = "$InstallDir\src"
if (-not (Test-Path "$InstallDir\data\mqtt-pki\ca.key")) {
    & "$InstallDir\.venv\Scripts\python.exe" "$InstallDir\src\mqtt_security.py" $BrokerAddress `
        --additional-host $env:COMPUTERNAME --port 8883
} else {
    Write-Host "Preserving the existing MQTT certificate authority and machine identities."
}
& icacls "$InstallDir\data\mqtt-pki" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null

if ($DashboardPrebuilt) {
    $BuiltDashboard = Join-Path $SourceDir "dashboard\dist"
    if (-not (Test-Path -LiteralPath "$BuiltDashboard\index.html")) {
        throw "The offline release does not contain a built dashboard."
    }
    New-Item -ItemType Directory -Force -Path "$InstallDir\dashboard\dist" | Out-Null
    robocopy $BuiltDashboard "$InstallDir\dashboard\dist" /E | Out-Null
} else {
    Set-Location "$InstallDir\dashboard"
    npm ci
    npm run build
}

$ConfigPath = "$InstallDir\config\machines.yaml"
$Config = Get-Content $ConfigPath -Raw
$EscapedCvFolder = $CvFolder.Replace("\", "\\")
$Config = $Config -replace 'cv_watch_folder:.*', "cv_watch_folder: `"$EscapedCvFolder`""
Set-Content $ConfigPath $Config -Encoding UTF8

Stop-Service mosquitto -ErrorAction SilentlyContinue
Set-Service mosquitto -StartupType Disabled -ErrorAction SilentlyContinue

$TaskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\deploy\windows\start-hive.ps1`" -InstallDir `"$InstallDir`""
schtasks /Create /TN "HIVE OS Central" /SC ONSTART /RU SYSTEM /RL HIGHEST /TR $TaskCommand /F | Out-Null

Remove-NetFirewallRule -DisplayName "HIVE OS API" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "HIVE OS MQTT" -Direction Inbound `
    -Protocol TCP -LocalPort 8883 -RemoteAddress LocalSubnet `
    -Action Allow -ErrorAction SilentlyContinue | Out-Null

Start-ScheduledTask -TaskName "HIVE OS Central"
Start-Sleep -Seconds 2

@"
[InternetShortcut]
URL=http://localhost:8000
"@ | Set-Content "$env:PUBLIC\Desktop\HIVE OS.url" -Encoding ASCII

Write-Host ""
Write-Host "HIVE OS central installation complete." -ForegroundColor Green
Write-Host "Dashboard: http://localhost:8000"
Write-Host "Secure MQTT: $BrokerAddress`:8883"
if (Test-Path $BootstrapTokenPath) {
    Write-Host "One-time administrator token: $(Get-Content $BootstrapTokenPath -Raw)" -ForegroundColor Yellow
    Write-Host "This token is deleted automatically after the first administrator is created."
}
Write-Host "Diagnostics: open the dashboard and click Diagnostics."
Write-Host "Logs: $InstallDir\logs"
Write-Host "Machine SSH bootstrap folder: $MachineBootstrapDir"
