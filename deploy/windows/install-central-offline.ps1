param([string]$BundleRoot = $PSScriptRoot)

# Verified no-network central installer. The bundle is assembled before travel.
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\hive-lifecycle-common.ps1"
Require-HiveAdmin
if (-not [Environment]::Is64BitOperatingSystem) { throw "This HIVE release requires Windows x64." }
$BundleRoot = (Resolve-Path -LiteralPath $BundleRoot).Path
$Manifest = Test-HiveReleaseManifest -BundleRoot $BundleRoot

function Run-Installer([string]$FilePath, [string[]]$Arguments) {
    $Process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -Wait -PassThru
    if ($Process.ExitCode -notin @(0, 3010, 1641)) {
        throw "$([IO.Path]::GetFileName($FilePath)) failed with exit code $($Process.ExitCode)."
    }
}

Update-HivePath
$PythonExe = $null
$ExistingPythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
$ExistingPython = if ($ExistingPythonCommand) { $ExistingPythonCommand.Source } else { $null }
if ($ExistingPython) {
    $Identity = & $ExistingPython -c "import sys, struct; print(f'{sys.version_info.major}.{sys.version_info.minor}-{struct.calcsize(`"P`") * 8}')" 2>$null
    if ($Identity -eq "3.12-64") { $PythonExe = $ExistingPython }
}
if (-not $PythonExe) {
    Run-Installer (Join-Path $BundleRoot ($Manifest.installers.python -replace '/', '\')) `
        @("/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_test=0")
    Update-HivePath
    $RefreshedPython = Get-Command python.exe -ErrorAction SilentlyContinue
    $Candidates = @(
        "C:\Program Files\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        $(if ($RefreshedPython) { $RefreshedPython.Source })
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    foreach ($Candidate in $Candidates) {
        $Identity = & $Candidate -c "import sys, struct; print(f'{sys.version_info.major}.{sys.version_info.minor}-{struct.calcsize(`"P`") * 8}')" 2>$null
        if ($Identity -eq "3.12-64") { $PythonExe = $Candidate; break }
    }
}
if (-not $PythonExe) { throw "64-bit Python 3.12 is unavailable after bundled installation." }
if (-not (Get-Command mosquitto.exe -ErrorAction SilentlyContinue) -and
    -not (Test-Path "C:\Program Files\mosquitto\mosquitto.exe")) {
    Run-Installer (Join-Path $BundleRoot ($Manifest.installers.mosquitto -replace '/', '\')) @("/S")
}
if (-not (Get-OdbcDriver -ErrorAction SilentlyContinue | Where-Object Name -Like "ODBC Driver 18 for SQL Server*")) {
    $Odbc = Join-Path $BundleRoot ($Manifest.installers.odbc -replace '/', '\')
    if ([IO.Path]::GetExtension($Odbc) -eq ".msi") {
        Run-Installer "msiexec.exe" @("/i", $Odbc, "/qn", "/norestart", "IACCEPTMSODBCSQLLICENSETERMS=YES")
    } else {
        Run-Installer $Odbc @("/quiet", "/norestart", "IACCEPTMSODBCSQLLICENSETERMS=YES")
    }
}
Update-HivePath

if (-not (Get-Command ssh-keygen.exe -ErrorAction SilentlyContinue)) {
    if (-not $Manifest.installers.openssh) {
        throw "OpenSSH Client is unavailable and this release has no Win64 OpenSSH archive."
    }
    $SshTarget = "C:\Program Files\OpenSSH"
    New-Item -ItemType Directory -Force -Path $SshTarget | Out-Null
    Expand-Archive -LiteralPath (Join-Path $BundleRoot ($Manifest.installers.openssh -replace '/', '\')) `
        -DestinationPath $SshTarget -Force
    $Nested = Get-ChildItem $SshTarget -Filter ssh-keygen.exe -Recurse | Select-Object -First 1
    if (-not $Nested) { throw "The bundled OpenSSH archive does not contain ssh-keygen.exe." }
    $SshBin = $Nested.DirectoryName
    $MachinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    if (($MachinePath -split ';') -notcontains $SshBin) {
        [Environment]::SetEnvironmentVariable("Path", "$MachinePath;$SshBin", "Machine")
    }
    Update-HivePath
}

if (Test-Path -LiteralPath "C:\HIVE-OS") {
    throw "C:\HIVE-OS already exists. Use upgrade-hive.ps1 for an existing installation."
}
$Installer = Join-Path $BundleRoot "app\deploy\windows\install-central.ps1"
& $Installer -SkipPrerequisites -OfflineWheelDir (Join-Path $BundleRoot "wheels") `
    -DashboardPrebuilt -PythonExe $PythonExe
$AgentPayloadSource = Join-Path $BundleRoot "agent-payload"
if (-not (Test-Path -LiteralPath "$AgentPayloadSource\agent-payload.json" -PathType Leaf)) {
    throw "The verified offline machine-agent payload is missing from this release."
}
$AgentPayloadTarget = "C:\HIVE-OS\data\offline-agent"
$AgentPayloadStage = "$AgentPayloadTarget.new"
Remove-Item -LiteralPath $AgentPayloadStage -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item -LiteralPath $AgentPayloadSource -Destination $AgentPayloadStage -Recurse -Force
Remove-Item -LiteralPath $AgentPayloadTarget -Recurse -Force -ErrorAction SilentlyContinue
Move-Item -LiteralPath $AgentPayloadStage -Destination $AgentPayloadTarget
& icacls.exe $AgentPayloadTarget /inheritance:r /grant:r `
    "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not secure the offline machine-agent payload." }
Write-Host "Verified offline installation completed without package-index access." -ForegroundColor Green
