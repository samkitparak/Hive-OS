param(
    [Parameter(Mandatory=$true)][string]$BackupPath,
    [string]$InstallDir = "C:\HIVE-OS",
    [string]$BackupDir = "C:\HIVE-Backups"
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\hive-lifecycle-common.ps1"
Require-HiveAdmin
$BackupPath = (Resolve-Path -LiteralPath $BackupPath).Path
$Python = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { throw "HIVE Python environment is unavailable." }

$Stage = Join-Path $env:TEMP "hive-restore-$([guid]::NewGuid().ToString('N'))"
$OldDb = "$InstallDir\hive.db.pre-restore"
$OldWal = "$InstallDir\hive.db-wal.pre-restore"
$OldShm = "$InstallDir\hive.db-shm.pre-restore"
$OldConfig = "$InstallDir\config.pre-restore"
$OldData = "$InstallDir\data.pre-restore"
$MutationStarted = $false
$DbMoved = $false
$ConfigMoved = $false
$DataMoved = $false
$DataReplaced = $false
try {
    & $Python "$InstallDir\src\resilience.py" verify $BackupPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Backup verification failed." }
    & "$PSScriptRoot\backup-hive.ps1" -InstallDir $InstallDir -BackupDir $BackupDir -Actor "pre-restore"
    & $Python "$InstallDir\src\resilience.py" extract $BackupPath $Stage | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Backup extraction failed." }

    Stop-HiveCentral -InstallDir $InstallDir
    $MutationStarted = $true
    Remove-Item $OldDb, $OldWal, $OldShm, $OldConfig, $OldData -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item "$InstallDir\hive.db" $OldDb
    $DbMoved = $true
    if (Test-Path "$InstallDir\hive.db-wal") { Move-Item "$InstallDir\hive.db-wal" $OldWal }
    if (Test-Path "$InstallDir\hive.db-shm") { Move-Item "$InstallDir\hive.db-shm" $OldShm }
    Move-Item "$InstallDir\config" $OldConfig
    $ConfigMoved = $true
    if (Test-Path "$InstallDir\data") { Move-Item "$InstallDir\data" $OldData; $DataMoved = $true }
    Copy-Item "$Stage\database\hive.db" "$InstallDir\hive.db"
    Copy-Item "$Stage\config" "$InstallDir\config" -Recurse
    New-Item -ItemType Directory -Force -Path "$InstallDir\data" | Out-Null
    $DataReplaced = $true
    if (Test-Path "$Stage\data") { Copy-Item "$Stage\data\*" "$InstallDir\data" -Recurse -Force }
    Start-HiveCentral -InstallDir $InstallDir
    if (-not (Test-HiveHealth)) { throw "Restored HIVE did not pass its health check." }
    Remove-Item $OldDb, $OldWal, $OldShm, $OldConfig, $OldData -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "HIVE restore completed and passed health validation." -ForegroundColor Green
} catch {
    Write-Warning "Restore failed; returning to the pre-restore state. $($_.Exception.Message)"
    if ($MutationStarted) { Stop-HiveCentral -InstallDir $InstallDir }
    if ($DbMoved -and (Test-Path $OldDb)) {
        Remove-Item "$InstallDir\hive.db", "$InstallDir\hive.db-wal", "$InstallDir\hive.db-shm" -Force -ErrorAction SilentlyContinue
        Move-Item $OldDb "$InstallDir\hive.db"
        if (Test-Path $OldWal) { Move-Item $OldWal "$InstallDir\hive.db-wal" }
        if (Test-Path $OldShm) { Move-Item $OldShm "$InstallDir\hive.db-shm" }
    }
    if ($ConfigMoved -and (Test-Path $OldConfig)) {
        Remove-Item "$InstallDir\config" -Recurse -Force -ErrorAction SilentlyContinue
        Move-Item $OldConfig "$InstallDir\config"
    }
    if ($DataMoved -or $DataReplaced) {
        Remove-Item "$InstallDir\data" -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path $OldData) { Move-Item $OldData "$InstallDir\data" }
    }
    if ($MutationStarted) { Start-HiveCentral -InstallDir $InstallDir }
    throw
} finally {
    Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
}
