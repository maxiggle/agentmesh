import asyncio

from agentmesh.service.api import ApiError
from agentmesh.service.device import Device
from agentmesh.service.rpc import Endpoint, RpcServer

DEFAULT_SYNC_INTERVAL = 2.0


class Daemon:
    """Serves tool calls and keeps syncing while Claude is closed."""

    def __init__(
        self, device: Device, endpoint: Endpoint, interval: float = DEFAULT_SYNC_INTERVAL
    ) -> None:
        self._device = device
        self._endpoint = endpoint
        self._interval = interval

    async def run(self, stop: asyncio.Event) -> None:
        server = await RpcServer(self._endpoint, self._device.call, (ApiError,)).start()
        try:
            while not stop.is_set():
                self._device.sync()
                try:
                    await asyncio.wait_for(stop.wait(), self._interval)
                except TimeoutError:
                    continue
        finally:
            server.close()
            await server.wait_closed()
