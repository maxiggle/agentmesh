import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.entry import Kind
from agentmesh.log.verify import EntryVerifier
from agentmesh.store.ingest import Ingestor
from agentmesh.store.local_log import LocalLog
from agentmesh.store.sqlite import SqliteStore
from agentmesh.sync.session import SyncError, SyncNode, SyncResult
from agentmesh.sync.wire import SyncReader, SyncWriter


class MemoryReader:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self._closed = False
        self._changed = asyncio.Condition()

    async def feed(self, data: bytes) -> None:
        async with self._changed:
            self._buffer += data
            self._changed.notify_all()

    async def feed_eof(self) -> None:
        async with self._changed:
            self._closed = True
            self._changed.notify_all()

    async def readexactly(self, n: int, /) -> bytes:
        async with self._changed:
            await self._changed.wait_for(lambda: len(self._buffer) >= n or self._closed)
            if len(self._buffer) < n:
                raise asyncio.IncompleteReadError(bytes(self._buffer), n)
            data = bytes(self._buffer[:n])
            del self._buffer[:n]
            return data

    async def read(self) -> bytes:
        async with self._changed:
            await self._changed.wait_for(lambda: self._closed)
            data = bytes(self._buffer)
            self._buffer.clear()
            return data


class MemoryWriter:
    def __init__(self, peer: MemoryReader) -> None:
        self._peer = peer
        self._pending = bytearray()
        self._tasks: set[asyncio.Task[None]] = set()

    def write(self, data: bytes, /) -> None:
        self._pending += data

    async def drain(self) -> None:
        data, self._pending = bytes(self._pending), bytearray()
        await self._peer.feed(data)

    def close(self) -> None:
        task = asyncio.get_running_loop().create_task(self._peer.feed_eof())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


TEAM = b"\x0a" * 16


class Peer:
    def __init__(
        self, path: Path, signer: SoftwareSigner, handle: KeyHandle, members: set[bytes]
    ) -> None:
        self.store = SqliteStore(path)
        verifier = EntryVerifier(TEAM)
        self.log = LocalLog(self.store, signer, handle, TEAM, verifier)
        self.node = SyncNode(
            self.store,
            Ingestor(self.store, verifier),
            signer,
            handle,
            TEAM,
            is_member=members.__contains__,
            timeout=5.0,
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
    members: set[bytes],
) -> Iterator[tuple[Peer, Peer]]:
    pair = (
        Peer(tmp_path / "a.db", signer, handle, members),
        Peer(tmp_path / "b.db", signer, other_handle, members),
    )
    yield pair
    for peer in pair:
        peer.store.close()


def _pair() -> tuple[tuple[SyncReader, SyncWriter], tuple[SyncReader, SyncWriter]]:
    first, second = MemoryReader(), MemoryReader()
    return (first, MemoryWriter(second)), (second, MemoryWriter(first))


async def _respond_then_close(peer: Peer, reader: SyncReader, writer: SyncWriter) -> SyncResult:
    try:
        return await peer.node.respond(reader, writer)
    finally:
        writer.close()


async def _run(initiator: Peer, responder: Peer) -> tuple[SyncResult, SyncResult]:
    (ir, iw), (rr, rw) = _pair()
    served = asyncio.create_task(_respond_then_close(responder, rr, rw))
    try:
        return await initiator.node.initiate(ir, iw), await served
    finally:
        iw.close()


@pytest.mark.parametrize("first", [0, 1])
def test_session_over_memory_streams_in_both_directions(
    peers: tuple[Peer, Peer],
    members: set[bytes],
    message: Callable[[str], bytes],
    first: int,
) -> None:
    a, b = peers
    members.update({a.log.author, b.log.author})
    for i in range(60):
        a.log.append("#backend", Kind.MESSAGE, message(f"a{i}"))
    b.log.append("#backend", Kind.MESSAGE, message("b0"))
    initiator, responder = (a, b) if first == 0 else (b, a)
    init_result, resp_result = asyncio.run(_run(initiator, responder))
    assert (init_result.peer, resp_result.peer) == (responder.log.author, initiator.log.author)
    assert (init_result.sent, init_result.received) == (resp_result.received, resp_result.sent)
    assert (init_result.stored, resp_result.stored) == (resp_result.sent, init_result.sent)
    assert a.store.frontier() == b.store.frontier() == {a.log.author: 60, b.log.author: 1}


def test_peer_closing_mid_handshake_fails_initiate(peers: tuple[Peer, Peer]) -> None:
    async def scenario() -> None:
        (ir, iw), (_, rw) = _pair()
        rw.close()
        await peers[0].node.initiate(ir, iw)

    with pytest.raises(SyncError, match="peer closed"):
        asyncio.run(scenario())


def test_peer_closing_mid_handshake_fails_respond(peers: tuple[Peer, Peer]) -> None:
    async def scenario() -> None:
        (_, iw), (rr, rw) = _pair()
        iw.close()
        await peers[0].node.respond(rr, rw)

    with pytest.raises(SyncError, match="peer closed"):
        asyncio.run(scenario())
