import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "hive.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection(db_path: Path = DB_PATH,
                   check_same_thread: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(db_path: Path = DB_PATH,
            check_same_thread: bool = True) -> sqlite3.Connection:
    conn = get_connection(db_path, check_same_thread=check_same_thread)
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    return conn
