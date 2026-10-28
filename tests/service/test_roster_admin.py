from pathlib import Path

import pytest

from agentmesh.service.api import ApiError
from agentmesh.service.device import Device

PASSPHRASE = b"test passphrase"
OTHER_KEY = "02" + "11" * 32


@pytest.fixture
def gef(tmp_path: Path) -> Device:
    return Device.create_team(tmp_path, "gef", PASSPHRASE, team_name="Acme")


def test_add_shows_roles_channels_and_admins(gef: Device) -> None:
    text = gef.call(
        "roster.add",
        {
            "handle": "tobi",
            "key": OTHER_KEY,
            "roles": ["@mobile"],
            "channels": ["#mobile"],
            "admin": True,
            "label": "tobi-mac",
        },
    )
    lines = text.splitlines()
    assert lines[0] == "added tobi"
    assert lines[1] == "Acme · roster v2"
    assert lines[3] == "tobi (admin, @mobile) · channels #mobile · tobi-mac 111111"
    gef.close()


def test_add_a_second_device_to_a_member(gef: Device) -> None:
    gef.call("roster.add", {"handle": "gef", "key": OTHER_KEY, "label": "gef-phone"})
    roster = gef.roster.current
    assert roster is not None
    member = roster.member("gef")
    assert member is not None
    assert {d.label for d in member.devices} == {"gef", "gef-phone"}
    assert [d.key for d in member.devices] == sorted(d.key for d in member.devices)
    gef.close()


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"handle": "tobi", "key": "zz"}, "hex"),
        ({"handle": "Tobi", "key": OTHER_KEY}, "invalid handle"),
        ({"handle": "tobi", "key": "02" + "11" * 31}, "33 bytes"),
        ({"handle": "tobi", "key": OTHER_KEY, "roles": ["mobile"]}, "invalid role"),
        ({"handle": "tobi", "key": OTHER_KEY, "admin": "yes"}, "true or false"),
    ],
)
def test_add_rejects_bad_input(gef: Device, params: dict[str, object], error: str) -> None:
    with pytest.raises(ApiError, match=error):
        gef.call("roster.add", params)
    gef.close()


def test_add_rejects_a_key_already_on_the_roster(gef: Device) -> None:
    with pytest.raises(ApiError, match="already on the roster"):
        gef.call("roster.add", {"handle": "tobi", "key": gef.author.hex()})
    gef.close()


def test_remove_unknown_member(gef: Device) -> None:
    with pytest.raises(ApiError, match="not on the roster"):
        gef.call("roster.remove", {"handle": "nobody"})
    gef.close()


def test_unknown_admin_method(gef: Device) -> None:
    with pytest.raises(ApiError, match="unknown method"):
        gef.call("roster.delete", {})
    gef.close()
