import asyncio
import hashlib
import logging
import secrets
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

SERVICE_TYPE = "_agentmesh._tcp.local."
_RESOLVE_TIMEOUT_MS = 3000
_TAG_BYTES = 8
_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PeerAddress:
    host: str
    port: int
    key_hint: str


class Discovery(Protocol):
    async def start(self, port: int) -> None: ...

    def peers(self) -> tuple[PeerAddress, ...]: ...

    async def close(self) -> None: ...


def team_tag(team: bytes) -> str:
    """Advertised instead of the team id, so the id itself is never broadcast."""
    return hashlib.sha256(team).digest()[:_TAG_BYTES].hex()


def key_hint(author: bytes) -> str:
    return author[1:7].hex()


def peer_from_service(
    properties: Mapping[bytes, bytes | None],
    addresses: list[str],
    port: int | None,
    *,
    team: bytes,
    own_author: bytes,
) -> PeerAddress | None:
    if properties.get(b"team") != team_tag(team).encode():
        return None
    hint = (properties.get(b"key") or b"").decode(errors="replace")
    if hint == key_hint(own_author) or not addresses or port is None:
        return None
    return PeerAddress(addresses[0], port, hint)


def local_address() -> str:
    """The address other machines on the LAN can reach; no packet is actually sent."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("224.0.0.251", 5353))
            address: str = probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"
    return address


class StaticDiscovery:
    def __init__(self, peers: tuple[PeerAddress, ...] = ()) -> None:
        self._peers = peers

    async def start(self, port: int) -> None:
        return None

    def peers(self) -> tuple[PeerAddress, ...]:
        return self._peers

    def set_peers(self, peers: tuple[PeerAddress, ...]) -> None:
        self._peers = peers

    async def close(self) -> None:
        return None


class ZeroconfDiscovery:
    """Advertises this device over mDNS and tracks teammates doing the same."""

    def __init__(
        self, team: bytes, author: bytes, on_found: Callable[[], None] | None = None
    ) -> None:
        self._team = team
        self._author = author
        self._on_found = on_found
        self._found: dict[str, PeerAddress] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._zeroconf: AsyncZeroconf | None = None
        self._browser: AsyncServiceBrowser | None = None
        self._info: AsyncServiceInfo | None = None

    async def start(self, port: int) -> None:
        self._zeroconf = AsyncZeroconf()
        name = f"{key_hint(self._author)}-{secrets.token_hex(3)}.{SERVICE_TYPE}"
        self._info = AsyncServiceInfo(
            SERVICE_TYPE,
            name,
            port=port,
            properties={"team": team_tag(self._team), "key": key_hint(self._author)},
            parsed_addresses=[local_address()],
        )
        await self._zeroconf.async_register_service(self._info)
        self._browser = AsyncServiceBrowser(
            self._zeroconf.zeroconf, SERVICE_TYPE, handlers=[self._on_change]
        )

    def peers(self) -> tuple[PeerAddress, ...]:
        return tuple(self._found.values())

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._browser is not None:
            await self._browser.async_cancel()
        if self._zeroconf is not None:
            if self._info is not None:
                await self._zeroconf.async_unregister_service(self._info)
            await self._zeroconf.async_close()

    def _on_change(
        self,
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        if state_change is ServiceStateChange.Removed:
            self._found.pop(name, None)
            return
        task = asyncio.get_running_loop().create_task(self._resolve(zeroconf, name))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _resolve(self, zeroconf: Zeroconf, name: str) -> None:
        info = AsyncServiceInfo(SERVICE_TYPE, name)
        if not await info.async_request(zeroconf, _RESOLVE_TIMEOUT_MS):
            return
        peer = peer_from_service(
            info.properties,
            info.parsed_addresses(),
            info.port,
            team=self._team,
            own_author=self._author,
        )
        if peer is None:
            return
        is_new = name not in self._found
        self._found[name] = peer
        _log.info("found teammate %s at %s:%s", peer.key_hint, peer.host, peer.port)
        if is_new and self._on_found is not None:
            self._on_found()
