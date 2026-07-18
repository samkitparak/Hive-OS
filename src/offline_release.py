"""Cross-platform verification for a HIVE Windows offline release bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable


FORMAT = "hive-offline-release"
FORMAT_VERSION = 1
AGENT_FORMAT = "hive-offline-agent-payload"
MAX_FILES = 50_000
MAX_UNCOMPRESSED_BYTES = 10 * 1024 * 1024 * 1024
PRIVATE_NAMES = {
    "hive.db", "hive.db-shm", "hive.db-wal", "hive-bootstrap.token", "hive-agent.token",
}
REQUIRED_FILES = {
    "Install-HIVE-OS.cmd",
    "install-central-offline.ps1",
    "hive-lifecycle-common.ps1",
    "backup-hive.ps1",
    "restore-hive.ps1",
    "upgrade-hive.ps1",
    "app/src/main.py",
    "app/src/schema.sql",
    "app/dashboard/dist/index.html",
    "app/deploy/windows/install-central.ps1",
    "agent-payload/agent-payload.json",
    "agent-payload/agent-payload.json.sha256",
}
APP_VERSION_PATTERN = re.compile(rb'^APP_VERSION\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def _sha_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _sha_path(path: Path) -> str:
    with path.open("rb") as stream:
        return _sha_stream(stream)[0]


def _safe_path(value: object) -> str:
    raw = str(value or "")
    path = PurePosixPath(raw)
    if (
        not raw or "\\" in raw or path.is_absolute() or ".." in path.parts
        or "." in path.parts or raw.endswith("/")
    ):
        raise ValueError(f"Unsafe release path: {raw!r}")
    return path.as_posix()


def _sidecar_hash(raw: bytes, label: str) -> str:
    try:
        token = raw.decode("ascii").strip().split()[0].lower()
    except (UnicodeDecodeError, IndexError) as error:
        raise ValueError(f"{label} hash sidecar is invalid") from error
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError(f"{label} hash sidecar is malformed")
    return token


def _manifest(raw: bytes, expected_format: str, label: str) -> dict:
    try:
        result = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} manifest is invalid") from error
    if result.get("format") != expected_format or result.get("format_version") != 1:
        raise ValueError(f"Unsupported {label} format")
    if not isinstance(result.get("files"), list) or not result["files"]:
        raise ValueError(f"{label} manifest has no files")
    return result


def _entries(manifest: dict, label: str) -> dict[str, dict]:
    entries = {}
    for raw in manifest["files"]:
        if not isinstance(raw, dict):
            raise ValueError(f"{label} file entry must be an object")
        path = _safe_path(raw.get("path"))
        if path in entries:
            raise ValueError(f"{label} manifest contains duplicate path: {path}")
        digest = str(raw.get("sha256") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"{label} file hash is invalid: {path}")
        try:
            size = int(raw.get("size"))
        except (TypeError, ValueError) as error:
            raise ValueError(f"{label} file size is invalid: {path}") from error
        if size < 0 or size > MAX_UNCOMPRESSED_BYTES:
            raise ValueError(f"{label} file size is unsafe: {path}")
        entries[path] = {**raw, "path": path, "sha256": digest, "size": size}
    if len(entries) > MAX_FILES or sum(item["size"] for item in entries.values()) > MAX_UNCOMPRESSED_BYTES:
        raise ValueError(f"{label} exceeds verification limits")
    return entries


def _verify_entries(entries: dict[str, dict], read: Callable[[str], BinaryIO], label: str) -> None:
    for path, entry in entries.items():
        with read(path) as stream:
            digest, size = _sha_stream(stream)
        if digest != entry["sha256"] or size != entry["size"]:
            raise ValueError(f"{label} file failed verification: {path}")


def _validate_contract(manifest: dict, entries: dict[str, dict], read_bytes: Callable[[str], bytes],
                       expected_version: str | None) -> list[dict]:
    gates = []

    def gate(key: str, passed: bool, detail: str) -> None:
        gates.append({"key": key, "passed": passed, "detail": detail})
        if not passed:
            raise ValueError(detail)

    gate("target", manifest.get("target") == "windows-x64",
         "Release target must be windows-x64")
    version = str(manifest.get("version") or "")
    gate("version", bool(version) and (expected_version is None or version == expected_version),
         f"Release version {version or 'missing'} does not match {expected_version or 'the requested version'}")
    gate("required_files", REQUIRED_FILES <= set(entries),
         "Release is missing required files: " + ", ".join(sorted(REQUIRED_FILES - set(entries))))
    installers = manifest.get("installers")
    gate("installer_map", isinstance(installers, dict), "Release installer map is missing")
    for key in ("python", "mosquitto", "odbc"):
        candidate = _safe_path(installers.get(key)) if installers.get(key) else ""
        gate(f"installer_{key}", candidate in entries,
             f"Release {key} installer is missing from the manifest")
    gate("central_wheelhouse", any(path.startswith("wheels/") and path.endswith(".whl") for path in entries),
         "Central Windows wheelhouse is empty")
    gate("agent_wheelhouse", any(
        path.startswith("agent-payload/payload/wheels/") and path.endswith(".whl") for path in entries
    ), "Machine-agent Windows wheelhouse is empty")
    forbidden = sorted(path for path in entries if PurePosixPath(path).name.lower() in PRIVATE_NAMES)
    gate("no_private_state", not forbidden,
         "Release contains private runtime state: " + ", ".join(forbidden))
    app_match = APP_VERSION_PATTERN.search(read_bytes("app/src/main.py"))
    app_version = app_match.group(1).decode("ascii") if app_match else None
    gate("app_version", app_version == version,
         f"Bundled app version {app_version or 'missing'} does not match manifest {version}")
    return gates


def _verify_agent(entries: dict[str, dict], read_bytes: Callable[[str], bytes],
                  read: Callable[[str], BinaryIO], release_version: str) -> dict:
    manifest_path = "agent-payload/agent-payload.json"
    sidecar_path = "agent-payload/agent-payload.json.sha256"
    manifest_raw = read_bytes(manifest_path)
    expected_hash = _sidecar_hash(read_bytes(sidecar_path), "Agent payload manifest")
    if hashlib.sha256(manifest_raw).hexdigest() != expected_hash:
        raise ValueError("Agent payload manifest failed SHA-256 verification")
    manifest = _manifest(manifest_raw, AGENT_FORMAT, "agent payload")
    if manifest.get("target") != "windows-x64" or manifest.get("version") != release_version:
        raise ValueError("Agent payload target or version does not match the release")
    agent_entries = _entries(manifest, "Agent payload")
    expected_paths = {f"agent-payload/{path}" for path in agent_entries}
    actual_paths = {
        path for path in entries
        if path.startswith("agent-payload/")
        and path not in {manifest_path, sidecar_path}
    }
    if expected_paths != actual_paths:
        missing = sorted(expected_paths - actual_paths)
        extra = sorted(actual_paths - expected_paths)
        raise ValueError(f"Agent payload contents differ from its manifest; missing={missing}, extra={extra}")

    def agent_read(path: str) -> BinaryIO:
        return read(f"agent-payload/{path}")

    _verify_entries(agent_entries, agent_read, "Agent payload")
    required = {
        "install-machine-agent.ps1", "payload/src/maestro_agent.py",
        "payload/src/mqtt_client.py", "payload/requirements-agent.txt",
    }
    if not required <= set(agent_entries):
        raise ValueError("Agent payload is missing required installation files")
    return {
        "status": "verified", "version": manifest["version"],
        "file_count": len(agent_entries), "manifest_sha256": expected_hash,
    }


def _verify_reader(*, names: set[str], read: Callable[[str], BinaryIO],
                   read_bytes: Callable[[str], bytes], expected_version: str | None,
                   source: str, archive_sha256: str | None = None) -> dict:
    if "manifest.json" not in names or "manifest.json.sha256" not in names:
        raise ValueError("Release manifest or its SHA-256 sidecar is missing")
    manifest_raw = read_bytes("manifest.json")
    expected_manifest_hash = _sidecar_hash(read_bytes("manifest.json.sha256"), "Release manifest")
    actual_manifest_hash = hashlib.sha256(manifest_raw).hexdigest()
    if actual_manifest_hash != expected_manifest_hash:
        raise ValueError("Release manifest failed SHA-256 verification")
    manifest = _manifest(manifest_raw, FORMAT, "release")
    entries = _entries(manifest, "Release")
    expected_names = set(entries) | {"manifest.json", "manifest.json.sha256"}
    if names != expected_names:
        missing = sorted(expected_names - names)
        extra = sorted(names - expected_names)
        raise ValueError(f"Release contents differ from the manifest; missing={missing}, extra={extra}")
    _verify_entries(entries, read, "Release")
    gates = _validate_contract(manifest, entries, read_bytes, expected_version)
    agent = _verify_agent(entries, read_bytes, read, str(manifest["version"]))
    gates.append({"key": "agent_payload", "passed": True,
                  "detail": f"Nested agent payload verified ({agent['file_count']} files)"})
    return {
        "status": "verified", "source": source,
        "format": FORMAT, "format_version": FORMAT_VERSION,
        "version": manifest["version"], "target": manifest["target"],
        "generated_at": manifest.get("generated_at"),
        "file_count": len(entries),
        "total_uncompressed_size": sum(item["size"] for item in entries.values()),
        "manifest_sha256": expected_manifest_hash,
        "archive_sha256": archive_sha256,
        "agent_payload": agent, "gates": gates,
        "rehearsal_scope": (
            "Static integrity, completeness, and version rehearsal passed. "
            "Windows installation, service startup, firewall, upgrade, and rollback still require Windows x64."
        ),
    }


def verify_release(path: Path, *, expected_version: str | None = None,
                   require_archive_sidecar: bool = True) -> dict:
    path = Path(path).resolve()
    if path.is_dir():
        items = list(path.rglob("*"))
        if any(item.is_symlink() for item in items):
            raise ValueError("Release directory contains a symbolic link")
        files = {item.relative_to(path).as_posix(): item for item in items if item.is_file()}
        names = {_safe_path(name) for name in files}

        def read(name: str) -> BinaryIO:
            return files[name].open("rb")

        def read_bytes(name: str) -> bytes:
            return files[name].read_bytes()

        return _verify_reader(
            names=names, read=read, read_bytes=read_bytes,
            expected_version=expected_version, source=str(path),
        )
    if not path.is_file() or path.suffix.lower() != ".zip":
        raise ValueError("Release path must be an extracted directory or ZIP archive")
    archive_hash = _sha_path(path)
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if require_archive_sidecar:
        if not sidecar.is_file():
            raise ValueError("Release archive SHA-256 sidecar is missing")
        if _sidecar_hash(sidecar.read_bytes(), "Release archive") != archive_hash:
            raise ValueError("Release archive failed SHA-256 sidecar verification")
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        file_infos = [item for item in infos if not item.is_dir()]
        raw_names = [item.filename for item in file_infos]
        if len(raw_names) != len(set(raw_names)):
            raise ValueError("Release archive contains duplicate members")
        names = {_safe_path(name) for name in raw_names}
        if len(names) != len(raw_names):
            raise ValueError("Release archive contains duplicate normalized paths")
        for item in file_infos:
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("Release archive contains a symbolic link")
        info_map = {item.filename: item for item in file_infos}

        def read(name: str) -> BinaryIO:
            return archive.open(info_map[name], "r")

        def read_bytes(name: str) -> bytes:
            return archive.read(info_map[name])

        return _verify_reader(
            names=names, read=read, read_bytes=read_bytes,
            expected_version=expected_version, source=str(path),
            archive_sha256=archive_hash,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a HIVE Windows offline release")
    parser.add_argument("path", type=Path)
    parser.add_argument("--version", dest="expected_version")
    parser.add_argument("--allow-missing-archive-sidecar", action="store_true")
    args = parser.parse_args()
    try:
        result = verify_release(
            args.path, expected_version=args.expected_version,
            require_archive_sidecar=not args.allow_missing_archive_sidecar,
        )
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(json.dumps({"status": "failed", "detail": str(error)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
