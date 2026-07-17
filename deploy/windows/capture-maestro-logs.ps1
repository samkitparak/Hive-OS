# Captures recent Maestro log evidence for parser setup.

param(
    [string]$MachineKey,
    [string]$CentralApiBase,
    [Security.SecureString]$AgentToken,
    [string]$LogFolder
)

function ConvertFrom-HiveSecureString([Security.SecureString]$Value) {
    $Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    }
}

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

if (-not [string]::IsNullOrWhiteSpace($CentralApiBase)) {
    if (-not $CentralApiBase.StartsWith("https://")) { throw "CentralApiBase must use HTTPS." }
    if (-not $AgentToken) { $AgentToken = Read-Host "HIVE machine integration key" -AsSecureString }
    if ([string]::IsNullOrWhiteSpace($MachineKey)) { $MachineKey = Read-Host "Machine key" }
    $Body = @{
        machine_key = $MachineKey
        log_text = ((Get-Content $Latest.FullName -Tail 500) -join "`n")
        persist = $false
        site_timezone = "Asia/Kolkata"
    } | ConvertTo-Json
    $PlainAgentToken = ConvertFrom-HiveSecureString $AgentToken
    try {
        $Headers = @{ Authorization = "Bearer $PlainAgentToken" }
        $Result = Invoke-RestMethod -Method Post -Uri "$($CentralApiBase.TrimEnd('/'))/api/commissioning/log/analyze" `
            -Headers $Headers -ContentType "application/json" -Body $Body -TimeoutSec 20
    } finally {
        $Headers = $null
        $PlainAgentToken = $null
    }
    $AnalysisPath = Join-Path $env:USERPROFILE "Desktop\hive-maestro-analysis.json"
    $Result | ConvertTo-Json -Depth 8 | Set-Content $AnalysisPath -Encoding UTF8
    Write-Host "Recognition: $([math]::Round($Result.recognition_rate * 100))%" -ForegroundColor Cyan
    Write-Host "Ready to replay: $($Result.ready_to_replay)"
    Write-Host "Analysis: $AnalysisPath"
}
