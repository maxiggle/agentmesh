import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from agentmesh.log.body import Status, StatusBody
from agentmesh.log.entry import Entry, Kind
from agentmesh.log.envelope import Envelope
from agentmesh.store.base import ChainError, DuplicateEntryError, Store
from agentmesh.store.sqlite import MIGRATIONS, SqliteStore


@pytest.fixture
def store(tmp_db: Path) -> Iterator[SqliteStore]:
    with SqliteStore(tmp_db) as store:
        yield store


@pytest.fixture
def chain(make_entry: Callable[..., Entry]) -> list[Entry]:
    entries: list[Entry] = []
    for seq in range(1, 4):
        prev = entries[-1].id if entries else None
        entries.append(make_entry(seq=seq, prev=prev, ts=seq))
    return entries


def test_implements_store_protocol(store: SqliteStore) -> None:
    protocol: Store = store
    assert protocol is store


def test_new_store_is_migrated(store: SqliteStore) -> None:
    assert store.schema_version == len(MIGRATIONS)


def test_reopening_keeps_entries_and_schema(tmp_db: Path, chain: list[Entry]) -> None:
    with SqliteStore(tmp_db) as store:
        for entry in chain:
            store.append(entry)
    with SqliteStore(tmp_db) as reopened:
        assert reopened.schema_version == len(MIGRATIONS)
        assert reopened.log(chain[0].author) == chain


def test_append_builds_a_chain(store: SqliteStore, chain: list[Entry]) -> None:
    for entry in chain:
        store.append(entry)
    assert store.head(chain[0].author) == chain[-1]
    assert store.get(chain[1].id) == chain[1]
    assert store.contains(chain[2].id)
    assert store.at(chain[0].author, 2) == [chain[1]]


def test_empty_log(store: SqliteStore, chain: list[Entry]) -> None:
    assert store.head(chain[0].author) is None
    assert store.log(chain[0].author) == []
    assert store.get(chain[0].id) is None
    assert not store.contains(chain[0].id)


def test_append_rejects_a_gap(store: SqliteStore, chain: list[Entry]) -> None:
    store.append(chain[0])
    with pytest.raises(ChainError, match="seq"):
        store.append(chain[2])
    assert store.head(chain[0].author) == chain[0]


def test_append_rejects_a_first_entry_that_is_not_seq_1(
    store: SqliteStore, chain: list[Entry]
) -> None:
    with pytest.raises(ChainError, match="seq"):
        store.append(chain[1])


def test_append_rejects_wrong_prev(
    store: SqliteStore, chain: list[Entry], make_entry: Callable[..., Entry]
) -> None:
    store.append(chain[0])
    with pytest.raises(ChainError, match="prev"):
        store.append(make_entry(seq=2, prev=b"\x00" * 32))


def test_append_rejects_a_rewrite(store: SqliteStore, chain: list[Entry]) -> None:
    store.append(chain[0])
    store.append(chain[1])
    with pytest.raises(ChainError):
        store.append(chain[1])


def test_insert_rejects_duplicates(store: SqliteStore, chain: list[Entry]) -> None:
    store.insert(chain[0])
    with pytest.raises(DuplicateEntryError):
        store.insert(chain[0])


@pytest.mark.parametrize(
    "statement",
    ["UPDATE entries SET raw = x'00'", "DELETE FROM entries"],
)
def test_entries_are_immutable_in_the_database(
    tmp_db: Path, store: SqliteStore, chain: list[Entry], statement: str
) -> None:
    store.append(chain[0])
    raw = sqlite3.connect(tmp_db)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        raw.execute(statement)
    raw.close()
    assert store.log(chain[0].author) == [chain[0]]


def test_forks_keep_the_earliest_seq(store: SqliteStore, chain: list[Entry]) -> None:
    author = chain[0].author
    assert store.forked_at(author) is None
    store.mark_forked(author, 5)
    store.mark_forked(author, 3)
    store.mark_forked(author, 7)
    assert store.forked_at(author) == 3


def test_parking(store: SqliteStore, chain: list[Entry]) -> None:
    author = chain[0].author
    store.park(chain[2])
    store.park(chain[2])
    assert store.parked(author, 3) == [chain[2]]
    assert not store.contains(chain[2].id)
    store.unpark(chain[2].id)
    assert store.parked(author, 3) == []


def _status(target: bytes) -> bytes:
    return Envelope.plain(StatusBody(target, Status.DONE).encode()).encode()


def test_arrivals_follow_local_arrival_order(
    store: SqliteStore, chain: list[Entry], make_entry: Callable[..., Entry]
) -> None:
    store.insert(chain[1])
    store.insert(chain[0])
    positions = store.arrivals()
    assert [a.entry for a in positions] == [chain[1], chain[0]]
    assert positions[0].position < positions[1].position
    assert [a.entry for a in store.arrivals(after=positions[0].position)] == [chain[0]]
    assert [a.entry for a in store.arrivals(limit=1)] == [chain[1]]


def test_arrivals_filter_by_kind(
    store: SqliteStore, chain: list[Entry], make_entry: Callable[..., Entry]
) -> None:
    store.append(chain[0])
    status = make_entry(
        seq=2, prev=chain[0].id, kind=Kind.STATUS, refs=(chain[0].id,), body=_status(chain[0].id)
    )
    store.append(status)
    assert [a.entry for a in store.arrivals(kinds=[Kind.STATUS])] == [status]
    assert [a.entry for a in store.arrivals(kinds=[Kind.MESSAGE, Kind.STATUS])] == [
        chain[0],
        status,
    ]


def test_read_marks(store: SqliteStore, chain: list[Entry]) -> None:
    for entry in chain:
        store.append(entry)
    store.mark_read([chain[0].id, chain[2].id])
    store.mark_read([chain[0].id])
    assert store.is_read(chain[0].id)
    assert not store.is_read(chain[1].id)
    assert [a.entry for a in store.arrivals(unread_only=True)] == [chain[1]]


def test_referencing(
    store: SqliteStore, chain: list[Entry], make_entry: Callable[..., Entry]
) -> None:
    store.append(chain[0])
    reply = make_entry(seq=2, prev=chain[0].id, refs=(chain[0].id,))
    store.append(reply)
    assert store.referencing(chain[0].id) == [reply]
    assert store.referencing(reply.id) == []


def test_find_by_prefix(store: SqliteStore, chain: list[Entry]) -> None:
    for entry in chain:
        store.append(entry)
    assert store.find_by_prefix(chain[1].id[:4]) == [chain[1]]
    assert store.find_by_prefix(b"") == sorted(chain, key=lambda e: e.id)


def test_upgrading_from_version_1_indexes_existing_entries(
    tmp_db: Path, chain: list[Entry], make_entry: Callable[..., Entry]
) -> None:
    conn = sqlite3.connect(tmp_db, isolation_level=None)
    for statement in MIGRATIONS[0].statements:
        conn.execute(statement)
    conn.execute("PRAGMA user_version = 1")
    reply = make_entry(seq=2, prev=chain[0].id, refs=(chain[0].id,))
    for entry in (chain[0], reply):
        conn.execute(
            "INSERT INTO entries (id, author, seq, raw) VALUES (?, ?, ?, ?)",
            (entry.id, entry.author, entry.seq, entry.encode()),
        )
    conn.close()
    with SqliteStore(tmp_db) as store:
        assert store.schema_version == len(MIGRATIONS)
        assert [a.entry for a in store.arrivals()] == [chain[0], reply]
        assert store.referencing(chain[0].id) == [reply]


def test_failed_append_rolls_back(store: SqliteStore, chain: list[Entry]) -> None:
    store.append(chain[0])
    with pytest.raises(ChainError):
        store.append(chain[2])
    store.append(chain[1])
    assert store.log(chain[0].author) == chain[:2]
