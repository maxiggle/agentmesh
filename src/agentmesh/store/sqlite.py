import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Self

from agentmesh.log.entry import Entry
from agentmesh.store.base import ChainError, DuplicateEntryError

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE entries (
        id BLOB PRIMARY KEY,
        author BLOB NOT NULL,
        seq INTEGER NOT NULL,
        raw BLOB NOT NULL
    ) WITHOUT ROWID;
    CREATE INDEX entries_position ON entries (author, seq);
    CREATE TRIGGER entries_no_update BEFORE UPDATE ON entries
        BEGIN SELECT RAISE(ABORT, 'entries are immutable'); END;
    CREATE TRIGGER entries_no_delete BEFORE DELETE ON entries
        BEGIN SELECT RAISE(ABORT, 'entries are immutable'); END;

    CREATE TABLE forks (
        author BLOB PRIMARY KEY,
        seq INTEGER NOT NULL
    ) WITHOUT ROWID;

    CREATE TABLE parked (
        id BLOB PRIMARY KEY,
        author BLOB NOT NULL,
        seq INTEGER NOT NULL,
        raw BLOB NOT NULL
    ) WITHOUT ROWID;
    CREATE INDEX parked_position ON parked (author, seq);
    """,
)


class SqliteStore:
    def __init__(self, path: Path | str) -> None:
        self._conn = sqlite3.connect(path, isolation_level=None)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    @property
    def schema_version(self) -> int:
        version: int = self._conn.execute("PRAGMA user_version").fetchone()[0]
        return version

    def contains(self, entry_id: bytes) -> bool:
        row = self._conn.execute("SELECT 1 FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return row is not None

    def get(self, entry_id: bytes) -> Entry | None:
        row = self._conn.execute("SELECT raw FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return None if row is None else Entry.decode(row[0])

    def at(self, author: bytes, seq: int) -> list[Entry]:
        return self._entries(
            "SELECT raw FROM entries WHERE author = ? AND seq = ? ORDER BY id", (author, seq)
        )

    def head(self, author: bytes) -> Entry | None:
        rows = self._entries(
            "SELECT raw FROM entries WHERE author = ? ORDER BY seq DESC, id LIMIT 1", (author,)
        )
        return rows[0] if rows else None

    def log(self, author: bytes) -> list[Entry]:
        return self._entries("SELECT raw FROM entries WHERE author = ? ORDER BY seq, id", (author,))

    def append(self, entry: Entry) -> None:
        with self._transaction():
            head = self.head(entry.author)
            expected_seq = 1 if head is None else head.seq + 1
            if entry.seq != expected_seq:
                raise ChainError(f"expected seq {expected_seq}, got {entry.seq}")
            expected_prev = None if head is None else head.id
            if entry.content.prev != expected_prev:
                raise ChainError("prev does not match the current head")
            self._insert(entry)

    def insert(self, entry: Entry) -> None:
        with self._transaction():
            self._insert(entry)

    def mark_forked(self, author: bytes, seq: int) -> None:
        self._conn.execute(
            "INSERT INTO forks (author, seq) VALUES (?, ?) "
            "ON CONFLICT (author) DO UPDATE SET seq = min(seq, excluded.seq)",
            (author, seq),
        )

    def forked_at(self, author: bytes) -> int | None:
        row = self._conn.execute("SELECT seq FROM forks WHERE author = ?", (author,)).fetchone()
        return None if row is None else int(row[0])

    def park(self, entry: Entry) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO parked (id, author, seq, raw) VALUES (?, ?, ?, ?)",
            (entry.id, entry.author, entry.seq, entry.encode()),
        )

    def parked(self, author: bytes, seq: int) -> list[Entry]:
        return self._entries(
            "SELECT raw FROM parked WHERE author = ? AND seq = ? ORDER BY id", (author, seq)
        )

    def unpark(self, entry_id: bytes) -> None:
        self._conn.execute("DELETE FROM parked WHERE id = ?", (entry_id,))

    def _insert(self, entry: Entry) -> None:
        try:
            self._conn.execute(
                "INSERT INTO entries (id, author, seq, raw) VALUES (?, ?, ?, ?)",
                (entry.id, entry.author, entry.seq, entry.encode()),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateEntryError(entry.id.hex()) from exc

    def _entries(self, query: str, params: tuple[object, ...]) -> list[Entry]:
        return [Entry.decode(row[0]) for row in self._conn.execute(query, params)]

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def _migrate(self) -> None:
        for version in range(self.schema_version + 1, len(MIGRATIONS) + 1):
            script = MIGRATIONS[version - 1]
            self._conn.executescript(f"BEGIN; {script} PRAGMA user_version = {version}; COMMIT;")
