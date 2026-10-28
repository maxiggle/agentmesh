from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.authorize import Decision
from agentmesh.log.entry import Entry, EntryContent, Kind
from agentmesh.log.verify import EntryVerifier
from agentmesh.store.ingest import Ingestor, Outcome
from agentmesh.store.local_log import LocalLog, NotAuthorizedError
from agentmesh.store.sqlite import SqliteStore


class FakeAuthorizer:
    """Allows listed authors; storing an entry from `granter` admits `grantee`."""

    def __init__(self) -> None:
        self.allowed: set[bytes] = set()
        self.denied: set[bytes] = set()
        self.grants: dict[bytes, bytes] = {}
        self.stored_ids: list[bytes] = []

    def authorize(self, entry: Entry) -> Decision:
        if entry.author in self.denied:
            return Decision.deny("denied")
        if entry.author in self.allowed:
            return Decision.allow()
        return Decision.wait("not yet")

    def stored(self, entry: Entry) -> bool:
        self.stored_ids.append(entry.id)
        grantee = self.grants.get(entry.author)
        if grantee is None or grantee in self.allowed:
            return False
        self.allowed.add(grantee)
        return True


@pytest.fixture
def store(tmp_db: Path) -> Iterator[SqliteStore]:
    with SqliteStore(tmp_db) as store:
        yield store


@pytest.fixture
def authorizer() -> FakeAuthorizer:
    return FakeAuthorizer()


@pytest.fixture
def ingestor(store: SqliteStore, team: bytes, authorizer: FakeAuthorizer) -> Ingestor:
    return Ingestor(store, EntryVerifier(team), authorizer)


def _chain(
    make: Callable[..., EntryContent], signer: SoftwareSigner, key: KeyHandle
) -> list[Entry]:
    author = signer.public_key(key)
    entries: list[Entry] = []
    for seq in range(1, 4):
        prev = entries[-1].id if entries else None
        entries.append(make(author=author, seq=seq, prev=prev, ts=seq).sign(signer, key))
    return entries


def test_allowed_entries_are_stored(
    ingestor: Ingestor, authorizer: FakeAuthorizer, make_entry: Callable[..., Entry]
) -> None:
    entry = make_entry()
    authorizer.allowed.add(entry.author)
    assert ingestor.receive(entry.encode()).outcome is Outcome.STORED
    assert authorizer.stored_ids == [entry.id]


def test_denied_entries_are_rejected(
    ingestor: Ingestor,
    store: SqliteStore,
    authorizer: FakeAuthorizer,
    make_entry: Callable[..., Entry],
) -> None:
    entry = make_entry()
    authorizer.denied.add(entry.author)
    receipt = ingestor.receive(entry.encode())
    assert (receipt.outcome, receipt.reason) == (Outcome.REJECTED, "denied")
    assert not store.contains(entry.id)
    assert store.all_parked() == []


def test_waiting_entries_are_released_when_authorization_changes(
    ingestor: Ingestor,
    store: SqliteStore,
    authorizer: FakeAuthorizer,
    make_content: Callable[..., EntryContent],
    signer: SoftwareSigner,
    handle: KeyHandle,
    other_handle: KeyHandle,
) -> None:
    admin = _chain(make_content, signer, handle)
    newcomer = _chain(make_content, signer, other_handle)
    authorizer.allowed.add(admin[0].author)
    authorizer.grants[admin[0].author] = newcomer[0].author
    for entry in newcomer:
        receipt = ingestor.receive(entry.encode())
        assert receipt.outcome is Outcome.PARKED
    assert len(store.all_parked()) == 3
    receipt = ingestor.receive(admin[0].encode())
    assert receipt.outcome is Outcome.STORED
    assert set(receipt.released) == {entry.id for entry in newcomer}
    assert store.log(newcomer[0].author) == newcomer
    assert store.all_parked() == []


def test_local_log_refuses_unauthorized_writes(
    store: SqliteStore,
    authorizer: FakeAuthorizer,
    signer: SoftwareSigner,
    handle: KeyHandle,
    team: bytes,
    message: Callable[[str], bytes],
) -> None:
    local_log = LocalLog(store, signer, handle, team, EntryVerifier(team), authorizer=authorizer)
    with pytest.raises(NotAuthorizedError, match="not yet"):
        local_log.append("#backend", Kind.MESSAGE, message("hi"))
    assert store.head(local_log.author) is None
    authorizer.allowed.add(local_log.author)
    entry = local_log.append("#backend", Kind.MESSAGE, message("hi"))
    assert authorizer.stored_ids == [entry.id]
