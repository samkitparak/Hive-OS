"""
Cabinet Vision export folder watcher.

Monitors cv_watch_folder from machines.yaml. When CV exports a new CSV or TXT,
waits 3 seconds (debounce) then re-ingests the whole folder. Idempotent — already-
ingested jobs are skipped by cv_parser.ingest_cv_csv().

Run standalone:
    PYTHONPATH=src python src/cv_watcher.py

Or started automatically by FastAPI lifespan (main.py).
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import yaml
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from db import DB_PATH, init_db
from ingest import ingest_folder

log = logging.getLogger("cv_watcher")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "machines.yaml"
DEBOUNCE_S  = 3.0   # seconds to wait after last file event before ingesting


class _CVHandler(FileSystemEventHandler):
    def __init__(self, watch_dir: Path, conn: sqlite3.Connection):
        self._watch_dir  = watch_dir
        self._conn       = conn
        self._timer: Optional[threading.Timer] = None
        self._lock       = threading.Lock()

    def _schedule(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_S, self._run_ingest)
            self._timer.daemon = True
            self._timer.start()

    def _run_ingest(self):
        log.info("CV export detected — ingesting %s", self._watch_dir)
        try:
            ingest_folder(self._watch_dir, conn=self._conn)
        except Exception as e:
            log.error("Ingest error: %s", e)

    def on_created(self, event):
        if not event.is_directory and Path(event.src_path).suffix.lower() in (".csv", ".txt"):
            log.debug("New file: %s", event.src_path)
            self._schedule()

    def on_modified(self, event):
        if not event.is_directory and Path(event.src_path).suffix.lower() in (".csv", ".txt"):
            self._schedule()


def _get_watch_folder(cfg_path: Path = CONFIG_PATH) -> Optional[Path]:
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    folder = cfg.get("cv_watch_folder")
    if not folder or "TODO" in str(folder):
        return None
    return Path(folder)


def start(conn: Optional[sqlite3.Connection] = None,
          cfg_path: Path = CONFIG_PATH) -> Optional[Observer]:
    """
    Start the watcher in a background thread. Returns the Observer (call
    observer.stop() + observer.join() to shut down), or None if the watch
    folder is not configured yet.
    """
    watch_dir = _get_watch_folder(cfg_path)
    if watch_dir is None:
        log.info("cv_watch_folder not configured — watcher not started (set it in machines.yaml)")
        return None

    if not watch_dir.exists():
        log.warning("cv_watch_folder %s does not exist — watcher not started", watch_dir)
        return None

    if conn is None:
        conn = init_db(DB_PATH, check_same_thread=False)

    handler  = _CVHandler(watch_dir, conn)
    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=True)
    observer.daemon = True
    observer.start()
    log.info("Watching CV export folder: %s", watch_dir)
    return observer


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s")
    observer = start()
    if observer is None:
        print("cv_watch_folder not configured or does not exist.")
        print("Set it in config/machines.yaml and try again.")
    else:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()
