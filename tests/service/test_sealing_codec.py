from collections.abc import Callable
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from agentmesh.crypto.sealing import public_bytes
from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.entry import Entry, EntryContent, Kind
from agentmesh.log.key_announce import KeyAnnounce
from agentmesh.log.verify import EntryVerifier
from agentmesh.roster.book import RosterBook
from agentmesh.service.sealing import KeyDirectory, SealingCodec
from agentmesh.store.local_log import LocalLog
from agentmesh.store.sqlite import SqliteStore


def test_key_directory_uses_the_latest_announce(
    tmp_db: Path, signer: SoftwareSigner, handle: KeyHandle, team: bytes
) -> None:
    store = SqliteStore(tmp_db)
    local_log = LocalLog(store, signer, handle, team, EntryVerifier(team))
    first, second = b"\x01" * 32, b"\x02" * 32
    for key in (first, second):
        local_log.append("_team", Kind.KEY_ANNOUNCE, KeyAnnounce(key).encode())
    assert KeyDirectory(store).encryption_keys() == {local_log.author: second}
    store.close()


def test_codec_round_trip_and_unreadable_bodies(
    tmp_db: Path,
    signer: SoftwareSigner,
    handle: KeyHandle,
    team: bytes,
    make_content: Callable[..., EntryContent],
    message: Callable[[str], bytes],
) -> None:
    store = SqliteStore(tmp_db)
    author = signer.public_key(handle)
    private = X25519PrivateKey.generate()
    codec = SealingCodec(RosterBook(b"\x00" * 32), KeyDirectory(store), private, author, team)
    assert codec.public_key == public_bytes(private)
    assert [r.public for r in codec.recipients("#backend")] == [codec.public_key]

    sealed = codec.seal("#backend", Kind.MESSAGE, b"body bytes")
    entry = make_content(body=sealed).sign(signer, handle)
    assert codec.open(entry) == b"body bytes"

    moved: Entry = make_content(body=sealed, channel="#other").sign(signer, handle)
    assert codec.open(moved) is None
    stranger = SealingCodec(
        RosterBook(b"\x00" * 32), KeyDirectory(store), X25519PrivateKey.generate(), author, team
    )
    assert stranger.open(entry) is None
    assert codec.open(make_content(body=b"not an envelope").sign(signer, handle)) is None
    assert codec.open(make_content(body=message("plain")).sign(signer, handle)) is not None
    store.close()
