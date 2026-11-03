import asyncio
from collections.abc import Callable
from pathlib import Path

from ehor.relay.server import Relay
from ehor.service.device import Device
from ehor.service.direct_sync import RoundReport
from ehor.service.peer_sync import PeerSync
from ehor.service.relay_policy import RelayGate, devices_to_reach
from ehor.transport.relays import RelayDirectory

PASSPHRASE = b"relay gate passphrase"


def _gated(device: Device, report: Callable[[], RoundReport | None]) -> PeerSync:
    gate = RelayGate(
        lambda: True,
        report,
        lambda: devices_to_reach(device.roster.current, device.author),
        lambda: frozenset(key for key, _ in device.direct_peers()),
    )
    return PeerSync(
        device.sync_node, RelayDirectory(device.relay_addresses), listen=False, should_sync=gate
    )


async def _scenario(root: Path) -> None:
    gef = Device.create_team(root / "gef", "gef", PASSPHRASE)
    relay = Relay.create(root / "relay", gef.invite.team, gef.invite.genesis, PASSPHRASE)
    server = await relay.start("127.0.0.1", 0)
    address = f"127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        gef.call("roster.relay_add", {"address": address, "key": relay.key.hex()})
        tobi = Device.join(root / "tobi", gef.invite, "tobi", PASSPHRASE)
        tobi_relay = PeerSync(tobi.sync_node, RelayDirectory(tobi.relay_addresses), listen=False)
        await tobi_relay.sync_all()
        gef.call("roster.add", {"handle": "tobi", "key": tobi.author.hex()})
        gef_relay = _gated(gef, lambda: RoundReport(frozenset(), frozenset({tobi.author})))
        await gef_relay.sync_all()
        await tobi_relay.sync_all()
        assert tobi.announce_key()
        await tobi_relay.sync_all()
        await gef_relay.sync_all()

        posted = gef.call("post", {"to": ["tobi"], "type": "fyi", "summary": "no direct path"})
        await gef_relay.sync_all()
        await tobi_relay.sync_all()
        assert posted.split()[1] in tobi.call("inbox", {})
        tobi.close()
    finally:
        gef.close()
        server.close()
        await server.wait_closed()
        relay.close()


def test_a_teammate_direct_sync_cannot_reach_gets_messages_through_the_relay(
    tmp_path: Path,
) -> None:
    asyncio.run(_scenario(tmp_path))
