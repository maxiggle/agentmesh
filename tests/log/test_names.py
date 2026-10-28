import pytest

from agentmesh.log.names import dm_channel, dm_members


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
