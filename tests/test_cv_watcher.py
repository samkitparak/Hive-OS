"""Tests for cv_watcher.py."""

import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db import init_db
import cv_watcher


CFG = Path(__file__).parent.parent / "config" / "machines.yaml"


@pytest.fixture
def conn():
    c = init_db(":memory:", check_same_thread=False)
    yield c
    c.close()


def test_start_returns_none_when_folder_not_configured(conn, tmp_path):
    """Watcher returns None when cv_watch_folder is a TODO placeholder."""
    fake_cfg = tmp_path / "machines.yaml"
    fake_cfg.write_text('cv_watch_folder: "C:\\\\CabinetVision\\\\Export"  # TODO: verify on-site\n')
    result = cv_watcher.start(conn, fake_cfg)
    assert result is None


def test_start_returns_none_when_folder_missing(conn, tmp_path):
    """Watcher returns None when the configured folder doesn't exist."""
    fake_cfg = tmp_path / "machines.yaml"
    fake_cfg.write_text('cv_watch_folder: "/nonexistent/path/cv_export"\n')
    result = cv_watcher.start(conn, fake_cfg)
    assert result is None


def test_start_returns_observer_when_folder_exists(conn, tmp_path):
    """Watcher starts successfully when folder exists."""
    watch_dir = tmp_path / "cv_export"
    watch_dir.mkdir()
    fake_cfg = tmp_path / "machines.yaml"
    fake_cfg.write_text(f'cv_watch_folder: "{watch_dir}"\n')

    observer = cv_watcher.start(conn, fake_cfg)
    assert observer is not None
    observer.stop()
    observer.join()


def test_new_csv_triggers_ingest(conn, tmp_path):
    """Dropping a CSV into the watch folder triggers ingest_folder."""
    watch_dir = tmp_path / "cv_export"
    watch_dir.mkdir()
    fake_cfg = tmp_path / "machines.yaml"
    fake_cfg.write_text(f'cv_watch_folder: "{watch_dir}"\n')

    ingest_calls = []

    def fake_ingest(root, conn=None):
        ingest_calls.append(root)

    with patch("cv_watcher.ingest_folder", side_effect=fake_ingest):
        observer = cv_watcher.start(conn, fake_cfg)
        assert observer is not None

        # Drop a CSV file into the watch folder
        (watch_dir / "TEST_JOB.csv").write_text("job,data")

        # Wait for debounce + ingest
        time.sleep(cv_watcher.DEBOUNCE_S + 1.5)

        observer.stop()
        observer.join()

    assert len(ingest_calls) >= 1
    assert ingest_calls[0] == watch_dir


def test_debounce_batches_rapid_writes(conn, tmp_path):
    """Multiple rapid file writes produce a single ingest call."""
    watch_dir = tmp_path / "cv_export"
    watch_dir.mkdir()
    fake_cfg = tmp_path / "machines.yaml"
    fake_cfg.write_text(f'cv_watch_folder: "{watch_dir}"\n')

    ingest_calls = []

    def fake_ingest(root, conn=None):
        ingest_calls.append(root)

    with patch("cv_watcher.ingest_folder", side_effect=fake_ingest):
        observer = cv_watcher.start(conn, fake_cfg)

        # Write 3 files rapidly
        for i in range(3):
            (watch_dir / f"JOB{i}.csv").write_text("data")
            time.sleep(0.1)

        time.sleep(cv_watcher.DEBOUNCE_S + 1.5)
        observer.stop()
        observer.join()

    # All 3 rapid writes should collapse into 1 ingest call
    assert len(ingest_calls) == 1
