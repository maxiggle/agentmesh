import random
from pathlib import Path

import pytest

from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.entry import Entry, Kind
from agentmesh.log.verify import DroppedEntryError, EntryVerifier, RejectedEntryError
from agentmesh.store.ingest import Ingestor, Outcome
from agentmesh.store.local_log import LocalLog
from agentmesh.store.sqlite import SqliteStore

TEAM = b"\x42" * 16
ENTRY_COUNT = 100


@pytest.fixture(scope="module")
def written_log(tmp_path_factory: pytest.TempPathFactory) -> list[Entry]:
    directory = tmp_path_factory.mktemp("device")
    signer = SoftwareSigner(directory / "keys", b"device passphrase")
    handle = signer.create_key("laptop")
    with SqliteStore(directory / "device.db") as store:
        local_log = LocalLog(store, signer, handle, TEAM, EntryVerifier(TEAM))
        entries = [
            local_log.append("#backend", Kind.MESSAGE, f"message {i}".encode())
            for i in range(ENTRY_COUNT)
        ]
        assert store.log(local_log.author) == entries
    return entries


def test_verifier_accepts_every_written_entry(written_log: list[Entry]) -> None:
    verifier = EntryVerifier(TEAM)
    for entry in written_log:
        assert verifier.verify(entry.encode()) == entry


def test_peer_rebuilds_the_log_from_shuffled_delivery(
    written_log: list[Entry], tmp_path: Path
) -> None:
    delivery = written_log[:]
    random.Random(7).shuffle(delivery)  # noqa: S311 - deterministic test order, not crypto
    with SqliteStore(tmp_path / "peer.db") as store:
        ingestor = Ingestor(store, EntryVerifier(TEAM))
        outcomes = {ingestor.receive(entry.encode()).outcome for entry in delivery}
        assert outcomes <= {Outcome.STORED, Outcome.PARKED}
        assert store.log(written_log[0].author) == written_log


def test_every_single_byte_modification_is_rejected(written_log: list[Entry]) -> None:
    verifier = EntryVerifier(TEAM)
    for entry in written_log:
        raw = entry.encode()
        for position in range(len(raw)):
            modified = bytearray(raw)
            modified[position] ^= 0x01
            with pytest.raises((RejectedEntryError, DroppedEntryError)):
                verifier.verify(bytes(modified))
