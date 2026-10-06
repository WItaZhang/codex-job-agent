"""SQLite persistence with explicit, short transactions and immutable event history."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .serialization import canonical, digest, utc_now


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "agent.sqlite3"
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported database schema version: {version}")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    namespace TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
                    hash TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(namespace, id)
                );
                CREATE TABLE IF NOT EXISTS applications (
                    job_id TEXT PRIMARY KEY, state TEXT NOT NULL,
                    assessment TEXT, packet TEXT, packet_hash TEXT, approval TEXT,
                    last_error TEXT, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_job ON events(job_id, seq);
                PRAGMA user_version = 1;
            """)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def get(db, namespace: str, key: str):
        row = db.execute("SELECT payload FROM records WHERE namespace=? AND id=?", (namespace, key)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def put(db, namespace: str, key: str, value: object):
        db.execute(
            "INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(namespace,id) DO UPDATE SET "
            "payload=excluded.payload, hash=excluded.hash, updated_at=excluded.updated_at",
            (namespace, key, canonical(value), digest(value), utc_now()),
        )

    @staticmethod
    def records(db, namespace: str) -> list[dict]:
        return [
            json.loads(row[0])
            for row in db.execute("SELECT payload FROM records WHERE namespace=? ORDER BY id", (namespace,))
        ]

    @staticmethod
    def application(db, job_id: str) -> dict:
        row = db.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError(f"Unknown job: {job_id}")
        result = dict(row)
        for key in ("assessment", "packet", "approval"):
            result[key] = json.loads(result[key]) if result[key] else None
        return result

    @staticmethod
    def update_application(db, job_id: str, **changes):
        allowed = {"state", "assessment", "packet", "packet_hash", "approval", "last_error"}
        if not changes or set(changes) - allowed:
            raise ValueError("Invalid application update")
        for key in ("assessment", "packet", "approval"):
            if key in changes and changes[key] is not None:
                changes[key] = canonical(changes[key])
        changes["updated_at"] = utc_now()
        fields = ", ".join(f"{key}=?" for key in changes)
        db.execute(f"UPDATE applications SET {fields} WHERE job_id=?", (*changes.values(), job_id))

    @staticmethod
    def event(db, kind: str, payload: object, job_id: str | None = None):
        db.execute(
            "INSERT INTO events(job_id,kind,payload,created_at) VALUES(?,?,?,?)",
            (job_id, kind, canonical(payload), utc_now()),
        )

    def events(self, job_id: str | None = None) -> list[dict]:
        with self.transaction() as db:
            query = "SELECT * FROM events" + (" WHERE job_id=?" if job_id else "") + " ORDER BY seq"
            rows = db.execute(query, (job_id,) if job_id else ()).fetchall()
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]
