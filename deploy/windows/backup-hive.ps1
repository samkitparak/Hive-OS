param(
    [string]$InstallDir = "C:\HIVE-OS",
    [string]$BackupDir = "C:\HIVE-Backups",
    [int]$Retain = 10,
    [string]$Actor = $env:USERNAME
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\hive-lifecycle-common.ps1"
Require-HiveAdmin
$Python = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { throw "HIVE Python environment is unavailable." }
$Version = & $Python -c "import sys; sys.path.insert(0, r'$InstallDir\src'); import main; print(main.APP_VERSION)"
& $Python "$InstallDir\src\resilience.py" create --db "$InstallDir\hive.db" --root $InstallDir `
    --output $BackupDir --version $Version --actor $Actor --retain $Retain
if ($LASTEXITCODE -ne 0) { throw "HIVE backup failed." }
& icacls.exe $BackupDir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
