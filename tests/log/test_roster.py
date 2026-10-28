from dataclasses import replace

import pytest

from agentmesh.log.canonical import decode, encode
from agentmesh.log.roster import Member, Roster, RosterDevice, RosterError, Trust

KEY_A = b"\x02" + b"\x01" * 32
KEY_B = b"\x02" + b"\x02" * 32
KEY_C = b"\x03" + b"\x03" * 32
PREVIOUS = b"\x09" * 32


def member(handle: str, *keys: bytes, channels: tuple[str, ...] = ("*",)) -> Member:
    devices = tuple(RosterDevice(key, Trust.SOFTWARE, f"{handle}-laptop") for key in sorted(keys))
    return Member(handle, (), channels, devices)


def genesis() -> Roster:
    return Roster(1, None, "Acme", ("gef",), (member("gef", KEY_A), member("tobi", KEY_B)))


def test_round_trip() -> None:
    roster = replace(
        genesis(),
        members=(
            Member(
                "gef",
                ("@backend",),
                ("#backend", "#general"),
                (RosterDevice(KEY_A, Trust.HARDWARE, "mac"),),
            ),
            member("tobi", KEY_B, KEY_C),
        ),
        require_encryption=True,
    )
    assert Roster.decode(roster.encode()) == roster


def test_lookups() -> None:
    roster = genesis()
    assert roster.member_for_key(KEY_B) == roster.member("tobi")
    assert roster.member_for_key(KEY_C) is None
    assert roster.is_admin_device(KEY_A)
    assert not roster.is_admin_device(KEY_B)
    assert not roster.is_admin_device(KEY_C)


def test_member_channels() -> None:
    limited = member("lee", KEY_C, channels=("#mobile",))
    assert limited.can_use("#mobile")
    assert not limited.can_use("#backend")
    assert member("gef", KEY_A).can_use("#anything")


def test_successor_bumps_version_and_sorts_members() -> None:
    roster = genesis()
    added = roster.successor(PREVIOUS, members=(member("zed", KEY_C), *roster.members))
    assert added.version == 2
    assert added.previous == PREVIOUS
    assert [m.handle for m in added.members] == ["gef", "tobi", "zed"]


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"version": 0}, "version"),
        ({"previous": PREVIOUS}, "previous"),
        ({"version": 2}, "previous"),
        ({"team_name": "x" * 65}, "team name"),
        ({"admins": ()}, "admins"),
        ({"admins": ("lee",)}, "every admin"),
        ({"members": (member("tobi", KEY_B), member("gef", KEY_A))}, "sorted"),
        ({"members": (member("gef", KEY_A), member("tobi", KEY_A))}, "two members"),
    ],
)
def test_roster_rules(changes: dict[str, object], error: str) -> None:
    with pytest.raises(RosterError, match=error):
        replace(genesis(), **changes)  # type: ignore[arg-type]  # built from a dict on purpose


@pytest.mark.parametrize(
    ("build", "error"),
    [
        (lambda: Member("Gef", (), ("*",), (RosterDevice(KEY_A, Trust.SOFTWARE, ""),)), "handle"),
        (lambda: Member("gef", ("backend",), ("*",), member("x", KEY_A).devices), "role"),
        (lambda: Member("gef", (), ("backend",), member("x", KEY_A).devices), "channel"),
        (lambda: Member("gef", (), ("#b", "#a"), member("x", KEY_A).devices), "sorted"),
        (lambda: Member("gef", (), ("*",), ()), "devices"),
        (lambda: RosterDevice(b"\x02" * 32, Trust.SOFTWARE, ""), "33 bytes"),
        (lambda: RosterDevice(KEY_A, Trust.SOFTWARE, "x" * 65), "label"),
    ],
)
def test_member_and_device_rules(build: object, error: str) -> None:
    with pytest.raises(RosterError, match=error):
        build()  # type: ignore[operator]  # each case is a zero-argument lambda


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "1"),
        ("previous", "none"),
        ("require_encryption", 1),
        ("admins", "gef"),
        ("members", [{"handle": "gef"}]),
        ("extra", 1),
    ],
)
def test_decode_rejects_malformed(field: str, value: object) -> None:
    fields = decode(genesis().encode())
    assert isinstance(fields, dict)
    fields[field] = value  # type: ignore[assignment]  # malformed on purpose
    with pytest.raises(RosterError):
        Roster.decode(encode(fields))


def test_decode_rejects_unknown_trust() -> None:
    fields = decode(genesis().encode())
    assert isinstance(fields, dict)
    members = fields["members"]
    assert isinstance(members, list)
    first = members[0]
    assert isinstance(first, dict)
    devices = first["devices"]
    assert isinstance(devices, list)
    device = devices[0]
    assert isinstance(device, dict)
    device["trust"] = "magic"
    with pytest.raises(RosterError, match="trust"):
        Roster.decode(encode(fields))
