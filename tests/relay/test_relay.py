import asyncio
import json
from pathlib import Path

import pytest

from agentmesh.relay.server import Relay, RelayConfig, RelayError
from agentmesh.service.device import Device

PASSPHRASE = b"relay passphrase"


def test_config_round_trip_and_errors(tmp_path: Path) -> None:
    config = RelayConfig(b"\x01" * 16, b"\x02" * 32)
    config.save(tmp_path)
    assert RelayConfig.load(tmp_path) == config
    with pytest.raises(RelayError, match="relay init"):
        RelayConfig.load(tmp_path / "missing")
    (tmp_path / "relay.json").write_text(json.dumps({"team": "zz"}))
    with pytest.raises(RelayError, match="invalid"):
        RelayConfig.load(tmp_path)
    with pytest.raises(RelayError, match="malformed"):
        RelayConfig(b"\x01", b"\x02" * 32)


def test_create_then_reopen(tmp_path: Path) -> None:
    with Relay.create(tmp_path, b"\x01" * 16, b"\x02" * 32, PASSPHRASE) as relay:
        key = relay.key
    with Relay.open(tmp_path, PASSPHRASE) as relay:
        assert relay.key == key
    with pytest.raises(RelayError, match="already exists"):
        Relay.create(tmp_path, b"\x01" * 16, b"\x02" * 32, PASSPHRASE)


async def _relay_carries_rosters(root: Path) -> None:
    gef = Device.create_team(root / "gef", "gef", PASSPHRASE)
    tobi = Device.join(root / "tobi", gef.invite, "tobi", PASSPHRASE)
    gef.call("roster.add", {"handle": "tobi", "key": tobi.author.hex()})
    relay = Relay.create(root / "relay", gef.invite.team, gef.invite.genesis, PASSPHRASE)
    server = await relay.start("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        await gef.sync_node.sync_with("127.0.0.1", port)
        assert relay.roster.current is not None
        assert relay.roster.current.version == 2
        await tobi.sync_node.sync_with("127.0.0.1", port)
        assert tobi.roster.current is not None
    finally:
        server.close()
        await server.wait_closed()
        for closable in (gef, tobi, relay):
            closable.close()


def test_relay_stores_and_serves_the_roster(tmp_path: Path) -> None:
    asyncio.run(_relay_carries_rosters(tmp_path))


async def _serve_until_stopped(root: Path) -> None:
    relay = Relay.create(root, b"\x01" * 16, b"\x02" * 32, PASSPHRASE)
    stop = asyncio.Event()
    task = asyncio.create_task(relay.serve("127.0.0.1", 0, stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 5)
    relay.close()


def test_serve_stops_cleanly(tmp_path: Path) -> None:
    asyncio.run(_serve_until_stopped(tmp_path))
