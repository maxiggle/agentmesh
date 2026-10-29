from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.body import Status, StatusBody
from agentmesh.log.entry import Kind
from agentmesh.log.envelope import Envelope
from agentmesh.log.roster import Member, Roster, RosterDevice, Trust
from agentmesh.log.verify import EntryVerifier, RejectedEntryError
from agentmesh.roster.book import RosterBook
from agentmesh.store.base import ChainError
from agentmesh.store.local_log import LocalLog, NotAuthorizedError
from agentmesh.store.sqlite import SqliteStore
from security_key_emulator import SoftwareAuthenticator


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


@pytest.fixture
def roster_body() -> Callable[[bytes], bytes]:
    def build(key: bytes) -> bytes:
        device = RosterDevice(key, Trust.SECURITY_KEY, "yubikey")
        return Roster(1, None, "Acme", ("gef",), (Member("gef", (), ("*",), (device,)),)).encode()

    return build


def test_commits_a_draft_signed_by_a_security_key(
    local_log: LocalLog, store: SqliteStore, roster_body: Callable[[bytes], bytes]
) -> None:
    key = SoftwareAuthenticator()
    draft = local_log.draft(
        "_team", Kind.ROSTER, roster_body(key.public_key), author=key.public_key
    )
    assert (draft.author, draft.seq, draft.prev, draft.ts) == (key.public_key, 1, None, 42)
    entry = local_log.commit(draft.with_assertion(key.sign(draft.signing_input)))
    assert store.log(key.public_key) == [entry]
    assert store.head(local_log.author) is None
    second = local_log.draft("_team", Kind.ROSTER, b"", author=key.public_key)
    assert (second.seq, second.prev) == (2, entry.id)


def test_refuses_a_stale_draft(
    local_log: LocalLog, store: SqliteStore, roster_body: Callable[[bytes], bytes]
) -> None:
    key = SoftwareAuthenticator()
    body = roster_body(key.public_key)
    first = local_log.draft("_team", Kind.ROSTER, body, author=key.public_key)
    stale = local_log.draft("_team", Kind.ROSTER, body, author=key.public_key)
    local_log.commit(first.with_assertion(key.sign(first.signing_input)))
    with pytest.raises(ChainError):
        local_log.commit(stale.with_assertion(key.sign(stale.signing_input)))
    assert len(store.log(key.public_key)) == 1


def test_refuses_a_commit_whose_assertion_does_not_verify(
    local_log: LocalLog, store: SqliteStore, roster_body: Callable[[bytes], bytes]
) -> None:
    key = SoftwareAuthenticator(flags=0)
    draft = local_log.draft(
        "_team", Kind.ROSTER, roster_body(key.public_key), author=key.public_key
    )
    with pytest.raises(RejectedEntryError, match="user presence"):
        local_log.commit(draft.with_assertion(key.sign(draft.signing_input)))
    assert store.head(key.public_key) is None


def test_refuses_an_unauthorized_commit(
    store: SqliteStore,
    signer: SoftwareSigner,
    handle: KeyHandle,
    team: bytes,
    roster_body: Callable[[bytes], bytes],
) -> None:
    key = SoftwareAuthenticator()
    book = RosterBook(b"\x00" * 32)
    local_log = LocalLog(store, signer, handle, team, EntryVerifier(team), authorizer=book)
    draft = local_log.draft(
        "_team", Kind.ROSTER, roster_body(key.public_key), author=key.public_key
    )
    with pytest.raises(NotAuthorizedError, match="pinned"):
        local_log.commit(draft.with_assertion(key.sign(draft.signing_input)))
    assert store.head(key.public_key) is None
