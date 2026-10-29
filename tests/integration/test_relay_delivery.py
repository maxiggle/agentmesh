import asyncio
from pathlib import Path

from agentmesh.relay.server import Relay
from agentmesh.service.device import Device
from agentmesh.service.peer_sync import PeerSync
from agentmesh.transport.relays import RelayDirectory

PASSPHRASE = b"relay passphrase"


def _via_relays(device: Device) -> PeerSync:
    return PeerSync(device.sync_node, RelayDirectory(device.relay_addresses), listen=False)


async def _offline_delivery(root: Path) -> None:
    gef = Device.create_team(root / "gef", "gef", PASSPHRASE)
    relay = Relay.create(root / "relay", gef.invite.team, gef.invite.genesis, PASSPHRASE)
    server = await relay.start("127.0.0.1", 0)
    address = f"127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        gef.call("roster.relay_add", {"address": address, "key": relay.key.hex()})
        gef_sync = _via_relays(gef)
        await gef_sync.sync_all()
        invite = gef.invite
        assert invite.relay == address

        tobi = Device.join(root / "tobi", invite, "tobi", PASSPHRASE)
        tobi_sync = _via_relays(tobi)
        await tobi_sync.sync_all()
        assert tobi.roster.current is not None
        assert tobi.roster.member_for_key(tobi.author) is None

        gef.call("roster.add", {"handle": "tobi", "key": tobi.author.hex()})
        await gef_sync.sync_all()
        await tobi_sync.sync_all()
        assert tobi.announce_key()
        await tobi_sync.sync_all()
        await gef_sync.sync_all()

        posted = gef.call(
            "post", {"to": ["tobi"], "type": "fyi", "summary": "sent while you were away"}
        )
        await gef_sync.sync_all()
        gef.close()

        await tobi_sync.sync_all()
        inbox = tobi.call("inbox", {})
        assert posted.split()[1] in inbox
        assert '"sent while you were away"' in inbox
        tobi.close()
    finally:
        server.close()
        await server.wait_closed()
        relay.close()
    stored = (root / "relay" / "relay.db").read_bytes()
    assert b"sent while you were away" not in stored


def test_message_reaches_an_offline_teammate_through_the_relay(tmp_path: Path) -> None:
    asyncio.run(_offline_delivery(tmp_path))
