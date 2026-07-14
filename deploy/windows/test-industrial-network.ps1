param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"

function Test-Endpoint([string]$Name, [string]$Protocol, [string]$Endpoint) {
    if ($Protocol -eq "mqtt_json") {
        Write-Host "[SKIP] $Name uses central MQTT" -ForegroundColor DarkGray
        return $true
    }

    if (-not $Endpoint) {
        Write-Host "[WAIT] $Name has no endpoint" -ForegroundColor Yellow
        return $false
    }

    if ($Protocol -eq "opcua") {
        $uri = [Uri]$Endpoint
        $hostName = $uri.Host
        $port = $uri.Port
    } elseif ($Protocol -eq "modbus_tcp") {
        $parts = $Endpoint.Split(":")
        $hostName = $parts[0]
        $port = if ($parts.Count -gt 1) { [int]$parts[1] } else { 502 }
    }

    $reachable = Test-NetConnection -ComputerName $hostName -Port $port `
        -InformationLevel Quiet -WarningAction SilentlyContinue
    if ($reachable) {
        Write-Host "[OK] $Name $hostName`:$port reachable" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] $Name $hostName`:$port unreachable" -ForegroundColor Red
    }
    return $reachable
}

Write-Host "HIVE OS industrial network preflight" -ForegroundColor Cyan
$snapshot = Invoke-RestMethod -Uri "$BaseUrl/api/industrial/snapshot" -TimeoutSec 10
$results = @()
foreach ($profile in $snapshot.profiles) {
    $results += Test-Endpoint $profile.name $profile.protocol $profile.endpoint
}

Write-Host ""
Write-Host ("Reachable/configured: {0}/{1}" -f (($results | Where-Object { $_ }).Count), $results.Count)
Write-Host "Use Commission > Industrial I/O for protocol reads and approval. This script never writes to a device."

if (($results | Where-Object { -not $_ }).Count -gt 0) { exit 1 }
