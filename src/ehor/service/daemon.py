import asyncio
from collections.abc import Mapping, Sequence
from typing import Protocol

from ehor.service.api import ApiError
from ehor.service.device import Device
from ehor.service.rpc import Endpoint, RpcServer

DEFAULT_SYNC_INTERVAL = 2.0


class Syncer(Protocol):
    async def run(self, stop: asyncio.Event) -> None: ...

    def poke(self) -> None: ...


class Daemon:
    """Serves tool calls and keeps syncing while Claude is closed."""

    def __init__(
        self,
        device: Device,
        endpoint: Endpoint,
        interval: float = DEFAULT_SYNC_INTERVAL,
        peers: Sequence[Syncer] = (),
    ) -> None:
        self._device = device
        self._endpoint = endpoint
        self._interval = interval
        self._peers = tuple(peers)

    async def run(self, stop: asyncio.Event) -> None:
        server = await RpcServer(self._endpoint, self._dispatch, (ApiError,)).start()
        try:
            await asyncio.gather(
                self._sync_folder(stop),
                *(peer.run(stop) for peer in self._peers),
            )
        finally:
            server.close()
            await server.wait_closed()

    def _dispatch(self, method: str, params: Mapping[str, object]) -> str:
        result = self._device.call(method, params)
        for peer in self._peers:
            peer.poke()
        return result

    async def _sync_folder(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self._device.sync()
            try:
                await asyncio.wait_for(stop.wait(), self._interval)
            except TimeoutError:
                continue
