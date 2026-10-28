import asyncio
import os

import pytest

from agentmesh.transport.lan import (
    PeerAddress,
    StaticDiscovery,
    ZeroconfDiscovery,
    key_hint,
    local_address,
    peer_from_service,
    team_tag,
)

TEAM = b"\x0c" * 16
ME = b"\x02" + b"\x01" * 32
THEM = b"\x03" + b"\x02" * 32


def _props(team: bytes = TEAM, author: bytes = THEM) -> dict[bytes, bytes | None]:
    return {b"team": team_tag(team).encode(), b"key": key_hint(author).encode()}


def test_team_tag_hides_the_team_id() -> None:
    tag = team_tag(TEAM)
    assert len(tag) == 16
    assert TEAM.hex() not in tag
    assert tag != team_tag(b"\x0d" * 16)


def test_accepts_a_teammate() -> None:
    peer = peer_from_service(_props(), ["10.0.0.5"], 4567, team=TEAM, own_author=ME)
    assert peer == PeerAddress("10.0.0.5", 4567, key_hint(THEM))


@pytest.mark.parametrize(
    ("properties", "addresses", "port"),
    [
        (_props(team=b"\x0d" * 16), ["10.0.0.5"], 4567),
        (_props(author=ME), ["10.0.0.5"], 4567),
        ({b"key": key_hint(THEM).encode()}, ["10.0.0.5"], 4567),
        (_props(), [], 4567),
        (_props(), ["10.0.0.5"], None),
    ],
)
def test_ignores_other_teams_itself_and_incomplete_records(
    properties: dict[bytes, bytes | None], addresses: list[str], port: int | None
) -> None:
    assert peer_from_service(properties, addresses, port, team=TEAM, own_author=ME) is None


def test_local_address_is_an_ipv4_address() -> None:
    assert local_address().count(".") == 3


def test_static_discovery() -> None:
    discovery = StaticDiscovery()
    asyncio.run(discovery.start(1))
    assert discovery.peers() == ()
    peer = PeerAddress("127.0.0.1", 9, "x")
    discovery.set_peers((peer,))
    assert discovery.peers() == (peer,)
    asyncio.run(discovery.close())


async def _find_each_other() -> tuple[tuple[PeerAddress, ...], tuple[PeerAddress, ...]]:
    found = asyncio.Event()
    first = ZeroconfDiscovery(TEAM, ME, on_found=found.set)
    second = ZeroconfDiscovery(TEAM, THEM)
    await first.start(40001)
    await second.start(40002)
    try:
        await asyncio.wait_for(found.wait(), 10)
        await asyncio.sleep(1)
        return first.peers(), second.peers()
    finally:
        await first.close()
        await second.close()


@pytest.mark.skipif(
    os.environ.get("AGENTMESH_MDNS_TEST") != "1",
    reason="needs multicast; set AGENTMESH_MDNS_TEST=1 to run",
)
def test_two_devices_find_each_other_over_mdns() -> None:
    first, second = asyncio.run(_find_each_other())
    assert [p.port for p in first] == [40002]
    assert [p.key_hint for p in first] == [key_hint(THEM)]
    assert key_hint(ME) in [p.key_hint for p in second]
