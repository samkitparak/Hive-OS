# Captures recent Maestro log evidence for parser setup.

param(
    [string]$MachineKey,
    [string]$CentralHost,
    [string]$LogFolder,
    [int]$CentralPort = 8000,
    [switch]$ImportValidated
)

if ([string]::IsNullOrWhiteSpace($LogFolder)) {
    $Candidates = @(
        "C:\SCM\Maestro\Logs",
        "C:\Program Files\SCM Group\Maestro\Logs",
        "C:\ProgramData\SCM Group\Maestro\Logs",
        "D:\SCM\Maestro\Logs"
    )
    $LogFolder = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($LogFolder)) { $LogFolder = Read-Host "Maestro log folder" }
if (-not (Test-Path $LogFolder)) { throw "Folder not found: $LogFolder" }

$Latest = Get-ChildItem $LogFolder -Filter *.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $Latest) { throw "No .log files found in $LogFolder" }

$Output = Join-Path $env:USERPROFILE "Desktop\hive-maestro-sample.txt"
@(
    "Source: $($Latest.FullName)"
    "Captured: $(Get-Date -Format o)"
    ""
    Get-Content $Latest.FullName -Tail 300
) | Set-Content $Output -Encoding UTF8

Write-Host "Captured recent Maestro lines to $Output" -ForegroundColor Green

if ([string]::IsNullOrWhiteSpace($CentralHost)) { $CentralHost = Read-Host "Central HIVE IP (leave blank to only save file)" }
if (-not [string]::IsNullOrWhiteSpace($CentralHost)) {
    if ([string]::IsNullOrWhiteSpace($MachineKey)) { $MachineKey = Read-Host "Machine key" }
    $Body = @{
        machine_key = $MachineKey
        log_text = ((Get-Content $Latest.FullName -Tail 500) -join "`n")
        persist = [bool]$ImportValidated
        site_timezone = "Asia/Kolkata"
    } | ConvertTo-Json
    $Result = Invoke-RestMethod -Method Post -Uri "http://$CentralHost`:$CentralPort/api/commissioning/log/analyze" `
        -ContentType "application/json" -Body $Body -TimeoutSec 20
    $AnalysisPath = Join-Path $env:USERPROFILE "Desktop\hive-maestro-analysis.json"
    $Result | ConvertTo-Json -Depth 8 | Set-Content $AnalysisPath -Encoding UTF8
    Write-Host "Recognition: $([math]::Round($Result.recognition_rate * 100))%" -ForegroundColor Cyan
    Write-Host "Ready to replay: $($Result.ready_to_replay)"
    Write-Host "Analysis: $AnalysisPath"
}
