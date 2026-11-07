import asyncio
import logging
from collections.abc import Callable

from ehor.service.pacing import pause
from ehor.sync.session import SyncError, SyncNode, SyncResult
from ehor.transport.lan import Discovery

DEFAULT_PEER_INTERVAL = 10.0
_log = logging.getLogger(__name__)


class PeerSync:
    """Keeps this device in sync with the peers a discovery source finds.

    With `listen`, it also accepts sessions from those peers, as on a LAN. Peers that
    only accept connections, like relays, need no listener on this side. `should_sync` is
    asked at the start of each round; when it says no, the round does nothing.
    """

    def __init__(
        self,
        node: SyncNode,
        discovery: Discovery,
        *,
        host: str = "0.0.0.0",  # noqa: S104 - teammates on the LAN must be able to connect
        interval: float = DEFAULT_PEER_INTERVAL,
        listen: bool = True,
        should_sync: Callable[[], bool] = lambda: True,
    ) -> None:
        self._node = node
        self._discovery = discovery
        self._host = host
        self._interval = interval
        self._listen = listen
        self._should_sync = should_sync
        self._wake = asyncio.Event()
        self._port: int | None = None
        discovery.subscribe(self.poke)

    @property
    def port(self) -> int | None:
        return self._port

    def poke(self) -> None:
        """Sync soon, e.g. after a local write or when a new teammate appears."""
        self._wake.set()

    async def run(self, stop: asyncio.Event) -> None:
        server = await self._node.serve(self._host) if self._listen else None
        if server is not None:
            self._port = server.sockets[0].getsockname()[1]
        await self._discovery.start(self._port or 0)
        try:
            while not stop.is_set():
                await self.sync_all()
                await pause(stop, self._wake, self._interval)
        finally:
            await self._discovery.close()
            if server is not None:
                server.close()
                await server.wait_closed()

    async def sync_all(self) -> list[SyncResult]:
        results: list[SyncResult] = []
        if not self._should_sync():
            return results
        for peer in self._discovery.peers():
            try:
                results.append(await self._node.sync_with(peer.host, peer.port))
            except SyncError as exc:
                _log.info("sync with %s failed: %s", peer.key_hint, exc)
        return results
