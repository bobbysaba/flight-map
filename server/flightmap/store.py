"""SQLite: a small JSON cache with expiry, and the AeroAPI call ledger."""

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    expires REAL NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS aeroapi_calls (
    ts REAL NOT NULL,
    ident TEXT NOT NULL,
    cost REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.execute("DELETE FROM cache WHERE expires < ?", (time.time(),))

    # -------------------------------------------------------------- cache

    def get(self, kind: str, key: str):
        row = self.db.execute(
            "SELECT value FROM cache WHERE kind = ? AND key = ? AND expires > ?",
            (kind, key, time.time()),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, kind: str, key: str, value, ttl_s: float):
        self.db.execute(
            "INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?)",
            (kind, key, time.time() + ttl_s, json.dumps(value)),
        )

    # -------------------------------------------------------------- ledger

    def log_call(self, ident: str, cost: float):
        self.db.execute("INSERT INTO aeroapi_calls VALUES (?, ?, ?)", (time.time(), ident, cost))

    def cost_since(self, ts: float) -> float:
        row = self.db.execute("SELECT COALESCE(SUM(cost), 0) FROM aeroapi_calls WHERE ts >= ?",
                              (ts,)).fetchone()
        return float(row[0])

    def calls_since(self, ts: float) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM aeroapi_calls WHERE ts >= ?", (ts,)).fetchone()
        return int(row[0])

    def get_kv(self, key: str):
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_kv(self, key: str, value):
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, json.dumps(value)))
