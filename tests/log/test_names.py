import pytest

from agentmesh.log.names import dm_channel, dm_members, parse_address


def test_dm_channel_is_order_independent() -> None:
    assert dm_channel("tobi", "gef") == dm_channel("gef", "tobi") == "dm:gef+tobi"


@pytest.mark.parametrize(
    ("channel", "members"),
    [
        ("dm:gef+tobi", ("gef", "tobi")),
        ("dm:tobi+gef", None),
        ("dm:gef+gef", None),
        ("dm:Gef+tobi", None),
        ("#backend", None),
    ],
)
def test_dm_members(channel: str, members: tuple[str, str] | None) -> None:
    assert dm_members(channel) == members


@pytest.mark.parametrize(
    ("address", "parsed"),
    [
        ("relay.example.com:7447", ("relay.example.com", 7447)),
        ("10.0.0.5:1", ("10.0.0.5", 1)),
        ("[::1]:7447", ("::1", 7447)),
        ("host", None),
        ("host:", None),
        (":7447", None),
        ("host:0", None),
        ("host:65536", None),
        ("host:http", None),
        ("h" * 250 + ":7447", ("h" * 250, 7447)),
        ("h" * 251 + ":7447", None),
    ],
)
def test_parse_address(address: str, parsed: tuple[str, int] | None) -> None:
    assert parse_address(address) == parsed
