import asyncio
import logging
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from agentmesh.sync.session import SyncError, SyncResult
from agentmesh.sync.wire import SyncReader, SyncWriter

DEFAULT_DIRECT_INTERVAL = 10.0
MAX_OUTBOUND = 8
MAX_INBOUND = 16
_log = logging.getLogger(__name__)


class SessionRunner(Protocol):
    async def initiate(self, reader: SyncReader, writer: SyncWriter) -> SyncResult: ...

    async def respond(self, reader: SyncReader, writer: SyncWriter) -> SyncResult: ...


class DirectTransport(Protocol):
    @property
    def endpoint_id(self) -> bytes: ...

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def dial(
        self, endpoint_id: bytes, direct: Sequence[str] = ()
    ) -> tuple[SyncReader, SyncWriter]: ...

    async def accept(self) -> tuple[bytes, SyncReader, SyncWriter] | None: ...


@dataclass(frozen=True, slots=True)
class RoundReport:
    """Which devices (by key) the last round reached and which it could not."""

    reached: frozenset[bytes] = frozenset()
    unreached: frozenset[bytes] = frozenset()


class DirectSync:
    """Keeps this device in sync with teammates' devices over direct connections.

    Each round dials every peer from `peers`, and a listener answers the sessions they
    dial. A session with one peer failing never affects the others.
    """

    def __init__(
        self,
        node: SessionRunner,
        transport: DirectTransport,
        peers: Callable[[], Sequence[tuple[bytes, bytes]]],
        *,
        addresses: Callable[[bytes], Sequence[str]] = lambda _: (),
        announce: Callable[[bytes], object] = lambda _: None,
        interval: float = DEFAULT_DIRECT_INTERVAL,
        max_outbound: int = MAX_OUTBOUND,
        max_inbound: int = MAX_INBOUND,
    ) -> None:
        self._node = node
        self._transport = transport
        self._peers = peers
        self._addresses = addresses
        self._announce = announce
        self._interval = interval
        self._max_outbound = max_outbound
        self._max_inbound = max_inbound
        self._wake = asyncio.Event()
        self._inbound: set[asyncio.Task[None]] = set()
        self._last_round = RoundReport()

    @property
    def last_round(self) -> RoundReport:
        return self._last_round

    def poke(self) -> None:
        """Sync soon, e.g. after a local write."""
        self._wake.set()

    async def run(self, stop: asyncio.Event) -> None:
        await self._transport.start()
        listener = asyncio.get_running_loop().create_task(self._listen())
        try:
            while not stop.is_set():
                self._announce(self._transport.endpoint_id)
                await self.sync_all()
                await self._sleep(stop)
        finally:
            listener.cancel()
            sessions = [listener, *self._inbound]
            for session in sessions[1:]:
                session.cancel()
            await asyncio.gather(*sessions, return_exceptions=True)
            await self._transport.close()

    async def sync_all(self) -> RoundReport:
        peers = list(self._peers())
        limit = asyncio.Semaphore(self._max_outbound)

        async def one(device: bytes, endpoint: bytes) -> bool:
            async with limit:
                return await self._sync_with(device, endpoint)

        outcomes = await asyncio.gather(*(one(device, endpoint) for device, endpoint in peers))
        self._last_round = RoundReport(
            frozenset(device for (device, _), ok in zip(peers, outcomes, strict=True) if ok),
            frozenset(device for (device, _), ok in zip(peers, outcomes, strict=True) if not ok),
        )
        return self._last_round

    async def _sync_with(self, device: bytes, endpoint: bytes) -> bool:
        try:
            reader, writer = await self._transport.dial(endpoint, self._addresses(endpoint))
        except (SyncError, OSError) as exc:
            _log.info("direct connection to %s failed: %s", device[1:4].hex(), exc)
            return False
        try:
            await self._node.initiate(reader, writer)
        except (SyncError, EOFError, OSError) as exc:
            _log.info("direct sync with %s failed: %s", device[1:4].hex(), exc)
            return False
        finally:
            writer.close()
        return True

    async def _listen(self) -> None:
        while True:
            accepted = await self._transport.accept()
            if accepted is None:
                return
            _, reader, writer = accepted
            if len(self._inbound) >= self._max_inbound:
                _log.info("too many direct sessions; turning one away")
                writer.close()
                continue
            task = asyncio.get_running_loop().create_task(self._answer(reader, writer))
            self._inbound.add(task)
            task.add_done_callback(self._inbound.discard)

    async def _answer(self, reader: SyncReader, writer: SyncWriter) -> None:
        try:
            await self._node.respond(reader, writer)
        except (SyncError, EOFError, OSError) as exc:
            _log.info("direct sync from a teammate refused: %s", exc)
        finally:
            writer.close()

    async def _sleep(self, stop: asyncio.Event) -> None:
        self._wake.clear()
        waiters = [asyncio.ensure_future(stop.wait()), asyncio.ensure_future(self._wake.wait())]
        with suppress(TimeoutError):
            await asyncio.wait_for(
                asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED), self._interval
            )
        for waiter in waiters:
            waiter.cancel()
