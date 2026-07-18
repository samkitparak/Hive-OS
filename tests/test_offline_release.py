"""Cross-platform integrity rehearsal for the Windows offline release."""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import offline_release


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_release(tmp_path: Path, version: str = "0.25.0") -> tuple[Path, Path]:
    root = tmp_path / f"HIVE-OS-{version}-offline"
    agent_files = {
        "install-machine-agent.ps1": b"Write-Host agent\n",
        "payload/src/maestro_agent.py": b"print('agent')\n",
        "payload/src/mqtt_client.py": b"print('mqtt')\n",
        "payload/requirements-agent.txt": b"paho-mqtt==2.1.0\n",
        "payload/wheels/paho_mqtt-2.1.0-py3-none-any.whl": b"agent-wheel",
    }
    agent_manifest = {
        "format": offline_release.AGENT_FORMAT,
        "format_version": 1,
        "version": version,
        "target": "windows-x64",
        "python_version": "3.12-64",
        "files": [
            {"path": path, "size": len(value), "sha256": digest(value)}
            for path, value in sorted(agent_files.items())
        ],
    }
    agent_manifest_bytes = json.dumps(agent_manifest, sort_keys=True).encode()
    files = {
        "Install-HIVE-OS.cmd": b"powershell install-central-offline.ps1\n",
        "install-central-offline.ps1": b"Write-Host install\n",
        "hive-lifecycle-common.ps1": b"function Test-HiveReleaseManifest {}\n",
        "backup-hive.ps1": b"Write-Host backup\n",
        "restore-hive.ps1": b"Write-Host restore\n",
        "upgrade-hive.ps1": b"Write-Host upgrade\n",
        "app/src/main.py": f'APP_VERSION = "{version}"\n'.encode(),
        "app/src/schema.sql": b"CREATE TABLE test (id INTEGER);\n",
        "app/dashboard/dist/index.html": b"<html>HIVE</html>\n",
        "app/deploy/windows/install-central.ps1": b"Write-Host central\n",
        "installers/python-3.12-x64.exe": b"python-installer",
        "installers/mosquitto-x64.exe": b"mosquitto-installer",
        "installers/msodbcsql18-x64.msi": b"odbc-installer",
        "wheels/fastapi-1-py3-none-any.whl": b"central-wheel",
        **{f"agent-payload/{path}": value for path, value in agent_files.items()},
        "agent-payload/agent-payload.json": agent_manifest_bytes,
        "agent-payload/agent-payload.json.sha256": (
            f"{digest(agent_manifest_bytes)}  agent-payload.json\n".encode()
        ),
    }
    manifest = {
        "format": offline_release.FORMAT,
        "format_version": 1,
        "version": version,
        "generated_at": "2026-08-01T00:00:00+00:00",
        "target": "windows-x64",
        "python_version": "3.12",
        "installers": {
            "python": "installers/python-3.12-x64.exe",
            "mosquitto": "installers/mosquitto-x64.exe",
            "odbc": "installers/msodbcsql18-x64.msi",
            "openssh": None,
        },
        "files": [
            {"path": path, "size": len(value), "sha256": digest(value)}
            for path, value in sorted(files.items())
        ],
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    files["manifest.json"] = manifest_bytes
    files["manifest.json.sha256"] = f"{digest(manifest_bytes)}  manifest.json\n".encode()
    for relative, value in files.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(value)
    archive = tmp_path / f"HIVE-OS-{version}-offline.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                target.write(path, path.relative_to(root).as_posix())
    archive.with_suffix(".zip.sha256").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    )
    return root, archive


def test_verifies_extracted_and_archived_release_with_nested_agent(tmp_path):
    root, archive = build_release(tmp_path)
    extracted = offline_release.verify_release(root, expected_version="0.25.0")
    zipped = offline_release.verify_release(archive, expected_version="0.25.0")
    assert extracted["status"] == "verified"
    assert zipped["status"] == "verified"
    assert zipped["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert zipped["agent_payload"]["status"] == "verified"
    assert all(gate["passed"] for gate in zipped["gates"])
    assert "still require Windows x64" in zipped["rehearsal_scope"]


def test_requires_archive_sidecar_and_matching_expected_version(tmp_path):
    _, archive = build_release(tmp_path)
    archive.with_suffix(".zip.sha256").unlink()
    with pytest.raises(ValueError, match="sidecar is missing"):
        offline_release.verify_release(archive)
    assert offline_release.verify_release(
        archive, require_archive_sidecar=False
    )["status"] == "verified"
    with pytest.raises(ValueError, match="does not match"):
        offline_release.verify_release(
            archive, expected_version="9.9.9", require_archive_sidecar=False
        )


def test_tampering_and_unexpected_files_fail_closed(tmp_path):
    root, _ = build_release(tmp_path)
    (root / "app/src/main.py").write_text('APP_VERSION = "tampered"\n')
    with pytest.raises(ValueError, match="failed verification"):
        offline_release.verify_release(root)

    root, _ = build_release(tmp_path / "extra")
    (root / "unexpected.exe").write_bytes(b"not in manifest")
    with pytest.raises(ValueError, match="differ from the manifest"):
        offline_release.verify_release(root)


def test_nested_agent_tampering_fails_even_if_outer_manifest_is_rehashed(tmp_path):
    root, _ = build_release(tmp_path)
    nested = root / "agent-payload/payload/src/maestro_agent.py"
    nested.write_bytes(b"tampered agent")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["files"]:
        if entry["path"] == "agent-payload/payload/src/maestro_agent.py":
            entry["size"] = nested.stat().st_size
            entry["sha256"] = digest(nested.read_bytes())
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    manifest_path.write_bytes(manifest_bytes)
    (root / "manifest.json.sha256").write_text(
        f"{digest(manifest_bytes)}  manifest.json\n"
    )
    with pytest.raises(ValueError, match="Agent payload file failed verification"):
        offline_release.verify_release(root)


def test_private_runtime_state_is_rejected_even_when_manifested(tmp_path):
    root, _ = build_release(tmp_path)
    secret = root / "app/hive.db"
    secret.write_bytes(b"private")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"].append({
        "path": "app/hive.db", "size": secret.stat().st_size,
        "sha256": digest(secret.read_bytes()),
    })
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    manifest_path.write_bytes(manifest_bytes)
    (root / "manifest.json.sha256").write_text(
        f"{digest(manifest_bytes)}  manifest.json\n"
    )
    with pytest.raises(ValueError, match="private runtime state"):
        offline_release.verify_release(root)
