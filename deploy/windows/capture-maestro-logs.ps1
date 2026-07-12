# Captures recent Maestro log evidence for parser setup.

$LogFolder = Read-Host "Maestro log folder [C:\SCM\Maestro\Logs]"
if ([string]::IsNullOrWhiteSpace($LogFolder)) { $LogFolder = "C:\SCM\Maestro\Logs" }
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
