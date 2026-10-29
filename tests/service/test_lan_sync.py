import asyncio
from contextlib import ExitStack
from pathlib import Path

from agentmesh.service.device import Device
from agentmesh.service.lan_sync import LanSync
from agentmesh.transport.lan import PeerAddress, StaticDiscovery

PASSPHRASE = b"lan passphrase"


class Node:
    def __init__(self, device: Device) -> None:
        self.device = device
        self.discovery = StaticDiscovery()
        self.lan = LanSync(self.device.sync_node, self.discovery, host="127.0.0.1", interval=60)
        self.stop = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(self.lan.run(self.stop))
        while self.lan.port is None:
            await asyncio.sleep(0.01)

    async def halt(self) -> None:
        self.stop.set()
        if self.task is not None:
            await self.task
        self.task = None

    @property
    def address(self) -> PeerAddress:
        assert self.lan.port is not None
        return PeerAddress("127.0.0.1", self.lan.port, self.device.config.handle)


def _see(nodes: list[Node]) -> None:
    for node in nodes:
        node.discovery.set_peers(tuple(o.address for o in nodes if o is not node))


async def _round(nodes: list[Node]) -> None:
    for node in nodes:
        await node.lan.sync_all()


def _post(device: Device, summary: str) -> str:
    return device.call("post", {"to": ["#general"], "type": "fyi", "summary": summary}).split()[1]


async def _scenario(root: Path) -> None:
    with ExitStack() as stack:
        gef = stack.enter_context(Device.create_team(root / "gef", "gef", PASSPHRASE))
        devices = [gef]
        for handle in ("tobi", "lee"):
            devices.append(
                stack.enter_context(Device.join(root / handle, gef.invite, handle, PASSPHRASE))
            )
        nodes = [Node(device) for device in devices]
        for node in nodes:
            await node.start()
        _see(nodes)

        for joiner in devices[1:]:
            gef.call("roster.add", {"handle": joiner.config.handle, "key": joiner.author.hex()})
        await _round(nodes)
        await _round(nodes)
        for device in devices:
            assert device.roster.current is not None
            assert device.roster.current.version == 3
            device.announce_key()
        await _round(nodes)
        await _round(nodes)

        first = _post(devices[1], "hello over wifi")
        await _round(nodes)
        assert first in devices[2].call("inbox", {})
        assert first in gef.call("inbox", {})

        await nodes[2].halt()
        _see(nodes[:2])
        missed = [_post(gef, f"while lee was away {i}") for i in range(3)]
        await _round(nodes[:2])

        await nodes[2].start()
        _see(nodes)
        await _round(nodes)
        inbox = devices[2].call("inbox", {})
        assert all(message_id in inbox for message_id in missed)

        for node in nodes:
            await node.halt()


def test_team_syncs_over_the_network_and_catches_up(tmp_path: Path) -> None:
    asyncio.run(_scenario(tmp_path))


async def _woken(root: Path) -> bool:
    with ExitStack() as stack:
        gef = stack.enter_context(Device.create_team(root / "gef", "gef", PASSPHRASE))
        node = Node(gef)
        await node.start()
        node.lan.poke()
        await asyncio.sleep(0.05)
        await node.halt()
        return node.task is None


def test_poke_and_stop_do_not_hang(tmp_path: Path) -> None:
    assert asyncio.run(asyncio.wait_for(_woken(tmp_path), 5))
