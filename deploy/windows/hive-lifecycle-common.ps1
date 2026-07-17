Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Require-HiveAdmin {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
    if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this command from an Administrator PowerShell window."
    }
}

function Update-HivePath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [Environment]::GetEnvironmentVariable("Path", "User")
}

function Test-HiveReleaseManifest {
    param([Parameter(Mandatory=$true)][string]$BundleRoot)
    $Root = (Resolve-Path -LiteralPath $BundleRoot).Path
    $ManifestPath = Join-Path $Root "manifest.json"
    if (-not (Test-Path -LiteralPath $ManifestPath)) { throw "Release manifest.json is missing." }
    $ManifestHashPath = Join-Path $Root "manifest.json.sha256"
    if (-not (Test-Path -LiteralPath $ManifestHashPath)) { throw "Release manifest SHA-256 is missing." }
    $ExpectedManifestHash = (Get-Content -LiteralPath $ManifestHashPath -Raw).Trim().Split()[0].ToLowerInvariant()
    $ActualManifestHash = (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ExpectedManifestHash -ne $ActualManifestHash) { throw "Release manifest failed SHA-256 verification." }
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if ($Manifest.format -ne "hive-offline-release" -or $Manifest.format_version -ne 1) {
        throw "Unsupported HIVE offline release format."
    }
    foreach ($File in $Manifest.files) {
        $Relative = [string]$File.path
        if ([IO.Path]::IsPathRooted($Relative) -or $Relative -match '(^|[\\/])\.\.([\\/]|$)') {
            throw "Unsafe release path: $Relative"
        }
        $Path = Join-Path $Root ($Relative -replace '/', '\')
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Release file is missing: $Relative" }
        $Actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($Actual -ne ([string]$File.sha256).ToLowerInvariant()) {
            throw "Release file failed SHA-256 verification: $Relative"
        }
        if ((Get-Item -LiteralPath $Path).Length -ne [int64]$File.size) {
            throw "Release file size does not match the manifest: $Relative"
        }
    }
    return $Manifest
}

function Stop-HiveCentral {
    param([string]$InstallDir = "C:\HIVE-OS")
    $Task = Get-ScheduledTask -TaskName "HIVE OS Central" -ErrorAction SilentlyContinue
    if ($Task) { Stop-ScheduledTask -TaskName "HIVE OS Central" -ErrorAction SilentlyContinue }
    $PidDir = Join-Path $InstallDir "data\runtime"
    foreach ($Name in @("backend.pid", "mosquitto.pid", "supervisor.pid")) {
        $Path = Join-Path $PidDir $Name
        if (Test-Path -LiteralPath $Path) {
            $ProcessId = [int](Get-Content -LiteralPath $Path -Raw)
            Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    # Compatibility cleanup for 0.20 and earlier, whose startup task detached
    # its children before lifecycle PID files existed.
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains($InstallDir) -and
        ($_.Name -in @("python.exe", "uvicorn.exe", "mosquitto.exe", "cmd.exe"))
    } | ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate -ErrorAction SilentlyContinue | Out-Null }
    Start-Sleep -Seconds 2
    Remove-Item "$PidDir\*.pid" -Force -ErrorAction SilentlyContinue
}

function Start-HiveCentral {
    param([string]$InstallDir = "C:\HIVE-OS")
    $Task = Get-ScheduledTask -TaskName "HIVE OS Central" -ErrorAction SilentlyContinue
    if ($Task) {
        Start-ScheduledTask -TaskName "HIVE OS Central"
    } else {
        Start-Process powershell.exe -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            (Join-Path $InstallDir "deploy\windows\start-hive.ps1"), "-InstallDir", $InstallDir
        ) -WindowStyle Hidden
    }
}

function Test-HiveHealth {
    param([string]$Url = "http://127.0.0.1:8000/api/health", [int]$Attempts = 30)
    for ($Index = 0; $Index -lt $Attempts; $Index++) {
        try {
            $Response = Invoke-RestMethod -Uri $Url -TimeoutSec 3
            if ($Response.status -eq "ok") { return $true }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
}
