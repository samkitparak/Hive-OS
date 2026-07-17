"""Online backup, archive integrity, retention, and restore staging tests."""

import hashlib
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import resilience


def _site(tmp_path: Path) -> tuple[Path, Path, sqlite3.Connection]:
    root = tmp_path / "site"
    (root / "config").mkdir(parents=True)
    (root / "data" / "mqtt-pki").mkdir(parents=True)
    (root / "data" / "backups").mkdir(parents=True)
    (root / "config" / "machines.yaml").write_text("site: test\n", encoding="utf-8")
    (root / "data" / "mqtt-pki" / "ca.key").write_text("private-ca", encoding="ascii")
    (root / "data" / "backups" / "old.db").write_text("excluded", encoding="ascii")
    db_path = root / "hive.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO events(value) VALUES ('committed-in-wal')")
    conn.commit()
    return root, db_path, conn


def test_online_backup_contains_wal_state_and_protected_files(tmp_path):
    root, db_path, conn = _site(tmp_path)
    try:
        result = resilience.create_backup(
            root=root, db_path=db_path, backup_dir=root / "backups",
            app_version="0.21.0", actor="Test Admin",
            now=datetime(2026, 7, 17, 12, tzinfo=timezone.utc),
        )
    finally:
        conn.close()

    assert result["status"] == "verified"
    assert result["app_version"] == "0.21.0"
    assert result["database"]["ok"] is True
    archive_path = Path(result["path"])
    assert archive_path.stat().st_mode & 0o077 == 0
    assert archive_path.with_suffix(".zip.sha256").is_file()

    extract_dir = tmp_path / "restored"
    resilience.extract_backup(archive_path, extract_dir)
    restored = sqlite3.connect(extract_dir / "database" / "hive.db")
    try:
        assert restored.execute("SELECT value FROM events").fetchone()[0] == "committed-in-wal"
    finally:
        restored.close()
    assert (extract_dir / "config" / "machines.yaml").is_file()
    assert (extract_dir / "data" / "mqtt-pki" / "ca.key").read_text() == "private-ca"
    assert not (extract_dir / "data" / "backups").exists()


def test_verification_detects_outer_and_inner_tampering(tmp_path):
    root, db_path, conn = _site(tmp_path)
    conn.close()
    result = resilience.create_backup(root=root, db_path=db_path, backup_dir=root / "backups")
    archive_path = Path(result["path"])

    with archive_path.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        resilience.verify_backup(archive_path)

    clean = resilience.create_backup(root=root, db_path=db_path, backup_dir=root / "backups")
    archive_path = Path(clean["path"])
    with zipfile.ZipFile(archive_path) as source:
        members = {name: source.read(name) for name in source.namelist()}
    members["config/machines.yaml"] = b"site: altered\n"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, content in members.items():
            target.writestr(name, content)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_suffix(".zip.sha256").write_text(
        f"{digest}  {archive_path.name}\n", encoding="ascii"
    )
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        resilience.verify_backup(archive_path)


def test_extract_rejects_nonempty_destination_and_unsafe_member(tmp_path):
    with pytest.raises(ValueError, match="Unsafe archive member"):
        resilience._safe_name("../hive.db")
    root, db_path, conn = _site(tmp_path)
    conn.close()
    result = resilience.create_backup(root=root, db_path=db_path, backup_dir=root / "backups")
    destination = tmp_path / "occupied"
    destination.mkdir()
    (destination / "keep.txt").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(ValueError, match="must be empty"):
        resilience.extract_backup(Path(result["path"]), destination)


def test_retention_and_snapshot_only_report_managed_archives(tmp_path):
    root, db_path, conn = _site(tmp_path)
    conn.close()
    base = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
    for index in range(3):
        resilience.create_backup(
            root=root, db_path=db_path, backup_dir=root / "backups", retain=2,
            now=base + timedelta(minutes=index),
        )
    (root / "backups" / "unrelated.zip").write_bytes(b"not a HIVE backup")
    status = resilience.snapshot(db_path=db_path, backup_dir=root / "backups")
    assert status["backup_count"] == 2
    assert status["latest"]["status"] == "verified"
    assert status["latest"]["generated_at"].startswith("2026-07-17T12:02")


def test_manifest_is_complete_and_has_no_secret_values_in_summary(tmp_path):
    root, db_path, conn = _site(tmp_path)
    conn.close()
    result = resilience.create_backup(root=root, db_path=db_path, backup_dir=root / "backups")
    with zipfile.ZipFile(result["path"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    paths = {item["path"] for item in manifest["files"]}
    assert paths == {
        "database/hive.db", "config/machines.yaml", "data/mqtt-pki/ca.key",
    }
    assert "private-ca" not in json.dumps(result)
