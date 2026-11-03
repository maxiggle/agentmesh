from collections.abc import Callable
from pathlib import Path

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.canonical import encode
from agentmesh.log.endpoint_announce import EndpointAnnounce
from agentmesh.log.entry import Entry, Kind
from agentmesh.log.verify import EntryVerifier
from agentmesh.service.endpoints import EndpointDirectory
from agentmesh.store.local_log import LocalLog
from agentmesh.store.sqlite import SqliteStore


def test_directory_uses_the_latest_announce(
    tmp_db: Path, signer: SoftwareSigner, handle: KeyHandle, team: bytes
) -> None:
    store = SqliteStore(tmp_db)
    local_log = LocalLog(store, signer, handle, team, EntryVerifier(team))
    first, second = b"\x01" * 32, b"\x02" * 32
    for iroh in (first, second):
        local_log.append("_team", Kind.ENDPOINT_ANNOUNCE, EndpointAnnounce(iroh).encode())
    assert EndpointDirectory(store).endpoints() == {local_log.author: second}
    store.close()


def test_directory_skips_undecodable_bodies(tmp_db: Path, make_entry: Callable[..., Entry]) -> None:
    store = SqliteStore(tmp_db)
    good = make_entry(
        kind=Kind.ENDPOINT_ANNOUNCE,
        channel="_team",
        body=EndpointAnnounce(b"\x01" * 32).encode(),
    )
    bad = make_entry(
        seq=2,
        prev=good.id,
        kind=Kind.ENDPOINT_ANNOUNCE,
        channel="_team",
        body=encode({"iroh": b"\x01"}),
    )
    store.append(good)
    store.append(bad)
    assert EndpointDirectory(store).endpoints() == {good.author: b"\x01" * 32}
    store.close()
