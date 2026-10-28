import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.entry import Kind
from agentmesh.log.names import CONTROL_CHANNEL
from agentmesh.log.roster import Member, Roster, RosterDevice, Trust
from agentmesh.log.verify import EntryVerifier
from agentmesh.store.ingest import Ingestor
from agentmesh.store.local_log import LocalLog
from agentmesh.store.sqlite import SqliteStore
from agentmesh.sync.session import SyncError, SyncNode, SyncResult
from agentmesh.sync.wire import Auth, Hello, read_message, write_message

TEAM = b"\x0a" * 16


class Peer:
    def __init__(
        self,
        path: Path,
        signer: SoftwareSigner,
        handle: KeyHandle,
        members: set[bytes],
        team: bytes = TEAM,
        timeout: float = 5.0,
    ) -> None:
        self.store = SqliteStore(path)
        verifier = EntryVerifier(team)
        self.log = LocalLog(self.store, signer, handle, team, verifier)
        self.node = SyncNode(
            self.store,
            Ingestor(self.store, verifier),
            signer,
            handle,
            team,
            is_member=members.__contains__,
            timeout=timeout,
        )


@pytest.fixture
def members() -> set[bytes]:
    return set()


@pytest.fixture
def peers(
    tmp_path: Path,
    signer: SoftwareSigner,
    handle: KeyHandle,
    other_handle: KeyHandle,
    third_handle: KeyHandle,
    members: set[bytes],
) -> Iterator[tuple[Peer, Peer, Peer]]:
    trio = (
        Peer(tmp_path / "a.db", signer, handle, members),
        Peer(tmp_path / "b.db", signer, other_handle, members),
        Peer(tmp_path / "c.db", signer, third_handle, members),
    )
    yield trio
    for peer in trio:
        peer.store.close()


async def _sync(initiator: Peer, responder: Peer) -> SyncResult:
    server = await responder.node.serve("127.0.0.1")
    port = server.sockets[0].getsockname()[1]
    try:
        return await initiator.node.sync_with("127.0.0.1", port)
    finally:
        server.close()
        await server.wait_closed()


def _roster(key: bytes) -> bytes:
    device = RosterDevice(key, Trust.SOFTWARE, "x")
    return Roster(1, None, "", ("gef",), (Member("gef", (), ("*",), (device,)),)).encode()


def test_members_swap_missing_entries(
    peers: tuple[Peer, Peer, Peer], members: set[bytes], message: Callable[[str], bytes]
) -> None:
    a, b, _ = peers
    members.update({a.log.author, b.log.author})
    for i in range(60):
        a.log.append("#backend", Kind.MESSAGE, message(f"a{i}"))
    b.log.append("#backend", Kind.MESSAGE, message("b0"))
    result = asyncio.run(_sync(a, b))
    assert (result.peer, result.peer_is_member) == (b.log.author, True)
    assert (result.sent, result.received, result.stored) == (60, 1, 1)
    assert a.store.frontier() == b.store.frontier() == {a.log.author: 60, b.log.author: 1}
    again = asyncio.run(_sync(b, a))
    assert (again.sent, again.received) == (0, 0)


def test_guests_only_receive_rosters(
    peers: tuple[Peer, Peer, Peer], members: set[bytes], message: Callable[[str], bytes]
) -> None:
    a, b, _ = peers
    members.add(a.log.author)
    a.log.append("#backend", Kind.MESSAGE, message("members only"))
    roster = a.log.append(CONTROL_CHANNEL, Kind.ROSTER, _roster(a.log.author))
    result = asyncio.run(_sync(b, a))
    assert result.received == 1
    assert b.store.log(a.log.author) == []
    assert [e.id for e in b.store.all_parked()] == [roster.id]


def test_entries_are_relayed_through_a_third_peer(
    peers: tuple[Peer, Peer, Peer], members: set[bytes], message: Callable[[str], bytes]
) -> None:
    a, b, c = peers
    members.update({a.log.author, b.log.author, c.log.author})
    entry = a.log.append("#backend", Kind.MESSAGE, message("pass it on"))
    asyncio.run(_sync(a, b))
    asyncio.run(_sync(c, b))
    assert c.store.get(entry.id) == entry


def test_other_team_is_refused(
    tmp_path: Path, signer: SoftwareSigner, other_handle: KeyHandle, peers: tuple[Peer, Peer, Peer]
) -> None:
    stranger = Peer(tmp_path / "x.db", signer, other_handle, set(), team=b"\x0b" * 16)
    with pytest.raises(SyncError, match="another team"):
        asyncio.run(_sync(stranger, peers[0]))
    stranger.store.close()


async def _impostor(target: Peer, key: bytes) -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await write_message(writer, Hello(TEAM, b"\x00" * 32))
        await read_message(reader)
        await write_message(writer, Auth(key, b"\x01" * 64))
        await reader.read()
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        await target.node.sync_with("127.0.0.1", port)
    finally:
        server.close()
        await server.wait_closed()


def test_forged_handshake_is_refused(peers: tuple[Peer, Peer, Peer]) -> None:
    a, b, _ = peers
    with pytest.raises(SyncError, match="handshake"):
        asyncio.run(_impostor(a, b.log.author))


async def _silent(target: Peer) -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(5)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        await target.node.sync_with("127.0.0.1", port)
    finally:
        server.close()


def test_silent_peer_times_out(tmp_path: Path, signer: SoftwareSigner, handle: KeyHandle) -> None:
    peer = Peer(tmp_path / "t.db", signer, handle, set(), timeout=0.2)
    with pytest.raises(SyncError, match="timed out"):
        asyncio.run(_silent(peer))
    peer.store.close()


def test_unreachable_peer(peers: tuple[Peer, Peer, Peer]) -> None:
    with pytest.raises(SyncError, match="cannot reach"):
        asyncio.run(peers[0].node.sync_with("127.0.0.1", 1))
