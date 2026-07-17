param(
    [Parameter(Mandatory=$true)][string]$BundlePath,
    [string]$InstallDir = "C:\HIVE-OS",
    [string]$BackupDir = "C:\HIVE-Backups"
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\hive-lifecycle-common.ps1"
Require-HiveAdmin
if (-not (Test-Path -LiteralPath "$InstallDir\.venv\Scripts\python.exe")) {
    throw "HIVE is not installed at $InstallDir."
}

$Work = Join-Path $env:TEMP "hive-upgrade-$([guid]::NewGuid().ToString('N'))"
$BundleRoot = $null
$Next = "$InstallDir.next"
$Previous = "$InstallDir.previous"
$Failed = "$InstallDir.failed"
$Swapped = $false
$OldTaskXml = $null
$PreBackup = $null
$OldStopped = $false
$OldMoved = $false
try {
    New-Item -ItemType Directory -Force -Path $Work | Out-Null
    $ResolvedBundle = (Resolve-Path -LiteralPath $BundlePath).Path
    if (Test-Path -LiteralPath $ResolvedBundle -PathType Container) {
        $BundleRoot = $ResolvedBundle
    } else {
        if ([IO.Path]::GetExtension($ResolvedBundle) -ne ".zip") { throw "Upgrade bundle must be a folder or ZIP." }
        $Sidecar = "$ResolvedBundle.sha256"
        if (-not (Test-Path -LiteralPath $Sidecar)) { throw "Release ZIP SHA-256 sidecar is missing." }
        $Expected = (Get-Content -LiteralPath $Sidecar -Raw).Trim().Split()[0].ToLowerInvariant()
        $Actual = (Get-FileHash -LiteralPath $ResolvedBundle -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($Expected -ne $Actual) { throw "Release ZIP failed SHA-256 verification." }
        $BundleRoot = Join-Path $Work "bundle"
        Expand-Archive -LiteralPath $ResolvedBundle -DestinationPath $BundleRoot
    }
    $Manifest = Test-HiveReleaseManifest -BundleRoot $BundleRoot

    $CurrentPython = "$InstallDir\.venv\Scripts\python.exe"
    $CurrentIdentity = & $CurrentPython -c "import sys, struct; print(f'{sys.version_info.major}.{sys.version_info.minor}-{struct.calcsize(`"P`") * 8}')"
    if ($CurrentIdentity -ne "3.12-64") { throw "Offline upgrade requires the installed 64-bit Python 3.12 runtime." }
    $CurrentVersion = & $CurrentPython -c "import sys; sys.path.insert(0, r'$InstallDir\src'); import main; print(main.APP_VERSION)"
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    & $CurrentPython "$BundleRoot\app\src\resilience.py" create --db "$InstallDir\hive.db" `
        --root $InstallDir --output $BackupDir --version $CurrentVersion --actor "pre-upgrade-$($Manifest.version)" --retain 10
    if ($LASTEXITCODE -ne 0) { throw "Pre-upgrade backup failed." }
    & icacls.exe $BackupDir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
    $PreBackup = Get-ChildItem -LiteralPath $BackupDir -Filter "hive-backup-*.zip" | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    if (-not $PreBackup) { throw "Pre-upgrade backup was not created." }
    $State = Join-Path $Work "state"
    & $CurrentPython "$BundleRoot\app\src\resilience.py" extract $PreBackup.FullName $State | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Pre-upgrade backup could not be staged." }

    $ExistingTask = Get-ScheduledTask -TaskName "HIVE OS Central" -ErrorAction SilentlyContinue
    if ($ExistingTask) { $OldTaskXml = Export-ScheduledTask -TaskName "HIVE OS Central" }
    Remove-Item $Next, $Previous, $Failed -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $Next | Out-Null
    robocopy "$BundleRoot\app" $Next /E /XD .git .pytest_cache .venv node_modules backups `
        /XF *.db *.db-shm *.db-wal hive-bootstrap.token hive-agent.token | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "Could not stage the new HIVE release." }
    Copy-Item "$State\database\hive.db" "$Next\hive.db"
    Remove-Item "$Next\config", "$Next\data" -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item "$State\config" "$Next\config" -Recurse
    New-Item -ItemType Directory -Force -Path "$Next\data", "$Next\logs" | Out-Null
    if (Test-Path "$State\data") { Copy-Item "$State\data\*" "$Next\data" -Recurse -Force }
    if (Test-Path "$InstallDir\logs") { Copy-Item "$InstallDir\logs\*" "$Next\logs" -Recurse -Force -ErrorAction SilentlyContinue }

    & "$InstallDir\.venv\Scripts\python.exe" -m venv "$Next\.venv"
    & "$Next\.venv\Scripts\python.exe" -m pip install --no-index `
        --find-links "$BundleRoot\wheels" -r "$Next\requirements.txt"
    if ($LASTEXITCODE -ne 0) { throw "Offline Python dependency installation failed." }
    $env:PYTHONPATH = "$Next\src"
    & "$Next\.venv\Scripts\python.exe" -c "from pathlib import Path; from db import init_db; c=init_db(Path(r'$Next\hive.db')); c.close()"
    if ($LASTEXITCODE -ne 0) { throw "Database migration failed in the staged release." }

    Stop-HiveCentral -InstallDir $InstallDir
    $OldStopped = $true
    Move-Item $InstallDir $Previous
    $OldMoved = $true
    Move-Item $Next $InstallDir
    $Swapped = $true
    $TaskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\deploy\windows\start-hive.ps1`" -InstallDir `"$InstallDir`""
    schtasks /Create /TN "HIVE OS Central" /SC ONSTART /RU SYSTEM /RL HIGHEST /TR $TaskCommand /F | Out-Null
    Start-HiveCentral -InstallDir $InstallDir
    if (-not (Test-HiveHealth)) { throw "Upgraded HIVE did not pass its health check." }
    Remove-Item $Previous -Recurse -Force
    Write-Host "HIVE upgraded to $($Manifest.version); health validation passed." -ForegroundColor Green
} catch {
    if ($Swapped -and (Test-Path -LiteralPath $Previous)) {
        Write-Warning "Upgrade failed; rolling back to the previous release. $($_.Exception.Message)"
        Stop-HiveCentral -InstallDir $InstallDir
        if (Test-Path $InstallDir) { Move-Item $InstallDir $Failed }
        Move-Item $Previous $InstallDir
        if ($OldTaskXml) {
            Register-ScheduledTask -TaskName "HIVE OS Central" -Xml $OldTaskXml -Force | Out-Null
        }
        Start-HiveCentral -InstallDir $InstallDir
        if (-not (Test-HiveHealth)) { Write-Error "Rollback also failed its health check; use restore-hive.ps1 with $PreBackup" }
    } elseif ($OldMoved -and (Test-Path -LiteralPath $Previous)) {
        if (Test-Path $InstallDir) { Move-Item $InstallDir $Failed }
        Move-Item $Previous $InstallDir
        if ($OldTaskXml) {
            Register-ScheduledTask -TaskName "HIVE OS Central" -Xml $OldTaskXml -Force | Out-Null
        }
        Start-HiveCentral -InstallDir $InstallDir
    } elseif ($OldStopped) {
        Start-HiveCentral -InstallDir $InstallDir
    }
    throw
} finally {
    Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue
    if (-not $Swapped) { Remove-Item $Next -Recurse -Force -ErrorAction SilentlyContinue }
}
