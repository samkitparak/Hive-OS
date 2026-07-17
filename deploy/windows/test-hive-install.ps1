param(
    [string]$HostName = "localhost",
    [int]$DashboardPort = 8000,
    [int]$ApiPort = 8000,
    [int]$MqttPort = 8883
)

# HIVE OS post-install health checker.
# Run after install from PowerShell. It does not modify the system.

$ErrorActionPreference = "Continue"

function Test-Http($Name, $Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
            Write-Host "[OK] $Name $Url" -ForegroundColor Green
            return $true
        }
        Write-Host "[FAIL] $Name returned $($response.StatusCode)" -ForegroundColor Red
        return $false
    } catch {
        Write-Host "[FAIL] $Name $Url - $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

function Test-ProtectedHttp($Name, $Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
            Write-Host "[OK] $Name reachable (authenticated session present)" -ForegroundColor Green
            return $true
        }
    } catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        if ($status -in @(401, 403, 428)) {
            Write-Host "[OK] $Name reachable and protected (HTTP $status)" -ForegroundColor Green
            return $true
        }
        Write-Host "[FAIL] $Name $Url - $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
    Write-Host "[FAIL] $Name returned $($response.StatusCode)" -ForegroundColor Red
    return $false
}

function Test-Port($Name, $ComputerName, $Port) {
    $result = Test-NetConnection -ComputerName $ComputerName -Port $Port -WarningAction SilentlyContinue
    if ($result.TcpTestSucceeded) {
        Write-Host "[OK] $Name port $Port reachable" -ForegroundColor Green
        return $true
    }
    Write-Host "[FAIL] $Name port $Port not reachable" -ForegroundColor Red
    return $false
}

Write-Host ""
Write-Host "HIVE OS install health check" -ForegroundColor Cyan
Write-Host "Target: $HostName"
Write-Host ""

$checks = @()
$checks += Test-Http "Dashboard" "http://$HostName`:$DashboardPort"
$checks += Test-Http "API health" "http://$HostName`:$ApiPort/api/health"
$checks += Test-Http "Access-control status" "http://$HostName`:$ApiPort/api/auth/status"
$checks += Test-ProtectedHttp "API machines" "http://$HostName`:$ApiPort/api/machines"
$checks += Test-ProtectedHttp "API diagnostics" "http://$HostName`:$ApiPort/api/diagnostics"
$checks += Test-ProtectedHttp "API data quality" "http://$HostName`:$ApiPort/api/data-quality"
$checks += Test-ProtectedHttp "API optimization" "http://$HostName`:$ApiPort/api/optimization"
$checks += Test-ProtectedHttp "API improvement learning" "http://$HostName`:$ApiPort/api/improvements"
$checks += Test-ProtectedHttp "API root-cause diagnostics" "http://$HostName`:$ApiPort/api/root-causes"
$checks += Test-ProtectedHttp "API alert management" "http://$HostName`:$ApiPort/api/alerts"
$checks += Test-ProtectedHttp "API connectors" "http://$HostName`:$ApiPort/api/connectors/snapshot"
$checks += Test-ProtectedHttp "API industrial I/O" "http://$HostName`:$ApiPort/api/industrial/snapshot"
$checks += Test-ProtectedHttp "API MQTT trust" "http://$HostName`:$ApiPort/api/mqtt-security"
$checks += Test-ProtectedHttp "API warehouse" "http://$HostName`:$ApiPort/api/inventory/snapshot"
$checks += Test-ProtectedHttp "API procurement" "http://$HostName`:$ApiPort/api/procurement/snapshot"
$checks += Test-Port "MQTT" $HostName $MqttPort

$odbc = Get-OdbcDriver -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "ODBC Driver 18 for SQL Server*" }
if ($odbc) {
    Write-Host "[OK] Microsoft ODBC Driver 18 installed" -ForegroundColor Green
    $checks += $true
} else {
    Write-Host "[WARN] Microsoft ODBC Driver 18 not found; Cabinet Vision SQL will remain unavailable" -ForegroundColor Yellow
}

try {
    & python -c "import pymodbus, asyncua" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "protocol import failed" }
    Write-Host "[OK] Modbus and OPC-UA Python clients installed" -ForegroundColor Green
    $checks += $true
} catch {
    Write-Host "[FAIL] pymodbus or asyncua is unavailable" -ForegroundColor Red
    $checks += $false
}

if (Test-Path "C:\HIVE-OS") {
    Write-Host "[OK] C:\HIVE-OS exists" -ForegroundColor Green
    $checks += $true
} else {
    Write-Host "[WARN] C:\HIVE-OS not found on this PC" -ForegroundColor Yellow
}

if (Test-Path "C:\HIVE-OS\logs") {
    Write-Host "[OK] C:\HIVE-OS\logs exists" -ForegroundColor Green
}

if ((Test-Path "C:\HIVE-OS\data\mqtt-pki\ca.key") -and
    (Select-String -Path "C:\HIVE-OS\config\mosquitto.conf" -Pattern "require_certificate true" -Quiet)) {
    Write-Host "[OK] MQTT mutual-TLS authority and broker policy exist" -ForegroundColor Green
    $checks += $true
} else {
    Write-Host "[FAIL] MQTT mutual-TLS files or policy are missing" -ForegroundColor Red
    $checks += $false
}

$centralTask = Get-ScheduledTask -TaskName "HIVE OS Central" -ErrorAction SilentlyContinue
if ($centralTask) {
    Write-Host "[OK] Scheduled task HIVE OS Central exists" -ForegroundColor Green
} else {
    Write-Host "[WARN] Scheduled task HIVE OS Central not found" -ForegroundColor Yellow
}

$passed = ($checks | Where-Object { $_ -eq $true }).Count
$failed = ($checks | Where-Object { $_ -eq $false }).Count
Write-Host ""
Write-Host "Result: $passed passed, $failed failed"

if ($failed -gt 0) {
    exit 1
}
