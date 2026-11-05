import sqlite3
from datetime import datetime, timezone

from app.config import STATE_DIR

DB_PATH = STATE_DIR / "downloads.db"


def connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS downloads (
            path TEXT PRIMARY KEY,
            downloaded_by TEXT NOT NULL,
            downloaded_at TEXT NOT NULL,
            oid TEXT
        )
        """
    )
    conn.commit()
    return conn


def record_download(path: str, downloaded_by: str, oid: str) -> None:
    downloaded_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO downloads (path, downloaded_by, downloaded_at, oid)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                downloaded_by = excluded.downloaded_by,
                downloaded_at = excluded.downloaded_at,
                oid = excluded.oid
            """,
            (path, downloaded_by, downloaded_at, oid),
        )
        conn.commit()
    finally:
        conn.close()


def last_downloads(paths: list[str]) -> dict[str, sqlite3.Row]:
    if not paths:
        return {}
    found: dict[str, sqlite3.Row] = {}
    conn = connect()
    try:
        for start in range(0, len(paths), 400):
            chunk = paths[start : start + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT path, downloaded_by, downloaded_at, oid FROM downloads WHERE path IN ({placeholders})",
                chunk,
            ).fetchall()
            found.update({row["path"]: row for row in rows})
    finally:
        conn.close()
    return found
