"""Small JSON record store, one SQLite connection per operation."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(kind,id))")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=15)
        try:
            conn.execute("PRAGMA busy_timeout=15000")
            with conn:
                yield conn
        finally:
            conn.close()

    def put(self, kind: str, identifier: str, payload: dict) -> dict:
        value = {**payload, "id": identifier}
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with self.lock, self.connect() as conn:
            conn.execute("INSERT INTO records(kind,id,payload) VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload", (kind, identifier, encoded))
        return value

    def get(self, kind: str, identifier: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM records WHERE kind=? AND id=?", (kind, identifier)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, kind: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT payload FROM records WHERE kind=? ORDER BY rowid DESC", (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def delete(self, kind: str, identifier: str):
        with self.lock, self.connect() as conn:
            conn.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, identifier))
