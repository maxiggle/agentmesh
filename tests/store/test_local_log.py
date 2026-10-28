from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.body import Status, StatusBody
from agentmesh.log.entry import Kind
from agentmesh.log.envelope import Envelope
from agentmesh.log.verify import EntryVerifier, RejectedEntryError
from agentmesh.store.local_log import LocalLog
from agentmesh.store.sqlite import SqliteStore


@pytest.fixture
def store(tmp_db: Path) -> Iterator[SqliteStore]:
    with SqliteStore(tmp_db) as store:
        yield store


@pytest.fixture
def local_log(
    store: SqliteStore, signer: SoftwareSigner, handle: KeyHandle, team: bytes
) -> LocalLog:
    return LocalLog(store, signer, handle, team, EntryVerifier(team), clock=lambda: 42)


def test_first_entry_starts_the_chain(local_log: LocalLog, message: Callable[[str], bytes]) -> None:
    entry = local_log.append("#backend", Kind.MESSAGE, message("hi"))
    assert entry.seq == 1
    assert entry.content.prev is None
    assert entry.content.ts == 42
    assert entry.author == local_log.author


def test_entries_chain_on_the_head(
    local_log: LocalLog, store: SqliteStore, message: Callable[[str], bytes]
) -> None:
    first = local_log.append("#backend", Kind.MESSAGE, message("one"))
    second = local_log.append("#backend", Kind.MESSAGE, message("two"))
    assert second.seq == 2
    assert second.content.prev == first.id
    assert store.log(local_log.author) == [first, second]


def test_refs_are_sorted_and_deduplicated(local_log: LocalLog) -> None:
    refs = [b"\x02" * 32, b"\x01" * 32, b"\x02" * 32]
    body = Envelope.plain(StatusBody(b"\x01" * 32, Status.DONE).encode()).encode()
    entry = local_log.append("#backend", Kind.STATUS, body, refs)
    assert entry.content.refs == (b"\x01" * 32, b"\x02" * 32)


def test_continues_an_existing_log(
    tmp_db: Path,
    signer: SoftwareSigner,
    handle: KeyHandle,
    team: bytes,
    message: Callable[[str], bytes],
) -> None:
    with SqliteStore(tmp_db) as store:
        LocalLog(store, signer, handle, team, EntryVerifier(team)).append(
            "#backend", Kind.MESSAGE, message("one")
        )
    with SqliteStore(tmp_db) as store:
        entry = LocalLog(store, signer, handle, team, EntryVerifier(team)).append(
            "#backend", Kind.MESSAGE, message("two")
        )
        assert entry.seq == 2


def test_refuses_invalid_entries_before_storing(
    local_log: LocalLog, store: SqliteStore, message: Callable[[str], bytes]
) -> None:
    with pytest.raises(RejectedEntryError, match="channel"):
        local_log.append("not-a-channel", Kind.MESSAGE, message("x"))
    assert store.head(local_log.author) is None
