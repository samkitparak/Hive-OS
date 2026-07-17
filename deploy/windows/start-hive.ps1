param(
    [string]$InstallDir = "C:\HIVE-OS",
    [int]$Port = 8000
)

# Long-running central supervisor. Task Scheduler owns this process; this
# process owns the broker and API children so lifecycle scripts can stop both.
$ErrorActionPreference = "Stop"
$LogDir = Join-Path $InstallDir "logs"
$PidDir = Join-Path $InstallDir "data\runtime"
New-Item -ItemType Directory -Force -Path $LogDir, $PidDir | Out-Null

$Mosquitto = (Get-Command mosquitto.exe -ErrorAction SilentlyContinue).Source
if (-not $Mosquitto) {
    $Candidate = "C:\Program Files\mosquitto\mosquitto.exe"
    if (Test-Path -LiteralPath $Candidate) { $Mosquitto = $Candidate }
}
if (-not $Mosquitto) { throw "mosquitto.exe is unavailable." }

$Uvicorn = Join-Path $InstallDir ".venv\Scripts\uvicorn.exe"
if (-not (Test-Path -LiteralPath $Uvicorn)) { throw "HIVE Python environment is unavailable." }
$BrokerConfig = Join-Path $InstallDir "config\mosquitto.conf"

$Broker = $null
$Backend = $null
try {
    $Broker = Start-Process -FilePath $Mosquitto -ArgumentList @("-c", $BrokerConfig) `
        -WorkingDirectory $InstallDir -RedirectStandardOutput "$LogDir\mqtt.log" `
        -RedirectStandardError "$LogDir\mqtt-error.log" -PassThru -WindowStyle Hidden
    $env:PYTHONPATH = Join-Path $InstallDir "src"
    $Backend = Start-Process -FilePath $Uvicorn `
        -ArgumentList @("src.main:app", "--host", "127.0.0.1", "--port", "$Port") `
        -WorkingDirectory $InstallDir -RedirectStandardOutput "$LogDir\backend.log" `
        -RedirectStandardError "$LogDir\backend-error.log" -PassThru -WindowStyle Hidden
    Set-Content "$PidDir\mosquitto.pid" $Broker.Id -Encoding ASCII -NoNewline
    Set-Content "$PidDir\backend.pid" $Backend.Id -Encoding ASCII -NoNewline
    Set-Content "$PidDir\supervisor.pid" $PID -Encoding ASCII -NoNewline

    while (-not $Broker.HasExited -and -not $Backend.HasExited) {
        Start-Sleep -Seconds 2
        $Broker.Refresh()
        $Backend.Refresh()
    }
    if ($Broker.HasExited) { throw "MQTT broker exited with code $($Broker.ExitCode)." }
    throw "HIVE backend exited with code $($Backend.ExitCode)."
} finally {
    foreach ($Process in @($Backend, $Broker)) {
        if ($Process -and -not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item "$PidDir\*.pid" -Force -ErrorAction SilentlyContinue
}
