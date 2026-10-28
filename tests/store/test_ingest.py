from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.entry import Entry, EntryContent, Signature
from agentmesh.log.verify import MAX_ENTRY_SIZE, EntryVerifier
from agentmesh.store.ingest import Ingestor, Outcome
from agentmesh.store.sqlite import SqliteStore

CURVE_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


@pytest.fixture
def store(tmp_db: Path) -> Iterator[SqliteStore]:
    with SqliteStore(tmp_db) as store:
        yield store


@pytest.fixture
def ingestor(store: SqliteStore, team: bytes) -> Ingestor:
    return Ingestor(store, EntryVerifier(team))


@pytest.fixture
def chain(make_entry: Callable[..., Entry]) -> list[Entry]:
    entries: list[Entry] = []
    for seq in range(1, 6):
        prev = entries[-1].id if entries else None
        entries.append(make_entry(seq=seq, prev=prev, ts=seq))
    return entries


def test_stores_a_chain_in_order(
    ingestor: Ingestor, store: SqliteStore, chain: list[Entry]
) -> None:
    for entry in chain:
        receipt = ingestor.receive(entry.encode())
        assert receipt.outcome is Outcome.STORED
        assert receipt.entry_id == entry.id
    assert store.log(chain[0].author) == chain


def test_ignores_duplicates(ingestor: Ingestor, chain: list[Entry]) -> None:
    ingestor.receive(chain[0].encode())
    assert ingestor.receive(chain[0].encode()).outcome is Outcome.IGNORED


def test_signature_variant_is_a_duplicate_not_a_fork(
    ingestor: Ingestor, store: SqliteStore, chain: list[Entry]
) -> None:
    entry = chain[0]
    s = int.from_bytes(entry.sig.value[32:], "big")
    high_s = entry.sig.value[:32] + (CURVE_ORDER - s).to_bytes(32, "big")
    variant = replace(entry, sig=Signature("es256", high_s))
    ingestor.receive(entry.encode())
    assert ingestor.receive(variant.encode()).outcome is Outcome.IGNORED
    assert store.forked_at(entry.author) is None


def test_reordered_delivery_parks_then_releases(
    ingestor: Ingestor, store: SqliteStore, chain: list[Entry]
) -> None:
    for entry in reversed(chain[1:]):
        assert ingestor.receive(entry.encode()).outcome is Outcome.PARKED
    assert store.log(chain[0].author) == []
    receipt = ingestor.receive(chain[0].encode())
    assert receipt.outcome is Outcome.STORED
    assert receipt.released == tuple(e.id for e in chain[1:])
    assert store.log(chain[0].author) == chain
    assert store.parked(chain[0].author, 3) == []


def test_parked_entry_received_again_after_release(
    ingestor: Ingestor, store: SqliteStore, chain: list[Entry]
) -> None:
    ingestor.receive(chain[1].encode())
    ingestor.receive(chain[0].encode())
    assert ingestor.receive(chain[1].encode()).outcome is Outcome.IGNORED
    assert store.forked_at(chain[0].author) is None


def test_rejects_entry_whose_prev_skips_an_entry(
    ingestor: Ingestor, store: SqliteStore, chain: list[Entry], make_entry: Callable[..., Entry]
) -> None:
    ingestor.receive(chain[0].encode())
    ingestor.receive(chain[1].encode())
    reordered = make_entry(seq=3, prev=chain[0].id)
    receipt = ingestor.receive(reordered.encode())
    assert receipt.outcome is Outcome.REJECTED
    assert receipt.reason == "prev does not match seq - 1"
    assert not store.contains(reordered.id)


def test_parked_entry_with_wrong_prev_is_discarded_on_release(
    ingestor: Ingestor, store: SqliteStore, chain: list[Entry], make_entry: Callable[..., Entry]
) -> None:
    bad = make_entry(seq=2, prev=b"\x00" * 32)
    assert ingestor.receive(bad.encode()).outcome is Outcome.PARKED
    receipt = ingestor.receive(chain[0].encode())
    assert receipt.released == ()
    assert not store.contains(bad.id)
    assert store.parked(bad.author, 2) == []


def test_fork_is_stored_and_flagged(
    ingestor: Ingestor,
    store: SqliteStore,
    chain: list[Entry],
    make_entry: Callable[..., Entry],
    message: Callable[[str], bytes],
) -> None:
    ingestor.receive(chain[0].encode())
    ingestor.receive(chain[1].encode())
    fork = make_entry(seq=2, prev=chain[0].id, body=message("different"))
    receipt = ingestor.receive(fork.encode())
    assert receipt.outcome is Outcome.FORKED
    assert store.forked_at(chain[0].author) == 2
    assert {e.id for e in store.at(chain[0].author, 2)} == {chain[1].id, fork.id}


def test_rejects_tampered_entry(ingestor: Ingestor, store: SqliteStore, chain: list[Entry]) -> None:
    tampered = replace(chain[0], content=replace(chain[0].content, body=b"tampered"))
    receipt = ingestor.receive(tampered.encode())
    assert receipt.outcome is Outcome.REJECTED
    assert receipt.reason == "bad signature"
    assert not store.contains(tampered.id)


def test_rejects_entry_forged_with_another_key(
    ingestor: Ingestor,
    make_content: Callable[..., EntryContent],
    signer: SoftwareSigner,
    other_handle: KeyHandle,
) -> None:
    forged = make_content().sign(signer, other_handle)
    assert ingestor.receive(forged.encode()).outcome is Outcome.REJECTED


def test_rejects_malformed_bytes(ingestor: Ingestor) -> None:
    receipt = ingestor.receive(b"not cbor at all")
    assert receipt.outcome is Outcome.REJECTED
    assert receipt.entry_id is None


def test_drops_oversize_entry(ingestor: Ingestor) -> None:
    assert ingestor.receive(b"\x00" * (MAX_ENTRY_SIZE + 1)).outcome is Outcome.DROPPED


def test_rejects_other_team(store: SqliteStore, chain: list[Entry]) -> None:
    receipt = Ingestor(store, EntryVerifier(b"\xee" * 16)).receive(chain[0].encode())
    assert receipt.outcome is Outcome.REJECTED
    assert receipt.reason == "wrong team"
