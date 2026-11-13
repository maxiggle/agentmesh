import asyncio
from collections.abc import Mapping, Sequence
from typing import Protocol

from ehor.service.api import ApiError
from ehor.service.device import Device
from ehor.service.plugins import BackgroundTask, PluginError, ToolHandler, check_method_name
from ehor.service.rpc import Endpoint, RpcServer

DEFAULT_SYNC_INTERVAL = 2.0


class Syncer(Protocol):
    async def run(self, stop: asyncio.Event) -> None: ...

    def poke(self) -> None: ...


class Watcher(Protocol):
    async def check(self) -> int: ...


class Daemon:
    """Serves tool calls and keeps syncing while the agent is closed."""

    def __init__(
        self,
        device: Device,
        endpoint: Endpoint,
        interval: float = DEFAULT_SYNC_INTERVAL,
        peers: Sequence[Syncer] = (),
        *,
        watcher: Watcher | None = None,
        methods: Mapping[str, ToolHandler] | None = None,
        tasks: Sequence[BackgroundTask] = (),
    ) -> None:
        for name in methods or {}:
            check_method_name(name)
        self._device = device
        self._methods = dict(methods or {})
        self._endpoint = endpoint
        self._interval = interval
        self._peers = tuple(peers)
        self._watcher = watcher
        self._tasks = tuple(tasks)

    async def run(self, stop: asyncio.Event) -> None:
        server = await RpcServer(self._endpoint, self._dispatch, (ApiError, PluginError)).start()
        try:
            await asyncio.gather(
                self._sync_folder(stop),
                *(peer.run(stop) for peer in self._peers),
                *(task.run(stop) for task in self._tasks),
            )
        finally:
            server.close()
            await server.wait_closed()

    async def _dispatch(self, method: str, params: Mapping[str, object]) -> str:
        handler = self._methods.get(method)
        if handler is None:
            result = self._device.call(method, params)
        else:
            self._device.sync()
            result = await handler(params)
            self._device.sync()
        for peer in self._peers:
            peer.poke()
        return result

    async def _sync_folder(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self._device.sync()
            if self._watcher is not None:
                await self._watcher.check()
            try:
                await asyncio.wait_for(stop.wait(), self._interval)
            except TimeoutError:
                continue
