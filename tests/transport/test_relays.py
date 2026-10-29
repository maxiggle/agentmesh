import asyncio

from agentmesh.transport.lan import PeerAddress
from agentmesh.transport.relays import RelayDirectory


def test_lists_valid_addresses_and_skips_the_rest() -> None:
    addresses = ["relay.example.com:7447", "broken", "10.0.0.2:1"]
    directory = RelayDirectory(lambda: addresses)
    assert directory.peers() == (
        PeerAddress("relay.example.com", 7447, "relay.example.com:7447"),
        PeerAddress("10.0.0.2", 1, "10.0.0.2:1"),
    )
    addresses.clear()
    assert directory.peers() == ()


def test_lifecycle_is_a_no_op() -> None:
    directory = RelayDirectory(list)
    directory.subscribe(lambda: None)
    asyncio.run(directory.start(0))
    asyncio.run(directory.close())
