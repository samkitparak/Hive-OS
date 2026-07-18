param(
    [string]$Version = "0.23.0",
    [Parameter(Mandatory=$true)][string]$PythonInstaller,
    [Parameter(Mandatory=$true)][string]$MosquittoInstaller,
    [Parameter(Mandatory=$true)][string]$OdbcInstaller,
    [string]$OpenSshArchive = "",
    [string]$OutputDir = ".\release"
)

# Run on an internet-connected Windows x64 build PC with Python 3.12 and Node.
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$OutputDir = [IO.Path]::GetFullPath($OutputDir)
$Stage = Join-Path $OutputDir "HIVE-OS-$Version-offline"
$Archive = "$Stage.zip"

foreach ($Path in @($PythonInstaller, $MosquittoInstaller, $OdbcInstaller)) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Required vendor installer is missing: $Path" }
}
if ([IO.Path]::GetExtension($PythonInstaller) -ne ".exe" -or
    [IO.Path]::GetExtension($MosquittoInstaller) -ne ".exe" -or
    [IO.Path]::GetExtension($OdbcInstaller) -notin @(".exe", ".msi")) {
    throw "Expected EXE installers for Python/Mosquitto and an EXE or MSI installer for ODBC."
}
if (-not [Environment]::Is64BitOperatingSystem) { throw "Build the offline release on Windows x64." }
if ($OpenSshArchive -and -not (Test-Path -LiteralPath $OpenSshArchive -PathType Leaf)) {
    throw "OpenSSH archive is missing: $OpenSshArchive"
}
if ((python -c "import sys, struct; print(f'{sys.version_info.major}.{sys.version_info.minor}-{struct.calcsize(`"P`") * 8}')") -ne "3.12-64") {
    throw "Build the Windows wheelhouse with 64-bit Python 3.12."
}
$VersionMatch = Select-String -LiteralPath "$Root\src\main.py" -Pattern '^APP_VERSION\s*=\s*"([^"]+)"$'
if (-not $VersionMatch -or $VersionMatch.Matches[0].Groups[1].Value -ne $Version) {
    throw "Bundle version $Version does not match src/main.py."
}

Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $Archive, "$Archive.sha256" -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "$Stage\app", "$Stage\wheels", "$Stage\installers", `
    "$Stage\agent-payload\payload\src", "$Stage\agent-payload\payload\runtime", `
    "$Stage\agent-payload\payload\wheels" | Out-Null

Push-Location "$Root\dashboard"
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "Dashboard dependency installation failed." }
    npm run lint
    if ($LASTEXITCODE -ne 0) { throw "Dashboard lint failed." }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Dashboard build failed." }
} finally { Pop-Location }

robocopy $Root "$Stage\app" /E /XD .git .pytest_cache .venv node_modules dashboard\node_modules `
    backups release /XF *.db *.db-shm *.db-wal hive-bootstrap.token hive-agent.token | Out-Null
if ($LASTEXITCODE -gt 7) { throw "Could not stage the HIVE application (robocopy $LASTEXITCODE)." }
robocopy "$Root\dashboard\dist" "$Stage\app\dashboard\dist" /E | Out-Null
if ($LASTEXITCODE -gt 7) { throw "Could not stage the built dashboard." }

python -m pip download --only-binary=:all: --dest "$Stage\wheels" -r "$Root\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "Central Python wheel download failed." }
python -m pip download --only-binary=:all: --dest "$Stage\agent-payload\payload\wheels" `
    -r "$Root\requirements-agent.txt"
if ($LASTEXITCODE -ne 0) { throw "Machine-agent Python wheel download failed." }

$PythonName = "python-3.12-x64.exe"
$MosquittoName = "mosquitto-x64.exe"
$OdbcName = if ([IO.Path]::GetExtension($OdbcInstaller) -eq ".msi") { "msodbcsql18-x64.msi" } else { "msodbcsql18-x64.exe" }
Copy-Item -LiteralPath $PythonInstaller "$Stage\installers\$PythonName"
Copy-Item -LiteralPath $PythonInstaller "$Stage\agent-payload\payload\runtime\$PythonName"
Copy-Item -LiteralPath $MosquittoInstaller "$Stage\installers\$MosquittoName"
Copy-Item -LiteralPath $OdbcInstaller "$Stage\installers\$OdbcName"
$SshName = $null
if ($OpenSshArchive) {
    $SshName = "openssh-win64.zip"
    Copy-Item -LiteralPath $OpenSshArchive "$Stage\installers\$SshName"
}

Copy-Item "$Root\deploy\windows\install-machine-agent.ps1" "$Stage\agent-payload\install-machine-agent.ps1"
Copy-Item "$Root\src\maestro_agent.py" "$Stage\agent-payload\payload\src\maestro_agent.py"
Copy-Item "$Root\src\mqtt_client.py" "$Stage\agent-payload\payload\src\mqtt_client.py"
Copy-Item "$Root\requirements-agent.txt" "$Stage\agent-payload\payload\requirements-agent.txt"
$AgentRoot = "$Stage\agent-payload"
$AgentFiles = Get-ChildItem -LiteralPath $AgentRoot -File -Recurse | Sort-Object FullName | ForEach-Object {
    $Relative = $_.FullName.Substring($AgentRoot.Length + 1).Replace('\', '/')
    [ordered]@{ path = $Relative; size = $_.Length; sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
}
$AgentManifest = [ordered]@{
    format = "hive-offline-agent-payload"
    format_version = 1
    version = $Version
    generated_at = [DateTimeOffset]::UtcNow.ToString("o")
    target = "windows-x64"
    python_version = "3.12-64"
    files = @($AgentFiles)
}
$AgentManifest | ConvertTo-Json -Depth 6 | Set-Content "$AgentRoot\agent-payload.json" -Encoding UTF8
$AgentManifestHash = (Get-FileHash "$AgentRoot\agent-payload.json" -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content "$AgentRoot\agent-payload.json.sha256" "$AgentManifestHash  agent-payload.json" -Encoding ASCII

Copy-Item "$Root\deploy\windows\install-central-offline.ps1" "$Stage\install-central-offline.ps1"
Copy-Item "$Root\deploy\windows\hive-lifecycle-common.ps1" "$Stage\hive-lifecycle-common.ps1"
Copy-Item "$Root\deploy\windows\backup-hive.ps1" "$Stage\backup-hive.ps1"
Copy-Item "$Root\deploy\windows\restore-hive.ps1" "$Stage\restore-hive.ps1"
Copy-Item "$Root\deploy\windows\upgrade-hive.ps1" "$Stage\upgrade-hive.ps1"
@"
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-central-offline.ps1" -BundleRoot "%~dp0"
pause
"@ | Set-Content "$Stage\Install-HIVE-OS.cmd" -Encoding ASCII

$Files = Get-ChildItem -LiteralPath $Stage -File -Recurse | Sort-Object FullName | ForEach-Object {
    $Relative = $_.FullName.Substring($Stage.Length + 1).Replace('\', '/')
    [ordered]@{ path = $Relative; size = $_.Length; sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
}
$Manifest = [ordered]@{
    format = "hive-offline-release"
    format_version = 1
    version = $Version
    generated_at = [DateTimeOffset]::UtcNow.ToString("o")
    target = "windows-x64"
    python_version = "3.12"
    installers = [ordered]@{
        python = "installers/$PythonName"
        mosquitto = "installers/$MosquittoName"
        odbc = "installers/$OdbcName"
        openssh = if ($SshName) { "installers/$SshName" } else { $null }
    }
    files = @($Files)
}
$Manifest | ConvertTo-Json -Depth 6 | Set-Content "$Stage\manifest.json" -Encoding UTF8
$ManifestHash = (Get-FileHash "$Stage\manifest.json" -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content "$Stage\manifest.json.sha256" "$ManifestHash  manifest.json" -Encoding ASCII

Compress-Archive -Path "$Stage\*" -DestinationPath $Archive -CompressionLevel Optimal
$Hash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content "$Archive.sha256" "$Hash  $([IO.Path]::GetFileName($Archive))" -Encoding ASCII
Write-Host "Offline release ready: $Archive" -ForegroundColor Green
Write-Host "Extract it on the central PC and double-click Install-HIVE-OS.cmd."
