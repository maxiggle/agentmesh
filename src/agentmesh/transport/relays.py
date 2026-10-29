from collections.abc import Callable

from agentmesh.log.names import parse_address
from agentmesh.transport.lan import PeerAddress


class RelayDirectory:
    """Discovery for relays: whatever addresses are currently listed for the team."""

    def __init__(self, addresses: Callable[[], list[str]]) -> None:
        self._addresses = addresses

    async def start(self, port: int) -> None:
        return None

    def subscribe(self, on_found: Callable[[], None]) -> None:
        return None

    def peers(self) -> tuple[PeerAddress, ...]:
        found = []
        for address in self._addresses():
            parsed = parse_address(address)
            if parsed is not None:
                found.append(PeerAddress(parsed[0], parsed[1], address))
        return tuple(found)

    async def close(self) -> None:
        return None
