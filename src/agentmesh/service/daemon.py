import asyncio
from collections.abc import Mapping

from agentmesh.service.api import ApiError
from agentmesh.service.device import Device
from agentmesh.service.lan_sync import LanSync
from agentmesh.service.rpc import Endpoint, RpcServer

DEFAULT_SYNC_INTERVAL = 2.0


class Daemon:
    """Serves tool calls and keeps syncing while Claude is closed."""

    def __init__(
        self,
        device: Device,
        endpoint: Endpoint,
        interval: float = DEFAULT_SYNC_INTERVAL,
        lan: LanSync | None = None,
    ) -> None:
        self._device = device
        self._endpoint = endpoint
        self._interval = interval
        self._lan = lan

    async def run(self, stop: asyncio.Event) -> None:
        server = await RpcServer(self._endpoint, self._dispatch, (ApiError,)).start()
        try:
            await asyncio.gather(
                self._sync_folder(stop),
                *([self._lan.run(stop)] if self._lan is not None else []),
            )
        finally:
            server.close()
            await server.wait_closed()

    def _dispatch(self, method: str, params: Mapping[str, object]) -> str:
        result = self._device.call(method, params)
        if self._lan is not None:
            self._lan.poke()
        return result

    async def _sync_folder(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self._device.sync()
            try:
                await asyncio.wait_for(stop.wait(), self._interval)
            except TimeoutError:
                continue
