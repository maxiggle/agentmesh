import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import ParamSpec, TypeVar

import pytest

from agentmesh.service.direct_sync import DirectSync, RoundReport
from agentmesh.sync.session import SyncError, SyncResult
from agentmesh.sync.wire import SyncReader, SyncWriter

A, B, C = (bytes([n]) * 33 for n in (1, 2, 3))
EA, EB, EC = (bytes([n]) * 32 for n in (11, 12, 13))
RESULT = SyncResult(b"", True, 0, 0, 0)
P = ParamSpec("P")
T = TypeVar("T")


def bounded(test: Callable[P, Awaitable[T]]) -> Callable[P, T]:
    @functools.wraps(test)
    def run(*args: P.args, **kwargs: P.kwargs) -> T:
        return asyncio.run(asyncio.wait_for(test(*args, **kwargs), 20))

    return run


class FakeWriter:
    def __init__(self) -> None:
        self.closed = 0

    def write(self, data: bytes, /) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None:
        self.closed += 1


class FakeReader:
    async def readexactly(self, n: int, /) -> bytes:
        return b"\x00" * n

    async def read(self) -> bytes:
        return b""


class FakeTransport:
    def __init__(self, unreachable: frozenset[bytes] = frozenset()) -> None:
        self.unreachable = unreachable
        self.endpoint_id = b"\x09" * 32
        self.started = False
        self.closed = False
        self.dialed: list[tuple[bytes, Sequence[str]]] = []
        self.writers: list[FakeWriter] = []
        self.inbound: asyncio.Queue[tuple[bytes, SyncReader, SyncWriter] | None] = asyncio.Queue()

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True
        self.inbound.put_nowait(None)

    async def dial(
        self, endpoint_id: bytes, direct: Sequence[str] = ()
    ) -> tuple[SyncReader, SyncWriter]:
        self.dialed.append((endpoint_id, direct))
        if endpoint_id in self.unreachable:
            raise ConnectionError("no route")
        writer = FakeWriter()
        self.writers.append(writer)
        return FakeReader(), writer

    async def accept(self) -> tuple[bytes, SyncReader, SyncWriter] | None:
        return await self.inbound.get()


class FakeNode:
    def __init__(self) -> None:
        self.failing: set[int] = set()
        self.calls = 0
        self.active = 0
        self.peak = 0
        self.gate: asyncio.Event | None = None
        self.responded = 0

    async def initiate(self, reader: SyncReader, writer: SyncWriter) -> SyncResult:
        self.calls += 1
        if (self.calls - 1) in self.failing:
            raise SyncError("peer failed the handshake")
        await self._hold()
        return RESULT

    async def respond(self, reader: SyncReader, writer: SyncWriter) -> SyncResult:
        await self._hold()
        self.responded += 1
        return RESULT

    async def _hold(self) -> None:
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.gate is not None:
                await self.gate.wait()
        finally:
            self.active -= 1


def _sync(
    node: FakeNode,
    transport: FakeTransport,
    peers: list[tuple[bytes, bytes]],
    *,
    announce: Callable[[bytes], object] = lambda _: None,
    max_outbound: int = 8,
    max_inbound: int = 16,
) -> DirectSync:
    return DirectSync(
        node,
        transport,
        lambda: peers,
        announce=announce,
        interval=60,
        max_outbound=max_outbound,
        max_inbound=max_inbound,
    )


@bounded
async def test_every_listed_peer_is_dialed_each_round() -> None:
    transport = FakeTransport()
    sync = _sync(FakeNode(), transport, [(A, EA), (B, EB)])
    report = await sync.sync_all()
    await sync.sync_all()
    assert [endpoint for endpoint, _ in transport.dialed] == [EA, EB, EA, EB]
    assert report == RoundReport(frozenset({A, B}), frozenset())
    assert all(writer.closed == 1 for writer in transport.writers)


@bounded
async def test_direct_addresses_are_passed_to_the_dial() -> None:
    transport = FakeTransport()
    sync = DirectSync(
        FakeNode(), transport, lambda: [(A, EA)], addresses=lambda _: ["127.0.0.1:1"], interval=60
    )
    await sync.sync_all()
    assert transport.dialed == [(EA, ["127.0.0.1:1"])]


@bounded
async def test_a_failing_peer_does_not_stop_the_others(caplog: pytest.LogCaptureFixture) -> None:
    transport = FakeTransport(unreachable=frozenset({EA}))
    node = FakeNode()
    node.failing = {0}
    sync = _sync(node, transport, [(A, EA), (B, EB), (C, EC)])
    with caplog.at_level(logging.INFO):
        report = await sync.sync_all()
    assert report == RoundReport(frozenset({C}), frozenset({A, B}))
    assert sync.last_round == report
    assert len(transport.writers) == 2
    assert all(writer.closed == 1 for writer in transport.writers)
    assert "failed" in caplog.text


@bounded
async def test_outbound_sessions_are_bounded() -> None:
    node = FakeNode()
    node.gate = asyncio.Event()
    peers = [(bytes([i]) * 33, bytes([i]) * 32) for i in range(1, 7)]
    sync = _sync(node, FakeTransport(), peers, max_outbound=2)
    round_ = asyncio.ensure_future(sync.sync_all())
    for _ in range(20):
        await asyncio.sleep(0)
    assert node.peak == 2
    node.gate.set()
    assert len((await round_).reached) == 6
    assert node.peak == 2


@bounded
async def test_inbound_sessions_are_bounded() -> None:
    node = FakeNode()
    node.gate = asyncio.Event()
    transport = FakeTransport()
    sync = _sync(node, transport, [], max_inbound=2)
    stop = asyncio.Event()
    running = asyncio.ensure_future(sync.run(stop))
    writers = [FakeWriter() for _ in range(4)]
    for writer in writers:
        transport.inbound.put_nowait((A, FakeReader(), writer))
    for _ in range(20):
        await asyncio.sleep(0)
    assert node.peak == 2
    assert [writer.closed for writer in writers] == [0, 0, 1, 1]
    node.gate.set()
    for _ in range(20):
        await asyncio.sleep(0)
    assert [writer.closed for writer in writers] == [1, 1, 1, 1]
    stop.set()
    await running


@bounded
async def test_a_refused_inbound_session_is_closed_and_the_listener_continues() -> None:
    class Refusing(FakeNode):
        async def respond(self, reader: SyncReader, writer: SyncWriter) -> SyncResult:
            await super().respond(reader, writer)
            raise SyncError("peer belongs to another team")

    node = Refusing()
    transport = FakeTransport()
    sync = _sync(node, transport, [])
    stop = asyncio.Event()
    running = asyncio.ensure_future(sync.run(stop))
    writers = [FakeWriter(), FakeWriter()]
    for writer in writers:
        transport.inbound.put_nowait((A, FakeReader(), writer))
    for _ in range(20):
        await asyncio.sleep(0)
    assert [writer.closed for writer in writers] == [1, 1]
    stop.set()
    await running


@bounded
async def test_run_starts_announces_and_polls() -> None:
    transport = FakeTransport()
    announced: list[bytes] = []
    node = FakeNode()
    sync = _sync(node, transport, [(A, EA)], announce=announced.append)
    stop = asyncio.Event()
    running = asyncio.ensure_future(sync.run(stop))
    for _ in range(20):
        await asyncio.sleep(0)
    assert transport.started
    assert announced == [transport.endpoint_id]
    assert sync.last_round.reached == {A}
    sync.poke()
    for _ in range(20):
        await asyncio.sleep(0)
    assert node.calls == 2
    assert announced == [transport.endpoint_id] * 2
    stop.set()
    await running


@bounded
async def test_stop_closes_the_transport_and_ends_every_session() -> None:
    node = FakeNode()
    node.gate = asyncio.Event()
    transport = FakeTransport()
    sync = _sync(node, transport, [])
    stop = asyncio.Event()
    before = asyncio.all_tasks()
    running = asyncio.ensure_future(sync.run(stop))
    writer = FakeWriter()
    transport.inbound.put_nowait((A, FakeReader(), writer))
    for _ in range(20):
        await asyncio.sleep(0)
    assert node.active == 1
    stop.set()
    await asyncio.wait_for(running, 5)
    assert transport.closed
    assert writer.closed == 1
    assert node.active == 0
    assert asyncio.all_tasks() - before - {asyncio.current_task()} == set()
