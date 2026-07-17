"""Verified HIVE system backups and safe archive extraction.

Backups use SQLite's online backup API so the running WAL database is copied as
a consistent snapshot. Restores are intentionally performed by the external
Windows lifecycle scripts after the HIVE process has stopped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable


ROOT = Path(__file__).parent.parent
DEFAULT_DB_PATH = ROOT / "hive.db"
DEFAULT_BACKUP_DIR = ROOT / "backups"
FORMAT_VERSION = 1
CHUNK_SIZE = 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stream_sha256(stream) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _integrity(path: Path) -> dict:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        result = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
    finally:
        conn.close()
    return {
        "integrity_check": result,
        "foreign_key_violations": len(foreign_keys),
        "ok": result == ["ok"] and not foreign_keys,
    }


def _copy_database(source: Path, destination: Path) -> dict:
    if not source.is_file():
        raise ValueError(f"Database does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source, timeout=30)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.execute("PRAGMA busy_timeout=30000")
        source_conn.backup(destination_conn, pages=256, sleep=0.05)
    finally:
        destination_conn.close()
        source_conn.close()
    result = _integrity(destination)
    destination.with_name(destination.name + "-wal").unlink(missing_ok=True)
    destination.with_name(destination.name + "-shm").unlink(missing_ok=True)
    if not result["ok"]:
        destination.unlink(missing_ok=True)
        raise ValueError("SQLite rejected the backup snapshot during integrity validation")
    return result


def _state_files(root: Path, db_path: Path) -> Iterable[tuple[Path, PurePosixPath]]:
    candidates: list[Path] = []
    config_dir = root / "config"
    if config_dir.is_dir():
        candidates.extend(path for path in config_dir.rglob("*") if path.is_file())
    data_dir = root / "data"
    if data_dir.is_dir():
        candidates.extend(
            path for path in data_dir.rglob("*")
            if path.is_file()
            and not ({"backups", "runtime"} & set(path.relative_to(data_dir).parts))
        )
    env_path = root / ".env"
    if env_path.is_file():
        candidates.append(env_path)

    db_resolved = db_path.resolve()
    for path in sorted(candidates):
        if path.resolve() == db_resolved:
            continue
        relative = path.resolve().relative_to(root.resolve())
        if "backups" in relative.parts or "__pycache__" in relative.parts:
            continue
        yield path, PurePosixPath(*relative.parts)


def _manifest_entry(path: Path, archive_path: PurePosixPath, kind: str) -> dict:
    return {
        "path": archive_path.as_posix(),
        "kind": kind,
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="ascii")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _verified_path(archive_path: Path) -> Path:
    return archive_path.with_suffix(archive_path.suffix + ".verified.json")


def _hash_path(archive_path: Path) -> Path:
    return archive_path.with_suffix(archive_path.suffix + ".sha256")


def _safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"Unsafe archive member: {name!r}")
    return path


def _read_manifest(archive: zipfile.ZipFile) -> dict:
    try:
        raw = archive.read("manifest.json")
        manifest = json.loads(raw)
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Backup manifest is missing or invalid") from error
    if manifest.get("format") != "hive-system-backup" or manifest.get("format_version") != FORMAT_VERSION:
        raise ValueError("Unsupported HIVE backup format")
    return manifest


def create_backup(*, db_path: Path = DEFAULT_DB_PATH, root: Path = ROOT,
                  backup_dir: Path = DEFAULT_BACKUP_DIR, app_version: str = "unknown",
                  actor: str = "local-admin", retain: int = 10,
                  now: datetime | None = None) -> dict:
    root = Path(root).resolve()
    db_path = Path(db_path).resolve()
    backup_dir = Path(backup_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    generated = (now or _now()).astimezone(timezone.utc)
    stamp = generated.strftime("%Y%m%dT%H%M%SZ")
    backup_id = uuid.uuid4().hex[:12]
    archive_path = backup_dir / f"hive-backup-{stamp}-{backup_id}.zip"

    with tempfile.TemporaryDirectory(prefix="hive-backup-", dir=backup_dir) as temp_name:
        stage = Path(temp_name)
        database_path = stage / "database" / "hive.db"
        database_check = _copy_database(db_path, database_path)
        entries = [_manifest_entry(database_path, PurePosixPath("database/hive.db"), "database")]

        for source, relative in _state_files(root, db_path):
            destination = stage.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            entries.append(_manifest_entry(destination, relative, "state"))

        manifest = {
            "format": "hive-system-backup",
            "format_version": FORMAT_VERSION,
            "backup_id": backup_id,
            "app_version": app_version,
            "generated_at": generated.isoformat(),
            "created_by": actor,
            "database": database_check,
            "files": entries,
            "restore_requires_stopped_service": True,
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            for path in sorted(item for item in stage.rglob("*") if item.is_file()):
                archive.write(path, path.relative_to(stage).as_posix())

    try:
        archive_path.chmod(0o600)
    except OSError:
        pass
    digest = _sha256(archive_path)
    _write_private(_hash_path(archive_path), f"{digest}  {archive_path.name}\n")
    verified = verify_backup(archive_path, record=True)
    _apply_retention(backup_dir, retain, keep=archive_path)
    return verified


def verify_backup(archive_path: Path, *, record: bool = True) -> dict:
    archive_path = Path(archive_path).resolve()
    if not archive_path.is_file() or archive_path.suffix.lower() != ".zip":
        raise ValueError("Backup archive does not exist")
    expected_hash = None
    hash_path = _hash_path(archive_path)
    if hash_path.is_file():
        expected_hash = hash_path.read_text(encoding="ascii").strip().split()[0].lower()
    archive_hash = _sha256(archive_path)
    if not expected_hash or expected_hash != archive_hash:
        raise ValueError("Backup archive SHA-256 does not match its sidecar")

    with zipfile.ZipFile(archive_path, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("Backup archive contains duplicate members")
        for info in infos:
            _safe_name(info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("Backup archive contains a symbolic link")
        manifest = _read_manifest(archive)
        expected_files = {entry["path"]: entry for entry in manifest.get("files", [])}
        if "database/hive.db" not in expected_files:
            raise ValueError("Backup does not contain the HIVE database")
        if set(names) != set(expected_files) | {"manifest.json"}:
            raise ValueError("Backup contents do not match the manifest")
        for name, entry in expected_files.items():
            with archive.open(name) as stream:
                digest, size = _stream_sha256(stream)
            if digest != entry.get("sha256") or size != entry.get("size"):
                raise ValueError(f"Backup member failed verification: {name}")
        with tempfile.TemporaryDirectory(prefix="hive-verify-") as temp_name:
            database_path = Path(temp_name) / "hive.db"
            with archive.open("database/hive.db") as source, database_path.open("wb") as target:
                shutil.copyfileobj(source, target)
            database_check = _integrity(database_path)
            if not database_check["ok"]:
                raise ValueError("Backup database failed SQLite integrity validation")

    checked_at = _now().isoformat()
    archive_stat = archive_path.stat()
    result = {
        "filename": archive_path.name,
        "path": str(archive_path),
        "backup_id": manifest["backup_id"],
        "app_version": manifest.get("app_version", "unknown"),
        "generated_at": manifest["generated_at"],
        "created_by": manifest.get("created_by", "unknown"),
        "size": archive_stat.st_size,
        "archive_mtime_ns": archive_stat.st_mtime_ns,
        "sha256": archive_hash,
        "file_count": len(expected_files),
        "database": database_check,
        "status": "verified",
        "verified_at": checked_at,
    }
    if record:
        _write_private(_verified_path(archive_path), json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def extract_backup(archive_path: Path, destination: Path) -> dict:
    result = verify_backup(archive_path, record=False)
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Restore staging directory must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(Path(archive_path).resolve(), "r") as archive:
        for info in archive.infolist():
            relative = _safe_name(info.filename)
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    result["extracted_to"] = str(destination)
    return result


def _apply_retention(backup_dir: Path, retain: int, *, keep: Path) -> None:
    if retain < 1:
        return
    archives = sorted(backup_dir.glob("hive-backup-*.zip"), key=lambda path: path.stat().st_mtime,
                      reverse=True)
    for archive in archives[retain:]:
        if archive == keep:
            continue
        archive.unlink(missing_ok=True)
        _hash_path(archive).unlink(missing_ok=True)
        _verified_path(archive).unlink(missing_ok=True)


def snapshot(*, db_path: Path = DEFAULT_DB_PATH,
             backup_dir: Path = DEFAULT_BACKUP_DIR) -> dict:
    db_path = Path(db_path).resolve()
    backup_dir = Path(backup_dir).resolve()
    backups = []
    if backup_dir.is_dir():
        for archive in sorted(backup_dir.glob("hive-backup-*.zip"),
                              key=lambda path: path.stat().st_mtime, reverse=True):
            verified_path = _verified_path(archive)
            item = {
                "filename": archive.name, "path": str(archive),
                "size": archive.stat().st_size, "status": "not_verified",
            }
            if verified_path.is_file():
                try:
                    record = json.loads(verified_path.read_text(encoding="utf-8"))
                    archive_stat = archive.stat()
                    if (record.get("size") == archive_stat.st_size
                            and record.get("archive_mtime_ns") == archive_stat.st_mtime_ns):
                        item.update(record)
                except (OSError, ValueError, json.JSONDecodeError):
                    item["status"] = "verification_record_invalid"
            backups.append(item)
    latest = backups[0] if backups else None
    return {
        "database_path": str(db_path),
        "database_exists": db_path.is_file(),
        "backup_directory": str(backup_dir),
        "backup_count": len(backups),
        "latest": latest,
        "backups": backups[:20],
        "restore_mode": "local_administrator_script",
        "restore_command": r".\deploy\windows\restore-hive.ps1 -BackupPath <verified-backup.zip>",
        "upgrade_command": r".\deploy\windows\upgrade-hive.ps1 -BundlePath <offline-release.zip>",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create, verify, or extract HIVE system backups")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    create.add_argument("--root", type=Path, default=ROOT)
    create.add_argument("--output", type=Path, default=DEFAULT_BACKUP_DIR)
    create.add_argument("--version", default="unknown")
    create.add_argument("--actor", default="local-admin")
    create.add_argument("--retain", type=int, default=10)
    verify = subparsers.add_parser("verify")
    verify.add_argument("archive", type=Path)
    extract = subparsers.add_parser("extract")
    extract.add_argument("archive", type=Path)
    extract.add_argument("destination", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "create":
        result = create_backup(db_path=args.db, root=args.root, backup_dir=args.output,
                               app_version=args.version, actor=args.actor, retain=args.retain)
    elif args.command == "verify":
        result = verify_backup(args.archive)
    else:
        result = extract_backup(args.archive, args.destination)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
