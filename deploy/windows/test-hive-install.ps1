param(
    [string]$HostName = "localhost",
    [int]$DashboardPort = 8000,
    [int]$ApiPort = 8000,
    [int]$MqttPort = 1883
)

# HIVE OS post-install health checker.
# Run after install from PowerShell. It does not modify the system.

$ErrorActionPreference = "Continue"

function Test-Http($Name, $Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
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
$checks += Test-Http "API machines" "http://$HostName`:$ApiPort/api/machines"
$checks += Test-Http "API diagnostics" "http://$HostName`:$ApiPort/api/diagnostics"
$checks += Test-Http "API data quality" "http://$HostName`:$ApiPort/api/data-quality"
$checks += Test-Http "API optimization" "http://$HostName`:$ApiPort/api/optimization"
$checks += Test-Port "MQTT" $HostName $MqttPort

if (Test-Path "C:\HIVE-OS") {
    Write-Host "[OK] C:\HIVE-OS exists" -ForegroundColor Green
    $checks += $true
} else {
    Write-Host "[WARN] C:\HIVE-OS not found on this PC" -ForegroundColor Yellow
}

if (Test-Path "C:\HIVE-OS\logs") {
    Write-Host "[OK] C:\HIVE-OS\logs exists" -ForegroundColor Green
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
