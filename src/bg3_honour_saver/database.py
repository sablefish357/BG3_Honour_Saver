import sqlite3
from contextlib import contextmanager
from dataclasses import astuple, dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True)
class Snapshot:
    id: str
    slot_name: str
    game_id: str
    saved_at: float
    backed_up_at: float
    size: int
    fingerprint: str
    tree_digest: str
    relative_path: str
    preview: str | None
    kind: str

    @property
    def restorable(self) -> bool:
        return bool(self.fingerprint) and self.fingerprint != sha256(b"").hexdigest()


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY,
                    slot_name TEXT NOT NULL COLLATE NOCASE,
                    game_id TEXT NOT NULL,
                    saved_at REAL NOT NULL,
                    backed_up_at REAL NOT NULL,
                    size INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    tree_digest TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    preview TEXT,
                    kind TEXT NOT NULL CHECK (kind IN ('auto', 'before_restore'))
                );
                CREATE INDEX IF NOT EXISTS snapshots_by_time ON snapshots(backed_up_at DESC);
                CREATE TABLE IF NOT EXISTS watch_state (
                    slot_name TEXT PRIMARY KEY COLLATE NOCASE,
                    fingerprint TEXT NOT NULL
                );
            """)

    def history(self) -> list[Snapshot]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM snapshots ORDER BY backed_up_at DESC, id DESC")
            return [Snapshot(**row) for row in rows]

    def get(self, snapshot_id: str) -> Snapshot | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
            return Snapshot(**row) if row else None

    def baseline(self, slot_name: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT fingerprint FROM watch_state WHERE slot_name = ?", (slot_name,)
            ).fetchone()
            return row["fingerprint"] if row else None

    @staticmethod
    def _set_baseline(connection: sqlite3.Connection, slot_name: str, fingerprint: str) -> None:
        connection.execute("""
            INSERT INTO watch_state (slot_name, fingerprint) VALUES (?, ?)
            ON CONFLICT (slot_name) DO UPDATE SET fingerprint = excluded.fingerprint
        """, (slot_name, fingerprint))

    def set_baseline(self, slot_name: str, fingerprint: str) -> None:
        with self.connect() as connection:
            self._set_baseline(connection, slot_name, fingerprint)

    def add(self, snapshot: Snapshot) -> None:
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO snapshots (
                    id, slot_name, game_id, saved_at, backed_up_at, size, fingerprint,
                    tree_digest, relative_path, preview, kind
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, astuple(snapshot))
            if snapshot.kind == "auto":
                self._set_baseline(connection, snapshot.slot_name, snapshot.fingerprint)
