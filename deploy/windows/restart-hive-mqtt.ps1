# Restart the HIVE-owned Mosquitto process after a certificate revocation.
# Run from an Administrator PowerShell window on the central PC.

$ErrorActionPreference = "Stop"
$InstallDir = "C:\HIVE-OS"
$ConfigPath = "$InstallDir\config\mosquitto.conf"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this command from an Administrator PowerShell window."
}
if (-not (Test-Path $ConfigPath)) { throw "HIVE Mosquitto configuration is missing: $ConfigPath" }

Get-CimInstance Win32_Process -Filter "Name = 'mosquitto.exe'" |
    Where-Object { $_.CommandLine -like "*$ConfigPath*" -or $_.CommandLine -like "*config\mosquitto.conf*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

$Mosquitto = (Get-Command mosquitto -ErrorAction Stop).Source
$Process = Start-Process $Mosquitto -ArgumentList @("-c", $ConfigPath) -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2
if ($Process.HasExited) { throw "Mosquitto failed to restart. Check C:\HIVE-OS\logs\mosquitto.log." }
Remove-Item "$InstallDir\data\mqtt-pki\restart-required" -Force -ErrorAction SilentlyContinue
Write-Host "HIVE MQTT restarted with the current certificate revocation list." -ForegroundColor Green
