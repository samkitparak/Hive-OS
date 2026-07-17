"""Static contracts for the Windows offline and recovery package."""

from pathlib import Path

import access_control


ROOT = Path(__file__).parent.parent
WINDOWS = ROOT / "deploy" / "windows"


def _script(name: str) -> str:
    return (WINDOWS / name).read_text(encoding="utf-8")


def test_offline_builder_is_closed_and_hash_manifested():
    script = _script("build-offline-bundle.ps1")
    assert "pip download" in script
    assert "--only-binary=:all:" in script
    assert "Get-FileHash" in script
    assert "manifest.json.sha256" in script
    assert 'format = "hive-offline-release"' in script
    assert "PythonInstaller" in script and "MosquittoInstaller" in script and "OdbcInstaller" in script
    assert "npm run build" in script


def test_offline_install_never_uses_package_indexes_or_winget():
    wrapper = _script("install-central-offline.ps1")
    installer = _script("install-central.ps1")
    assert "winget" not in wrapper.lower()
    assert "Test-HiveReleaseManifest" in wrapper
    assert "3.12-64" in wrapper
    assert "-SkipPrerequisites" in wrapper
    assert "-OfflineWheelDir" in wrapper
    assert "--no-index" in installer
    assert "--find-links" in installer
    assert "DashboardPrebuilt" in installer


def test_restore_and_upgrade_fail_back_to_preserved_state():
    common = _script("hive-lifecycle-common.ps1")
    restore = _script("restore-hive.ps1")
    upgrade = _script("upgrade-hive.ps1")
    assert "Stop-ScheduledTask" in common and "Start-ScheduledTask" in common
    assert "Get-FileHash" in common
    assert "pre-restore" in restore and "returning to the pre-restore state" in restore
    assert "Test-HiveHealth" in restore
    assert "$MutationStarted" in restore and "$OldWal" in restore and "$OldShm" in restore
    assert "pre-upgrade" in upgrade and "rolling back to the previous release" in upgrade
    assert "--no-index" in upgrade and "Test-HiveReleaseManifest" in upgrade
    assert "Export-ScheduledTask" in upgrade and "Register-ScheduledTask" in upgrade
    assert '"$BundleRoot\\app\\src\\resilience.py" extract' in upgrade
    assert "$OldStopped" in upgrade and "$OldMoved" in upgrade


def test_supervisor_tracks_every_runtime_process():
    script = _script("start-hive.ps1")
    assert "mosquitto.pid" in script
    assert "backend.pid" in script
    assert "supervisor.pid" in script
    assert "finally" in script


def test_resilience_mutations_are_admin_only():
    assert access_control.required_permissions("GET", "/resilience") == ("view",)
    assert access_control.required_permissions("POST", "/resilience/backups") == ("admin",)
